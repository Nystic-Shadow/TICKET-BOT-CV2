"""Download legacy custom emojis and sync themed assets to the application.

Discord assigns a new snowflake when an emoji is uploaded.  This module keeps
the legacy IDs in source code and stores the assigned application IDs in a
local manifest, allowing outgoing component/text helpers to resolve them at
runtime.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import aiohttp


logger = logging.getLogger("discord")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EMOJI_DIRECTORY = PROJECT_ROOT / "emojis"
DEFAULT_MANIFEST_PATH = DEFAULT_EMOJI_DIRECTORY / "manifest.json"
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_CDN_BASE = "https://cdn.discordapp.com/emojis"
MAX_EMOJI_BYTES = 256 * 1024
MAX_APPLICATION_EMOJIS = 2000
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
SOURCE_LOCATIONS = ("main.py", "cogs", "views", "utils")
CUSTOM_EMOJI_RE = re.compile(r"<(?P<animated>a?):(?P<name>[A-Za-z0-9_]+):(?P<id>[0-9]{13,20})>")

_resolved_emojis: dict[str, dict[str, Any]] = {}
_manifest_loaded = False


class EmojiSyncError(RuntimeError):
    """Raised when Discord's application-emoji API cannot complete a request."""


@dataclass(frozen=True, slots=True)
class EmojiReference:
    """One legacy custom emoji discovered in runtime source code."""

    old_id: str
    name: str
    animated: bool
    occurrences: int = 1

    @property
    def mention(self) -> str:
        marker = "a" if self.animated else ""
        return f"<{marker}:{self.name}:{self.old_id}>"

    @property
    def asset_filename(self) -> str:
        return f"{self.name}-{self.old_id}.webp"

    @property
    def themed_asset_filename(self) -> str:
        return f"{self.name}-{self.old_id}.png"


@dataclass(slots=True)
class EmojiSyncReport:
    """Summary returned by download-only and full application sync runs."""

    discovered: int = 0
    downloaded: int = 0
    cached_assets: int = 0
    reused: int = 0
    uploaded: int = 0
    replaced: int = 0
    upload_enabled: bool = False
    failures: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_emoji_references(
    source_root: Path = PROJECT_ROOT,
    source_locations: Iterable[str | Path] = SOURCE_LOCATIONS,
) -> list[EmojiReference]:
    """Find unique legacy Discord emoji mentions in runtime Python sources."""

    discovered: dict[str, EmojiReference] = {}
    counts: dict[str, int] = {}

    for location in source_locations:
        path = source_root / location
        candidates = [path] if path.is_file() else sorted(path.rglob("*.py")) if path.is_dir() else []
        for candidate in candidates:
            if any(part in {".git", ".venv", "__pycache__", "graphify-out"} for part in candidate.parts):
                continue
            try:
                content = candidate.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as error:
                logger.warning("Could not scan emoji references in %s: %s", candidate, error)
                continue

            for match in CUSTOM_EMOJI_RE.finditer(content):
                old_id = match.group("id")
                counts[old_id] = counts.get(old_id, 0) + 1
                discovered.setdefault(
                    old_id,
                    EmojiReference(
                        old_id=old_id,
                        name=match.group("name"),
                        animated=bool(match.group("animated")),
                    ),
                )

    return [
        EmojiReference(
            old_id=reference.old_id,
            name=reference.name,
            animated=reference.animated,
            occurrences=counts[reference.old_id],
        )
        for reference in sorted(discovered.values(), key=lambda item: (item.name.lower(), item.old_id))
    ]


def _mapping_from_manifest(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for old_id, record in manifest.get("emojis", {}).items():
        new_id = record.get("new_id")
        name = record.get("application_name")
        if new_id and name:
            mapping[str(old_id)] = {
                "new_id": str(new_id),
                "application_name": str(name),
                "animated": bool(record.get("animated", False)),
            }
    return mapping


def install_emoji_mapping(manifest: dict[str, Any]) -> None:
    """Install one manifest's old-ID to application-ID mapping in memory."""

    global _manifest_loaded
    _resolved_emojis.clear()
    _resolved_emojis.update(_mapping_from_manifest(manifest))
    _manifest_loaded = True


def load_emoji_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    """Load the local mapping manifest, returning an empty manifest if absent."""

    global _manifest_loaded
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        manifest = {"version": 1, "emojis": {}}
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("Could not load emoji manifest %s: %s", path, error)
        manifest = {"version": 1, "emojis": {}}

    install_emoji_mapping(manifest)
    _manifest_loaded = True
    return manifest


def resolve_emojis(value: str) -> str:
    """Replace legacy custom-emoji mentions with their application-owned IDs."""

    if not isinstance(value, str):
        return value
    if not _manifest_loaded:
        load_emoji_manifest()

    def replace(match: re.Match[str]) -> str:
        record = _resolved_emojis.get(match.group("id"))
        if not record:
            return match.group(0)
        marker = "a" if record["animated"] else ""
        return f"<{marker}:{record['application_name']}:{record['new_id']}>"

    return CUSTOM_EMOJI_RE.sub(replace, value)


def resolve_component_emoji(value: str | None) -> str | None:
    """Resolve a custom emoji value before giving it to a Discord component."""

    return resolve_emojis(value) if value else value


class ApplicationEmojiSynchronizer:
    """Download referenced emoji assets and synchronize application emojis."""

    def __init__(
        self,
        *,
        token: str = "",
        application_id: int | str | None = None,
        source_root: Path = PROJECT_ROOT,
        emoji_directory: Path = DEFAULT_EMOJI_DIRECTORY,
    ) -> None:
        self.token = token.strip()
        self.application_id = str(application_id) if application_id else ""
        self.source_root = source_root.resolve()
        self.emoji_directory = emoji_directory.resolve()
        self.asset_directory = self.emoji_directory / "assets"
        self.theme_directory = self.emoji_directory / "purple"
        self.manifest_path = self.emoji_directory / "manifest.json"

    async def sync(self, *, upload: bool = True) -> EmojiSyncReport:
        """Download discovered originals and optionally sync themed app emojis."""

        references = discover_emoji_references(self.source_root)
        report = EmojiSyncReport(discovered=len(references))

        timeout = aiohttp.ClientTimeout(total=45)
        headers = {"User-Agent": "Ticket-Bot-with-CV2/2.0 (application emoji sync)"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            await self._download_assets(session, references, report)
            should_upload = upload and bool(self.token and self.application_id)
            report.upload_enabled = should_upload
            if should_upload:
                await self._sync_application_inventory(session, references, report)
            else:
                self._write_download_manifest(references)

        return report

    async def _download_assets(
        self,
        session: aiohttp.ClientSession,
        references: list[EmojiReference],
        report: EmojiSyncReport,
    ) -> None:
        semaphore = asyncio.Semaphore(5)

        async def download(reference: EmojiReference) -> None:
            themed_path = self.theme_directory / reference.themed_asset_filename
            if themed_path.is_file() and themed_path.stat().st_size > 0:
                report.cached_assets += 1
                return

            asset_path = self.asset_directory / reference.asset_filename
            if asset_path.is_file() and asset_path.stat().st_size > 0:
                report.cached_assets += 1
                return

            query = "size=128&quality=lossless"
            if reference.animated:
                query += "&animated=true"
            url = f"{DISCORD_CDN_BASE}/{reference.old_id}.webp?{query}"

            try:
                async with semaphore, session.get(url) as response:
                    if response.status != 200:
                        details = (await response.text())[:200]
                        raise EmojiSyncError(f"CDN returned HTTP {response.status}: {details}")
                    content_type = response.headers.get("Content-Type", "").lower()
                    if not content_type.startswith("image/"):
                        raise EmojiSyncError(f"CDN returned unexpected content type {content_type or 'unknown'}")
                    image = await response.read()
                if not image:
                    raise EmojiSyncError("Discord CDN returned an empty image")
                if len(image) > MAX_DOWNLOAD_BYTES:
                    raise EmojiSyncError(f"Discord CDN image exceeds the {MAX_DOWNLOAD_BYTES}-byte download limit")
                self.asset_directory.mkdir(parents=True, exist_ok=True)
                temporary_path = asset_path.with_suffix(asset_path.suffix + ".tmp")
                temporary_path.write_bytes(image)
                temporary_path.replace(asset_path)
                report.downloaded += 1
            except (OSError, aiohttp.ClientError, asyncio.TimeoutError, EmojiSyncError) as error:
                report.failures[reference.old_id] = f"download failed: {error}"

        await asyncio.gather(*(download(reference) for reference in references))

    async def _sync_application_inventory(
        self,
        session: aiohttp.ClientSession,
        references: list[EmojiReference],
        report: EmojiSyncReport,
    ) -> None:
        manifest = self._read_manifest()
        existing = await self._api_request(session, "GET", self._application_endpoint())
        items = existing.get("items", [])
        existing_by_id = {str(item["id"]): item for item in items}
        existing_by_name = {str(item["name"]): item for item in items if item.get("name")}
        old_records = manifest.get("emojis", {}) if manifest.get("application_id") == self.application_id else {}
        records: dict[str, dict[str, Any]] = {}

        for reference in references:
            asset_path = self._preferred_asset_path(reference)
            asset_sha256 = self._asset_sha256(asset_path)
            previous = old_records.get(reference.old_id, {})
            previous_id = str(previous.get("new_id", ""))

            matched = existing_by_id.get(previous_id) or existing_by_id.get(reference.old_id)
            if not matched:
                previous_name = previous.get("application_name")
                name_match = existing_by_name.get(previous_name) or existing_by_name.get(reference.name)
                expected_animated = reference.animated and not self._is_themed_asset(asset_path)
                if name_match and bool(name_match.get("animated", False)) == expected_animated:
                    matched = name_match

            should_replace = bool(
                matched
                and asset_sha256
                and self._is_themed_asset(asset_path)
                and previous.get("asset_sha256") != asset_sha256
            )

            if should_replace and len(existing_by_id) >= MAX_APPLICATION_EMOJIS:
                report.failures[reference.old_id] = (
                    "replacement skipped: the application inventory is full, so a safe staged replacement is unavailable"
                )
            elif should_replace and asset_path.stat().st_size > MAX_EMOJI_BYTES:
                report.failures[reference.old_id] = (
                    f"replacement skipped: {asset_path.stat().st_size} bytes exceeds Discord's 256 KiB limit"
                )
            elif should_replace:
                try:
                    matched = await self._replace_application_emoji(
                        session,
                        reference,
                        matched,
                        asset_path,
                        existing_by_id,
                        existing_by_name,
                    )
                except (OSError, EmojiSyncError) as error:
                    report.failures[reference.old_id] = f"replacement failed: {error}"
                else:
                    report.replaced += 1
            elif matched:
                current_name = str(matched.get("name") or "")
                desired_name = self._normalized_name(reference)
                if current_name != desired_name and desired_name not in existing_by_name:
                    try:
                        renamed = await self._api_request(
                            session,
                            "PATCH",
                            self._application_emoji_endpoint(str(matched["id"])),
                            payload={"name": desired_name},
                        )
                    except EmojiSyncError as error:
                        report.failures[reference.old_id] = f"rename failed: {error}"
                    else:
                        if existing_by_name.get(current_name) is matched:
                            existing_by_name.pop(current_name, None)
                        matched = renamed
                        existing_by_id[str(matched["id"])] = matched
                        existing_by_name[str(matched["name"])] = matched
                report.reused += 1
            elif not asset_path.is_file():
                report.failures.setdefault(reference.old_id, "upload skipped: downloaded asset is unavailable")
                records[reference.old_id] = self._manifest_record(reference, asset_path)
                continue
            elif asset_path.stat().st_size > MAX_EMOJI_BYTES:
                report.failures[reference.old_id] = (
                    f"upload skipped: {asset_path.stat().st_size} bytes exceeds Discord's 256 KiB limit"
                )
                records[reference.old_id] = self._manifest_record(reference, asset_path)
                continue
            elif len(existing_by_id) >= MAX_APPLICATION_EMOJIS:
                report.failures[reference.old_id] = "upload skipped: the application emoji inventory is full (2000)"
                records[reference.old_id] = self._manifest_record(reference, asset_path)
                continue
            else:
                application_name = self._available_name(reference, existing_by_name)
                try:
                    matched = await self._api_request(
                        session,
                        "POST",
                        self._application_endpoint(),
                        payload={"name": application_name, "image": self._image_data_uri(asset_path)},
                    )
                except (OSError, EmojiSyncError) as error:
                    report.failures[reference.old_id] = f"upload failed: {error}"
                    records[reference.old_id] = self._manifest_record(reference, asset_path)
                    continue
                existing_by_id[str(matched["id"])] = matched
                existing_by_name[str(matched["name"])] = matched
                report.uploaded += 1

            record = self._manifest_record(
                reference,
                asset_path,
                matched=matched,
            )
            if should_replace and reference.old_id in report.failures:
                record["asset_sha256"] = previous.get("asset_sha256")
                record["theme"] = previous.get("theme")
            records[reference.old_id] = record

        complete_manifest = {
            "version": 2,
            "application_id": self.application_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "emojis": records,
        }
        self._write_manifest(complete_manifest)
        install_emoji_mapping(complete_manifest)

    def _preferred_asset_path(self, reference: EmojiReference) -> Path:
        themed_path = self.theme_directory / reference.themed_asset_filename
        if themed_path.is_file() and themed_path.stat().st_size > 0:
            return themed_path
        return self.asset_directory / reference.asset_filename

    def _is_themed_asset(self, asset_path: Path) -> bool:
        return asset_path.parent == self.theme_directory

    @staticmethod
    def _asset_sha256(asset_path: Path) -> str | None:
        try:
            return hashlib.sha256(asset_path.read_bytes()).hexdigest()
        except OSError:
            return None

    @staticmethod
    def _image_data_uri(asset_path: Path) -> str:
        mime_type = {
            ".avif": "image/avif",
            ".gif": "image/gif",
            ".jpeg": "image/jpeg",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(asset_path.suffix.lower(), "application/octet-stream")
        image = base64.b64encode(asset_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{image}"

    async def _replace_application_emoji(
        self,
        session: aiohttp.ClientSession,
        reference: EmojiReference,
        existing: dict[str, Any],
        asset_path: Path,
        existing_by_id: dict[str, dict[str, Any]],
        existing_by_name: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        old_id = str(existing["id"])
        old_name = str(existing.get("name") or reference.name)
        temporary_name = self._available_name(reference, existing_by_name)
        replacement = await self._api_request(
            session,
            "POST",
            self._application_endpoint(),
            payload={"name": temporary_name, "image": self._image_data_uri(asset_path)},
        )
        replacement_id = str(replacement["id"])

        try:
            await self._api_request(session, "DELETE", self._application_emoji_endpoint(old_id))
        except EmojiSyncError:
            try:
                await self._api_request(session, "DELETE", self._application_emoji_endpoint(replacement_id))
            except EmojiSyncError:
                logger.warning("Could not roll back staged application emoji %s", replacement_id)
            raise

        existing_by_id.pop(old_id, None)
        if existing_by_name.get(old_name) is existing:
            existing_by_name.pop(old_name, None)

        desired_name = self._normalized_name(reference)
        if replacement.get("name") != desired_name:
            try:
                replacement = await self._api_request(
                    session,
                    "PATCH",
                    self._application_emoji_endpoint(replacement_id),
                    payload={"name": desired_name},
                )
            except EmojiSyncError as error:
                logger.warning(
                    "Purple emoji %s uploaded but could not be renamed to %s: %s",
                    replacement_id,
                    desired_name,
                    error,
                )

        existing_by_id[replacement_id] = replacement
        existing_by_name[str(replacement["name"])] = replacement
        return replacement

    async def _api_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bot {self.token}"}
        for attempt in range(5):
            try:
                async with session.request(method, url, headers=headers, json=payload) as response:
                    body = await response.text()
                    try:
                        data = json.loads(body) if body else {}
                    except json.JSONDecodeError:
                        data = {}

                    if response.status == 429:
                        try:
                            retry_after = float(data.get("retry_after", response.headers.get("Retry-After", 1)))
                        except (TypeError, ValueError):
                            retry_after = 1.0
                        await asyncio.sleep(max(0.25, min(retry_after, 60)))
                        continue
                    if response.status >= 500 and attempt < 4:
                        await asyncio.sleep(2**attempt)
                        continue
                    if response.status >= 400:
                        message = data.get("message", body[:300]) if isinstance(data, dict) else body[:300]
                        raise EmojiSyncError(f"Discord API HTTP {response.status}: {message}")
                    if not isinstance(data, dict):
                        raise EmojiSyncError("Discord API returned an unexpected response")
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                if attempt == 4:
                    raise EmojiSyncError(f"Discord API request failed: {error}") from error
                await asyncio.sleep(2**attempt)

        raise EmojiSyncError("Discord API rate limit did not clear after five attempts")

    def _application_endpoint(self) -> str:
        return f"{DISCORD_API_BASE}/applications/{self.application_id}/emojis"

    def _application_emoji_endpoint(self, emoji_id: str | int) -> str:
        return f"{self._application_endpoint()}/{emoji_id}"

    @staticmethod
    def _normalized_name(reference: EmojiReference) -> str:
        name = re.sub(r"[^A-Za-z0-9_]", "_", reference.name)[:32]
        if len(name) < 2:
            name = f"emoji_{reference.old_id[-6:]}"
        return name

    @classmethod
    def _available_name(cls, reference: EmojiReference, existing_by_name: dict[str, dict[str, Any]]) -> str:
        name = cls._normalized_name(reference)
        if name not in existing_by_name:
            return name
        suffix = f"_{reference.old_id[-6:]}"
        candidate = f"{name[: 32 - len(suffix)]}{suffix}"
        if candidate not in existing_by_name:
            return candidate

        for index in range(2, 1000):
            suffix = f"_{reference.old_id[-4:]}_{index}"
            candidate = f"{name[: 32 - len(suffix)]}{suffix}"
            if candidate not in existing_by_name:
                return candidate
        raise EmojiSyncError(f"Could not allocate a unique application emoji name for {reference.name}")

    def _manifest_record(
        self,
        reference: EmojiReference,
        asset_path: Path,
        *,
        matched: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            asset = asset_path.relative_to(self.source_root).as_posix()
        except ValueError:
            asset = str(asset_path)
        return {
            "legacy_mention": reference.mention,
            "source_name": reference.name,
            "old_id": reference.old_id,
            "animated": bool(matched.get("animated", reference.animated)) if matched else reference.animated,
            "occurrences": reference.occurrences,
            "asset": asset,
            "asset_sha256": self._asset_sha256(asset_path) if matched else None,
            "theme": "purple" if matched and self._is_themed_asset(asset_path) else None,
            "application_name": str(matched["name"]) if matched else None,
            "new_id": str(matched["id"]) if matched else None,
        }

    def _read_manifest(self) -> dict[str, Any]:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {"version": 1, "emojis": {}}

    def _write_download_manifest(self, references: list[EmojiReference]) -> None:
        previous = self._read_manifest()
        previous_records = previous.get("emojis", {})
        records = {}
        for reference in references:
            asset_path = self._preferred_asset_path(reference)
            record = self._manifest_record(reference, asset_path)
            old_record = previous_records.get(reference.old_id, {})
            if previous.get("application_id") and old_record.get("new_id"):
                record.update(
                    application_name=old_record.get("application_name"),
                    new_id=old_record.get("new_id"),
                    animated=old_record.get("animated", reference.animated),
                    asset_sha256=old_record.get("asset_sha256"),
                    theme=old_record.get("theme"),
                )
            records[reference.old_id] = record

        manifest = {
            "version": 2,
            "application_id": previous.get("application_id"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "emojis": records,
        }
        self._write_manifest(manifest)
        install_emoji_mapping(manifest)

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        self.emoji_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = self.manifest_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary_path.replace(self.manifest_path)

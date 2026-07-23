"""Download legacy emojis and optionally upload them to the Discord app.

Run from the project root:

    python -m scripts.sync_application_emojis --download-only
    python -m scripts.sync_application_emojis --application-id 123456789012345678

The upload command reads the bot token from DISCORD_TOKEN.  It never uses a
user token and never calls guild/server emoji endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from utils.application_emojis import ApplicationEmojiSynchronizer, PROJECT_ROOT


load_dotenv(PROJECT_ROOT / ".env", override=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download referenced Discord emojis and sync the application's emoji inventory."
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download assets and update the local manifest without calling the application API.",
    )
    parser.add_argument(
        "--application-id",
        default=os.getenv("DISCORD_APPLICATION_ID", ""),
        help="Discord application ID (or set DISCORD_APPLICATION_ID).",
    )
    parser.add_argument(
        "--emoji-directory",
        type=Path,
        default=PROJECT_ROOT / "emojis",
        help="Asset and manifest directory (default: ./emojis).",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not args.download_only and (not token or not args.application_id):
        raise SystemExit(
            "Upload requires DISCORD_TOKEN and --application-id (or DISCORD_APPLICATION_ID). "
            "Use --download-only when credentials are intentionally unavailable."
        )

    synchronizer = ApplicationEmojiSynchronizer(
        token=token,
        application_id=args.application_id,
        source_root=PROJECT_ROOT,
        emoji_directory=args.emoji_directory,
    )
    report = await synchronizer.sync(upload=not args.download_only)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 1 if report.failures else 0


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

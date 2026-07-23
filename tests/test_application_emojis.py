import struct
import tempfile
import unittest
from pathlib import Path

from utils.application_emojis import (
    ApplicationEmojiSynchronizer,
    EmojiReference,
    EmojiSyncError,
    EmojiSyncReport,
    MAX_EMOJI_BYTES,
    discover_emoji_references,
    install_emoji_mapping,
    resolve_component_emoji,
    resolve_emojis,
)


ROOT = Path(__file__).resolve().parents[1]


class ApplicationEmojiTests(unittest.TestCase):
    def setUp(self):
        install_emoji_mapping({"version": 1, "emojis": {}})

    def test_discovers_and_deduplicates_legacy_mentions(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "cogs"
            source.mkdir()
            (source / "sample.py").write_text(
                'STATIC = "<:sample:123456789012345678>"\n'
                'AGAIN = "<:sample:123456789012345678>"\n'
                'ANIMATED = "<a:spin:223456789012345678>"\n',
                encoding="utf-8",
            )

            references = discover_emoji_references(root, ("cogs",))

        by_id = {reference.old_id: reference for reference in references}
        self.assertEqual(set(by_id), {"123456789012345678", "223456789012345678"})
        self.assertEqual(by_id["123456789012345678"].occurrences, 2)
        self.assertTrue(by_id["223456789012345678"].animated)

    def test_resolves_text_and_component_mentions_from_manifest(self):
        install_emoji_mapping(
            {
                "version": 1,
                "emojis": {
                    "123456789012345678": {
                        "new_id": "987654321098765432",
                        "application_name": "sample_app",
                        "animated": False,
                    }
                },
            }
        )

        expected = "<:sample_app:987654321098765432>"
        self.assertEqual(resolve_emojis("Before <:sample:123456789012345678> after"), f"Before {expected} after")
        self.assertEqual(resolve_component_emoji("<:sample:123456789012345678>"), expected)
        self.assertEqual(resolve_emojis("<:unknown:333456789012345678>"), "<:unknown:333456789012345678>")

    def test_restored_component_ids_are_present_in_source(self):
        ticket_views = (ROOT / "views" / "ticket_views.py").read_text(encoding="utf-8")
        author_info = (ROOT / "utils" / "author_info.py").read_text(encoding="utf-8")

        for mention in (
            "<:shield:1382703287891136564>",
            "<:megaphone:1382704888294936649>",
            "<:stats_1:1382703019334045830>",
            "<:paint_icons:1383849816022581332>",
            "<:j_icons_Correct:1382701297987485706>",
            "<:id_icons:1384041001114407013>",
        ):
            self.assertIn(mention, ticket_views)
        self.assertIn("<:icons_heart:1382705238619984005>", author_info)
        self.assertIn("<:id_icons:1384041001114407013>", author_info)

    def test_every_referenced_emoji_has_a_valid_purple_png(self):
        references = discover_emoji_references(ROOT)
        purple_directory = ROOT / "emojis" / "purple"
        expected = {reference.themed_asset_filename for reference in references}
        actual = {path.name for path in purple_directory.glob("*.png")}

        self.assertEqual(len(references), 27)
        self.assertEqual(actual, expected)
        for filename in sorted(expected):
            asset = purple_directory / filename
            data = asset.read_bytes()
            self.assertLessEqual(len(data), MAX_EMOJI_BYTES, filename)
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", filename)
            self.assertEqual(struct.unpack(">II", data[16:24]), (128, 128), filename)

    def test_synchronizer_targets_only_application_endpoint(self):
        synchronizer = ApplicationEmojiSynchronizer(token="test", application_id=123, source_root=ROOT)
        endpoint = synchronizer._application_endpoint()
        self.assertEqual(endpoint, "https://discord.com/api/v10/applications/123/emojis")
        self.assertEqual(
            synchronizer._application_emoji_endpoint("456"),
            "https://discord.com/api/v10/applications/123/emojis/456",
        )
        self.assertNotIn("/guilds/", endpoint)


class ApplicationEmojiSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        install_emoji_mapping({"version": 1, "emojis": {}})

    async def test_purple_asset_skips_legacy_download_and_folder_creation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            synchronizer = ApplicationEmojiSynchronizer(
                source_root=root,
                emoji_directory=root / "emojis",
            )
            synchronizer.theme_directory.mkdir(parents=True)
            reference = EmojiReference(
                old_id="123456789012345678",
                name="sample",
                animated=False,
            )
            (synchronizer.theme_directory / reference.themed_asset_filename).write_bytes(b"purple-png")
            report = EmojiSyncReport(discovered=1)

            await synchronizer._download_assets(None, [reference], report)

            self.assertEqual(report.cached_assets, 1)
            self.assertFalse(synchronizer.asset_directory.exists())

    async def test_missing_asset_is_uploaded_and_new_id_is_persisted(self):
        class StubSynchronizer(ApplicationEmojiSynchronizer):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.requests = []

            async def _api_request(self, session, method, url, *, payload=None):
                self.requests.append((method, url, payload))
                if method == "GET":
                    return {"items": []}
                return {"id": "987654321098765432", "name": payload["name"], "animated": False}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            synchronizer = StubSynchronizer(
                token="not-a-real-token",
                application_id="123456789012345678",
                source_root=root,
                emoji_directory=root / "emojis",
            )
            synchronizer.asset_directory.mkdir(parents=True)
            reference = EmojiReference(
                old_id="123456789012345678",
                name="sample",
                animated=False,
            )
            (synchronizer.asset_directory / reference.asset_filename).write_bytes(b"small-webp-test-data")
            report = EmojiSyncReport(discovered=1, upload_enabled=True)

            await synchronizer._sync_application_inventory(None, [reference], report)

            manifest = synchronizer._read_manifest()

        self.assertEqual(report.uploaded, 1)
        self.assertEqual([request[0] for request in synchronizer.requests], ["GET", "POST"])
        self.assertTrue(synchronizer.requests[1][2]["image"].startswith("data:image/webp;base64,"))
        self.assertEqual(manifest["emojis"][reference.old_id]["new_id"], "987654321098765432")
        self.assertEqual(
            resolve_emojis(reference.mention),
            "<:sample:987654321098765432>",
        )

    async def test_existing_emoji_is_safely_replaced_with_purple_png(self):
        old_application_id = "987654321098765432"
        new_application_id = "887654321098765432"

        class StubSynchronizer(ApplicationEmojiSynchronizer):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.requests = []

            async def _api_request(self, session, method, url, *, payload=None):
                self.requests.append((method, url, payload))
                if method == "GET":
                    return {
                        "items": [
                            {
                                "id": old_application_id,
                                "name": "sample",
                                "animated": False,
                            }
                        ]
                    }
                if method == "POST":
                    return {
                        "id": new_application_id,
                        "name": payload["name"],
                        "animated": False,
                    }
                if method == "PATCH":
                    return {
                        "id": new_application_id,
                        "name": payload["name"],
                        "animated": False,
                    }
                return {}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            synchronizer = StubSynchronizer(
                token="not-a-real-token",
                application_id="123456789012345678",
                source_root=root,
                emoji_directory=root / "emojis",
            )
            synchronizer.theme_directory.mkdir(parents=True)
            reference = EmojiReference(
                old_id="123456789012345678",
                name="sample",
                animated=False,
            )
            purple_asset = synchronizer.theme_directory / reference.themed_asset_filename
            purple_asset.write_bytes(b"small-purple-png-test-data")
            synchronizer._write_manifest(
                {
                    "version": 1,
                    "application_id": synchronizer.application_id,
                    "emojis": {
                        reference.old_id: {
                            "new_id": old_application_id,
                            "application_name": reference.name,
                            "animated": False,
                        }
                    },
                }
            )
            report = EmojiSyncReport(discovered=1, upload_enabled=True)

            await synchronizer._sync_application_inventory(None, [reference], report)

            manifest = synchronizer._read_manifest()
            expected_hash = synchronizer._asset_sha256(purple_asset)

        self.assertEqual(report.replaced, 1)
        self.assertEqual(report.uploaded, 0)
        self.assertEqual([request[0] for request in synchronizer.requests], ["GET", "POST", "DELETE", "PATCH"])
        self.assertTrue(synchronizer.requests[1][2]["image"].startswith("data:image/png;base64,"))
        self.assertTrue(synchronizer.requests[2][1].endswith(f"/emojis/{old_application_id}"))
        record = manifest["emojis"][reference.old_id]
        self.assertEqual(record["new_id"], new_application_id)
        self.assertEqual(record["asset_sha256"], expected_hash)
        self.assertEqual(record["theme"], "purple")

    async def test_failed_old_emoji_delete_rolls_back_staged_replacement(self):
        old_application_id = "987654321098765432"
        staged_application_id = "887654321098765432"

        class StubSynchronizer(ApplicationEmojiSynchronizer):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.requests = []

            async def _api_request(self, session, method, url, *, payload=None):
                self.requests.append((method, url, payload))
                if method == "GET":
                    return {"items": [{"id": old_application_id, "name": "sample", "animated": False}]}
                if method == "POST":
                    return {"id": staged_application_id, "name": payload["name"], "animated": False}
                if method == "DELETE" and url.endswith(old_application_id):
                    raise EmojiSyncError("simulated delete failure")
                return {}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            synchronizer = StubSynchronizer(
                token="not-a-real-token",
                application_id="123456789012345678",
                source_root=root,
                emoji_directory=root / "emojis",
            )
            synchronizer.theme_directory.mkdir(parents=True)
            reference = EmojiReference(old_id="123456789012345678", name="sample", animated=False)
            (synchronizer.theme_directory / reference.themed_asset_filename).write_bytes(b"new-purple-asset")
            synchronizer._write_manifest(
                {
                    "version": 2,
                    "application_id": synchronizer.application_id,
                    "emojis": {
                        reference.old_id: {
                            "new_id": old_application_id,
                            "application_name": reference.name,
                            "animated": False,
                            "asset_sha256": "old-hash",
                            "theme": None,
                        }
                    },
                }
            )
            report = EmojiSyncReport(discovered=1, upload_enabled=True)

            await synchronizer._sync_application_inventory(None, [reference], report)

            manifest = synchronizer._read_manifest()

        self.assertEqual(report.replaced, 0)
        self.assertIn(reference.old_id, report.failures)
        self.assertEqual([request[0] for request in synchronizer.requests], ["GET", "POST", "DELETE", "DELETE"])
        self.assertTrue(synchronizer.requests[2][1].endswith(old_application_id))
        self.assertTrue(synchronizer.requests[3][1].endswith(staged_application_id))
        record = manifest["emojis"][reference.old_id]
        self.assertEqual(record["new_id"], old_application_id)
        self.assertEqual(record["asset_sha256"], "old-hash")


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", ".venv", "__pycache__", "graphify-out"}
DISCORD_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{20,30}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])"
)


class SourceSafetyTests(unittest.TestCase):
    def test_no_discord_tokens_in_tracked_source(self):
        findings = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or IGNORED_PARTS.intersection(path.parts) or path.suffix in {".db", ".pyc"}:
                continue
            try:
                content = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                continue
            if DISCORD_TOKEN_PATTERN.search(content):
                findings.append(str(path.relative_to(ROOT)))
        self.assertEqual(findings, [], f"Potential Discord token(s) found in: {findings}")

    def test_runtime_data_is_ignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in (".env", "*.db", "*.log", "graphify-out/", "emojis/assets/", "emojis/manifest.json"):
            self.assertIn(entry, ignore)

    def test_destructive_sql_replace_is_not_used_for_ticket_config(self):
        for relative_path in ("views/modals.py", "views/ticket_views.py"):
            content = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("INSERT OR REPLACE INTO tickets", content)

    def test_project_credit_uses_nystic_shadow_without_legacy_identity(self):
        forbidden = (
            "its" + "fizys",
            "112424" + "8109472550993",
            "Aero" + "X",
        )
        findings = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or IGNORED_PARTS.intersection(path.parts) or path.suffix in {".db", ".pyc"}:
                continue
            try:
                content = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                continue
            if any(value.casefold() in content.casefold() for value in forbidden):
                findings.append(str(path.relative_to(ROOT)))

        self.assertEqual(findings, [], f"Legacy project credit found in: {findings}")
        self.assertIn("Nystic Shadow", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("Nystic Shadow", (ROOT / "pyproject.toml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

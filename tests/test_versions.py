"""The version history: docs/versions.json is the one source; the app and CHANGELOG.md both show it."""

import datetime as dt
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
sys.path.insert(0, str(ROOT / "scripts"))

import make_changelog  # noqa: E402


def releases() -> list:
    return json.loads((DOCS / "versions.json").read_text(encoding="utf-8"))["releases"]


class VersionHistoryTests(unittest.TestCase):
    def test_every_build_has_a_date_a_title_and_changes(self):
        self.assertGreater(len(releases()), 10)
        for release in releases():
            with self.subTest(build=release.get("build")):
                self.assertIsInstance(release["build"], int)
                dt.date.fromisoformat(release["date"])
                self.assertTrue(release["title"].strip())
                self.assertTrue(release["changes"])
                self.assertTrue(all(isinstance(change, str) and change.strip() for change in release["changes"]))

    def test_builds_are_listed_newest_first(self):
        builds = [release["build"] for release in releases()]
        self.assertEqual(builds, sorted(set(builds), reverse=True))
        dates = [release["date"] for release in releases()]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_the_newest_entry_is_the_build_the_service_worker_serves(self):
        cache = re.search(r'const CACHE = "cairn-v(\d+)"', (DOCS / "sw.js").read_text(encoding="utf-8"))
        self.assertEqual(
            releases()[0]["build"],
            int(cache[1]),
            "docs/sw.js has a new cache name: add this build to docs/versions.json (build, date, title, what changed) "
            "and run python scripts/make_changelog.py",
        )

    def test_changelog_is_written_from_the_same_file(self):
        expected = make_changelog.changelog(json.loads((DOCS / "versions.json").read_text(encoding="utf-8")))
        self.assertEqual(
            (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
            expected,
            "CHANGELOG.md is out of date: run python scripts/make_changelog.py",
        )

    def test_the_history_opens_offline_and_ships_with_the_package(self):
        self.assertIn('"versions.json"', (DOCS / "sw.js").read_text(encoding="utf-8"))
        package_data = re.search(r'"cairn\.web" = \[(.*?)\]', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))[1]
        self.assertIn('"*.json"', package_data)


if __name__ == "__main__":
    unittest.main()

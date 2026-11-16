"""GitHub Actions minutes: the month's figure, the note that carries it, and the hourly sync."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from cairn import core, github_usage
from cairn.brain import Brain


def row(day: str, repo: str, quantity: float, sku: str = "Actions Linux", product: str = "actions", unit: str = "Minutes") -> dict:
    return {"date": f"{day}T00:00:00Z", "product": product, "sku": sku, "quantity": quantity, "unitType": unit, "repositoryName": repo}


NOVEMBER = [
    row("2026-11-01", "vault", 100),
    row("2026-11-02", "vault", 40, sku="Actions Windows"),
    row("2026-11-03", "jobs", 20),
    row("2026-11-03", "site", 500),
    row("2026-11-03", "vault", 9, product="actions", sku="Actions storage", unit="GigabyteHours"),
    row("2026-10-31", "vault", 999),
]


class QuotaTests(unittest.TestCase):
    def test_a_minute_costs_more_on_windows_and_macos(self):
        self.assertEqual([github_usage.multiplier(sku) for sku in ("Actions Linux", "Actions Windows", "Actions macOS 12-core", "")], [1, 2, 10, 1])

    def test_only_private_minutes_of_the_month_count(self):
        reading = github_usage.quota(NOVEMBER, {"site"}, 2026, 11, 3, 2000)
        self.assertEqual((reading["month"], reading["used"], reading["pct"], reading["public"]), ("2026-11", 200, 10, 500))
        self.assertEqual(reading["days"], [100, 80, 20])
        self.assertEqual(reading["repos"], [("vault", 180), ("jobs", 20)])
        self.assertEqual(reading["resets"], "2026-12-01")

    def test_a_repo_not_known_to_be_public_counts(self):
        self.assertEqual(github_usage.quota(NOVEMBER, set(), 2026, 11, 3, 2000)["used"], 700)

    def test_the_month_end_figure_follows_the_last_seven_days(self):
        rows = [row(f"2026-11-{day:02d}", "vault", 70 if day > 3 else 0) for day in range(1, 11)]
        reading = github_usage.quota(rows, set(), 2026, 11, 10, 2000)
        self.assertEqual((reading["used"], reading["projected"], reading["projected_pct"]), (490, 490 + 70 * 20, 94))
        self.assertEqual(reading["level"], "ok")

    def test_a_finished_month_projects_nothing_more(self):
        reading = github_usage.quota([row("2026-10-12", "vault", 68)], set(), 2026, 10, 31, 2000)
        self.assertEqual((reading["used"], reading["projected"], len(reading["days"]), reading["resets"]), (68, 68, 31, "2026-11-01"))

    def test_december_starts_again_in_january(self):
        self.assertEqual(github_usage.quota([], set(), 2026, 12, 5, 2000)["resets"], "2027-01-01")

    def test_levels_follow_the_points_github_mails_at(self):
        levels = [github_usage.usage_level(pct, projected) for pct, projected in ((10, 40), (40, 100), (75, 80), (90, 95), (100, 120))]
        self.assertEqual(levels, ["ok", "warn", "warn", "critical", "over"])


class NoteTests(unittest.TestCase):
    def test_the_note_keeps_every_figure_as_text(self):
        reading = github_usage.quota(NOVEMBER, {"site"}, 2026, 11, 3, 2000)
        path, text = github_usage.usage_note({**reading, "account": "ada", "at": "2026-11-03T12:00"})
        meta = core.parse_note(text)["meta"]
        self.assertEqual(path, "github-usage/2026-11.md")
        self.assertEqual(
            (meta["gh_month"], meta["gh_used"], meta["gh_limit"], meta["gh_pct"], meta["gh_days"], meta["gh_repos"], meta["gh_final"]),
            ("2026-11", "200", "2000", "10", "100;80;20", "vault 180; jobs 20", "no"),
        )
        self.assertIn("200 of 2,000 free minutes used (10%)", text)
        self.assertNotIn("publish", meta)

    def test_a_finished_month_says_so(self):
        reading = github_usage.quota([row("2026-10-12", "vault", 68)], set(), 2026, 10, 31, 2000)
        text = github_usage.usage_note({**reading, "account": "ada", "at": "2026-11-03T12:00", "final": True})[1]
        self.assertEqual(core.parse_note(text)["meta"]["gh_final"], "yes")
        self.assertNotIn("Starts again", text)


class SyncTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 15, 12, 0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "repo"
        root.mkdir()
        self.brain = Brain(root)
        self.brain.init()
        self.calls: list[str] = []
        self.usage = {11: [row("2026-11-14", "vault", 300), row("2026-11-14", "site", 50)], 10: [row("2026-10-12", "vault", 68)]}
        github_usage._retry_after = 0.0
        self.addCleanup(setattr, github_usage, "_retry_after", 0.0)

    def http(self, method: str, path: str):
        self.calls.append(path)
        if path == "/user":
            return 200, {"login": "ada", "plan": {"name": "free"}}
        if path.startswith("/user/repos"):
            return 200, [{"name": "site", "private": False}, {"name": "vault", "private": True}]
        month = int(path.rsplit("month=", 1)[1])
        return 200, {"usageItems": self.usage.get(month, [])}

    def meta(self, month: str) -> dict:
        return core.parse_note(self.brain.read(f"github-usage/{month}.md")["text"])["meta"]

    def test_the_first_pass_notes_this_month_and_fills_in_the_past(self):
        self.assertTrue(github_usage.sync(self.brain, self.NOW, self.http))
        now = self.meta("2026-11")
        self.assertEqual((now["gh_account"], now["gh_used"], now["gh_limit"], now["gh_public"], now["gh_final"]), ("ada", "300", "2000", "50", "no"))
        self.assertEqual((self.meta("2026-10")["gh_used"], self.meta("2026-10")["gh_final"]), ("68", "yes"))
        self.assertEqual(self.meta("2026-06")["gh_used"], "0")
        self.assertFalse(self.brain.resolve("github-usage/2026-05.md").exists())

    def test_it_asks_again_only_after_an_hour_and_never_for_a_closed_month(self):
        github_usage.sync(self.brain, self.NOW, self.http)
        self.calls.clear()
        self.assertFalse(github_usage.sync(self.brain, self.NOW + dt.timedelta(minutes=59), self.http))
        self.assertEqual(self.calls, [])
        self.usage[11].append(row("2026-11-15", "vault", 25))
        self.assertTrue(github_usage.sync(self.brain, self.NOW + dt.timedelta(minutes=61), self.http))
        self.assertEqual(self.meta("2026-11")["gh_used"], "325")
        self.assertEqual([path for path in self.calls if "billing" in path], ["/users/ada/settings/billing/usage?year=2026&month=11"])

    def test_a_refusal_writes_nothing_and_waits(self):
        def refused(method: str, path: str):
            self.calls.append(path)
            return 403, {"message": "Resource not accessible"}

        self.assertFalse(github_usage.sync(self.brain, self.NOW, refused))
        self.assertFalse((self.brain.dir / "github-usage").exists())
        self.assertFalse(github_usage.sync(self.brain, self.NOW, self.http), "waits before asking again")
        self.assertEqual(self.calls, ["/user"])


if __name__ == "__main__":
    unittest.main()

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


class CalibrationTests(unittest.TestCase):
    """GitHub's own figure, once given, is where the count starts."""

    AT = dt.datetime(2026, 11, 16, 14, 51)
    ROWS = [row("2026-11-10", "vault", 1000), row("2026-11-16", "vault", 440)]

    def reading(self, extra: int = 0, day: int = 16) -> dict:
        rows = self.ROWS + ([row(f"2026-11-{day:02d}", "jobs", extra)] if extra else [])
        return github_usage.quota(rows, set(), 2026, 11, day, 2000)

    def test_it_counts_on_from_githubs_figure(self):
        anchor = {"used": 1801, "at": self.AT, "api": 1440}
        same = github_usage.calibrate(self.reading(), anchor, self.AT + dt.timedelta(hours=1))
        self.assertEqual((same["used"], same["pct"], same["since"], same["api_used"], same["level"]), (1801, 90, 0, 1440, "critical"))
        later = github_usage.calibrate(self.reading(extra=8), anchor, self.AT + dt.timedelta(hours=3))
        self.assertEqual((later["api_used"], later["since"], later["used"]), (1448, 10, 1811), "8 new minutes count as 10: the rows were a quarter behind")
        self.assertEqual((later["pace"], later["projected"]), (None, 1811), "no month-end figure within a day of the reading")

    def test_after_a_day_the_month_end_figure_paces_from_the_reading(self):
        anchor = {"used": 1801, "at": self.AT, "api": 1440}
        now = self.AT + dt.timedelta(days=2)
        reading = github_usage.calibrate(self.reading(extra=16, day=18), anchor, now)
        days_left = (dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc) - now.astimezone(dt.timezone.utc)).total_seconds() / 86400
        self.assertEqual((reading["since"], reading["used"], reading["pace"]), (20, 1821, 10.0))
        self.assertEqual(reading["projected"], round(1821 + 10 * days_left))

    def test_a_figure_far_from_the_rows_is_not_trusted_as_a_ratio(self):
        wild = github_usage.calibrate(self.reading(extra=10), {"used": 1801, "at": self.AT, "api": 100}, self.AT)
        self.assertEqual(wild["scale"], 3.0)
        none = github_usage.calibrate(self.reading(extra=10), {"used": 50, "at": self.AT, "api": 0}, self.AT)
        self.assertEqual((none["scale"], none["used"]), (1.0, 1500))

    def test_the_note_says_where_the_figure_comes_from(self):
        reading = github_usage.calibrate(self.reading(extra=8), {"used": 1801, "at": self.AT, "api": 1440}, self.AT + dt.timedelta(hours=3))
        text = github_usage.usage_note({**reading, "account": "ada", "at": "2026-11-16T17:51"})[1]
        meta = core.parse_note(text)["meta"]
        self.assertEqual(
            (meta["gh_used"], meta["gh_anchor_used"], meta["gh_anchor_at"], meta["gh_anchor_api"], meta["gh_api_used"], meta["gh_since"], meta["gh_scale"], meta["gh_pace"]),
            ("1811", "1801", "2026-11-16T14:51", "1440", "1448", "10", "1.251", ""),
        )
        self.assertIn("1,811 of 2,000 free minutes used (91%), 189 left", text)
        self.assertIn("GitHub's own count: 1,801 at 2026-11-16 14:51, and 10 more since", text)
        self.assertEqual(github_usage.anchor_of(meta, "2026-11"), {"used": 1801, "at": self.AT, "api": 1440})
        self.assertIsNone(github_usage.anchor_of(meta, "2026-12"), "a reading belongs to its month")
        self.assertIsNone(github_usage.anchor_of({"gh_used": "5"}, "2026-11"))


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
        github_usage._retry_accounts.clear()
        self.addCleanup(github_usage._retry_accounts.clear)

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

    def test_githubs_figure_survives_the_hourly_rewrite_and_closes_the_month(self):
        self.usage[11] = [row("2026-11-14", "vault", 1440)]
        reading = github_usage.set_anchor(self.brain, 1801, now=self.NOW, http=self.http)
        self.assertEqual((reading["used"], reading["anchor"]["api"]), (1801, 1440), "a fresh figure pairs with the rows of this moment")
        self.assertIn("1,801 of 2,000 minutes (90%), 199 left", github_usage.summary(reading))
        self.usage[11].append(row("2026-11-15", "vault", 4))
        self.assertTrue(github_usage.sync(self.brain, self.NOW + dt.timedelta(minutes=61), self.http))
        meta = self.meta("2026-11")
        self.assertEqual((meta["gh_used"], meta["gh_api_used"], meta["gh_since"], meta["gh_anchor_used"], meta["gh_anchor_at"]), ("1806", "1444", "5", "1801", "2026-11-15T12:00"))
        december = dt.datetime(2026, 12, 2, 12, 0)
        self.assertTrue(github_usage.sync(self.brain, december, self.http))
        closed = self.meta("2026-11")
        self.assertEqual((closed["gh_final"], closed["gh_used"], closed["gh_anchor_used"]), ("yes", "1806", "1801"), "history keeps the counted figure")
        self.assertNotIn("gh_anchor_used", self.meta("2026-12"))

    def test_setting_the_figure_does_not_wait_for_the_hour_and_takes_an_older_reading(self):
        self.usage[11] = [row("2026-11-14", "vault", 1442)]
        github_usage.sync(self.brain, self.NOW, self.http)
        at = self.NOW - dt.timedelta(hours=3)
        reading = github_usage.set_anchor(self.brain, 1801, at=at, api=1440, now=self.NOW + dt.timedelta(minutes=5), http=self.http)
        self.assertEqual((reading["used"], reading["since"]), (1804, 3))
        self.assertEqual(self.meta("2026-11")["gh_anchor_api"], "1440")

    def test_a_figure_from_another_month_or_the_future_is_refused(self):
        for at in (dt.datetime(2026, 10, 20, 9, 0), self.NOW + dt.timedelta(hours=2)):
            with self.assertRaises(ValueError):
                github_usage.set_anchor(self.brain, 1801, at=at, now=self.NOW, http=self.http)
        with self.assertRaises(ValueError):
            github_usage.set_anchor(self.brain, -1, now=self.NOW, http=self.http)
        self.assertEqual(self.calls, [], "nothing was asked of GitHub")

    def test_a_refusal_writes_nothing_and_waits(self):
        def refused(method: str, path: str):
            self.calls.append(path)
            return 403, {"message": "Resource not accessible"}

        self.assertFalse(github_usage.sync(self.brain, self.NOW, refused))
        self.assertFalse((self.brain.dir / "github-usage").exists())
        self.assertFalse(github_usage.sync(self.brain, self.NOW, self.http), "waits before asking again")
        self.assertEqual(self.calls, ["/user"])

    # ---- a second account: its own sign-in, its own folder, its own wait

    def other(self, method: str, path: str):
        self.calls.append(path)
        if path == "/user":
            return 200, {"login": "Grace"}  # a sign-in without the scope that shows the plan
        if path.startswith("/user/repos"):
            return 200, [{"name": "diary", "private": True}]
        month = int(path.rsplit("month=", 1)[1])
        return 200, {"usageItems": [row("2026-11-14", "diary", 120)] if month == 11 else []}

    def test_another_account_keeps_its_months_in_its_own_folder(self):
        self.assertTrue(github_usage.sync(self.brain, self.NOW, self.http))
        self.assertTrue(github_usage.sync(self.brain, self.NOW, self.other, account="grace"))
        mine = self.meta("2026-11")
        hers = self.meta("grace/2026-11")
        self.assertEqual((mine["gh_account"], mine["gh_used"]), ("ada", "300"), "the main account's note is left alone")
        self.assertEqual((hers["gh_account"], hers["gh_used"], hers["gh_limit"], hers["title"]), ("Grace", "120", "2000", "GitHub Actions 2026-11 (grace)"))
        self.assertEqual(self.meta("grace/2026-10")["gh_final"], "yes")
        self.assertIn("/users/Grace/settings/billing/usage?year=2026&month=11", self.calls)
        self.assertIn("cairn vibe github <minutes> --account grace", self.brain.read("github-usage/grace/2026-11.md")["text"])

    def test_several_accounts_are_noted_in_one_go_and_each_waits_alone(self):
        def refused(method: str, path: str):
            return 403, {"message": "Resource not accessible"}

        noted = github_usage.sync_accounts(self.brain, ["grace", "@grace", "", "linus"], self.NOW, {"grace": self.other, "linus": refused}.get)
        self.assertEqual(noted, ["grace"], "a name is asked once, and a refusal is not a note")
        self.assertFalse((self.brain.dir / "github-usage" / "linus").exists())
        self.assertTrue(github_usage.sync(self.brain, self.NOW, self.http), "another account's refusal does not hold the main one back")
        self.assertEqual(github_usage.sync_accounts(self.brain, ["grace"], self.NOW + dt.timedelta(minutes=5), {"grace": self.other}.get), [], "fresh for an hour")

    def test_a_sign_in_of_someone_else_is_not_written_under_the_name_asked_for(self):
        self.assertFalse(github_usage.sync(self.brain, self.NOW, self.http, account="grace"))
        self.assertFalse((self.brain.dir / "github-usage").exists())

    def test_githubs_figure_can_be_set_for_another_account(self):
        reading = github_usage.set_anchor(self.brain, 150, now=self.NOW, http=self.other, account="grace")
        self.assertEqual((reading["used"], reading["anchor"]["api"]), (150, 120))
        self.assertIn("GitHub Actions 2026-11 (Grace): 150 of 2,000 minutes", github_usage.summary(reading))
        self.assertEqual(self.meta("grace/2026-11")["gh_anchor_used"], "150")
        self.assertFalse(self.brain.resolve("github-usage/2026-11.md").exists())

    def test_a_sign_in_that_may_not_see_billing_says_what_it_needs(self):
        def hidden(method: str, path: str):
            return (404, {"message": "Not Found"}) if "billing" in path else self.other(method, path)

        with self.assertRaises(github_usage.publish.PublishError) as raised:
            github_usage.read_now(self.brain, "grace", self.NOW, hidden)
        self.assertIn("gh auth refresh -h github.com -s user", str(raised.exception))

    def test_an_account_name_cannot_point_outside_its_folder(self):
        for name in ("../secrets", "a/b", "", "-dash", "x" * 40):
            with self.assertRaises(ValueError):
                github_usage.account_name(name)
        self.assertEqual(github_usage.note_path("2026-11", "Grace-1"), "github-usage/Grace-1/2026-11.md")
        self.assertFalse(github_usage.sync(self.brain, self.NOW, self.other, account="../secrets"))


if __name__ == "__main__":
    unittest.main()

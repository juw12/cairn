"""Handing a private repo to another of your GitHub accounts: what is asked of GitHub, and what is refused first."""

import datetime as dt
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import go_to, launch_browser  # noqa: E402

from cairn import core, github_move, github_usage, publish  # noqa: E402
from cairn.brain import Brain  # noqa: E402
from cairn.server import make_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None


class MoveTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 19, 21, 40)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "repo"
        root.mkdir()
        self.brain = Brain(root)
        self.brain.init()
        self.calls: list[tuple] = []
        self.repo = {"name": "vault", "private": True, "owner": {"login": "ada"}, "permissions": {"admin": True}}
        self.transfer = (202, {"name": "vault"})
        self.signed_in = {"ada", "grace"}

    def http(self, method: str, path: str, body: dict | None = None):
        self.calls.append((method, path, body))
        if method == "GET":
            return (200, self.repo) if self.repo else (404, {"message": "Not Found"})
        return self.transfer

    def token_of(self, name: str) -> str:
        if name.lower() not in self.signed_in:
            raise publish.PublishError(f"The GitHub CLI is not signed in as {name} on this computer.")
        return "secret"

    def move(self, repo="vault", source="ada", target="grace", brain="own"):
        return github_move.move(self.brain if brain == "own" else brain, repo, source, target, http=self.http, token_of=self.token_of, now=self.NOW)

    def refused(self, **kwargs) -> str:
        with self.assertRaises(github_move.MoveError) as raised:
            self.move(**kwargs)
        return str(raised.exception)

    def posts(self) -> list[tuple]:
        return [call for call in self.calls if call[0] == "POST"]

    def test_it_asks_github_once_and_leaves_a_note(self):
        result = self.move()
        self.assertEqual(self.posts(), [("POST", "/repos/ada/vault/transfer", {"new_owner": "grace"})])
        self.assertEqual((result["repo"], result["from"], result["to"], result["at"]), ("vault", "ada", "grace", "2026-11-19T21:40"))
        self.assertIn("GitHub mailed grace a link", result["message"])
        self.assertEqual(result["note"], "github-usage/moves/ada-vault.md")
        note = core.parse_note(self.brain.read(result["note"])["text"])
        self.assertEqual(
            (note["meta"]["gh_move_repo"], note["meta"]["gh_move_from"], note["meta"]["gh_move_to"], note["meta"]["gh_move_at"]),
            ("vault", "ada", "grace", "2026-11-19T21:40"),
        )
        self.assertIn("ada stays on the repo as a collaborator", note["body"])

    def test_asking_again_rewrites_the_same_note(self):
        self.move()
        again = github_move.move(self.brain, "vault", "ada", "grace", http=self.http, token_of=self.token_of, now=self.NOW + dt.timedelta(hours=30))
        self.assertEqual(again["note"], "github-usage/moves/ada-vault.md")
        self.assertEqual(core.parse_note(self.brain.read(again["note"])["text"])["meta"]["gh_move_at"], "2026-11-21T03:40")

    def test_only_between_accounts_signed_in_on_this_computer(self):
        self.assertIn("not signed in as linus", self.refused(target="linus"))
        self.signed_in = {"grace"}
        self.assertIn("not signed in as ada", self.refused())
        self.assertEqual(self.calls, [], "GitHub was not asked anything")

    def test_a_public_repo_is_left_where_it_is(self):
        self.repo["private"] = False
        self.assertIn("minutes in a public repo are free", self.refused())
        self.assertEqual(self.posts(), [])

    def test_a_repo_that_is_not_the_owners_to_move_is_refused(self):
        self.repo["owner"] = {"login": "Grace"}
        self.assertEqual(self.refused(), "vault is already with grace.")
        self.repo["owner"] = {"login": "linus"}
        self.assertIn("belongs to linus now", self.refused())
        self.repo = {**self.repo, "owner": {"login": "ada"}, "permissions": {"admin": False, "push": True}}
        self.assertIn("only its owner can", self.refused())
        self.repo = None
        self.assertEqual(self.refused(), "ada has no repo named vault.")
        self.assertEqual(self.posts(), [])

    def test_names_that_cannot_be_right_never_reach_github(self):
        for kwargs in ({"repo": "../vault"}, {"repo": "a/b"}, {"repo": ""}, {"repo": ".."}, {"source": "ada/x"}, {"target": ""}, {"target": "ADA"}):
            with self.subTest(**kwargs):
                self.refused(**kwargs)
        self.assertEqual(self.calls, [])

    def test_githubs_refusal_is_passed_on_and_leaves_no_note(self):
        self.transfer = (422, {"message": "Validation Failed", "errors": [{"message": "grace already has a repository named vault"}]})
        message = self.refused()
        self.assertIn("422: Validation Failed. grace already has a repository named vault", message)
        self.assertFalse((self.brain.dir / "github-usage").exists())

    def test_without_a_brain_the_request_still_goes_out(self):
        result = self.move(brain=None)
        self.assertEqual((result["note"], len(self.posts())), ("", 1))


class CommandTests(unittest.TestCase):
    """`cairn vibe github move REPO --to NAME`, and `--account NAME` for another account's figure."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brain = Brain(self.root)
        self.brain.init()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from cairn.cli import main

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin.isatty", return_value=False, create=True):
            code = main(["--root", str(self.root), "vibe", "github", *args])
        return code, out.getvalue(), err.getvalue()

    def test_a_move_needs_a_yes_and_a_new_owner(self):
        with mock.patch.object(github_move, "move") as move:
            self.assertEqual(self.run_cli("move", "vault", "--account", "ada")[0], 2, "no --to")
            code, _, err = self.run_cli("move", "vault", "--to", "grace", "--account", "ada")
            self.assertEqual((code, "add --yes" in err), (2, True))
            self.assertEqual(self.run_cli("move", "vault", "--to", "grace", "--yes")[0], 2, "nobody named as the owner")
        move.assert_not_called()

    def test_a_move_with_a_yes_asks_once(self):
        with mock.patch.object(github_move, "move", return_value={"message": "GitHub mailed grace a link."}) as move:
            code, out, _ = self.run_cli("move", "vault", "--to", "@grace", "--account", "ada", "--yes")
        self.assertEqual((code, out.strip()), (0, "GitHub mailed grace a link."))
        move.assert_called_once_with(mock.ANY, "vault", "ada", "grace")

    def test_the_owner_defaults_to_the_main_account_of_this_months_note(self):
        month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
        self.brain.write(f"github-usage/{month}.md", f"---\ntitle: GitHub Actions {month}\ntype: note\ngh_account: ada\ngh_month: {month}\n---\n")
        with mock.patch.object(github_move, "move", side_effect=github_move.MoveError("vault is public.")) as move:
            code, _, err = self.run_cli("move", "vault", "--to", "grace", "--yes")
        move.assert_called_once_with(mock.ANY, "vault", "ada", "grace")
        self.assertEqual((code, err.strip()), (1, "vault is public."))

    def test_another_accounts_figure_is_read_and_set_by_name(self):
        reading = {"month": "2026-11", "account": "grace", "used": 120, "limit": 2000, "pct": 6}
        with mock.patch.object(github_usage, "read_now", return_value=reading) as read_now:
            code, out, _ = self.run_cli("--account", "grace")
        read_now.assert_called_once_with(mock.ANY, "grace")
        self.assertEqual((code, out.strip()), (0, "GitHub Actions 2026-11 (grace): 120 of 2,000 minutes (6%), 1,880 left."))
        with mock.patch.object(github_usage, "set_anchor", return_value=reading) as set_anchor:
            self.assertEqual(self.run_cli("150", "--account", "grace")[0], 0)
        self.assertEqual(set_anchor.call_args.kwargs["account"], "grace")
        self.assertEqual(self.run_cli("--account", "../x")[0], 2)


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class MovePageTests(unittest.TestCase):
    """The Vibe page: a meter per account, and the button that hands a repo to the account with more room."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = Brain(Path(self.tmp.name))
        self.brain.init()
        self.now = dt.datetime.now()
        github_usage.read_now(self.brain, "", self.now, self.account("ada", {"vault": 1500, "jobs": 300}))
        github_usage.read_now(self.brain, "grace", self.now, self.account("grace", {"diary": 120}))
        self.server = make_server(self.brain, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = launch_browser(self.pw)
        self.addCleanup(self.browser.close)
        self.errors: list[str] = []

    def account(self, login: str, minutes: dict):
        today = dt.datetime.now(dt.timezone.utc).date()
        rows = [
            {"date": f"{today.isoformat()}T00:00:00Z", "product": "actions", "sku": "Actions Linux", "quantity": used, "unitType": "Minutes", "repositoryName": repo}
            for repo, used in minutes.items()
        ]

        def http(method: str, path: str, body=None):
            if path == "/user":
                return 200, {"login": login, "plan": {"name": "free"}}
            if path.startswith("/user/repos"):
                return 200, []
            return 200, {"usageItems": rows if path.endswith(f"year={today.year}&month={today.month}") else []}

        return http

    def moved(self, brain, repo, source, target):
        stamp = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
        path, text = github_move.move_note(repo, source, target, stamp)
        brain.write(path, text)
        return {"repo": repo, "from": source, "to": target, "at": stamp, "note": path, "message": f"GitHub mailed {target} a link."}

    def open(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 1200})
        page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/")
        page.wait_for_selector("#nav button")
        go_to(page, "vibe")
        page.wait_for_selector(".vibe-github")
        return page

    def test_each_account_has_a_meter_and_a_repo_moves_only_after_a_yes(self):
        with mock.patch.object(github_move, "move", side_effect=self.moved) as move:
            page = self.open()
            heads = page.locator(".vibe-github > header h2").all_inner_texts()
            self.assertEqual(heads, ["GitHub Actions · ada", "GitHub Actions · grace"])
            self.assertIn("1,800 of 2,000", page.locator(".vibe-github").nth(0).inner_text())
            self.assertIn("120 of 2,000", page.locator(".vibe-github").nth(1).inner_text())
            self.assertIn("--account grace", page.locator(".vibe-github").nth(1).locator(".gh-note").inner_text())
            buttons = page.locator("[data-action=vibe-gh-move]")
            self.assertEqual(buttons.all_inner_texts(), ["vault → grace", "jobs → grace"], "only towards the account with more room")
            self.assertIn("Move one to grace (1,880 min left)", page.locator(".gh-move").inner_text())

            buttons.first.click()
            page.wait_for_selector(".modal")
            self.assertIn("ada stays on the repo as a collaborator", page.locator(".modal").inner_text())
            page.locator(".modal-wrap").click(position={"x": 4, "y": 4})  # beside the dialog: not now
            page.wait_for_selector(".modal", state="detached")
            move.assert_not_called()

            page.locator("[data-action=vibe-gh-move]").first.click()
            page.click(".modal button[type=submit]")
            page.wait_for_selector(".gh-moving")
            move.assert_called_once_with(mock.ANY, "vault", "ada", "grace")
            self.assertIn("vault → grace: waiting for grace to open GitHub's mail", page.locator(".gh-moving").inner_text())
            self.assertEqual(page.locator("[data-action=vibe-gh-move]").all_inner_texts(), ["jobs → grace"])
            self.assertEqual(self.errors, [])

    def test_one_account_alone_offers_no_move(self):
        for month in (self.brain.dir / "github-usage" / "grace").iterdir():
            month.unlink()
        page = self.open()
        self.assertEqual(page.locator(".vibe-github").count(), 1)
        self.assertEqual(page.locator("[data-action=vibe-gh-move]").count(), 0)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

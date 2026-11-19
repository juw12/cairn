"""Which GitHub account this computer pushes as: what is read, what a switch runs, and what it refuses."""

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import go_to, launch_browser  # noqa: E402

from cairn import github_push, github_usage  # noqa: E402
from cairn.brain import Brain  # noqa: E402
from cairn.server import make_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None


class Computer:
    """git and the GitHub CLI of one computer, as far as a switch can tell: the CLI's sign-ins, the one
    it uses, the helper git asks for github.com, and a sign-in git saved by itself before."""

    def __init__(self):
        self.tokens = {"Grace": "cli-secret-grace", "ada": "cli-secret-ada"}  # listed as the CLI pleases; shown by name
        self.active = "ada"
        self.helper = "osxkeychain"
        self.saved = ("Grace", "saved-secret-grace")
        self.knows_json = True
        self.installed = True
        self.overridden = False  # something outside Cairn keeps git on its saved sign-in
        self.calls: list[list[str]] = []

    def run(self, args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
        self.calls.append(args)
        if args[0] == "gh" and not self.installed:
            return 127, "", ""
        if args[:3] == ["gh", "auth", "status"]:
            if "--json" in args:
                if not self.knows_json:
                    return 1, "", "unknown flag: --json"
                return 0, json.dumps({"hosts": {"github.com": [{"login": name, "active": name == self.active} for name in self.tokens]}}), ""
            lines = [f"  ✓ Logged in to github.com account {name} (keyring)\n  - Active account: {str(name == self.active).lower()}" for name in self.tokens]
            return 0, "github.com\n" + "\n".join(lines), ""
        if args[:3] == ["gh", "config", "get"]:
            return 0, self.active + "\n", ""
        if args[:3] == ["gh", "auth", "token"]:
            return (0, self.tokens[args[-1]] + "\n", "") if args[-1] in self.tokens else (1, "", "no such account")
        if args[:3] == ["gh", "auth", "switch"]:
            self.active = args[-1]
            return 0, "", ""
        if args[:3] == ["gh", "auth", "setup-git"]:
            self.helper = "!/usr/local/bin/gh auth git-credential"
            return 0, "", ""
        if args[:2] == ["git", "config"]:
            return (0, self.helper + "\n", "") if self.helper else (1, "", "")
        if args[:2] == ["git", "credential"]:
            if "auth git-credential" in self.helper and not self.overridden:
                user, password = self.active, self.tokens[self.active]
            else:
                user, password = self.saved
            if not password:
                return 128, "", "fatal: could not read Username"
            return 0, f"protocol=https\nhost=github.com\nusername={user}\npassword={password}\n", ""
        raise AssertionError(f"unexpected command: {args}")

    def ran(self, *start: str) -> int:
        return sum(1 for call in self.calls if call[: len(start)] == list(start))


class PushTests(unittest.TestCase):
    def setUp(self):
        self.pc = Computer()

    def refused(self, name: str) -> str:
        with self.assertRaises(github_push.PushError) as raised:
            github_push.switch(name, run=self.pc.run)
        return str(raised.exception)

    def test_a_sign_in_git_saved_itself_is_named_but_not_vouched_for(self):
        reading = github_push.status(run=self.pc.run)
        self.assertEqual(reading, {"accounts": ["ada", "Grace"], "active": "ada", "pusher": "Grace", "sure": False, "viaCli": False})
        self.assertIn("go to GitHub as Grace (a sign-in git saved by itself", github_push.summary(reading))

    def test_the_first_switch_makes_git_ask_the_cli_and_says_how_to_undo_it(self):
        result = github_push.switch("ada", run=self.pc.run)
        self.assertEqual((result["pusher"], result["active"], result["sure"], result["viaCli"]), ("ada", "ada", True, True))
        self.assertEqual((self.pc.ran("gh", "auth", "switch"), self.pc.ran("gh", "auth", "setup-git")), (1, 1))
        self.assertIn("now go to GitHub as ada", result["message"])
        self.assertIn("git config --global --unset-all credential.https://github.com.helper", result["message"])

    def test_after_that_a_switch_is_one_command_each_way(self):
        github_push.switch("ada", run=self.pc.run)
        self.pc.calls.clear()
        result = github_push.switch("@grace", run=self.pc.run)  # any case, with or without the @
        self.assertEqual((result["pusher"], result["sure"]), ("Grace", True))
        self.assertIn(["gh", "auth", "switch", "--hostname", "github.com", "--user", "Grace"], self.pc.calls)
        self.assertEqual(self.pc.ran("gh", "auth", "setup-git"), 0)
        self.assertEqual(result["message"], "Pushes from this computer now go to GitHub as Grace.")
        self.assertEqual(github_push.switch("ada", run=self.pc.run)["pusher"], "ada")
        self.assertEqual(github_push.summary(github_push.status(run=self.pc.run)), "Pushes from this computer go to GitHub as ada (git asks the GitHub CLI). Also signed in: Grace.")

    def test_only_an_account_the_cli_is_signed_in_to(self):
        self.assertIn("not signed in as mallory", self.refused("mallory"))
        self.assertIn("is not a GitHub account name", self.refused("../x"))
        self.assertEqual((self.pc.ran("gh", "auth", "switch"), self.pc.active), (0, "ada"))

    def test_a_switch_git_does_not_follow_is_not_called_done(self):
        self.pc.overridden = True
        message = self.refused("ada")
        self.assertIn("git on this computer still signs in as Grace", message)

    def test_one_account_is_nothing_to_switch_and_git_is_not_asked(self):
        del self.pc.tokens["Grace"]
        reading = github_push.status(run=self.pc.run)
        self.assertEqual((reading["accounts"], reading["pusher"]), (["ada"], ""))
        self.assertEqual(self.pc.ran("git"), 0)
        self.assertIn("signed in to 1 account (ada)", github_push.summary(reading))

    def test_no_sign_in_at_all_says_so(self):
        self.pc.saved = ("", "")
        reading = github_push.status(run=self.pc.run)
        self.assertEqual((reading["pusher"], reading["sure"]), ("", False))
        self.assertIn("git has no sign-in for github.com", github_push.summary(reading))

    def test_an_older_cli_and_no_cli(self):
        self.pc.knows_json = False
        self.assertEqual(github_push.signed_in(run=self.pc.run), ["ada", "Grace"])
        self.pc.installed = False
        self.assertEqual(github_push.status(run=self.pc.run)["accounts"], [])

    def test_the_list_of_accounts_is_asked_for_once_in_a_while_even_when_it_is_empty(self):
        self.pc.tokens.clear()
        with mock.patch.object(github_push, "_run", self.pc.run), mock.patch.object(github_push, "_accounts", {"at": float("-inf"), "names": []}):
            for _ in range(3):
                self.assertEqual(github_push.status()["accounts"], [])
            self.assertEqual(self.pc.ran("gh", "auth", "status", "--hostname", "github.com", "--json"), 1)
            github_push.status(fresh=True)
            self.assertEqual(self.pc.ran("gh", "auth", "status", "--hostname", "github.com", "--json"), 2)

    def test_no_secret_leaves_the_module(self):
        before = github_push.status(run=self.pc.run)
        after = github_push.switch("ada", run=self.pc.run)
        said = json.dumps([before, after, github_push.summary(before), github_push.summary(after)])
        self.assertNotIn("secret", said)

    def test_the_live_page_never_waits_and_a_switch_shows_at_once(self):
        release = threading.Event()

        def slow():
            release.wait(5)
            return {"accounts": ["ada", "Grace"], "active": "ada", "pusher": "ada", "sure": True, "viaCli": True}

        fresh = {"value": None, "at": float("-inf"), "thread": None}
        with mock.patch.object(github_push, "_soon", fresh), mock.patch.object(github_push, "status", slow):
            started = time.monotonic()
            self.assertIsNone(github_push.status_soon())
            self.assertLess(time.monotonic() - started, 2)
            release.set()
            fresh["thread"].join(5)
            self.assertEqual(github_push.status_soon()["pusher"], "ada")
        with mock.patch.object(github_push, "_soon", fresh), mock.patch.object(github_push, "_run", self.pc.run), mock.patch.object(github_push, "_accounts", {"at": float("-inf"), "names": []}):
            github_push.switch("grace")
            self.assertEqual(github_push.status_soon(), {"accounts": ["ada", "Grace"], "active": "Grace", "pusher": "Grace", "sure": True, "viaCli": True})


class CommandTests(unittest.TestCase):
    """`cairn vibe github push-as` says who pushes; with a name it switches."""

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from cairn.cli import main

        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as root, redirect_stdout(out), redirect_stderr(err):
            code = main(["--root", root, "vibe", "github", "push-as", *args])
        return code, out.getvalue(), err.getvalue()

    def test_says_who_pushes_and_switches_by_name(self):
        pc = Computer()
        with mock.patch.object(github_push, "_run", pc.run), mock.patch.object(github_push, "_accounts", {"at": float("-inf"), "names": []}), mock.patch.object(github_push, "_soon", {"value": None, "at": float("-inf"), "thread": None}):
            code, out, _ = self.run_cli()
            self.assertEqual((code, "go to GitHub as Grace" in out), (0, True))
            code, out, _ = self.run_cli("ada")
            self.assertEqual((code, out.startswith("Pushes from this computer now go to GitHub as ada.")), (0, True))
            code, _, err = self.run_cli("mallory")
            self.assertEqual((code, "not signed in as mallory" in err), (1, True))
            self.assertEqual(self.run_cli("ada", "grace")[0], 2)
        self.assertEqual(pc.active, "ada")


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class PushPageTests(unittest.TestCase):
    """The Vibe page: a chip per account under the meters, the one in use pressed, and a press that switches."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = Brain(Path(self.tmp.name))
        self.brain.init()
        self.pc = Computer()
        self.server = make_server(self.brain, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = launch_browser(self.pw)
        self.addCleanup(self.browser.close)
        self.errors: list[str] = []
        switch = github_push.switch
        for patch in (
            mock.patch.object(github_push, "status_soon", lambda: github_push.status(run=self.pc.run)),
            mock.patch.object(github_push, "switch", lambda name: switch(name, run=self.pc.run)),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def open(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 1200})
        page.add_init_script("window.CAIRN_TEST_POLL_MS = 150")
        page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/")
        page.wait_for_selector("#nav button")
        go_to(page, "vibe")
        return page

    def pressed(self, page) -> list[str]:
        return page.locator("[data-action=vibe-gh-push][aria-pressed=true]").all_inner_texts()

    def test_the_first_press_asks_then_every_press_switches(self):
        page = self.open()
        page.wait_for_selector(".vibe-ghpush")
        self.assertEqual(page.locator("[data-action=vibe-gh-push]").all_inner_texts(), ["ada", "Grace"])
        self.assertEqual(self.pressed(page), ["Grace"])
        self.assertIn("go to GitHub as Grace", page.locator(".vibe-ghpush header").inner_text())
        self.assertIn("a sign-in it saved by itself", page.locator(".vibe-ghpush .gh-note").inner_text())

        page.locator("[data-action=vibe-gh-push]", has_text="ada").click()
        page.wait_for_selector(".modal")
        self.assertIn("gh auth setup-git", page.locator(".modal").inner_text())
        page.locator(".modal-wrap").click(position={"x": 4, "y": 4})  # beside the dialog: not now
        page.wait_for_selector(".modal", state="detached")
        self.assertEqual((self.pc.active, self.pc.ran("gh", "auth", "setup-git")), ("ada", 0))
        self.assertEqual(self.pressed(page), ["Grace"])

        page.locator("[data-action=vibe-gh-push]", has_text="ada").click()
        page.click(".modal button[type=submit]")
        page.wait_for_selector("[data-action=vibe-gh-push][aria-pressed=true]:has-text('ada')")
        self.assertEqual(self.pc.ran("gh", "auth", "setup-git"), 1)
        self.assertIn("follow this choice", page.locator(".vibe-ghpush .gh-note").inner_text())

        page.locator("[data-action=vibe-gh-push]", has_text="Grace").click()  # no question the second time
        page.wait_for_selector("[data-action=vibe-gh-push][aria-pressed=true]:has-text('Grace')")
        self.assertEqual((self.pc.active, self.pc.ran("gh", "auth", "setup-git"), page.locator(".modal").count()), ("Grace", 1, 0))

        self.pc.active = "ada"  # switched in a terminal, or by an assistant: the page follows
        page.wait_for_selector("[data-action=vibe-gh-push][aria-pressed=true]:has-text('ada')")
        self.assertIn("go to GitHub as ada", page.locator(".vibe-ghpush header").inner_text())
        self.assertEqual(self.errors, [])

    def test_it_sits_under_the_meters_and_one_account_shows_no_switch(self):
        http = lambda method, path, body=None: (200, {"login": "ada", "plan": {"name": "free"}} if path == "/user" else [] if path.startswith("/user/repos") else {"usageItems": []})  # noqa: E731
        github_usage.read_now(self.brain, "", dt.datetime.now(), http)
        page = self.open()
        page.wait_for_selector(".vibe-github + .vibe-ghpush")
        page.click('.vibe-panel[data-panel="github"] [data-action=vibe-panel-fold]')  # folded, one short line says both
        page.wait_for_selector('.vibe-panel.folded[data-panel="github"]')
        self.assertEqual(page.locator('.vibe-panel[data-panel="github"] .vp-sum').inner_text(), "0% used · pushes as Grace")
        page.click('.vibe-panel[data-panel="github"] [data-action=vibe-panel-fold]')
        page.wait_for_selector(".vibe-ghpush")
        del self.pc.tokens["Grace"]
        page.wait_for_selector(".vibe-ghpush", state="detached")
        self.assertEqual(page.locator(".vibe-github").count(), 1)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

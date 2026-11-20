"""A copy of every GitHub repo on this computer: real git against stand-in repos, a stand-in GitHub."""

import datetime as dt
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cairn import core, github_copy, watch
from cairn.brain import Brain
from cairn.cli import main

NOW = dt.datetime(2026, 11, 20, 13, 40)
TOKEN = "cli-secret-ada"


def git(folder: Path, *args: str) -> str:
    identity = ["-c", "user.name=Ada", "-c", "user.email=ada@example.com", "-c", "init.defaultBranch=main"]
    result = subprocess.run(["git", *identity, *args], cwd=folder, capture_output=True, text=True, check=True)
    return result.stdout.strip()


class Hub:
    """GitHub as far as a copy can tell: the repos of each account, each a real git repo in a folder."""

    def __init__(self, root: Path):
        self.root = root
        self.repos: dict[str, list[dict]] = {}
        self.refused: set[str] = set()

    def add(self, owner: str, name: str, private: bool = False) -> Path:
        folder = self.root / owner / name
        folder.mkdir(parents=True)
        git(folder, "init", "--quiet")
        self.repos.setdefault(owner, []).append({"owner": {"login": owner}, "name": name, "private": private, "pushed_at": "", "clone_url": str(folder)})
        self.push(owner, name, "first")
        return folder

    def push(self, owner: str, name: str, text: str) -> str:
        folder = self.root / owner / name
        (folder / "notes.txt").write_text(text, encoding="utf-8")
        git(folder, "add", "notes.txt")
        git(folder, "commit", "--quiet", "-m", text)
        repo = next(repo for repo in self.repos[owner] if repo["name"] == name)
        repo["pushed_at"] = f"2026-11-20T0{len(git(folder, 'rev-list', 'HEAD').split())}:00:00Z"
        return git(folder, "rev-parse", "HEAD")

    def http_for(self, account: str):
        def http(method: str, path: str, body=None):
            if account in self.refused:
                return 401, {"message": "Bad credentials"}
            page = int(path.rsplit("page=", 1)[1])
            return 200, self.repos.get(account, []) if page == 1 else []
        return http


def head(folder: Path, key: str) -> str:
    return git(folder, "--git-dir", str(folder / (key + ".git")), "rev-parse", "HEAD")


class CopyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.hub = Hub(base / "hub")
        self.folder = base / "copy"
        self.env = mock.patch.dict(os.environ, {"CAIRN_HOME": str(base / "home")})
        self.env.start()
        self.addCleanup(self.env.stop)
        root = base / "project"
        (root / "brain").mkdir(parents=True)
        self.brain = Brain(root)

    def copy(self, accounts=("ada",), **more):
        more.setdefault("now", NOW)
        return github_copy.copy(self.folder, list(accounts), http_for=self.hub.http_for, token_for=lambda account: TOKEN, **more)


class TestCopy(CopyCase):
    def test_every_repo_of_every_account_gets_a_full_copy(self):
        self.hub.add("ada", "engine")
        first = self.hub.push("ada", "engine", "second")
        self.hub.add("ada", "diary", private=True)
        self.hub.add("Grace", "compiler")
        said: list[str] = []
        state = self.copy(("ada", "Grace"), say=said.append)
        self.assertEqual(head(self.folder, "ada/engine"), first)
        self.assertEqual(git(self.folder, "--git-dir", str(self.folder / "ada" / "engine.git"), "rev-list", "--count", "HEAD"), "2")  # the whole history
        self.assertEqual(sorted(state["repos"]), ["Grace/compiler", "ada/diary", "ada/engine"])
        self.assertEqual(github_copy.counts(state), {"repos": 3, "private": 1, "failed": 0, "gone": 0})
        self.assertEqual(sorted(said), ["Grace/compiler: new", "ada/diary: new", "ada/engine: new"])
        self.assertEqual(json.loads((self.folder / "copy.json").read_text(encoding="utf-8")), state)

    def test_a_later_run_fetches_only_what_was_pushed_to(self):
        self.hub.add("ada", "engine")
        self.hub.add("ada", "diary")
        self.copy()
        newest = self.hub.push("ada", "engine", "second")
        ran: list[list[str]] = []

        def run(args, token):
            ran.append(args)
            return github_copy._run_git(args, token)

        said: list[str] = []
        state = self.copy(run=run, say=said.append, now=NOW + dt.timedelta(days=1))
        self.assertEqual([args[2] for args in ran], ["fetch"])  # diary was not asked about
        self.assertEqual(said, ["ada/engine: updated"])
        self.assertEqual(head(self.folder, "ada/engine"), newest)
        self.assertEqual(state["repos"]["ada/engine"]["copied_at"], "2026-11-21T13:40")
        self.assertEqual(state["repos"]["ada/diary"]["copied_at"], "2026-11-20T13:40")

    def test_a_repo_that_fails_is_named_and_tried_again(self):
        self.hub.add("ada", "engine")
        self.hub.repos["ada"].append({"owner": {"login": "ada"}, "name": "lost", "private": True, "pushed_at": "x", "clone_url": str(self.hub.root / "nowhere")})
        state = self.copy()
        self.assertTrue(state["repos"]["ada/lost"]["error"])
        self.assertEqual(github_copy.counts(state), {"repos": 1, "private": 0, "failed": 1, "gone": 0})
        self.assertFalse((self.folder / "ada" / "lost.git").exists())
        self.assertFalse((self.folder / "ada" / "lost.git.part").exists())  # nothing half made is left
        self.assertIn("1 could not be copied", github_copy.summary(self.folder, state))
        self.hub.repos["ada"].pop()
        self.hub.add("ada", "lost", private=True)
        state = self.copy()
        self.assertEqual(github_copy.counts(state), {"repos": 2, "private": 1, "failed": 0, "gone": 0})

    def test_a_repo_gone_from_github_keeps_its_copy(self):
        self.hub.add("ada", "engine")
        kept = self.hub.push("ada", "engine", "second")
        self.hub.add("ada", "diary")
        self.copy()
        self.hub.repos["ada"] = [repo for repo in self.hub.repos["ada"] if repo["name"] != "engine"]
        state = self.copy()
        self.assertTrue(state["repos"]["ada/engine"]["gone"])
        self.assertFalse(state["repos"]["ada/diary"]["gone"])
        self.assertEqual(head(self.folder, "ada/engine"), kept)
        self.assertIn("1 are no longer on GitHub; their copies are kept", github_copy.summary(self.folder, state))

    def test_an_account_github_will_not_list_leaves_its_copies_alone(self):
        self.hub.add("ada", "engine")
        self.copy()
        self.hub.refused.add("ada")
        state = self.copy()
        self.assertEqual(state["problems"], ["ada: GitHub would not list the repos (401: Bad credentials)."])
        self.assertFalse(state["repos"]["ada/engine"].get("gone"))  # a refusal is not a deleted repo
        self.assertTrue((self.folder / "ada" / "engine.git").is_dir())

    def test_the_sign_in_reaches_git_by_its_environment_only(self):
        seen = {}

        def fake_run(command, **options):
            seen.update(command=command, env=options["env"])
            return subprocess.CompletedProcess(command, 128, "", f"fatal: could not read from https://x-access-token:{TOKEN}@github.com/ada/engine.git")

        with mock.patch.object(github_copy.subprocess, "run", fake_run):
            code, output = github_copy._run_git(["clone", "--mirror", "https://github.com/ada/engine.git", "there"], TOKEN)
        self.assertNotIn(TOKEN, " ".join(seen["command"]))
        self.assertEqual(seen["env"]["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")  # for github.com, nowhere else
        self.assertTrue(seen["env"]["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic "))
        self.assertEqual(seen["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn(TOKEN, github_copy._last_line(output, TOKEN))  # and what git says back is shown without it

    def test_the_copy_holds_no_sign_in(self):
        self.hub.add("ada", "engine")
        self.copy()
        for path in self.folder.rglob("*"):
            if path.is_file() and path.stat().st_size < 100_000:
                self.assertNotIn(TOKEN.encode(), path.read_bytes(), path)


class TestNoteAndCommand(CopyCase):
    def test_the_note_tells_every_device_what_this_computer_holds(self):
        self.hub.add("ada", "engine")
        self.hub.add("ada", "diary", private=True)
        state = self.copy()
        path = github_copy.write_note(self.brain, self.folder, state, "Work laptop")
        self.assertEqual(path, "github-usage/copy/work-laptop.md")
        note = core.parse_note(self.brain.read(path)["text"])
        self.assertEqual(note["meta"]["gh_copy_repos"], "2")
        self.assertEqual(note["meta"]["gh_copy_at"], "2026-11-20T13:40")
        self.assertIn("2 repos (1 private) have a full copy in", note["body"])
        self.assertIn("git clone", note["body"])
        self.hub.push("ada", "engine", "second")
        github_copy.write_note(self.brain, self.folder, self.copy(now=NOW + dt.timedelta(hours=1)), "Work laptop")  # written over, not beside
        self.assertEqual(core.parse_note(self.brain.read(path)["text"])["meta"]["gh_copy_at"], "2026-11-20T14:40")

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        patches = [
            mock.patch.object(github_copy.github_push, "signed_in", lambda fresh=False: ["ada"]),
            mock.patch.object(github_copy.github_usage, "account_token", lambda account: TOKEN),
            mock.patch.object(github_copy.publish, "make_http", lambda token: self.hub.http_for("ada")),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = main(["--root", str(self.brain.root), "vibe", "github", "copy", *args])
        return code, out.getvalue(), err.getvalue()

    def test_the_command_remembers_its_folder(self):
        self.hub.add("ada", "engine")
        code, out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("Usage: cairn vibe github copy FOLDER", err)
        code, out, err = self.run_cli(str(self.folder))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("ada/engine: new", out)
        self.assertIn("1 repos (0 private) have a full copy in", out)
        self.assertEqual(github_copy.settings(), {"folder": str(self.folder.resolve()), "accounts": []})
        self.assertTrue(list((self.brain.root / "brain" / "github-usage" / "copy").glob("*.md")))
        newest = self.hub.push("ada", "engine", "second")
        code, out, err = self.run_cli()  # no folder: the one remembered
        self.assertEqual((code, err), (0, ""))
        self.assertIn("ada/engine: updated", out)
        self.assertEqual(head(self.folder.resolve(), "ada/engine"), newest)


class TestWatcher(CopyCase):
    def setUp(self):
        super().setUp()
        github_copy._watch.update(thread=None, after=0.0)
        self.addCleanup(lambda: github_copy._watch.update(thread=None, after=0.0))

    def test_a_copy_is_due_once_it_is_a_day_old_and_never_before_one_was_made(self):
        self.assertFalse(github_copy.is_due({"repos": {}}, NOW))
        self.assertFalse(github_copy.is_due({"at": "2026-11-20T13:40"}, NOW + dt.timedelta(hours=23)))
        self.assertTrue(github_copy.is_due({"at": "2026-11-20T13:40"}, NOW + dt.timedelta(hours=24)))

    def test_the_watcher_starts_nothing_without_a_folder_and_only_when_asked(self):
        self.assertFalse(github_copy.sync_soon(self.brain))
        with mock.patch.object(github_copy, "sync_soon", side_effect=AssertionError("not asked for")):
            watch.watch_once(self.brain, opencode=False, vector=False)

    def test_the_watcher_brings_an_old_copy_up_to_date(self):
        self.hub.add("ada", "engine")
        self.copy(now=dt.datetime.now() - dt.timedelta(days=2))
        github_copy.remember(self.folder, ["ada"])
        newest = self.hub.push("ada", "engine", "second")
        real = github_copy.copy
        with mock.patch.object(github_copy, "copy", lambda folder, names: real(folder, names, http_for=self.hub.http_for, token_for=lambda account: TOKEN)):
            lines = watch.watch_once(self.brain, opencode=False, vector=False, repo_copy=True)
            self.assertEqual(lines, ["GitHub: bringing this computer's copy of your repos up to date"])
            self.assertFalse(github_copy.sync_soon(self.brain))  # one run at a time
            deadline = time.monotonic() + 60
            while github_copy._watch["thread"] is not None and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertEqual(head(self.folder, "ada/engine"), newest)
        self.assertFalse(github_copy.is_due(github_copy.load_state(self.folder), dt.datetime.now()))
        self.assertTrue(list((self.brain.root / "brain" / "github-usage" / "copy").glob("*.md")))


if __name__ == "__main__":
    unittest.main()

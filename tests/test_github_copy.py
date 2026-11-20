"""A copy of every GitHub repo on this computer: real git against stand-in repos, a stand-in GitHub."""

import collections
import datetime as dt
import http.server
import json
import os
import threading
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cairn import core, github_copy, github_extras, watch
from cairn.brain import Brain
from cairn.cli import main

NOW = dt.datetime(2026, 11, 20, 13, 40)
TOKEN = "cli-secret-ada"


def git(folder: Path, *args: str) -> str:
    identity = ["-c", "user.name=Ada", "-c", "user.email=ada@example.com", "-c", "init.defaultBranch=main"]
    result = subprocess.run(["git", *identity, *args], cwd=folder, capture_output=True, text=True, check=True)
    return result.stdout.strip()


class Hub:
    """GitHub as far as a copy can tell: the repos of each account, each a real git repo in a folder, and
    what GitHub keeps outside git for each (issues, comments, releases and their files, Actions names)."""

    def __init__(self, root: Path):
        self.root = root
        self.repos: dict[str, list[dict]] = {}
        self.refused: set[str] = set()
        self.more: dict[str, dict] = {}  # "owner/name" -> issues, comments, review, releases, variables, secrets
        self.files: dict[str, bytes] = {}  # url of a release file -> what is in it
        self.asked: list[str] = []  # every path asked for, in order
        self.fetched: list[str] = []  # every release file downloaded, in order
        self.silent = False  # True: the one call that says what changed fails

    def add(self, owner: str, name: str, private: bool = False, **about) -> Path:
        folder = self.root / owner / name
        folder.mkdir(parents=True)
        git(folder, "init", "--quiet")
        self.repos.setdefault(owner, []).append({"owner": {"login": owner}, "name": name, "private": private, "pushed_at": "", "clone_url": str(folder), **about})
        self.more[f"{owner}/{name}"] = {"issues": [], "comments": [], "review": [], "releases": [], "variables": [], "secrets": []}
        self.push(owner, name, "first")
        return folder

    def push(self, owner: str, name: str, text: str, file: str = "notes.txt") -> str:
        folder = self.root / owner / name
        (folder / file).parent.mkdir(parents=True, exist_ok=True)
        (folder / file).write_text(text, encoding="utf-8")
        git(folder, "add", file)
        git(folder, "commit", "--quiet", "-m", text)
        repo = next(repo for repo in self.repos[owner] if repo["name"] == name)
        repo["pushed_at"] = f"2026-11-20T0{len(git(folder, 'rev-list', 'HEAD').split())}:00:00Z"
        return git(folder, "rev-parse", "HEAD")

    def issue(self, key: str, title: str, pull: bool = False, at: str = "2026-11-01T00:00:00Z") -> None:
        row = {"number": len(self.more[key]["issues"]) + 1, "title": title, "updated_at": at}
        self.more[key]["issues"].append({**row, "pull_request": {}} if pull else row)

    def release(self, key: str, tag: str, **files: bytes) -> None:
        assets = []
        for name, content in files.items():
            url = f"asset://{key}/{tag}/{name}"
            self.files[url] = content
            assets.append({"name": name.replace("_", "."), "size": len(content), "url": url})
        self.more[key]["releases"].append({"tag_name": tag, "assets": assets, "updated_at": f"2026-11-0{len(self.more[key]['releases']) + 1}T00:00:00Z"})

    def fetch(self, url: str, path: Path, token: str) -> None:
        self.fetched.append(url)
        if url not in self.files:
            raise github_extras.ExtrasError(f"could not download {path.name} (404)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.files[url])

    def stamps(self, account: str) -> dict:
        nodes = []
        for repo in self.repos.get(account, []):
            more = self.more.get(f"{account}/{repo['name']}")
            if more is None:
                continue
            pulls = [row for row in more["issues"] if "pull_request" in row]
            issues = [row for row in more["issues"] if "pull_request" not in row]

            def part(rows: list) -> dict:
                return {"totalCount": len(rows), "nodes": [{"updatedAt": max(row["updated_at"] for row in rows)}] if rows else []}

            nodes.append({"nameWithOwner": f"{account}/{repo['name']}", "issues": part(issues), "pullRequests": part(pulls), "releases": part(more["releases"])})
        return {"data": {"viewer": {"repositories": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}

    def http_for(self, account: str):
        def http(method: str, path: str, body=None):
            self.asked.append(path)
            if account in self.refused:
                return 401, {"message": "Bad credentials"}
            if path == "/graphql":
                return (502, None) if self.silent else (200, self.stamps(account))
            page = int(path.rsplit("page=", 1)[1]) if "page=" in path else 1
            if path.startswith("/user/repos"):
                return 200, self.repos.get(account, []) if page == 1 else []
            _, _, owner, name, rest = path.split("?")[0].split("/", 4)
            more = self.more[f"{owner}/{name}"]
            if rest == "actions/variables":
                return 200, {"variables": more["variables"]}
            if rest == "actions/secrets":
                return 200, {"secrets": [{"name": secret} for secret in more["secrets"]]}
            rows = {
                "issues": more["issues"],
                "pulls": [row for row in more["issues"] if "pull_request" in row],
                "issues/comments": more["comments"],
                "pulls/comments": more["review"],
                "releases": more["releases"],
            }[rest]
            return 200, rows[(page - 1) * 100:page * 100]
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
        more.setdefault("fetch", self.hub.fetch)
        return github_copy.copy(self.folder, list(accounts), http_for=self.hub.http_for, token_for=lambda account: TOKEN, **more)

    def extras(self, key: str, name: str):
        return json.loads((github_extras.extras_folder(self.folder, key) / name).read_text(encoding="utf-8"))


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
        self.assertEqual([args[2] for args in ran if args[2] == "fetch"], ["fetch"])
        self.assertFalse([args for args in ran if "diary" in " ".join(args)])  # diary was not asked about
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
            mock.patch.object(github_extras, "download", self.hub.fetch),
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


class TestExtras(CopyCase):
    """What is not in git: issues and pull requests, releases and their files, the wiki, Actions names."""

    def test_what_is_not_in_git_is_kept_beside_the_mirror(self):
        self.hub.add("ada", "engine", description="An engine", has_wiki=False)
        self.hub.push("ada", "engine", "on: push", ".github/workflows/test.yml")
        self.hub.issue("ada/engine", "It will not start")
        self.hub.issue("ada/engine", "Start it", pull=True)
        more = self.hub.more["ada/engine"]
        more["comments"].append({"body": "Thanks"})
        more["review"].append({"body": "This line"})
        more["variables"].append({"name": "REGION", "value": "eu", "updated_at": "x"})
        more["secrets"] += ["DEPLOY_KEY", "TOKEN"]
        self.hub.release("ada/engine", "v1.0", engine_exe=b"0123456789", notes_txt=b"first")
        said: list[str] = []
        state = self.copy(say=said.append)
        self.assertEqual([row["title"] for row in self.extras("ada/engine", "issues.json")], ["It will not start", "Start it"])
        self.assertEqual(self.extras("ada/engine", "comments.json"), [{"body": "Thanks"}])
        self.assertEqual(self.extras("ada/engine", "review-comments.json"), [{"body": "This line"}])
        self.assertEqual(self.extras("ada/engine", "releases.json")[0]["tag_name"], "v1.0")
        files = github_extras.extras_folder(self.folder, "ada/engine") / "releases" / "v1.0"
        self.assertEqual((files / "engine.exe").read_bytes(), b"0123456789")
        self.assertEqual((files / "notes.txt").read_bytes(), b"first")
        actions = self.extras("ada/engine", "actions.json")
        self.assertEqual(actions["variables"], [{"name": "REGION", "value": "eu"}])
        self.assertEqual(actions["secrets"], ["DEPLOY_KEY", "TOKEN"])  # the names: GitHub never gives a value back
        self.assertEqual(self.extras("ada/engine", "repo.json"), {"name": "ada/engine", "description": "An engine", "private": False, "has_wiki": False})
        held = state["repos"]["ada/engine"]["extras"]
        self.assertEqual({name: held[name] for name in ("talk", "releases", "files", "bytes", "secrets")}, {"talk": 2, "releases": 1, "files": 2, "bytes": 15, "secrets": 2})
        self.assertNotIn("error", held)
        self.assertEqual(said, ["ada/engine: new", "ada/engine: with 2 issues and pull requests, 1 releases with 2 files (0 KB), 2 secret names"])
        self.assertIn("Beside them: 2 issues and pull requests, 1 releases with 2 files (0 KB), 2 secret names.", github_copy.summary(self.folder, state))

    def test_only_a_repo_that_changed_is_asked_again(self):
        self.hub.add("ada", "engine")
        self.hub.add("ada", "diary")
        self.hub.issue("ada/engine", "It will not start")
        self.hub.issue("ada/diary", "Dear diary")
        self.copy()
        self.hub.asked.clear()
        self.copy(now=NOW + dt.timedelta(days=1))
        self.assertEqual([path.split("?")[0] for path in self.hub.asked], ["/user/repos", "/graphql"])  # two calls, whatever the number of repos
        self.hub.issue("ada/engine", "Still will not", at="2026-11-21T00:00:00Z")
        self.hub.asked.clear()
        state = self.copy(now=NOW + dt.timedelta(days=2))
        self.assertEqual({path.split("/")[3] for path in self.hub.asked if path.startswith("/repos/")}, {"engine"})
        self.assertEqual(len(self.extras("ada/engine", "issues.json")), 2)
        self.assertEqual(state["repos"]["ada/diary"]["extras"]["at"], "2026-11-20T13:40")
        self.hub.asked.clear()
        self.copy(now=NOW + dt.timedelta(days=9))  # and once a week whatever happened
        self.assertEqual({path.split("/")[3] for path in self.hub.asked if path.startswith("/repos/")}, {"engine", "diary"})

    def test_when_github_will_not_say_what_changed_the_repos_wait_for_their_week(self):
        self.hub.add("ada", "engine")
        self.hub.issue("ada/engine", "It will not start")
        self.hub.silent = True
        state = self.copy()
        self.assertEqual(state["repos"]["ada/engine"]["extras"]["talk"], 1)  # nothing known: everything is asked for
        self.hub.asked.clear()
        self.copy(now=NOW + dt.timedelta(days=1))
        self.assertFalse([path for path in self.hub.asked if path.startswith("/repos/")])
        self.copy(now=NOW + dt.timedelta(days=7))
        self.assertTrue([path for path in self.hub.asked if path.startswith("/repos/")])

    def test_a_release_file_is_downloaded_once_and_a_lost_one_is_named_and_tried_again(self):
        self.hub.add("ada", "engine")
        self.hub.release("ada/engine", "v1.0", engine_exe=b"0123456789", notes_txt=b"first")
        lost = self.hub.files.pop("asset://ada/engine/v1.0/notes_txt")
        state = self.copy()
        held = state["repos"]["ada/engine"]["extras"]
        self.assertEqual((held["files"], held["bytes"]), (1, 10))
        self.assertIn("could not download notes.txt", held["error"])
        self.assertIn("1 have parts that would not come", github_copy.summary(self.folder, state))
        self.assertEqual(github_copy.extras_counts(state)["failed"], 1)
        self.hub.files["asset://ada/engine/v1.0/notes_txt"] = lost
        self.hub.fetched.clear()
        state = self.copy(now=NOW + dt.timedelta(hours=1))  # nothing changed on GitHub, but it failed: tried again
        self.assertEqual(self.hub.fetched, ["asset://ada/engine/v1.0/notes_txt"])  # the file already held is not fetched twice
        self.assertNotIn("error", state["repos"]["ada/engine"]["extras"])
        self.assertEqual(state["repos"]["ada/engine"]["extras"]["files"], 2)

    def test_a_file_that_comes_back_the_wrong_size_is_not_kept(self):
        self.hub.add("ada", "engine")
        self.hub.release("ada/engine", "v1.0", engine_exe=b"0123456789")
        self.hub.files["asset://ada/engine/v1.0/engine_exe"] = b"01234"  # cut short on the way
        state = self.copy()
        self.assertIn("wrong size", state["repos"]["ada/engine"]["extras"]["error"])
        self.assertFalse((github_extras.extras_folder(self.folder, "ada/engine") / "releases" / "v1.0" / "engine.exe").exists())
        self.assertEqual(len(self.hub.fetched), 2)  # asked for once more before giving up

    def test_a_file_cut_short_once_is_asked_for_again_in_the_same_run(self):
        self.hub.add("ada", "engine")
        self.hub.release("ada/engine", "v1.0", engine_exe=b"0123456789")
        url, whole = "asset://ada/engine/v1.0/engine_exe", b"0123456789"
        self.hub.files[url] = b"01234"
        real = self.hub.fetch

        def cut_short_once(asked, path, token):
            real(asked, path, token)
            self.hub.files[url] = whole

        self.hub.fetch = cut_short_once
        state = self.copy()
        self.assertNotIn("error", state["repos"]["ada/engine"]["extras"])
        self.assertEqual(self.hub.fetched, [url, url])
        self.assertEqual((github_extras.extras_folder(self.folder, "ada/engine") / "releases" / "v1.0" / "engine.exe").read_bytes(), whole)

    def test_the_files_of_a_repo_come_a_few_at_a_time(self):
        self.hub.add("ada", "engine")
        self.hub.release("ada/engine", "v1.0", a_bin=b"1", b_bin=b"22", c_bin=b"333", d_bin=b"4444", e_bin=b"55555")
        real, lock, now, most = self.hub.fetch, threading.Lock(), [0], [0]

        def slow(url, path, token):
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
            time.sleep(0.2)
            real(url, path, token)
            with lock:
                now[0] -= 1

        self.hub.fetch = slow
        held = self.copy()["repos"]["ada/engine"]["extras"]
        self.assertEqual((held["files"], held["bytes"]), (5, 15))
        self.assertNotIn("error", held)
        self.assertGreater(most[0], 1)
        self.assertLessEqual(most[0], github_extras.FILES_AT_ONCE)

    def test_a_tag_or_file_name_cannot_leave_its_folder(self):
        self.assertEqual(github_extras.safe_name("release/v1:0"), "release_v1_0")
        self.assertEqual(github_extras.safe_name("../.."), "_")
        self.assertEqual(github_extras.safe_name(".."), "_")
        self.hub.add("ada", "engine")
        self.hub.release("ada/engine", "../../../out", engine_exe=b"0123456789")
        self.copy()
        inside = github_extras.extras_folder(self.folder, "ada/engine") / "releases"
        self.assertEqual([path.name for path in inside.iterdir()], ["_.._.._out"])
        self.assertFalse((self.folder / "out").exists())

    def test_a_repo_with_no_workflow_is_not_asked_for_its_secrets(self):
        self.hub.add("ada", "engine")
        self.hub.more["ada/engine"]["secrets"].append("DEPLOY_KEY")
        state = self.copy()
        self.assertFalse([path for path in self.hub.asked if "/actions/" in path])
        self.assertEqual(state["repos"]["ada/engine"]["extras"]["secrets"], 0)
        self.assertFalse((github_extras.extras_folder(self.folder, "ada/engine") / "actions.json").exists())

    def test_with_issues_switched_off_the_pull_requests_are_asked_for_alone(self):
        self.hub.add("ada", "engine", has_issues=False)
        self.hub.issue("ada/engine", "Start it", pull=True)
        self.copy()
        asked = [path.split("?")[0] for path in self.hub.asked]
        self.assertIn("/repos/ada/engine/pulls", asked)
        self.assertNotIn("/repos/ada/engine/issues", asked)
        self.assertEqual([row["title"] for row in self.extras("ada/engine", "issues.json")], ["Start it"])

    def test_the_wiki_is_mirrored_when_there_is_one(self):
        self.hub.add("ada", "engine", has_wiki=True)
        self.hub.add("ada", "diary", has_wiki=True)  # switched on, no page in it: nothing to clone, and no failure
        wiki = self.hub.root / "ada" / "engine.wiki.git"
        wiki.mkdir()
        git(wiki, "init", "--quiet")
        (wiki / "Home.md").write_text("Welcome", encoding="utf-8")
        git(wiki, "add", "Home.md")
        git(wiki, "commit", "--quiet", "-m", "home")
        state = self.copy()
        held = github_extras.extras_folder(self.folder, "ada/engine") / "wiki.git"
        self.assertEqual(git(self.folder, "--git-dir", str(held), "show", "HEAD:Home.md"), "Welcome")
        self.assertTrue(state["repos"]["ada/engine"]["extras"]["wiki"])
        self.assertFalse(state["repos"]["ada/diary"]["extras"]["wiki"])
        self.assertNotIn("error", state["repos"]["ada/diary"]["extras"])
        self.assertIn("1 wikis", github_copy.summary(self.folder, state))
        (wiki / "Home.md").write_text("Welcome back", encoding="utf-8")
        git(wiki, "commit", "--quiet", "-am", "again")
        self.hub.push("ada", "engine", "second")
        self.copy(now=NOW + dt.timedelta(days=1))
        self.assertEqual(git(self.folder, "--git-dir", str(held), "show", "HEAD:Home.md"), "Welcome back")

    def test_files_kept_in_git_lfs_are_fetched_into_the_mirror(self):
        self.hub.add("ada", "engine")
        self.hub.push("ada", "engine", "*.bin filter=lfs diff=lfs merge=lfs -text", ".gitattributes")
        self.hub.add("ada", "diary")
        asked: list[list[str]] = []

        def run(args, token):
            if "lfs" in args[2:3]:
                asked.append(args)
                return 0, ""
            return github_copy._run_git(args, token)

        state = self.copy(run=run)
        self.assertEqual(asked, [["--git-dir", str(self.folder / "ada" / "engine.git"), "lfs", "fetch", "--all", "origin"]])
        self.assertTrue(state["repos"]["ada/engine"]["extras"]["lfs"])
        self.assertFalse(state["repos"]["ada/diary"]["extras"]["lfs"])

    def test_the_sign_in_is_not_passed_on_to_the_file_store(self):
        seen: dict[str, str | None] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen[self.path] = self.headers.get("Authorization")
                if self.path == "/asset":
                    self.send_response(302)
                    self.send_header("Location", "/store")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "10")
                    self.end_headers()
                    self.wfile.write(b"0123456789")

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        target = Path(self.tmp.name) / "down" / "engine.exe"
        with mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            github_extras.download(f"http://127.0.0.1:{server.server_port}/asset", target, TOKEN)
            self.assertEqual(target.read_bytes(), b"0123456789")
            self.assertEqual(seen, {"/asset": f"Bearer {TOKEN}", "/store": None})
            with self.assertRaises(github_extras.ExtrasError):
                github_extras.download(f"http://127.0.0.1:{server.server_port + 1}/asset", target.with_name("other.exe"), TOKEN)
        self.assertEqual([path.name for path in target.parent.iterdir()], ["engine.exe"])  # nothing half made is left


class TestEveryComputer(CopyCase):
    """One disk is one accident away from none: the brain can ask every computer for its own copy."""

    def setUp(self):
        super().setUp()
        github_copy._watch.update(thread=None, after=0.0)
        self.addCleanup(lambda: github_copy._watch.update(thread=None, after=0.0))
        self.home = Path(self.tmp.name) / "second" / "github-copy"
        self.home.parent.mkdir()
        self.free = 500 * 1024**3
        usage = collections.namedtuple("usage", "total used free")
        for patch in (
            mock.patch.object(github_copy, "home_folder", lambda: self.home),
            mock.patch.object(github_copy.shutil, "disk_usage", lambda path: usage(0, 0, self.free)),
            mock.patch.object(github_copy.github_push, "signed_in", lambda fresh=False: ["ada"]),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def watch(self) -> list[str]:
        github_copy._watch["after"] = 0.0  # as if the watcher's minute between looks had passed
        real = github_copy.copy
        with mock.patch.object(github_copy, "copy", lambda folder, names: real(folder, names, http_for=self.hub.http_for, token_for=lambda account: TOKEN, fetch=self.hub.fetch)):
            lines = watch.watch_once(self.brain, opencode=False, vector=False, repo_copy=True)
            deadline = time.monotonic() + 60
            while github_copy._watch["thread"] is not None and time.monotonic() < deadline:
                time.sleep(0.05)
        return lines

    def test_a_watcher_that_leaves_the_minutes_to_another_computer_still_keeps_its_copy(self):
        seen = []
        with mock.patch.object(watch, "watch", lambda brain, **options: seen.append(options)):
            for flags in (["--no-github"], ["--no-github", "--no-copy"]):
                self.assertEqual(main(["--root", str(self.brain.root), "vibe", "watch", "--no-vector", *flags]), 0)
        self.assertEqual([(options["github"], options["repo_copy"]) for options in seen], [(False, True), (False, False)])

    def test_a_summary_says_whether_this_is_the_only_copy(self):
        self.hub.add("ada", "engine")
        state = self.copy()
        self.assertNotIn("other computer", github_copy.summary(self.folder, state))
        self.assertEqual(github_copy.other_copies(self.brain, "Work laptop"), [])
        self.assertIn("No other computer holds a copy", github_copy.summary(self.folder, state, []))
        github_copy.write_note(self.brain, self.folder, state, "Home desktop")
        github_copy.write_note(self.brain, self.folder, state, "Work laptop")
        others = github_copy.other_copies(self.brain, "Work laptop")
        self.assertEqual([(other["device"], other["at"], other["repos"]) for other in others], [("Home desktop", "2026-11-20T13:40", 1)])
        self.assertGreater(others[0]["bytes"], 0)
        self.assertIn("Also on Home desktop (checked 2026-11-20 13:40).", github_copy.summary(self.folder, state, others))

    def test_a_computer_nobody_asked_starts_its_own_copy_when_the_brain_says_every_computer(self):
        self.hub.add("ada", "engine")
        self.assertEqual(self.watch(), [])  # not asked: nothing starts, nothing is written
        self.assertEqual(github_copy.settings(), {})
        github_copy.write_note(self.brain, self.folder, self.copy(), "Home desktop")
        github_copy.set_everywhere(self.brain, True, "Home desktop", NOW)
        self.assertTrue(github_copy.everywhere(self.brain))
        self.assertEqual(self.watch(), ["GitHub: bringing this computer's copy of your repos up to date"])
        self.assertEqual(github_copy.settings(), {"folder": str(self.home), "accounts": []})
        self.assertEqual(head(self.home, "ada/engine"), head(self.folder, "ada/engine"))
        self.assertEqual(len(github_copy.other_copies(self.brain, "Home desktop")), 1)  # this computer says so in its note

    def test_a_computer_without_the_room_does_not_start_and_says_why(self):
        self.hub.add("ada", "engine")
        github_copy.set_everywhere(self.brain, True, "Home desktop", NOW)
        self.free = 3 * 1024**3
        self.assertEqual(self.watch(), [])
        self.assertFalse(self.home.exists())
        self.assertEqual(github_copy.settings(), {})
        note = self.brain.read(github_copy.note_path(github_copy.machine.device_name()))["text"]
        self.assertIn("a copy wants about 30 GB free", note)
        self.assertIn("and 3 GB is", note)
        self.assertEqual(github_copy.other_copies(self.brain, "Home desktop"), [])  # a note that holds nothing is not a copy
        self.assertEqual(github_copy.room_needed([{"bytes": 20 * 1024**3}]), 35 * 1024**3)

    def test_a_computer_not_signed_in_says_so_in_its_note(self):
        github_copy.set_everywhere(self.brain, True, "Home desktop", NOW)
        with mock.patch.object(github_copy.github_push, "signed_in", lambda fresh=False: []):
            self.watch()
        note = self.brain.read(github_copy.note_path(github_copy.machine.device_name()))["text"]
        self.assertIn("The GitHub CLI is not signed in on this computer", note)

    def test_asking_is_a_flag_of_the_command_and_can_be_taken_back(self):
        self.hub.add("ada", "engine")
        run_cli = TestNoteAndCommand.run_cli.__get__(self)
        code, out, err = run_cli("everywhere")
        self.assertEqual(code, 2)  # a computer with no copy of its own does not ask the others
        self.assertIn("Make this computer's copy first", err)
        code, out, err = run_cli(str(self.folder))
        self.assertIn("No other computer holds a copy", out)
        self.assertIn("cairn vibe github copy everywhere", out)
        self.hub.asked.clear()
        code, out, err = run_cli("everywhere")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(github_copy.everywhere(self.brain))
        self.assertIn("Every other computer whose Cairn watcher reads this brain now keeps its own copy", out)
        self.assertIn("Your other computers are asked for one", out)  # not told to ask again
        self.assertNotIn("asks your other computers", out)
        self.assertIn("1 repos (0 private) have a full copy in", out)
        self.assertEqual(self.hub.asked, [])  # asking is a note in the brain: GitHub is not called
        code, out, err = run_cli("everywhere", "off")
        self.assertEqual((code, err), (0, ""))
        self.assertFalse(github_copy.everywhere(self.brain))
        self.assertEqual(self.watch(), [])  # this computer has its day-fresh copy; no other is asked now
        github_copy.settings_path().unlink()
        self.assertEqual(self.watch(), [])
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()

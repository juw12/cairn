"""The cloud backup: what is locked and sent, what is recorded, when it runs by itself, and what it refuses.

The drive itself is not asked here. `send` (the helper that steers the drive's web page) is stood in for by a
made-up drive that keeps what it is given, so everything up to that call and after it is real: git bundles,
7-Zip archives opened again with the key, the record of what is held.
"""

import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import go_to, launch_browser  # noqa: E402

from cairn import cloud_backup, cloud_terabox  # noqa: E402
from cairn.brain import Brain  # noqa: E402
from cairn.cli import main  # noqa: E402
from cairn.server import make_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

HAS_TOOLS = bool(cloud_backup.git() and cloud_backup.seven_zip())
KEY = "test-key-for-the-archives"


def git(folder, *args):
    subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


class Drive:
    """Stands in for `send`: keeps a copy of every folder it is given, the way the drive would."""

    def __init__(self, root: Path):
        self.root = root
        self.calls: list[tuple] = []
        self.fail = ""
        self.signed_in = True

    def __call__(self, *args, log=print):
        self.calls.append(args)
        command = args[0]
        if self.fail:
            raise cloud_backup.CloudError(self.fail)
        if command == "check":
            return {"ok": True, "signedIn": self.signed_in}
        if command == "window":
            return {"ok": True, "opened": True}
        if command == "upload-folder":
            local, parent = Path(args[1]), args[2]
            target = self.root / parent.strip("/") / local.name
            if target.exists():
                raise cloud_backup.CloudError("is on TeraBox already")
            shutil.copytree(local, target)
            return {"ok": True, "files": len(list(target.iterdir())), "wrong": []}
        if command == "list":
            folder = self.root / args[1].strip("/")
            return {"ok": True, "files": {path.name: path.stat().st_size if path.is_file() else 0 for path in folder.iterdir()} if folder.is_dir() else {}}
        if command == "remove":
            for name in args[2:]:
                shutil.rmtree(self.root / args[1].strip("/") / name, ignore_errors=True)
            return {"ok": True, "removed": list(args[2:])}
        raise AssertionError(args)

    def folders(self, inside="Cairn backup"):
        folder = self.root / inside
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


class CloudCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "project"
        self.brain = Brain(self.project)
        self.brain.init()
        self.brain.create("note", "Coffee order", [], "Flat white.", "2026-11-21")
        if cloud_backup.git():
            git(self.project, "init", "-q", "-b", "main")
            git(self.project, "add", "-A")
            git(self.project, "commit", "-q", "-m", "first")
        self.drive = Drive(self.tmp / "drive")
        patches = [
            mock.patch.dict(os.environ, {"CAIRN_HOME": str(self.tmp / "cairn-home"), "CAIRN_BACKUP_KEY": KEY}),
            mock.patch.object(cloud_backup, "send", self.drive),
            mock.patch.object(cloud_backup, "sender_python", lambda: "python"),
            mock.patch.object(cloud_backup, "chrome", lambda: "chrome"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        cloud_backup._progress.update({"running": False, "step": ""})

    def set_up(self, **more):
        cloud_backup._write("settings.json", {"set_up": True, "drive": "terabox", "auto": True, **more})

    def open_archive(self, archive: Path, key: str = KEY) -> Path:
        out = Path(tempfile.mkdtemp(dir=self.tmp))
        done = subprocess.run([cloud_backup.seven_zip(), "x", f"-p{key}", "-bso0", "-bsp0", f"-o{out}", str(archive)], capture_output=True)
        if done.returncode != 0:
            raise ValueError("did not open")
        return out


class NotSetUpTests(CloudCase):
    def test_it_is_off_until_it_is_set_up_and_says_how(self):
        self.assertFalse(cloud_backup.status()["setUp"])
        self.assertIn("cairn cloud setup", cloud_backup.summary(cloud_backup.status()))
        for call in (lambda: cloud_backup.sync(self.brain), lambda: cloud_backup.start(self.brain)):
            with self.assertRaises(cloud_backup.CloudError) as caught:
                call()
            self.assertIn("cairn cloud setup", str(caught.exception))
        self.assertEqual(cloud_backup.due(), "")
        self.assertEqual(self.drive.calls, [])

    def test_setup_stops_at_what_only_the_person_can_do_and_carries_on_after(self):
        said: list[str] = []
        with mock.patch.object(cloud_backup, "seven_zip", lambda: ""):
            self.assertFalse(cloud_backup.setup(say=said.append))
        self.assertIn("7-Zip", " ".join(said))
        self.assertEqual(self.drive.calls, [])

        self.drive.signed_in = False
        said.clear()
        with mock.patch.object(cloud_backup, "seven_zip", lambda: "7z"), mock.patch.object(cloud_backup, "git", lambda: "git"):
            self.assertFalse(cloud_backup.setup(say=said.append))
            self.assertEqual([call[0] for call in self.drive.calls], ["check", "window"])
            self.assertIn("Sign in there", " ".join(said))
            self.assertFalse(cloud_backup.status()["setUp"])
            self.drive.signed_in = True
            self.assertTrue(cloud_backup.setup(say=said.append))
        reading = cloud_backup.status()
        self.assertEqual((reading["setUp"], reading["repos"], reading["auto"]), (True, False, True))
        self.assertIn("Copy it somewhere that is not this computer", " ".join(said))

    def test_a_key_is_made_once_and_kept_where_only_this_user_reads_it(self):
        with mock.patch.dict(os.environ, {"CAIRN_BACKUP_KEY": ""}), mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(cloud_backup.read_key(), "")
            made = cloud_backup.make_key()
            self.assertRegex(made, r"^([a-z2-9]{5}-){5}[a-z2-9]{5}$")
            self.assertEqual(cloud_backup.read_key(), made)
            path = cloud_backup.home() / "key.txt"
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn(str(path), cloud_backup.key_place())
        self.assertEqual(cloud_backup.read_key(), KEY)  # the setting of this computer comes first


@unittest.skipUnless(HAS_TOOLS, "needs git and 7-Zip")
class BrainTests(CloudCase):
    def test_the_brain_goes_up_locked_and_comes_back_whole(self):
        self.set_up()
        said: list[str] = []
        outcome = cloud_backup.sync(self.brain, log=said.append)
        self.assertTrue(outcome["ok"])
        self.assertIn("Sent: brain", outcome["message"])
        [folder] = self.drive.folders()
        self.assertRegex(folder, r"^brain \d{4}-\d{2}-\d{2} \d{6}$")
        sent_folder = self.tmp / "drive" / "Cairn backup" / folder
        self.assertEqual(sorted(path.name for path in sent_folder.iterdir()), ["CONTENTS.json", "HOW-TO-RESTORE.txt", "brain.7z"])
        # nothing readable in the archive without the key, not even the names
        self.assertNotIn(b"coffee", (sent_folder / "brain.7z").read_bytes().lower())
        with self.assertRaises(ValueError):
            self.open_archive(sent_folder / "brain.7z", "wrong-key")
        out = self.open_archive(sent_folder / "brain.7z")
        subprocess.run(["git", "clone", "-q", str(out / "brain.bundle"), str(out / "back")], check=True, capture_output=True)
        self.assertIn("Flat white.", (out / "back" / "brain" / "notes" / "coffee-order.md").read_text(encoding="utf-8"))
        contents = json.loads((sent_folder / "CONTENTS.json").read_text(encoding="utf-8"))["brain"]
        self.assertEqual((contents["kind"], contents["files"][0]["bytes"]), ("git bundle", (sent_folder / "brain.7z").stat().st_size))
        how = (sent_folder / "HOW-TO-RESTORE.txt").read_text(encoding="utf-8")
        self.assertIn("git clone NAME.bundle NAME", how)
        self.assertNotIn(KEY, how + json.dumps(contents) + " ".join(said) + json.dumps(cloud_backup.sent()))
        # nothing is left on this computer, and what is up is on record
        self.assertFalse((cloud_backup.home() / "work").exists())
        reading = cloud_backup.status()
        self.assertEqual((reading["brain"]["folder"], reading["last"]["ok"], reading["running"]), (folder, True, False))

    def test_a_new_copy_never_takes_an_old_name_and_old_copies_go_only_after_it_is_up(self):
        self.set_up()
        base = self.tmp / "drive" / "Cairn backup"
        for old in ("brain", "brain 2001-01-01 0900", "brain 2001-01-02 0900", "repos"):
            (base / old).mkdir(parents=True)
        cloud_backup.sync(self.brain, log=lambda line: None)
        kept = self.drive.folders()
        self.assertEqual(len(kept), 3, kept)
        self.assertEqual(kept[0], "brain 2001-01-02 0900")  # the one before stays
        self.assertEqual(kept[2], "repos")
        commands = [call[0] for call in self.drive.calls]
        self.assertLess(commands.index("upload-folder"), commands.index("remove"))
        removed = next(call for call in self.drive.calls if call[0] == "remove")
        self.assertEqual(removed[1:], ("/Cairn backup", "brain", "brain 2001-01-01 0900"))

    def test_a_run_that_fails_is_said_and_removes_nothing(self):
        self.set_up()
        (self.tmp / "drive" / "Cairn backup" / "brain 2001-01-01 0900").mkdir(parents=True)
        self.drive.fail = "TeraBox asks to sign in."
        with self.assertRaises(cloud_backup.CloudError):
            cloud_backup.sync(self.brain, log=lambda line: None)
        reading = cloud_backup.status()
        self.assertEqual((reading["last"]["ok"], reading["last"]["message"], reading["running"]), (False, "TeraBox asks to sign in.", False))
        self.assertIsNone(reading["brain"])
        self.assertEqual(self.drive.folders(), ["brain 2001-01-01 0900"])
        self.assertNotIn("remove", [call[0] for call in self.drive.calls])
        self.assertFalse((cloud_backup.home() / "work").exists())
        self.assertIn("did not finish: TeraBox asks to sign in.", cloud_backup.summary(reading))
        self.drive.fail = ""
        self.assertTrue(cloud_backup.sync(self.brain, log=lambda line: None)["ok"])  # the lock was let go

    def test_a_folder_without_git_is_kept_too(self):
        plain = Brain(self.tmp / "plain")
        plain.init()
        plain.create("note", "No history here", [], "Just files.", "2026-11-21")
        self.set_up()
        cloud_backup.sync(plain, log=lambda line: None)
        [folder] = self.drive.folders()
        out = self.open_archive(self.tmp / "drive" / "Cairn backup" / folder / "brain.7z")
        self.assertTrue(list(out.rglob("no-history-here.md")))

    def test_a_brain_with_half_its_history_is_refused(self):
        shallow = self.tmp / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.project}", str(shallow)], check=True, capture_output=True)
        git(self.project, "commit", "-q", "--allow-empty", "-m", "second")
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.project}", str(shallow) + "2"], check=True, capture_output=True)
        self.set_up()
        with self.assertRaises(cloud_backup.CloudError) as caught:
            cloud_backup.sync(Brain(str(shallow) + "2"), log=lambda line: None)
        self.assertIn("only part of its history", str(caught.exception))
        self.assertEqual(self.drive.folders(), [])


@unittest.skipUnless(HAS_TOOLS, "needs git and 7-Zip")
class RepoTests(CloudCase):
    def setUp(self):
        super().setUp()
        self.hub = self.tmp / "hub"
        self.pushed = {}
        for name in ("alpha", "beta", "empty"):
            bare = self.hub / "ada" / f"{name}.git"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
            self.pushed[name] = "2026-11-01T00:00:00Z"
        for name in ("alpha", "beta"):
            self.push(name, "first")
        for patch in (
            mock.patch.object(cloud_backup, "CLONE_FROM", str(self.hub / "{owner}" / "{name}.git")),
            mock.patch.object(cloud_backup, "repo_list", lambda owner: [{"name": name, "kb": 10, "pushed": when} for name, when in self.pushed.items()]),
            mock.patch.object(cloud_backup, "gh", lambda: "gh"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.set_up(repos=True, owner="ada")

    def push(self, name, text):
        clone = Path(tempfile.mkdtemp(dir=self.tmp))
        subprocess.run(["git", "clone", "-q", str(self.hub / "ada" / f"{name}.git"), str(clone)], check=True, capture_output=True)
        (clone / "file.txt").write_text(text, encoding="utf-8")
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", text)
        git(clone, "push", "-q", "origin", "HEAD:main")
        self.pushed[name] = f"2026-11-{len(text):02d}T00:00:00Z"

    def repo_folders(self):
        return self.drive.folders("Cairn backup/repos")

    def test_every_repo_once_then_only_what_was_pushed(self):
        outcome = cloud_backup.sync(self.brain, log=lambda line: None)
        self.assertEqual((outcome["repos"]["sent"], outcome["repos"]["held"], outcome["repos"]["on_github"]), (2, 2, 3))
        [first] = self.repo_folders()
        base = self.tmp / "drive" / "Cairn backup" / "repos"
        self.assertEqual(sorted(path.name for path in (base / first).iterdir()), ["CONTENTS.json", "HOW-TO-RESTORE.txt", "alpha.7z", "beta.7z"])
        out = self.open_archive(base / first / "alpha.7z")
        subprocess.run(["git", "clone", "-q", str(out / "alpha.bundle"), str(out / "back")], check=True, capture_output=True)
        self.assertEqual((out / "back" / "file.txt").read_text(encoding="utf-8"), "first")

        again = cloud_backup.sync(self.brain, log=lambda line: None)
        self.assertEqual(again["repos"]["sent"], 0)
        self.assertEqual(self.repo_folders(), [first])  # an empty repo is not asked for again either

        self.push("beta", "second!")
        with mock.patch.object(cloud_backup, "_stamp", lambda: "2030-01-01 0000"):
            third = cloud_backup.sync(self.brain, log=lambda line: None)
        self.assertEqual(third["repos"]["sent"], 1)
        self.assertEqual(self.repo_folders(), sorted([first, "2030-01-01 0000"]))
        newer = base / "2030-01-01 0000"
        self.assertEqual(sorted(path.name for path in newer.iterdir()), ["CONTENTS.json", "HOW-TO-RESTORE.txt", "beta.7z"])
        contents = json.loads((newer / "CONTENTS.json").read_text(encoding="utf-8"))
        self.assertEqual((contents["alpha"]["folder"], contents["beta"]["folder"]), (f"repos/{first}", "repos/2030-01-01 0000"))
        self.assertTrue((base / first / "beta.7z").is_file())  # the older archive stays where it was
        self.assertIn("1 repo(s) sent, 2 of 3 held", third["message"])

    def test_a_full_disk_makes_a_repo_wait_and_batches_keep_the_disk_light(self):
        with mock.patch.object(cloud_backup, "free_gb", lambda folder: 1.0):
            outcome = cloud_backup.sync(self.brain, log=lambda line: None)
        self.assertEqual((outcome["repos"]["sent"], outcome["repos"]["waiting"]), (0, 3))
        self.assertIn("3 waiting for room", outcome["message"])
        with mock.patch.object(cloud_backup, "BATCH_FILES", 1), mock.patch.object(cloud_backup, "_stamp", lambda: "2030-01-01 0000"):
            outcome = cloud_backup.sync(self.brain, log=lambda line: None)
        self.assertEqual(outcome["repos"]["sent"], 2)
        self.assertEqual(self.repo_folders(), ["2030-01-01 0000", "2030-01-01 0000 part 2"])

    def test_a_run_that_stops_half_way_starts_from_what_is_up(self):
        calls = {"n": 0}
        real = self.drive.__call__

        def flaky(*args, log=print):
            if args[0] == "upload-folder" and "/repos" in args[2]:
                calls["n"] += 1
                if calls["n"] == 2:
                    raise cloud_backup.CloudError("the connection dropped")
            return real(*args, log=log)

        with mock.patch.object(cloud_backup, "send", flaky), mock.patch.object(cloud_backup, "BATCH_FILES", 1):
            with self.assertRaises(cloud_backup.CloudError):
                cloud_backup.sync(self.brain, log=lambda line: None)
        self.assertEqual(cloud_backup.status()["reposHeld"], 1)
        outcome = cloud_backup.sync(self.brain, log=lambda line: None)
        self.assertEqual((outcome["repos"]["sent"], outcome["repos"]["held"]), (1, 2))


class TimingTests(CloudCase):
    def test_once_a_day_the_repos_once_a_week_and_not_right_after_a_failure(self):
        now = dt.datetime(2026, 11, 21, 12, 0)
        ago = lambda hours: (now - dt.timedelta(hours=hours)).isoformat(timespec="minutes")  # noqa: E731
        self.set_up(repos=True, owner="ada")
        self.assertEqual(cloud_backup.due(now), "all")  # never sent
        cloud_backup._write("sent.json", {"brain": {"at": ago(2)}, "repos_at": ago(2), "last": {"at": ago(2), "ok": True}})
        self.assertEqual(cloud_backup.due(now), "")
        cloud_backup._write("sent.json", {"brain": {"at": ago(25)}, "repos_at": ago(25), "last": {"at": ago(25), "ok": True}})
        self.assertEqual(cloud_backup.due(now), "brain")
        cloud_backup._write("sent.json", {"brain": {"at": ago(25)}, "repos_at": ago(24 * 8), "last": {"at": ago(25), "ok": True}})
        self.assertEqual(cloud_backup.due(now), "all")
        cloud_backup._write("sent.json", {"brain": {"at": ago(48)}, "last": {"at": ago(1), "ok": False, "message": "x"}})
        self.assertEqual(cloud_backup.due(now), "")  # it failed an hour ago: not again yet
        cloud_backup._write("sent.json", {"brain": {"at": ago(48)}, "last": {"at": ago(7), "ok": False, "message": "x"}})
        self.assertEqual(cloud_backup.due(now), "all")
        cloud_backup.change(auto=False)
        self.assertEqual(cloud_backup.due(now), "")
        cloud_backup.change(auto=True, repos=False)
        self.assertEqual(cloud_backup.due(now), "brain")

    def test_the_daily_turn_runs_a_due_backup_and_survives_a_failure(self):
        ran: list = []
        stop = threading.Event()

        def fake_sync(brain, repos=None, log=print):
            ran.append(repos)
            if len(ran) == 1:
                raise cloud_backup.CloudError("no connection")
            stop.set()

        with mock.patch.object(cloud_backup, "due", lambda: "all" if len(ran) < 1 else "brain"), mock.patch.object(cloud_backup, "sync", fake_sync):
            thread = threading.Thread(target=cloud_backup.auto_loop, args=(lambda: self.brain, stop, 0.01))
            thread.start()
            thread.join(timeout=10)
        self.assertEqual(ran, [True, False])


class CommandTests(CloudCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--root", str(self.project), "cloud", *args])
        return code, out.getvalue(), err.getvalue()

    def test_status_switches_and_the_key(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("not set up on this computer", out)
        code, _, err = self.run_cli("sync")
        self.assertEqual(code, 1)
        self.assertIn("cairn cloud setup", err)
        self.set_up()
        code, out, _ = self.run_cli("auto", "off")
        self.assertIn("by itself once a day while Cairn is open: off", out)
        code, _, err = self.run_cli("repos", "on")
        self.assertEqual(code, 1)
        self.assertIn("Whose repos?", err)
        code, out, _ = self.run_cli("key")
        self.assertIn("CAIRN_BACKUP_KEY", out)
        self.assertNotIn(KEY, out)
        code, out, _ = self.run_cli("key", "--show")
        self.assertEqual(out.strip(), KEY)
        code, out, _ = self.run_cli("off")
        self.assertIn("not set up on this computer", out)


class HelperTests(unittest.TestCase):
    """cairn/cloud_terabox.py away from any browser: what it calls right, and what it says when it cannot work."""

    def test_missing_or_another_size_is_not_right(self):
        local = {"a.7z": 10, "b.7z": 20, "c.7z": 30, "d.7z": 40}
        remote = {"a.7z": 10, "b.7z": 21, "d.7z": -1, "a_20261122_002926.7z": 10}
        self.assertEqual(cloud_terabox.not_right(local, remote), ["b.7z", "c.7z"])

    def test_a_file_the_drive_renamed_with_the_time_is_there(self):
        local = {"HOW-TO-RESTORE.txt": 975, "brain.7z": 500, "CONTENTS.json": 40, "notes": 7}
        remote = {"HOW-TO-RESTORE_20261122_004953.txt": 975, "brain_20261122_004953.7z": 499, "CONTENTS_2026.json": 40, "notes_20261122_004953": 7}
        self.assertEqual(cloud_terabox.renamed_twin("HOW-TO-RESTORE.txt", 975, remote), "HOW-TO-RESTORE_20261122_004953.txt")
        self.assertEqual(cloud_terabox.not_right(local, remote), ["CONTENTS.json", "brain.7z"])  # another size, another pattern

    def test_after_a_run_the_profile_keeps_the_sign_in_and_drops_what_chrome_fetches_again(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        with mock.patch.dict(os.environ, {"CAIRN_HOME": str(home), "CAIRN_TERABOX_PROFILE": ""}):
            profile = cloud_terabox.profile()
            self.assertEqual(profile, home / "cloud-backup" / "chrome")
            for folder in ("Default/Cache/Cache_Data", "Default/Code Cache/js", "component_crx_cache", "Default/Network"):
                (profile / folder).mkdir(parents=True)
                (profile / folder / "file").write_bytes(b"x" * 10)
            cloud_terabox.trim_profile()
            left = sorted(path.relative_to(profile).as_posix() for path in profile.rglob("file"))
        self.assertEqual(left, ["Default/Network/file"])  # where Chrome keeps the sign-in

    def test_it_answers_in_one_line_also_when_it_cannot_start(self):
        env = {**os.environ, "CAIRN_HOME": tempfile.mkdtemp(), "CAIRN_CHROME": "", "PATH": ""}
        for name in ("LOCALAPPDATA",):
            env.pop(name, None)
        script = Path(cloud_terabox.__file__)
        done = subprocess.run([sys.executable, str(script), "nonsense"], capture_output=True, text=True, env=env)
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual((done.returncode, answer["ok"]), (1, False))
        self.assertIn("upload-folder", answer["error"])


class ServerTests(CloudCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.brain, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def ask(self, method, path, body=None):
        request = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", "X-Cairn": "1"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read() or b"{}")

    def test_the_page_is_told_and_the_button_starts_a_run(self):
        status, data = self.ask("GET", "/api/vibe")
        self.assertEqual((status, data["cloud"]["setUp"]), (200, False))
        status, data = self.ask("POST", "/api/cloud/sync", {})
        self.assertEqual(status, 400)
        self.assertIn("cairn cloud setup", json.dumps(data))

        self.set_up()
        gate = threading.Event()
        ran: list = []

        def slow_sync(brain, repos=None, log=print):
            ran.append(repos)
            gate.wait(10)
            cloud_backup._progress.update({"running": False, "step": ""})

        with mock.patch.object(cloud_backup, "sync", slow_sync):
            status, data = self.ask("POST", "/api/cloud/sync", {"repos": True})
            self.assertEqual((status, data["running"]), (200, True))
            status, again = self.ask("POST", "/api/cloud/sync", {})
            self.assertEqual(status, 400)
            self.assertIn("already running", json.dumps(again))
            self.assertTrue(self.ask("GET", "/api/vibe")[1]["cloud"]["running"])
            gate.set()
            for _ in range(100):
                if not self.ask("GET", "/api/vibe")[1]["cloud"]["running"]:
                    break
                time.sleep(0.05)
        self.assertEqual(ran, [True])

    def test_a_phone_gets_neither_the_reading_nor_the_button(self):
        self.set_up()
        self.server.RequestHandlerClass.github_moves = False
        self.assertIsNone(self.ask("GET", "/api/vibe")[1]["cloud"])
        self.assertEqual(self.ask("POST", "/api/cloud/sync", {})[0], 404)
        self.assertEqual(self.drive.calls, [])


@unittest.skipUnless(sync_playwright, "needs playwright")
class PageTests(CloudCase):
    """The Vibe page: off with the one command that sets it up, then one button and what was sent."""

    def setUp(self):
        super().setUp()
        self.server = make_server(self.brain, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = launch_browser(self.pw)
        self.addCleanup(self.browser.close)
        self.errors: list[str] = []

    def open(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 1200})
        page.add_init_script("window.CAIRN_TEST_POLL_MS = 150")
        page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/")
        page.wait_for_selector("#nav button")
        go_to(page, "vibe")
        return page

    def test_off_until_set_up_then_a_button_that_sends_and_says_what_happened(self):
        page = self.open()
        page.wait_for_selector(".vibe-cloud")
        text = page.locator(".vibe-cloud").inner_text()
        self.assertIn("not set up on this computer", text)
        self.assertIn("cairn cloud setup", text)
        self.assertEqual(page.locator("[data-action=vibe-cloud-sync]").count(), 0)

        self.set_up(repos=True, owner="ada")
        cloud_backup._write("sent.json", {"brain": {"at": "2026-11-21T19:06", "bytes": 37_000_000}, "repos": {"alpha": {"files": []}}, "repos_at": "2026-11-21T18:51"})
        page.wait_for_selector("[data-action=vibe-cloud-sync]")
        text = page.locator(".vibe-cloud").inner_text()
        self.assertIn("37 MB", text)
        self.assertIn("Repos of ada: 1 held", text)
        self.assertEqual(page.locator("[data-action=vibe-cloud-sync]").all_inner_texts(), ["Back up now", "With the repos"])

        gate = threading.Event()
        ran: list = []

        def slow_sync(brain, repos=None, log=print):
            ran.append(repos)
            cloud_backup._progress.update({"running": True, "step": "Sending the brain (37 MB)"})
            gate.wait(10)
            record = cloud_backup.sent()
            record["last"] = {"at": "2026-11-22T00:30", "ok": False, "message": "TeraBox asks to sign in."}
            cloud_backup._write("sent.json", record)
            cloud_backup._progress.update({"running": False, "step": ""})

        with mock.patch.object(cloud_backup, "sync", slow_sync):
            page.locator("[data-action=vibe-cloud-sync]", has_text="Back up now").click()
            page.wait_for_selector(".vibe-cloud button[disabled]")
            page.wait_for_selector(".vibe-cloud header:has-text('Sending the brain (37 MB)')")
            gate.set()
            page.wait_for_selector(".vibe-cloud .callout")
        self.assertIn("did not finish: TeraBox asks to sign in.", page.locator(".vibe-cloud .callout").inner_text())
        self.assertEqual(page.locator(".vibe-cloud button[disabled]").count(), 0)
        self.assertEqual(ran, [False])  # the first button is the brain alone; "With the repos" is the other one
        page.click('.vibe-panel[data-panel="cloud"] [data-action=vibe-panel-fold]')
        page.wait_for_selector('.vibe-panel.folded[data-panel="cloud"]')
        self.assertEqual(page.locator('.vibe-panel[data-panel="cloud"] .vp-sum').inner_text(), "the last run did not finish")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

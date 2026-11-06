"""One-click sync, the desktop app's API and its first-run choices. Uses only temp folders."""

import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cairn import desktop
from cairn.brain import Brain, SyncError, clone_repo, repo_folder_name
from cairn.cli import build_parser
from cairn.server import make_server

HAS_GIT = shutil.which("git") is not None


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout.strip()


def note(title, body="hi"):
    return f"---\ntitle: {title}\ntype: note\n---\n{body}\n"


class Repos:
    """A bare 'GitHub' remote and two clones of it (two devices)."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.laptop = self.clone("laptop")
        self.phone = self.clone("phone")

    def clone(self, name):
        folder = self.tmp / name
        subprocess.run(["git", "clone", "-q", str(self.remote), str(folder)], check=True, capture_output=True)
        git(folder, "config", "user.email", f"{name}@example.com")
        git(folder, "config", "user.name", name)
        git(folder, "checkout", "-q", "-B", "main")
        return folder

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


@unittest.skipUnless(HAS_GIT, "git not installed")
class SyncTests(unittest.TestCase):
    def setUp(self):
        self.repos = Repos()
        self.addCleanup(self.repos.cleanup)

    def test_sync_sends_and_receives_between_devices(self):
        laptop = Brain(self.repos.laptop)
        laptop.init()
        laptop.write("notes/first.md", note("First"))
        result = laptop.git_sync()
        self.assertTrue(result["remote"])
        self.assertTrue(result["committed"])
        self.assertEqual(result["pushed"], 1)

        phone = Brain(self.repos.phone)
        result = phone.git_sync()
        self.assertEqual(result["pulled"], 1)
        self.assertTrue((self.repos.phone / "brain" / "notes" / "first.md").exists())

        phone.write("notes/second.md", note("Second"))
        self.assertEqual(phone.git_sync()["pushed"], 1)
        self.assertEqual(laptop.git_sync()["pulled"], 1)
        self.assertTrue((self.repos.laptop / "brain" / "notes" / "second.md").exists())
        status = laptop.git_status()
        self.assertEqual((status["ahead"], status["behind"], status["changes"]), (0, 0, []))

    def test_sync_leaves_work_outside_the_brain_alone(self):
        laptop = Brain(self.repos.laptop)
        laptop.init()
        (self.repos.laptop / "code.py").write_text("x = 1\n", encoding="utf-8")
        git(self.repos.laptop, "add", "code.py")
        git(self.repos.laptop, "commit", "-qm", "code")
        laptop.write("notes/a.md", note("A"))
        laptop.git_sync()

        phone = Brain(self.repos.phone)
        phone.git_sync()
        phone.write("notes/b.md", note("B"))
        phone.git_sync()

        (self.repos.laptop / "code.py").write_text("x = 2  # not ready to commit\n", encoding="utf-8")
        result = laptop.git_sync()
        self.assertEqual(result["pulled"], 1)
        self.assertIn("x = 2", (self.repos.laptop / "code.py").read_text(encoding="utf-8"))
        self.assertIn("code.py", git(self.repos.laptop, "status", "--porcelain"))

    def test_clash_is_reported_and_nothing_is_lost(self):
        laptop = Brain(self.repos.laptop)
        laptop.init()
        laptop.write("notes/shared.md", note("Shared", "original"))
        laptop.git_sync()
        phone = Brain(self.repos.phone)
        phone.git_sync()

        current = laptop.read("notes/shared.md")
        laptop.write("notes/shared.md", note("Shared", "laptop version"), current["mtime"])
        laptop.git_sync()
        current = phone.read("notes/shared.md")
        phone.write("notes/shared.md", note("Shared", "phone version"), current["mtime"])
        with self.assertRaises(SyncError) as caught:
            phone.git_sync()
        self.assertIn("same lines", str(caught.exception))
        self.assertFalse((self.repos.phone / ".git" / "rebase-merge").exists(), "no half-finished rebase left behind")
        self.assertIn("phone version", (self.repos.phone / "brain" / "notes" / "shared.md").read_text(encoding="utf-8"))
        self.assertIn("phone version", git(self.repos.phone, "show", "HEAD:brain/notes/shared.md"))

    def test_without_a_remote_sync_just_commits(self):
        folder = self.repos.tmp / "solo"
        folder.mkdir()
        git(folder, "init", "-q")
        git(folder, "config", "user.email", "s@example.com")
        git(folder, "config", "user.name", "Solo")
        brain = Brain(folder)
        brain.init()
        brain.write("ideas/x.md", note("X"))
        result = brain.git_sync()
        self.assertFalse(result["remote"])
        self.assertTrue(result["committed"])
        self.assertFalse(brain.git_status()["remote"])

    def test_clone_repo(self):
        laptop = Brain(self.repos.laptop)
        laptop.init()
        laptop.write("notes/n.md", note("N"))
        laptop.git_sync()
        target = clone_repo(str(self.repos.remote), self.repos.tmp / "fresh")
        self.assertTrue((target / "brain" / "notes" / "n.md").exists())
        with self.assertRaises(SyncError):
            clone_repo(str(self.repos.remote), target)  # not empty any more


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.first = self.tmp / "first"
        self.second = self.tmp / "second vault"
        for folder in (self.first, self.second):
            folder.mkdir()
        (self.second / ".obsidian").mkdir()
        (self.second / "hello.md").write_text("# Hello\n", encoding="utf-8")
        self.documents = self.tmp / "Documents"
        for name, value in (("documents_dir", self.documents), ("obsidian_vaults", []), ("log", None)):
            patcher = mock.patch.object(desktop, name, return_value=value)  # never touch the real app's files
            patcher.start()
            self.addCleanup(patcher.stop)

        self.config = desktop.Config(self.tmp / "config.json")
        self.config.remember_folder(self.first)  # what the app does at start
        self.hooks = desktop.DesktopHooks(self.config)
        brain = Brain(self.first)
        brain.init()
        self.server = make_server(brain, port=0, desktop=self.hooks)
        self.hooks.server = self.server
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"X-Cairn": "1", "Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def test_info_says_desktop(self):
        status, info = self.call("GET", "/api/info")
        self.assertEqual(status, 200)
        self.assertTrue(info["desktop"])
        self.assertEqual(Path(info["path"]), self.first.resolve())

    def test_open_switches_the_served_brain_and_is_remembered(self):
        status, result = self.call("POST", "/api/desktop", {"action": "open", "path": str(self.second)})
        self.assertEqual(status, 200)
        self.assertTrue(result["reload"])
        _, info = self.call("GET", "/api/info")
        self.assertEqual(info["project"], "second vault")
        self.assertEqual(info["brainDir"], ".", "an Obsidian vault opens as it is")
        _, files = self.call("GET", "/api/files")
        self.assertEqual([f["path"] for f in files["files"]], ["hello.md"])
        self.assertEqual(Path(self.config.get("folder")), self.second.resolve())
        _, state = self.call("GET", "/api/desktop")
        listed = [desktop.folder_key(p["path"]) for p in state["places"]]
        self.assertNotIn(desktop.folder_key(self.second), listed, "the open brain isn't offered again")
        self.assertIn(desktop.folder_key(self.first), listed, "the previous brain is one click away")

    def test_new_brain_lands_in_documents(self):
        status, result = self.call("POST", "/api/desktop", {"action": "new", "name": "Team notes"})
        self.assertEqual(status, 200)
        created = self.documents / "Cairn" / "Team notes"
        self.assertTrue((created / "brain" / "ideas").is_dir())
        _, info = self.call("GET", "/api/info")
        self.assertEqual(info["project"], "Team notes")

    def test_bad_requests_are_explained(self):
        status, result = self.call("POST", "/api/desktop", {"action": "open", "path": str(self.tmp / "missing")})
        self.assertEqual(status, 400)
        self.assertIn("isn't a folder", result["error"])
        status, _ = self.call("POST", "/api/desktop", {"action": "format-disk"})
        self.assertEqual(status, 400)
        status, state = self.call("POST", "/api/desktop", {"action": "auto-sync", "on": False})
        self.assertEqual((status, state["autoSync"]), (200, False))
        self.assertFalse(self.config.get("autoSync"))

    def test_plain_server_has_no_desktop_api(self):
        server = make_server(Brain(self.first), port=0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/api/desktop", body=json.dumps({"action": "open", "path": str(self.second)}),
                         headers={"X-Cairn": "1"})
            self.assertEqual(conn.getresponse().status, 404)
            conn.close()
        finally:
            server.shutdown()
            server.server_close()


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_obsidian_vaults_most_recent_first(self):
        older, newer, gone = self.tmp / "older", self.tmp / "newer", self.tmp / "gone"
        older.mkdir()
        newer.mkdir()
        listing = self.tmp / "obsidian.json"
        listing.write_text(json.dumps({"vaults": {
            "a": {"path": str(older), "ts": 100},
            "b": {"path": str(newer), "ts": 200},
            "c": {"path": str(gone), "ts": 300},
        }}), encoding="utf-8")
        self.assertEqual(desktop.obsidian_vaults(listing), [newer, older])
        self.assertEqual(desktop.obsidian_vaults(self.tmp / "missing.json"), [])

    def test_default_folder_prefers_last_then_vault_then_documents(self):
        config = desktop.Config(self.tmp / "config.json")
        vault = self.tmp / "vault"
        vault.mkdir()
        with mock.patch.object(desktop, "documents_dir", return_value=self.tmp / "Documents"):
            self.assertEqual(desktop.default_folder(config, vaults=[]), self.tmp / "Documents" / "Cairn")
            self.assertEqual(desktop.default_folder(config, vaults=[vault]), vault)
            config.remember_folder(self.tmp / "Documents" / "Cairn")
            self.assertEqual(desktop.default_folder(config, vaults=[vault]), self.tmp / "Documents" / "Cairn")

    def test_names_and_commands(self):
        self.assertEqual(repo_folder_name("https://github.com/you/notes.git"), "notes")
        self.assertEqual(repo_folder_name("git@github.com:you/my-vault.git"), "my-vault")
        self.assertEqual(repo_folder_name("you/brain"), "brain")
        command = desktop.autostart_command(Path("C:/Cairn/app/Cairn.pyw"))
        self.assertTrue(command.endswith('Cairn.pyw" --hidden'))
        self.assertEqual(build_parser().parse_args(["desktop", "--hidden"]).hidden, True)


if __name__ == "__main__":
    unittest.main()

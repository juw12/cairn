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



class StartMenuShortcutTests(unittest.TestCase):
    """The Start-menu shortcut must carry Cairn's AppUserModelID or a pinned taskbar icon goes blank."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.config = desktop.Config(self.temp / "config.json")
        self.link = self.temp / "Cairn.lnk"

    def run_ensure(self):
        with mock.patch.object(desktop.sys, "platform", "win32"), \
             mock.patch.object(desktop, "start_menu_shortcut", return_value=self.link), \
             mock.patch.object(desktop, "stamp_shortcut") as stamp:
            stamp.side_effect = lambda link, *args, **kwargs: link.write_text("lnk")
            desktop.ensure_start_menu_shortcut(self.config)
        return stamp

    def test_writes_shortcut_with_app_id_once(self):
        stamp = self.run_ensure()
        stamp.assert_called_once()
        target, args, icon, workdir = stamp.call_args.args[1:5]
        self.assertTrue(args.endswith('Cairn.pyw"'))
        self.assertEqual(icon.name, "cairn.ico")
        self.assertEqual(self.config.get("shortcutStamp"), f"{desktop.AUMID}|{desktop.APP_ROOT}")
        self.assertFalse(self.run_ensure().called, "second start must not rewrite the shortcut")

    def test_rewrites_shortcut_when_it_goes_missing(self):
        self.run_ensure()
        self.link.unlink()
        self.assertTrue(self.run_ensure().called)

    def test_failure_is_logged_not_raised(self):
        with mock.patch.object(desktop.sys, "platform", "win32"), \
             mock.patch.object(desktop, "start_menu_shortcut", return_value=self.link), \
             mock.patch.object(desktop, "stamp_shortcut", side_effect=OSError("no powershell")), \
             mock.patch.object(desktop, "log") as log:
            desktop.ensure_start_menu_shortcut(self.config)
        self.assertIsNone(self.config.get("shortcutStamp"))
        self.assertIn("could not tag", log.call_args.args[0])


def desktop_head(folder):
    return git(folder, "rev-parse", "--short", "HEAD")


@unittest.skipUnless(HAS_GIT, "git not installed")
class AutoUpdateTests(unittest.TestCase):
    """Updates install themselves: web-only changes reload the page, Python changes restart quietly."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin = self.tmp / "origin.git"
        self.dev = self.tmp / "dev"
        self.app = self.tmp / "app"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        subprocess.run(["git", "init", "-q", str(self.dev)], check=True)
        for key, value in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
            git(self.dev, "config", key, value)
        self.commit({"docs/app.js": "v1", "cairn/server.py": "v1", "Cairn.pyw": "v1"}, "first")
        git(self.dev, "branch", "-M", "main")
        git(self.dev, "remote", "add", "origin", str(self.origin))
        git(self.dev, "push", "-q", "-u", "origin", "main")
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")  # whatever init.defaultBranch says
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.origin), str(self.app)], check=True)
        self.assertTrue(desktop_head(self.app), "the app checkout has a commit")
        for patcher in (mock.patch.object(desktop, "APP_ROOT", self.app), mock.patch.object(desktop, "log")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = desktop.Config(self.tmp / "config.json")
        self.hooks = desktop.DesktopHooks(self.config)

    def commit(self, files, message):
        for name, text in files.items():
            path = self.dev / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(self.dev, "add", "-A")
        git(self.dev, "commit", "-q", "-m", message)

    def publish(self, files, message):
        self.commit(files, message)
        git(self.dev, "push", "-q", "origin", "main")

    def test_which_changes_need_a_restart(self):
        self.assertFalse(desktop.needs_restart(["docs/app.js", "docs/styles.css", "README.md", "tests/test_x.py"]))
        self.assertTrue(desktop.needs_restart(["docs/app.js", "cairn/desktop.py"]))
        self.assertTrue(desktop.needs_restart(["Cairn.pyw"]))
        self.assertTrue(desktop.needs_restart(["pyproject.toml"]))

    def test_web_only_update_reloads_the_page_without_restarting(self):
        self.assertEqual(self.hooks.pending_update(), "")
        self.publish({"docs/app.js": "v2", "README.md": "new"}, "web change")
        restart = mock.Mock()
        self.hooks.request_restart = restart
        result = self.hooks.update()
        self.assertEqual(result, {"updated": True, "reload": True})
        self.assertEqual(self.hooks.page_head, desktop.app_head())
        self.assertNotEqual(self.hooks.loaded_head, self.hooks.page_head, "the Python code didn't change")
        self.assertEqual(self.hooks.pending_update(), "", "nothing left to do")
        self.assertFalse(self.hooks.update_waiting())
        restart.assert_not_called()

    def test_python_update_restarts(self):
        self.publish({"docs/app.js": "v2"}, "web change")
        self.hooks.update()
        self.publish({"cairn/server.py": "v2"}, "server change")
        restarted = threading.Event()
        self.hooks.request_restart = restarted.set
        result = self.hooks.update()
        self.assertEqual(result, {"updated": True, "restarting": True})
        self.assertTrue(restarted.wait(5))
        self.assertTrue(self.hooks.update_waiting())

    def test_page_can_only_ask_for_a_restart_when_one_is_due(self):
        restart = mock.Mock()
        self.hooks.request_restart = restart
        self.assertEqual(self.hooks.act("restart", {}), {"restarting": False})
        self.publish({"cairn/server.py": "v2"}, "server change")
        git(self.app, "pull", "-q", "--ff-only")
        with mock.patch.object(desktop.threading, "Timer") as timer:
            self.assertEqual(self.hooks.act("restart", {}), {"restarting": True})
        timer.assert_called_once_with(0.3, restart)

    def test_auto_update_setting(self):
        self.assertTrue(self.hooks.state()["autoUpdate"])
        self.assertEqual(self.hooks.act("auto-update", {"on": False}), {"autoUpdate": False})
        self.assertFalse(self.hooks.state()["autoUpdate"])

    def make_app(self, visible):
        app = desktop.DesktopApp.__new__(desktop.DesktopApp)
        app.hooks = self.hooks
        app.visible = visible
        app.window = mock.Mock()
        app.restart = mock.Mock()
        return app

    def test_background_loop_reloads_the_page_for_web_updates(self):
        self.publish({"docs/app.js": "v2"}, "web change")
        app = self.make_app(visible=True)
        self.assertFalse(app.apply_update())
        script = app.window.evaluate_js.call_args.args[0]
        self.assertIn("applyUpdate({restart: false})", script)
        self.assertIn("location.reload()", script, "pages from before auto-update still pick it up")
        app.restart.assert_not_called()

    def test_background_loop_restarts_at_once_when_in_the_tray(self):
        self.publish({"cairn/server.py": "v2"}, "server change")
        app = self.make_app(visible=False)
        self.assertTrue(app.apply_update())
        app.restart.assert_called_once_with()
        app.window.evaluate_js.assert_not_called()

    def test_background_loop_waits_for_an_idle_moment_when_the_window_is_open(self):
        self.publish({"cairn/server.py": "v2"}, "server change")
        app = self.make_app(visible=True)
        self.assertFalse(app.apply_update())
        app.restart.assert_not_called()
        script = app.window.evaluate_js.call_args.args[0]
        self.assertIn("applyUpdate({restart: true})", script)
        self.assertNotIn("location.reload()", script)

    def test_restart_remembers_whether_the_window_was_open(self):
        app = desktop.DesktopApp.__new__(desktop.DesktopApp)
        app.visible = True
        app.quit = mock.Mock()
        app.restart()
        self.assertTrue(app.restart_requested)
        self.assertTrue(app.restart_visible)
        app.visible = False
        app.restart()
        self.assertFalse(app.restart_visible)


if __name__ == "__main__":
    unittest.main()

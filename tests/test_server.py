import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from cairn.brain import Brain
from cairn.server import make_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "app.py").write_text("x = 1  # TODO: one\n# FIXME two\n", encoding="utf-8")
        (self.tmp / "node_modules").mkdir()
        (self.tmp / "node_modules" / "skip.js").write_text("// TODO ignored\n", encoding="utf-8")
        self.brain = Brain(self.tmp)
        self.brain.init()
        self.server = make_server(self.brain, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        base = {"X-Cairn": "1", "Content-Type": "application/json"}
        base.update(headers or {})
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=base)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, raw

    def test_binds_localhost_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        with self.assertRaises(ValueError):
            make_server(self.brain, port=0, host="0.0.0.0")

    def test_a_repo_move_goes_to_github_and_its_note_comes_back(self):
        from cairn import github_move

        def moved(brain, repo, source, target):
            path, text = github_move.move_note(repo, source, target, "2026-11-19T21:40")
            brain.write(path, text)
            return {"repo": repo, "from": source, "to": target, "at": "2026-11-19T21:40", "note": path, "message": "GitHub mailed grace a link."}

        _, info = self.call("GET", "/api/info")
        self.assertIs(info["githubMove"], True)
        _, before = self.call("GET", "/api/changes?since=0", headers={"X-Cairn-Client": "another-page"})
        with mock.patch.object(github_move, "move", side_effect=moved) as move:
            status, result = self.call("POST", "/api/github/move", {"repo": "vault", "from": "ada", "to": "grace"})
        self.assertEqual(status, 200)
        move.assert_called_once_with(self.brain, "vault", "ada", "grace")
        self.assertEqual((result["message"], result["file"]["path"]), ("GitHub mailed grace a link.", "github-usage/moves/ada-vault.md"))
        self.assertIn("gh_move_to: grace", result["file"]["text"])
        _, changes = self.call("GET", f"/api/changes?since={before['revision']}&epoch={before['epoch']}", headers={"X-Cairn-Client": "another-page"})
        self.assertEqual(changes["paths"], ["github-usage/moves/ada-vault.md"], "other open pages hear of the note")

    def test_a_refused_repo_move_says_why(self):
        from cairn import github_move

        with mock.patch.object(github_move, "move", side_effect=github_move.MoveError("vault is public.")):
            status, result = self.call("POST", "/api/github/move", {"repo": "vault", "from": "ada", "to": "grace"})
        self.assertEqual((status, result["error"]), (400, "vault is public."))
        status, _ = self.call("POST", "/api/github/move", {"repo": "vault", "from": "ada", "to": "grace"}, headers={"X-Cairn": ""})
        self.assertEqual(status, 403)

    def test_who_pushes_rides_on_the_vibe_poll_and_a_switch_answers_with_the_new_state(self):
        from cairn import github_push

        before = {"accounts": ["ada", "grace"], "active": "ada", "pusher": "ada", "sure": True, "viaCli": True}
        after = {**before, "active": "grace", "pusher": "grace", "message": "Pushes from this computer now go to GitHub as grace."}
        with mock.patch.object(github_push, "status_soon", return_value=before), mock.patch.object(github_push, "switch", return_value=after) as switch:
            _, vibe = self.call("GET", "/api/vibe")
            status, result = self.call("POST", "/api/github/push", {"account": "grace"})
        self.assertEqual(vibe["push"], before)
        switch.assert_called_once_with("grace")
        self.assertEqual((status, result), (200, after))

    def test_the_speed_is_measured_when_asked_and_a_dead_line_is_said(self):
        from cairn import machine

        speed = {"down_mbps": 12.0, "mb": 6.0, "seconds": 4.0, "at": "2026-11-22T10:42", "text": "measured 12 Mbit/s down at 10:42"}
        with mock.patch.object(machine, "measure_speed", return_value=speed) as measure:
            status, result = self.call("POST", "/api/vibe/speed", {})
        measure.assert_called_once_with()
        self.assertEqual((status, result), (200, speed))
        with mock.patch.object(machine, "measure_speed", side_effect=OSError("no route to host")):
            status, result = self.call("POST", "/api/vibe/speed", {})
        self.assertEqual(status, 502)
        self.assertIn("the internet did not answer", result["error"])
        with mock.patch.object(machine, "measure_speed") as measure:
            status, _ = self.call("POST", "/api/vibe/speed", {}, headers={"X-Cairn": ""})
        self.assertEqual(status, 403)
        measure.assert_not_called()

    def test_a_refused_switch_says_why(self):
        from cairn import github_push

        with mock.patch.object(github_push, "switch", side_effect=github_push.PushError("The GitHub CLI is not signed in as mallory on this computer.")):
            status, result = self.call("POST", "/api/github/push", {"account": "mallory"})
        self.assertEqual((status, result["error"]), (400, "The GitHub CLI is not signed in as mallory on this computer."))
        with mock.patch.object(github_push, "switch") as switch:
            status, _ = self.call("POST", "/api/github/push", {"account": "grace"}, headers={"X-Cairn": ""})
        self.assertEqual(status, 403)
        switch.assert_not_called()

    def test_requires_header_and_host(self):
        status, _ = self.call("GET", "/api/files", headers={"X-Cairn": ""})
        self.assertEqual(status, 403)
        status, _ = self.call("GET", "/api/files", headers={"Host": "evil.example:80"})
        self.assertEqual(status, 403)

    def test_crud_and_conflict(self):
        status, created = self.call("PUT", "/api/files?path=ideas/x.md", {"text": "---\ntitle: X\n---\nhi\n", "mtime": None})
        self.assertEqual(status, 200)
        status, _ = self.call("PUT", "/api/files?path=ideas/x.md", {"text": "again", "mtime": None})
        self.assertEqual(status, 409, "create must not overwrite")
        status, conflict = self.call("PUT", "/api/files?path=ideas/x.md", {"text": "stale", "mtime": created["mtime"] - 5000})
        self.assertEqual(status, 409)
        self.assertIn("hi", conflict["text"])
        status, _ = self.call("PUT", "/api/files?path=ideas/x.md", {"text": "fresh\n", "mtime": created["mtime"]})
        self.assertEqual(status, 200)
        _, listing = self.call("GET", "/api/files")
        self.assertIn("ideas/x.md", [f["path"] for f in listing["files"]])
        self.assertEqual((self.brain.dir / "ideas" / "x.md").read_text(encoding="utf-8"), "fresh\n")
        status, _ = self.call("DELETE", "/api/files?path=ideas/x.md")
        self.assertEqual(status, 200)
        self.assertFalse((self.brain.dir / "ideas" / "x.md").exists())

    def test_path_traversal_refused(self):
        for bad in ("../evil.md", "..%2Fevil.md", "/etc/passwd.md", "C:/x.md", "ideas/x.txt", "ideas/../../evil.md"):
            status, _ = self.call("PUT", f"/api/files?path={bad}", {"text": "x", "mtime": None})
            self.assertEqual(status, 400, bad)
        self.assertFalse((self.tmp / "evil.md").exists())

    def test_code_inbox_skips_vendor_dirs(self):
        _, data = self.call("GET", "/api/todos")
        self.assertEqual([(i["path"], i["line"]) for i in data["items"]], [("src/app.py", 1), ("src/app.py", 2)])

    def test_serves_app_with_server_marker(self):
        status, body = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'name="cairn-server"', body)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_git_commit_touches_only_brain(self):
        def git(*args):
            return subprocess.run(["git", "-C", str(self.tmp), *args], capture_output=True, text=True, check=True)

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "Test")
        git("add", "src")
        git("commit", "-qm", "init")
        (self.tmp / "src" / "app.py").write_text("changed\n", encoding="utf-8")
        self.call("PUT", "/api/files?path=tasks/t.md", {"text": "task\n", "mtime": None})
        _, status = self.call("GET", "/api/git")
        self.assertTrue(status["available"])
        self.assertIn("tasks/t.md", [c["path"] for c in status["changes"]])
        code, result = self.call("POST", "/api/git/commit", {"message": "brain: test"})
        self.assertEqual(code, 200, result)
        files = git("show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertTrue(files and all(f.startswith("brain/") for f in files), files)
        self.assertIn("src/app.py", git("status", "--porcelain").stdout)

    def test_info_still_answers_when_the_git_check_stalls(self):
        # A git that hangs past its time limit used to raise out of /api/info: the request died
        # and the web app fell back to its welcome page. It now counts as "no git" for that call.
        stalled = subprocess.TimeoutExpired(cmd=["git"], timeout=20)
        with mock.patch("cairn.brain.shutil.which", return_value="git"), \
                mock.patch("cairn.brain.subprocess.run", side_effect=stalled) as git_run:
            status, info = self.call("GET", "/api/info")
            self.assertFalse(self.brain.git_available())
        self.assertTrue(git_run.called, "the stalled git call was never reached")
        self.assertEqual(status, 200)
        self.assertIs(info["git"], False)
        self.assertEqual(info["mode"], "server")


class VaultFolderTests(unittest.TestCase):
    """An Obsidian vault opens as it is: no brain/ folder, CRLF kept, .obsidian ignored, nothing added."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / ".obsidian").mkdir()
        (self.tmp / ".obsidian" / "workspace.md").write_text("not a note", encoding="utf-8")
        (self.tmp / "Projects").mkdir()
        self.crlf = "---\r\nstatus: active\r\n---\r\nSee [[Home]]\r\n"
        (self.tmp / "Projects" / "Alpha.md").write_bytes(self.crlf.encode("utf-8"))
        (self.tmp / "Home.md").write_bytes(b"# Home\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_vault_root_is_the_brain(self):
        brain = Brain(self.tmp)
        self.assertEqual(brain.dir, self.tmp.resolve())
        paths = [f["path"] for f in brain.files()]
        self.assertEqual(paths, ["Home.md", "Projects/Alpha.md"])
        self.assertEqual(brain.read("Projects/Alpha.md")["text"], self.crlf)
        brain.init()
        self.assertFalse((self.tmp / "README.md").exists())

    def test_writes_keep_line_endings_byte_for_byte(self):
        brain = Brain(self.tmp)
        current = brain.read("Projects/Alpha.md")
        updated = self.crlf.replace("active", "done")
        brain.write("Projects/Alpha.md", updated, current["mtime"])
        self.assertEqual((self.tmp / "Projects" / "Alpha.md").read_bytes(), updated.encode("utf-8"))


class BrainCacheTests(unittest.TestCase):
    """files() caches text by mtime + size; edits made outside Cairn must still show up."""

    def test_external_edits_deletes_and_new_files_are_seen(self):
        import os
        import tempfile

        from cairn.brain import Brain

        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(tmp)
            brain.init()
            note = brain.dir / "ideas" / "alpha.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_bytes(b"---\ntitle: Alpha\ntype: idea\n---\nfirst\n")
            texts = {f["path"]: f["text"] for f in brain.files()}
            self.assertIn("first", texts["ideas/alpha.md"])
            # Same size, different bytes, newer mtime: an editor saving over the file.
            note.write_bytes(b"---\ntitle: Alpha\ntype: idea\n---\nsecnd\n")
            stat = note.stat()
            os.utime(note, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            texts = {f["path"]: f["text"] for f in brain.files()}
            self.assertIn("secnd", texts["ideas/alpha.md"])
            (brain.dir / "ideas" / "beta.md").write_bytes(b"---\ntitle: Beta\ntype: idea\n---\n")
            note.unlink()
            paths = [f["path"] for f in brain.files()]
            self.assertIn("ideas/beta.md", paths)
            self.assertNotIn("ideas/alpha.md", paths)

    def test_unchanged_files_are_not_read_again(self):
        import tempfile
        from unittest import mock

        from cairn import brain as brain_module
        from cairn.brain import Brain

        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(tmp)
            brain.init()
            for index in range(5):
                brain.create("idea", f"Idea {index}", [], "", "2026-11-09")
            first = brain.files()
            with mock.patch.object(brain_module, "_read_text", wraps=brain_module._read_text) as reads:
                second = brain.files()
            self.assertEqual(reads.call_count, 0)
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

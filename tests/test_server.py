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

import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
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


if __name__ == "__main__":
    unittest.main()

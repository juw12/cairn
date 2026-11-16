"""Vibe sessions and feedback: the CLI an assistant drives, the files it writes, and the live API."""

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
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import http.client

from cairn import core, vibe
from cairn.brain import Brain
from cairn.cli import main
from cairn.server import make_server

HAS_GIT = shutil.which("git") is not None


def run_cli(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--root", str(root), *args])
    return code, out.getvalue(), err.getvalue()


class VibeCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.brain = Brain(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def session(self) -> dict:
        notes = [n for n in self.brain.notes() if n["type"] == "session"]
        self.assertEqual(len(notes), 1)
        return notes[0]

    def test_full_session_lifecycle(self):
        code, out, _ = run_cli(self.root, "vibe", "start", "Add dark mode", "--agent", "claude", "--task", "ship-v1")
        self.assertEqual(code, 0, out)
        note = self.session()
        self.assertTrue(note["path"].startswith("sessions/") and note["path"].endswith("-add-dark-mode.md"))
        self.assertEqual((note["title"], note["status"]), ("Add dark mode", "live"))
        self.assertEqual((note["meta"]["agent"], note["meta"]["task"]), ("claude", "[[ship-v1]]"))

        self.assertEqual(run_cli(self.root, "vibe", "log", "asked", "for", "a", "toggle", "--kind", "prompt")[0], 0)
        passed, _, _ = run_cli(self.root, "vibe", "check", "--", sys.executable, "-c", "print('3 passed')")
        failed, _, _ = run_cli(self.root, "vibe", "check", "--", sys.executable, "-c", "import sys; print('1 failed'); sys.exit(3)")
        self.assertEqual((passed, failed), (0, 3))
        self.assertEqual(run_cli(self.root, "vibe", "end", "toggle works", "--result", "partial")[0], 0)

        note = self.session()
        log = core.session_log(note["body"])
        self.assertEqual([e["kind"] for e in log], ["start", "prompt", "pass", "fail", "end"])
        self.assertEqual(log[1]["text"], "asked for a toggle")
        self.assertIn("3 passed", log[2]["text"])
        self.assertIn("exit 3", log[3]["text"])
        self.assertEqual(log[4]["text"], "partial: toggle works")
        self.assertEqual(note["status"], "partial")
        self.assertIsNotNone(core.stamp_minutes(note["meta"]["ended"]))

        stats = core.vibe_stats(self.brain.notes(), note["meta"]["created"])
        self.assertEqual((stats["sessions"], stats["partial"], stats["passed"], stats["failed"], stats["prompts"]), (1, 1, 1, 1, 1))

    def test_parallel_sessions_skills_and_session_targeting(self):
        run_cli(self.root, "vibe", "start", "Map tiles")
        code, out, _ = run_cli(self.root, "vibe", "start", "Share link")
        self.assertEqual(code, 0)
        self.assertIn("CAIRN_SESSION", out)
        live = vibe.live_sessions(self.brain)
        self.assertEqual([n["title"] for n in live], ["Map tiles", "Share link"])
        older = live[0]["slug"]
        # Default: the newest; --session or $CAIRN_SESSION picks another.
        run_cli(self.root, "vibe", "log", "newest gets this")
        run_cli(self.root, "vibe", "skill", "/refine 6.5 -> 8.7", "--session", older)
        os.environ["CAIRN_SESSION"] = older
        try:
            run_cli(self.root, "vibe", "log", "/council verdict ship")
            run_cli(self.root, "vibe", "end", "--result", "dropped")
        finally:
            del os.environ["CAIRN_SESSION"]
        code, _, err = run_cli(self.root, "vibe", "log", "x", "--session", "nope")
        self.assertEqual(code, 2)
        self.assertIn("No live session called", err)
        by_title = {n["title"]: n for n in self.brain.notes() if n["type"] == "session"}
        older_log = core.session_log(by_title["Map tiles"]["body"])
        self.assertEqual([e["kind"] for e in older_log], ["start", "skill", "skill", "end"])
        self.assertEqual(by_title["Map tiles"]["status"], "dropped")
        self.assertEqual(by_title["Share link"]["status"], "live")
        self.assertIn("newest gets this", by_title["Share link"]["body"])
        stats = core.vibe_stats(self.brain.notes(), by_title["Map tiles"]["meta"]["created"])
        self.assertEqual(stats["skills"], {"/refine": 1, "/council": 1})
        self.assertIn("/refine x1", run_cli(self.root, "vibe", "status")[1])

    def test_log_without_a_live_session_explains(self):
        code, _, err = run_cli(self.root, "vibe", "log", "hello")
        self.assertEqual(code, 2)
        self.assertIn("No live session", err)

    def test_feedback_links_to_latest_session_and_validates_rating(self):
        run_cli(self.root, "vibe", "start", "Share link")
        code, _, _ = run_cli(self.root, "feedback", "The link opened first time", "--rating", "5", "--from", "@ben")
        self.assertEqual(code, 0)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_cli(self.root, "feedback", "bad", "--rating", "9")
        feedback = [n for n in self.brain.notes() if n["type"] == "feedback"]
        self.assertEqual(len(feedback), 1)
        meta = feedback[0]["meta"]
        self.assertEqual((feedback[0]["status"], meta["rating"], meta["from"]), ("new", "5", "@ben"))
        self.assertEqual(meta["about"], f"[[{self.session()['slug']}]]")
        self.assertEqual(core.feedback_rating(feedback[0]), 5)

    def test_status_prints_totals(self):
        code, out, _ = run_cli(self.root, "vibe", "status")
        self.assertEqual(code, 0)
        self.assertIn("Live now: nothing", out)
        self.assertIn("Sessions: 0", out)


@unittest.skipUnless(HAS_GIT, "git not installed")
class VibeGitAndServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git = lambda *a: subprocess.run(["git", "-C", str(self.root), *a], capture_output=True, check=True)  # noqa: E731
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (self.root / "app.py").write_text("a = 1\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-qm", "init")
        self.git = git
        self.brain = Brain(self.root)
        self.brain.init()
        vibe._git_cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_end_records_commits_and_churn_since_start(self):
        session = vibe.start(self.brain, "Two lines")
        meta = core.parse_note(session["text"])["meta"]
        started = meta["started"]
        self.assertRegex(meta["base"], r"^[0-9a-f]{40}$")
        # Commits during the session: back-date nothing, the session started this minute.
        (self.root / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        self.git("commit", "-qam", "two more lines", "--date", started.replace("T", " ") + ":30")
        activity = vibe.git_activity(self.brain, started, meta["base"])
        self.assertTrue(activity["available"])
        # Only the commit made during the session, not "init" from earlier in the same minute.
        self.assertEqual([c["subject"] for c in activity["commits"]], ["two more lines"])
        self.assertEqual(activity["commits"][0]["added"], 2)
        ended = vibe.end(self.brain, "shipped")
        meta = core.parse_note(ended["text"])["meta"]
        self.assertEqual((meta["status"], meta["commits"], meta["added"]), ("shipped", "1", "2"))

    def test_api_vibe_returns_session_files_and_git(self):
        vibe.start(self.brain, "Live API")
        vibe.add_feedback(self.brain, "Nice", rating=4)
        (self.brain.dir / "notes").mkdir(exist_ok=True)
        (self.brain.dir / "notes" / "other.md").write_text("not polled\n", encoding="utf-8")
        server = make_server(self.brain, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            for _ in range(60):  # git is read in the background; a slow PC answers null first
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.request("GET", "/api/vibe?since=&base=", headers={"X-Cairn": "1"})
                data = json.loads(conn.getresponse().read())
                conn.close()
                if data["git"] is not None:
                    break
        finally:
            server.shutdown()
            server.server_close()
        paths = sorted(f["path"] for f in data["files"])
        self.assertEqual(len(paths), 2)
        self.assertTrue(paths[0].startswith("feedback/") and paths[1].startswith("sessions/"))
        self.assertTrue(data["git"]["available"])
        self.assertEqual(data["git"]["commits"], [])
        # This computer's load rides along: a reading on Windows, null where it cannot be read.
        self.assertIn("machine", data)
        if data["machine"] is not None:
            self.assertIn(data["machine"]["level"], ("light", "medium", "heavy"))


class GitActivitySoonTests(unittest.TestCase):
    """The live page must not wait on git: no repository needed, `git_activity` is replaced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Brain(Path(self.tmp.name))
        self.real = vibe.git_activity
        self.first_wait = vibe.GIT_FIRST_WAIT_S
        vibe._git_cache.clear()

    def tearDown(self):
        vibe.git_activity = self.real
        vibe.GIT_FIRST_WAIT_S = self.first_wait
        vibe._git_cache.clear()
        self.tmp.cleanup()

    def _settle(self):
        for _ in range(200):
            if all(entry["thread"] is None for entry in vibe._git_cache.values()):
                return
            time.sleep(0.01)
        self.fail("background git read did not finish")

    def test_slow_git_does_not_block_and_the_reading_arrives_later(self):
        release = threading.Event()
        calls = []

        def slow(brain, since="", base=""):
            calls.append((since, base))
            release.wait(5)
            return {"available": True, "commits": [], "head": "abc"}

        vibe.git_activity = slow
        vibe.GIT_FIRST_WAIT_S = 0.05
        started = time.time()
        self.assertIsNone(vibe.git_activity_soon(self.brain, "2026-01-01T10:00", ""))
        self.assertIsNone(vibe.git_activity_soon(self.brain, "2026-01-01T10:00", ""))
        self.assertLess(time.time() - started, 2)
        self.assertEqual(len(calls), 1)  # one run at a time, however often the page asks
        release.set()
        self._settle()
        self.assertEqual(vibe.git_activity_soon(self.brain, "2026-01-01T10:00", "")["head"], "abc")
        self.assertEqual(len(calls), 1)  # fresh: served from the last reading

    def test_a_failed_read_keeps_the_last_reading(self):
        vibe.git_activity = lambda brain, since="", base="": {"available": True, "head": "good"}
        self.assertEqual(vibe.git_activity_soon(self.brain)["head"], "good")
        self._settle()

        def failing(brain, since="", base=""):
            raise subprocess.TimeoutExpired("git", 20)

        vibe.git_activity = failing
        for entry in vibe._git_cache.values():
            entry["at"] = 0.0  # stale: the next call starts a refresh
        self.assertEqual(vibe.git_activity_soon(self.brain)["head"], "good")
        self._settle()
        self.assertEqual(vibe.git_activity_soon(self.brain)["head"], "good")


if __name__ == "__main__":
    unittest.main()

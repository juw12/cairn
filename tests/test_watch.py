"""`cairn vibe watch`: OpenCode sessions read from its database, Vector detected from outside."""

import io
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cairn import core, watch
from cairn.brain import Brain
from cairn.cli import main

HAS_GIT = shutil.which("git") is not None
MINUTE = 60_000

FULL_COLUMNS = (
    "id text primary key, project_id text, parent_id text, slug text, directory text, title text, "
    "version text, summary_additions integer, summary_deletions integer, summary_files integer, "
    "time_created integer, time_updated integer, time_archived integer, model text, cost real"
)
OLD_COLUMNS = "id text primary key, parent_id text, directory text, title text, time_created integer, time_updated integer"


def make_db(path: Path, rows: list[dict], columns: str = FULL_COLUMNS, messages: dict | None = None) -> Path:
    """`messages` maps a session id to message times (epoch ms)."""
    connection = sqlite3.connect(path)
    connection.execute(f"create table session ({columns})")
    connection.execute("create table message (id text, session_id text, time_created integer, data text)")
    names = [part.split()[0] for part in columns.split(", ")]
    for row in rows:
        keys = [k for k in row if k in names]
        connection.execute(
            f"insert into session ({', '.join(keys)}) values ({', '.join('?' for _ in keys)})",
            [row[k] for k in keys],
        )
        for number, stamp in enumerate((messages or {}).get(row["id"], [])):
            connection.execute(
                "insert into message values (?, ?, ?, ?)", (f"{row['id']}-{number}", row["id"], stamp, "SECRET PROMPT TEXT"),
            )
    connection.commit()
    connection.close()
    return path


def sessions(brain: Brain) -> list[dict]:
    return sorted((n for n in brain.notes() if n["type"] == "session"), key=lambda n: n["path"])


class OpenCodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.brain = Brain(self.root)
        self.brain.init()
        self.now = int(time.time() * 1000)
        self.db = Path(self.tmp.name) / "opencode.db"

    def tearDown(self):
        self.tmp.cleanup()

    def row(self, ident, title, started_min_ago, updated_min_ago, **extra):
        row = {
            "id": ident, "title": title, "directory": str(self.root),
            "time_created": self.now - started_min_ago * MINUTE, "time_updated": self.now - updated_min_ago * MINUTE,
            "summary_additions": 0, "summary_deletions": 0, "summary_files": 0, "cost": 0.0,
            "model": '{"providerID": "anthropic", "modelID": "claude-sonnet"}',
        }
        row.update(extra)
        return row

    def sync(self, everywhere=False):
        return watch.watch_once(
            self.brain, vector=False, everywhere=everywhere, now_ms=self.now, db_path=self.db,
        )

    def ago(self, *minutes):
        return [self.now - m * MINUTE for m in minutes]

    def test_live_and_finished_sessions_become_notes(self):
        make_db(self.db, [
            self.row("ses_a", "Add dark mode", 90, 5, summary_additions=40, summary_deletions=3, summary_files=2, cost=0.4),
            self.row("ses_b", "Explain the build", 300, 200),
            self.row("ses_c", "Write tests", 400, 250, summary_additions=12, summary_files=1),
            # OpenCode bumped time_updated a minute ago, but nobody has talked to it for two hours.
            self.row("ses_d", "Housekeeping bump", 180, 1),
        ], messages={
            "ses_a": self.ago(90, 60, 5),
            "ses_c": self.ago(400, 395, 390, 300, 250),
            "ses_d": self.ago(180, 170, 120),
        })
        self.sync()
        notes = {n["meta"]["external"]: n for n in sessions(self.brain)}
        self.assertEqual(set(notes), {"ses_a", "ses_b", "ses_c", "ses_d"})
        live = notes["ses_a"]
        self.assertEqual(live["status"], "live")
        self.assertEqual(live["meta"]["agent"], "opencode")
        self.assertEqual(live["meta"]["model"], "claude-sonnet")
        self.assertEqual((live["meta"]["added"], live["meta"]["removed"], live["meta"]["files"]), ("40", "3", "2"))
        self.assertNotIn("ended", live["meta"])
        self.assertEqual(notes["ses_b"]["status"], "done")  # finished, no code changed
        self.assertEqual(notes["ses_d"]["status"], "done")  # not live: the last message was 2 h ago
        shipped = notes["ses_c"]
        self.assertEqual(shipped["status"], "shipped")
        self.assertIn("ended", shipped["meta"])
        # gaps 5 + 5 + 90 + 50 minutes, each capped at 10: 5 + 5 + 10 + 10
        self.assertEqual(shipped["meta"]["minutes"], "30")
        self.assertEqual(core.session_minutes(shipped), 30)
        log = core.session_log(self.brain.read(shipped["path"])["text"])
        self.assertEqual([e["kind"] for e in log], ["start", "note", "note", "end"])
        for note in notes.values():
            self.assertNotIn("SECRET", self.brain.read(note["path"])["text"])

    def test_active_minutes(self):
        self.assertEqual(watch.active_minutes([0]), 1)
        self.assertEqual(watch.active_minutes([0, 3 * MINUTE, 6 * MINUTE]), 6)
        self.assertEqual(watch.active_minutes([0, 2 * 60 * MINUTE]), 10)

    def test_active_spans_match_active_minutes(self):
        stamps = [0, 3 * MINUTE, 6 * MINUTE, 120 * MINUTE, 125 * MINUTE]
        spans = watch.active_spans(stamps)
        self.assertEqual(spans, [(0, 16), (120 * MINUTE, 5)])
        self.assertEqual(sum(minutes for _, minutes in spans), watch.active_minutes(stamps))
        self.assertEqual(watch.active_spans([]), [])
        self.assertEqual(watch.active_spans([7]), [(7, 1)])

    def test_parallel_sessions_count_clock_time_once(self):
        # Two sessions working over the same 20 minutes, a third alone later.
        make_db(self.db, [
            self.row("ses_a", "One", 300, 280),
            self.row("ses_b", "Two", 300, 280),
            self.row("ses_c", "Three", 100, 90),
        ], messages={
            "ses_a": self.ago(300, 290, 280),
            "ses_b": self.ago(300, 290, 280),
            "ses_c": self.ago(100, 90),
        })
        self.sync()
        stats = core.vibe_stats(self.brain.notes(), time.strftime("%Y-%m-%d"))
        self.assertEqual(stats["agent_minutes"], 20 + 20 + 10)
        self.assertEqual(stats["minutes"], 20 + 10)
        self.assertEqual(stats["parallel"], round(50 / 30, 1))
        # Gate: never more time vibing than the clock allows.
        self.assertLessEqual(stats["minutes"], len(stats["daily"]) * 24 * 60)
        self.assertEqual(sum(d["minutes"] for d in stats["daily"]), stats["minutes"])

    def test_finished_sessions_are_not_reprocessed(self):
        make_db(self.db, [
            self.row("ses_old", "Finished", 300, 200),
            self.row("ses_live", "Working", 30, 1),
        ], messages={"ses_old": self.ago(300, 200), "ses_live": self.ago(30, 1)})
        found = watch.read_opencode(self.db, 0, str(self.root))
        self.assertEqual({s["id"] for s in watch.needs_refresh(self.brain, found, self.now)}, {"ses_old", "ses_live"})
        self.sync()
        self.assertEqual([s["id"] for s in watch.needs_refresh(self.brain, found, self.now)], ["ses_live"])

    def test_folder_without_git_is_done_and_says_why(self):
        make_db(self.db, [self.row("ses_a", "Chat", 300, 200)], messages={"ses_a": self.ago(300, 200)})
        watch.watch_once(self.brain, vector=False, now_ms=self.now, db_path=self.db, git="definitely-not-git")
        [note] = sessions(self.brain)
        self.assertEqual(note["status"], "done")
        self.assertNotIn("commits", note["meta"])
        self.assertEqual(note["meta"]["git"], "no")
        self.assertIn("not a git repo", self.brain.read(note["path"])["text"])

    @unittest.skipUnless(HAS_GIT, "git not installed")
    def test_commits_in_the_folder_mark_the_session_shipped(self):
        def git(*args, when=None):
            env = dict(os.environ)
            if when is not None:
                stamp = f"@{when // 1000} +0000"
                env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, env=env)

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "Tester")
        (self.root / "app.py").write_text("print('hi')\n", encoding="utf-8")
        git("add", "app.py")
        git("commit", "-q", "-m", "inside the session", when=self.now - 250 * MINUTE)
        (self.root / "ci.txt").write_text("built\n", encoding="utf-8")
        git("add", "ci.txt")
        git("commit", "-q", "-m", "bot bump", "--author", "github-actions[bot] <bot@example.com>", when=self.now - 240 * MINUTE)
        (self.root / "app.py").write_text("print('later')\n", encoding="utf-8")
        git("commit", "-q", "-am", "long after", when=self.now - 100 * MINUTE)
        make_db(self.db, [
            self.row("ses_a", "Ship it", 300, 200),
            self.row("ses_b", "Earlier chat", 600, 500),
        ], messages={"ses_a": self.ago(300, 250, 200), "ses_b": self.ago(600, 500)})
        self.sync()
        notes = {n["meta"]["external"]: n for n in sessions(self.brain)}
        self.assertEqual(notes["ses_a"]["status"], "shipped")
        self.assertEqual(notes["ses_a"]["meta"]["commits"], "1")
        self.assertIn("1 commit in this folder", self.brain.read(notes["ses_a"]["path"])["text"])
        self.assertEqual(notes["ses_b"]["status"], "done")
        self.assertNotIn("git", notes["ses_b"]["meta"])
        self.assertIn("no commits in its folder", self.brain.read(notes["ses_b"]["path"])["text"])

    def test_resync_is_idempotent_and_follows_the_session(self):
        make_db(self.db, [self.row("ses_a", "Add dark mode", 60, 2)])
        self.assertTrue(self.sync())
        self.assertEqual(self.sync(), [])  # nothing changed, nothing rewritten
        connection = sqlite3.connect(self.db)
        connection.execute(
            "update session set summary_additions = 25, summary_files = 1, time_updated = ? where id = 'ses_a'",
            (self.now - 45 * MINUTE,),
        )
        connection.commit()
        connection.close()
        self.sync()
        [note] = sessions(self.brain)
        self.assertEqual(note["status"], "shipped")
        self.assertEqual(note["meta"]["added"], "25")
        self.assertEqual(len(sessions(self.brain)), 1)

    def test_status_set_by_hand_is_kept(self):
        make_db(self.db, [self.row("ses_a", "Spike", 300, 200, summary_additions=5, summary_files=1)])
        self.sync()
        [note] = sessions(self.brain)
        current = self.brain.read(note["path"])
        self.brain.write(note["path"], core.update_note_text(current["text"], {"status": "dropped"}), current["mtime"])
        self.sync()
        [note] = sessions(self.brain)
        self.assertEqual(note["status"], "dropped")

    def test_subagents_are_counted_not_listed_and_other_folders_skipped(self):
        make_db(self.db, [
            self.row("ses_parent", "Survey", 60, 40),
            self.row("ses_child", "Sub task", 50, 45, parent_id="ses_parent"),
            self.row("ses_child2", "Sub task 2", 50, 44, parent_id="ses_parent"),
            self.row("ses_far", "Elsewhere", 60, 40, directory="C:/somewhere/else"),
            self.row("ses_old", "Ancient", 60 * 24 * 40, 60 * 24 * 39),
            self.row("ses_gone", "Archived", 60, 40, time_archived=self.now),
        ])
        self.sync()
        notes = sessions(self.brain)
        self.assertEqual([n["meta"]["external"] for n in notes], ["ses_parent"])
        self.assertIn("2 subagent tasks", self.brain.read(notes[0]["path"])["text"])
        self.sync(everywhere=True)
        self.assertEqual({n["meta"]["external"] for n in sessions(self.brain)}, {"ses_parent", "ses_far"})

    def test_older_schema_without_summary_columns(self):
        make_db(self.db, [
            {"id": "ses_x", "title": "Old style", "directory": str(self.root),
             "time_created": self.now - 300 * MINUTE, "time_updated": self.now - 200 * MINUTE},
        ], columns=OLD_COLUMNS)
        self.sync()
        [note] = sessions(self.brain)
        self.assertEqual(note["status"], "done")
        self.assertEqual(note["meta"]["added"], "0")

    def test_missing_database_is_quiet(self):
        self.assertEqual(self.sync(), [])
        self.assertEqual(sessions(self.brain), [])

    def test_stats_count_auto_sessions(self):
        make_db(self.db, [
            self.row("ses_a", "One", 300, 200, summary_additions=10, summary_files=1),
            self.row("ses_b", "Two", 300, 250),
        ], messages={"ses_a": self.ago(300, 295, 200), "ses_b": self.ago(300, 290, 250)})
        self.sync()
        stats = core.vibe_stats(self.brain.notes(), time.strftime("%Y-%m-%d"))
        self.assertEqual(stats["sessions"], 2)
        self.assertEqual((stats["shipped"], stats["done"], stats["partial"]), (1, 1, 0))
        self.assertEqual(stats["ship_rate"], 100)  # research chats do not drag the ship rate down
        # Active time, not end minus start: 15 and 20 minutes, side by side, so 20 on the clock.
        self.assertEqual(stats["agent_minutes"], 15 + 20)
        self.assertEqual(stats["minutes"], 20)


class StartupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "the startup script is Windows-only")
    def test_startup_script_runs_from_the_package_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(Path(tmp) / "brain")
            original = watch.startup_folder
            watch.startup_folder = lambda: Path(tmp) / "Startup"
            try:
                target = watch.install_startup(brain, ["--everywhere"])
                script = target.read_text(encoding="utf-8")
                self.assertTrue(watch.uninstall_startup())
            finally:
                watch.startup_folder = original
        package_parent = Path(watch.__file__).resolve().parent.parent
        self.assertIn(f'shell.CurrentDirectory = "{package_parent}"', script)
        self.assertIn('""-m"" ""cairn""', script)
        self.assertIn("--everywhere", script)
        self.assertLess(script.index("CurrentDirectory"), script.index("shell.Run"))


@unittest.skipUnless(HAS_GIT, "git not installed")
class VectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "Tester")
        (self.root / "app.py").write_text("print('hi')\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")
        self.brain = Brain(self.root)
        self.brain.init()
        self.now = int(time.time() * 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def tick(self, running, minutes_later=0):
        return watch.sync_vector(self.brain, running, self.now + minutes_later * MINUTE, idle_minutes=30)

    def live(self):
        return [n for n in sessions(self.brain) if n["status"] == "live"]

    def test_open_edit_commit_close(self):
        self.assertEqual(self.tick(True), "")  # first look only records the baseline
        self.assertEqual(self.tick(True, 1), "")  # app open but nothing changed: no session
        (self.root / "app.py").write_text("print('hi there')\n", encoding="utf-8")
        self.assertEqual(self.tick(True, 2), "started")
        [live] = self.live()
        self.assertEqual(live["meta"]["source"], "vector")
        self.assertEqual(live["meta"]["agent"], "vector")
        self.assertEqual(self.tick(True, 3), "")  # the watcher's own note does not count as activity
        self.git("commit", "-q", "-am", "greet properly")
        self.assertEqual(self.tick(True, 4), "activity")
        text = self.brain.read(self.live()[0]["path"])["text"]
        self.assertIn("commit: greet properly", text)
        self.assertEqual(self.tick(False, 5), "ended")
        [done] = sessions(self.brain)
        self.assertEqual(done["status"], "shipped")
        self.assertEqual(done["meta"]["commits"], "1")
        self.assertIn("Vector closed", self.brain.read(done["path"])["text"])

    def test_quiet_repo_ends_the_session_and_closed_app_never_starts_one(self):
        self.tick(True)
        (self.root / "notes.txt").write_text("x\n", encoding="utf-8")
        self.assertEqual(self.tick(True, 1), "started")
        self.assertEqual(self.tick(True, 40), "ended")
        [done] = sessions(self.brain)
        self.assertEqual(done["status"], "partial")  # changed but nothing committed
        self.assertIn("no repo activity for 30 min, nothing committed yet", self.brain.read(done["path"])["text"])
        (self.root / "more.txt").write_text("y\n", encoding="utf-8")
        self.assertEqual(self.tick(False, 41), "")
        self.assertEqual(self.live(), [])

    def test_self_reported_vector_session_is_not_duplicated(self):
        from cairn import vibe

        self.tick(True)
        vibe.start(self.brain, "add dark mode", agent="vector")  # the assistant reports itself
        (self.root / "app.py").write_text("print('dark')\n", encoding="utf-8")
        self.assertEqual(self.tick(True, 1), "")
        [live] = self.live()
        self.assertEqual(live["meta"]["agent"], "vector")
        self.assertNotEqual(live["meta"].get("source"), "vector")

    def test_watch_once_cli(self):
        out, err = io.StringIO(), io.StringIO()
        os.environ["CAIRN_OPENCODE_DB"] = str(Path(self.tmp.name) / "none.db")
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["--root", str(self.root), "vibe", "watch", "--once", "--no-vector"])
        finally:
            del os.environ["CAIRN_OPENCODE_DB"]
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("No new or changed sessions", out.getvalue())


class ProcessTests(unittest.TestCase):
    def test_process_check_runs(self):
        self.assertIsInstance(watch.process_running(("definitely-not-running-cairn.exe",)), bool)
        self.assertFalse(watch.process_running(("definitely-not-running-cairn.exe",)))


if __name__ == "__main__":
    unittest.main()

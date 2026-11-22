"""`cairn vibe watch`: OpenCode sessions read from its database, Vector detected from outside."""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from cairn import core, vibe, watch
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
# OpenCode 2.2: the tables became session_v2 and session_message, with tokens and typed messages.
V2_COLUMNS = (
    "id text primary key, parent_id text, directory text, title text, time_created integer, time_updated integer, "
    "time_archived integer, summary_additions integer, summary_deletions integer, summary_files integer, "
    "model text, cost real, tokens_input integer, tokens_output integer, tokens_reasoning integer, "
    "tokens_cache_read integer, tokens_cache_write integer, idle_outcome text"
)


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


def make_v2_db(path: Path, rows: list[dict], messages: dict | None = None) -> Path:
    """An OpenCode 2.2 database. `messages` maps a session id to (time, type, data) in order."""
    connection = sqlite3.connect(path)
    connection.execute(f"create table session_v2 ({V2_COLUMNS})")
    connection.execute(
        "create table session_message (id text, session_id text, type text, seq integer, time_created integer, "
        "time_updated integer, data text)"
    )
    names = [part.split()[0] for part in V2_COLUMNS.split(", ")]
    for row in rows:
        keys = [k for k in row if k in names]
        connection.execute(
            f"insert into session_v2 ({', '.join(keys)}) values ({', '.join('?' for _ in keys)})",
            [row[k] for k in keys],
        )
        for number, (stamp, kind, data) in enumerate((messages or {}).get(row["id"], [])):
            connection.execute(
                "insert into session_message values (?, ?, ?, ?, ?, ?, ?)",
                (f"{row['id']}-{number}", row["id"], kind, number, stamp, stamp, json.dumps(data)),
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

    def add_row(self, row):
        connection = sqlite3.connect(self.db)
        connection.execute(f"insert into session ({', '.join(row)}) values ({', '.join('?' for _ in row)})", list(row.values()))
        connection.commit()
        connection.close()

    def test_the_first_pass_takes_the_whole_history_and_later_ones_the_last_days(self):
        make_db(self.db, [self.row("ses_old", "Ancient", 60 * 24 * 40, 60 * 24 * 39), self.row("ses_new", "Recent", 60, 40)])
        self.sync()
        self.assertEqual({n["meta"]["external"] for n in sessions(self.brain)}, {"ses_old", "ses_new"}, "a new computer brings all it has")
        self.add_row(self.row("ses_older", "Older still", 60 * 24 * 50, 60 * 24 * 49))
        self.sync()
        self.assertEqual({n["meta"]["external"] for n in sessions(self.brain)}, {"ses_old", "ses_new"}, "after that, 14 days back")
        watch.watch_once(self.brain, vector=False, now_ms=self.now, db_path=self.db, days=0)
        self.assertEqual({n["meta"]["external"] for n in sessions(self.brain)}, {"ses_old", "ses_new", "ses_older"}, "--days 0 reads everything")
        with mock.patch.object(watch.machine, "device_name", return_value="Other laptop"):
            self.add_row(self.row("ses_oldest", "The oldest", 60 * 24 * 60, 60 * 24 * 59))
            self.sync()
        self.assertIn("ses_oldest", {n["meta"]["external"] for n in sessions(self.brain)}, "each computer has its own first pass")

    def test_a_session_names_its_computer_and_another_computers_is_left_alone(self):
        make_db(self.db, [self.row("ses_here", "Made here", 300, 200)])
        with mock.patch.object(watch.machine, "device_name", return_value="ACME Roadrunner 14"):
            self.sync()
            [here] = sessions(self.brain)
            self.assertEqual(here["meta"]["machine"], "ACME Roadrunner 14")
            # Two live notes from sessions this database does not hold: one names another computer, one names none.
            started = watch._stamp(self.now - 600 * MINUTE)
            for slug, extra in (("there", {"machine": "MacBook Pro 15,1"}), ("unnamed", {})):
                meta = {"title": slug, "type": "session", "status": "live", "tags": [], "created": started[:10], "started": started,
                        "source": "opencode", "external": f"ses_{slug}", **extra}
                self.brain.write(f"sessions/{slug}.md", core.serialize_note(meta, f"## Log\n\n- {started[11:]} start: {slug}\n"), None)
            self.sync()
            by_slug = {n["slug"]: n for n in sessions(self.brain)}
            self.assertEqual(by_slug["there"]["status"], "live", "the other computer ends its own sessions")
            self.assertNotEqual(by_slug["unnamed"]["status"], "live", "one that names no computer is this one's to end")
            # A note already stamped keeps its computer even when this watcher refreshes it.
            current = self.brain.read(here["path"])
            self.brain.write(here["path"], core.update_note_text(current["text"], {"machine": "Renamed", "status": "live"}), current["mtime"])
            self.sync()
            self.assertEqual({n["slug"]: n for n in sessions(self.brain)}[here["slug"]]["meta"]["machine"], "Renamed")

    def test_subagents_are_counted_not_listed_and_other_folders_skipped(self):
        make_db(self.db, [
            self.row("ses_parent", "Survey", 60, 40),
            self.row("ses_child", "Sub task", 50, 45, parent_id="ses_parent"),
            self.row("ses_child2", "Sub task 2", 50, 44, parent_id="ses_parent"),
            self.row("ses_far", "Elsewhere", 60, 40, directory="C:/somewhere/else"),
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


class OpenCodeV2Tests(unittest.TestCase):
    """OpenCode 2.2 and later: tokens, effort, typed messages, and scripted runs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.brain = Brain(self.root)
        self.brain.init()
        self.now = int(time.time() * 1000)
        self.db = Path(self.tmp.name) / "opencode.db"
        # Noon yesterday: a fixed point well inside one calendar day, whenever the tests run.
        yesterday = time.localtime(self.now / 1000 - 86_400)
        self.noon = int(time.mktime((yesterday.tm_year, yesterday.tm_mon, yesterday.tm_mday, 12, 0, 0, 0, 0, -1))) * 1000

    def tearDown(self):
        self.tmp.cleanup()

    def row(self, ident, title, started_min_ago, **extra):
        row = {
            "id": ident, "title": title, "directory": str(self.root),
            "time_created": self.now - started_min_ago * MINUTE, "time_updated": self.now,
            "summary_additions": 0, "summary_deletions": 0, "summary_files": 0, "cost": 0.0,
            "model": '{"id": "big-model", "providerID": "acme", "variant": "high"}',
            "tokens_input": 0, "tokens_output": 0, "tokens_reasoning": 0, "tokens_cache_read": 0, "tokens_cache_write": 0,
            "idle_outcome": "succeeded",
        }
        row.update(extra)
        return row

    def turn(self, *minutes_ago, reply="SECRET REPLY TEXT", finished=True, outcome="succeeded"):
        """One prompt and its reply at the given times (minutes ago), then the idle marker when `finished`."""
        asked, replied, *rest = minutes_ago
        chat = [
            (self.now - asked * MINUTE, "user", {"text": "SECRET PROMPT TEXT"}),
            (self.now - replied * MINUTE, "assistant", {
                "model": {"id": "small-model", "providerID": "acme", "variant": "low"},
                "content": [{"type": "reasoning", "text": "SECRET THINKING"}, {"type": "text", "text": reply}],
            }),
        ]
        if finished:
            chat.append((self.now - (rest[0] if rest else replied) * MINUTE, "idle", {"outcome": outcome}))
        return chat

    def sync(self, **options):
        return watch.watch_once(self.brain, vector=False, now_ms=self.now, db_path=self.db, git="definitely-not-git", **options)

    def notes(self):
        return {n["meta"]["external"]: n for n in sessions(self.brain)}

    def text(self, note):
        return self.brain.read(note["path"])["text"]

    def test_new_tables_give_model_effort_tokens_and_where_the_turn_stands(self):
        make_v2_db(self.db, [
            self.row("ses_wait", "Turn finished", 60, tokens_input=1000, tokens_output=200, tokens_reasoning=300, tokens_cache_read=5000),
            self.row("ses_busy", "Still working", 20),
            self.row("ses_fail", "Broke", 300, idle_outcome="failed"),
            self.row("ses_cut", "Cut off", 300),
        ], messages={
            "ses_wait": self.turn(60, 50) + self.turn(10, 5),
            "ses_busy": self.turn(20, 2, finished=False),
            "ses_fail": self.turn(300, 200, outcome="failed"),
            "ses_cut": self.turn(300, 200, finished=False),
        })
        self.sync()
        notes = self.notes()
        self.assertEqual(set(notes), {"ses_wait", "ses_busy", "ses_fail", "ses_cut"})
        waiting = notes["ses_wait"]["meta"]
        # A finished turn ends the session there and then: nobody is asked to come back to it.
        self.assertEqual((notes["ses_wait"]["status"], waiting["ended"]), ("done", watch._stamp(self.now - 5 * MINUTE)))
        self.assertEqual(notes["ses_busy"]["status"], "live")
        self.assertEqual((waiting["model"], waiting["effort"]), ("big-model", "high"))
        # Read and written, reasoning included; what the cache served is kept apart.
        self.assertEqual((waiting["tokens"], waiting["tokens_in"], waiting["tokens_out"], waiting["tokens_cache"]), ("1500", "1000", "500", "5000"))
        self.assertEqual((waiting["prompts"], waiting["turn"]), ("2", "done"))
        self.assertNotIn("waiting for you", self.text(notes["ses_wait"]))
        self.assertEqual(notes["ses_busy"]["meta"]["turn"], "working")
        self.assertNotIn("tokens", notes["ses_busy"]["meta"])
        self.assertEqual((notes["ses_fail"]["status"], notes["ses_fail"]["meta"]["turn"]), ("done", "failed"))
        self.assertIn("last turn stopped with an error", self.text(notes["ses_fail"]))
        self.assertEqual((notes["ses_cut"]["status"], notes["ses_cut"]["meta"]["turn"]), ("done", "working"))
        self.assertIn("stopped mid-turn", self.text(notes["ses_cut"]))
        for note in notes.values():
            self.assertNotIn("SECRET", self.text(note))
        self.assertEqual(self.sync(), [], "a second pass rewrites nothing")

    def test_a_new_prompt_opens_a_finished_session_again(self):
        row = self.row("ses_a", "Back and forth", 60)
        make_v2_db(self.db, [row], messages={"ses_a": self.turn(60, 50)})
        self.sync()
        self.assertEqual(self.notes()["ses_a"]["status"], "done")
        self.db.unlink()
        make_v2_db(self.db, [row], messages={"ses_a": self.turn(60, 50) + self.turn(3, 1, finished=False)})
        self.sync()
        note = self.notes()["ses_a"]
        self.assertEqual((note["status"], note["meta"]["turn"]), ("live", "working"))
        self.assertNotIn("ended", note["meta"])
        self.db.unlink()
        make_v2_db(self.db, [row], messages={"ses_a": self.turn(60, 50) + self.turn(3, 1)})
        self.sync()
        self.assertEqual(self.notes()["ses_a"]["status"], "done")

    def test_a_prompt_in_the_minute_the_turn_ended_still_opens_it_again(self):
        row = self.row("ses_a", "Quick follow-up", 60)
        make_v2_db(self.db, [row], messages={"ses_a": self.turn(60, 5)})
        self.sync()
        self.assertEqual(self.notes()["ses_a"]["status"], "done")
        self.db.unlink()
        again = [(self.now - 5 * MINUTE, "user", {"text": "SECRET PROMPT TEXT"})]  # same minute as the idle marker
        make_v2_db(self.db, [row], messages={"ses_a": self.turn(60, 5) + again})
        self.sync()
        self.assertEqual(self.notes()["ses_a"]["status"], "live")

    def test_changes_landing_just_after_the_turn_still_count_as_shipped(self):
        make_v2_db(self.db, [self.row("ses_a", "Edit then summarise", 60)], messages={"ses_a": self.turn(60, 5)})
        self.sync()
        self.assertEqual(self.notes()["ses_a"]["status"], "done")
        connection = sqlite3.connect(self.db)
        connection.execute("update session_v2 set summary_additions = 12, summary_files = 1 where id = 'ses_a'")
        connection.commit()
        connection.close()
        self.sync()
        self.assertEqual(self.notes()["ses_a"]["status"], "shipped")

    def test_a_session_deleted_from_opencode_is_closed(self):
        make_v2_db(self.db, [
            self.row("ses_gone", "Script run, deleted after", 90, summary_additions=4, summary_files=1),
            self.row("ses_fresh", "Deleted a moment ago", 20),
            self.row("ses_here", "Still working", 20),
        ], messages={
            "ses_gone": self.turn(90, 40, finished=False),
            "ses_fresh": self.turn(20, 2, finished=False),
            "ses_here": self.turn(20, 2, finished=False),
        })
        watch.watch_once(self.brain, vector=False, now_ms=self.now - 35 * MINUTE, db_path=self.db, git="definitely-not-git")
        self.assertEqual({n["status"] for n in self.notes().values()}, {"live"})
        connection = sqlite3.connect(self.db)
        connection.execute("delete from session_v2 where id in ('ses_gone', 'ses_fresh')")
        connection.commit()
        connection.close()
        messages = self.sync()
        notes = self.notes()
        self.assertEqual({key: note["status"] for key, note in notes.items()}, {"ses_gone": "shipped", "ses_fresh": "live", "ses_here": "live"})
        self.assertEqual(notes["ses_gone"]["meta"]["ended"], watch._stamp(self.now - 40 * MINUTE))
        self.assertIn("no longer in OpenCode", self.text(notes["ses_gone"]))
        self.assertIn("OpenCode: 1 session(s) ended, no longer in OpenCode", messages)
        self.assertEqual(self.sync(), [], "a second pass rewrites nothing")
        # A device without OpenCode leaves other devices' live sessions alone.
        self.db.unlink()
        watch.watch_once(self.brain, vector=False, now_ms=self.now + 120 * MINUTE, db_path=self.db, git="definitely-not-git")
        self.assertEqual(self.notes()["ses_here"]["status"], "live")

    def test_model_comes_from_the_last_reply_when_the_session_has_none(self):
        make_v2_db(self.db, [self.row("ses_a", "No model on the row", 300, model=None)], messages={"ses_a": self.turn(300, 200)})
        self.sync()
        note = self.notes()["ses_a"]
        self.assertEqual((note["meta"]["model"], note["meta"]["effort"]), ("small-model", "low"))
        self.assertNotIn("SECRET", self.text(note))

    def test_subagent_tokens_count_toward_the_conversation_that_started_them(self):
        make_v2_db(self.db, [
            self.row("ses_parent", "Survey", 300, tokens_input=100, tokens_output=10),
            self.row("ses_child", "Sub task", 290, parent_id="ses_parent", tokens_input=50, tokens_output=5, tokens_reasoning=5),
            self.row("ses_grandchild", "Sub sub task", 280, parent_id="ses_child", tokens_input=7, tokens_cache_read=40),
        ], messages={"ses_parent": self.turn(300, 200)})
        self.sync()
        [note] = sessions(self.brain)
        self.assertEqual((note["meta"]["tokens"], note["meta"]["tokens_cache"]), ("177", "40"))

    def test_the_closing_reply_is_copied_only_when_asked(self):
        long_reply = "All 12 tests pass.  " + "Done and pushed. " * 40
        make_v2_db(self.db, [self.row("ses_a", "Fix the build", 30)], messages={"ses_a": self.turn(30, 5, reply=long_reply)})
        self.sync()
        note = self.notes()["ses_a"]
        self.assertNotIn("Last reply", self.text(note))
        self.assertNotIn("tests pass", self.text(note))
        self.sync(last_reply=True)
        text = self.text(self.notes()["ses_a"])
        reply = text.split("## Last reply")[1].strip()
        self.assertTrue(reply.startswith("> All 12 tests pass. Done and pushed."), reply)
        self.assertLessEqual(len(reply), watch.REPLY_CHARS + 2)
        self.assertNotIn("SECRET", text)  # the prompt and the reasoning stay out even then
        self.assertEqual([e["kind"] for e in core.session_log(text)], ["start", "note", "end"])

    def test_a_script_firing_one_prompt_chats_becomes_one_note_a_day(self):
        def run(number, minute):
            start = self.noon + minute * MINUTE
            row = self.row(f"ses_run{number}", f"Run {number}", 0, time_created=start, time_updated=start, tokens_input=100, tokens_output=10,
                           idle_outcome="failed" if number == 4 else "succeeded")
            chat = [
                (start, "user", {"text": "SECRET PROMPT TEXT"}),
                (start + MINUTE, "assistant", {"content": [{"type": "text", "text": "SECRET REPLY TEXT"}]}),
                (start + MINUTE, "idle", {"outcome": row["idle_outcome"]}),
            ]
            return row, chat

        def build(count):
            self.db.unlink(missing_ok=True)
            runs = [run(number, number * 2) for number in range(count)]
            talk = self.row("ses_talk", "A real conversation", 0, time_created=self.noon, time_updated=self.noon)
            chat = [(self.noon + m * MINUTE, kind, {}) for m, kind in ((0, "user"), (1, "assistant"), (2, "idle"), (3, "user"), (4, "assistant"), (5, "idle"))]
            make_v2_db(self.db, [row for row, _ in runs] + [talk], messages={**{row["id"]: chat for row, chat in runs}, "ses_talk": chat})

        build(3)  # three quick chats are just three chats
        self.sync()
        self.assertEqual(set(self.notes()), {"ses_run0", "ses_run1", "ses_run2", "ses_talk"})
        build(25)  # the same folder, the same day, now plainly a script
        self.sync()
        notes = self.notes()
        [batch_id] = [ident for ident in notes if ident.startswith("batch-")]
        self.assertEqual(set(notes), {"ses_run0", "ses_run1", "ses_run2", "ses_talk", batch_id}, "notes already written are kept")
        batch = notes[batch_id]
        self.assertEqual(batch["title"], "repo: automated runs")
        self.assertEqual((batch["meta"]["runs"], batch["meta"]["prompts"], batch["meta"]["tokens"]), ("22", "22", str(22 * 110)))
        self.assertEqual(batch["status"], "done")
        self.assertIn("22 automated runs (1 stopped with an error)", self.text(batch))
        self.assertNotIn("SECRET", self.text(batch))
        self.assertEqual(self.sync(), [], "a second pass rewrites nothing")
        stats = core.vibe_stats(self.brain.notes(), time.strftime("%Y-%m-%d"))
        self.assertEqual(stats["sessions"], 5)

    def test_the_watcher_command_takes_last_reply(self):
        make_v2_db(self.db, [self.row("ses_a", "Fix the build", 30)], messages={"ses_a": self.turn(30, 5, reply="Build is green.")})
        out = io.StringIO()
        os.environ["CAIRN_OPENCODE_DB"] = str(self.db)
        try:
            with redirect_stdout(out), redirect_stderr(out):
                code = main(["--root", str(self.root), "vibe", "watch", "--once", "--no-vector", "--no-github", "--last-reply"])
        finally:
            del os.environ["CAIRN_OPENCODE_DB"]
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("> Build is green.", self.text(self.notes()["ses_a"]))


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
                code = main(["--root", str(self.root), "vibe", "watch", "--once", "--no-vector", "--no-github"])
        finally:
            del os.environ["CAIRN_OPENCODE_DB"]
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("No new or changed sessions", out.getvalue())


class SilentSessionTests(unittest.TestCase):
    """A session somebody started by hand and never ended does not stay live for days."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.brain = Brain(self.root)
        self.brain.init()
        self.now = int(time.time() * 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def later(self, minutes):
        return watch.watch_once(
            self.brain, opencode=False, vector=False, now_ms=self.now + minutes * MINUTE,
            db_path=Path(self.tmp.name) / "no.db",
        )

    def test_a_reported_session_ends_after_hours_of_silence_and_reopens_on_its_next_step(self):
        vibe.start(self.brain, "Refactor the parser", agent="vector")
        vibe.log(self.brain, "split the module")
        note = sessions(self.brain)[0]
        self.assertEqual(self.later(watch.REPORTED_IDLE_MINUTES - 5), [])
        self.assertEqual(sessions(self.brain)[0]["status"], "live")
        self.assertEqual(self.later(watch.REPORTED_IDLE_MINUTES + 5), ["Sessions: 1 ended after 4 h without a step"])
        [closed] = sessions(self.brain)
        self.assertEqual((closed["status"], closed["meta"]["closed_by"]), ("done", "watcher"))
        self.assertEqual(closed["meta"]["ended"][:16], closed["meta"]["started"][:16][:11] + core.session_log(closed["body"])[1]["time"])
        self.assertIn("auto-ended, no step for 4 h", closed["body"])
        self.assertEqual(self.later(watch.REPORTED_IDLE_MINUTES + 6), [], "closed once")
        # The assistant comes back and reports to its session by name: it is live again, then ends properly.
        vibe.log(self.brain, "picked it up again", session=note["slug"])
        [reopened] = sessions(self.brain)
        self.assertEqual(reopened["status"], "live")
        self.assertNotIn("ended", reopened["meta"])
        self.assertNotIn("closed_by", reopened["meta"])
        vibe.end(self.brain, "shipped", "merged", session_slug=note["slug"])
        with self.assertRaises(vibe.VibeError):
            vibe.log(self.brain, "too late", session=note["slug"])  # ended on purpose: stays ended

    def test_core_last_activity_follows_the_log_past_midnight(self):
        note = {"meta": {"started": "2026-11-07T23:50"}, "body": "- 23:50 start: x\n- 23:58 note: a\n- 00:10 note: b\n"}
        last = core.session_last_activity(note)
        self.assertEqual(core.minutes_stamp(last), "2026-11-08T00:10")
        self.assertEqual(core.session_last_activity({"meta": {"turn_at": "2026-11-07T09:30"}, "body": ""}), core.stamp_minutes("2026-11-07T09:30"))
        self.assertIsNone(core.session_last_activity({"meta": {}, "body": ""}))


class ProcessTests(unittest.TestCase):
    def test_process_check_runs(self):
        self.assertIsInstance(watch.process_running(("definitely-not-running-cairn.exe",)), bool)
        self.assertFalse(watch.process_running(("definitely-not-running-cairn.exe",)))


if __name__ == "__main__":
    unittest.main()


class ClaimTests(unittest.TestCase):
    """One watcher writes a brain at a time: the app, the startup watcher and a terminal can overlap."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Brain(Path(self.tmp.name))
        self.brain.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_watcher_holds_the_brain_until_it_goes_quiet(self):
        self.assertTrue(watch.claim_watch(self.brain, "app:1", now=1000))
        self.assertTrue(watch.claim_watch(self.brain, "app:1", now=1060), "the holder renews its claim")
        self.assertFalse(watch.claim_watch(self.brain, "cli:2", now=1100), "a second watcher stands by")
        stale = 1060 + watch.CLAIM_STALE_SECONDS + 1
        self.assertTrue(watch.claim_watch(self.brain, "cli:2", now=stale), "a holder that stopped is replaced")
        self.assertFalse(watch.claim_watch(self.brain, "app:1", now=stale + 1))

    def test_release_frees_the_brain_only_for_its_holder(self):
        watch.claim_watch(self.brain, "app:1", now=1000)
        watch.release_watch(self.brain, "cli:2")
        self.assertFalse(watch.claim_watch(self.brain, "cli:2", now=1001), "someone else's release changes nothing")
        watch.release_watch(self.brain, "app:1")
        self.assertTrue(watch.claim_watch(self.brain, "cli:2", now=1002))

    def test_a_damaged_claim_file_does_not_block_watching(self):
        watch._claim_path(self.brain).write_text("{not json", encoding="utf-8")
        self.assertTrue(watch.claim_watch(self.brain, "app:1", now=1000))

    def test_the_claim_never_lands_in_the_notes(self):
        watch.claim_watch(self.brain, "app:1", now=1000)
        self.assertEqual([n for n in self.brain.notes() if n["type"] == "session"], [])
        self.assertFalse(str(watch._claim_path(self.brain)).endswith(".md"))


class MachineNoteTests(unittest.TestCase):
    """The watcher notes the computer's load in machines/, so a phone reading the brain from GitHub sees it too."""

    READING = {
        "name": "ACME Roadrunner 14", "cpu": "Intel Core i5", "cores": 8, "ram_gb": 15.8,
        "at": "2026-03-04T10:00", "cpu_pct": 91, "ram_pct": 80, "ram_free_gb": 3.1, "commit_pct": 70,
        "power": "on battery 62%", "uptime": "up 76 h (3 d 4 h) since restart", "level": "heavy",
        "reasons": ["CPU 91% busy", "memory 80% full"],
        "top": ["python x3: 2.0 GB, 60% CPU", "node: 1.1 GB"],
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Brain(Path(self.tmp.name))
        self.brain.init()

    def tearDown(self):
        self.tmp.cleanup()

    def reading(self, **changes) -> dict:
        return {**self.READING, **changes}

    def test_the_note_reads_back_as_written(self):
        path, text = watch.machine_note(self.READING)
        self.assertEqual(path, "machines/acme-roadrunner-14.md")
        parsed = core.parse_note(text)
        meta = parsed["meta"]
        self.assertEqual((meta["title"], meta["type"], meta["tags"]), ("ACME Roadrunner 14", "note", ["machine"]))
        self.assertEqual((meta["machine_at"], meta["machine_cpu"], meta["machine_cores"], meta["machine_ram_gb"]), ("2026-03-04T10:00", "Intel Core i5", "8", "15.8"))
        self.assertEqual((meta["load"], meta["load_cpu"], meta["load_ram"], meta["load_commit"]), ("heavy", "91", "80", "70"))
        self.assertEqual(meta["load_power"], "on battery 62%")
        self.assertEqual(meta["machine_uptime"], "up 76 h (3 d 4 h) since restart")
        self.assertIn("- Up 76 h (3 d 4 h) since restart", parsed["body"])
        self.assertEqual(meta["load_why"], "CPU 91% busy; memory 80% full")
        self.assertEqual(meta["load_top"], "python x3: 2.0 GB, 60% CPU; node: 1.1 GB")
        self.assertNotIn("publish", meta)
        self.assertIn("whole computer, not one session", parsed["body"])

    def test_a_computer_without_a_name_or_a_cpu_figure_still_gets_a_note(self):
        path, text = watch.machine_note(self.reading(name="", cpu_pct=None, power="", reasons=[], top=[], level="light"))
        self.assertEqual(path, "machines/this-computer.md")
        meta = core.parse_note(text)["meta"]
        self.assertEqual((meta["title"], meta["load"], meta["load_cpu"]), ("This computer", "light", ""))

    NET = {
        "level": "slow", "why": "the internet takes 1800 ms to answer",
        "link": "Wi-Fi Office 5G · 802.11ax 5 GHz · signal good (86%) · link 865 Mbit/s",
        "reach": "Internet answers in 1800 ms · in use now 2.0 down, 0.2 up Mbit/s",
        "speed": "measured 12 Mbit/s down at 10:42",
    }

    def test_the_note_carries_the_network_the_computer_is_on(self):
        parsed = core.parse_note(watch.machine_note(self.reading(net=self.NET))[1])
        meta = parsed["meta"]
        self.assertEqual((meta["net"], meta["net_why"]), ("slow", "the internet takes 1800 ms to answer"))
        self.assertEqual((meta["net_link"], meta["net_reach"], meta["net_speed"]), (self.NET["link"], self.NET["reach"], self.NET["speed"]))
        self.assertIn("- Network: Wi-Fi Office 5G", parsed["body"])
        self.assertIn("- Internet answers in 1800 ms · in use now 2.0 down, 0.2 up Mbit/s; measured 12 Mbit/s down at 10:42", parsed["body"])

    def test_a_reading_without_the_network_still_gets_a_note(self):
        for reading in (self.READING, self.reading(net=None)):
            parsed = core.parse_note(watch.machine_note(reading)[1])
            self.assertEqual((parsed["meta"]["net"], parsed["meta"]["net_link"], parsed["meta"]["net_reach"]), ("", "", ""))
            self.assertNotIn("Network", parsed["body"])

    def test_the_internet_going_or_turning_slow_is_written_at_once(self):
        vibe.start(self.brain, "Something live")
        good = {**self.NET, "level": "good", "why": ""}
        self.assertTrue(watch.sync_machine(self.brain, self.reading(net=good)))
        self.assertFalse(watch.sync_machine(self.brain, self.reading(net=good)), "the same network is not written again")
        self.assertTrue(watch.sync_machine(self.brain, self.reading(net=self.NET)), "slow is")
        self.assertTrue(watch.sync_machine(self.brain, self.reading(net={**self.NET, "level": "offline"})), "gone is")
        self.assertTrue(watch.sync_machine(self.brain, self.reading(net=good)), "and back is")
        self.assertEqual(core.parse_note(self.brain.read("machines/acme-roadrunner-14.md")["text"])["meta"]["net"], "good")

    def test_a_reading_from_before_uptime_was_measured_still_gets_a_note(self):
        reading = {key: value for key, value in self.READING.items() if key != "uptime"}
        parsed = core.parse_note(watch.machine_note(reading)[1])
        self.assertEqual(parsed["meta"]["machine_uptime"], "")
        self.assertNotIn("since restart", parsed["body"])

    def test_nothing_is_written_without_a_live_session(self):
        self.assertFalse(watch.sync_machine(self.brain, self.READING))
        self.assertFalse(self.brain.resolve("machines/acme-roadrunner-14.md").exists())

    def test_the_note_is_rewritten_only_when_the_load_really_changed(self):
        vibe.start(self.brain, "Something live")
        self.assertTrue(watch.sync_machine(self.brain, self.READING), "the first reading is written")
        self.assertFalse(watch.sync_machine(self.brain, self.READING), "the same reading is not")
        self.assertFalse(watch.sync_machine(self.brain, self.reading(at="2026-03-04T10:10", cpu_pct=86, ram_pct=84)), "a small move is not")
        self.assertTrue(watch.sync_machine(self.brain, self.reading(at="2026-03-04T10:01", cpu_pct=70, ram_pct=80, level="medium")), "another level is")
        self.assertTrue(watch.sync_machine(self.brain, self.reading(at="2026-03-04T10:02", cpu_pct=58, level="medium")), "CPU moved 12 points")
        self.assertFalse(watch.sync_machine(self.brain, self.reading(at="2026-03-04T10:16", cpu_pct=58, level="medium")), "14 minutes on, unchanged")
        self.assertTrue(watch.sync_machine(self.brain, self.reading(at="2026-03-04T10:17", cpu_pct=58, level="medium")), "15 minutes on, refreshed")
        meta = core.parse_note(self.brain.read("machines/acme-roadrunner-14.md")["text"])["meta"]
        self.assertEqual((meta["machine_at"], meta["load"], meta["load_cpu"]), ("2026-03-04T10:17", "medium", "58"))

    def test_an_unreadable_computer_writes_nothing(self):
        vibe.start(self.brain, "Something live")
        with mock.patch.object(watch.machine, "snapshot", return_value=None):
            self.assertFalse(watch.sync_machine(self.brain))
        self.assertFalse((self.brain.dir / "machines").exists())

    def test_a_pass_notes_the_load_only_when_asked(self):
        vibe.start(self.brain, "Something live")
        with mock.patch.object(watch.machine, "snapshot", return_value=self.READING):
            quiet = watch.watch_once(self.brain, opencode=False, vector=False)
            self.assertFalse((self.brain.dir / "machines").exists())
            lines = watch.watch_once(self.brain, opencode=False, vector=False, laptop=True)
        self.assertEqual(quiet, [])
        self.assertEqual(lines, ["Laptop: load noted in machines/"])

    def test_a_pass_asks_github_only_when_asked(self):
        with mock.patch.object(watch.github_usage, "sync", return_value=True) as sync:
            quiet = watch.watch_once(self.brain, opencode=False, vector=False)
            sync.assert_not_called()
            lines = watch.watch_once(self.brain, opencode=False, vector=False, github=True)
        self.assertEqual(quiet, [])
        self.assertEqual(lines, ["GitHub: Actions minutes noted in github-usage/"])

    def test_a_pass_notes_other_accounts_by_name_even_without_the_main_one(self):
        """One computer writes the main account's note; another may be the only one signed in to a second account."""
        with mock.patch.object(watch.github_usage, "sync", return_value=True) as sync:
            lines = watch.watch_once(self.brain, opencode=False, vector=False, github=False, github_accounts=("grace",))
        sync.assert_called_once_with(self.brain, None, None, account="grace")
        self.assertEqual(lines, ["GitHub: Actions minutes of grace noted in github-usage/grace/"])

"""A chat is on the Vibe page from its first command: the memory skill checks it in (cairn/chatlog.py)."""

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
import unittest
from pathlib import Path
from unittest import mock

from cairn import chatlog, connect, core, vibe, watch
from cairn.brain import Brain
from cairn.cli import main

AT = dt.datetime(2026, 11, 27, 15, 37)


def later(minutes: int) -> dt.datetime:
    return AT + dt.timedelta(minutes=minutes)


class ChatCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.brain = Brain(self.tmp / "project")
        self.brain.init()
        self.chat = self.tmp / "scratch" / "session_one"
        self.other = self.tmp / "scratch" / "session_two"
        self.chat.mkdir(parents=True)
        self.other.mkdir(parents=True)
        named = mock.patch.dict(os.environ, {"CAIRN_DEVICE": "Test laptop"})
        named.start()
        self.addCleanup(named.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def sessions(self) -> list[dict]:
        return sorted((n for n in self.brain.notes() if n["type"] == "session"), key=lambda n: n["path"])

    def note(self, path: str) -> dict:
        file = self.brain.read(path)
        return core.note_from_file(file["path"], file["text"], file["mtime"])

    def log(self, path: str) -> list[str]:
        return [f"{entry['kind']}: {entry['text']}" for entry in core.session_log(self.note(path)["body"])]


class CheckinTests(ChatCase):
    def test_the_first_command_of_a_chat_opens_its_session(self):
        entry = chatlog.checkin(self.brain, self.chat, agent="vector", now=AT)
        self.assertEqual(entry["state"], "opened")
        self.assertTrue(entry["unnamed"])
        note = self.note(entry["path"])
        self.assertEqual(note["status"], "live")
        self.assertEqual(note["title"], "Vector chat, goal not named yet (15:37)")
        self.assertEqual(note["meta"]["agent"], "vector")
        self.assertEqual(note["meta"]["machine"], "Test laptop")
        self.assertEqual(note["meta"]["started"], "2026-11-27T15:37")
        self.assertEqual(note["meta"]["turn_at"], "2026-11-27T15:37")
        self.assertEqual(note["meta"]["chat"], chatlog.chat_key(self.chat))
        self.assertEqual(self.log(entry["path"]), ["start: Vector chat, goal not named yet (15:37)"])

    def test_the_folder_on_the_note_names_no_user(self):
        with mock.patch.object(Path, "home", return_value=self.tmp):
            entry = chatlog.checkin(self.brain, self.chat, now=AT)
        self.assertEqual(self.note(entry["path"])["meta"]["folder"], "~/scratch/session_one")

    def test_later_commands_are_signs_of_life_and_not_one_write_each(self):
        entry = chatlog.checkin(self.brain, self.chat, now=AT)
        before = self.brain.read(entry["path"])["text"]
        self.assertEqual(chatlog.checkin(self.brain, self.chat, now=later(4))["state"], "live")
        self.assertEqual(self.brain.read(entry["path"])["text"], before)
        chatlog.checkin(self.brain, self.chat, now=later(18))
        note = self.note(entry["path"])
        self.assertEqual(note["meta"]["turn_at"], "2026-11-27T15:55")
        self.assertEqual(core.session_last_activity(note), core.stamp_minutes("2026-11-27T15:55"))
        self.assertEqual(len(self.sessions()), 1)

    def test_the_chat_names_its_work_once_it_knows_it(self):
        entry = chatlog.checkin(self.brain, self.chat, agent="vector", now=AT)
        named = chatlog.checkin(self.brain, self.chat, agent="vector", task="Lory Aaron draft:  reply on the DOE thread", now=later(1))
        self.assertEqual((named["state"], named["unnamed"], named["path"]), ("named", False, entry["path"]))
        note = self.note(entry["path"])
        self.assertEqual(note["title"], "Lory Aaron draft: reply on the DOE thread")
        self.assertNotIn("unnamed", note["meta"])
        self.assertIn("## Goal\n\nLory Aaron draft: reply on the DOE thread\n\n## Log", note["body"])
        self.assertEqual(self.log(entry["path"])[-1], "prompt: Lory Aaron draft: reply on the DOE thread")

    def test_a_task_given_at_the_start_is_the_title(self):
        entry = chatlog.checkin(self.brain, self.chat, agent="vector", task="update OpenCode to 2.4.1", now=AT)
        self.assertEqual((entry["state"], entry["unnamed"], entry["title"]), ("opened", False, "update OpenCode to 2.4.1"))
        again = chatlog.checkin(self.brain, self.chat, task="and automate the update", now=later(30))
        self.assertEqual(again["path"], entry["path"])
        self.assertEqual(self.log(entry["path"])[-1], "prompt: and automate the update")

    def test_each_chat_folder_has_its_own_session(self):
        one = chatlog.checkin(self.brain, self.chat, task="first", now=AT)
        two = chatlog.checkin(self.brain, self.other, task="second", now=AT)
        self.assertNotEqual(one["path"], two["path"])
        self.assertEqual(chatlog.live_slug(self.brain, self.other), two["slug"])
        self.assertEqual(chatlog.live_slug(self.brain, self.tmp), "")

    def test_two_commands_at_the_same_moment_open_one_note(self):
        found = []
        threads = [threading.Thread(target=lambda: found.append(chatlog.checkin(self.brain, self.chat, now=AT)["path"]))
                   for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(found)), 1)
        self.assertEqual(len(self.sessions()), 1)

    def test_the_small_file_stays_on_this_computer_and_forgets_old_folders(self):
        (self.brain.root / ".git").mkdir()
        chatlog.checkin(self.brain, self.chat, now=AT)
        state = self.brain.root / ".git" / chatlog.STATE_FILE
        self.assertEqual(list(json.loads(state.read_text(encoding="utf-8"))), [chatlog.chat_key(self.chat)])
        chatlog.checkin(self.brain, self.other, now=later(60 * 24 * 40))
        self.assertEqual(list(json.loads(state.read_text(encoding="utf-8"))), [chatlog.chat_key(self.other)])


class StepTests(ChatCase):
    def test_a_step_is_one_line_with_the_progress_the_page_reads(self):
        entry = chatlog.checkin(self.brain, self.chat, task="Lory draft", now=AT)
        chatlog.step(self.brain, self.chat, "turn 10: 49 retest rows filled,\n not sent", progress=80, now=later(18))
        self.assertEqual(self.log(entry["path"])[-1], "note: [80%] turn 10: 49 retest rows filled, not sent")
        self.assertEqual(self.note(entry["path"])["meta"]["turn_at"], "2026-11-27T15:55")

    def test_a_step_opens_the_session_when_it_is_the_first_command(self):
        entry = chatlog.step(self.brain, self.chat, "tests green", kind="pass", agent="vector", now=AT)
        self.assertEqual(entry["state"], "opened")
        self.assertEqual(self.log(entry["path"])[-1], "pass: tests green")

    def test_what_cannot_be_logged_is_said(self):
        for arguments in ({"text": " "}, {"text": "x", "progress": 140}, {"text": "x", "kind": "end"}):
            with self.subTest(arguments), self.assertRaises(chatlog.ChatError):
                chatlog.step(self.brain, self.chat, now=AT, **arguments)

    def test_a_step_written_with_cairn_vibe_log_moves_the_sign_of_life_too(self):
        entry = chatlog.checkin(self.brain, self.chat, task="work", now=AT)
        with mock.patch.object(vibe, "now_stamp", return_value="2026-11-27T16:40"):
            vibe.log(self.brain, "merged", kind="commit", session=entry["slug"])
        note = self.note(entry["path"])
        self.assertEqual(note["meta"]["turn_at"], "2026-11-27T16:40")
        self.assertEqual(core.session_last_activity(note), core.stamp_minutes("2026-11-27T16:40"))

    def test_a_session_that_no_chat_checked_in_keeps_its_log_as_its_clock(self):
        note = vibe.start(self.brain, "by hand")
        vibe.log(self.brain, "a step", session=note["path"].rsplit("/", 1)[-1][:-3])
        self.assertNotIn("turn_at", self.note(note["path"])["meta"])


class FinishTests(ChatCase):
    def test_the_chat_says_how_it_ended(self):
        entry = chatlog.checkin(self.brain, self.chat, task="send the Aaron reply", now=AT)
        ended = chatlog.finish(self.brain, self.chat, "partial", "drafted, not sent: 2 values open", now=later(20))
        self.assertEqual((ended["state"], ended["status"]), ("ended", "partial"))
        note = self.note(entry["path"])
        self.assertEqual((note["status"], note["meta"]["ended"]), ("partial", "2026-11-27T15:57"))
        self.assertEqual(self.log(entry["path"])[-1], "end: partial: drafted, not sent: 2 values open")
        with self.assertRaises(chatlog.ChatError):
            chatlog.finish(self.brain, self.chat, "great", now=later(21))

    def test_its_closing_commands_open_nothing_new(self):
        entry = chatlog.checkin(self.brain, self.chat, task="the work", now=AT)
        chatlog.finish(self.brain, self.chat, "done", "answered", now=later(5))
        rest = chatlog.checkin(self.brain, self.chat, now=later(7))
        self.assertEqual((rest["state"], rest["path"]), ("resting", entry["path"]))
        self.assertEqual(len(self.sessions()), 1)

    def test_the_next_request_of_the_chat_is_a_new_session(self):
        first = chatlog.checkin(self.brain, self.chat, task="the work", now=AT)
        chatlog.finish(self.brain, self.chat, "shipped", now=later(5))
        second = chatlog.checkin(self.brain, self.chat, task="one more thing", now=later(6))
        self.assertEqual(second["state"], "opened")
        self.assertNotEqual(second["path"], first["path"])
        self.assertEqual(self.note(first["path"])["status"], "shipped")
        self.assertEqual(chatlog.live_slug(self.brain, self.chat), second["slug"])

    def test_work_that_goes_on_without_a_new_name_is_the_same_work_continued(self):
        first = chatlog.checkin(self.brain, self.chat, task="the work", now=AT)
        chatlog.finish(self.brain, self.chat, "partial", now=later(5))
        entry = chatlog.step(self.brain, self.chat, "found the missing value", now=later(90))
        self.assertNotEqual(entry["path"], first["path"])
        self.assertEqual(entry["title"], "the work, continued")
        chatlog.finish(self.brain, self.chat, "shipped", now=later(95))
        self.assertEqual(chatlog.checkin(self.brain, self.chat, now=later(200))["title"], "the work, continued")

    def test_a_session_the_watcher_ended_for_silence_is_the_same_one_again(self):
        entry = chatlog.checkin(self.brain, self.chat, task="long work", now=AT)
        now_ms = int(later(300).timestamp() * 1000)
        self.assertEqual(watch.close_silent_reported(self.brain, now_ms), 1)
        self.assertEqual(self.note(entry["path"])["meta"]["closed_by"], "watcher")
        again = chatlog.checkin(self.brain, self.chat, now=later(301))
        self.assertEqual((again["state"], again["path"]), ("reopened", entry["path"]))
        note = self.note(entry["path"])
        self.assertEqual(note["status"], "live")
        self.assertNotIn("ended", note["meta"])
        self.assertNotIn("closed_by", note["meta"])
        self.assertEqual(self.log(entry["path"])[-1], "note: at work again")


    def test_a_session_that_ended_for_silence_long_ago_is_not_gone_on_with(self):
        # One project folder, another chat the next day: it gets a note of its own, without the old name.
        first = chatlog.checkin(self.brain, self.chat, agent="vector", task="yesterday's work", now=AT)
        watch.close_silent_reported(self.brain, int(later(300).timestamp() * 1000))
        entry = chatlog.checkin(self.brain, self.chat, agent="vector", now=later(60 * 20))
        self.assertEqual(entry["state"], "opened")
        self.assertNotEqual(entry["path"], first["path"])
        self.assertTrue(entry["unnamed"])
        self.assertEqual(self.note(first["path"])["status"], "done")


class WatcherTests(ChatCase):
    def closed_after(self, minutes: int) -> int:
        return watch.close_silent_reported(self.brain, int(later(minutes).timestamp() * 1000))

    def test_a_chat_that_never_named_its_work_leaves_the_page_after_an_hour(self):
        unnamed = chatlog.checkin(self.brain, self.chat, agent="vector", now=AT)
        named = chatlog.checkin(self.brain, self.other, agent="vector", task="real work", now=AT)
        self.assertEqual(self.closed_after(59), 0)
        self.assertEqual(self.closed_after(61), 1)
        self.assertEqual(self.note(unnamed["path"])["status"], "done")
        self.assertIn("no sign of life for 1 h", self.log(unnamed["path"])[-1])
        self.assertEqual(self.note(named["path"])["status"], "live")
        self.assertEqual(self.closed_after(239), 0)
        self.assertEqual(self.closed_after(241), 1)

    def test_a_sign_of_life_keeps_it_there(self):
        chatlog.checkin(self.brain, self.chat, now=AT)
        chatlog.checkin(self.brain, self.chat, now=later(50))
        self.assertEqual(self.closed_after(100), 0)
        self.assertEqual(self.closed_after(111), 1)


@contextlib.contextmanager
def working_in(folder: Path):
    before = Path.cwd()
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(before)


def run_cli(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--root", str(root), *args])
    return code, out.getvalue(), err.getvalue()


class CommandLineTests(ChatCase):
    """The `cairn` command an assistant already uses, run in a chat's own folder."""

    def test_cairn_vibe_start_names_the_chats_session_instead_of_opening_a_second(self):
        entry = chatlog.checkin(self.brain, self.chat, agent="vector")
        with working_in(self.chat):
            code, out, _ = run_cli(self.brain.root, "vibe", "start", "fix the sync", "--agent", "vector")
        self.assertEqual(code, 0)
        self.assertIn(entry["path"], out)
        self.assertEqual(len(self.sessions()), 1)
        self.assertEqual(self.note(entry["path"])["title"], "fix the sync")

    def test_a_second_start_in_the_chat_is_where_its_signs_of_life_go(self):
        first = chatlog.checkin(self.brain, self.chat, task="first job")
        with working_in(self.chat):
            run_cli(self.brain.root, "vibe", "start", "second job", "--agent", "vector")
        self.assertEqual(len(self.sessions()), 2)
        second = chatlog.session_of(self.brain, self.chat)
        self.assertNotEqual(second["path"], first["path"])
        self.assertEqual(second["title"], "second job")
        self.assertEqual(second["meta"]["chat"], chatlog.chat_key(self.chat))

    def test_cairn_vibe_start_after_a_cd_names_the_one_chat_that_just_checked_in(self):
        entry = chatlog.checkin(self.brain, self.chat, agent="vector")
        with working_in(self.tmp):
            run_cli(self.brain.root, "vibe", "start", "fix the sync", "--agent", "vector")
        self.assertEqual([n["title"] for n in self.sessions()], ["fix the sync"])
        self.assertEqual(self.sessions()[0]["path"], entry["path"])

    def test_with_two_nameless_chats_there_is_no_telling_and_nothing_is_adopted(self):
        chatlog.checkin(self.brain, self.chat, agent="vector")
        chatlog.checkin(self.brain, self.other, agent="vector")
        with working_in(self.tmp):
            run_cli(self.brain.root, "vibe", "start", "fix the sync", "--agent", "vector")
        self.assertEqual(len(self.sessions()), 3)
        with working_in(self.tmp):  # and another app's chat is never taken for this one's
            run_cli(self.brain.root, "vibe", "start", "a claude job", "--agent", "claude")
        self.assertEqual(len(self.sessions()), 4)

    def test_outside_a_chat_cairn_vibe_start_is_as_it_was(self):
        with working_in(self.other):
            run_cli(self.brain.root, "vibe", "start", "by hand in a terminal")
        note = self.sessions()[0]
        self.assertNotIn("chat", note["meta"])
        self.assertIsNone(chatlog.session_of(self.brain, self.other))

    def test_a_step_without_a_session_named_goes_to_the_chat_it_is_run_in(self):
        one = chatlog.checkin(self.brain, self.chat, task="first chat")
        two = chatlog.checkin(self.brain, self.other, task="second chat")
        with working_in(self.chat):
            code, _, err = run_cli(self.brain.root, "vibe", "log", "--progress", "40", "design chosen")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.log(one["path"])[-1], "note: [40%] design chosen")
        self.assertEqual(len(self.log(two["path"])), 1)
        with working_in(self.tmp):  # no chat here: two are live, so it has to be told which
            code, _, err = run_cli(self.brain.root, "vibe", "log", "whose is this")
        self.assertEqual(code, 2)
        self.assertIn("--session", err)

    def test_a_journal_line_from_a_chats_folder_is_a_step_of_the_chat(self):
        entry = chatlog.checkin(self.brain, self.chat, task="Lory draft")
        with working_in(self.chat):
            run_cli(self.brain.root, "log", "Lory Aaron draft turn 10: 49 retest rows filled. Not sent. #lory")
        self.assertEqual(self.log(entry["path"])[-1], "note: Lory Aaron draft turn 10: 49 retest rows filled. Not sent. #lory")
        with working_in(self.other):
            run_cli(self.brain.root, "log", "a line from somewhere else")
        self.assertEqual(len(self.sessions()), 1)
        self.assertEqual(len(self.log(entry["path"])), 2)


class SkillTests(ChatCase):
    """The scripts as an AI app runs them: by their path, in the chat's working folder."""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / ".vector").mkdir(parents=True)
        (self.home / ".config" / "opencode").mkdir(parents=True)
        connect.connect(self.brain, ["vector", "opencode"], home=self.home, settings_folder=self.tmp / "cairn-home")
        self.skill = self.home / ".vector" / "skills" / "cairn-memory"

    def run_script(self, name, *args, folder=None, skill=None, env=None):
        return subprocess.run(
            [sys.executable, str((skill or self.skill) / name), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=folder or self.chat,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
        )

    def test_reading_the_rules_puts_the_chat_on_the_page_and_tells_it_so(self):
        done = self.run_script("recall.py", "rules")
        self.assertEqual(done.returncode, 0, done.stderr)
        session = self.sessions()[0]
        self.assertTrue(done.stdout.startswith(f"THIS CHAT IS ON CAIRN (Vibe page, every device): {session['path']}"), done.stdout)
        self.assertIn("remember.py begin", done.stdout)
        self.assertEqual((session["status"], session["meta"]["agent"], session["meta"]["machine"]), ("live", "vector", "Test laptop"))
        self.assertEqual(session["meta"]["unnamed"], "yes")

    def test_the_whole_life_of_a_chat(self):
        done = self.run_script("recall.py", "rules", "--task", "draft the Lory reply to Aaron")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("remember.py begin", done.stdout)
        self.run_script("recall.py", "search", "lory")
        self.assertEqual(self.run_script("remember.py", "step", "turn 1 drafted, not sent", "--progress", "30").returncode, 0)
        saved = self.run_script("remember.py", "note", "Lory draft lesson", "--body", "Attachments.Add(path, 1, 0) hides a file.")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        ended = self.run_script("remember.py", "finish", "partial", "in Drafts, not sent: 2 values open")
        self.assertEqual(ended.returncode, 0, ended.stderr)
        self.run_script("remember.py", "note", "Follow-up record", "--body", "What was done, where it lives, what is open.")
        session = self.sessions()
        self.assertEqual(len(session), 1)
        self.assertEqual((session[0]["title"], session[0]["status"]), ("draft the Lory reply to Aaron", "partial"))
        self.assertEqual(self.log(session[0]["path"]), [
            "start: draft the Lory reply to Aaron",
            "note: [30%] turn 1 drafted, not sent",
            "note: saved note: Lory draft lesson",
            "end: partial: in Drafts, not sent: 2 values open",
        ])

    def test_a_chat_that_starts_with_a_search_is_on_the_page_too_and_names_itself_later(self):
        done = self.run_script("recall.py", "search", "anything")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("THIS CHAT IS ON CAIRN", done.stderr)
        self.assertNotIn("THIS CHAT IS ON CAIRN", done.stdout)
        begun = self.run_script("remember.py", "begin", "check the ASUS drafts")
        self.assertIn("(named)", begun.stdout)
        self.assertEqual([n["title"] for n in self.sessions()], ["check the ASUS drafts"])
        self.assertNotIn("THIS CHAT IS ON CAIRN", self.run_script("recall.py", "search", "more").stderr)

    def test_two_chats_are_two_sessions(self):
        self.run_script("recall.py", "rules", "--task", "one", folder=self.chat)
        self.run_script("recall.py", "rules", "--task", "two", folder=self.other)
        self.assertEqual(sorted(n["title"] for n in self.sessions()), ["one", "two"])

    def test_a_secret_is_not_logged_as_a_step(self):
        self.run_script("recall.py", "rules", "--task", "work")
        refused = self.run_script("remember.py", "step", "signed in with password: hunter2hunter2")
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotIn("hunter2", self.brain.read(self.sessions()[0]["path"])["text"])

    def test_it_can_be_switched_off_and_a_command_never_fails_for_it(self):
        done = self.run_script("recall.py", "rules", env={"CAIRN_NO_CHECKIN": "1"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("ON CAIRN", done.stdout)
        self.assertEqual(self.sessions(), [])
        # Its small file damaged, and a folder where the session notes should be that is a file instead:
        # the rules are still read.
        (self.brain.dir / chatlog.STATE_FILE).write_text("{not json", encoding="utf-8")
        shutil.rmtree(self.brain.dir / "sessions")
        (self.brain.dir / "sessions").write_text("in the way", encoding="utf-8")
        done = self.run_script("recall.py", "rules")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("ON CAIRN", done.stdout)
        self.assertIn("decision", done.stdout.lower() + "decision")

    def test_opencodes_chats_are_left_to_the_watcher(self):
        skill = self.home / ".config" / "opencode" / "skills" / "cairn-memory"
        self.assertFalse(json.loads((skill / "config.json").read_text(encoding="utf-8"))["checkin"])
        self.assertNotIn("This chat on Cairn", (skill / "SKILL.md").read_text(encoding="utf-8"))
        done = self.run_script("recall.py", "rules", skill=skill)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.sessions(), [])
        self.assertNotEqual(self.run_script("remember.py", "step", "x", skill=skill).returncode, 0)

    def test_the_guide_tells_a_vector_chat_its_three_commands(self):
        guide = (self.skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## This chat on Cairn", guide)
        for words in ('recall.py rules --task', "remember.py step", "remember.py finish shipped|partial|dropped|done"):
            self.assertIn(words, guide)
        self.assertNotIn("{{", guide)
        config = json.loads((self.skill / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["app"], config["checkin"]), ("vector", True))


if __name__ == "__main__":
    unittest.main()

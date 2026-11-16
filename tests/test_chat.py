"""Chat with the OpenCode models: storage, sending, reply streaming and session reuse (no real OpenCode)."""

import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cairn import chat


class FakeProcess:
    """Stands in for `opencode run --format json`, emitting a session id and two reply chunks."""

    def __init__(self, command, **kwargs):
        self.command = command
        events = [
            {"type": "step_start", "sessionID": "ses_test", "part": {"type": "step-start"}},
            {"type": "text", "sessionID": "ses_test", "part": {"id": "p1", "type": "text", "text": "Hello"}},
            {"type": "text", "sessionID": "ses_test", "part": {"id": "p1", "type": "text", "text": "Hello there"}},
        ]
        self.stdout = io.StringIO("".join(json.dumps(event) + "\n" for event in events))
        self.stderr = io.StringIO("")
        self.returncode = None
        self.pid = 4321

    def wait(self):
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CAIRN_HOME": self.tmp.name})
        self.env.start()
        self.commands = []
        self.started_in = []

        def fake_popen(command, **kwargs):
            self.commands.append(command)
            self.started_in.append(kwargs.get("cwd"))
            return FakeProcess(command, **kwargs)

        self.patches = [
            mock.patch.object(chat, "find_opencode", return_value="opencode"),
            mock.patch.object(chat.subprocess, "Popen", side_effect=fake_popen),
            mock.patch.object(chat, "models", return_value=["pegaai/Pega-Chat"]),
            mock.patch.object(chat, "keep_normal_priority"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def wait_idle(self, chat_id):
        for _ in range(100):
            thread = chat.load(chat_id)
            if thread["status"] == "idle":
                return thread
            time.sleep(0.02)
        self.fail("chat never finished")

    def test_send_streams_reply_and_keeps_session(self):
        first = chat.send("", "hi there", "pegaai/Pega-Chat")
        self.assertEqual(first["status"], "running")
        done = self.wait_idle(first["id"])
        self.assertEqual([m["role"] for m in done["messages"]], ["user", "assistant"])
        self.assertEqual(done["messages"][1]["text"], "Hello there")
        self.assertNotIn("pending", done["messages"][1])
        self.assertEqual(done["session"], "ses_test")
        self.assertIn("-m", self.commands[0])
        self.assertNotIn("-s", self.commands[0])

        chat.send(first["id"], "and again", "")
        again = self.wait_idle(first["id"])
        self.assertEqual(len(again["messages"]), 4)
        self.assertIn("ses_test", self.commands[1])  # continues the same OpenCode session

    def test_a_reply_run_is_watched_for_a_priority_drop(self):
        with mock.patch.object(chat, "_keep_normal") as watcher:
            started = chat.send("", "hi there", "")
            self.wait_idle(started["id"])
        (process,), _ = watcher.call_args
        self.assertEqual(process.command, self.commands[0])

    def test_a_run_is_put_back_to_normal_until_it_ends(self):
        process = mock.Mock(pid=4321)
        process.poll.side_effect = [None, None, 0]
        with mock.patch.object(chat, "PRIORITY_CHECK_S", 0):
            chat._keep_normal(process)
        self.assertEqual(chat.keep_normal_priority.call_args_list, [mock.call(4321), mock.call(4321)])

    def test_summary_lists_chats_newest_first(self):
        a = chat.send("", "first", "")
        self.wait_idle(a["id"])
        b = chat.send("", "second", "")
        self.wait_idle(b["id"])
        listed = chat.summary()
        self.assertTrue(listed["available"])
        self.assertEqual([c["title"] for c in listed["chats"]], ["second", "first"])

    def test_saving_waits_out_a_page_that_is_reading_the_chat(self):
        real_replace = os.replace
        refusals = [PermissionError(13, "Access is denied")] * 2

        def busy_then_free(source, target):
            if refusals:
                raise refusals.pop()
            return real_replace(source, target)

        with mock.patch.object(chat.os, "replace", side_effect=busy_then_free):
            chat._save({"id": "20260101-abc", "title": "kept", "messages": []})
        self.assertEqual(chat.load("20260101-abc")["title"], "kept")

    def test_rejects_empty_and_bad_ids(self):
        with self.assertRaises(ValueError):
            chat.send("", "   ", "")
        self.assertIsNone(chat.load("../../etc"))  # path tricks are just "no such chat"

    def test_delete_removes_the_chat(self):
        thread = chat.send("", "bye", "")
        self.wait_idle(thread["id"])
        chat.delete(thread["id"])
        self.assertIsNone(chat.load(thread["id"]))
        self.assertFalse((Path(self.tmp.name) / "chats" / f"{thread['id']}.json").exists())

    def test_chat_started_from_a_session_continues_it_in_its_folder(self):
        folder = tempfile.mkdtemp(dir=self.tmp.name)
        started = chat.send("", "carry on", "", session="ses_abc", folder=folder, title="Fix the relay")
        done = self.wait_idle(started["id"])
        command = self.commands[0]
        self.assertEqual(command[command.index("-s") + 1], "ses_abc")  # continues that OpenCode session
        self.assertNotIn("--title", command)
        self.assertEqual(self.started_in[0], folder)  # and runs in its repo, not the empty chat workspace
        self.assertEqual((done["title"], done["folder"]), ("Fix the relay", folder))

        chat.send(started["id"], "and again", "", session="ses_other", folder=self.tmp.name)
        self.wait_idle(started["id"])
        self.assertEqual(self.started_in[1], folder, "an existing chat keeps its own folder and session")
        self.assertNotIn("ses_other", self.commands[1])

    def test_plain_chat_still_runs_in_the_chat_workspace(self):
        started = chat.send("", "hello", "")
        self.wait_idle(started["id"])
        self.assertEqual(self.started_in[0], str(chat.workspace()))

    def test_about_reads_the_folder_and_session_from_the_note_only(self):
        folder = tempfile.mkdtemp(dir=self.tmp.name).replace("\\", "/")

        def session_note(**meta):
            fields = {"title": "Fix the relay", "type": "session", "source": "opencode", "external": "ses_abc", "folder": folder}
            fields.update(meta)
            head = "\n".join(f"{key}: {value}" for key, value in fields.items() if value is not None)
            return f"---\n{head}\n---\n\n## Log\n"

        class FakeBrain:
            notes = {
                "sessions/ok.md": session_note(),
                "sessions/vector.md": session_note(source=None, external=None),
                "sessions/batch.md": session_note(external="batch-2026-11-14"),
                "sessions/odd.md": session_note(external="ses abc; rm"),
                "sessions/gone.md": session_note(folder=folder + "/missing"),
                "notes/idea.md": session_note(),
            }

            def read(self, path):
                if path not in self.notes:
                    raise FileNotFoundError(path)
                return {"path": path, "text": self.notes[path], "mtime": 1}

        brain = FakeBrain()
        self.assertEqual(chat.about(brain, "sessions/ok.md", "continue"), {"session": "ses_abc", "folder": folder, "title": "Fix the relay"})
        self.assertEqual(chat.about(brain, "sessions/ok.md", "new"), {"session": "", "folder": folder, "title": ""})
        self.assertEqual(chat.about(brain, "sessions/vector.md", "new")["folder"], folder)
        refused = [
            ("sessions/vector.md", "continue", "only continue OpenCode"),
            ("sessions/batch.md", "continue", "only continue OpenCode"),
            ("sessions/odd.md", "continue", "only continue OpenCode"),
            ("sessions/gone.md", "new", "folder is not on this PC"),
            ("sessions/nope.md", "continue", "not on this PC"),
            ("notes/idea.md", "continue", "not a session"),
            ("../sessions/ok.md", "new", "not a session"),
            ("sessions/../notes/idea.md", "continue", "not a session"),
        ]
        for path, mode, reason in refused:
            with self.subTest(path=path, mode=mode), self.assertRaisesRegex(ValueError, reason):
                chat.about(brain, path, mode)
        with self.assertRaisesRegex(ValueError, "not a session"):
            chat.about(None, "sessions/ok.md", "continue")


class ModelListTests(unittest.TestCase):
    """The chat page never waits for `opencode models`, which can take a minute on a busy PC."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs = []
        self.answer = threading.Event()  # set when the fake `opencode models` may finish
        self.output = "pegaai/Pega-Chat\nloading plugins\n"

        def fake_run(command, **kwargs):
            self.runs.append(command)
            self.answer.wait(5)
            if self.output is None:
                raise subprocess.TimeoutExpired(command, 60)
            return mock.Mock(stdout=self.output)

        for patcher in (
            mock.patch.dict(os.environ, {"CAIRN_HOME": self.tmp.name}),
            mock.patch.dict(chat._models_cache, {"at": 0.0, "models": []}),
            mock.patch.object(chat, "find_opencode", return_value="opencode"),
            mock.patch.object(chat.subprocess, "run", side_effect=fake_run),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def wait_for(self, condition, what):
        for _ in range(250):
            if condition():
                return
            time.sleep(0.02)
        self.fail(what)

    def test_summary_answers_at_once_and_the_list_arrives_later(self):
        started = time.monotonic()
        self.assertEqual(chat.summary()["models"], [])
        self.assertLess(time.monotonic() - started, 2, "it must not wait for opencode")
        self.wait_for(lambda: self.runs, "the list is never asked for")
        chat.summary()
        chat.summary()
        self.assertEqual(len(self.runs), 1, "one run at a time, however often the page asks")
        self.answer.set()
        self.wait_for(lambda: chat.summary()["models"] == ["pegaai/Pega-Chat"], "the list never arrived")
        self.assertEqual(len(self.runs), 1, "and a fresh list is not asked for again")

    def test_a_failing_opencode_is_not_run_again_on_every_request(self):
        self.output = None
        self.answer.set()
        chat.summary()
        self.wait_for(lambda: chat._models_cache["at"] and not chat._models_refresh.locked(), "the run never ended")
        self.assertEqual(chat.summary()["models"], [])
        time.sleep(0.1)
        self.assertEqual(len(self.runs), 1)

    def test_a_failed_refresh_keeps_the_last_good_list(self):
        chat._models_cache.update(at=0.0, models=["pegaai/Pega-Chat"])
        self.output = None
        self.answer.set()
        self.assertEqual(chat.models(force=True), ["pegaai/Pega-Chat"])
        self.assertEqual(len(self.runs), 1)


if __name__ == "__main__":
    unittest.main()

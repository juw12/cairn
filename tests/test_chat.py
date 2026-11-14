"""Chat with the OpenCode models: storage, sending, reply streaming and session reuse (no real OpenCode)."""

import io
import json
import os
import tempfile
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

    def test_summary_lists_chats_newest_first(self):
        a = chat.send("", "first", "")
        self.wait_idle(a["id"])
        b = chat.send("", "second", "")
        self.wait_idle(b["id"])
        listed = chat.summary()
        self.assertTrue(listed["available"])
        self.assertEqual([c["title"] for c in listed["chats"]], ["second", "first"])

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


if __name__ == "__main__":
    unittest.main()

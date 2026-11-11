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

        def fake_popen(command, **kwargs):
            self.commands.append(command)
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


if __name__ == "__main__":
    unittest.main()

"""Chat from anywhere through a private GitHub repo (cairn/relay.py), against an in-memory GitHub."""

import base64
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
import urllib.parse
from contextlib import redirect_stdout
from unittest import mock

from cairn import chat, relay
from cairn.cli import main


class FakeProcess:
    """Stands in for `opencode run --format json`: one session id and one reply."""

    def __init__(self, command, **kwargs):
        events = [
            {"type": "text", "sessionID": "ses_relay", "part": {"id": "p1", "type": "text", "text": "Hi from the PC"}},
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


class FakeGitHub(relay.GitHub):
    """The Contents API rules the relay depends on: shas, 409/422 on stale writes, ETags on folders."""

    def __init__(self):
        super().__init__("me/cairn-relay", "token")
        self.files: dict[str, tuple[str, str]] = {}
        self.calls: list[tuple[str, str]] = []

    def put_file(self, path, text):
        self.files[path] = (text, hashlib.sha1(f"{path}:{text}:{time.time_ns()}".encode()).hexdigest())

    def text(self, path):
        return self.files[path][0] if path in self.files else None

    def call(self, method, path, body=None, etag=""):
        self.calls.append((method, path))
        prefix = f"/repos/{self.repo}/contents/"
        assert path.startswith(prefix), path
        name = urllib.parse.unquote(path[len(prefix):])
        if method == "GET":
            if name in self.files:
                text, sha = self.files[name]
                return 200, {"sha": sha, "content": base64.b64encode(text.encode()).decode()}, {}
            children = sorted(p for p in self.files if p.startswith(name + "/") and "/" not in p[len(name) + 1:])
            if not children:
                return 404, {"message": "Not Found"}, {}
            listing = [{"type": "file", "name": p.rsplit("/", 1)[1], "path": p, "sha": self.files[p][1]} for p in children]
            tag = '"' + hashlib.sha1(json.dumps(listing).encode()).hexdigest() + '"'
            if etag == tag:
                return 304, None, {"ETag": tag}
            return 200, listing, {"ETag": tag}
        current = self.files.get(name)
        if method == "PUT":
            if current and body.get("sha") != current[1]:
                return (409 if body.get("sha") else 422), {"message": "sha"}, {}
            if not current and body.get("sha"):
                return 422, {"message": "sha"}, {}
            self.put_file(name, base64.b64decode(body["content"]).decode())
            return (200 if current else 201), {"content": {"sha": self.files[name][1]}}, {}
        if method == "DELETE":
            if not current:
                return 404, {"message": "Not Found"}, {}
            if body.get("sha") != current[1]:
                return 409, {"message": "sha"}, {}
            del self.files[name]
            return 200, {}, {}
        raise AssertionError(method)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CAIRN_HOME": self.tmp.name})
        self.env.start()
        self.patches = [
            mock.patch.object(chat, "find_opencode", return_value="opencode"),
            mock.patch.object(chat.subprocess, "Popen", side_effect=lambda command, **kw: FakeProcess(command)),
            mock.patch.object(chat, "models", return_value=["pegaai/Pega-Chat"]),
        ]
        for patch in self.patches:
            patch.start()
        self.gh = FakeGitHub()
        self.gh.put_file("requests/.keep", "")
        self.relay = relay.Relay(self.gh)

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def phone_sends(self, request_id, **request):
        request.setdefault("at", time.time() * 1000)
        self.gh.put_file(f"requests/{request_id}.json", json.dumps(request))

    def wait_idle(self, chat_id):
        for _ in range(200):
            loaded = chat.load(chat_id)
            if loaded and loaded["status"] == "idle":
                return loaded
            time.sleep(0.01)
        self.fail("chat never finished")

    def test_message_from_phone_is_answered_in_the_repo(self):
        self.phone_sends("1700000000000-aa01", action="send", thread="20261111-abcd1234", text="hello", model="pegaai/Pega-Chat")
        self.relay.step()
        self.assertIsNone(self.gh.text("requests/1700000000000-aa01.json"), "request is removed once taken")
        self.wait_idle("20261111-abcd1234")
        self.relay.step()
        thread = json.loads(self.gh.text("threads/20261111-abcd1234.json"))
        self.assertEqual(thread["id"], "20261111-abcd1234", "the phone's chat id is kept")
        self.assertEqual(thread["request"], "1700000000000-aa01")
        self.assertEqual(thread["status"], "idle")
        self.assertEqual([m["role"] for m in thread["messages"]], ["user", "assistant"])
        self.assertEqual(thread["messages"][1]["text"], "Hi from the PC")
        index = json.loads(self.gh.text("index.json"))
        self.assertEqual(index["models"], ["pegaai/Pega-Chat"])
        self.assertEqual([c["id"] for c in index["chats"]], ["20261111-abcd1234"])
        self.assertGreater(index["pcSeen"], 0)

    def test_each_pc_answers_only_its_own_folder(self):
        work = relay.Relay(self.gh, device={"id": "work-pc-1a2b", "name": "Work PC"})
        home = relay.Relay(self.gh, device={"id": "home-pc-3c4d", "name": "Home PC"})
        self.gh.put_file(
            "devices/work-pc-1a2b/requests/1700000000009-aa09.json",
            json.dumps({"action": "send", "thread": "20261112-00000009", "text": "hi work", "at": time.time() * 1000}),
        )
        home.step()
        self.assertIsNotNone(self.gh.text("devices/work-pc-1a2b/requests/1700000000009-aa09.json"),
                             "another PC leaves the request alone")
        work.step()
        self.assertIsNone(self.gh.text("devices/work-pc-1a2b/requests/1700000000009-aa09.json"))
        self.wait_idle("20261112-00000009")
        work.step()
        thread = json.loads(self.gh.text("devices/work-pc-1a2b/threads/20261112-00000009.json"))
        self.assertEqual(thread["messages"][1]["text"], "Hi from the PC")
        work_index = json.loads(self.gh.text("devices/work-pc-1a2b/index.json"))
        home_index = json.loads(self.gh.text("devices/home-pc-3c4d/index.json"))
        self.assertEqual(work_index["device"], {"id": "work-pc-1a2b", "name": "Work PC"})
        self.assertEqual(home_index["device"]["name"], "Home PC")
        self.assertIsNone(self.gh.text("index.json"), "devices don't overwrite a shared index")

    def test_device_id_is_stable_and_saved(self):
        config = {"repo": "me/cairn-relay"}
        first = relay.device_of(config)
        self.assertRegex(first["id"], relay.DEVICE_ID_RE)
        self.assertEqual(relay.device_of(relay.load_config()), first, "saved in relay.json and reused")

    def test_quiet_polls_cost_one_cheap_request(self):
        self.relay.step()
        before = len(self.gh.calls)
        self.relay.step()
        self.assertEqual(self.gh.calls[before:], [("GET", "/repos/me/cairn-relay/contents/requests")])

    def test_failure_is_reported_back_to_the_phone(self):
        with mock.patch.object(chat, "find_opencode", return_value=""):
            self.phone_sends("1700000000001-aa02", action="send", thread="20261111-00000001", text="anyone there?")
            self.relay.step()
        thread = json.loads(self.gh.text("threads/20261111-00000001.json"))
        self.assertEqual(thread["request"], "1700000000001-aa02")
        self.assertIn("not installed", thread["error"])
        self.assertEqual(thread["messages"][0]["text"], "anyone there?")
        self.assertIn("Couldn't run that", thread["messages"][1]["text"])

    def test_delete_removes_the_chat_everywhere(self):
        self.phone_sends("1700000000002-aa03", action="send", thread="20261111-00000002", text="hello")
        self.relay.step()
        self.wait_idle("20261111-00000002")
        self.relay.step()
        self.assertIsNotNone(self.gh.text("threads/20261111-00000002.json"))
        self.phone_sends("1700000000003-aa04", action="delete", thread="20261111-00000002")
        self.relay.step()
        self.assertIsNone(chat.load("20261111-00000002"))
        self.assertIsNone(self.gh.text("threads/20261111-00000002.json"))
        self.assertEqual(json.loads(self.gh.text("index.json"))["chats"], [])

    def test_old_and_malformed_requests_are_dropped_not_run(self):
        self.phone_sends("1600000000000-aa05", action="send", thread="20201111-00000003", text="old", at=1600000000000)
        self.phone_sends("1700000000004-aa06", action="send", thread="../../evil", text="nope")
        self.relay.step()
        self.assertEqual([p for p in self.gh.files if p.startswith("requests/") and p != "requests/.keep"], [])
        self.assertEqual(chat.summary()["chats"], [])

    def test_stale_sha_is_refreshed_and_the_write_retried(self):
        self.gh.write("index.json", "{}", "first")
        self.gh.put_file("index.json", '{"someone": "else"}')  # changed behind the worker's back
        self.gh.write("index.json", '{"ok": 1}', "second")
        self.assertEqual(json.loads(self.gh.text("index.json")), {"ok": 1})

    def test_missing_requests_folder_is_recreated(self):
        del self.gh.files["requests/.keep"]
        self.relay.poll_requests()
        self.assertEqual(self.gh.text("requests/.keep"), "")


class ChatIdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CAIRN_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_send_rejects_a_bad_new_chat_id(self):
        with mock.patch.object(chat, "find_opencode", return_value="opencode"):
            with self.assertRaises(ValueError):
                chat.send("../x", "hi")

    def test_saved_token_is_sealed_and_preferred(self):
        sealed = relay.seal_token("ghp_saved_token_example")
        self.assertNotIn("ghp_saved_token_example", sealed)
        relay.save_config({"repo": "me/cairn-relay", "proxy": "", "token": sealed})
        with mock.patch.dict(os.environ, {"CAIRN_GITHUB_TOKEN": "", "GITHUB_TOKEN": "", "GH_TOKEN": ""}):
            self.assertEqual(relay.get_token(), "ghp_saved_token_example")

    def test_no_background_worker_without_setup(self):
        import threading

        self.assertIsNone(relay.start_in_background(threading.Event(), log=lambda *_: None))

    def test_config_round_trip_and_cli_status(self):
        self.assertIsNone(relay.load_config())
        relay.save_config({"repo": "me/cairn-relay", "proxy": ""})
        self.assertEqual(relay.load_config()["repo"], "me/cairn-relay")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["relay", "status"]), 0)
        self.assertIn("me/cairn-relay", out.getvalue())


if __name__ == "__main__":
    unittest.main()

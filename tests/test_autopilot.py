import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cairn.autopilot import Autopilot, License, PlanError, parse_reply
from cairn.pro import generate_keypair, issue_key
from cairn.brain import Brain
from cairn.server import make_server


class FakeEngine:
    """Stands in for OpenCode: replays scripted replies, one per round."""

    name = "Fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def available(self):
        return True

    def stop(self):
        pass

    def run(self, prompt, session_id, cwd, on_line):
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if self.replies else "STATUS: DONE\nNEXT: nothing"
        on_line(reply)
        return reply, session_id or "ses_fake", ""


TEST_SELLER = generate_keypair(1024)  # a throwaway seller key; small is fine for tests
TEST_PUBLIC = {"n": TEST_SELLER["n"], "e": TEST_SELLER["e"]}
GOOD_KEY = issue_key("buyer@example.com", "lifetime", TEST_SELLER)


def test_license(home):
    return License(home, public_key=TEST_PUBLIC, paypal_client_id="")


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.brain = Brain(self.tmp / "project")
        self.brain.root.mkdir(parents=True)
        self.brain.init()
        self.brain.create("decision", "Use SQLite for reports", body="We store reports in SQLite.")
        self.license = test_license(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pilot(self, replies, **kwargs):
        return Autopilot(self.brain, engine=FakeEngine(replies), license=self.license, home=self.home, **kwargs)

    def test_parse_reply_takes_last_status(self):
        status, detail, hint = parse_reply("STATUS: CONTINUE\nmore\nSTATUS: BLOCKED needs a password\nNEXT: log in")
        self.assertEqual((status, detail, hint), ("BLOCKED", "needs a password", "log in"))

    def test_runs_to_done_and_logs_into_task_note(self):
        pilot = self.pilot(["planned\nSTATUS: CONTINUE\nNEXT: write code", "built it\nSTATUS: DONE\nNEXT: polish"], improve_rounds=0)
        job = pilot.start("Add SQLite reports export")
        self.assertTrue(wait_for(lambda: pilot.get(job["id"])["status"] == "done"))
        finished = pilot.get(job["id"])
        self.assertEqual(finished["rounds"], 2)
        note = self.brain.read(job["note"])["text"]
        self.assertIn("status: done", note)
        self.assertIn("autopilot", note)
        self.assertIn("Done after 2 rounds", note)
        first_prompt = pilot.engine.prompts[0]
        self.assertIn("Use SQLite for reports", first_prompt)  # precedent from the brain
        self.assertIn("STATUS:", first_prompt)

    def test_blocked_waits_for_a_comment_then_resumes(self):
        pilot = self.pilot(["STATUS: BLOCKED need the Stripe key", "STATUS: DONE\nNEXT: -"], improve_rounds=0)
        job = pilot.start("Wire up payments")
        self.assertTrue(wait_for(lambda: pilot.get(job["id"])["status"] == "blocked"))
        self.assertIn("Stripe", pilot.get(job["id"])["reason"])
        pilot.comment(job["id"], "use test mode for now")
        self.assertTrue(wait_for(lambda: pilot.get(job["id"])["status"] == "done"))
        self.assertIn("use test mode for now", pilot.engine.prompts[1])

    def test_trial_then_paywall_then_license(self):
        pilot = self.pilot(["STATUS: DONE\nNEXT: -"] * 4, improve_rounds=0)
        first = pilot.start("first goal")
        self.assertEqual(first["plan"], "trial")
        self.assertTrue(wait_for(lambda: pilot.get(first["id"])["status"] == "done"))
        with self.assertRaises(PlanError):
            pilot.start("second goal")
        with self.assertRaises(ValueError):
            self.license.activate("BAD-KEY")
        status = self.license.activate(GOOD_KEY)
        self.assertTrue(status["pro"])
        self.assertEqual(pilot.start("second goal")["plan"], "pro")
        self.assertFalse(self.license.deactivate()["pro"])

    def test_engine_failures_block_after_three(self):
        class Broken(FakeEngine):
            def run(self, prompt, session_id, cwd, on_line):
                return "", session_id, "engine exited with code 1"

        import cairn.autopilot as module
        original = module.time.sleep
        pilot = Autopilot(self.brain, engine=Broken([]), license=self.license, home=self.home)
        job = pilot.start("anything")
        try:
            self.assertTrue(wait_for(lambda: pilot.get(job["id"])["status"] == "blocked"))
        finally:
            module.time.sleep = original
        self.assertIn("keeps failing", pilot.get(job["id"])["reason"])


class AutopilotApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.brain = Brain(self.tmp)
        self.brain.init()
        license = test_license(self.tmp / "home")
        license.use_trial()  # trial already spent
        pilot = Autopilot(self.brain, engine=FakeEngine([]), license=license, home=self.tmp / "home")
        self.server = make_server(self.brain, port=0, autopilot=pilot)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"X-Cairn": "1", "Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def test_state_paywall_and_activation(self):
        status, data = self.call("GET", "/api/autopilot")
        self.assertEqual(status, 200)
        self.assertEqual(data["license"]["plan"], "free")
        status, data = self.call("POST", "/api/autopilot/start", {"goal": "do a thing"})
        self.assertEqual(status, 402)
        status, data = self.call("POST", "/api/autopilot/license", {"key": "BAD-KEY"})
        self.assertEqual(status, 400)
        status, data = self.call("POST", "/api/autopilot/license", {"key": GOOD_KEY})
        self.assertEqual(status, 200)
        self.assertTrue(data["pro"])
        status, job = self.call("POST", "/api/autopilot/start", {"goal": "do a thing"})
        self.assertEqual(status, 200)
        status, log = self.call("GET", f"/api/autopilot/log?id={job['id']}&since=0")
        self.assertEqual(status, 200)
        self.assertIn("do a thing", log["text"])

    def test_requires_cairn_header(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/autopilot")
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()


if __name__ == "__main__":
    unittest.main()

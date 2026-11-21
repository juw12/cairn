"""Cairn Pro: one free autopilot goal, then a Polar licence key. A small local server stands in for
Polar, so nothing here touches the network and no file leaves the temp folder (CAIRN_HOME)."""

import http.client
import http.server
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

from cairn import autopilot, precedent, pro, widget
from cairn.brain import Brain
from cairn.server import make_server

ROOT = Path(__file__).resolve().parent.parent
GOOD_KEY = "CAIRN-2F6A9C1E-77B0-4D1B-9A55-0C3E8F1D4321"
HOUR = 3600
DAY = 24 * HOUR
T0 = 1_800_000_000.0


class FakePolar(http.server.BaseHTTPRequestHandler):
    """Answers like api.polar.sh: 200 with the licence, 404 ResourceNotFound for a key it does not know."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self.server.asked.append(body)
        licence = self.server.keys.get(body.get("key"))
        if self.server.broken:
            return self.answer(502, b"<html>Bad gateway</html>", "text/html")
        if licence is None or body.get("organization_id") != pro.ORGANIZATION_ID:
            return self.answer(404, json.dumps({"error": "ResourceNotFound", "detail": "Not found"}).encode())
        payload = {"status": "granted", "expires_at": None, "customer": {"email": "buyer@example.com", "name": "A Buyer"}}
        payload.update(licence)
        return self.answer(200, json.dumps(payload).encode())

    def answer(self, code, data, kind="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class ProTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.polar = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakePolar)
        self.polar.keys = {GOOD_KEY: {}}
        self.polar.asked = []
        self.polar.broken = False
        threading.Thread(target=self.polar.serve_forever, daemon=True).start()
        self.polar_url = f"http://127.0.0.1:{self.polar.server_address[1]}/validate"
        patches = [
            mock.patch.dict(os.environ, {"CAIRN_HOME": str(self.tmp / "home")}),
            mock.patch.object(pro, "VALIDATE_URL", self.polar_url),
            mock.patch.object(widget, "proxy_url", return_value=""),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.brain = Brain(self.project)
        self.brain.init()

    def tearDown(self):
        self.polar.shutdown()
        self.polar.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class LicenceTests(ProTestCase):
    def test_the_first_goal_is_free_and_the_second_needs_a_key(self):
        self.assertEqual(pro.status()["trial_left"], True)
        autopilot.new_goal("Ship the page", str(self.tmp / "w"))
        self.assertEqual((pro.status()["trial_left"], pro.status()["active"]), (False, False))
        with self.assertRaises(pro.ProError) as blocked:
            autopilot.new_goal("Write the notes", str(self.tmp / "w2"))
        self.assertIn(pro.CHECKOUT_URL, str(blocked.exception))
        self.assertEqual([job["goal"] for job in autopilot.jobs()], ["Ship the page"])
        self.assertEqual(self.polar.asked, [], "nobody is asked about a key that was never entered")

    def test_a_goal_that_was_refused_for_being_empty_does_not_use_the_free_one(self):
        with self.assertRaises(ValueError):
            autopilot.new_goal("   ")
        self.assertTrue(pro.status()["trial_left"])

    def test_a_good_key_turns_pro_on_and_keeps_nothing_about_the_buyer(self):
        autopilot.new_goal("Ship the page", str(self.tmp / "w"))
        state = pro.activate(f"  {GOOD_KEY}\n")
        self.assertEqual((state["active"], state["key"]), (True, "...4321"))
        self.assertNotIn(GOOD_KEY, json.dumps(pro.status()), "the page never gets the whole key back")
        autopilot.new_goal("Write the notes", str(self.tmp / "w2"))
        autopilot.new_goal("Third one", str(self.tmp / "w3"))
        self.assertEqual(len(autopilot.jobs()), 3)
        saved = pro.path().read_text(encoding="utf-8")
        self.assertNotIn("buyer@example.com", saved)
        self.assertNotIn("A Buyer", saved)

    def test_a_key_polar_does_not_know_is_refused(self):
        with self.assertRaises(pro.ProError) as refused:
            pro.activate("CAIRN-NOT-A-REAL-KEY")
        self.assertNotIsInstance(refused.exception, pro.Unreachable)
        self.assertFalse(pro.status()["active"])
        with self.assertRaises(pro.ProError):
            pro.activate("")

    def test_a_key_of_another_state_or_already_ended_is_refused(self):
        self.polar.keys = {"CAIRN-REVOKED": {"status": "revoked"}, "CAIRN-ENDED": {"expires_at": "2020-01-01T00:00:00Z"}}
        for key in ("CAIRN-REVOKED", "CAIRN-ENDED"):
            with self.subTest(key=key), self.assertRaises(pro.ProError):
                pro.activate(key)
        self.assertFalse(pro.status()["active"])

    def test_a_proxy_error_page_is_not_read_as_a_bad_key(self):
        self.polar.broken = True
        with self.assertRaises(pro.Unreachable):
            pro.activate(GOOD_KEY)
        self.polar.broken = False
        pro.activate(GOOD_KEY, now=T0)
        self.polar.broken = True
        pro.refresh(now=T0 + 2 * DAY, force=True)
        self.assertTrue(pro.is_active(T0 + 2 * DAY), "no answer from Polar leaves the last good answer in use")

    def test_away_from_the_network_pro_lasts_fourteen_days(self):
        autopilot.new_goal("Ship the page", str(self.tmp / "w"))
        pro.activate(GOOD_KEY, now=T0)
        self.polar.broken = True
        self.assertTrue(pro.allowed(T0 + 13 * DAY))
        self.assertFalse(pro.allowed(T0 + 15 * DAY))
        self.polar.broken = False
        self.assertTrue(pro.allowed(T0 + 15 * DAY + 2 * HOUR), "back online, the next check turns it on again")

    def test_polar_is_asked_once_a_day_and_an_ended_subscription_turns_pro_off(self):
        autopilot.new_goal("Ship the page", str(self.tmp / "w"))
        pro.activate(GOOD_KEY, now=T0)
        asked = len(self.polar.asked)
        self.polar.keys = {}
        self.assertTrue(pro.allowed(T0 + HOUR))
        self.assertEqual(len(self.polar.asked), asked, "inside a day the saved answer is used")
        self.assertFalse(pro.allowed(T0 + 25 * HOUR))
        self.assertEqual(len(self.polar.asked), asked + 1)
        self.assertFalse(pro.status(T0 + 25 * HOUR)["active"])

    def test_while_polar_is_down_it_is_tried_once_an_hour_not_on_every_goal(self):
        pro.activate(GOOD_KEY, now=T0)
        self.polar.broken = True
        asked = len(self.polar.asked)
        for minutes in (0, 5, 30):
            pro.allowed(T0 + 2 * DAY + minutes * 60)
        self.assertEqual(len(self.polar.asked), asked + 1)

    def test_removing_the_key_turns_pro_off_but_does_not_hand_back_the_free_goal(self):
        autopilot.new_goal("Ship the page", str(self.tmp / "w"))
        pro.activate(GOOD_KEY)
        state = pro.remove()
        self.assertEqual((state["active"], state["key"], state["trial_left"]), (False, "", False))
        self.assertNotIn(GOOD_KEY, pro.path().read_text(encoding="utf-8"))


class WorkerTests(ProTestCase):
    def test_the_queue_is_left_alone_until_there_is_a_key(self):
        worker = autopilot.Autopilot(self.brain, engine=None, notifier=lambda title, text: None)
        precedent.queue_add(self.brain, "Add CSV export #auto")
        precedent.queue_add(self.brain, "Add PDF export #auto")
        first = worker.pull_from_queue()
        self.assertEqual(first["goal"], "Add CSV export")
        self.assertIsNone(worker.pull_from_queue(), "the free goal is used: the second queue item waits")
        self.assertEqual(len(autopilot.jobs()), 1)
        pro.activate(GOOD_KEY)
        self.assertEqual(worker.pull_from_queue()["goal"], "Add PDF export")

    def test_a_goal_already_handed_over_keeps_running_without_a_key(self):
        worker = autopilot.Autopilot(self.brain, engine=None, notifier=lambda title, text: None)
        job = autopilot.new_goal("Ship the page", str(self.tmp / "w"))
        self.assertFalse(pro.allowed())
        self.assertEqual(worker.pick()["id"], job["id"])


class ServerTests(ProTestCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.brain, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers={"X-Cairn": "1", "Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def test_the_page_can_see_the_state_enter_a_key_and_remove_it(self):
        status, state = self.call("GET", "/api/autopilot")
        self.assertEqual((state["pro"]["active"], state["pro"]["trial_left"], state["pro"]["checkout"]), (False, True, pro.CHECKOUT_URL))
        self.assertEqual(self.call("POST", "/api/autopilot/goal", {"goal": "Ship it", "workdir": str(self.tmp / "w")})[0], 200)
        status, refused = self.call("POST", "/api/autopilot/goal", {"goal": "And this", "workdir": str(self.tmp / "w2")})
        self.assertEqual(status, 400)
        self.assertIn("Cairn Pro", json.dumps(refused))
        status, refused = self.call("POST", "/api/autopilot/pro-activate", {"key": "CAIRN-WRONG"})
        self.assertEqual(status, 400)
        status, state = self.call("POST", "/api/autopilot/pro-activate", {"key": GOOD_KEY})
        self.assertEqual((status, state["active"], state["key"]), (200, True, "...4321"))
        self.assertEqual(self.call("POST", "/api/autopilot/goal", {"goal": "And this", "workdir": str(self.tmp / "w2")})[0], 200)
        status, state = self.call("POST", "/api/autopilot/pro-remove", {})
        self.assertEqual((status, state["active"]), (200, False))
        self.assertEqual(self.call("POST", "/api/autopilot/goal", {"goal": "One more", "workdir": str(self.tmp / "w3")})[0], 400)


class CliTests(ProTestCase):
    def cairn(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, "-m", "cairn", "--root", str(self.project), *args], capture_output=True, text=True, env=env, timeout=60, encoding="utf-8")

    def test_status_and_the_second_goal_from_the_command_line(self):
        self.assertIn("first autopilot goal is free", self.cairn("pro").stdout)
        self.assertEqual(self.cairn("autopilot", "add", "Ship the page").returncode, 0)
        second = self.cairn("autopilot", "add", "Write the notes")
        self.assertEqual(second.returncode, 1)
        self.assertIn(pro.CHECKOUT_URL, second.stderr)
        self.assertIn("free goal is used", self.cairn("pro", "status").stdout)
        self.assertEqual(self.cairn("pro", "remove").returncode, 0)


if __name__ == "__main__":
    unittest.main()

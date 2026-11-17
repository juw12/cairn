"""cairn telegram: the daily status text, handing it to the Worker, and the Worker's bot itself."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cairn import telegram, watch
from cairn.brain import Brain

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

REQUIRE = os.environ.get("CAIRN_REQUIRE_BROWSER") == "1"
WORKER = Path(__file__).resolve().parents[1] / "worker" / "cairn-telegram.js"
KEY = "k" * 24
TOKEN = "123456789:" + "A" * 35
APP = {"version": "1.0.0", "build": "5ba5fd8aaaa", "running": True, "update": "", "autoSync": True,
       "vibeWatch": True, "phoneAccess": True, "phones": 2, "phoneError": ""}


def session(title, status, started, lines):
    meta = [f"title: {title}", "type: session", f"status: {status}", f"started: {started}"]
    return "---\n" + "\n".join(meta) + "\n---\n## Goal\n\n" + title + "\n\n## Log\n\n" + "\n".join(lines) + "\n"


class FakeWorker:
    """Stands in for the Worker: records each call and answers from a table."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self.answers.get(path, (200, {"kept": True, "sent": True, "bot": "cairn_status_bot"}))


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.root = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CAIRN_HOME": self.home.name})
        self.env.start()
        self.brain = Brain(self.root.name, None)
        for folder in ("sessions", "tasks", "github-usage", "machines"):
            (self.brain.dir / folder).mkdir(parents=True)
        self.write("sessions/2026-11-13-a.md", session(
            "Add dark mode to the settings page", "live", "2026-11-13T09:00",
            ["- 09:00 start: Add dark mode", "- 09:20 pass: pytest (12s) · 40 passed"],
        ))
        self.write("sessions/2026-11-13-b.md", session(
            "Fix the GPX import", "shipped", "2026-11-13T07:00",
            ["- 07:00 start: Fix", "- 07:40 fail: secret log text", "- 08:10 end: shipped"],
        ))
        self.write("sessions/2026-11-12-c.md", session("Yesterday's work", "shipped", "2026-11-12T07:00", ["- 07:00 start: x"]))
        self.write("tasks/ship.md", "---\ntitle: Ship it\ntype: task\nstatus: todo\n---\nprivate task body\n")
        self.write("tasks/doing.md", "---\ntitle: Doing\ntype: task\nstatus: doing\n---\n")
        self.write("tasks/done.md", "---\ntitle: Done\ntype: task\nstatus: done\n---\n")
        self.now = dt.datetime(2026, 11, 13, 10, 0)

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()
        self.root.cleanup()

    def write(self, relative, text):
        (self.brain.dir / relative).write_text(text, encoding="utf-8")

    def text(self, **app):
        return telegram.status_text(self.brain, {**APP, **app}, self.now)

    def test_the_day_and_the_health_are_both_there(self):
        text = self.text()
        self.assertIn("Fri 13 Nov", text.splitlines()[0])
        self.assertIn("2 AI sessions: 1 shipped, 1 still live", text)
        self.assertIn("checks: 1 passed, 1 failed", text)
        self.assertIn("Live now: Add dark mode to the settings page", text)
        self.assertIn("Tasks: 1 to do, 1 in progress", text)
        self.assertIn("Desktop app 1.0.0 (5ba5fd8): running", text)
        self.assertIn("Phone access: on, 2 phones paired", text)
        self.assertNotIn("Yesterday's work", text)

    def test_only_titles_and_counts_leave_the_pc(self):
        text = self.text()
        for private in ("secret log text", "pytest", "private task body", "Ship it", self.root.name):
            self.assertNotIn(private, text)

    def test_the_text_does_not_change_with_the_minute(self):
        self.assertEqual(self.text(), telegram.status_text(self.brain, APP, self.now + dt.timedelta(minutes=7)))
        self.assertNotRegex(self.text(), r"\b\d{1,2}:\d{2}\b")  # no clock times: the Worker adds how old it is

    def test_problems_lead_the_message(self):
        self.assertTrue(self.text().splitlines()[1].startswith("Needs a look: 1 check failed"))
        self.write("sessions/2026-11-13-b.md", session("Fix the GPX import", "shipped", "2026-11-13T07:00", ["- 07:00 start: Fix"]))
        self.assertEqual(self.text().splitlines()[1], "All good.")
        self.assertIn("an update is waiting for Cairn to restart", self.text(update="restart").splitlines()[1])
        self.assertIn("the desktop app is not running", self.text(running=False).splitlines()[1])

    def test_a_stopped_watcher_is_a_problem_but_one_switched_off_is_not(self):
        claim = watch._claim_path(self.brain)
        claim.write_text(json.dumps({"owner": "app:1", "at": self.now.timestamp() - 30}), encoding="utf-8")
        self.assertIn("Session watcher: running", self.text())
        claim.write_text(json.dumps({"owner": "app:1", "at": self.now.timestamp() - 3600}), encoding="utf-8")
        self.assertIn("the session watcher stopped", self.text().splitlines()[1])
        self.assertIn("Session watcher: stopped, last pass 13 Nov", self.text())
        off = self.text(vibeWatch=False)
        self.assertIn("Session watcher: switched off in Settings", off)
        self.assertNotIn("watcher stopped", off.splitlines()[1])

    def test_github_minutes_and_pc_load_come_from_the_watcher_notes(self):
        self.write("github-usage/2026-11.md", "---\ntitle: GitHub Actions 2026-11\ntype: note\ngh_used: \"1830\"\n"
                   "gh_limit: \"2000\"\ngh_pct: \"92\"\ngh_level: critical\ngh_resets: 2026-12-01\n---\n")
        self.write("machines/laptop.md", "---\ntitle: Laptop\ntype: note\nmachine_at: 2026-11-13T09:58\nload: medium\n"
                   "load_why: CPU 63% busy\n---\n")
        text = self.text()
        self.assertIn("GitHub Actions: 1,830 of 2,000 free minutes (92%), starts again 2026-12-01", text)
        self.assertIn("GitHub Actions minutes are nearly used up", text.splitlines()[1])
        self.assertIn("PC load: medium (CPU 63% busy)", text)

    def test_a_quiet_day_says_so(self):
        text = telegram.status_text(self.brain, APP, dt.datetime(2026, 11, 20, 9, 0))
        self.assertIn("No AI sessions today.", text)
        self.assertIn("Live now: Add dark mode", text)  # still live from an earlier day


class HandOverTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.root = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CAIRN_HOME": self.home.name})
        self.env.start()
        self.brain = Brain(self.root.name, None)
        self.brain.dir.mkdir(parents=True, exist_ok=True)
        self.app = mock.patch.object(telegram, "app_facts", return_value=dict(APP))
        self.app.start()

    def tearDown(self):
        self.app.stop()
        self.env.stop()
        self.home.cleanup()
        self.root.cleanup()

    def connect(self):
        telegram.save_worker("https://cairn-telegram.example.workers.dev/", KEY)

    def test_nothing_is_sent_before_setup(self):
        worker = FakeWorker()
        self.assertEqual(telegram.push(self.brain, call=worker)["why"], "not set up")
        self.assertEqual(worker.calls, [])
        self.assertEqual(telegram.status(call=worker), {"worker": "", "bot": "", "hour": 21})

    def test_save_worker_checks_the_address_and_hides_the_key(self):
        with self.assertRaises(telegram.TelegramError):
            telegram.save_worker("http://plain.example", KEY)
        with self.assertRaises(telegram.TelegramError):
            telegram.save_worker("https://cairn-telegram.example.workers.dev", "short")
        self.connect()
        saved = telegram.config_path().read_text(encoding="utf-8")
        self.assertEqual(telegram.load_config()["worker"], "https://cairn-telegram.example.workers.dev")
        if os.name == "nt":
            self.assertNotIn(KEY, saved)  # encrypted for this Windows user

    def test_setup_sends_the_token_to_the_worker_and_never_keeps_it(self):
        self.connect()
        worker = FakeWorker()
        with self.assertRaises(telegram.TelegramError):
            telegram.setup("not-a-token", self.brain, call=worker)
        self.assertEqual(worker.calls, [])
        result = telegram.setup(TOKEN, self.brain, hour=7, call=worker)
        method, path, body = worker.calls[0]
        self.assertEqual((method, path, body["token"]), ("POST", "/setup", TOKEN))
        self.assertEqual(result["link"], f"https://t.me/cairn_status_bot?start={body['code']}")
        self.assertGreaterEqual(len(body["code"]), 12)
        self.assertEqual(worker.calls[1][:2], ("PUT", "/status"))  # the first status is ready before the chat is paired
        self.assertEqual(worker.calls[1][2]["hour"], 7)
        for path in (telegram.config_path(), telegram.state_path()):
            self.assertNotIn(TOKEN, path.read_text(encoding="utf-8"))

    def test_setup_explains_a_refused_token(self):
        self.connect()
        worker = FakeWorker({"/setup": (400, {"error": "telegram_refused_token"})})
        with self.assertRaisesRegex(telegram.TelegramError, "Telegram doesn't know that token"):
            telegram.setup(TOKEN, self.brain, call=worker)

    def test_push_only_when_changed_and_at_least_hourly(self):
        self.connect()
        worker = FakeWorker()
        start = time.mktime((2026, 11, 13, 10, 0, 0, 0, 0, -1))
        self.assertTrue(telegram.push(self.brain, now=start, call=worker)["sent"])
        self.assertEqual(telegram.push(self.brain, now=start + 60, call=worker)["why"], "waiting")
        self.assertEqual(telegram.push(self.brain, now=start + 20 * 60, call=worker)["why"], "unchanged")
        (self.brain.dir / "sessions").mkdir()
        (self.brain.dir / "sessions" / "2026-11-13-a.md").write_text(
            session("New work", "live", "2026-11-13T10:05", ["- 10:05 start: x"]), encoding="utf-8")
        self.assertTrue(telegram.push(self.brain, now=start + 21 * 60, call=worker)["sent"])  # it changed
        self.assertEqual(telegram.push(self.brain, now=start + 25 * 60, call=worker)["why"], "waiting")
        self.assertEqual(telegram.push(self.brain, now=start + 50 * 60, call=worker)["why"], "unchanged")
        self.assertTrue(telegram.push(self.brain, now=start + 85 * 60, call=worker)["sent"])  # the hourly sign of life
        self.assertEqual(len(worker.calls), 3)
        body = worker.calls[-1][2]
        self.assertIn("Live now: New work", body["text"])
        self.assertEqual(body["hour"], 21)
        self.assertIsInstance(body["offset"], int)

    def test_a_worker_that_is_down_is_tried_again(self):
        self.connect()

        def down(method, path, body=None):
            raise telegram.TelegramError("Couldn't reach the Worker at x: timed out")

        start = time.mktime((2026, 11, 13, 10, 0, 0, 0, 0, -1))
        self.assertIn("Couldn't reach", telegram.push(self.brain, now=start, call=down)["why"])
        self.assertTrue(telegram.push(self.brain, now=start + 1, call=FakeWorker())["sent"])  # nothing was recorded as sent

    def test_send_now_says_when_no_chat_is_paired(self):
        self.connect()
        worker = FakeWorker({"/send": (409, {"sent": False, "why": "not_paired"})})
        result = telegram.send_now(self.brain, call=worker)
        self.assertFalse(result["sent"])
        self.assertIn("open the link", result["why"])
        self.assertEqual([call[1] for call in worker.calls], ["/status", "/send"])

    def test_off_forgets_the_bot_but_keeps_the_worker(self):
        self.connect()
        worker = FakeWorker()
        telegram.setup(TOKEN, self.brain, call=worker)
        self.assertTrue(telegram.off(call=worker))
        self.assertEqual(worker.calls[-1][:2], ("POST", "/off"))
        self.assertEqual(telegram.load_config().get("bot"), None)
        self.assertTrue(telegram.load_config()["worker"])
        self.assertFalse(telegram.off(call=worker))


HARNESS = """
async ([source, steps, key]) => {
  const mod = await import("data:text/javascript;base64," + source);
  const kept = new Map();
  const env = {
    CAIRN_KEY: key,
    STORE: {
      get: async (name) => (kept.has(name) ? kept.get(name) : null),
      put: async (name, value) => void kept.set(name, value),
      delete: async (name) => void kept.delete(name),
    },
  };
  const results = [];
  for (const step of steps) {
    const sent = [];
    const fakeFetch = async (url, init) => {
      const method = url.split("/").pop();
      sent.push({ url, method, body: JSON.parse(init.body) });
      const refused = step.refuse === method;
      return new Response(JSON.stringify(refused ? { ok: false } : { ok: true, result: { username: "cairn_status_bot" } }));
    };
    if (step.daily) {
      results.push({ result: await mod.daily(env, fakeFetch, step.now), sent });
      continue;
    }
    const request = new Request("https://cairn-telegram.example.workers.dev" + step.path, {
      method: step.method,
      headers: step.headers || {},
      body: step.body === undefined ? undefined : JSON.stringify(step.body),
    });
    const response = await mod.handle(request, step.unset ? {} : env, fakeFetch, step.now);
    results.push({ status: response.status, body: await response.json(), sent });
  }
  return { results, kept: Object.fromEntries(kept) };
}
"""

AUTH = {"Authorization": "Bearer " + KEY}
HOOK = {"X-Telegram-Bot-Api-Secret-Token": hashlib.sha256((KEY + ":hook").encode()).hexdigest()}
CODE = "pair-code-1234"
T0 = 1_794_571_200_000  # 2026-11-13T12:00Z
HOUR = 3_600_000
WIB = 7 * 60  # the PC's clock is UTC+7


def message(chat, text):
    return {"method": "POST", "path": "/hook", "headers": HOOK, "body": {"message": {"chat": {"id": chat}, "text": text}}, "now": T0}


SETUP = {"method": "POST", "path": "/setup", "headers": AUTH, "body": {"token": TOKEN, "code": CODE}, "now": T0}
STATUS = {"method": "PUT", "path": "/status", "headers": AUTH, "body": {"text": "All good.", "hour": 21, "offset": WIB}, "now": T0}
PAIR = message(42, "/start " + CODE)


@unittest.skipIf(sync_playwright is None and not REQUIRE, "playwright not installed")
class WorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, base = start_static_server()  # crypto.subtle needs a secure context: 127.0.0.1 is one
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)
        cls.page = cls.browser.new_page()
        cls.page.goto(base + "manifest.webmanifest")
        cls.source = base64.b64encode(WORKER.read_bytes()).decode("ascii")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def run_steps(self, steps):
        return self.page.evaluate(HARNESS, [self.source, steps, KEY])

    def test_everything_but_the_hook_needs_the_key(self):
        out = self.run_steps([
            {"method": "GET", "path": "/"},
            {"method": "GET", "path": "/state"},
            {"method": "PUT", "path": "/status", "headers": {"Authorization": "Bearer wrong"}, "body": {"text": "x"}},
            {"method": "POST", "path": "/setup", "body": {"token": TOKEN, "code": CODE}},
            {"method": "POST", "path": "/hook", "body": {"message": {"chat": {"id": 1}, "text": "/status"}}},
            {"method": "GET", "path": "/state", "headers": AUTH, "unset": True},
            {"method": "GET", "path": "/nothing", "headers": AUTH},
        ])["results"]
        self.assertEqual([r["status"] for r in out], [200, 403, 403, 403, 403, 500, 404])
        self.assertEqual(sum(len(r["sent"]) for r in out), 0)

    def test_setup_points_telegram_at_the_hook_and_keeps_the_token_to_itself(self):
        out = self.run_steps([SETUP, {"method": "GET", "path": "/state", "headers": AUTH}])
        setup, state = out["results"]
        self.assertEqual(setup["body"], {"bot": "cairn_status_bot"})
        self.assertEqual([call["method"] for call in setup["sent"]], ["getMe", "setWebhook", "setMyCommands"])
        hook = setup["sent"][1]["body"]
        self.assertEqual(hook["url"], "https://cairn-telegram.example.workers.dev/hook")
        self.assertEqual(hook["secret_token"], HOOK["X-Telegram-Bot-Api-Secret-Token"])
        self.assertEqual(state["body"], {"bot": "cairn_status_bot", "paired": False, "waiting": True, "at": None, "hour": 21, "sent": ""})
        self.assertNotIn(TOKEN, json.dumps(state["body"]))

    def test_setup_refuses_bad_input_and_tokens_telegram_does_not_know(self):
        out = self.run_steps([
            {**SETUP, "body": {"token": "nope", "code": CODE}},
            {**SETUP, "body": {"token": TOKEN, "code": "short"}},
            {**SETUP, "refuse": "getMe"},
        ])
        self.assertEqual([r["status"] for r in out["results"]], [400, 400, 400])
        self.assertEqual(out["results"][2]["body"], {"error": "telegram_refused_token"})
        self.assertEqual(out["kept"], {})

    def test_only_the_right_code_pairs_a_chat(self):
        out = self.run_steps([SETUP, STATUS, message(666, "/start wrong-code-000"), message(666, "/status"), PAIR, message(666, "/start " + CODE)])
        _, _, wrong, stranger, paired, replay = out["results"]
        self.assertEqual(len(wrong["sent"]), 1)
        self.assertIn("run `cairn telegram setup`", wrong["sent"][0]["body"]["text"])
        self.assertEqual(stranger["sent"], [])  # strangers learn nothing
        self.assertEqual([call["body"]["chat_id"] for call in paired["sent"]], [42, 42])
        self.assertIn("Paired. Cairn reports here every day at 21:00", paired["sent"][0]["body"]["text"])
        self.assertIn("All good.", paired["sent"][1]["body"]["text"])
        self.assertEqual(json.loads(out["kept"]["chat"]), {"id": 42})
        self.assertNotIn("pair", out["kept"])  # the code works once
        self.assertNotIn("All good.", json.dumps(replay["sent"]))

    def test_status_command_answers_the_paired_chat(self):
        out = self.run_steps([SETUP, STATUS, PAIR, message(42, "/status"), message(42, "hello")])
        asked, other = out["results"][3:]
        self.assertEqual(asked["sent"][0]["body"]["text"], "All good.\n\nAs of 19:00.")
        self.assertIn("Send /status", other["sent"][0]["body"]["text"])

    def test_daily_report_goes_once_at_the_chosen_local_hour(self):
        # 12:00Z is 19:00 on the PC, so 21:00 there is two hours later.
        out = self.run_steps([
            SETUP, {**STATUS, "now": T0 + HOUR}, PAIR,
            {"daily": True, "now": T0 + HOUR},
            {"daily": True, "now": T0 + 2 * HOUR},
            {"daily": True, "now": T0 + 3 * HOUR},
            {"daily": True, "now": T0 + 25 * HOUR},
            {"daily": True, "now": T0 + 26 * HOUR},
        ])
        early, due, again, next_early, next_due = out["results"][3:]
        self.assertEqual(early["result"], {"sent": False, "why": "too_early"})
        self.assertEqual(due["result"], {"sent": True})
        self.assertEqual(due["sent"][0]["body"]["text"], "All good.\n\nAs of 20:00.")
        self.assertEqual(again["result"], {"sent": False, "why": "already_sent"})
        self.assertEqual(next_early["result"]["why"], "too_early")
        self.assertEqual(next_due["result"], {"sent": True})
        text = next_due["sent"][0]["body"]["text"]
        self.assertTrue(text.startswith("Cairn has been quiet since 2026-11-13 20:00 (25 h ago)"), text)
        self.assertIn("All good.", text)

    def test_a_failed_send_is_tried_again_the_next_hour(self):
        out = self.run_steps([
            SETUP, STATUS, PAIR,
            {"daily": True, "now": T0 + 2 * HOUR, "refuse": "sendMessage"},
            {"daily": True, "now": T0 + 3 * HOUR},
        ])
        failed, retried = out["results"][3:]
        self.assertFalse(failed["result"]["sent"])
        self.assertEqual(retried["result"], {"sent": True})

    def test_nothing_is_sent_before_a_chat_is_paired(self):
        out = self.run_steps([SETUP, STATUS, {"daily": True, "now": T0 + 2 * HOUR}, {"method": "POST", "path": "/send", "headers": AUTH}])
        daily, send = out["results"][2:]
        self.assertEqual(daily["result"], {"sent": False, "why": "not_paired"})
        self.assertEqual((send["status"], send["sent"]), (409, []))

    def test_off_forgets_everything(self):
        out = self.run_steps([SETUP, STATUS, PAIR, {"method": "POST", "path": "/off", "headers": AUTH}, message(42, "/status")])
        off, after = out["results"][3:]
        self.assertEqual([call["method"] for call in off["sent"]], ["deleteWebhook"])
        self.assertEqual(out["kept"], {})
        self.assertEqual(after["sent"], [])


if __name__ == "__main__":
    unittest.main()

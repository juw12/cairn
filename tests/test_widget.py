"""cairn widget: Cairn's live sessions sent to the iPhone home-screen widget relay."""

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cairn import widget
from cairn.brain import Brain

CODE = "jw-" + "ab" * 16


def session(title, status, started, body_lines, ended=""):
    meta = [f"title: {title}", "type: session", f"status: {status}", f"started: {started}"]
    if ended:
        meta.append(f"ended: {ended}")
    return "---\n" + "\n".join(meta) + "\n---\n## Goal\n\n" + title + "\n\n## Log\n\n" + "\n".join(body_lines) + "\n"


class WidgetTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.root = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"CAIRN_HOME": self.home.name})
        self.env.start()
        self.brain = Brain(self.root.name, None)
        folder = self.brain.dir / "sessions"
        folder.mkdir(parents=True)
        (folder / "2026-11-13-a.md").write_text(session(
            "Add dark mode to the settings page", "live", "2026-11-13T09:00",
            ["- 09:00 start: Add dark mode", "- 09:20 pass: pytest (12s) · 40 passed"],
        ), encoding="utf-8")
        (folder / "2026-11-13-b.md").write_text(session(
            "Fix the GPX import", "shipped", "2026-11-13T07:00",
            ["- 07:00 start: Fix", "- 08:10 end: shipped: secret log text"], ended="2026-11-13T08:10",
        ), encoding="utf-8")
        (folder / "2026-11-12-c.md").write_text(session(
            "Yesterday's work", "shipped", "2026-11-12T07:00", ["- 07:00 start: x"], ended="2026-11-12T08:00",
        ), encoding="utf-8")
        self.now = dt.datetime(2026, 11, 13, 10, 0)

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()
        self.root.cleanup()

    def test_snapshot_counts_and_privacy(self):
        snap = widget.snapshot(self.brain, self.now)
        self.assertEqual(snap["day"], "2026-11-13")
        self.assertEqual(snap["live"], 1)
        self.assertEqual(snap["sessions"][0]["t"], "Add dark mode to the settings page")
        self.assertEqual(snap["sessions"][0]["k"], "pass")
        self.assertEqual(snap["shipped"], 1)  # yesterday's session doesn't count
        self.assertEqual(snap["last"]["t"], "Fix the GPX import")
        text = json.dumps(snap)
        self.assertNotIn("secret log text", text)  # titles and step kinds only
        self.assertNotIn("pytest", text)
        self.assertLess(len(widget.message_for(snap, 0).encode()), 3500)

    def test_log_line_after_midnight_is_yesterday(self):
        kind, at = widget._last_step("- 23:50 note: late", dt.datetime(2026, 11, 13, 0, 10))
        self.assertEqual(kind, "note")
        self.assertEqual(dt.datetime.fromtimestamp(at / 1000), dt.datetime(2026, 11, 12, 23, 50))

    def test_pair_rejects_bad_codes(self):
        with self.assertRaises(widget.WidgetError):
            widget.pair("not-a-code")
        self.assertEqual(widget.pair(CODE.upper()), CODE)
        self.assertEqual(widget.load_code(), CODE)
        self.assertTrue(widget.unpair())
        self.assertEqual(widget.load_code(), "")

    def test_publish_only_when_changed_and_rate_limited(self):
        sent = []

        def post(code, message):
            sent.append((code, json.loads(message)))
            return 200

        self.assertEqual(widget.publish(self.brain, now=1000, post=post)["why"], "not paired")
        widget.pair(CODE)
        start = self.now.timestamp()
        self.assertTrue(widget.publish(self.brain, now=start, post=post)["sent"])
        self.assertEqual(sent[0][0], CODE)
        self.assertEqual(sent[0][1]["app"], "cairn")
        self.assertEqual(sent[0][1]["v"], 1)
        self.assertEqual(widget.publish(self.brain, now=start + 3, post=post)["why"], "unchanged")
        # a new log line: sent, but not sooner than the minimum gap
        path = self.brain.dir / "sessions" / "2026-11-13-a.md"
        path.write_text(path.read_text(encoding="utf-8") + "- 09:59 fail: pytest\n", encoding="utf-8")
        waiting = widget.publish(self.brain, now=start + 3, post=post)
        self.assertEqual(waiting["why"], "waiting")
        later = widget.publish(self.brain, now=start + widget.MIN_GAP_SECONDS, post=post)
        self.assertTrue(later["sent"])
        self.assertEqual(sent[-1][1]["d"]["sessions"][0]["k"], "fail")
        self.assertEqual(len(sent), 2)

    def test_backoff_after_429_and_daily_budget(self):
        widget.pair(CODE)
        start = self.now.timestamp()
        result = widget.publish(self.brain, now=start, post=lambda code, message: 429)
        self.assertFalse(result["sent"])
        self.assertEqual(widget.publish(self.brain, now=start + 60, post=lambda c, m: 200)["why"], "relay asked us to slow down")
        state = widget._read_json(widget.state_path())
        state.update({"backoff": 0, "count": widget.DAILY_BUDGET, "day": "2026-11-13"})
        widget._write_json(widget.state_path(), state)
        self.assertEqual(widget.publish(self.brain, now=start + 60, post=lambda c, m: 200)["why"], "daily budget used")
        self.assertTrue(widget.publish(self.brain, now=start + 60, force=True, post=lambda c, m: 200)["sent"])

    def test_network_failure_is_reported_not_raised(self):
        widget.pair(CODE)

        def down(code, message):
            raise widget.WidgetError("Couldn't reach https://ntfy.sh: offline")

        result = widget.publish(self.brain, now=self.now.timestamp(), post=down)
        self.assertFalse(result["sent"])
        self.assertIn("Couldn't reach", result["why"])
        self.assertFalse(widget._read_json(widget.state_path()).get("body"))  # retried next pass


if __name__ == "__main__":
    unittest.main()

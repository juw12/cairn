"""Precedent (briefs from your own history, the #queue) and the autopilot worker, with no network,
no real agent and no files outside a temp folder (CAIRN_HOME points there)."""

import http.client
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from cairn import autopilot, mcp, precedent
from cairn.brain import Brain
from cairn.server import make_server

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def write_transcripts(folder: Path) -> None:
    folder.mkdir(parents=True)
    lines = [
        {"type": "user", "timestamp": "2026-10-01T09:00:00", "content": "build a landing page for trailmix"},
        {"type": "assistant", "content": "ok"},
        {"type": "user", "timestamp": "2026-10-01T09:30:00", "content": "also add it to my portfolio"},
        {"type": "user", "timestamp": "2026-10-01T09:40:00", "content": "continue"},
        {"type": "user", "timestamp": "2026-10-01T10:00:00", "content": f"push it, my token is {TOKEN}"},
    ]
    (folder / "s1.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    other = [
        {"type": "user", "timestamp": "2026-10-03T08:00:00", "message": {"content": [{"type": "text", "text": "landing page for the cairn app"}]}},
        {"type": "user", "timestamp": "2026-10-03T08:20:00", "content": "add it to my portfolio and the obsidian note"},
    ]
    (folder / "s2.jsonl").write_text("\n".join(json.dumps(line) for line in other) + "\n", encoding="utf-8")


def write_opencode_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT)")
    connection.execute("CREATE TABLE part (message_id TEXT, data TEXT)")
    rows = [
        ("m1", "oc1", 1790000000000, '{"role":"user"}', {"type": "text", "text": "export the weekly report as csv"}),
        ("m2", "oc1", 1790000060000, '{"role":"user"}', {"type": "text", "text": "and email it to the team"}),
        ("m3", "oc1", 1790000120000, '{"role":"assistant"}', {"type": "text", "text": "done, here it is"}),
        ("m4", "oc1", 1790000180000, '{"role":"user"}', {"type": "text", "text": "system reminder", "synthetic": True}),
    ]
    for message_id, session, created, data, part in rows:
        connection.execute("INSERT INTO message VALUES (?, ?, ?, ?)", (message_id, session, created, data))
        connection.execute("INSERT INTO part VALUES (?, ?)", (message_id, json.dumps(part)))
    connection.commit()
    connection.close()


class HomeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old_home = os.environ.get("CAIRN_HOME")
        os.environ["CAIRN_HOME"] = str(self.tmp / "home")
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.brain = Brain(self.project)
        self.brain.init()

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("CAIRN_HOME", None)
        else:
            os.environ["CAIRN_HOME"] = self.old_home
        shutil.rmtree(self.tmp, ignore_errors=True)


class PrecedentTests(HomeTestCase):
    def learn(self):
        transcripts = self.tmp / "transcripts"
        write_transcripts(transcripts)
        db = self.tmp / "opencode.db"
        write_opencode_db(db)
        update_log = self.project / "Update Log"
        update_log.mkdir()
        (update_log / "2026-10-02 trailmix landing.md").write_text("# Trailmix landing page\n\nShipped the landing page and the portfolio card.\n", encoding="utf-8")
        return precedent.learn(self.brain, transcripts=[transcripts], opencode_db=db)

    def test_learn_reads_every_source_and_redacts(self):
        self.learn()
        rows = [json.loads(line) for line in (precedent.library_dir() / "precedents.jsonl").read_text(encoding="utf-8").splitlines()]
        goals = {row["goal"] for row in rows}
        self.assertIn("build a landing page for trailmix", goals)
        self.assertIn("landing page for the cairn app", goals)
        self.assertIn("export the weekly report as csv", goals)
        trailmix = next(row for row in rows if row["goal"].startswith("build a landing"))
        self.assertEqual(trailmix["nudges"], 1, "'continue' is a nudge, not a follow-up")
        self.assertIn("also add it to my portfolio", trailmix["followups"])
        library = (precedent.library_dir() / "precedents.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, library)
        self.assertNotIn("system reminder", library)
        self.assertNotIn("done, here it is", library)
        updates = (precedent.library_dir() / "updates.jsonl").read_text(encoding="utf-8")
        self.assertIn("Trailmix landing page", updates)

    def test_learn_keeps_lessons_and_old_precedents(self):
        self.learn()
        precedent.log_lesson("landing page", "page plus portfolio card", "also the obsidian note")
        precedent.learn(self.brain, transcripts=[], opencode_db=self.tmp / "missing.db")
        library = (precedent.library_dir() / "precedents.jsonl").read_text(encoding="utf-8")
        self.assertIn("build a landing page for trailmix", library, "precedents from sources now gone are kept")
        self.assertIn("also the obsidian note", (precedent.library_dir() / "learned.jsonl").read_text(encoding="utf-8"))

    def test_search_and_hidden_asks(self):
        self.learn()
        results = precedent.search("landing page for my new app", k=5)
        self.assertTrue(results)
        self.assertIn("landing page", results[0][1].get("goal", results[0][1].get("title", "")).lower())
        asks = precedent.hidden_asks([r for r in results if r[1]["_kind"] == "precedent"])
        self.assertTrue(any("portfolio" in ask for ask in asks), asks)

    def test_brief_has_every_section(self):
        self.learn()
        precedent.ensure_profile(self.brain, "## Who\n\nAn engineer who wants things shipped.\n")
        precedent.queue_add(self.brain, "Write the release notes #auto")
        text = precedent.brief(self.brain, "landing page for my new app")
        for heading in ("Definition of Done", "Hidden asks", "Closest precedents", "Profile", "Queue", "How to work"):
            self.assertIn(heading, text)
        self.assertIn("portfolio", text)
        self.assertIn("An engineer who wants things shipped.", text)
        empty = precedent.brief(self.brain, "")
        self.assertIn("no goal", empty)

    def test_queue_add_next_done_and_flags(self):
        long_text = "Make the export page handle very large reports without freezing the browser, stream rows in chunks and show progress " * 2
        auto = precedent.queue_add(self.brain, "Add CSV export #auto")
        blocked = precedent.queue_add(self.brain, "Renew the signing certificate #needs-juwen")
        plain = precedent.queue_add(self.brain, long_text)
        items = {item["path"]: item for item in precedent.queue_items(self.brain)}
        self.assertTrue(items[auto["path"]]["auto"])
        self.assertEqual(items[auto["path"]]["title"], "Add CSV export")
        self.assertTrue(items[blocked["path"]]["needs_me"])
        self.assertLessEqual(len(items[plain["path"]]["title"]), 91)
        self.assertEqual(items[plain["path"]]["text"], " ".join(long_text.split()))
        self.assertEqual(precedent.queue_next(self.brain, auto_only=True)["path"], auto["path"])
        self.assertNotEqual(precedent.queue_next(self.brain)["path"], blocked["path"])
        precedent.queue_done(self.brain, auto["path"], "shipped in v1.4")
        self.assertNotIn(auto["path"], {i["path"] for i in precedent.queue_items(self.brain)})
        self.assertIsNone(precedent.queue_next(self.brain, auto_only=True))
        self.assertIn("shipped in v1.4", self.brain.read(auto["path"])["text"])
        with self.assertRaises(ValueError):
            precedent.queue_add(self.brain, "   ")

    def test_import_legacy_is_idempotent(self):
        skill = self.tmp / "skill"
        (skill / "data").mkdir(parents=True)
        (skill / "data" / "precedents.jsonl").write_text(json.dumps({"session": "x", "date": "2026-09-01", "goal": "old goal", "followups": []}) + "\n", encoding="utf-8")
        (skill / "data" / "learned.jsonl").write_text(json.dumps({"date": "2026-09-02", "goal": "g", "delivered": "d", "correction": ""}) + "\n", encoding="utf-8")
        (skill / "profile.md").write_text("# Profile\n\n## Who\n\nLikes it shipped.\n", encoding="utf-8")
        queue = self.tmp / "Queue.md"
        queue.write_text("# Queue\n\n## Cairn\n\n- [ ] Add dark mode #auto\n- [ ] Pay the domain bill #needs-juwen\n- [x] Already done\n", encoding="utf-8")
        first = precedent.import_legacy(self.brain, skill, queue)
        second = precedent.import_legacy(self.brain, skill, queue)
        self.assertEqual((first["library"], first["lessons"], first["queue"]), (1, 1, 2))
        self.assertTrue(first["profile"])
        self.assertEqual((second["library"], second["lessons"], second["queue"]), (0, 0, 0))
        items = precedent.queue_items(self.brain)
        self.assertEqual(len(items), 2)
        self.assertEqual({i["auto"] for i in items if "dark mode" in i["title"]}, {True})
        self.assertEqual({i["needs_me"] for i in items if "domain" in i["title"]}, {True})
        self.assertIn("Likes it shipped.", precedent.profile_text(self.brain))


class FakeEngine:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def run(self, job, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0), "session-1", ""

    def stop(self):
        pass


class AutopilotTests(HomeTestCase):
    def worker(self, replies):
        self.notes = []
        engine = FakeEngine(replies)
        return autopilot.Autopilot(self.brain, engine, notifier=lambda title, text: self.notes.append((title, text))), engine

    def test_rounds_until_done_then_marks_queue_and_logs_lesson(self):
        autopilot.update_settings({"improve_rounds_after_done": 1})
        task = precedent.queue_add(self.brain, "Add CSV export #auto")
        worker, engine = self.worker([
            "Read the code.\nSTATUS: CONTINUE\nNEXT: write the exporter",
            "Exporter written, tests pass.\nSTATUS: DONE",
            "Polished the button label.\nSTATUS: DONE",
        ])
        job = worker.pull_from_queue()
        self.assertEqual(job["queue_path"], task["path"])
        self.assertIsNone(worker.pull_from_queue(), "a queue task is only taken once")
        for _ in range(3):
            worker.work_on(autopilot.get_job(job["id"]))
        job = autopilot.get_job(job["id"])
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["rounds"], 3)
        self.assertEqual(job["session_id"], "session-1")
        self.assertIn("Add CSV export", engine.prompts[0])
        self.assertIn("How to work", engine.prompts[0], "the first round gets the brief")
        self.assertIn("write the exporter", engine.prompts[1])
        self.assertEqual(precedent.queue_items(self.brain), [])
        self.assertIn("Polished the button label", (precedent.library_dir() / "learned.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(self.notes[-1][0], "Cairn autopilot finished")
        log = autopilot.read_log(job["id"])["text"]
        self.assertIn("Round 3 (improve)", log)

    def test_blocked_notifies_and_comment_resumes(self):
        worker, engine = self.worker(["STATUS: BLOCKED: needs the GitHub token", "Pushed.\nSTATUS: CONTINUE"])
        job = autopilot.create_job("Publish the page", str(self.tmp / "work"))
        worker.work_on(job)
        job = autopilot.get_job(job["id"])
        self.assertEqual(job["status"], "blocked")
        self.assertIn("GitHub token", job["blocked_reason"])
        self.assertEqual(self.notes[0][0], "Cairn autopilot needs you")
        job = autopilot.add_comment(job["id"], "token is in the vault now")
        self.assertEqual(job["status"], "queued")
        worker.work_on(job)
        self.assertIn("token is in the vault now", engine.prompts[1])
        self.assertEqual(autopilot.get_job(job["id"])["pending_comments"], [])

    def test_parse_reply_takes_the_last_status(self):
        self.assertEqual(autopilot.parse_reply("STATUS: CONTINUE\nmore\nSTATUS: DONE - shipped\nNEXT: polish"), ("DONE", "shipped", "polish"))
        self.assertEqual(autopilot.parse_reply("no protocol"), ("", "", ""))

    def test_one_worker_per_machine(self):
        first, _ = self.worker([])
        second, _ = self.worker([])
        self.assertTrue(first.claim())
        self.assertFalse(second.claim())
        self.assertEqual(second.status()["worker"], "elsewhere")
        first.release()
        self.assertTrue(second.claim())

    def test_custom_command_places_the_prompt(self):
        engine = autopilot.Engine()
        job = {"goal": "g", "session_id": ""}
        autopilot.update_settings({"engine": "command", "command": "agent --ask {prompt} --quiet"})
        self.assertEqual(engine.command(job, "do it"), ["agent", "--ask", "do it", "--quiet"])
        autopilot.update_settings({"command": "claude -p"})
        self.assertEqual(engine.command(job, "do it"), ["claude", "-p", "do it"])

    def test_settings_are_validated(self):
        saved = autopilot.update_settings({"engine": "nonsense", "max_rounds_per_goal": "0", "unknown": 1, "enabled": 1})
        self.assertEqual(saved["engine"], "opencode")
        self.assertEqual(saved["max_rounds_per_goal"], 1)
        self.assertNotIn("unknown", saved)
        self.assertIs(saved["enabled"], True)

    def test_import_legacy_jobs(self):
        app = self.tmp / "agent"
        (app / "state" / "logs").mkdir(parents=True)
        (app / "state" / "jobs.json").write_text(json.dumps([
            {"id": "a1", "goal": "old running goal", "status": "running", "workdir": "x"},
            {"id": "b2", "goal": "old done goal", "status": "done", "workdir": "y"},
        ]), encoding="utf-8")
        (app / "state" / "logs" / "a1.log").write_text("round 1\n", encoding="utf-8")
        self.assertEqual(autopilot.import_legacy_jobs(app), 2)
        self.assertEqual(autopilot.import_legacy_jobs(app), 0)
        self.assertEqual(autopilot.get_job("a1")["status"], "stopped")
        self.assertIn("round 1", autopilot.read_log("a1")["text"])


class McpPrecedentTests(HomeTestCase):
    def call(self, server, name, **arguments):
        result = server.call_tool(name, arguments)
        return result["content"][0]["text"], result["isError"]

    def test_tools_and_prompt(self):
        server = mcp.Server(self.brain)
        text, error = self.call(server, "queue_add", text="Add CSV export", auto=True)
        self.assertFalse(error)
        text, error = self.call(server, "queue_next", auto_only=True)
        self.assertIn("Add CSV export", text)
        path = text.split()[0].removeprefix("brain/")
        text, error = self.call(server, "precedent_brief", goal="csv export")
        self.assertIn("Definition of Done", text)
        text, error = self.call(server, "precedent_log", goal="csv export", delivered="CSV button", queue_path=path)
        self.assertFalse(error, text)
        self.assertEqual(precedent.queue_items(self.brain), [])
        text, error = self.call(server, "precedent_log", goal="", delivered="")
        self.assertTrue(error)
        prompt = server.dispatch("prompts/get", {"name": "precedent", "arguments": {"goal": "csv export"}})
        self.assertIn("csv export", prompt["messages"][0]["content"]["text"])
        hidden = {t["name"] for t in mcp.Server(self.brain, read_only=True).tools}
        self.assertIn("precedent_brief", hidden)
        self.assertNotIn("queue_add", hidden)


class ServerApiTests(HomeTestCase):
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

    def test_goal_lifecycle_and_queue(self):
        self.assertIsNone(self.server.RequestHandlerClass.autopilot, "test servers never start a worker")
        status, job = self.call("POST", "/api/autopilot/goal", {"goal": "Ship it", "workdir": str(self.tmp / "w")})
        self.assertEqual((status, job["status"]), (200, "queued"))
        status, state = self.call("GET", "/api/autopilot")
        self.assertEqual([j["goal"] for j in state["jobs"]], ["Ship it"])
        self.assertFalse(state["settings"]["enabled"])
        status, log = self.call("GET", f"/api/autopilot/log?id={job['id']}&since=0")
        self.assertIn("Goal received: Ship it", log["text"])
        status, tail = self.call("GET", f"/api/autopilot/log?id={job['id']}&since={log['size']}")
        self.assertEqual(tail["text"], "")
        self.call("POST", "/api/autopilot/stop", {"id": job["id"]})
        self.assertEqual(autopilot.get_job(job["id"])["status"], "stopped")
        status, commented = self.call("POST", "/api/autopilot/comment", {"id": job["id"], "text": "use the blue theme"})
        self.assertEqual(commented["status"], "queued")
        status, _ = self.call("POST", "/api/autopilot/comment", {"id": "nope", "text": "x"})
        self.assertEqual(status, 404)
        status, saved = self.call("POST", "/api/autopilot/settings", {"enabled": True, "round_timeout_min": 10})
        self.assertEqual((saved["enabled"], saved["round_timeout_min"]), (True, 10))
        status, note = self.call("POST", "/api/autopilot/queue", {"text": "Write release notes", "auto": True})
        self.assertEqual(status, 200)
        status, brief = self.call("GET", "/api/precedent/brief?goal=release%20notes")
        self.assertIn("Write release notes", brief["brief"])
        self.assertEqual(brief["queue"][0]["auto"], True)
        self.call("POST", "/api/autopilot/delete", {"id": job["id"]})
        self.assertIsNone(autopilot.get_job(job["id"]))
        status, _ = self.call("POST", "/api/autopilot/goal", {"goal": "  "})
        self.assertEqual(status, 400)


class CliTests(HomeTestCase):
    def cairn(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, "-m", "cairn", "--root", str(self.project), *args], capture_output=True, text=True, env=env, timeout=60, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_queue_next_brief_and_autopilot(self):
        self.cairn("precedent", "queue", "Add CSV export", "--auto")
        self.assertIn("Add CSV export", self.cairn("precedent", "next", "--auto"))
        self.assertIn("Definition of Done", self.cairn("precedent", "brief", "csv", "export"))
        self.cairn("autopilot", "add", "Ship the page")
        self.assertIn("Ship the page", self.cairn("autopilot", "status"))
        self.cairn("autopilot", "set", "round_timeout_min=7")
        self.assertEqual(autopilot.settings()["round_timeout_min"], 7)


    def test_init_moves_everything_in(self):
        skill = self.tmp / "skill"
        (skill / "data").mkdir(parents=True)
        (skill / "profile.md").write_text("# Profile\n\n## Who\n\nShips things.\n", encoding="utf-8")
        (skill / "data" / "learned.jsonl").write_text(json.dumps({"date": "2026-09-02", "goal": "g", "delivered": "d", "correction": ""}) + "\n", encoding="utf-8")
        queue = self.tmp / "Queue.md"
        queue.write_text("- [ ] Add dark mode #auto\n", encoding="utf-8")
        app = self.tmp / "agent"
        (app / "state").mkdir(parents=True)
        (app / "state" / "jobs.json").write_text(json.dumps([{"id": "j1", "goal": "old goal", "status": "done", "workdir": "x"}]), encoding="utf-8")
        out = self.cairn("precedent", "init", "--import-skill", str(skill), "--queue-file", str(queue), "--agent-app", str(app))
        self.assertIn("1", out)
        self.assertEqual([i["title"] for i in precedent.queue_items(self.brain)], ["Add dark mode"])
        self.assertIn("Ships things.", precedent.profile_text(self.brain))
        self.assertEqual(autopilot.get_job("j1")["goal"], "old goal")
        self.cairn("precedent", "init", "--import-skill", str(skill), "--queue-file", str(queue), "--agent-app", str(app))
        self.assertEqual(len(precedent.queue_items(self.brain)), 1, "init is safe to re-run")

if __name__ == "__main__":
    unittest.main()

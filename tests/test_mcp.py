"""`cairn mcp`: a real subprocess speaking MCP (JSON-RPC 2.0 over stdio), as an assistant would."""

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class McpClient:
    def __init__(self, project: Path, *extra: str):
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "cairn", "--root", str(project), "mcp", *extra],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.next_id = 0

    def send(self, payload) -> None:
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def read(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("server closed stdout: " + self.proc.stderr.read().decode("utf-8", "replace"))
        return json.loads(line.decode("utf-8"))  # every stdout line must be JSON

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        reply = self.read()
        assert reply["id"] == self.next_id, reply
        return reply

    def call(self, name: str, **arguments) -> tuple[str, bool]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})["result"]
        return result["content"][0]["text"], result["isError"]

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "trailmap"
        tasks = self.project / "brain" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "ship-v1.md").write_text(
            "---\ntitle: Ship v1\ntype: task\nstatus: todo\ntags: [release]\n---\nShip the GPX import first.\n",
            encoding="utf-8",
        )
        notes = self.project / "brain" / "notes"
        notes.mkdir()
        (notes / "launch-plan.md").write_text("# Launch plan\n\nDepends on [[ship-v1]].\n", encoding="utf-8")
        (self.project / "secret.txt").write_text("outside the brain", encoding="utf-8")
        self.client = McpClient(self.project)
        init = self.client.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        })
        self.init = init["result"]
        self.client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_handshake_negotiates_the_version_and_describes_the_server(self):
        self.assertEqual(self.init["protocolVersion"], "2025-03-26")
        self.assertEqual(self.init["serverInfo"]["name"], "cairn")
        self.assertIn("trailmap", self.init["serverInfo"]["title"])
        self.assertEqual(set(self.init["capabilities"]), {"tools", "resources", "prompts"})
        self.assertIn("decision", self.init["instructions"])
        # The notification above got no reply: the next line answers the ping.
        self.assertEqual(self.client.request("ping")["result"], {})
        unknown = McpClient(self.project)
        reply = unknown.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        unknown.close()

    def test_tools_are_listed_with_schemas(self):
        tools = {t["name"]: t for t in self.client.request("tools/list")["result"]["tools"]}
        self.assertEqual(
            set(tools),
            {"search", "read_note", "list_notes", "context", "recent", "code_todos", "capture", "update_note", "log",
         "precedent_brief", "queue_next", "queue_add", "precedent_log", "handoffs", "handoff_send", "handoff_answer",
             "priority"},
        )
        for tool in tools.values():
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
            self.assertNotIn("handler", tool)
        self.assertTrue(tools["search"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["capture"]["annotations"]["readOnlyHint"])
        hint_names = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
        for tool in tools.values():
            for hint in hint_names:
                self.assertIsInstance(tool["annotations"].get(hint), bool, f"{tool['name']} {hint}")

    def test_computers_leave_each_other_messages(self):
        mac, asus = "MacBook Pro 15,4", "ASUS EXPERTBOOK P5405CSA"
        sessions = self.project / "brain" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        for index, device in enumerate((mac, asus)):
            (sessions / f"2026-11-20-work-{index}.md").write_text(
                f'---\ntitle: Work {index}\ntype: session\nstatus: done\nmachine: "{device}"\nstarted: 2026-11-20T10:00\n---\n', encoding="utf-8")

        text, error = self.client.call("handoffs", device=asus)
        self.assertFalse(error, text)
        self.assertIn("Nothing is waiting for this computer.", text)

        text, error = self.client.call("handoff_send", title="Check Buddy on the Windows laptop", to=["asus"], body="Run the self-test.", device=mac)
        self.assertFalse(error, text)
        self.assertIn(f"Left for {asus}: brain/handoffs/", text)
        self.assertIn("next sync", text, "a folder without git cannot send at once, and says so")

        text, error = self.client.call("handoffs", device=asus)
        self.assertIn(f"For this computer ({asus}):", text)
        self.assertIn("Check Buddy on the Windows laptop", text)

        text, error = self.client.call("handoff_answer", thread="buddy", says="picked-up", device=asus)
        self.assertFalse(error, text)
        self.assertIn("Picked up: brain/handoffs/", text)
        text, error = self.client.call("handoff_answer", thread="buddy", says="done", text="24 of 24 checks pass.", device=asus)
        self.assertFalse(error, text)

        text, error = self.client.call("handoffs", thread="buddy")
        self.assertFalse(error, text)
        self.assertIn("Run the self-test.", text)
        self.assertIn("[picked up]", text)
        self.assertIn("24 of 24 checks pass.", text)
        self.assertIn("settled", text)
        text, error = self.client.call("handoffs", device=mac, all=True)
        self.assertIn("Settled:", text)

        # What cannot be done says why, as a tool error and not a crash.
        text, error = self.client.call("handoff_send", title="For nobody")
        self.assertTrue(error)
        self.assertIn("who it is for", text)
        text, error = self.client.call("handoff_send", title="For a stranger", to=["thinkpad"], device=mac)
        self.assertTrue(error)
        self.assertIn("No computer called", text)
        text, error = self.client.call("handoff_answer", thread="no such conversation", text="hello")
        self.assertTrue(error)
        self.assertIn("No conversation fits", text)

        # A read-only server still shows what was left; it cannot answer.
        reader = McpClient(self.project, "--read-only")
        reader.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        names = {tool["name"] for tool in reader.request("tools/list")["result"]["tools"]}
        self.assertIn("handoffs", names)
        self.assertFalse({"handoff_send", "handoff_answer"} & names)
        text, error = reader.call("handoffs", device=mac, all=True)
        self.assertIn("Check Buddy on the Windows laptop", text)
        reader.close()

    def test_a_session_that_never_runs_cairn_vibe_learns_its_place_in_the_order(self):
        """The watcher lists another app's runs as a session (source: opencode, with the folder it works in).
        That app never runs `cairn vibe`, so nothing told it its place: it asks the `priority` tool."""
        sessions = self.project / "brain" / "sessions"
        sessions.mkdir()
        now = dt.datetime.now()
        day, clock = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")

        def session(name: str, title: str, more: str = "") -> None:
            (sessions / f"{name}.md").write_text(
                f"---\ntitle: \"{title}\"\ntype: session\nstatus: live\ntags: []\ncreated: {day}\nstarted: {day}T{clock}\n{more}---\n"
                f"## Goal\n\n{title}\n\n## Log\n\n- {clock} start: {title}\n", encoding="utf-8")

        session(f"{day}-lab-automated-runs", "lab: automated runs", "agent: opencode\nsource: opencode\nexternal: batch-1\nfolder: E:/Lab/run\n")
        session(f"{day}-neon-coast", "Neon Coast touch controls", "agent: vector\n")
        text, error = self.client.call("priority", folder="E:/Lab/run")
        self.assertFalse(error)
        self.assertIn("has not put the running sessions in order", text)

        (self.project / "brain" / "CAIRN-PRIORITY.md").write_text(f"1. {day}-neon-coast\n2. {day}-lab-automated-runs\n", encoding="utf-8")
        text, error = self.client.call("priority", folder="e:\\lab\\run\\")  # as Windows writes the same folder
        self.assertFalse(error)
        self.assertIn("PRIORITY (set in Cairn): this chat is number 2 of 2 running sessions.", text)
        self.assertIn("Before it: 1. Neon Coast touch controls", text)
        self.assertIn(f"This session: {day}-lab-automated-runs", text)
        text, error = self.client.call("priority", session=f"{day}-neon-coast")
        self.assertIn("number 1 of 2 running sessions, the most important one", text)

        text, error = self.client.call("priority", folder="E:/Somewhere/else")
        self.assertFalse(error)
        self.assertIn("No running session of this computer works in", text)
        self.assertIn("1. Neon Coast touch controls", text)
        self.assertNotIn("Move one:", text, "the order is the user's: an assistant is not told how to change it")
        before = (self.project / "brain" / "CAIRN-PRIORITY.md").read_text(encoding="utf-8")
        reader = McpClient(self.project, "--read-only")
        reader.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        self.assertIn("priority", {tool["name"] for tool in reader.request("tools/list")["result"]["tools"]})
        reader.close()
        self.assertEqual((self.project / "brain" / "CAIRN-PRIORITY.md").read_text(encoding="utf-8"), before, "the tool only reads")

    def test_capture_search_and_read_a_decision(self):
        text, error = self.client.call(
            "capture",
            text="decision: cache tiles in SQLite #storage",
            body="## Context\n\nOffline maps need a cache.\n\n## Decision\n\nSQLite, not IndexedDB.\n",
        )
        self.assertFalse(error, text)
        self.assertIn("decisions/", text)
        files = list((self.project / "brain" / "decisions").glob("*.md"))
        self.assertEqual(len(files), 1)
        saved = files[0].read_text(encoding="utf-8")
        self.assertIn("type: decision", saved)
        self.assertIn("status: proposed", saved)
        self.assertIn("tags: [storage]", saved)
        self.assertIn("SQLite, not IndexedDB", saved)

        found, _ = self.client.call("search", query="sqlite cache")
        self.assertIn(files[0].name, found)
        self.assertIn("cache tiles in SQLite", found)
        missing, _ = self.client.call("search", query="sqlite kubernetes")
        self.assertIn("Nothing in the brain matches", missing)

        page, error = self.client.call("read_note", path="ship-v1")
        self.assertFalse(error)
        self.assertIn("Ship the GPX import first.", page)
        self.assertIn("Linked from: notes/launch-plan.md", page)
        by_title, _ = self.client.call("read_note", path="Ship v1")
        self.assertIn("brain/tasks/ship-v1.md", by_title)

    def test_update_note_moves_a_task_and_keeps_the_rest(self):
        text, error = self.client.call("update_note", path="tasks/ship-v1.md", status="doing", add_tags=["web"], append="Started on the parser.")
        self.assertFalse(error, text)
        saved = (self.project / "brain" / "tasks" / "ship-v1.md").read_text(encoding="utf-8")
        self.assertIn("status: doing", saved)
        self.assertIn("tags: [release, web]", saved)
        self.assertIn("Ship the GPX import first.\n\nStarted on the parser.", saved)
        self.assertIn("title: Ship v1", saved)

        bad, error = self.client.call("update_note", path="ship-v1", status="finished")
        self.assertTrue(error)
        self.assertIn("todo, doing, done", bad)
        nothing, error = self.client.call("update_note", path="ship-v1")
        self.assertTrue(error)
        self.assertIn("Nothing to change", nothing)

        listed, _ = self.client.call("list_notes", type="task", status="doing")
        self.assertIn("tasks/ship-v1.md", listed)
        self.assertIn("#web", listed)
        none, _ = self.client.call("list_notes", type="task", status="done")
        self.assertEqual(none, "No notes match.")

    def test_log_writes_today_and_links_a_new_task(self):
        text, error = self.client.call("log", text="Fixed the elevation profile, tests pass")
        self.assertFalse(error, text)
        today = dt.date.today().isoformat()
        page = self.project / "brain" / "journal" / f"{today}.md"
        self.assertIn("Fixed the elevation profile, tests pass", page.read_text(encoding="utf-8"))

        text, error = self.client.call("log", text="todo: write release notes #release")
        self.assertFalse(error, text)
        self.assertTrue((self.project / "brain" / "tasks" / "write-release-notes.md").exists())
        self.assertIn("[[write-release-notes|write release notes]]", page.read_text(encoding="utf-8"))

        recent, _ = self.client.call("recent", days=2)
        self.assertIn(f"## {today}", recent)
        self.assertIn("Fixed the elevation profile", recent)

        brief, _ = self.client.call("context")
        self.assertIn("Ship v1", brief)

    def test_resources_and_the_project_brief_prompt(self):
        resources = self.client.request("resources/list")["result"]["resources"]
        uris = {r["uri"] for r in resources}
        self.assertIn("cairn://context", uris)
        self.assertIn("cairn://brain/tasks/ship-v1.md", uris)
        content = self.client.request("resources/read", {"uri": "cairn://brain/tasks/ship-v1.md"})["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "text/markdown")
        self.assertEqual(content["text"], (self.project / "brain" / "tasks" / "ship-v1.md").read_bytes().decode("utf-8"))
        brief = self.client.request("resources/read", {"uri": "cairn://context"})["result"]["contents"][0]["text"]
        self.assertIn("Ship v1", brief)

        prompts = self.client.request("prompts/list")["result"]["prompts"]
        self.assertEqual([p["name"] for p in prompts], ["project-brief", "precedent"])
        message = self.client.request("prompts/get", {"name": "project-brief"})["result"]["messages"][0]
        self.assertEqual(message["role"], "user")
        self.assertIn("Ship v1", message["content"]["text"])

    def test_nothing_outside_the_brain_is_reachable(self):
        text, error = self.client.call("read_note", path="../secret.txt")
        self.assertTrue(error)
        self.assertNotIn("outside the brain", text)
        reply = self.client.request("resources/read", {"uri": "cairn://brain/../secret.txt"})
        self.assertEqual(reply["error"]["code"], -32002)
        reply = self.client.request("resources/read", {"uri": "file:///etc/passwd"})
        self.assertEqual(reply["error"]["code"], -32002)

    def test_protocol_errors_are_answered_and_the_session_survives(self):
        self.assertEqual(self.client.request("no/such/method")["error"]["code"], -32601)
        self.assertEqual(self.client.request("tools/call", {"name": "nope", "arguments": {}})["error"]["code"], -32602)
        self.client.proc.stdin.write(b"{not json\n")
        self.client.proc.stdin.flush()
        parse = self.client.read()
        self.assertEqual(parse["error"]["code"], -32700)
        self.assertIsNone(parse["id"])
        empty, error = self.client.call("capture", text="   ")
        self.assertTrue(error)
        self.assertIn("Give text", empty)
        self.assertEqual(self.client.request("ping")["result"], {})

    def test_read_only_server_has_no_write_tools(self):
        reader = McpClient(self.project, "--read-only")
        init = reader.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        self.assertIn("read-only", init["instructions"])
        names = {t["name"] for t in reader.request("tools/list")["result"]["tools"]}
        self.assertFalse(names & {"capture", "update_note", "log", "queue_add", "precedent_log"})
        self.assertIn("search", names)
        refused = reader.request("tools/call", {"name": "capture", "arguments": {"text": "idea: nope"}})
        self.assertIn("read-only", refused["error"]["message"])
        self.assertFalse((self.project / "brain" / "ideas").exists())
        reader.close()

    def test_print_config_names_every_client(self):
        result = subprocess.run(
            [sys.executable, "-m", "cairn", "--root", str(self.project), "mcp", "--print-config"],
            capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)), timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in ('"mcpServers"', '"servers"', '"mcp"', "claude mcp add cairn-trailmap", '"type": "stdio"'):
            self.assertIn(marker, result.stdout)
        block = result.stdout.split("Windsurf:\n", 1)[1].split("\n\n", 1)[0]
        config = json.loads(block)
        server = config["mcpServers"]["cairn-trailmap"]
        self.assertEqual(server["args"][-1], "mcp")
        self.assertTrue(os.path.samefile(server["args"][server["args"].index("--root") + 1], self.project))


if __name__ == "__main__":
    unittest.main()

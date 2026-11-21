"""Gates B2 and B5: the real web app in a headless browser (demo mode, GitHub Pages runtime)."""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import go_to, install_github_mock, launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

VIEWS = ["home", "ideas", "board", "decisions", "notes", "journal", "diary", "map", "canvas", "code", "vibe", "autopilot", "context", "plugins", "people", "proof"]

# The sidebar has one entry per hub; a hub with several pages shows them as tabs (docs/state.js HUBS).
HUBS = {
    "today": ["home", "board", "ideas", "decisions"],
    "journal": ["journal", "diary"],
    "chat": ["chat", "glow", "autopilot"],
    "map": ["map", "canvas"],
    "build": ["vibe", "projects"],
    "people": ["people", "proof"],
}
# Pages with no sidebar entry: Ctrl+K (and the puzzle button in the sidebar foot) open them, as tabs of one strip.
# All notes and the Code inbox are there too: the assistants use them more than you do.
HIDDEN_HUBS = {
    "tools": ["notes", "code", "context", "obsidian", "plugins"],
}


# The demo brain has 27 notes: the original 18 plus 9 dated journal pages (docs/store.js DEMO_DAYS),
# whatever today's date is (a dated page never overwrites the fixed sample journal page).
DEMO_NOTES = 27

# Sessions in every state the Vibe recap tells apart: working, turn finished, stopped on an error, working but
# slow to log, left open for days, and two that ended (one shipped, one cut off mid-turn).
RECAP_SEED = """() => {
  const s = window.cairn.state;
  const core = window.cairn.core;
  const pad = (n) => String(n).padStart(2, "0");
  const at = (minutesAgo) => {
    const d = new Date(Date.now() - minutesAgo * 60000);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const hm = (minutesAgo) => at(minutesAgo).slice(11);
  const note = (slug, meta, log, extra = "") => {
    const head = Object.entries(meta).map(([k, v]) => `${k}: ${v}`).join("\\n");
    const text = `---\\n${head}\\n---\\n\\n## Log\\n\\n${log.join("\\n")}\\n${extra}`;
    s.files.push({ path: `sessions/${slug}.md`, text, mtime: Date.now() });
  };
  note("a-working", { title: "Recap band for the Vibe page", type: "session", status: "live", started: at(42), source: "opencode", external: "ses_working1", folder: "E:/Projects/cairn", model: "Pega-Coding", effort: "high", tokens: 1843200, prompts: 6, turn: "working", turn_at: at(1) },
    [`- ${hm(42)} start: Recap band for the Vibe page`, `- ${hm(3)} prompt: now add the chips for each state`]);
  note("b-waiting", { title: "Fix the relay retry loop", type: "session", status: "live", started: at(95), source: "opencode", external: "ses_waiting1", folder: "E:/Projects/cairn", model: "Pega-Thinking", tokens: 912345, prompts: 4, turn: "done", turn_at: at(12) },
    [`- ${hm(95)} start: Fix the relay retry loop`, `- ${hm(12)} pass: pytest (14s) · 61 passed`],
    "\\n## Last reply\\n\\n> The retry loop now backs off 2, 4, 8 seconds and gives up after five tries.\\n> All 61 tests pass. Want me to commit and push, or tune the limits first?\\n");
  note("c-error", { title: "Import the Obsidian vault", type: "session", status: "live", started: at(130), source: "opencode", external: "ses_error1", folder: "E:/Projects/trailmix", model: "Pega-Chat", tokens: 220100, prompts: 2, turn: "failed", turn_at: at(31) },
    [`- ${hm(130)} start: Import the Obsidian vault`, `- ${hm(31)} fail: provider error · context length exceeded`]);
  note("d-quiet", { title: "Tolerance stack for the O-ring", type: "session", status: "live", started: at(200), agent: "Vector" },
    [`- ${hm(200)} start: Tolerance stack for the O-ring`, `- ${hm(75)} note: wrote the loop items from the 2D FAI`]);
  note("e-stale", { title: "Old experiment nobody closed", type: "session", status: "live", started: at(4000) },
    [`- ${hm(4000)} start: Old experiment nobody closed`]);
  note("f-shipped", { title: "Share card for weekly stats", type: "session", status: "shipped", started: at(400), ended: at(310), source: "opencode", external: "ses_done1", folder: "E:/Projects/cairn", model: "Pega-Coding", tokens: 5300000, prompts: 11, turn: "done", turn_at: at(310), commits: 3 },
    [`- ${hm(400)} start: Share card for weekly stats`, `- ${hm(310)} end: shipped · story and square cards render, share sheet opens on the phone`]);
  note("g-cut", { title: "Leaderboard query", type: "session", status: "done", started: at(600), ended: at(560), source: "opencode", external: "ses_cut1", folder: "E:/Projects/cairn", model: "Pega-Thinking", tokens: 480000, prompts: 3, turn: "working", turn_at: at(560) },
    [`- ${hm(600)} start: Leaderboard query`, `- ${hm(565)} prompt: rank by tokens, hours and streak`]);
  s.notes = s.files.map((f) => core.noteFromFile(f.path, f.text, f.mtime));
  window.cairn.rerender();
}"""

# One running session of the OpenBot bridge: OpenCode is the engine, the bot's workspace is the folder.
OPENBOT_SEED = """() => {
  const s = window.cairn.state;
  const pad = (n) => String(n).padStart(2, "0");
  const d = new Date(Date.now() - 120000);
  const at = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const meta = { title: "team-eval-7", type: "session", status: "live", started: at, source: "opencode", agent: "opencode", external: "ses_bot1",
    folder: "C:/Users/me/openbot-pegaai-bridge/workspace", turn: "working", turn_at: at };
  const head = Object.entries(meta).map(([k, v]) => `${k}: ${v}`).join("\\n");
  const text = `---\\n${head}\\n---\\n\\n## Log\\n\\n- ${at.slice(11)} start: team-eval-7\\n`;
  s.files.push({ path: "sessions/h-openbot.md", text, mtime: Date.now() });
  s.notes = s.files.map((f) => window.cairn.core.noteFromFile(f.path, f.text, f.mtime));
  window.cairn.rerender();
}"""

# One brain with the sessions of two computers, each with its machines/ note.
DEVICES_SEED = """() => {
  const s = window.cairn.state;
  const pad = (n) => String(n).padStart(2, "0");
  const ago = (minutes) => {
    const d = new Date(Date.now() - minutes * 60000);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const add = (path, meta, body) => {
    const head = Object.entries(meta).map(([k, v]) => `${k}: ${v}`).join("\\n");
    s.files.push({ path, text: `---\\n${head}\\n---\\n\\n${body}\\n`, mtime: Date.now() });
  };
  const session = (slug, machine, extra) =>
    add(`sessions/${slug}.md`, { title: slug, type: "session", source: "opencode", agent: "opencode", machine, ...extra }, `## Log\\n\\n- ${ago(5).slice(11)} start: ${slug}`);
  session("pc-live", "ASUS EXPERTBOOK", { status: "live", started: ago(4), turn: "working", turn_at: ago(1), tokens: 1200 });
  session("pc-done", "ASUS EXPERTBOOK", { status: "shipped", started: ago(90), ended: ago(60), tokens: 300 });
  session("mac-live", "MacBook Pro", { status: "live", started: ago(2), turn: "working", turn_at: ago(1), tokens: 50 });
  const machine = (slug, title, at, cpu) =>
    add(`machines/${slug}.md`, { title, type: "note", tags: "[machine]", machine_at: at, machine_cpu: cpu, machine_cores: 8, machine_ram_gb: 16, load: "light", load_cpu: 12, load_ram: 40 }, "Written by the watcher.");
  machine("asus-expertbook", "ASUS EXPERTBOOK", ago(3), "Intel Core Ultra 7");
  machine("macbook-pro", "MacBook Pro", ago(45), "Intel Core i7");
  s.notes = s.files.map((f) => window.cairn.core.noteFromFile(f.path, f.text, f.mtime));
  window.cairn.rerender();
}"""

# Two computers that share one brain: each with sessions, the watcher's note of its use, and (for today)
# a day report. A name with a comma in it has to survive.
DIARY_SEED = """() => {
  const s = window.cairn.state;
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const today = stamp(new Date());
  const yesterday = stamp(new Date(Date.now() - 86400000));
  const add = (path, meta, body) => {
    const head = Object.entries(meta).map(([k, v]) => `${k}: ${v}`).join("\\n");
    s.files.push({ path, text: `---\\n${head}\\n---\\n\\n${body}\\n`, mtime: Date.now() });
  };
  const session = (slug, machine, day, extra) =>
    add(`sessions/${day}-${slug}.md`, { title: slug, type: "session", agent: "Vector", machine, started: `${day}T00:01`, ...extra }, "## Log");
  session("pc-shipped", "ACME Roadrunner 14", today, { status: "shipped", ended: `${today}T00:02`, minutes: 50, tokens_in: 4000, tokens_out: 1000 });
  session("pc-live", "ACME Roadrunner 14", today, { status: "live" });
  session("mac-done", "MacBook Pro 15,4", today, { status: "done", ended: `${today}T00:02`, minutes: 20, tokens: 700 });
  session("mac-old", "MacBook Pro 15,4", yesterday, { status: "shipped", ended: `${yesterday}T00:02`, minutes: 5 });
  for (let n = 0; n < 14; n += 1) session(`pc-batch-${pad(n)}`, "ACME Roadrunner 14", yesterday, { status: "shipped", ended: `${yesterday}T00:02`, minutes: 3 });
  const hours = Array.from({ length: 24 }, (_, h) => (h === 9 || h === 10 ? 60 : 0)).join(" ");
  const activity = (slug, machine, lines) =>
    add(`machines/${slug}-activity.md`, { title: `${machine} activity`, type: "note", tags: "[activity]", machine, activity_at: `${today}T00:02` }, lines.join("\\n"));
  activity("acme-roadrunner-14", "ACME Roadrunner 14", [
    `- ${today} | screen 120 | awake 200 | cpu 30 | cpu_peak 90 | ram 60 | ram_peak 75 | heavy 4 | first 08:55 | last 11:00 | hours ${hours}`,
    `- ${yesterday} | screen 240 | awake 300 | cpu 20 | cpu_peak 50 | ram 55 | ram_peak 60 | heavy 0 | first 09:00 | last 13:00 | hours ${hours}`,
  ]);
  activity("macbook-pro-15-4", "MacBook Pro 15,4", [`- ${today} | screen 45 | awake 50 | cpu 12 | cpu_peak 40 | ram 70 | ram_peak 71 | heavy 0 | first 07:58 | last 08:45 | hours ${hours}`]);
  add(`Productivity Diary/${today}.md`, { title: `"Productivity Diary - ${today}"`, type: "note", date: today, tags: "[diary, productivity]", ai_tokens: 99000,
    devices: `\\n  - "ACME Roadrunner 14 | screen 150 | cpu 33 | cpu_peak 95 | ram 62 | ram_peak 80 | source tracker"` }, "# The day");
  s.notes = s.files.map((f) => window.cairn.core.noteFromFile(f.path, f.text, f.mtime));
  window.cairn.rerender();
}"""

# A brain an agent works in: five tasks that wait for the owner, a dated update that opens with a picture, one
# named for a time that has not come yet, a run of three automatic notes, and one note whose title is long
# enough to wrap on a phone.
HOME_SEED = """() => {
  const s = window.cairn.state;
  const pad = (n) => String(n).padStart(2, "0");
  const d = new Date();
  const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const add = (path, text) => s.files.push({ path, text, mtime: Date.now() });
  for (const n of [1, 2, 3, 4, 5]) {
    add(`tasks/needs-${n}.md`, `---\\ntitle: Sign in to store account ${n} once so the upload can run\\ntype: task\\nstatus: todo\\ntags: [needs-me]\\ncreated: ${day}\\n---\\n\\nOnly you have the password.\\n`);
  }
  add(`Update Log/${day} 0905 home page shows what matters.md`, "![[shots/home.png]]\\n\\nAsked: a Home that leads with what waits for me.\\n");
  add(`Update Log/${day} 2359 named by a wrong clock.md`, "Written this morning, named for tonight.\\n");
  for (const n of [1, 2, 3]) add(`Update Log/${day} 080${n} lab_writer_run_${n}.md`, `Run ${n} of the nightly writer.\\n`);
  add("notes/long.md", "---\\ntitle: A note whose title is long enough to take three lines on a phone, the way the titles of real work notes usually are, with a path like E:/Projects/somewhere/deep\\ntype: note\\n---\\n\\nBody.\\n");
  s.notes = s.files.map((f) => window.cairn.core.noteFromFile(f.path, f.text, f.mtime));
  window.cairn.rerender();
}"""

# Recap rows that run into each other or out of their own row: [] when the page is laid out right.
RECAP_COLLISIONS = """() => {
  const bad = [];
  const rows = [...document.querySelectorAll("#view .page-recap li")];
  rows.forEach((row, index) => {
    const box = row.getBoundingClientRect();
    for (const el of row.querySelectorAll("*")) {
      const r = el.getBoundingClientRect();
      if (r.height && (r.bottom > box.bottom + 1 || r.top < box.top - 1 || r.right > box.right + 1)) bad.push(`${el.className || el.tagName} leaves its row: ${el.textContent.trim().slice(0, 40)}`);
    }
    const next = rows[index + 1];
    if (next && next.parentElement === row.parentElement && next.getBoundingClientRect().top < box.bottom - 1) bad.push(`row runs into the next: ${row.textContent.trim().slice(0, 40)}`);
  });
  return bad;
}"""

# A brain read from GitHub: no file has a time, so only a note's own dates say when it changed.
NO_FILE_TIMES_SEED = """() => {
  const s = window.cairn.state;
  const pad = (n) => String(n).padStart(2, "0");
  const dayAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  s.files = [
    { path: "tasks/aa-undated.md", text: "---\\ntitle: A task with no date anywhere, with a name long enough to need the whole row on a phone and then some\\ntype: task\\nstatus: todo\\n---\\n", mtime: 0 },
    { path: "tasks/bb-yesterday.md", text: `---\\ntitle: Asked yesterday\\ntype: task\\nstatus: todo\\ncreated: ${dayAgo(1)}\\n---\\n`, mtime: 0 },
    { path: `tasks/${dayAgo(0)} 0001 named for today.md`, text: "---\\ntype: task\\nstatus: todo\\n---\\nBody.\\n", mtime: 0 },
    { path: "notes/repo.md", text: "---\\ntitle: A repository note\\ntype: note\\nsource: github-sync\\n---\\n", mtime: 0 },
    { path: "tasks/from-code.md", text: `---\\ntitle: Handle the empty file\\ntype: task\\nstatus: todo\\nsource: src/app.py:12\\ncreated: ${dayAgo(3)}\\n---\\n`, mtime: 0 },
    { path: `journal/${dayAgo(0)}.md`, text: "- 09:00 Read [[A note whose name is far longer than a phone is wide so the chip has to shorten it somewhere]] with @sam #beta\\n", mtime: 0 },
  ];
  s.notes = s.files.map((f) => window.cairn.core.noteFromFile(f.path, f.text, f.mtime));
  window.cairn.rerender();
}"""

# A PC to chat with: remembers what was sent instead of running a model.
CHAT_STUB = """() => {
  const s = window.cairn.state;
  window.__sent = [];
  s.store.chat = async () => ({ available: true, models: ["Pega-Chat"], chats: [] });
  s.store.chatAction = async (action, body) => {
    window.__sent.push({ action, ...body });
    return { id: "20261114-00000001", title: body.text, status: "idle", messages: [{ role: "user", text: body.text }] };
  };
  s.store.chatThread = async (id) => ({ id, title: "Chat", status: "idle", messages: [] });
  window.cairn.rerender();
}"""


# A PC with three chats (one still answering) and an autopilot with three goals, for the pages that need one.
PC_STUB = """() => {
  const s = window.cairn.state;
  const now = Math.floor(Date.now() / 1000);
  const chats = [
    { id: "20261119-00000001", title: "Why is the relay slow?", status: "idle", updated: now - 300 },
    { id: "20261119-00000002", title: "Draft the weekly summary", status: "running", updated: now - 20 },
    { id: "20261118-00000003", title: "Explain tolerance stack-up", status: "idle", updated: now - 200000 },
  ];
  s.store.chat = async () => ({ available: true, models: ["Pega-Chat", "Pega-Coding"], chats });
  s.store.chatAction = async (action, body) => ({ id: chats[0].id, title: body.text, status: "idle", messages: [{ role: "user", text: body.text }] });
  s.store.chatThread = async (id) => ({ id, title: chats.find((c) => c.id === id).title, status: "idle",
    messages: [{ role: "user", text: "Why is the relay slow?" }, { role: "assistant", text: "It polls every 20 seconds." }] });
  const stamp = new Date().toISOString().slice(0, 16).replace("T", " ");
  const autopilot = {
    settings: { enabled: true, engine: "opencode", command: "", opencode_flags: "", workdir_root: "", round_timeout_min: 30,
      max_rounds_per_goal: 8, improve_rounds_after_done: 1, auto_pull_queue: true },
    worker: { worker: "here", current: "j1" },
    engine_found: true,
    state_dir: "~/.cairn/autopilot",
    jobs: [
      { id: "j1", goal: "Add dark mode to the settings page", status: "running", rounds: 2, updated: stamp, workdir: "E:/Projects/demo", next_hint: "wire the toggle" },
      { id: "j2", goal: "Write release notes for v1", status: "blocked", rounds: 1, updated: stamp, workdir: "E:/Projects/demo", blocked_reason: "Which version number?" },
      { id: "j3", goal: "Tidy the README", status: "done", rounds: 3, updated: stamp, workdir: "E:/Projects/demo", summary: "README rewritten, links checked" },
    ],
  };
  s.store.autopilot = async () => autopilot;
  s.store.autopilotLog = async () => ({ size: 23, text: "round 2: editing files\\n" });
  s.store.autopilotAction = async () => autopilot.jobs[0];
  window.cairn.rerender();
}"""


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = start_static_server()
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()

    def open(self, query="?demo=1", viewport=(1440, 900), theme="light", wait="#nav button", setup=None):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        self.github_log = install_github_mock(context)
        if setup:
            setup(self.github_log)
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('cairn.theme', '{theme}')")
        page.goto(self.url + query)
        page.wait_for_selector(wait)
        self.addCleanup(context.close)
        return page

    def note_text(self, page, path):
        return page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p)?.text ?? null", path)

    def go(self, page, view):
        go_to(page, view)
        page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)

    def sample_week(self, page):
        page.click('[data-action="vibe-sample"]')
        page.wait_for_function("() => window.cairn.state.notes.filter((n) => n.type === 'session').length === 4")
        page.wait_for_selector(".vibe-overview")

    def start_session(self, page, title):
        page.click('[data-action="vibe-start"]')
        page.fill(".modal input", title)
        page.click('.modal button[type="submit"]')
        page.wait_for_selector(".ov-lane")
        return page.evaluate("(t) => window.cairn.state.notes.find((n) => n.type === 'session' && n.title === t).path", title)

    def overview_tiles(self, page):
        return page.eval_on_selector_all(
            "button.ov-tile",
            """nodes => nodes.map((n) => ({
                key: n.dataset.outcome,
                count: Number(n.querySelector('.ov-count').dataset.count),
                share: parseInt(n.querySelector('.ov-share').textContent, 10),
                pressed: n.getAttribute('aria-pressed') === 'true',
                off: n.disabled,
            }))""",
        )

    def test_notes_list_pages_big_vaults(self):
        page = self.open()
        page.evaluate("""() => {
            const s = window.cairn.state;
            const extra = Array.from({ length: 650 }, (_, i) => ({
                ...s.notes[0], path: `notes/bulk-${i}.md`, slug: `bulk-${i}`, title: `Bulk ${i}`, tags: [], mtime: i,
            }));
            s.notes = s.notes.concat(extra);
        }""")
        self.go(page, "notes")
        total = page.evaluate("() => window.cairn.state.notes.length")
        self.assertEqual(page.locator("#notes-list .row, #notes-list > *").count(), 300)
        self.assertIn(f"300 of {total}", page.inner_text(".list-more"))
        page.click('[data-action="notes-more"]')
        self.assertEqual(page.locator("#notes-list > *").count(), 600)
        page.click('[data-action="notes-more"]')
        self.assertEqual(page.locator("#notes-list > *").count(), total)
        self.assertEqual(page.locator(".list-more").count(), 0)
        # A search starts again from one page.
        page.fill("#notes-search", "bulk")
        page.wait_for_function("() => document.querySelectorAll('#notes-list > *').length === 300")
        self.assertEqual(self.errors, [])

    def test_context_view_offers_mcp_setup_for_each_assistant(self):
        page = self.open()
        self.go(page, "context")
        # Outside `cairn serve` the page can't know local paths, so it points at the CLI instead.
        self.assertIn("cairn mcp --print-config", page.inner_text(".mcp-card pre"))
        self.assertEqual(page.locator("[data-mcp-client]").count(), 0)
        # In server mode /api/info carries the launch entry; every client gets a ready-to-paste snippet.
        entry = {
            "name": "cairn-demo",
            "command": "C:\\Program Files\\Python313\\python.exe",
            "args": ["-m", "cairn", "--root", "C:\\work\\my app", "mcp"],
            "env": {"PYTHONPATH": "C:\\tools\\cairn"},
        }
        page.evaluate("(e) => { Object.assign(window.cairn.state.info, { mode: 'server', mcp: e }); window.cairn.rerender(); }", entry)
        snippet = json.loads(page.inner_text(".mcp-card pre"))
        self.assertEqual(snippet["mcpServers"]["cairn-demo"]["args"], entry["args"])
        page.click('[data-mcp-client="vscode"]')
        snippet = json.loads(page.inner_text(".mcp-card pre"))
        self.assertEqual(snippet["servers"]["cairn-demo"]["type"], "stdio")
        self.assertIn(".vscode/mcp.json", page.inner_text(".mcp-card"))
        page.click('[data-mcp-client="opencode"]')
        snippet = json.loads(page.inner_text(".mcp-card pre"))
        self.assertEqual(snippet["mcp"]["cairn-demo"]["command"][0], entry["command"])
        self.assertEqual(snippet["mcp"]["cairn-demo"]["environment"], entry["env"])
        page.click('[data-mcp-client="claude-code"]')
        line = page.inner_text(".mcp-card pre")
        self.assertTrue(line.startswith("claude mcp add cairn-demo --env PYTHONPATH="), line)
        self.assertIn(' -- "C:\\Program Files\\Python313\\python.exe" -m cairn --root "C:\\work\\my app" mcp', line)
        self.assertEqual(page.get_attribute('[data-mcp-client="claude-code"]', "aria-pressed"), "true")
        self.assertEqual(self.errors, [])

    def test_demo_brain_is_the_same_whatever_the_date(self):
        import datetime

        # 2026-11-05 used to clash with the fixture's fixed 2026-11-04 page and drop a note.
        for day in ("2026-11-05", "2026-11-08", "2027-01-01"):
            context = self.browser.new_context()
            install_github_mock(context)
            page = context.new_page()
            page.clock.install(time=datetime.datetime.fromisoformat(day + "T10:00:00"))
            page.goto(self.url + "?demo=1")
            page.wait_for_selector("#nav button")
            notes = page.evaluate("() => window.cairn.state.notes.length")
            journal = page.evaluate("() => window.cairn.state.files.filter((f) => f.path.startsWith('journal/')).length")
            context.close()
            self.assertEqual((day, notes, journal), (day, DEMO_NOTES, 10))

    def test_every_view_renders_without_errors(self):
        page = self.open()
        for view in VIEWS:
            self.go(page, view)
            self.assertTrue(page.inner_text("#view").strip(), view)
            if view == "map":
                self.assertEqual(page.locator(".map-node").count(), DEMO_NOTES)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ sidebar hubs and the tabs inside them

    def nav_state(self, page):
        return page.evaluate("""() => ({
            view: window.cairn.state.view,
            hubs: [...document.querySelectorAll('#nav button[aria-current="page"]')].map((b) => b.dataset.hub),
            tabs: [...document.querySelectorAll('.hub-tabs .hub-tab')].map((b) => b.dataset.go),
            tab: document.querySelector('.hub-tabs [aria-current="page"]')?.dataset.go ?? null,
        })""")

    def test_sidebar_has_six_entries_and_no_group_labels(self):
        page = self.open()
        entries = page.eval_on_selector_all("#nav button", "els => els.map((e) => e.querySelector('span').textContent)")
        self.assertEqual(entries, ["Today", "Journal", "Chat", "Map", "Build", "People"])
        self.assertEqual(page.locator("#nav .nav-label").count(), 0)
        self.assertEqual(page.eval_on_selector_all("#nav button", "els => els.map((e) => e.dataset.hub)"), list(HUBS))
        self.assertEqual(self.errors, [])

    def test_every_old_address_opens_its_page_inside_a_hub(self):
        page = self.open()
        for hub, views in {**HUBS, **HIDDEN_HUBS}.items():
            tabs = views if len(views) > 1 else []
            for view in views:
                page.goto(f"{self.url}?demo=1#/{view}")
                page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)
                expected = {"view": view, "hubs": [] if hub in HIDDEN_HUBS else [hub], "tabs": tabs, "tab": view if tabs else None}
                self.assertEqual(self.nav_state(page), expected)
                self.assertTrue(page.inner_text("#view").strip(), view)
        self.assertEqual(sum(len(views) for views in {**HUBS, **HIDDEN_HUBS}.values()), 20)
        self.assertEqual(self.errors, [])

    def test_a_sidebar_entry_reopens_the_tab_used_last(self):
        page = self.open()
        self.assertEqual(page.get_attribute('#nav [data-hub="build"]', "data-go"), "vibe")
        self.go(page, "projects")
        self.go(page, "home")
        page.click('#nav [data-hub="build"]')
        page.wait_for_function("() => window.cairn.state.view === 'projects'")
        self.assertEqual(self.nav_state(page)["tab"], "projects")
        # It is remembered in this browser, so it is still the choice after a reload.
        self.go(page, "home")
        page.reload()
        page.wait_for_selector("#nav button")
        self.assertEqual(page.evaluate("() => window.cairn.state.view"), "home")
        self.assertEqual(page.get_attribute('#nav [data-hub="build"]', "data-go"), "projects")
        self.assertEqual(self.errors, [])

    def test_sidebar_entries_and_tabs_are_buttons_that_keep_the_keyboard_focus(self):
        page = self.open()
        self.go(page, "board")
        page.focus('.hub-tabs [data-go="ideas"]')
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.cairn.state.view === 'ideas'")
        focused = page.evaluate("() => [document.activeElement.className, document.activeElement.dataset.go]")
        self.assertEqual(focused, ["hub-tab active", "ideas"])
        page.focus('#nav [data-hub="map"]')
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.cairn.state.view === 'map'")
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.hub"), "map")
        reachable = page.eval_on_selector_all("#nav button, .hub-tabs .hub-tab", "els => els.map((e) => e.tagName === 'BUTTON' && e.tabIndex === 0)")
        self.assertEqual(reachable, [True] * 8)
        self.assertEqual(self.errors, [])

    def test_tabs_fit_a_phone_and_the_drawer_closes_after_a_choice(self):
        page = self.open(viewport=(390, 844))
        for view in ("obsidian", "decisions", "code"):
            page.goto(f"{self.url}?demo=1#/{view}")
            page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - innerWidth"), 0, view)
            strip = page.locator(".hub-tabs").bounding_box()
            self.assertGreaterEqual(strip["x"], 0, view)
            self.assertLessEqual(strip["x"] + strip["width"], 390, view)
            self.assertTrue(page.is_visible(f'.hub-tabs [data-go="{view}"]'), view)
        page.click("#menu-btn")
        page.click('#nav [data-hub="map"]')
        page.wait_for_function("() => window.cairn.state.view === 'map'")
        self.assertFalse(page.evaluate("() => document.querySelector('#app').classList.contains('nav-open')"))
        # Opened and not used: a tap anywhere on the page closes it again, and the page under it stays put.
        page.click("#menu-btn")
        page.wait_for_function("() => document.querySelector('#app').classList.contains('nav-open')")
        page.mouse.click(370, 600)
        page.wait_for_function("() => !document.querySelector('#app').classList.contains('nav-open')")
        self.assertEqual(page.evaluate("() => window.cairn.state.view"), "map")
        self.assertEqual(self.errors, [])

    def test_palette_reaches_every_page_and_lands_inside_its_hub(self):
        page = self.open()
        labels = page.evaluate("() => import('./state.js').then((m) => m.VIEWS.map((v) => [v.id, v.label]))")
        self.assertEqual(sorted(view for view, _ in labels), sorted(view for views in {**HUBS, **HIDDEN_HUBS}.values() for view in views))
        page.keyboard.press("Control+k")
        page.wait_for_selector(".palette input")
        for view, label in labels:
            # The palette lists six commands at a time, so ask for each page by name.
            page.fill(".palette input", f"Go to {label}")
            shown = page.eval_on_selector_all(".palette-item .label", "els => els.map((e) => e.textContent.trim())")
            self.assertIn(f"Go to {label}", shown, view)
        page.fill(".palette input", "Go to Proof")
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.cairn.state.view === 'proof'")
        self.assertEqual(self.nav_state(page), {"view": "proof", "hubs": ["people"], "tabs": ["people", "proof"], "tab": "proof"})
        self.assertEqual(self.errors, [])

    def test_vibe_session_feedback_and_stats(self):
        page = self.open()
        self.go(page, "vibe")
        self.assertIn("Nothing live", page.inner_text(".vibe-live.idle"))
        # Blocks with nothing in them stay off the page: no feedback yet, so no Feedback block and no rating figures.
        self.assertEqual(page.locator('.vibe-panel[data-panel="feedback"]').count(), 0)
        self.assertNotIn("feedback", page.inner_text(".vibe-figures"))
        page.click('[data-action="vibe-start"]')
        page.fill(".modal input", "Add dark mode")
        page.click('.modal button[type="submit"]')
        # The session you start is a row of the live recap, opened to its log form. There is no second card.
        page.wait_for_selector(".rc-row.open .vibe-log-form")
        self.assertEqual(page.locator(".vibe-live").count(), 1)
        self.assertEqual(page.locator('.vibe-panel[data-panel="live"]').count(), 0)
        self.assertEqual(page.locator('.vibe-panel[data-panel="hook"]').count(), 0, "the setup hint goes once a session exists")
        self.assertEqual(page.inner_text('#nav [data-views~="vibe"] .count'), "live")
        path = page.evaluate("window.cairn.state.notes.find((n) => n.type === 'session').path")
        self.assertRegex(path, r"^sessions/\d{4}-\d{2}-\d{2}-add-dark-mode\.md$")

        page.select_option(".vibe-log-form select", "pass")
        page.fill(".vibe-log-form input", "pytest: 12 passed")
        page.press(".vibe-log-form input", "Enter")
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('pass: pytest: 12 passed')", arg=path)
        self.assertIn("pytest: 12 passed", page.inner_text(".vibe-log"))

        # The first piece of feedback comes from the capture bar; from then on the Feedback block is there.
        page.fill("#capture-input", "fb: first impression is good")
        page.press("#capture-input", "Enter")
        page.wait_for_selector("#fb-form")
        page.fill("#fb-text", "The toggle is hard to find")
        page.select_option("#fb-form select[name=rating]", "3")
        page.fill("#fb-form input[name=from]", "@alice")
        page.click('#fb-form button[type="submit"]')
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'feedback/the-toggle-is-hard-to-find.md')")
        feedback = self.note_text(page, "feedback/the-toggle-is-hard-to-find.md")
        self.assertIn("rating: 3", feedback)
        self.assertIn('from: "@alice"', feedback)
        self.assertIn("about: \"[[", feedback)

        page.click('[data-action="vibe-end"][data-result="shipped"]')
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('status: shipped')", arg=path)
        text = self.note_text(page, path)
        self.assertIn("ended: ", text)
        self.assertIn("end: shipped", text)
        figures = page.inner_text(".vibe-figures")
        self.assertIn("100%", figures)  # ship rate and checks green
        self.assertIn("3.0", figures)  # average rating
        self.assertEqual(page.locator('#nav [data-views~="vibe"] .count').count(), 0)

        page.select_option('[data-fb-status="feedback/the-toggle-is-hard-to-find.md"]', "addressed")
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'feedback/the-toggle-is-hard-to-find.md').text.includes('status: addressed')")
        self.assertEqual(self.errors, [])

    def test_vibe_skills_and_parallel_sessions(self):
        page = self.open()
        self.go(page, "vibe")
        for title in ("Map tiles", "Share link"):
            page.click('[data-action="vibe-start"]')
            page.fill(".modal input", title)
            page.click('.modal button[type="submit"]')
            page.wait_for_function("(t) => window.cairn.state.notes.some((n) => n.type === 'session' && n.title === t)", arg=title)
        page.wait_for_function("() => document.querySelectorAll('.rc-row.open > .vibe-live').length === 2")
        self.assertEqual(page.inner_text('#nav [data-views~="vibe"] .count'), "2 live")
        titles = page.eval_on_selector_all(".rc-live > .rc-row .rc-title", "els => els.map((e) => e.innerText.trim())")
        self.assertEqual(sorted(titles), ["Map tiles", "Share link"])
        # A row folds to its one-line summary and opens again from the same button.
        newest = page.evaluate("window.cairn.state.notes.find((n) => n.title === 'Share link').path")
        toggle = f'.rc-toggle[data-path="{newest}"]'
        page.click(toggle)
        page.wait_for_function("() => document.querySelectorAll('.rc-row.open').length === 1")
        self.assertEqual(page.get_attribute(toggle, "aria-expanded"), "false")
        self.assertEqual(page.evaluate("() => document.activeElement.className"), "rc-toggle")
        page.click(toggle)
        page.wait_for_function("() => document.querySelectorAll('.rc-row.open').length === 2")

        # A note that starts with a slash command is logged as a skill run, on the session whose form was used.
        older = page.evaluate("window.cairn.state.notes.find((n) => n.title === 'Map tiles').path")
        form = f'.vibe-log-form[data-session="{older}"]'
        page.fill(f"{form} input", "/refine app trailmix · 6.5 -> 8.7")
        page.press(f"{form} input", "Enter")
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('skill: /refine app trailmix')", arg=older)
        page.fill(f"{form} input", "/council tooltips")
        page.press(f"{form} input", "Enter")
        page.wait_for_selector(".skill-chip")
        chips = page.inner_text(".vibe-skills")
        self.assertIn("/refine", chips)
        self.assertIn("/council", chips)

        page.click(f'[data-action="vibe-end"][data-result="partial"][data-path="{older}"]')
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('status: partial')", arg=older)
        self.assertEqual(page.inner_text('#nav [data-views~="vibe"] .count'), "live")
        self.assertEqual(page.locator(".vibe-live").count(), 1)
        self.assertEqual(self.errors, [])

    def test_demo_can_load_a_sample_week(self):
        page = self.open()
        self.go(page, "vibe")
        page.click('[data-action="vibe-sample"]')
        page.wait_for_function("() => window.cairn.state.notes.filter((n) => n.type === 'session').length === 4")
        self.assertEqual(page.evaluate("window.cairn.state.notes.filter((n) => n.type === 'feedback').length"), 3)
        self.assertIn("4", page.inner_text(".vibe-figures"))
        self.assertEqual(page.locator(".skill-chip").count(), 4)
        self.assertEqual(page.locator('[data-action="vibe-sample"]').count(), 0)
        self.assertEqual(self.errors, [])

    def test_vibe_blocks_fold_move_and_follow_the_brain_file(self):
        page = self.open()
        self.go(page, "vibe")
        self.sample_week(page)
        order = lambda: page.eval_on_selector_all(".vibe-panel", "els => els.map((e) => e.dataset.panel)")
        folded = lambda: page.eval_on_selector_all(".vibe-panel.folded", "els => els.map((e) => e.dataset.panel)")
        self.assertEqual(order()[0], "recap")
        self.assertNotIn("hook", order())
        self.assertNotIn("live", order())
        self.assertEqual(folded(), [], "every block that has something to show starts open")

        # Fold: the block keeps one line with a summary and no body.
        page.click('.vp-toggle[data-panel="overview"]')
        page.wait_for_selector('.vibe-panel.folded[data-panel="overview"]')
        self.assertEqual(page.locator('[data-panel="overview"] .vp-body').count(), 0)
        self.assertIn("4 sessions", page.inner_text('[data-panel="overview"] .vp-sum'))
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.panel"), "overview")

        # Move with the arrows, then drag a block onto another.
        page.click('.vp-move[data-panel="sessions"][data-step="-1"]')
        page.wait_for_function("() => document.querySelector('.vibe-panel[data-panel=sessions]').previousElementSibling.dataset.panel === 'figures'")
        grip = page.locator('.vp-grip[data-panel="feedback"]')
        grip.scroll_into_view_if_needed()
        box = grip.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"], box["y"] - 20, steps=5)
        head = page.locator(".vibe-panel:first-child .vp-head")
        head.scroll_into_view_if_needed()
        box = head.bounding_box()
        page.mouse.move(box["x"] + 40, box["y"] + 3, steps=10)
        page.mouse.up()
        page.wait_for_function("() => document.querySelector('.vibe-panel').dataset.panel === 'feedback'")

        # The layout lands in the brain, and a reload (or another device reading the file) shows it.
        page.wait_for_function("""() => {
            const text = window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '';
            const saved = (text.match(/^vibe-order: (.*)$/m)?.[1] || '').split(', ');
            const shown = [...document.querySelectorAll('.vibe-panel')].map((el) => el.dataset.panel);
            return saved.filter((id) => shown.includes(id)).join() === shown.join();
        }""")
        text = self.note_text(page, "CAIRN-LAYOUT.md")
        self.assertRegex(text, r"vibe-folded: .*overview")
        self.assertFalse(page.evaluate("() => window.cairn.state.notes.some((n) => n.path === 'CAIRN-LAYOUT.md')"), "the layout is not a note")
        page.evaluate("() => localStorage.removeItem('cairn.vibeLayout')")
        page.reload()
        page.wait_for_selector("#nav button")
        page.wait_for_selector(".vibe-panel")
        self.assertEqual(order()[0], "feedback")
        self.assertIn("overview", folded())

        # A lane jump opens a folded live recap for this visit only; the saved layout keeps it folded.
        page.click('[data-action="vibe-layout-reset"]')
        page.wait_for_function("() => document.querySelector('.vibe-panel').dataset.panel === 'recap'")
        path = self.start_session(page, "Layout check")
        page.click('.vp-toggle[data-panel="recap"]')
        page.wait_for_selector('.vibe-panel.folded[data-panel="recap"]')
        page.click(".ov-lane [data-action=vibe-lane]")
        page.wait_for_selector(f'.vibe-panel[data-panel="recap"]:not(.folded) .rc-row.open[data-path="{path}"] .vibe-live')
        page.wait_for_function("() => /vibe-folded: .*recap/.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")
        self.assertEqual(self.errors, [])

    def test_vibe_outcome_chips_and_folding_days(self):
        page = self.open()
        self.go(page, "vibe")
        figures = page.inner_text(".vibe-figures")
        self.assertIn("no checks logged yet", figures)  # a dash always says why
        self.assertNotIn("rating", figures)  # figures with nothing behind them are left out
        self.assertNotIn("feedback", figures)
        tile = page.locator('.vibe-figures [data-action="vibe-why"]').first
        tile.click()  # a tap shows the same explanation the hover tooltip gives
        page.wait_for_selector(".vibe-figures .figure.open .why")
        self.assertEqual(page.locator(".vibe-figures .figure.open .why").inner_text(), tile.get_attribute("title"))
        page.keyboard.press("Enter")  # and the keyboard closes it again
        page.wait_for_function("() => !document.querySelector('.vibe-figures .why')")
        page.click('[data-action="vibe-sample"]')
        page.wait_for_function("() => window.cairn.state.notes.filter((n) => n.type === 'session').length === 4")
        rows = page.locator(".session-row").count()
        chip = page.locator('.vibe-outcomes [data-action="vibe-outcome"]').first
        outcome = chip.get_attribute("data-outcome")
        count = int(chip.locator("b").inner_text())
        self.assertLess(count, rows, "the sample week mixes outcomes")

        chip.click()
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=count)
        self.assertEqual(page.locator(f".session-row .dot.s-{outcome}").count(), count)
        self.assertEqual(page.locator('.vibe-outcomes [aria-pressed="true"]').count(), 1)
        self.assertIn("Show every outcome", page.inner_text(".vibe-sessions"))
        page.click(f'.vibe-outcomes [data-outcome="{outcome}"]')  # pressing it again shows everything
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)

        head = page.locator(".day-head").first
        in_day = page.locator(".day-group").first.locator(".session-row").count()
        self.assertEqual(head.get_attribute("aria-expanded"), "true")
        head.click()
        page.wait_for_function("() => document.querySelector('.day-head').getAttribute('aria-expanded') === 'false'")
        self.assertEqual(page.locator(".session-row").count(), rows - in_day)
        self.assertEqual(page.evaluate("document.activeElement.classList.contains('day-head')"), True)
        page.keyboard.press("Enter")  # a folded day opens again from the keyboard
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)

        # Fold every day at once, then unfold them all again.
        page.click('[data-action="vibe-fold-all"]')
        page.wait_for_function("() => document.querySelectorAll('.session-row').length === 0")
        self.assertEqual(page.locator('.day-head[aria-expanded="true"]').count(), 0)
        self.assertIn("Unfold all days", page.inner_text(".vibe-sessions"))
        page.click('[data-action="vibe-fold-all"]')
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)

        # A bar in the chart opens just that day, even when every day is folded.
        page.click('[data-action="vibe-fold-all"]')
        page.wait_for_function("() => document.querySelectorAll('.session-row').length === 0")
        bar = page.locator('.vibe-activity [data-action="vibe-jump"]').last
        day = bar.get_attribute("data-day")
        bar.click()
        page.wait_for_function("(d) => document.querySelector(`.day-head[data-day='${d}']`)?.getAttribute('aria-expanded') === 'true'", arg=day)
        self.assertEqual(page.locator('.day-head[aria-expanded="true"]').count(), 1)
        self.assertGreater(page.locator(".session-row").count(), 0)
        self.assertEqual(page.evaluate("document.activeElement.dataset.day"), day)
        # The jumped-to day offers a way back up, which lands on the bar it came from.
        page.click(f'.day-back[data-day="{day}"]')
        page.wait_for_function("() => !document.querySelector('.day-back')")
        self.assertEqual(page.evaluate("document.activeElement.dataset.action"), "vibe-jump")
        self.assertEqual(page.evaluate("document.activeElement.dataset.day"), day)
        page.click('[data-action="vibe-fold-all"]')  # back to every day open for the checks below
        page.click('[data-action="vibe-fold-all"]')
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)

        # With history, the idle card is one line that points at the last session.
        idle = page.inner_text(".vibe-live.idle")
        self.assertIn("Last session:", idle)
        self.assertEqual(page.locator(".vibe-live.idle [data-open]").count(), 1)

        # The chart keeps "no git" apart from "no code change", like the chips do.
        has_no_git = page.locator('.vibe-outcomes [data-outcome="unknown"]').count() > 0
        legend = page.inner_text(".vibe-activity header")
        self.assertIn("no code change", legend)
        self.assertNotIn("no code change or no git", legend)
        self.assertEqual("no git" in legend, has_no_git)
        self.assertEqual(page.locator(".vibe-activity .bar.nogit").count() > 0, has_no_git)
        # Each bar's tooltip names the day and splits it by outcome, like the chips.
        tips = page.eval_on_selector_all(".vibe-activity svg g title", "nodes => nodes.map((n) => n.textContent)")
        busy = [tip for tip in tips if " 0 sessions" not in tip and "session" in tip and "·" in tip]
        self.assertTrue(busy, tips)
        self.assertTrue(any(word in busy[0] for word in ("shipped", "no code change", "no git", "live", "partial", "dropped")), busy[0])
        self.assertEqual(self.errors, [])

    def test_vibe_overview_counts_every_status_and_filters_the_list(self):
        page = self.open()
        self.go(page, "vibe")
        self.assertEqual(page.locator(".vibe-overview").count(), 0)  # nothing to count yet: the empty card stands in
        self.sample_week(page)
        tiles = self.overview_tiles(page)
        self.assertEqual([t["key"] for t in tiles], ["live", "shipped", "partial", "dropped", "done", "unknown"])
        self.assertEqual(sum(t["count"] for t in tiles), 4)  # every session sits under exactly one status
        self.assertEqual(sum(t["share"] for t in tiles), 100)
        for tile in tiles:
            self.assertEqual(tile["off"], tile["count"] == 0, tile)  # an empty status shows 0 and cannot be pressed
            self.assertFalse(tile["pressed"], tile)
        self.assertEqual(page.locator(".ov-bar .ov-seg").count(), 6)
        self.assertIn("4 sessions", page.inner_text(".vibe-overview > header"))
        # The numbers count up as the page opens and land on the real counts.
        page.wait_for_function("() => [...document.querySelectorAll('.ov-count')].every((n) => n.textContent === n.dataset.count)")
        count_of = {t["key"]: t["count"] for t in tiles}
        chips = page.eval_on_selector_all('.vibe-outcomes [data-action="vibe-outcome"]', "nodes => nodes.map((n) => [n.dataset.outcome, Number(n.querySelector('b').textContent)])")
        self.assertTrue(chips)
        for outcome, count in chips:
            self.assertEqual(count_of[outcome], count, outcome)  # the tiles and the legend chips agree
        rows = page.locator(".session-row").count()
        shipped = count_of["shipped"]
        self.assertTrue(0 < shipped < rows, count_of)

        tile = '.ov-tile[data-outcome="shipped"]'
        page.click(tile)
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=shipped)
        self.assertEqual(page.locator(".session-row .dot.s-shipped").count(), shipped)
        self.assertEqual(page.get_attribute(tile, "aria-pressed"), "true")
        self.assertEqual(page.locator('.ov-tile[aria-pressed="true"]').count(), 1)
        pressed_chips = page.eval_on_selector_all('.vibe-outcomes [aria-pressed="true"]', "nodes => nodes.map((n) => n.dataset.outcome)")
        self.assertEqual(pressed_chips, ["shipped"])  # the same filter the legend chip sets
        self.assertEqual(page.locator(".ov-peek li").count(), shipped)
        page.wait_for_function("(sel) => document.activeElement.matches(sel)", arg=tile)  # focus stays on the tile
        page.keyboard.press("Enter")  # pressing it again clears the filter
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)
        self.assertEqual(page.locator('.ov-tile[aria-pressed="true"], .vibe-outcomes [aria-pressed="true"]').count(), 0)
        self.assertEqual(page.locator(".ov-peek").count(), 0)

        # A legend chip presses the matching tile, and "Clear filter" lets go of it.
        page.click('.vibe-outcomes [data-outcome="partial"]')
        page.wait_for_selector('.ov-tile[data-outcome="partial"][aria-pressed="true"]')
        self.assertEqual(page.locator(".session-row").count(), count_of["partial"])
        page.click(".ov-clear")
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)
        self.assertEqual(page.locator('.ov-tile[aria-pressed="true"]').count(), 0)
        self.assertEqual(self.errors, [])

    def test_vibe_overview_follows_a_live_session(self):
        page = self.open()
        self.go(page, "vibe")
        self.sample_week(page)
        self.assertEqual(page.locator(".ov-lane").count(), 0)  # lanes are only for sessions that are running
        self.assertTrue(page.is_disabled('.ov-tile[data-outcome="live"]'))
        shipped = {t["key"]: t["count"] for t in self.overview_tiles(page)}["shipped"]
        page.wait_for_timeout(800)  # let the opening draw-in finish, so what follows is a change to a settled page

        path = self.start_session(page, "Wire the overview")
        lane = f'.ov-lane[data-path="{path}"]'
        page.wait_for_selector(lane)
        self.assertEqual(page.locator(".ov-lane").count(), 1)
        self.assertEqual(page.inner_text(f"{lane} .ov-title"), "Wire the overview")
        self.assertIn("1 live now", page.inner_text(".vibe-overview > header"))
        tiles = self.overview_tiles(page)
        self.assertEqual({t["key"]: t["count"] for t in tiles}["live"], 1)
        self.assertEqual(sum(t["count"] for t in tiles), 5)
        self.assertEqual(sum(t["share"] for t in tiles), 100)
        # Only what changed moves: the new lane comes in and the live tile bumps.
        self.assertEqual(page.locator(f"{lane}.ov-new").count(), 1)
        self.assertEqual(page.eval_on_selector_all(".ov-tile.ov-bump", "nodes => nodes.map((n) => n.dataset.outcome)"), ["live"])

        # The clock ticks in place, without redrawing the page.
        clock = f"{lane} .ov-clock"
        shown = page.inner_text(clock)
        self.assertRegex(shown, r"^\d+:\d{2}(:\d{2})?$")
        page.evaluate("document.querySelector('.vibe-overview').dataset.kept = 'yes'")
        page.wait_for_function("([sel, was]) => document.querySelector(sel).textContent !== was", arg=[clock, shown])
        self.assertEqual(page.get_attribute(".vibe-overview", "data-kept"), "yes")

        # A new step shows in the lane as soon as it is logged, with no reload.
        steps = int(page.get_attribute(lane, "data-steps"))
        page.evaluate("window.__sameLoad = true")
        page.select_option(".vibe-log-form select", "pass")
        page.fill(".vibe-log-form input", "overview probe green")
        page.press(".vibe-log-form input", "Enter")
        page.wait_for_function("(sel) => document.querySelector(sel)?.textContent === 'overview probe green'", arg=f"{lane} .ov-step .txt")
        self.assertEqual(int(page.get_attribute(lane, "data-steps")), steps + 1)
        self.assertEqual(page.locator(f"{lane} .ov-step.k-pass").count(), 1)
        self.assertEqual(page.locator(f"{lane} .ov-track i").count(), steps + 1)
        self.assertEqual(page.inner_text(f"{lane} .ov-checks .check-pass").strip(), "1")
        self.assertEqual(page.locator(f"{lane}.ov-stepped").count(), 1)
        self.assertTrue(page.evaluate("window.__sameLoad"))

        # The step line jumps to that session's own row in the live recap, opened to its steps.
        page.click(f"{lane} .ov-lane-log")
        page.wait_for_function("(p) => document.activeElement.closest('.rc-row.open')?.dataset.path === p", arg=path)

        # Ending it moves it from live to shipped.
        page.click(f'[data-action="vibe-end"][data-result="shipped"][data-path="{path}"]')
        page.wait_for_function("() => document.querySelectorAll('.ov-lane').length === 0")
        tiles = self.overview_tiles(page)
        after = {t["key"]: t for t in tiles}
        self.assertEqual(after["live"]["count"], 0)
        self.assertTrue(after["live"]["off"])
        self.assertEqual(after["shipped"]["count"], shipped + 1)
        self.assertEqual(sum(t["count"] for t in tiles), 5)
        self.assertNotIn("live now", page.inner_text(".vibe-overview > header"))
        self.assertEqual(self.errors, [])

    def test_vibe_overview_draw_in_carries_on_through_a_redraw(self):
        page = self.open()
        self.go(page, "vibe")
        # Timed inside the page, so a slow machine cannot stretch the gaps between the steps.
        seen = page.evaluate(
            """async () => {
                const drawn = new Promise((resolve) => {
                    const watch = new MutationObserver(() => {
                        if (!document.querySelector('.vibe-overview')) return;
                        watch.disconnect();
                        resolve(performance.now());
                    });
                    watch.observe(document.body, { childList: true, subtree: true });
                });
                document.querySelector('[data-action="vibe-sample"]').click();
                const at = await drawn;
                const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const read = () => {
                    const band = document.querySelector('.vibe-overview');
                    const bar = getComputedStyle(band.querySelector('.ov-bar'));
                    return {
                        since: performance.now() - at,
                        enter: band.classList.contains('ov-enter'),
                        into: parseFloat(band.style.getPropertyValue('--ov-into')),
                        name: bar.animationName,
                        delay: parseFloat(bar.animationDelay),
                    };
                };
                const first = read();
                await wait(250);
                window.cairn.rerender();
                const during = read();
                while (performance.now() - at < 800) await wait(20);
                window.cairn.rerender();
                const after = read();
                return { first, during, after };
            }"""
        )
        first, during, after = seen["first"], seen["during"], seen["after"]
        self.assertTrue(first["enter"], seen)
        self.assertEqual(first["into"], 0, seen)
        self.assertEqual(first["name"], "ov-reveal", seen)
        if during["since"] < 500:  # a stalled machine may already be past the draw-in; then there is nothing to carry on
            self.assertTrue(during["enter"], seen)
            self.assertLessEqual(during["into"], -240, seen)  # it picks up where it was; it does not start over
            self.assertLessEqual(during["delay"], -0.24, seen)
        self.assertFalse(after["enter"], seen)  # once drawn, a redraw leaves it alone
        self.assertEqual(after["name"], "none", seen)
        page.wait_for_function("() => [...document.querySelectorAll('.ov-count')].every((n) => n.textContent === n.dataset.count)")
        self.assertEqual(self.errors, [])

    def test_vibe_overview_keeps_still_with_reduced_motion(self):
        page = self.open()
        page.emulate_media(reduced_motion="reduce")
        self.go(page, "vibe")
        self.sample_week(page)
        still = """() => ({
            moving: document.querySelectorAll('.ov-enter, .ov-new, .ov-stepped, .ov-bump').length,
            settled: [...document.querySelectorAll('.ov-count')].every((n) => n.textContent === n.dataset.count),
            bar: getComputedStyle(document.querySelector('.ov-bar')).animationName,
            slide: getComputedStyle(document.querySelector('.ov-seg')).transitionDuration,
        })"""
        self.assertEqual(page.evaluate(still), {"moving": 0, "settled": True, "bar": "none", "slide": "0s"})

        self.start_session(page, "Quiet session")
        page.select_option(".vibe-log-form select", "pass")
        page.fill(".vibe-log-form input", "still green")
        page.press(".vibe-log-form input", "Enter")
        page.wait_for_function("() => document.querySelector('.ov-lane .ov-step .txt')?.textContent === 'still green'")
        self.assertEqual(page.evaluate(still), {"moving": 0, "settled": True, "bar": "none", "slide": "0s"})
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.ov-pulse')).animationName"), "none")
        # Still is not frozen: the clock keeps the time.
        shown = page.inner_text(".ov-lane .ov-clock")
        page.wait_for_function("(was) => document.querySelector('.ov-lane .ov-clock').textContent !== was", arg=shown)
        self.assertEqual(self.errors, [])

    def test_vibe_overview_on_a_phone_in_dark_mode(self):
        page = self.open(viewport=(390, 844), theme="dark")
        page.evaluate("location.hash = '#/vibe'")
        page.wait_for_selector(".vibe-figures")
        self.sample_week(page)
        self.start_session(page, "Pocket check")
        overflow = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        self.assertLessEqual(page.evaluate(overflow), 0)
        sizes = page.eval_on_selector_all("button.ov-tile", "nodes => nodes.map((n) => Math.min(n.getBoundingClientRect().width, n.getBoundingClientRect().height))")
        self.assertEqual(len(sizes), 6)
        self.assertGreaterEqual(min(sizes), 44, sizes)  # every status is big enough to tap
        page.click('.ov-tile[data-outcome="shipped"]')
        page.wait_for_selector('.ov-tile[data-outcome="shipped"][aria-pressed="true"]')
        self.assertGreater(page.locator(".ov-peek li").count(), 0)
        self.assertEqual(page.locator(".session-row").count(), page.locator(".ov-peek li").count())
        self.assertLessEqual(page.evaluate(overflow), 0)
        self.assertEqual(self.errors, [])

    def test_vibe_live_recap_and_continue_from_a_session(self):
        page = self.open()
        self.go(page, "vibe")
        page.evaluate(RECAP_SEED)
        page.wait_for_selector(".vibe-recap .rc-row")

        states = page.eval_on_selector_all(".rc-live > .rc-row", "nodes => nodes.map((n) => n.dataset.state)")
        self.assertEqual(states, ["error", "working", "working", "finished"])  # what stopped comes first
        chips = page.eval_on_selector_all(".rc-chip", "nodes => nodes.map((n) => n.textContent.replace(/\\s+/g, ' ').trim())")
        self.assertEqual(chips, ["1 stopped on an error", "2 working", "1 turn finished", "1 left open"])
        # Sessions run on their own: nothing on the page says one is waiting for you, or calls a running one quiet.
        band = page.inner_text(".vibe-recap").lower()
        self.assertNotIn("waiting", band)
        self.assertNotIn("quiet", band)
        working = '.rc-row[data-path="sessions/a-working.md"]'
        waiting = '.rc-row[data-path="sessions/b-waiting.md"]'
        slow = '.rc-row[data-path="sessions/d-quiet.md"]'
        self.assertEqual(page.inner_text(f"{slow} .rc-state"), "Working")
        self.assertIn("last step 1h 15m ago", page.inner_text(f"{slow} .rc-when"))
        self.assertEqual(page.inner_text(f"{working} .rc-state"), "Working")
        self.assertEqual(page.inner_text(f"{working} .rc-model"), "Pega-Coding")
        self.assertEqual(page.inner_text(f"{working} .rc-effort"), "high effort")
        self.assertEqual(page.inner_text(f"{working} .rc-tokens"), "1.8M tokens")
        self.assertEqual(page.inner_text(f"{waiting} .rc-state"), "Turn finished")
        self.assertIn("backs off 2, 4, 8 seconds", page.inner_text(f"{waiting} .rc-last.k-reply"))
        self.assertIn("context length exceeded", page.inner_text('.rc-row[data-state="error"] .rc-last'))
        self.assertIn("tokens in live sessions", page.inner_text(".vibe-recap > header"))

        # A session nobody closed is folded away, not mixed in with the ones that are running.
        self.assertEqual(page.locator('details[data-block="rc-stale"] .rc-row').count(), 1)
        self.assertFalse(page.is_visible('.rc-row[data-path="sessions/e-stale.md"]'))
        finished = page.eval_on_selector_all(".rc-finished .rc-state", "nodes => nodes.map((n) => n.innerText.trim())")
        self.assertEqual(finished, ["Shipped", "Stopped mid-turn"])
        self.assertIn("share sheet opens on the phone", page.inner_text('.rc-row[data-path="sessions/f-shipped.md"] .rc-last'))

        # Every part of the recap folds, and a fold is still how you left it after a reload.
        sections = page.eval_on_selector_all(".vibe-recap > details.rc-section", "nodes => nodes.map((n) => [n.dataset.block, n.open])")
        self.assertEqual(sections, [["rc-running", True], ["rc-stale", False], ["rc-finished-all", True]])
        page.click('details[data-block="rc-finished-all"] > summary')
        page.wait_for_function("() => !document.querySelector('details[data-block=\"rc-finished-all\"]').open")
        self.assertFalse(page.is_visible('.rc-row[data-path="sessions/f-shipped.md"]'))
        self.assertEqual(page.evaluate("() => JSON.parse(localStorage.getItem('cairn.vibeFolds'))['rc-finished-all']"), False)
        page.evaluate("window.cairn.rerender()")
        self.assertFalse(page.evaluate("() => document.querySelector('details[data-block=\"rc-finished-all\"]').open"))
        page.click('details[data-block="rc-finished-all"] > summary')
        page.wait_for_function("() => document.querySelector('details[data-block=\"rc-finished-all\"]').open")
        # A finished session opens to its last steps too.
        page.click('.rc-toggle[data-path="sessions/f-shipped.md"]')
        page.wait_for_selector('.rc-row.open[data-path="sessions/f-shipped.md"] .rc-detail')
        page.click('.rc-toggle[data-path="sessions/f-shipped.md"]')
        page.wait_for_selector('.rc-row[data-path="sessions/f-shipped.md"] .rc-detail', state="detached")

        # The overview folds per assistant, and the bridge's own OpenCode sessions get their own name.
        agents = page.eval_on_selector_all("details.ov-agent", "nodes => nodes.map((n) => [n.dataset.block, n.open])")
        self.assertIn(["ov-agent:opencode", True], agents)
        self.assertIn(["ov-agent:vector", True], agents)
        page.click('details[data-block="ov-agent:opencode"] > summary')
        page.wait_for_function("() => !document.querySelector('details[data-block=\"ov-agent:opencode\"]').open")
        page.click('details[data-block="ov-agent:opencode"] > summary')
        page.wait_for_function("() => document.querySelector('details[data-block=\"ov-agent:opencode\"]').open")

        # No PC to talk to: nothing to press. With one: continue what stopped, never what is still working.
        self.assertEqual(page.locator(".rc-actions").count(), 0)
        page.evaluate(CHAT_STUB)
        page.wait_for_selector(f'{waiting} [data-action="vibe-continue"]')
        self.assertEqual(page.locator(f'{working} [data-action="vibe-continue"]').count(), 0)
        self.assertEqual(page.locator(f'{working} [data-action="vibe-newchat"]').count(), 1)
        self.assertEqual(page.locator('.rc-row[data-path="sessions/d-quiet.md"] .rc-actions').count(), 0)

        page.click(f'{waiting} [data-action="vibe-continue"]')
        page.wait_for_function("() => window.cairn.state.view === 'chat'")
        banner = page.inner_text('.chat-about[data-mode="continue"]')
        self.assertIn("Fix the relay retry loop", banner)
        self.assertIn("cairn", banner)
        page.fill("#chat-input", "commit and push")
        page.press("#chat-input", "Enter")
        page.wait_for_function("() => window.__sent.length === 1")
        sent = page.evaluate("window.__sent[0]")
        self.assertEqual((sent["action"], sent["text"], sent["about"], sent["mode"]), ("send", "commit and push", "sessions/b-waiting.md", "continue"))
        page.wait_for_selector(".chat-msg.user")
        self.assertEqual(page.locator(".chat-about").count(), 0)  # said once; the chat carries on from here

        self.go(page, "vibe")
        page.click(f'{working} [data-action="vibe-newchat"]')
        page.wait_for_selector('.chat-about[data-mode="new"]')
        page.click('.chat-about [data-chat="plain"]')
        page.wait_for_selector(".chat-about", state="detached")
        page.fill("#chat-input", "just a question")
        page.press("#chat-input", "Enter")
        page.wait_for_function("() => window.__sent.length === 2")
        self.assertNotIn("about", page.evaluate("window.__sent[1]"))

        # What a bot runs through OpenCode from its own workspace is listed under the bot, not under OpenCode.
        self.go(page, "vibe")
        page.evaluate(OPENBOT_SEED)
        page.wait_for_selector('details[data-block="ov-agent:openbot"]')
        self.assertEqual(page.inner_text('details[data-block="ov-agent:openbot"] .ov-agent-name'), "OpenBot")
        self.assertIn("OpenBot", page.inner_text('.rc-row[data-path="sessions/h-openbot.md"] .rc-facts'))
        self.assertEqual(self.errors, [])

    def test_vibe_share_card_is_drawn_from_totals_and_names_the_model_only_when_asked(self):
        page = self.open()
        self.go(page, "vibe")
        page.evaluate(RECAP_SEED)
        page.click('[data-action="vibe-share"]')
        page.wait_for_selector(".modal.share .share-canvas")
        card = "document.querySelector('.share-canvas')"

        self.assertEqual(page.evaluate(f"[{card}.width, {card}.height]"), [1080, 1920])
        self.assertEqual(page.evaluate(f"{card}.getContext('2d').getImageData(5, 5, 1, 1).data[3]"), 255)  # painted, not blank
        self.assertEqual(page.evaluate(f"{card}.dataset.headline"), "hours")
        self.assertEqual(page.evaluate(f"{card}.dataset.model"), "")  # model names stay off the card until asked for
        page.check('.modal.share [name="models"]')
        self.assertTrue(page.evaluate(f"{card}.dataset.model").startswith("Pega-"))

        page.select_option('.modal.share [name="headline"]', "tokens")
        self.assertEqual(page.evaluate(f"{card}.dataset.headline"), "tokens")
        self.assertRegex(page.evaluate(f"{card}.dataset.hero"), r"^\d[\d.]*[kMBT]$")
        page.select_option('.modal.share [name="format"]', "square")
        self.assertEqual(page.evaluate(f"[{card}.width, {card}.height]"), [1080, 1080])

        with page.expect_download() as download:
            page.click('.modal.share [data-share="save"]')
        self.assertEqual(download.value.suggested_filename, "cairn-vibe-square.png")
        page.click(".modal.share [data-cancel]")
        page.wait_for_selector(".modal.share", state="detached")
        self.assertEqual(self.errors, [])

    def test_people_leaderboard_counts_every_computer_or_the_ones_picked(self):
        """Sessions from two computers sync into one brain: "You" is both together unless you pick."""
        seed = """() => {
          const s = window.cairn.state;
          const session = (slug, machine, tokens) => s.files.push({
            path: `sessions/${slug}.md`,
            text: `---\\ntitle: ${slug}\\ntype: session\\nstatus: shipped\\nstarted: 2026-11-18T09:00\\nended: 2026-11-18T10:00\\n${machine ? `machine: ${machine}\\n` : ""}tokens: ${tokens}\\n---\\n`,
            mtime: Date.now(),
          });
          session("on-the-desk", "Desk PC", 4000000);
          session("on-the-desk-too", "Desk PC", 1000000);
          session("on-the-road", "Road Mac", 250000);
          s.notes = s.files.map((f) => window.cairn.core.noteFromFile(f.path, f.text, f.mtime));
          window.cairn.rerender();
        }"""
        page = self.open()
        self.go(page, "people")
        page.evaluate(seed)
        page.wait_for_selector(".lb-devices")
        chips = "nodes => nodes.map((n) => [n.innerText.replace(/\\s+/g, ' ').trim(), n.getAttribute('aria-pressed')])"
        devices = '.lb-devices [data-action="lb-device"]'
        self.assertEqual(
            page.eval_on_selector_all(devices, chips),
            [["All devices 5.3M", "true"], ["Desk PC 5M", "false"], ["Road Mac 250k", "false"]],
        )
        self.assertEqual(page.inner_text(".lb-row.me .lb-value"), "5.3M")
        self.assertIn("all 2 devices · not published", page.inner_text(".lb-row.me"))

        page.click(f'{devices}[data-device="Road Mac"]')
        page.wait_for_function("() => document.querySelector('.lb-row.me .lb-value').innerText === '250k'")
        self.assertIn("Road Mac only", page.inner_text(".lb-row.me"))
        self.assertEqual(page.evaluate("localStorage.getItem('cairn.lbDevices')"), '["Road Mac"]')

        # What is published follows the choice, and the computers' names stay out of the note.
        page.click('[data-action="stats-publish"]')
        page.wait_for_selector(".modal")
        self.assertIn("Counted: Road Mac only.", page.inner_text(".modal"))
        page.click(".modal button[type=submit]")
        page.wait_for_selector('[data-action="stats-unpublish"]')
        note = self.note_text(page, "vibe-stats.md")
        self.assertIn("vibe_tokens: 250000", note)
        self.assertNotIn("Road Mac", note)

        # The other computer too is every computer again; "All devices" does the same in one press.
        page.click(f'{devices}[data-device="Desk PC"]')
        page.wait_for_function("() => document.querySelector('.lb-row.me .lb-value').innerText === '5.3M'")
        self.assertIsNone(page.evaluate("localStorage.getItem('cairn.lbDevices')"))
        page.click(f'{devices}[data-device="Desk PC"]')
        page.wait_for_function("() => document.querySelector('.lb-row.me .lb-value').innerText === '5M'")
        page.click(f"{devices}:not([data-device])")
        page.wait_for_function("() => document.querySelector('.lb-row.me .lb-value').innerText === '5.3M'")
        self.assertEqual(page.eval_on_selector_all(devices, chips)[0], ["All devices 5.3M", "true"])
        self.assertEqual(self.errors, [])

    def test_people_leaderboard_ranks_friends_by_their_published_totals(self):
        def stats_note(tokens, minutes, sessions):
            lines = ["title: Vibe stats", "type: note", "publish: true", "updated: 2026-11-10", f"vibe_tokens: {tokens}", f"vibe_minutes: {minutes}",
                     f"vibe_sessions: {sessions}", "vibe_prompts: 300", "vibe_streak: 3", "vibe_active_days: 12"]
            return "---\n" + "\n".join(lines) + "\n---\n\nSelf-reported.\n"

        def friends_on_github(mock):
            base = mock.repos["juw12/cairn"]["meta"]
            brains = {
                "alice": {"brain/vibe-stats.md": stats_note(5_000_000_000, 600, 40)},
                "bob": {"vibe-stats.md": stats_note(1000, 90_000, 12)},
                # Text from the internet: a number that is not a number must never reach the page.
                "carol": {"brain/vibe-stats.md": stats_note("<img src=x onerror=alert(1)>", 60, 5)},
            }
            for login, files in brains.items():
                meta = dict(base, name="brain", full_name=f"{login}/brain", owner={"login": login, "avatar_url": base["owner"]["avatar_url"]})
                mock.repos[f"{login}/brain"] = {"meta": meta, "files": files}

        page = self.open(setup=friends_on_github)
        self.go(page, "vibe")
        page.evaluate(RECAP_SEED)
        self.go(page, "people")
        for login in ("alice", "bob", "carol"):
            page.fill("#friend-query", f"{login}/brain")
            page.press("#friend-query", "Enter")
            page.wait_for_selector(f'#leaderboard [data-login="{login}"]')

        order = "nodes => nodes.map((n) => (n.classList.contains('me') ? 'me' : n.dataset.login))"
        self.assertEqual(page.eval_on_selector_all(".lb-row", order), ["alice", "me", "bob"])
        self.assertEqual(page.inner_text('.lb-row[data-login="alice"] .lb-place'), "1")
        self.assertEqual(page.inner_text('.lb-row[data-login="alice"] .lb-value'), "5B")
        self.assertIn("not published", page.inner_text(".lb-row.me"))
        self.assertEqual(page.locator(".lb-devices").count(), 0, "one computer: nothing to choose")
        self.assertIn("hasn't published totals yet", page.inner_text('.lb-waiting [data-login="carol"]'))
        self.assertEqual(page.locator("#leaderboard img[src=x]").count(), 0)

        page.click('[data-action="lb-stat"][data-stat="hours"]')
        page.wait_for_selector('[data-action="lb-stat"][data-stat="hours"][aria-pressed="true"]')
        friends = [login for login in page.eval_on_selector_all(".lb-row", order) if login != "me"]
        self.assertEqual(friends, ["bob", "alice"])
        self.assertEqual(page.inner_text('.lb-row[data-login="bob"] .lb-place'), "1")

        # Your own totals stay on the device until you publish them, and the note carries numbers only.
        self.assertIsNone(self.note_text(page, "vibe-stats.md"))
        page.click('[data-action="stats-publish"]')
        page.wait_for_selector(".modal")
        self.assertIn("Not included: session titles, folders, model names, messages.", page.inner_text(".modal"))
        page.click(".modal button[type=submit]")
        page.wait_for_selector('[data-action="stats-unpublish"]')
        note = self.note_text(page, "vibe-stats.md")
        self.assertIn("publish: true", note)
        self.assertRegex(note, r"vibe_tokens: \d+")
        for private in ("Pega-", "Recap band", "E:/Projects"):
            self.assertNotIn(private, note)
        self.assertRegex(page.inner_text(".lb-row.me"), r"(?<!not )published \d{4}-\d{2}-\d{2}")
        page.click('[data-action="stats-unpublish"]')
        page.wait_for_selector('[data-action="stats-unpublish"]', state="detached")
        self.assertIsNone(self.note_text(page, "vibe-stats.md"))

        page.click('[data-action="friend-remove"][data-repo="bob/brain"]')
        page.wait_for_selector('.lb-row[data-login="bob"]', state="detached")
        kept = page.evaluate("JSON.parse(localStorage.getItem('cairn.friends')).map((f) => [f.fullName, f.root])")
        self.assertEqual(kept, [["alice/brain", "brain/"], ["carol/brain", "brain/"]])
        # Bob keeps his notes at the top of his repo: brain/ and .brain/ were tried once, then remembered.
        self.assertEqual(len(self.errors), 2, self.errors)
        for error in self.errors:
            self.assertIn("404", error)

    def test_feedback_capture_prefix(self):
        page = self.open()
        page.fill("#capture-input", "fb: offline maps saved my hike #maps")
        self.assertEqual(page.inner_text("#capture-type"), "Feedback")
        page.press("#capture-input", "Enter")
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'feedback/offline-maps-saved-my-hike.md')")
        self.assertIn("status: new", self.note_text(page, "feedback/offline-maps-saved-my-hike.md"))
        self.assertEqual(self.errors, [])

    def test_version_history_lists_every_build_and_opens_one_at_a_time(self):
        versions_file = Path(__file__).resolve().parent.parent / "docs" / "versions.json"
        builds = json.loads(versions_file.read_text(encoding="utf-8"))["releases"]
        for viewport in ((1280, 900), (390, 844)):
            with self.subTest(viewport=viewport):
                page = self.open(viewport=viewport)
                # On a phone the sidebar is behind the menu button; the button itself is the same one.
                page.evaluate("document.querySelector('[data-action=\"versions\"]').click()")
                page.wait_for_selector(".modal.versions .ver")
                rows = page.eval_on_selector_all(
                    ".modal.versions .ver",
                    "nodes => nodes.map((n) => ({ open: n.open, build: n.querySelector('.ver-build').textContent, "
                    "changes: n.querySelectorAll('li').length, current: !!n.querySelector('.pill') }))",
                )
                self.assertEqual([row["build"] for row in rows], [f"Build {build['build']}" for build in builds])
                self.assertEqual([row["changes"] for row in rows], [len(build["changes"]) for build in builds])
                # Only the build you are on is open and marked; the rest stay one line each.
                self.assertEqual([row["open"] for row in rows], [True] + [False] * (len(rows) - 1))
                self.assertEqual([row["current"] for row in rows], [True] + [False] * (len(rows) - 1))
                self.assertIn(f"build {builds[0]['build']}", page.inner_text(".modal.versions p"))
                page.click(".modal.versions .ver:nth-of-type(2) summary")
                self.assertTrue(page.eval_on_selector(".modal.versions .ver:nth-of-type(2)", "n => n.open"))
                link = page.get_attribute(".modal.versions .ver:nth-of-type(2) li a", "href")
                self.assertRegex(link, r"^https://github\.com/juw12/cairn/pull/\d+$")
                # Nothing runs off the side of the screen, and the list scrolls inside the dialog.
                box = page.eval_on_selector(".modal.versions", "n => { const r = n.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, n.scrollWidth, n.clientWidth]; }")
                self.assertGreaterEqual(box[0], 0)
                self.assertLessEqual(box[1], viewport[0])
                self.assertGreaterEqual(box[2], 0)
                self.assertLessEqual(box[3], viewport[1])
                self.assertLessEqual(box[4], box[5])
                page.keyboard.press("Escape")
                page.wait_for_selector(".modal.versions", state="detached")
                self.assertEqual(self.errors, [])

    def test_vibe_device_bar_shows_one_computer_or_all_and_keeps_the_choice(self):
        page = self.open()
        self.go(page, "vibe")
        self.assertEqual(page.locator(".vibe-devices").count(), 0)  # no second computer: nothing to choose
        page.evaluate(DEVICES_SEED)
        page.wait_for_selector(".vibe-devices")
        chips = lambda: page.eval_on_selector_all(
            ".vibe-devices .chip",
            "els => els.map((e) => [e.dataset.device ?? null, e.querySelector('b').textContent, e.querySelector('.vd-live')?.textContent ?? '', e.getAttribute('aria-pressed')])",
        )
        # Every computer together first; the one used last leads the list.
        self.assertEqual(chips(), [[None, "3", "2 live", "true"], ["MacBook Pro", "1", "1 live", "false"], ["ASUS EXPERTBOOK", "2", "1 live", "false"]])
        self.assertIn("3 sessions", page.inner_text(".vibe-overview > header"))
        self.assertEqual(page.locator(".rc-live > .rc-row").count(), 2)
        self.assertEqual(sorted(page.locator(".rc-live .rc-device").all_inner_texts()), ["ASUS EXPERTBOOK", "MacBook Pro"])
        self.assertEqual(page.inner_text(".vibe-machine h2"), "ASUS EXPERTBOOK")  # the newest reading

        mac = '.vibe-devices .chip[data-device="MacBook Pro"]'
        page.click(mac)
        page.wait_for_function("() => document.querySelectorAll('.rc-live > .rc-row').length === 1")
        self.assertEqual(page.get_attribute(mac, "aria-pressed"), "true")
        page.wait_for_function("(sel) => document.activeElement.matches(sel)", arg=mac)  # focus stays on the chip
        self.assertEqual(page.inner_text(".rc-live .rc-title").strip(), "mac-live")
        self.assertEqual(page.locator(".rc-device").count(), 0)  # one computer shown: no need to name it on each row
        self.assertIn("1 session", page.inner_text(".vibe-overview > header"))
        self.assertIn("1 session · 50 tokens", page.inner_text(".vibe-device-note"))
        self.assertEqual(page.inner_text(".vibe-machine h2"), "MacBook Pro")  # its own reading, not this computer's
        self.assertEqual(page.inner_text('[data-panel="machine"] .vp-title'), "Computer")
        self.assertIn("2 live", page.inner_text('.hub-tabs [data-go="vibe"]'))  # the tab's count is every computer's
        self.assertEqual(page.evaluate("() => localStorage.getItem('cairn.vibeDevice')"), '"MacBook Pro"')

        page.click(".vibe-devices .chip:not([data-device])")
        page.wait_for_function("() => document.querySelectorAll('.rc-live > .rc-row').length === 2")
        self.assertIsNone(page.evaluate("() => localStorage.getItem('cairn.vibeDevice')"))
        self.assertEqual(page.locator(".vibe-device-note").count(), 0)
        self.assertEqual(page.evaluate("() => document.documentElement.scrollWidth - innerWidth"), 0)
        self.assertEqual(self.errors, [])

    def test_diary_shows_each_day_for_every_computer(self):
        page = self.open()
        self.go(page, "diary")
        page.wait_for_selector(".empty-state")  # the sample brain has no sessions: nothing to show, and no row of zeros
        self.assertEqual(page.locator(".diary-table").count(), 0)
        page.evaluate(DIARY_SEED)
        page.wait_for_selector(".diary-table")
        self.assertEqual(self.nav_state(page)["tabs"], ["journal", "diary"])

        chips = lambda: page.eval_on_selector_all(
            ".diary-devices .chip",
            "els => els.map((e) => [e.dataset.device ?? null, e.querySelector('b')?.textContent ?? '', e.getAttribute('aria-pressed')])",
        )
        # Both computers are found from the notes alone: most days first, then by name.
        self.assertEqual(chips(), [[None, "", "true"], ["ACME Roadrunner 14", "2", "false"], ["MacBook Pro 15,4", "2", "false"]])
        self.assertEqual(page.locator(".diary-row").count(), 30)
        self.assertEqual(page.locator(".diary-row.rest").count(), 28)
        self.assertNotIn("0.0", page.inner_text(".diary-table"))  # a day with nothing in it is a rest day, never a zero

        # Today, both computers: the report's 150 min for the PC (more than the watcher's 120) plus the Mac's 45.
        today = page.locator(".diary-row").first
        cells = today.locator("td").all_inner_texts()
        self.assertEqual(cells[1], "3h 15m")
        self.assertEqual(cells[2].split(), ["2", "of", "3"])
        self.assertEqual(cells[3], "1h 10m")
        self.assertRegex(cells[0], r"^\d+\.\d ")
        self.assertGreaterEqual(today.locator(".diary-stars").get_attribute("aria-label")[0], "1")
        self.assertIn("3h 15m", page.inner_text(".diary-stats"))

        # A day opens: one block per computer, the hours it was used, and the tasks of that day.
        today.locator(".diary-day-btn").click()
        page.wait_for_selector(".diary-detail")
        self.assertEqual(page.locator(".diary-detail .diary-device h3").count(), 2)
        self.assertEqual(page.locator(".diary-detail .diary-hours").count(), 2)
        self.assertEqual(page.locator(".diary-log .row-item").count(), 3)
        self.assertIn("MacBook Pro 15,4", page.inner_text(".diary-log"))
        self.assertEqual(page.locator('.diary-detail [data-open^="Productivity Diary/"]').count(), 1)

        # A day with more tasks than fit shows the last twelve and offers the rest.
        page.locator(".diary-row").nth(1).locator(".diary-day-btn").click()
        page.wait_for_function("() => document.querySelectorAll('.diary-log .row-item').length === 12")
        page.click('[data-action="diary-all"]')
        page.wait_for_function("() => document.querySelectorAll('.diary-log .row-item').length === 15")

        # One computer on its own.
        mac = '.diary-devices .chip[data-device="MacBook Pro 15,4"]'
        page.click(mac)
        page.wait_for_function("(sel) => document.querySelector(sel).getAttribute('aria-pressed') === 'true'", arg=mac)
        cells = page.locator(".diary-row").first.locator("td").all_inner_texts()
        self.assertEqual((cells[1], cells[2].split(), cells[3]), ("45m", ["1", "of", "1"], "20m"))
        self.assertEqual(page.evaluate("() => localStorage.getItem('cairn.diaryDevice')"), '"MacBook Pro 15,4"')
        page.click(".diary-devices .chip:not([data-device])")
        page.wait_for_function("() => localStorage.getItem('cairn.diaryDevice') === null")

        # A task of the log opens its session note.
        page.locator(".diary-row").first.locator(".diary-day-btn").click()
        page.locator(".diary-log .row-item").first.click()
        page.wait_for_function("() => (window.cairn.state.open || '').startsWith('sessions/')")
        self.assertEqual(self.errors, [])

    def test_diary_on_a_phone_in_dark_mode(self):
        page = self.open(viewport=(390, 844), theme="dark")
        page.evaluate("location.hash = '#/diary'")  # the sidebar is a drawer at this width
        page.wait_for_function("() => window.cairn.state.view === 'diary'")
        page.evaluate(DIARY_SEED)
        page.wait_for_selector(".diary-table")
        page.locator(".diary-row").first.locator(".diary-day-btn").click()
        page.wait_for_selector(".diary-detail")
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        self.assertLessEqual(overflow, 0)
        self.assertFalse(page.is_visible(".diary-table th.dc-load"))  # the load columns leave; the open day still has them
        self.assertIn("processor", page.inner_text(".diary-detail"))
        self.assertEqual(self.errors, [])

    def test_vibe_view_on_a_phone_in_dark_mode(self):
        page = self.open(viewport=(390, 844), theme="dark")
        page.evaluate("location.hash = '#/vibe'")
        page.wait_for_selector(".vibe-figures")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0)
        self.assertEqual(page.locator(".vibe-feedback").count(), 0, "no feedback yet, so no Feedback block")
        fold = page.locator("details.vibe-skills")
        self.assertEqual(fold.count(), 1, "an empty block folds to one line on a phone")
        self.assertFalse(page.evaluate("() => document.querySelector('details.vibe-skills').open"))
        page.click("details.vibe-skills > summary")
        page.wait_for_function("() => document.querySelector('details.vibe-skills').open")
        page.evaluate("window.cairn.rerender()")
        self.assertTrue(page.evaluate("() => document.querySelector('details.vibe-skills').open"), "an opened block stays open when the view re-renders")
        self.assertEqual(self.errors, [])

    def test_capture_routes_to_task_with_tags(self):
        page = self.open()
        page.fill("#capture-input", "todo: ship the e2e test #release")
        self.assertEqual(page.inner_text("#capture-type"), "Task")
        page.press("#capture-input", "Enter")
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'tasks/ship-the-e2e-test.md')")
        text = self.note_text(page, "tasks/ship-the-e2e-test.md")
        self.assertIn("status: todo", text)
        self.assertIn("tags: [release]", text)
        self.assertEqual(self.errors, [])

    def test_log_capture_appends_to_today(self):
        page = self.open()
        page.fill("#capture-input", "log: wrote the tests")
        page.press("#capture-input", "Enter")
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path.startsWith('journal/') && f.text.includes('wrote the tests'))")

    def test_drag_idea_between_columns(self):
        page = self.open()
        self.go(page, "ideas")
        card = '.item-card[data-drag="ideas/share-trip-link.md"]'
        page.drag_and_drop(card, '.column[data-column="exploring"]')
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'ideas/share-trip-link.md').text.includes('status: exploring')")
        self.assertEqual(page.locator(f'.column[data-column="exploring"] {card}').count(), 1)

    def test_edit_persists_and_promote_links_back(self):
        page = self.open()
        self.go(page, "ideas")
        page.click('.item-card[data-drag="ideas/voice-notes-on-trail.md"]')
        page.wait_for_selector("#ed-body")
        page.fill("#ed-body", "Edited body from the e2e test.\n")
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'ideas/voice-notes-on-trail.md').text.includes('Edited body')")
        page.click('[data-action="spark"]')
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'ideas/voice-notes-on-trail.md').text.includes('### Spark:')")
        page.click('[data-action="promote"]')
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'tasks/voice-notes-pinned-to-the-map.md')")
        task = self.note_text(page, "tasks/voice-notes-pinned-to-the-map.md")
        idea = self.note_text(page, "ideas/voice-notes-on-trail.md")
        self.assertIn("[[voice-notes-on-trail]]", task)
        self.assertIn("[[voice-notes-pinned-to-the-map]]", idea)
        self.assertIn("status: ready", idea)
        page.reload()
        page.wait_for_selector("#nav button")
        self.assertIn("Edited body", self.note_text(page, "ideas/voice-notes-on-trail.md"))
        self.assertEqual(self.errors, [])

    def test_typing_right_after_opening_stays_in_the_body(self):
        page = self.open()
        self.go(page, "ideas")
        page.click('.item-card[data-drag="ideas/voice-notes-on-trail.md"]')
        # Click into the body immediately, before the drawer's own focus timer fires.
        page.click("#ed-body")
        page.keyboard.type("quick words")
        page.wait_for_timeout(120)
        self.assertEqual(page.input_value("#ed-title"), "Voice notes pinned to the map")
        self.assertIn("quick words", page.input_value("#ed-body"))
        self.assertEqual(self.errors, [])

    def test_palette_finds_body_text(self):
        page = self.open()
        page.keyboard.press("Control+k")
        page.fill(".palette input", "switchbacks")  # only in code; should find nothing in notes
        page.fill(".palette input", "Strava")
        self.assertIn("GPX import", page.inner_text(".palette-list"))
        page.keyboard.press("Enter")
        page.wait_for_selector(".drawer")

    def test_code_inbox_makes_tracked_task(self):
        page = self.open()
        self.go(page, "code")
        page.click('[data-action="todo-task"][data-source="src/maps/download.ts:88"]')
        page.wait_for_function("() => window.cairn.state.notes.some((n) => n.meta.source === 'src/maps/download.ts:88')")
        self.assertGreaterEqual(page.locator(".tracked").count(), 2)

    def test_first_run_guide_on_empty_brain(self):
        page = self.open("?empty=1")
        page.evaluate("localStorage.removeItem('cairn.guide')")
        page.reload()
        page.wait_for_selector("#guide")
        self.assertEqual(page.locator(".guide-step.done").count(), 0)
        page.fill("#capture-input", "my first idea")
        page.press("#capture-input", "Enter")
        page.wait_for_selector(".guide-step.done")
        self.assertEqual(page.locator(".guide-step.done").count(), 1)

    def test_autopilot_queue_in_demo(self):
        page = self.open()
        self.go(page, "autopilot")
        self.assertIn("needs Cairn on this computer", page.inner_text("#view"))
        page.fill("#ap-queue-text", "Add CSV export to the report page #auto")
        page.press("#ap-queue-text", "Enter")
        page.wait_for_selector(".ap-q")
        task = page.evaluate("() => window.cairn.state.notes.find((n) => n.tags.includes('queue'))")
        self.assertEqual(task["title"], "Add CSV export to the report page")
        self.assertEqual(task["tags"], ["queue", "auto"])
        self.assertEqual(page.inner_text('.hub-tabs [data-go="autopilot"] .count'), "1")
        page.click('.ap-q [data-flag="needs-me"]')
        page.wait_for_selector(".ap-q.needs-me")
        page.click('.ap-q [data-action="ap-q-done"]')
        page.wait_for_selector(".ap-queue .empty")
        self.assertEqual(self.errors, [])

    def test_mobile_and_dark_have_no_horizontal_scroll(self):
        for theme, width in (("light", 390), ("dark", 390), ("light", 320)):
            page = self.open(viewport=(width, 844), theme=theme)
            for view in VIEWS:
                page.evaluate("(v) => { location.hash = '#/' + v; dispatchEvent(new PopStateEvent('popstate')); }", view)
                page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)
                overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
                self.assertLessEqual(overflow, 0, f"{theme}/{width}px/{view} scrolls sideways by {overflow}px")
            self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), theme)
            self.assertEqual(self.errors, [])


    def test_small_phone_welcome_and_capture_fit(self):
        page = self.open(query="", viewport=(320, 640), wait="#welcome:not([hidden])")
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        self.assertLessEqual(overflow, 0, f"welcome scrolls sideways by {overflow}px at 320px")
        page = self.open(viewport=(320, 640))
        fits = page.evaluate("""() => {
            const input = document.querySelector('#capture-input');
            const style = getComputedStyle(input);
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
            const room = input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
            return { text: ctx.measureText(input.placeholder).width, room };
        }""")
        self.assertLessEqual(fits["text"], fits["room"], f"capture placeholder is clipped: {fits}")

    def test_map_labels_never_overlap(self):
        for theme, viewport in (("light", (1440, 900)), ("dark", (1024, 768))):
            page = self.open(query="?demo=1#/map", viewport=viewport, theme=theme, wait="#map-svg text")
            overlaps = page.evaluate("""() => {
                const boxes = [...document.querySelectorAll('#map-svg text')]
                    .filter((t) => getComputedStyle(t).opacity !== '0')
                    .map((t) => [t.textContent, t.getBoundingClientRect()]);
                const pairs = [];
                boxes.forEach(([na, a], i) => boxes.slice(i + 1).forEach(([nb, b]) => {
                    if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) pairs.push([na, nb]);
                }));
                return pairs;
            }""")
            self.assertEqual(overlaps, [], f"{theme}: overlapping map labels")
            # Labels hidden for lack of room still appear when their dot is hovered or focused.
            quiet_id = page.evaluate("document.querySelector('#map-svg .map-node:has(text.quiet)')?.dataset.node ?? null")
            if quiet_id:
                node = page.locator(f'#map-svg .map-node[data-node="{quiet_id}"]')
                node.focus()
                page.wait_for_timeout(200)
                self.assertEqual(node.locator("text").evaluate("(t) => getComputedStyle(t).opacity"), "1")
            self.assertEqual(self.errors, [])

    def test_map_big_brain_hides_orphans_and_pans_without_redrawing(self):
        # A brain the size of a real vault on a phone: only linked notes are drawn until "Show all",
        # and dragging slides the picture already painted instead of redrawing every dot per move.
        page = self.open(query="?demo=1#/map", viewport=(390, 844), wait="#map-svg text")
        page.evaluate(r"""() => {
            const { state, core } = window.cairn;
            const name = (i) => 'n' + String(i).padStart(4, '0');
            const files = [];
            for (let i = 0; i < 1600; i += 1) {
                // The first 600 notes link in a ring; the other 1000 have no links at all.
                const link = i < 600 ? ` [[${name((i + 1) % 600)}]]` : '';
                files.push({ path: `notes/${name(i)}.md`, text: `---\ntitle: Entry ${name(i)}\ntype: note\n---\n\nText.${link}\n`, mtime: 1 });
            }
            state.files = files;
            state.notes = files.map((f) => core.noteFromFile(f.path, f.text, f.mtime));
            window.cairn.rerender();
        }""")
        page.wait_for_selector("#map-wrap.big #map-svg .map-dot")
        self.assertEqual(page.locator("#map-svg .map-node").count(), 600)
        self.assertIn("600 notes shown of 1600", page.inner_text(".map-chip"))

        page.wait_for_function("() => !document.querySelector('#map-wrap').classList.contains('moving')", timeout=60000)
        before = page.get_attribute("#map-svg", "viewBox")
        start = page.evaluate("""() => {
            const box = document.querySelector('#map-svg').getBoundingClientRect();
            for (let y = box.top + 20; y < box.bottom - 20; y += 12) {
                for (let x = box.left + 20; x < box.right - 20; x += 12) {
                    const hit = document.elementFromPoint(x, y);
                    if (hit && hit.closest('#map-svg') && !hit.closest('.map-dot')) return [x, y];
                }
            }
            return null;
        }""")
        self.assertIsNotNone(start, "no empty spot on the map to drag from")
        page.evaluate("""() => {
            const svg = document.querySelector('#map-svg');
            window.__slid = 0;
            const first = svg.getAttribute('viewBox');
            const watch = () => {
                if (svg.style.transform && svg.getAttribute('viewBox') === first) window.__slid += 1;
                window.__watch = requestAnimationFrame(watch);
            };
            watch();
        }""")
        page.mouse.move(*start)
        page.mouse.down()
        page.mouse.move(start[0] + 60, start[1] + 40, steps=12)
        page.wait_for_function("() => window.__slid > 0")  # slid as a picture: same viewBox, a transform on top
        page.mouse.up()
        page.wait_for_function("(old) => { const svg = document.querySelector('#map-svg'); return !svg.style.transform && svg.getAttribute('viewBox') !== old; }", arg=before)
        page.evaluate("cancelAnimationFrame(window.__watch)")

        page.click('.map-chip [data-map="clear-filters"]')
        page.wait_for_function("() => document.querySelectorAll('#map-svg .map-node').length === 1600")
        self.assertTrue(page.evaluate("JSON.parse(localStorage.getItem('cairn.map')).orphansSet"))
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ map: Obsidian-plus features

    FAKE_VAULT = """
    (() => {
      class FileHandle {
        constructor(dir, name) { this.kind = 'file'; this.dir = dir; this.name = name; }
        async getFile() {
          const entry = this.dir.files.get(this.name);
          return { lastModified: entry.mtime, size: entry.text.length, text: async () => entry.text };
        }
        async createWritable() {
          const dir = this.dir; const name = this.name; let buffer = '';
          return { write: async (t) => { buffer += t; }, close: async () => { dir.files.set(name, { text: buffer, mtime: Date.now() }); } };
        }
      }
      class DirHandle {
        constructor(name) { this.kind = 'directory'; this.name = name; this.files = new Map(); this.dirs = new Map(); }
        async *entries() {
          for (const [n, d] of this.dirs) yield [n, d];
          for (const n of this.files.keys()) yield [n, new FileHandle(this, n)];
        }
        async getDirectoryHandle(name, opts = {}) {
          if (!this.dirs.has(name)) { if (!opts.create) throw new DOMException('missing', 'NotFoundError'); this.dirs.set(name, new DirHandle(name)); }
          return this.dirs.get(name);
        }
        async getFileHandle(name, opts = {}) {
          if (!this.files.has(name)) { if (!opts.create) throw new DOMException('missing', 'NotFoundError'); this.files.set(name, { text: '', mtime: Date.now() }); }
          return new FileHandle(this, name);
        }
        async removeEntry(name) { this.files.delete(name); }
        async queryPermission() { return 'granted'; }
        async requestPermission() { return 'granted'; }
        async isSameEntry(other) { return other === this; }
      }
      const vault = new DirHandle('My Vault');
      const put = async (path, text) => {
        const parts = path.split('/'); const name = parts.pop(); let dir = vault;
        for (const p of parts) dir = await dir.getDirectoryHandle(p, { create: true });
        dir.files.set(name, { text, mtime: Date.now() - 60000 });
      };
      window.__vault = { root: vault, put, read: async (path) => {
        const parts = path.split('/'); const name = parts.pop(); let dir = vault;
        for (const p of parts) { dir = dir.dirs.get(p); if (!dir) return null; }
        return dir.files.get(name)?.text ?? null;
      } };
      window.showDirectoryPicker = async () => vault;
    })()
    """

    def test_obsidian_vault_imports_and_syncs_both_ways(self):
        page = self.open(query="?demo=1#/obsidian", wait="#obsidian-form")
        page.evaluate(self.FAKE_VAULT)
        page.evaluate(r"""async () => {
            await window.__vault.put('.obsidian/app.md', 'settings, never synced');
            await window.__vault.put('Projects/Trail Plan.md', '---\ntags: [hiking]\n---\nSee [[Gear List|gear]] and [[Competitor scan]].\n');
            await window.__vault.put('Gear List.md', 'Tent, stove.\n');
            await window.__vault.put('Daily/2026-11-05.md', '- packed\n');
        }""")
        page.click('#obsidian-form button[type="submit"]')
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'obsidian/Projects/Trail Plan.md')")
        paths = page.evaluate("window.cairn.state.files.map((f) => f.path).filter((p) => p.startsWith('obsidian/'))")
        self.assertEqual(sorted(paths), ["obsidian/Daily/2026-11-05.md", "obsidian/Gear List.md", "obsidian/Projects/Trail Plan.md"])
        types = page.evaluate("Object.fromEntries(window.cairn.state.notes.filter((n) => n.path.startsWith('obsidian/')).map((n) => [n.path, n.type]))")
        self.assertEqual(types["obsidian/Daily/2026-11-05.md"], "journal")
        page.wait_for_selector(".sync-card.linked")
        self.assertIn("3 notes in sync", page.inner_text(".sync-card.linked"))
        # Obsidian links resolve on the map, including links into the existing Cairn notes.
        edges = page.evaluate(r"""async () => {
            const core = window.cairn.core;
            return core.buildGraph(window.cairn.state.notes).edges.filter((e) => e.source.startsWith('obsidian/')).map((e) => e.target).sort();
        }""")
        self.assertEqual(edges, ["notes/competitor-scan.md", "obsidian/Gear List.md"])
        # Edit in Cairn -> lands in the vault; edit in the vault -> lands in Cairn.
        page.evaluate(r"""async () => {
            const state = window.cairn.state;
            const f = state.files.find((x) => x.path === 'obsidian/Gear List.md');
            const { mtime } = await state.store.write(f.path, 'Tent, stove, map.\n', f.mtime);
            f.text = 'Tent, stove, map.\n'; f.mtime = mtime;
            await window.__vault.put('Daily/2026-11-05.md', '- packed\n- left at dawn\n');
            const dir = window.__vault.root.dirs.get('Daily'); dir.files.get('2026-11-05.md').mtime = Date.now();
        }""")
        page.click('[data-action="obsidian-sync"]')
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'obsidian/Daily/2026-11-05.md').text.includes('left at dawn')")
        self.assertEqual(page.evaluate("window.__vault.read('Gear List.md')"), "Tent, stove, map.\n")
        self.assertIsNone(page.evaluate("window.cairn.state.files.find((f) => f.path.includes('.obsidian')) || null"))
        self.assertIn("from Obsidian", page.inner_text(".sync-report"))
        self.assertEqual(self.errors, [])

    def test_map_alt_drag_links_missing_notes_and_growth(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        # Two notes with no link between them, both on screen.
        pair = page.evaluate(r"""() => {
            const { state, core } = window.cairn;
            const g = core.buildGraph(state.notes);
            const linked = new Set(g.edges.flatMap((e) => [e.source + '|' + e.target, e.target + '|' + e.source]));
            const ids = g.nodes.map((n) => n.id).filter((id) => document.querySelector(`#map-svg .map-node[data-node="${id}"]`));
            for (const a of ids) for (const b of ids) if (a !== b && !linked.has(a + '|' + b)) return [a, b];
            return null;
        }""")
        self.assertIsNotNone(pair)
        source, target = pair

        def centre(node_id):
            box = page.locator(f'#map-svg .map-node[data-node="{node_id}"] circle').bounding_box()
            return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

        (ax, ay), (bx, by) = centre(source), centre(target)
        page.keyboard.down("Alt")
        page.mouse.move(ax, ay)
        page.mouse.down()
        page.mouse.move((ax + bx) / 2, (ay + by) / 2, steps=6)
        page.mouse.move(bx, by, steps=6)
        page.mouse.up()
        page.keyboard.up("Alt")
        page.wait_for_function(
            r"""([a, b]) => {
                const { state, core } = window.cairn;
                return core.buildGraph(state.notes).edges.some((e) => (e.source === a && e.target === b) || (e.source === b && e.target === a));
            }""",
            arg=[source, target],
        )
        self.assertIn("Linked", page.inner_text(".toast"))

        # Missing notes: link to a note that doesn't exist, show missing notes, double-click to create it.
        page.evaluate(r"""async () => {
            const { state, core } = window.cairn;
            const f = state.files.find((x) => x.path === 'notes/architecture.md');
            const text = f.text.replace(/\s*$/, '') + '\n\nSee [[trail-snacks]].\n';
            const { mtime } = await state.store.write(f.path, text, f.mtime);
            f.text = text;
            f.mtime = mtime;
            const i = state.notes.findIndex((n) => n.path === f.path);
            state.notes[i] = core.noteFromFile(f.path, text, mtime);
            window.cairn.rerender();
        }""")
        page.wait_for_selector('[data-map="show-missing"]')
        page.click('[data-map="show-missing"]')
        page.wait_for_selector("#map-svg .map-ghost")
        ghost = page.evaluate("document.querySelector('#map-svg .map-ghost').dataset.node")
        page.locator(f'#map-svg .map-ghost[data-node="{ghost}"]').dispatch_event("dblclick")
        page.wait_for_function(
            "(id) => !document.querySelector(`#map-svg .map-ghost[data-node=\"${id}\"]`)",
            arg=ghost,
        )
        unresolved = page.evaluate("window.cairn.core.buildGraph(window.cairn.state.notes).unresolved.map((u) => '?' + u.target.toLowerCase())")
        self.assertNotIn(ghost, unresolved)
        self.assertEqual(ghost, "?trail-snacks")
        created = page.evaluate("window.cairn.state.notes.find((n) => n.path === 'notes/trail-snacks.md')?.title")
        self.assertEqual(created, "Trail snacks")

        # Growth time-lapse: everything starts hidden, then the whole map is back.
        page.click('[data-map="grow"]')
        page.wait_for_selector("#map-growth:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#map-svg .future')", timeout=15000)
        self.assertEqual(self.errors, [])

    def test_map_3d_mode_turns_selects_and_remembers(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        page.evaluate("localStorage.removeItem('cairn.map')")
        page.click('[data-map="view"][data-view="3d"]')
        page.wait_for_selector("#map-canvas")
        self.assertEqual(page.locator("#map-svg").count(), 0)
        page.wait_for_function("() => window.cairn.map3d && window.cairn.map3d.settled", timeout=15000)
        # Every note has a place in space (canvases too), and the layout really uses depth.
        bodies = page.evaluate("window.cairn.map3d.bodies()")
        canvases = [b for b in bodies if str(b["id"]).startswith("canvas:")]
        self.assertEqual(len(bodies) - len(canvases), DEMO_NOTES)
        self.assertEqual(len(canvases), 1, "the demo canvas is on the map")
        zs = [b["z"] for b in bodies]
        self.assertGreater(max(zs) - min(zs), 40, "the 3D layout should not be flat")
        # Dragging turns the map (a trackball: any direction, no limit).
        if page.get_attribute('[data-map="spin"]', "aria-pressed") == "true":
            page.click('[data-map="spin"]')
        before = page.evaluate("window.cairn.map3d.camera.rot")
        box = page.locator("#map-canvas").bounding_box()
        x, y = box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.75
        page.mouse.move(x, y)
        page.mouse.down()
        for i in range(1, 16):
            page.mouse.move(x + i * 14, y - i * 10)
        page.mouse.up()
        self.assertNotEqual(page.evaluate("window.cairn.map3d.camera.rot"), before)
        page.keyboard.press("f")
        page.wait_for_timeout(600)
        # Clicking a dot selects it and opens the card, as in 2D.
        hub = page.evaluate("""() => {
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const deg = new Map();
            g.edges.forEach((e) => { deg.set(e.source, (deg.get(e.source) || 0) + 1); deg.set(e.target, (deg.get(e.target) || 0) + 1); });
            return [...deg.entries()].sort((a, b) => b[1] - a[1])[0][0];
        }""")
        pos = page.evaluate("(id) => window.cairn.map3d.screenPos(id)", hub)
        page.mouse.click(box["x"] + pos["x"], box["y"] + pos["y"])
        page.wait_for_selector("#map-card:not([hidden])")
        title = page.evaluate("(p) => window.cairn.state.notes.find((n) => n.path === p).title", hub)
        self.assertEqual(page.inner_text("#map-card strong"), title)
        # "Watch it grow" replays the brain in 3D too, then everything is back.
        page.click('[data-map="grow"]')
        page.wait_for_selector("#map-growth:not([hidden])")
        page.wait_for_selector("#map-growth", state="hidden", timeout=15000)
        # The choice is remembered; "2" goes back to the flat map.
        page.reload()
        page.wait_for_selector("#map-canvas")
        page.locator("#map-canvas").focus()
        page.keyboard.press("2")
        page.wait_for_selector("#map-svg text")
        self.assertEqual(page.locator(".map-node").count(), DEMO_NOTES)
        self.assertEqual(self.errors, [])

    def test_map_select_keyboard_local_and_path(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        page.evaluate("localStorage.removeItem('cairn.map')")
        hub = page.evaluate("""() => {
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const deg = new Map();
            g.edges.forEach((e) => { deg.set(e.source, (deg.get(e.source) || 0) + 1); deg.set(e.target, (deg.get(e.target) || 0) + 1); });
            return [...deg.entries()].sort((a, b) => b[1] - a[1])[0][0];
        }""")
        node = page.locator(f'#map-svg .map-node[data-node="{hub}"]')
        node.locator("circle").click()  # the dot itself: the group's box also spans the label, which ignores the pointer
        self.assertIn("selected", node.get_attribute("class"))
        self.assertTrue(page.locator("#map-card").is_visible())
        # Arrow keys walk to a linked note.
        before = hub
        for key in ("ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"):
            page.keyboard.press(key)
            now = page.evaluate("document.querySelector('#map-svg .map-dot.selected')?.dataset.node")
            if now != before:
                break
        self.assertNotEqual(now, before, "arrow keys should move the selection")
        # L opens the local map of the selected note; Escape goes back to the whole map.
        page.keyboard.press("l")
        page.wait_for_selector(".map-chip.local")
        self.assertEqual(page.locator(".log-sheet").count(), 0, "L on the Map is the local map, not the voice log")
        self.assertLess(page.locator(".map-node").count(), DEMO_NOTES)
        page.locator("#map-svg .map-dot.selected").focus()
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('.map-chip.local')")
        self.assertEqual(page.locator(".map-node").count(), DEMO_NOTES)
        # Path between two notes on the same island.
        ends = page.evaluate("""async () => {
            const G = await import('./graph.js');
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const adj = G.adjacency(g);
            const big = G.components(adj)[0];
            let best = null;
            for (const a of big) for (const b of big) {
                const p = G.shortestPath(adj, a, b);
                if (p && (!best || p.length > best.length)) best = p;
            }
            const title = (id) => window.cairn.state.notes.find((n) => n.path === id).title;
            return { from: title(best[0]), to: title(best.at(-1)), steps: best.length };
        }""")
        page.fill('#map-path [name="from"]', ends["from"])
        page.fill('#map-path [name="to"]', ends["to"])
        page.click('#map-path button[type="submit"]')
        page.wait_for_selector(".path-steps li")
        self.assertEqual(page.locator(".path-steps li").count(), ends["steps"])
        self.assertEqual(page.locator(".map-edge.on-path").count(), ends["steps"] - 1)
        self.assertEqual(self.errors, [])

    def test_map_filters_and_settings_persist(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        tasks = page.evaluate("window.cairn.state.notes.filter((n) => n.type === 'task').length")
        page.click('[data-map="type"][data-type="task"]')
        page.wait_for_function("(n) => document.querySelectorAll('.map-node').length === n", arg=tasks)
        page.check('[data-map-setting="arrows"]')
        page.wait_for_selector(".map-edges.arrows", state="attached")
        page.reload()
        page.wait_for_selector("#map-svg text")
        self.assertEqual(page.locator(".map-node").count(), tasks)
        page.click('[data-map="clear-filters"]')
        page.wait_for_function("() => document.querySelectorAll('.map-node').length === %d" % DEMO_NOTES)
        orphans = page.evaluate("""() => {
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const linked = new Set(g.edges.flatMap((e) => [e.source, e.target]));
            return g.nodes.filter((n) => !linked.has(n.id)).length;
        }""")
        page.uncheck('[data-map-setting="orphans"]')
        page.wait_for_function("(n) => document.querySelectorAll('.map-node').length === n", arg=DEMO_NOTES - orphans)
        page.check('[data-map-setting="orphans"]')
        page.uncheck('[data-map-setting="arrows"]')
        self.assertEqual(self.errors, [])

    def test_map_suggested_link_is_written_without_touching_the_rest(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        page.evaluate("localStorage.removeItem('cairn.map')")
        info = page.evaluate("""async () => {
            const { saveNote, state } = await import('./state.js');
            const notes = state.notes;
            const target = notes.find((n) => n.title.trim().includes(' ') && !notes.some((o) => o !== n && o.title === n.title));
            const g = window.cairn.core.buildGraph(notes);
            const linked = new Set(g.edges.filter((e) => e.source === target.path || e.target === target.path).flatMap((e) => [e.source, e.target]));
            const source = notes.find((n) => n.path !== target.path && !linked.has(n.path));
            await saveNote(source.path, { body: source.body.trimEnd() + `\\n\\nWe should revisit ${target.title} soon.\\n` });
            window.cairn.rerender();
            return { source: source.path, target: target.path, title: target.title, edges: g.edges.length };
        }""")
        before = self.note_text(page, info["source"])
        row = page.locator(f'.suggestion[data-a="{info["source"]}"][data-b="{info["target"]}"]')
        row.locator('[data-map="link"]').click()
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('[[')", arg=info["source"])
        after = self.note_text(page, info["source"])
        link = after[after.index("[["):after.index("]]") + 2] if after.count("[[") > before.count("[[") else None
        self.assertIsNotNone(link)
        self.assertEqual(after.replace(link, info["title"], 1), before, "only the mention may change")
        edges = page.evaluate("window.cairn.core.buildGraph(window.cairn.state.notes).edges.length")
        self.assertEqual(edges, info["edges"] + 1)
        self.assertEqual(self.errors, [])

    def test_graph_algorithms(self):
        page = self.open()
        result = page.evaluate("""async () => {
            const G = await import('./graph.js');
            const core = window.cairn.core;
            const notes = [
                core.noteFromFile('a.md', '# Alpha\\nSee [[b]]. Glow is great; glow is a word. `Beta Note` in code. [[c|Gamma]]'),
                core.noteFromFile('b.md', '---\\ntitle: Beta Note\\n---\\nlinks [[c]]'),
                core.noteFromFile('c.md', '# Gamma\\nmentions Beta Note and Glow'),
                core.noteFromFile('Glow.md', '# Glow\\nproject'),
                core.noteFromFile('d.md', '# Delta\\nalone'),
            ];
            const graph = core.buildGraph(notes);
            const adj = G.adjacency(graph);
            const mentions = G.unlinkedMentions(notes, graph).map((m) => `${m.from}>${m.to}:${m.phrase}x${m.count}`);
            return {
                path: G.shortestPath(adj, 'a.md', 'c.md'),
                none: G.shortestPath(adj, 'a.md', 'd.md'),
                near: [...G.neighbourhood(adj, 'b.md', 1).keys()].sort(),
                islands: G.components(adj).map((c) => c.length),
                mentions,
                linked: G.linkMention('`Glow` and [[x|Glow]] then Glow here', 'Glow', 'Glow'),
                aliased: G.linkMention('see beta note', 'beta note', 'b'),
                retarget: G.retargetLink('[[Alpha — Notes#Plan|the plan]] and `[[Alpha — Notes]]`', 'Alpha — Notes', 'Alpha - Notes'),
            };
        }""")
        self.assertEqual(result["path"], ["a.md", "c.md"])
        self.assertIsNone(result["none"])
        self.assertEqual(result["near"], ["a.md", "b.md", "c.md"])
        self.assertEqual(result["islands"], [3, 1, 1])
        # One-word names count only with the same spelling; code and existing links never count.
        self.assertEqual(sorted(result["mentions"]), ["a.md>Glow.md:Glowx1", "c.md>Glow.md:Glowx1"])
        self.assertEqual(result["linked"], "`Glow` and [[x|Glow]] then [[Glow]] here")
        self.assertEqual(result["aliased"], "see [[b|beta note]]")
        self.assertEqual(result["retarget"], "[[Alpha - Notes#Plan|the plan]] and `[[Alpha — Notes]]`")

    def test_unlinked_mentions_on_a_big_brain(self):
        # Four thousand names in one regular expression froze the Map for seconds in Chrome and for minutes
        # in Safari. The names are found by a letter tree now: same answers, and a big brain stays quick.
        page = self.open()
        result = page.evaluate("""async () => {
            const G = await import('./graph.js');
            const core = window.cairn.core;
            const list = (notes) => G.unlinkedMentions(notes, core.buildGraph(notes)).map((m) => `${m.from}>${m.to}:${m.phrase}x${m.count}`).sort();
            const small = [
                core.noteFromFile('long.md', '# Launch Plan Review\\nThe longer name'),
                core.noteFromFile('short.md', '# Launch Plan\\nThe shorter name'),
                core.noteFromFile('glow.md', '# Glow\\nA project'),
                core.noteFromFile('cafe.md', '# Café Notes\\nAccents'),
                core.noteFromFile('a.md', '# Reader\\nThe LAUNCH PLAN REVIEW is due; then the launch plan again. Launch Plans do not count.'),
                core.noteFromFile('b.md', '# Words\\nGlowing and aGlow and Glow_2 and éGlow do not count, (Glow) and Glow. do. CAFÉ NOTES too.'),
                core.noteFromFile('c.md', '# Hidden\\n`Launch Plan` and [Launch Plan](x.md) and https://x.test/Launch Plan stay hidden'),
            ];
            const big = [];
            for (let i = 0; i < 4000; i += 1) {
                const other = (i * 7 + 13) % 4000;
                big.push(core.noteFromFile(`n${i}.md`, `# Topic number ${i} alpha\\n` + 'plain words of a note go on and on here. '.repeat(40) + `It mentions Topic number ${other} alpha once.`));
            }
            const started = performance.now();
            const found = G.unlinkedMentions(big, core.buildGraph(big));
            return { small: list(small), bigCount: found.length, first: found[0], ms: Math.round(performance.now() - started) };
        }""")
        self.assertEqual(
            result["small"],
            [
                "a.md>long.md:LAUNCH PLAN REVIEWx1",  # the longest name wins, in any case
                "a.md>short.md:launch planx1",  # "Launch Plans" is another word
                "b.md>cafe.md:CAFÉ NOTESx1",
                "b.md>glow.md:Glowx2",  # whole words only
            ],
        )
        self.assertEqual(result["bigCount"], 4000)
        self.assertEqual(result["first"]["to"], "n13.md")
        self.assertLess(result["ms"], 4000, "finding 4,000 names in 4,000 notes must not freeze the page")

    # ------------------------------------------------------------ network: profiles + read-only brains

    def test_private_pictures_use_the_tree_without_guessing(self):
        page = self.open()
        requested = []

        def picture(route):
            requested.append(route.request.url.split('/contents/')[1].split('?')[0])
            route.fulfill(body=b'picture', content_type='image/png', headers={'Access-Control-Allow-Origin': '*'})

        page.route('https://api.github.com/repos/fixture/brain/contents/**', picture)
        result = page.evaluate("""async () => {
            const {GitHubStore} = await import('./store.js');
            const {brainItems} = await import('./github.js');
            sessionStorage.setItem('cairn.session', JSON.stringify({token: 'fixture', login: 'fixture'}));
            const blobs = ['notes/post.md', 'shots/root.png', 'notes/beside.png', 'both.png', 'notes/both.png'];
            const tree = {tree: blobs.map(p => ({type: 'blob', path: 'brain/' + p}))};
            const index = brainItems(tree, 'fixture', 'brain');
            const repo = {owner: 'fixture', name: 'brain', branch: 'main', isPrivate: true};
            const store = new GitHubStore(repo, index);
            const counts = [];
            for (const target of ['shots/root.png', './beside.png', 'both.png', '/both.png', 'missing.png', 'both.png']) {
                counts.push((await store.assetUrls(target, 'notes/post.md')).length);
            }
            const partial = brainItems({...tree, truncated: true}, 'fixture', 'brain');
            const fallback = await new GitHubStore(repo, partial).assetUrls('unknown.png', 'notes/post.md');
            return {counts, fallback: fallback.length};
        }""")
        self.assertEqual(result, {'counts': [1, 1, 1, 1, 0, 1], 'fallback': 1})
        self.assertEqual(requested, ['brain/shots/root.png', 'brain/notes/beside.png', 'brain/notes/both.png',
                                     'brain/both.png', 'brain/notes/unknown.png'])

    def test_github_input_parsing(self):
        page = self.open()
        results = page.evaluate(
            """async () => {
                const gh = await import('./github.js');
                return ['juw12', '@juw12', 'https://github.com/juw12', 'github.com/juw12/cairn/', 'juw12/cairn',
                        'https://github.com/juw12/cairn/tree/main/brain', 'not a user!', '', 'a/b/c..x']
                    .map((v) => gh.parseGithubInput(v));
            }"""
        )
        self.assertEqual(results[0], {"owner": "juw12", "repo": None})
        self.assertEqual(results[1], {"owner": "juw12", "repo": None})
        self.assertEqual(results[2], {"owner": "juw12", "repo": None})
        self.assertEqual(results[3], {"owner": "juw12", "repo": "cairn"})
        self.assertEqual(results[4], {"owner": "juw12", "repo": "cairn"})
        self.assertEqual(results[5], {"owner": "juw12", "repo": "cairn"})
        self.assertIsNone(results[6])
        self.assertIsNone(results[7])

    def test_github_brain_keeps_sessions_past_the_cap(self):
        # A git tree is alphabetical, so a plain "first N notes" cut dropped sessions/ and tasks/ of a big vault:
        # the phone showed 2 live sessions while the PC showed 32.
        page = self.open()
        result = page.evaluate(
            """async () => {
                const gh = await import('./github.js');
                const blob = (path) => ({ type: 'blob', path, sha: 'x' });
                const tree = (archive) => ({ tree: [
                    ...Array.from({ length: archive }, (_, i) => blob(`Archive/${String(i).padStart(5, '0')}.md`)),
                    ...Array.from({ length: 900 }, (_, i) => blob(`sessions/${String(i).padStart(5, '0')}.md`)),
                    ...Array.from({ length: 150 }, (_, i) => blob(`tasks/${i}.md`)),
                ] });
                const count = (items, folder) => items.filter((item) => item.path.startsWith(folder)).length;
                const under = gh.brainItems(tree(1500), 'a', 'b').items;
                const over = gh.brainItems(tree(6000), 'a', 'b').items;
                return {
                    under: [under.length, count(under, 'sessions/'), count(under, 'tasks/'), under[0].path],
                    over: [over.length, count(over, 'sessions/'), count(over, 'tasks/')],
                };
            }"""
        )
        self.assertEqual(result["under"], [2550, 900, 150, "Archive/00000.md"])  # the old cap of 2,000 cut these
        self.assertEqual(result["over"], [5000, 900, 150])

    def test_profile_shows_proof_of_work(self):
        page = self.open("?u=juw12")
        page.wait_for_selector(".proof-grid")
        profile = page.inner_text(".profile-card")
        self.assertIn("Juwen Lie", profile)
        self.assertIn("@juw12", profile)
        proof = page.inner_text(".proof-grid")
        self.assertIn("Get five people to publish a brain", proof)  # doing
        self.assertIn("Explore: look up people and open their brains", proof)  # shipped
        self.assertIn("GitHub is the backend", proof)  # accepted decision
        self.assertIn("AI agents as brain members", proof)  # ready + help-wanted
        self.assertNotIn("Join-request inbox", proof)  # todo tasks are not claimed as work
        self.assertGreaterEqual(page.locator(".repo-row").count(), 1)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ proof of work: claims, levels, vouches

    def test_proof_rules(self):
        page = self.open()
        result = page.evaluate("""async () => {
            const P = await import('./proof.js');
            const core = window.cairn.core;
            const ev = (u) => P.parseEvidence(u);
            const claim = { slug: 'built-x', title: 'Built X' };
            const vouch = (meta) => '---\\n' + Object.entries(meta).map(([k, v]) => k + ': ' + v).join('\\n') + '\\n---\\nSaw it ship.\\n';
            const fileLink = ev('https://github.com/alice/brain/blob/main/vouches/bob/built-x.md');
            return {
                kinds: ['https://github.com/a/b', 'https://github.com/a/b/pull/3', 'https://github.com/a/b/commit/abc1234',
                        'https://github.com/a/b/releases/tag/v1.0', 'https://github.com/a/b/tree/main/src', 'https://example.com/x',
                        'https://github.com/a/b/issues/2'].map((u) => ev(u).kind),
                ownRepo: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { owner: 'bob', stars: 0, forks: 0 }).level,
                starred: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { owner: 'Bob', stars: 3, forks: 0 }).level,
                forked: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { owner: 'bob', stars: 0, forks: 1 }).level,
                notMine: P.judgeEvidence('bob', ev('https://github.com/eve/x'), { owner: 'eve', stars: 99, forks: 9 }).ok,
                fork: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { owner: 'bob', fork: true }).ok,
                prOther: P.judgeEvidence('bob', ev('https://github.com/eve/x/pull/1'), { owner: 'eve', author: 'bob', merged: true }).level,
                prOwn: P.judgeEvidence('bob', ev('https://github.com/bob/x/pull/1'), { owner: 'bob', author: 'bob', merged: true }).level,
                prOpen: P.judgeEvidence('bob', ev('https://github.com/eve/x/pull/1'), { owner: 'eve', author: 'bob', merged: false }).ok,
                prStolen: P.judgeEvidence('bob', ev('https://github.com/eve/x/pull/1'), { owner: 'eve', author: 'eve', merged: true }).ok,
                commit: P.judgeEvidence('bob', ev('https://github.com/eve/x/commit/abcdef1'), { owner: 'eve', author: 'bob' }).level,
                missing: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { error: 'Not found on GitHub.' }).note,
                vouchOk: P.judgeVouch('bob', claim, fileLink, vouch({ vouch_for: 'bob', claim: 'built-x', relation: 'client' })).level,
                vouchSelf: P.judgeVouch('alice', claim, fileLink, vouch({ vouch_for: 'alice', claim: 'built-x' })).ok,
                vouchWrongClaim: P.judgeVouch('bob', claim, fileLink, vouch({ vouch_for: 'bob', claim: 'other' })).ok,
                vouchWrongPerson: P.judgeVouch('bob', claim, fileLink, vouch({ vouch_for: 'carol', claim: 'built-x' })).ok,
                vouchMissing: P.judgeVouch('bob', claim, fileLink, null).ok,
                levelNone: P.claimLevel([]),
                levelBest: P.claimLevel([{ ok: true, level: 2 }, { ok: false, level: 4 }, { ok: true, level: 3 }]),
                isPublic: [{ publish: 'true' }, { publish: true }, { publish: 'yes' }, { publish: 'true', private: 'true' }, {}].map(P.isPublic),
                claims: P.claimsFromNotes([
                    core.noteFromFile('n/a.md', '---\\nclaim: Did A\\nevidence: [https://github.com/a/b]\\ncreated: 2026-01-01\\n---\\n'),
                    core.noteFromFile('n/b.md', '---\\ntitle: no claim\\n---\\n'),
                    core.noteFromFile('n/c.md', '---\\nclaim: Did C\\nevidence:\\n  - https://github.com/a/c/pull/1\\npublish: true\\ncreated: 2026-02-01\\n---\\n'),
                ]).map((c) => [c.slug, c.evidence.map((e) => e.kind).join(','), c.isPublic]),
                url: P.vouchUrl({ subject: 'Bob', claim, voucher: 'alice', repo: 'brain', today: '2026-11-07' }),
                link: P.vouchLink({ subject: 'Bob', claim, voucher: 'alice', repo: 'brain' }),
            };
        }""")
        self.assertEqual(result["kinds"], ["repo", "pr", "commit", "release", "repo", "link", "link"])
        self.assertEqual((result["ownRepo"], result["starred"], result["forked"]), (2, 3, 3))
        self.assertFalse(result["notMine"])
        self.assertFalse(result["fork"])
        self.assertEqual((result["prOther"], result["prOwn"]), (3, 2))
        self.assertFalse(result["prOpen"])
        self.assertFalse(result["prStolen"])
        self.assertEqual(result["commit"], 2)
        self.assertEqual(result["missing"], "Not found on GitHub.")
        self.assertEqual(result["vouchOk"], 4)
        self.assertFalse(result["vouchSelf"])
        self.assertFalse(result["vouchWrongClaim"])
        self.assertFalse(result["vouchWrongPerson"])
        self.assertFalse(result["vouchMissing"])
        self.assertEqual((result["levelNone"], result["levelBest"]), (1, 3))
        self.assertEqual(result["isPublic"], [True, True, False, False, False])
        self.assertEqual(result["claims"], [["c", "pr", True], ["a", "repo", False]])
        self.assertTrue(result["url"].startswith("https://github.com/alice/brain/new/main?filename=vouches%2Fbob%2Fbuilt-x.md&value="))
        self.assertIn("vouch_for%3A%20Bob", result["url"])
        self.assertEqual(result["link"], "https://github.com/alice/brain/blob/main/vouches/bob/built-x.md")
        # The publish rule is the same in Python (cairn publish) and JS (the app's Public/Private pill).
        from cairn.publish import is_public
        cases = [{"publish": "true"}, {"publish": True}, {"publish": "yes"}, {"publish": "true", "private": "true"}, {}]
        self.assertEqual([is_public(c) for c in cases], result["isPublic"])

    def test_profile_claims_are_checked_on_github(self):
        def setup(mock):
            claims = {
                "brain/notes/claim-pr.md": "---\nclaim: Fixed offline sync in someone/tool\nevidence: [https://github.com/someone/tool/pull/7]\ncreated: 2026-11-03\n---\n",
                "brain/notes/claim-vouched.md": "---\nclaim: Ran the Trailmix beta\nevidence: [https://github.com/juw12/cairn/commit/abcdef1]\nattested_by: [https://github.com/alice/brain/blob/main/vouches/juw12/claim-vouched.md]\ncreated: 2026-11-02\n---\n",
                "brain/notes/claim-stolen.md": "---\nclaim: Wrote the parser\nevidence: [https://github.com/someone/tool/pull/8]\ncreated: 2026-11-01\n---\n",
                "brain/notes/claim-bare.md": "---\nclaim: Mentored three interns\ncreated: 2026-10-01\n---\n",
            }
            for path, text in claims.items():
                mock.set_text("juw12/cairn", path, text)
            mock.extra["/repos/someone/tool/pulls/7"] = {"user": {"login": "juw12"}, "merged_at": "2026-11-03T10:00:00Z"}
            mock.extra["/repos/someone/tool/pulls/8"] = {"user": {"login": "someone"}, "merged_at": "2026-11-01T10:00:00Z"}
            mock.extra["/repos/juw12/cairn/commits/abcdef1"] = {"author": {"login": "juw12"}, "commit": {"author": {"date": "2026-11-02T09:00:00Z"}}}
            mock.repos["alice/brain"] = {
                "meta": dict(mock.repos["juw12/cairn"]["meta"], name="brain", full_name="alice/brain", owner={"login": "alice"}),
                "files": {"vouches/juw12/claim-vouched.md": "---\nvouch_for: juw12\nclaim: claim-vouched\nrelation: client\n---\nI ran the beta with Juwen.\n"},
            }

        page = self.open("?u=juw12", setup=setup)
        page.wait_for_selector(".claims .lvl-summary", timeout=15000)
        level = lambda slug: page.inner_text(f'.claim[data-claim="{slug}"] .lvl-badge')  # noqa: E731
        self.assertIn("L3", level("claim-built-cairn"))  # own repo, 3 stars
        self.assertIn("L3", level("claim-pr"))  # merged into someone else's repo
        self.assertIn("L4", level("claim-vouched"))  # alice vouched in her own repo
        self.assertIn("L1", level("claim-stolen"))  # PR opened by someone else
        self.assertIn("L1", level("claim-bare"))  # no evidence at all
        stolen = page.inner_text('.claim[data-claim="claim-stolen"]')
        self.assertIn("Opened by @someone, not @juw12", stolen)
        # Per level: L1 stolen + bare, L2 none, L3 repo + merged PR, L4 the vouched one.
        self.assertEqual(page.locator(".claims .lvl-num").all_inner_texts(), ["2", "0", "2", "1"])
        # Signed out, so anyone may vouch: the button opens GitHub's new-file page in the voucher's repo.
        self.assertGreaterEqual(page.locator('.claim [data-action="vouch"]').count(), 5)
        self.assertEqual(self.errors, [])

    def test_proof_view_lists_claims_and_creates_one(self):
        page = self.open()
        self.go(page, "proof")
        self.assertIn("No claims yet", page.inner_text("#view"))
        page.click('[data-action="new-claim"]')
        page.fill(".modal input", "Shipped offline maps")
        page.click(".modal button[type=submit]")
        page.wait_for_selector(".drawer")
        text = page.evaluate("window.cairn.state.files.find((f) => f.text.includes('claim: Shipped offline maps'))?.text")
        self.assertIsNotNone(text)
        self.assertIn("evidence: []", text)
        self.assertIn("publish: false", text)
        page.keyboard.press("Escape")
        self.go(page, "proof")
        card = page.inner_text(".claim")
        self.assertIn("Shipped offline maps", card)
        self.assertIn("Private", card)
        self.assertIn("Not checked", card)
        self.assertEqual(self.errors, [])

    def test_people_and_proof_are_blocks_with_a_dashboard_and_a_recap(self):
        page = self.open()
        panels = lambda name: page.eval_on_selector_all(f'.vibe-board[data-page="{name}"] .vibe-panel', "els => els.map((e) => e.dataset.panel)")
        folded = lambda name: page.eval_on_selector_all(f'.vibe-board[data-page="{name}"] .vibe-panel.folded', "els => els.map((e) => e.dataset.panel)")
        layout = lambda: self.note_text(page, "CAIRN-LAYOUT.md") or ""

        # Arrange Vibe first, so the file already holds another page's lines.
        self.go(page, "vibe")
        self.sample_week(page)
        page.click('.vp-toggle[data-panel="overview"]')
        page.wait_for_function("() => /^vibe-folded: .*overview/m.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")

        # People: dashboard and recap lead, the rest keeps its order, publishing help starts folded.
        self.go(page, "people")
        page.wait_for_selector('.vibe-board[data-page="people"] .vibe-panel')
        self.assertEqual(panels("people"), ["dashboard", "recap", "mine", "leaderboard", "discover", "publish"])
        self.assertEqual(folded("people"), ["publish"])
        self.assertGreaterEqual(page.locator('[data-page="people"][data-panel="dashboard"] .figure').count(), 4)
        self.assertEqual(page.locator('[data-page="people"][data-panel="recap"] .vp-body').count(), 1)
        self.assertEqual(page.locator("#people-form").count(), 1, "the look-up box stays above the blocks")
        page.click('.vp-move[data-page="people"][data-panel="leaderboard"][data-step="-1"]')
        page.wait_for_function("() => /^people-order: dashboard, recap, (profile, )?leaderboard, (profile, )?mine/m.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")
        self.assertRegex(layout(), r"(?m)^vibe-folded: .*overview", "arranging one page keeps the others")
        self.assertEqual(panels("people")[:4], ["dashboard", "recap", "leaderboard", "mine"])

        # Proof: the dashboard and the recap are there before the first claim.
        self.go(page, "proof")
        self.assertEqual(panels("proof"), ["dashboard", "recap", "claims", "publish"])
        self.assertEqual(folded("proof"), [])
        self.assertIn("No claims yet", page.inner_text('[data-page="proof"][data-panel="recap"]'))
        self.assertTrue(page.inner_text('[data-page="proof"][data-panel="dashboard"] .figure').startswith("0"))
        page.click('[data-action="new-claim"]')
        page.fill(".modal input", "Shipped offline maps")
        page.click(".modal button[type=submit]")
        page.wait_for_selector(".drawer")
        page.keyboard.press("Escape")
        self.go(page, "proof")
        self.assertIn("Shipped offline maps has no evidence links yet", page.inner_text('[data-page="proof"][data-panel="recap"]'))
        self.assertTrue(page.inner_text('[data-page="proof"][data-panel="dashboard"] .figure').startswith("1"))
        page.click('.vp-toggle[data-page="proof"][data-panel="claims"]')
        page.wait_for_selector('.vibe-panel.folded[data-page="proof"][data-panel="claims"]')
        self.assertEqual(page.locator(".claim").count(), 0)
        page.wait_for_function("() => /^proof-folded: claims$/m.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")
        self.assertRegex(layout(), r"(?m)^people-order: ")
        self.assertRegex(layout(), r"(?m)^vibe-folded: .*overview")

        # Reset is per page: Proof goes back, People keeps what was arranged.
        page.click('[data-action="vibe-layout-reset"][data-page="proof"]')
        page.wait_for_selector('.vibe-panel[data-page="proof"][data-panel="claims"]:not(.folded)')
        self.go(page, "people")
        self.assertEqual(panels("people")[:4], ["dashboard", "recap", "leaderboard", "mine"])
        self.assertEqual(self.errors, [])

    def test_every_page_is_blocks_with_a_dashboard_and_a_recap(self):
        page = self.open()
        panels = lambda name: page.eval_on_selector_all(f'.vibe-board[data-page="{name}"] .vibe-panel', "els => els.map((e) => e.dataset.panel)")
        folded = lambda name: page.eval_on_selector_all(f'.vibe-board[data-page="{name}"] .vibe-panel.folded', "els => els.map((e) => e.dataset.panel)")
        pages = {
            "home": ["needs", "dashboard", "chart", "recap", "log", "focus"],
            "board": ["dashboard", "recap", "cards"],
            "ideas": ["dashboard", "recap", "cards"],
            "decisions": ["dashboard", "recap", "log"],
            "journal": ["dashboard", "recap", "calendar", "mind", "search", "pages"],
            "map": ["graph", "dashboard", "recap"],
            "canvas": ["dashboard", "recap", "boards"],
            "notes": ["dashboard", "recap", "all"],
            "code": ["dashboard", "recap", "inbox"],
            "autopilot": ["dashboard", "recap", "queue"],
        }
        for name, expected in pages.items():
            with self.subTest(page=name):
                self.go(page, name)
                page.wait_for_selector(f'.vibe-board[data-page="{name}"] .vibe-panel')
                self.assertEqual(panels(name), expected)
                self.assertEqual(folded(name), ["pages"] if name == "journal" else [])
                dashboard = f'[data-page="{name}"][data-panel="dashboard"]'
                recap = f'[data-page="{name}"][data-panel="recap"]'
                self.assertGreaterEqual(page.locator(f"{dashboard} :is(.figure, .stat-card)").count(), 4)
                self.assertGreaterEqual(page.locator(f"{recap} :is(.page-recap li, .page-recap-empty)").count(), 1)
                # Fold the recap to its one line, then open it again.
                page.click(f'.vp-toggle[data-page="{name}"][data-panel="recap"]')
                page.wait_for_selector(f".vibe-panel.folded{recap}")
                self.assertTrue(page.inner_text(f"{recap} .vp-sum").strip(), "a folded block says what is in it")
                self.assertEqual(page.locator(f"{recap} .vp-body").count(), 0)
                page.click(f'.vp-toggle[data-page="{name}"][data-panel="recap"]')
                page.wait_for_selector(f".vibe-panel{recap}:not(.folded) .vp-body")
        # The tools keep working inside their block: the map draws, and a recap row opens its note.
        self.go(page, "map")
        page.wait_for_selector("#map-svg text")
        self.assertEqual(page.locator(".map-node").count(), DEMO_NOTES)
        self.go(page, "home")
        self.assertIn("Today's log", page.inner_text('.vibe-panel[data-page="home"][data-panel="log"] .vp-title'))
        page.click('[data-page="home"][data-panel="recap"] .page-recap [data-open]')
        page.wait_for_selector(".drawer")
        self.assertEqual(self.errors, [])

    def test_today_carries_the_conversation_between_computers(self):
        page = self.open()
        block = '.vibe-board[data-page="home"] section[data-panel="handoffs"]'
        # One computer, nothing said: the page stays as plain as it was.
        page.wait_for_selector('.vibe-board[data-page="home"] section[data-panel="focus"]')
        self.assertEqual(page.locator(block).count(), 0)

        page.evaluate(DEVICES_SEED)
        page.wait_for_selector(block)
        # The web app is no computer, but it takes part under a name of its own.
        self.assertIn("Nothing is waiting for this device", page.inner_text(block))
        self.assertIn("This device is called Web app.", page.inner_text(f"{block} .ho-name"))

        # This is the MacBook Pro, and the other computer has left it something.
        page.evaluate("""() => {
          const s = window.cairn.state;
          const core = window.cairn.core;
          const message = core.handoffMessage("ASUS EXPERTBOOK", "Check Buddy on a real Mac", "Run the **self-test** and say what the log says.",
            "2026-11-20T09:00:00", s.files.map((f) => f.path), { to: ["MacBook Pro"], sent: "2026-11-20T02:00:00Z" });
          s.files.push({ path: message.path, text: message.text, mtime: Date.now() });
          s.vibeHere = "MacBook Pro";
          s.notes = s.files.filter((f) => f.path.endsWith(".md")).map((f) => core.noteFromFile(f.path, f.text, f.mtime));
          window.cairn.rerender();
        }""")
        page.wait_for_selector(f"{block} .ho-row")
        handoffs = lambda: page.evaluate("() => window.cairn.state.files.filter((f) => f.path.startsWith('handoffs/')).map((f) => f.text)")  # noqa: E731
        # The files are listed by name, not by time: find a message by its title.
        message = lambda title: next(text for text in handoffs() if f"title: {title}\n" in text)  # noqa: E731
        slug_of = lambda title: page.evaluate(  # noqa: E731
            "(t) => window.cairn.state.notes.find((n) => n.meta.type === 'handoff' && n.title === t).slug", title)
        self.assertIn("for this computer", page.inner_text(f"{block} .ho-head").lower())
        self.assertEqual(page.locator(f"{block} .ho-name").count(), 0, "a computer is called what its sessions call it")
        self.assertEqual(page.inner_text(f"{block} .ho-row .ho-new"), "New")
        self.assertIn("from ASUS EXPERTBOOK", page.inner_text(f"{block} .ho-row .hr-meta"))
        self.assertIn("1 for this computer", page.text_content(f"{block} .vp-sum"))
        # It is a conversation, not one of the day's dated updates.
        self.assertNotIn("Check Buddy on a real Mac", page.inner_text('.vibe-board[data-page="home"] section[data-panel="recap"]'))

        # Open it: the message, rendered, and what this computer can say.
        page.click(f"{block} .ho-row")
        page.wait_for_selector(f"{block} .ho-thread.is-open .ho-body strong")
        self.assertIn("MacBook Pro: has not picked it up", page.inner_text(f"{block} .ho-parts"))
        page.click(f'{block} [data-action="handoff-say"][data-says="picked-up"]')
        page.wait_for_selector(f"{block} .ho-row .hr-badge.warn")
        self.assertEqual(len(handoffs()), 2)
        self.assertIn("from: MacBook Pro\n", message("Picked up"))
        self.assertIn("says: picked-up\n", message("Picked up"))
        self.assertIn('thread: "[[2026-11-20-0900-asus-expertbook-check-buddy-on-a-real-mac]]"', message("Picked up"))
        # Its time is there twice: on this computer's clock, and in UTC for a computer in another time zone.
        self.assertRegex(message("Picked up"), r"created: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\nsent: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n")
        self.assertNotIn("after:", message("Picked up"), "it follows the first message, which the thread names")
        self.assertEqual(page.locator(f'{block} [data-action="handoff-say"][data-says="picked-up"]').count(), 0)

        # An answer; what is typed survives the page drawing itself again.
        page.fill("#handoff-reply", "Which Buddy version?")
        page.evaluate("() => window.cairn.rerender()")
        self.assertEqual(page.input_value("#handoff-reply"), "Which Buddy version?")
        page.click(f'{block} .ho-reply button[type="submit"]')
        page.wait_for_function("() => window.cairn.state.files.filter((f) => f.path.startsWith('handoffs/')).length === 3")
        self.assertIn("says: note\n", message("Which Buddy version?"))
        # It names the message it follows, so a wrong clock cannot put it before that one.
        self.assertIn(f'after: "[[{slug_of("Picked up")}]]"', message("Which Buddy version?"))
        self.assertEqual(page.input_value("#handoff-reply"), "")
        self.assertEqual(page.locator(f"{block} .ho-msg").count(), 3)
        self.assertEqual(page.locator(f"{block} .ho-msg.own").count(), 2)
        self.assertEqual(page.eval_on_selector_all(f"{block} .ho-msg .ho-line", "els => els.map((e) => e.textContent)"), ["Which Buddy version?"])

        # Done, with a last line: the conversation is settled and leaves the list.
        page.fill("#handoff-reply", "24 of 24 checks pass.")
        page.click(f'{block} [data-action="handoff-say"][data-says="done"]')
        page.wait_for_selector(f'{block} [data-action="handoff-settled"]')
        self.assertEqual(len(handoffs()), 4)
        self.assertIn("says: done\n", message("24 of 24 checks pass."))
        self.assertEqual(page.locator(f"{block} .ho-row").count(), 0)
        self.assertIn("Nothing is waiting for this computer", page.inner_text(block))
        page.click(f'{block} [data-action="handoff-settled"]')
        page.wait_for_selector(f"{block} .ho-row")
        self.assertIn("Check Buddy on a real Mac", page.inner_text(f"{block} .ho-row"))
        page.click(f'{block} [data-action="handoff-settled"]')

        # Leave one for the other computer. With one other computer it is already ticked.
        page.click(f'{block} [data-action="handoff-write"]')
        page.wait_for_selector("#handoff-form")
        self.assertEqual(page.eval_on_selector_all('#handoff-form input[name="to"]', "els => els.map((e) => [e.value, e.checked])"), [["ASUS EXPERTBOOK", True]])
        self.assertIn("From MacBook Pro.", page.inner_text("#handoff-form"))
        page.fill("#handoff-title", "The fix is in pull request 45")
        page.fill("#handoff-body", "Merge it and cut a release.")
        page.evaluate("() => window.cairn.rerender()")
        self.assertEqual(page.input_value("#handoff-title"), "The fix is in pull request 45")
        self.assertEqual(page.input_value("#handoff-body"), "Merge it and cut a release.")
        page.click('#handoff-form button[type="submit"]')
        page.wait_for_function("() => window.cairn.state.files.filter((f) => f.path.startsWith('handoffs/')).length === 5")
        sent = message("The fix is in pull request 45")
        self.assertIn("from: MacBook Pro\n", sent)
        self.assertIn("to: [ASUS EXPERTBOOK]\n", sent)
        self.assertIn("Merge it and cut a release.\n", sent)
        self.assertEqual(page.locator("#handoff-form").count(), 0)
        self.assertIn("this computer is waiting for", page.locator(f"{block} .ho-head").first.inner_text().lower())
        self.assertIn("to ASUS EXPERTBOOK", page.locator(f"{block} .ho-row .hr-meta").first.inner_text())
        self.assertIn("waiting for 1 answer", page.text_content(f"{block} .vp-sum"))

        # Called off: anyone can close a conversation for every computer.
        page.locator(f"{block} .ho-row").first.click()
        page.wait_for_selector(f"{block} .ho-reply")
        self.assertEqual(page.locator(f'{block} [data-action="handoff-say"][data-says="done"]').count(), 0, "nothing was asked of this computer")
        page.fill("#handoff-reply", "Not needed, it was merged by hand.")
        page.click(f'{block} [data-action="handoff-say"][data-says="closed"]')
        page.wait_for_function("() => window.cairn.state.files.filter((f) => f.path.startsWith('handoffs/')).length === 6")
        self.assertIn("says: closed\n", message("Not needed, it was merged by hand."))
        page.wait_for_function("(sel) => !document.querySelector(sel)", arg=f"{block} .ho-row")
        self.assertIn("Show 2 settled", page.inner_text(f'{block} [data-action="handoff-settled"]'))
        self.assertEqual(self.errors, [])

        # A phone takes part too: under its own name it has things to pick up, finish and close.
        phone = self.open(viewport=(390, 844))
        phone.evaluate(DEVICES_SEED)
        phone.evaluate("""() => {
          const s = window.cairn.state;
          const core = window.cairn.core;
          const message = core.handoffMessage("ASUS EXPERTBOOK", "A long title that has to wrap on a narrow screen without pushing the page wider than it is",
            "", "2026-11-20T09:00:00", s.files.map((f) => f.path), { to: [] });
          s.files.push({ path: message.path, text: message.text, mtime: Date.now() });
          s.notes = s.files.filter((f) => f.path.endsWith(".md")).map((f) => core.noteFromFile(f.path, f.text, f.mtime));
          window.cairn.rerender();
        }""")
        phone.wait_for_selector(f"{block} .ho-row .ho-new")
        self.assertIn("for this device (web app)", phone.inner_text(f"{block} .ho-head").lower())
        phone.click(f'{block} [data-action="handoff-rename"]')
        phone.fill(".modal input", "Juwen's phone")
        phone.click('.modal button[type="submit"]')
        phone.wait_for_function("(sel) => document.querySelector(sel)?.textContent.includes(\"Juwen's phone\")", arg=f"{block} .ho-head")
        self.assertEqual(phone.evaluate("() => localStorage.getItem('cairn.handoffName')"), "Juwen's phone")
        phone.click(f"{block} .ho-row")
        phone.wait_for_selector(f"{block} .ho-reply")
        phone.click(f'{block} [data-action="handoff-say"][data-says="picked-up"]')
        phone.wait_for_selector(f"{block} .ho-row .hr-badge.warn")
        said = phone.evaluate("() => window.cairn.state.files.filter((f) => f.path.startsWith('handoffs/')).map((f) => f.text)")
        self.assertTrue(any("from: Juwen's phone\n" in text and "says: picked-up\n" in text for text in said), said)
        self.assertEqual(phone.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), True)
        # A computer can now leave something for the phone by its name.
        phone.evaluate("""() => {
          const s = window.cairn.state;
          const core = window.cairn.core;
          const message = core.handoffMessage("MacBook Pro", "Approve the release from the phone", "", "2026-11-20T10:00:00", s.files.map((f) => f.path), { to: ["juwen's  PHONE"] });
          s.files.push({ path: message.path, text: message.text, mtime: Date.now() });
          s.notes = s.files.filter((f) => f.path.endsWith(".md")).map((f) => core.noteFromFile(f.path, f.text, f.mtime));
          window.cairn.rerender();
        }""")
        phone.wait_for_function("(sel) => document.querySelectorAll(sel).length === 2", arg=f"{block} .ho-section:first-child .ho-row")
        self.assertIn("2 for this device", phone.text_content(f"{block} .vp-sum"))

    def test_home_leads_with_what_needs_you_and_what_runs(self):
        page = self.open()
        home = '.vibe-board[data-page="home"]'
        # No agent has worked here yet: no "Working now", no "Computers", and the chart only knows the journal.
        page.wait_for_selector(f"{home} .hc-bar")
        self.assertEqual(page.eval_on_selector_all(".hc-opt[data-metric]", "els => els.map((e) => e.dataset.metric)"), ["entries"])
        page.evaluate(DEVICES_SEED)
        page.evaluate(HOME_SEED)
        page.wait_for_selector(f'{home} [data-panel="now"]')
        panels = page.eval_on_selector_all(f"{home} .vibe-panel", "els => els.map((e) => e.dataset.panel)")
        self.assertEqual(panels, ["needs", "handoffs", "now", "dashboard", "chart", "recap", "devices", "log", "focus"])
        lede = page.inner_text(".today .lede")
        self.assertIn("2 sessions working on 2 computers", lede)
        self.assertIn("5 things waiting for you", lede)

        needs = f'{home} [data-panel="needs"]'
        self.assertEqual(page.locator(f"{needs} .hr-dot.task").count(), 4, "the newest four, the rest behind one link")
        self.assertIn("Decide", page.inner_text(needs))
        self.assertEqual(page.locator(f'{home} [data-panel="now"] .home-row').count(), 2)
        self.assertIn("MacBook Pro", page.inner_text(f'{home} [data-panel="now"]'))
        self.assertEqual(page.locator(f'{home} [data-panel="dashboard"] .figure').count(), 6)
        self.assertEqual(page.locator(f'{home} [data-panel="devices"] .home-device').count(), 2)

        # Latest updates: the dated notes, the automatic run folded into one row, a picture where the note has one.
        updates = f'{home} [data-panel="recap"]'
        self.assertEqual(page.inner_text(f"{updates} .vp-title"), "Latest updates")
        self.assertEqual(page.locator(f"{updates} .home-updates > li").count(), 3)
        self.assertIn("2 more like it", page.inner_text(updates))
        self.assertEqual(page.locator(f"{updates} li.has-thumb .recap-thumb").count(), 1)
        # The first line of an update is what it says, not the name of the picture it opens with.
        self.assertIn("Asked: a Home that leads with what waits for me.", page.inner_text(f"{updates} li.has-thumb"))
        self.assertNotIn("shots/home.png", page.inner_text(updates))
        # A note named for a time that has not come yet shows no time (checked unless it is that minute now).
        if page.evaluate("() => new Date().getHours() * 60 + new Date().getMinutes() < 23 * 60 + 58"):
            self.assertNotIn("23:59", page.inner_text(updates))
        # The demo brain holds no such file, so the frame steps aside instead of showing a broken picture.
        page.wait_for_function("() => getComputedStyle(document.querySelector('.recap-thumb')).display === 'none'")

        # The chart: how far back, what to count, and a day you tap.
        self.assertEqual(page.locator(".hc-bar").count(), 30)
        page.click('.hc-opt[data-range="14"]')
        page.wait_for_function("() => document.querySelectorAll('.hc-bar').length === 14")
        self.assertIn("today", page.inner_text("#home-day .hd-head"))
        self.assertEqual(page.locator("#home-day .hd-list > li").count(), 3, "the day's updates sit under the chart")
        page.locator(".hc-bar").nth(10).click()
        self.assertNotIn("today", page.inner_text("#home-day .hd-head"))
        self.assertEqual(page.locator('.hc-bar[aria-pressed="true"]').count(), 1)
        page.click('.hc-opt[data-metric="tokens"]')
        page.wait_for_selector('.hc-opt[data-metric="tokens"][aria-pressed="true"]')
        self.assertIn("tokens", page.inner_text(".hc-total"))
        self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('cairn.homeChart'))"), {"range": 14, "metric": "tokens"})

        page.click('[data-action="home-needs-all"]')
        page.wait_for_function("() => window.cairn.state.view === 'notes'")
        self.assertEqual(page.evaluate("window.cairn.state.notesQuery"), "tag:needs-me type:task -status:done")
        self.assertEqual(self.errors, [])

    def test_recap_rows_keep_to_themselves_on_a_phone(self):
        for width in (320, 390):
            page = self.open(viewport=(width, 844))
            page.evaluate(DEVICES_SEED)
            page.evaluate(HOME_SEED)
            for name in ("home", "board", "ideas", "decisions", "notes"):
                with self.subTest(width=width, page=name):
                    page.evaluate("(v) => { location.hash = `#/${v}`; }", name)  # the sidebar is a drawer at this width
                    page.wait_for_selector(f'.vibe-board[data-page="{name}"] .page-recap li')
                    self.assertEqual(page.evaluate(RECAP_COLLISIONS), [])
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0)
            self.assertEqual(self.errors, [])

    def test_recap_without_file_times_uses_the_row_and_the_notes_own_date(self):
        # Seen on the phone: every Recap row was folded into the 84px time column, one word a line,
        # because a brain read from GitHub has no file times and the row had no time to show.
        page = self.open(viewport=(390, 844))
        page.evaluate(NO_FILE_TIMES_SEED)
        page.evaluate("() => { location.hash = '#/board'; }")
        page.wait_for_selector('.vibe-board[data-page="board"] .page-recap li')
        rows = page.evaluate(
            """() => [...document.querySelectorAll('.vibe-board[data-page="board"] .page-recap li')].map((li) => ({
              when: li.querySelector(".t")?.textContent || "",
              title: li.querySelector("[data-open]").textContent,
              share: li.querySelector(".txt").getBoundingClientRect().width / li.getBoundingClientRect().width,
            }))"""
        )
        self.assertEqual([row["when"] for row in rows], ["today", "yesterday", rows[2]["when"], ""])
        self.assertIn("named for today", rows[0]["title"])
        self.assertEqual(rows[1]["title"], "Asked yesterday")
        self.assertEqual(rows[2]["title"], "Handle the empty file")
        self.assertRegex(rows[2]["when"], r"^[A-Z][a-z]{2} \d{1,4}$")
        self.assertGreater(rows[3]["share"], 0.95, "a row with no time takes the whole row")
        # The tile above counts the same way: three of the four tasks carry a date from the last week.
        self.assertEqual(page.inner_text('.vibe-board[data-page="board"] .figure:has(.label:text-is("changed this week")) .value'), "3")
        self.assertEqual(page.evaluate(RECAP_COLLISIONS), [])

        # Code inbox: a note that only says where it was synced from is not a task made from code.
        page.evaluate("() => { location.hash = '#/code'; }")
        page.wait_for_selector('.vibe-board[data-page="code"] .page-recap li')
        self.assertEqual(page.locator('.vibe-board[data-page="code"] .page-recap li').count(), 1)
        self.assertIn("Handle the empty file", page.inner_text('.vibe-board[data-page="code"] .page-recap'))
        # A brain with no code beside it (read from GitHub) says so; it used to say "Scanning your code…" for ever.
        page.evaluate("() => { const s = window.cairn.state; s.info.canScan = false; s.todos = null; window.cairn.rerender(); }")
        self.assertIn("No code to scan here", page.inner_text("#view"))
        self.assertNotIn("Scanning", page.inner_text("#view"))
        self.assertEqual(page.locator('[data-action="refresh-todos"]').count(), 0)

        # A file named by a clock that runs ahead still counts as today, a planned day is not a change,
        # and a title that is a whole paragraph is shortened in the row.
        found = page.evaluate(
            """async () => {
              const ui = await import("./ui.js");
              const layout = await import("./vibelayout.js");
              const note = (slug, meta = {}, mtime = 0) => ({ path: `notes/${slug}.md`, slug, title: slug, meta, mtime });
              const ahead = note(`${ui.today()} 2359 named by a clock that runs ahead`);
              const morning = note(`${ui.today()} 0000 written at midnight`, { updated: `${ui.today()} 00:00` });
              const row = document.createElement("div");
              row.innerHTML = layout.noteRecap([{ ...note("long"), title: "word ".repeat(60).trim() }], null, "");
              return {
                ahead: ui.noteWhen(ahead),
                plan: ui.noteWhen(note("plan", { date: "2999-01-01" })),
                aheadNotAfterNow: ui.noteStamp(ahead) <= Date.now(),
                last: [note("old file time", {}, 5), ahead, morning].sort(ui.byRecent)[2].slug,
                shown: row.querySelector("[data-open]").textContent.length,
                full: row.querySelector("[data-open]").title.length,
              };
            }"""
        )
        self.assertEqual(found, {"ahead": "today", "plan": "", "aheadNotAfterNow": True, "last": "old file time", "shown": 110, "full": 299})

        # Journal: a link chip wider than the card shortens its name with dots and keeps its count.
        page.evaluate("() => { location.hash = '#/journal'; }")
        page.wait_for_selector(".memory-card .tags .tag-label")
        chip = page.evaluate(
            """() => {
              const label = [...document.querySelectorAll(".memory-card .tags .tag-label")].find((el) => el.textContent.startsWith("A note whose name"));
              const box = label.parentElement.getBoundingClientRect();
              const count = label.nextElementSibling.getBoundingClientRect();
              const card = label.closest(".memory-card").getBoundingClientRect();
              return { cut: label.scrollWidth > label.clientWidth, dots: getComputedStyle(label).textOverflow, inCard: box.right <= card.right + 1, countShown: count.width > 0 && count.right <= box.right + 1 };
            }"""
        )
        self.assertEqual(chip, {"cut": True, "dots": "ellipsis", "inCard": True, "countShown": True})
        self.assertEqual(self.errors, [])

    def test_block_layout_is_kept_per_page_and_tools_still_work(self):
        page = self.open()
        panels = lambda name: page.eval_on_selector_all(f'.vibe-board[data-page="{name}"] .vibe-panel', "els => els.map((e) => e.dataset.panel)")
        saved = "(pattern) => new RegExp(pattern, 'm').test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')"

        # Board: the tasks move above the recap, and a card still drags from one column to the next.
        self.go(page, "board")
        page.click('.vp-move[data-page="board"][data-panel="cards"][data-step="-1"]')
        page.wait_for_function(saved, arg="^board-order: dashboard, cards, recap$")
        self.assertEqual(panels("board"), ["dashboard", "cards", "recap"])
        todo = page.locator('[data-page="board"][data-panel="dashboard"] .figure .value').first.inner_text()
        card = page.locator('.column[data-column="todo"] .item-card[data-drag]').first
        path = card.get_attribute("data-drag")
        card.drag_to(page.locator('.column[data-column="doing"]'))
        page.wait_for_function("(p) => window.cairn.state.notes.find((n) => n.path === p).status === 'doing'", arg=path)
        self.assertEqual(panels("board"), ["dashboard", "cards", "recap"], "dropping a card moves no block")
        self.assertEqual(page.locator('[data-page="board"][data-panel="dashboard"] .figure .value').first.inner_text(), str(int(todo) - 1))

        # Journal: with the search block folded, a tag in On your mind unfolds it and searches.
        self.go(page, "journal")
        page.wait_for_selector(".memory-stats")
        page.click('.vp-toggle[data-page="journal"][data-panel="search"]')
        page.wait_for_selector('.vibe-panel.folded[data-page="journal"][data-panel="search"]')
        page.wait_for_function(saved, arg="^journal-folded: (pages, search|search, pages)$")
        self.assertEqual(page.locator("#journal-search").count(), 0)
        tag = page.locator(".memory-card .tags .tag").first
        label = tag.inner_text().split()[0]
        tag.click()
        page.wait_for_selector("#journal-search")
        self.assertEqual(page.input_value("#journal-search"), label)
        self.assertGreater(page.locator(".search-hits li").count(), 0)
        # All journal pages start folded; opening the block lists them.
        page.click('.vp-toggle[data-page="journal"][data-panel="pages"]')
        page.wait_for_selector('[data-page="journal"][data-panel="pages"] .all-pages .row-item')

        # Canvas: the recap row opens the canvas, like its card.
        self.go(page, "canvas")
        page.click('[data-page="canvas"][data-panel="recap"] [data-cv-open]')
        page.wait_for_selector(".cv-page #cv-wrap")
        self.assertEqual(page.locator(".vibe-board").count(), 0, "an open canvas is the whole page")

        # One file, one pair of lines per arranged page; Reset is per page.
        layout = self.note_text(page, "CAIRN-LAYOUT.md")
        self.assertRegex(layout, r"(?m)^board-order: dashboard, cards, recap$")
        self.assertNotRegex(layout, r"(?m)^(home|map|canvas)-order:", "a page nobody arranged gets no line")
        self.go(page, "board")
        page.click('[data-action="vibe-layout-reset"][data-page="board"]')
        page.wait_for_function("() => document.querySelectorAll('.vibe-board[data-page=board] .vibe-panel')[1].dataset.panel === 'recap'")
        self.assertRegex(self.note_text(page, "CAIRN-LAYOUT.md"), r"(?m)^journal-folded: ")
        self.assertEqual(self.errors, [])

    def test_chat_and_autopilot_are_blocks_when_a_pc_answers(self):
        page = self.open()
        page.evaluate(PC_STUB)
        panels = lambda name: page.eval_on_selector_all(f'.vibe-board[data-page="{name}"] .vibe-panel', "els => els.map((e) => e.dataset.panel)")

        # Chat: the conversation first, then the numbers and the latest chats.
        self.go(page, "chat")
        page.wait_for_selector("#chat-recap .page-recap li")
        self.assertEqual(panels("chat"), ["talk", "dashboard", "recap"])
        tiles = page.eval_on_selector_all("#chat-dash .figure", "els => els.map((e) => e.innerText.replace(/\\s+/g, ' ').trim())")
        self.assertEqual(tiles[:3], ["3 chats", "1 answering now", "2 active in the last day"])
        self.assertEqual(page.locator("#chat-recap .page-recap li").count(), 3)
        self.assertIn("answering", page.inner_text("#chat-recap .page-recap li:first-child"))
        box = page.locator("#chat-form").bounding_box()
        self.assertLessEqual(box["y"] + box["height"], 900, "the message box stays on screen")
        # Fold the conversation, then pick a chat in the recap: it unfolds on that chat.
        page.click('.vp-toggle[data-page="chat"][data-panel="talk"]')
        page.wait_for_selector('.vibe-panel.folded[data-page="chat"][data-panel="talk"]')
        self.assertEqual(page.locator(".chat-page").count(), 0)
        page.click('#chat-recap [data-chat-open="20261119-00000001"]')
        page.wait_for_selector(".chat-msg.assistant")
        self.assertEqual(page.inner_text(".chat-title"), "Why is the relay slow?")
        page.fill("#chat-input", "and now?")
        page.press("#chat-input", "Enter")
        page.wait_for_function("() => document.querySelector('.chat-msg.user')?.innerText.includes('and now?')")

        # Autopilot: every part is a block; settings start folded.
        self.go(page, "autopilot")
        page.wait_for_selector("#ap-numbers .figure")
        self.assertEqual(panels("autopilot"), ["dashboard", "recap", "goal", "goals", "log", "queue", "settings"])
        self.assertEqual(page.eval_on_selector_all('.vibe-board[data-page="autopilot"] .vibe-panel.folded', "els => els.map((e) => e.dataset.panel)"), ["settings"])
        self.assertEqual(page.locator("#ap-recap .page-recap li").count(), 3)
        self.assertIn("Which version number?", page.inner_text("#ap-recap"))
        page.click('#ap-recap [data-action="ap-select"][data-id="j3"]')
        page.wait_for_function("() => document.querySelector('.ap-detail h3')?.innerText === 'Tidy the README'")
        page.wait_for_function("() => document.querySelector('#ap-log').textContent.includes('round 2')")
        page.click('.vp-toggle[data-page="autopilot"][data-panel="settings"]')
        page.wait_for_selector("#ap-settings-form")
        self.assertEqual(self.errors, [])

    def test_proof_view_of_someone_elses_brain_checks_itself(self):
        page = self.open("?brain=juw12/cairn#/proof", wait=".claim")
        page.wait_for_selector(".lvl-summary", timeout=15000)
        self.assertIn("L3", page.inner_text('.claim[data-claim="claim-built-cairn"] .lvl-badge'))
        self.assertEqual(page.inner_text('.hub-tabs [data-go="proof"] .count'), "1")
        self.assertEqual(page.locator('[data-action="new-claim"]').count(), 0)  # read-only: no create button
        self.assertEqual(self.errors, [])

    def test_unknown_user_and_bad_input(self):
        page = self.open()
        go_to(page, "people")
        page.fill("#people-query", "nobody-here-123")
        page.click("#people-form button[type=submit]")
        page.wait_for_selector(".notice")
        self.assertIn("Not found", page.inner_text("#view"))
        page.fill("#people-query", "not a user!")
        page.click("#people-form button[type=submit]")
        page.wait_for_function("() => document.querySelector('#view').innerText.includes(\"doesn't look like\")")

    def test_typing_survives_background_render(self):
        page = self.open()
        go_to(page, "people")
        page.fill("#people-query", "juw12")
        page.evaluate("window.cairn.rerender()")
        self.assertEqual(page.input_value("#people-query"), "juw12")
        self.assertEqual(self.errors, [])

    def test_read_only_brain(self):
        page = self.open("?brain=juw12/cairn")
        page.wait_for_selector(".ro-banner")
        self.assertTrue(page.locator("#capture-form").is_hidden())
        self.assertEqual(page.evaluate("window.cairn.state.notes.length"), 19)
        join = page.get_attribute(".ro-banner a.btn.primary", "href")
        self.assertTrue(join.startswith("https://github.com/juw12/cairn/issues/new?title="), join)
        self.assertIn("Request%20to%20join", join)
        self.go(page, "board")
        self.assertEqual(page.locator('.item-card[draggable="true"]').count(), 0)
        self.assertEqual(page.locator(".quick-add").count(), 0)
        page.click('.item-card[data-open="tasks/mcp-server.md"]')
        page.wait_for_selector(".drawer")
        self.assertTrue(page.is_disabled("#ed-title"))
        self.assertTrue(page.locator('[data-action="delete"]').is_hidden())
        self.assertEqual(page.locator("#ed-body").count(), 0)
        page.keyboard.press("Escape")
        # No create buttons are offered on someone else's brain...
        self.assertEqual(page.locator('.page-head [data-action="new"]').count(), 0)
        # ...and the action guard still refuses if one is triggered anyway (e.g. a stale render).
        before = page.evaluate("window.cairn.state.files.length")
        page.evaluate(
            """() => {
                const button = document.createElement("button");
                button.dataset.action = "new";
                button.dataset.type = "task";
                button.id = "stale-new";
                document.querySelector("main").appendChild(button);
            }"""
        )
        page.click("#stale-new")
        page.wait_for_selector(".toast")
        self.assertEqual(page.evaluate("window.cairn.state.files.length"), before)
        self.assertEqual(self.errors, [])

    def test_welcome_explore_box(self):
        page = self.open("", wait=".choices")
        page.evaluate("localStorage.clear()")
        page.reload()
        page.wait_for_selector("#explore-form")
        page.fill("#explore-input", "https://github.com/juw12")
        page.press("#explore-input", "Enter")
        page.wait_for_selector(".profile-card")
        self.assertIn("u=juw12", page.url)


if __name__ == "__main__":
    unittest.main()

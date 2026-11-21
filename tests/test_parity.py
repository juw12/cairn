"""Gate B3: Python (cairn/core.py) and JS (docs/core.js) must agree on every shared rule."""

import json
import os
import sys
import unittest
from pathlib import Path

from cairn import core

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

FIXTURES = Path(__file__).parent / "fixtures"

NOTE_TEXTS = [
    "---\ntitle: Ship v1\ntype: task   # comment\nstatus: doing\ntags: [release, \"a, b\"]\n---\nBody [[Offline Sync]].\n",
    "---\naliases:\n  - one\n  - two\nweird:\n  nested: 1\n  more: 2\ntitle: 'single quoted'\n---\n\nbody\n",
    "# Heading only\nno frontmatter",
    "---\n---\nempty header",
    "---\ntitle: unterminated\nbody without end",
    "---\r\ntitle: CRLF\r\n---\r\nwindows body\r\n",
    "---\ntitle: \"Decide: SQLite #1\"\ntags: []\nempty:\n---\n",
]
TITLES = ["Café au lait: déjà vu!", "???", "  Ship   v1  ", "word " * 30, "a\u0903b", "中文 title", "Ünïcödé — dash"]
CAPTURES = ["fb: hard to find the toggle #ui", "Feedback: love it", "todo: ship v1 #release #Web", "idea", "Decision: use SQLite\nbecause offline", "log: fixed #bug-1", "http: not a prefix", "#only #tags", "note:   spaced   title  "]
LINKS = "[[Folder/My Note.md#Goals|alias]] [[Offline Sync]] [[a\\|b]] [[  spaced  ]] [[Café/Déjà Vu]] [[x.MD]]"
VAULT_NOTES = {
    "Inbox/My Note.md": "# Heading differs\nlinks [[2026-11-05]] and [[Projects/Other Note]]",
    "Daily/2026-11-05.md": "- walked",
    "Projects/Other Note.md": "---\ntype: book\n---\nback to [[my note]]",
}
H = lambda h, m=0: {"hash": h, "mtime": m}  # noqa: E731
SYNC_CASES = [
    [{"a.md": H("1"), "s.md": H("s")}, {"b.md": H("2"), "s.md": H("s")}, {}],
    [{"a.md": H("1x"), "b.md": H("2")}, {"a.md": H("1"), "b.md": H("2y")}, {"a.md": "1", "b.md": "2"}],
    [{"gb.md": H("2"), "e.md": H("3n")}, {"gv.md": H("1")}, {"gv.md": "1", "gb.md": "2", "e.md": "3"}],
    [{"c.md": H("v", 10), "d.md": H("v", 30)}, {"c.md": H("b", 20), "d.md": H("b", 20)}, {"c.md": "o"}],
    [{"n.md": H("v")}, {"n.md": H("b")}, {}],
]
EDITS = [
    [{}, None],
    [{"status": "done"}, None],
    [{"title": "Renamed", "tags": ["a", "b c"]}, None],
    [{}, "new body\nline two\n"],
    [{"status": "doing", "tags": []}, "x [[link]]\n"],
]
VIBE_NOTES = {
    "sessions/2026-11-04-dark-mode.md": "---\ntitle: Dark mode\ntype: session\nstatus: shipped\nstarted: 2026-11-04T23:40\nended: 2026-11-05T00:55\ncommits: 3\nadded: 120\nremoved: 8\nmodel: zeta-large\neffort: high\ntokens: 1500\ntokens_in: 1000\ntokens_out: 500\ntokens_cache: 9000\nprompts: 4\n---\n## Log\n\n- 23:40 start: Dark mode\n- 23:50 prompt: add a toggle\n- 9:05 fail: pytest (12s)\n- 00:40 pass: pytest\n* 00:41 pass: again\n- 00:42 bogus: ignored\n- 00:43 skill: /refine app cairn · 6.5 -> 8.7\n- 00:44 skill: /Council verdict ship\n- 00:45 skill: no slash\n- 00:46 skill: /refine again\n- not a log line\n- 00:55 end: shipped\n",
    "sessions/2026-11-05-sync.md": "---\ntitle: Sync\ntype: session\nstatus: dropped\nstarted: 2026-11-05T10:00\nended: 2026-11-05T09:00\ncommits: two\n---\n- 10:00 fail: npm test\n",
    "sessions/2026-11-05-live.md": "---\ntitle: Live one\ntype: session\nstatus: live\nstarted: 2026-02-30T10:00\ncreated: 2026-11-05\n---\n",
    "sessions/2026-11-03-runs.md": "---\ntitle: Runs\ntype: session\nstatus: done\nstarted: 2026-11-03T08:00\nended: 2026-11-03T09:00\nminutes: 12\nmodel: alpha-mini\ntokens: 1500\ntokens_in: 1400\ntokens_out: 100\nprompts: 22\nruns: 22\n---\n- 08:00 prompt: one\n",
    "sessions/2026-11-03-more.md": "---\ntitle: More\ntype: session\nstatus: done\nstarted: 2026-11-03T10:00\nended: 2026-11-03T10:30\nmodel: alpha-mini\ntokens: lots\n---\n- 10:00 prompt: one\n- 10:05 prompt: two\n",
    "sessions/2026-11-01-old.md": "---\ntitle: Old\ntype: session\nstatus: done\nstarted: 2026-11-01T10:00\nended: 2026-11-01T10:30\nmodel: Beta\ntokens: 1500\n---\n",
    "feedback/a.md": "---\ntitle: Hard to find\ntype: feedback\nrating: 3\n---\n",
    "feedback/b.md": "---\ntitle: Love it\ntype: feedback\nstatus: addressed\nrating: 5\n---\n",
    "feedback/c.md": "---\ntitle: Meh\ntype: feedback\nstatus: planned\nrating: 9\n---\n",
    "feedback/d.md": "---\ntitle: Ok\ntype: feedback\nstatus: wontfix\nrating: 4\n---\n",
}
SKILL_CASES = [["/refine app cairn", "note"], ["  /ambitious-founder idea", "note"], ["/council", "prompt"], ["not /skill", "note"], ["/-bad", "note"], ["/Precedent limits", "note"]]
# Conversations between three computers and a phone, as
# [sender, title, body, stamp, to, index of the message it answers, says, sent, index of the message it follows],
# then files written by hand that bend the rules.
HANDOFF_SCRIPT = [
    ["MacBook Pro 15,4", "Buddy 1.13.0 checked on the real Mac", "Self-test: 24 of 24.\n\n- one\n- two\n", "2026-11-20T23:01:05", ["ASUS EXPERTBOOK P5405CSA"], None, "note", "2026-11-20T16:01:05Z", None],
    ["ASUS EXPERTBOOK P5405CSA", "Picked up", "", "2026-11-20T23:02:00", [], 0, "picked-up", "2026-11-20T16:02:00Z", None],
    ["asus  expertbook p5405csa", "Which macOS: 15 or 14?", "", "2026-11-20T23:02:30", [], 0, "note", "2026-11-20T16:02:30Z", 1],
    ["ASUS EXPERTBOOK P5405CSA", "Pull Cairn", "", "2026-11-20T23:03:00", [], None, "note", "", None],
    ["Office PC", "Done", "", "2026-11-20T23:04:00", [], 3, "done", "", None],
    ["ASUS EXPERTBOOK P5405CSA", "Café pictures: rebuild them!", "They show 0.6.3.", "2026-11-20T22:00:00", ["Office PC", "MacBook Pro 15,4", "office  pc"], None, "note", "", None],
    ["MacBook Pro 15,4", "Done", "", "2026-11-20T22:30:00", [], 5, "done", "", None],
    ["MacBook Pro 15,4", "Old question", "", "2026-11-19T08:00:00", ["ASUS EXPERTBOOK P5405CSA"], None, "note", "", None],
    ["ASUS EXPERTBOOK P5405CSA", "Answered long ago", "", "2026-11-19T09:00:00", [], 7, "done", "", None],
    ["MacBook Pro 15,4", "Old question", "", "2026-11-19T08:00:00", ["all", "Office PC"], None, "note", "", None],
    ["", "  ", "", "2026-11-20T10:00:00", ["MacBook Pro 15,4"], None, "shouted", "", None],
    ["Phone", "A note to myself and the Mac", "", "2026-11-20T12:00:00", ["Phone"], None, "note", "", None],
    ["Phone", "Closed by its writer", "", "2026-11-20T12:30:00", [], 11, "done", "", None],
    ["ASUS EXPERTBOOK P5405CSA", "All in one second", "", "2026-11-20T13:00:00", ["Office PC"], None, "note", "", None],
    ["Office PC", "Finished", "", "2026-11-20T13:00:01", [], 13, "done", "", None],
    ["Office PC", "A word in between", "", "2026-11-20T13:00:01", [], 13, "note", "", None],
    ["Office PC", "Picked up", "", "2026-11-20T13:00:01", [], 13, "picked-up", "", None],
    # a clock an hour behind: by the clocks the answer came before the question it follows
    ["MacBook Pro 15,4", "Which build is live?", "", "2026-11-20T14:30:00", ["ASUS EXPERTBOOK P5405CSA"], None, "note", "2026-11-20T07:30:00Z", None],
    ["MacBook Pro 15,4", "And is the cache cleared?", "", "2026-11-20T14:40:00", [], 17, "note", "2026-11-20T07:40:00Z", None],
    ["ASUS EXPERTBOOK P5405CSA", "Build 87, and yes", "", "2026-11-20T13:45:00", [], 17, "note", "2026-11-20T06:45:00Z", 18],
    ["MacBook Pro 15,4", "Thanks", "", "2026-11-20T14:50:00", [], 17, "note", "2026-11-20T07:50:00Z", 19],
    # two time zones, one of them a phone, closed from the phone
    ["MacBook Pro 15,4", "From Batam at 23:10", "", "2026-11-20T23:10:00", ["Office PC", "Phone"], None, "note", "2026-11-20T16:10:00Z", None],
    ["Phone", "From Berlin at 17:20", "", "2026-11-20T17:20:00", [], 21, "picked-up", "2026-11-20T16:20:00Z", None],
    ["MacBook Pro 15,4", "From Batam at 23:30", "", "2026-11-20T23:30:00", [], 21, "note", "2026-11-20T16:30:00Z", 22],
    ["Phone", "Not needed any more", "", "2026-11-20T17:40:00", [], 21, "closed", "2026-11-20T16:40:00Z", 23],
    ["Office PC", "Too late", "", "2026-11-20T23:50:00", [], 21, "done", "2026-11-20T16:50:00Z", 24],
]
HANDOFF_BY_HAND = {
    "handoffs/by-hand.md": "---\ntitle: Written in an editor\ntype: handoff\nfrom: Office PC\nto: MacBook Pro 15,4\ncreated: 2026-11-20\n---\nOne name, with a comma, not in a list.\n",
    "handoffs/by-hand-answer.md": "---\ntype: handoff\nfrom: MacBook Pro 15,4\nthread: handoffs/by-hand.md\nsays: Done\n---\n# Heading as title\n",
    "notes/moved-out.md": "---\ntitle: Moved to another folder\ntype: handoff\nfrom: Office PC\nto: [All]\nthread: \"[[by-hand|the first one]]\"\nsays: picked-up\ncreated: 2026-11-20T09:00\n---\n",
    "handoffs/orphan.md": "---\ntitle: Its first message never arrived\ntype: handoff\nfrom: Office PC\nthread: \"[[never-synced]]\"\nsays: picked-up\ncreated: 2026-11-20T09:30:00\n---\n",
    "handoffs/not-one.md": "---\ntitle: A plain note that lives here\nfrom: Office PC\nto: [MacBook Pro 15,4]\n---\n",
    "tasks/pick-it-up.md": "---\ntitle: A task tagged handoff\ntype: task\ntags: [handoff]\n---\n",
    "handoffs/ring-a.md": "---\ntitle: Ring A\ntype: handoff\nfrom: Office PC\nthread: by-hand\nafter: \"[[ring-b]]\"\ncreated: 2026-11-20T08:00:00\n---\n",
    "handoffs/ring-b.md": "---\ntitle: Ring B\ntype: handoff\nfrom: Office PC\nthread: by-hand\nafter: handoffs/ring-a.md\nsays: closed\ncreated: 2026-11-20T08:01:00\n---\n",
    "handoffs/follows-itself.md": "---\ntitle: Follows itself\ntype: handoff\nfrom: Phone\nthread: never-synced\nafter: follows-itself\nsent: 2026-11-20T01:00:00Z\n---\n",
}
HANDOFF_DEVICES = ["MacBook Pro 15,4", "ASUS EXPERTBOOK P5405CSA", "office pc", "Phone", "A computer nobody has heard of", ""]
CODE = "x = 1  # TODO: fix\n// FIXME(juwen): leak */\n<!-- HACK temp -->\n-- XXX sql\nprint('TODO no')\n  * TODO in docblock\n"


def python_handoffs():
    files, slugs = dict(HANDOFF_BY_HAND), []
    for sender, title, body, stamp, to, answers, says, sent, follows in HANDOFF_SCRIPT:
        thread = slugs[answers] if answers is not None else ""
        after = slugs[follows] if follows is not None else ""
        message = core.handoff_message(sender, title, body, stamp, list(files), to=to, thread=thread, says=says, sent=sent, after=after)
        files[message["path"]] = message["text"]
        slugs.append(message["path"].rsplit("/", 1)[-1][:-3])
    threads = core.handoff_threads([core.note_from_file(p, t, 0) for p, t in files.items()])
    return {
        "files": files,
        "threads": threads,
        "inbox": [core.handoff_inbox(threads, device) for device in HANDOFF_DEVICES],
        "waits": [[core.handoff_waits_for(thread, device) for device in HANDOFF_DEVICES] for thread in threads],
        "keys": [core.device_key(device) for device in [*HANDOFF_DEVICES, "  MacBook\tPro  15,4 ", None]],
    }


def python_results(demo_files, vault_files):
    notes = [core.note_from_file(p, t, 0) for p, t in demo_files.items()]
    vault = [core.note_from_file(p, t, 0) for p, t in vault_files.items()]
    return {
        "vault_graph": core.build_graph(vault),
        "vault_backlinks": {n["path"]: core.backlinks(vault, n["path"]) for n in vault},
        "vault_links": {p: core.link_targets(t) for p, t in vault_files.items()},
        "edits": {p: [core.update_note_text(t, c, b) for c, b in EDITS] for p, t in [*vault_files.items(), *demo_files.items()]},
        "parse": [core.parse_note(t) for t in NOTE_TEXTS],
        "roundtrip": [core.serialize_note(core.parse_note(t)["meta"], core.parse_note(t)["body"]) for t in NOTE_TEXTS],
        "slug": [core.slugify(t) for t in TITLES],
        "capture": [core.parse_capture(t) for t in CAPTURES],
        "todos": core.scan_todos(CODE, "a.py"),
        "paths": [
            core.note_path("decision", "Use SQLite", ["decisions/0007-x.md", "tasks/a.md"], "2026-11-05"),
            core.note_path("task", "A", ["tasks/a.md", "tasks/a-2.md"], "2026-11-05"),
            core.note_path("journal", "", ["journal/2026-11-05.md"], "2026-11-05"),
        ],
        "notes": [{k: n[k] for k in ("path", "slug", "type", "title", "status", "tags")} for n in notes],
        "graph": core.build_graph(notes),
        "backlinks": core.backlinks(notes, "notes/user-interviews.md"),
        "context": core.context_pack(notes, "trailmix", "2026-11-05"),
        "links": core.extract_links(LINKS),
        "vault": (lambda vn: {
            "types": [n["type"] for n in vn],
            "graph": core.build_graph(vn),
            "backlinks": core.backlinks(vn, "Inbox/My Note.md"),
        })([core.note_from_file(p, t, 0) for p, t in VAULT_NOTES.items()]),
        "sync": [core.plan_sync(*case) for case in SYNC_CASES],
        "handoffs": python_handoffs(),
        "vibe": (lambda vn: {
            "types": [n["type"] for n in vn],
            "statuses": [n["status"] for n in vn],
            "logs": [core.session_log(n["body"]) for n in vn],
            "minutes": [core.session_minutes(n) for n in vn],
            "stats": core.vibe_stats(vn, "2026-11-05", 7),
            "empty": core.vibe_stats([], "2026-03-01", 3),
            "path": core.note_path("session", "Dark mode", ["sessions/2026-11-05-dark-mode.md"], "2026-11-05"),
            "kinds": [core.log_kind(t, k) for t, k in SKILL_CASES],
            "names": [core.skill_name(t) for t, _ in SKILL_CASES],
        })([core.note_from_file(p, t, 0) for p, t in VIBE_NOTES.items()]),
    }


JS_HARNESS = """
async ([inputs, demoFiles, vaultFiles]) => {
  const core = await import('./core.js');
  const notes = Object.entries(demoFiles).map(([p, t]) => core.noteFromFile(p, t, 0));
  const vault = Object.entries(vaultFiles).map(([p, t]) => core.noteFromFile(p, t, 0));
  const edits = {};
  for (const [p, t] of [...Object.entries(vaultFiles), ...Object.entries(demoFiles)]) {
    edits[p] = inputs.edits.map(([c, b]) => core.updateNoteText(t, c, b === null ? undefined : b));
  }
  return {
    vault_graph: core.buildGraph(vault),
    vault_backlinks: Object.fromEntries(vault.map((n) => [n.path, core.backlinks(vault, n.path)])),
    vault_links: Object.fromEntries(Object.entries(vaultFiles).map(([p, t]) => [p, core.linkTargets(t)])),
    edits,
    parse: inputs.notes.map((t) => core.parseNote(t)),
    roundtrip: inputs.notes.map((t) => { const p = core.parseNote(t); return core.serializeNote(p.meta, p.body); }),
    slug: inputs.titles.map((t) => core.slugify(t)),
    capture: inputs.captures.map((t) => core.parseCapture(t)),
    todos: core.scanTodos(inputs.code, 'a.py'),
    paths: [
      core.notePath('decision', 'Use SQLite', ['decisions/0007-x.md', 'tasks/a.md'], '2026-11-05'),
      core.notePath('task', 'A', ['tasks/a.md', 'tasks/a-2.md'], '2026-11-05'),
      core.notePath('journal', '', ['journal/2026-11-05.md'], '2026-11-05'),
    ],
    notes: notes.map((n) => ({ path: n.path, slug: n.slug, type: n.type, title: n.title, status: n.status, tags: n.tags })),
    graph: core.buildGraph(notes),
    backlinks: core.backlinks(notes, 'notes/user-interviews.md'),
    context: core.contextPack(notes, 'trailmix', '2026-11-05'),
    links: core.extractLinks(inputs.links),
    vault: ((vn) => ({
      types: vn.map((n) => n.type),
      graph: core.buildGraph(vn),
      backlinks: core.backlinks(vn, 'Inbox/My Note.md'),
    }))(Object.entries(inputs.vault).map(([p, t]) => core.noteFromFile(p, t, 0))),
    sync: inputs.sync.map((c) => core.planSync(...c)),
    handoffs: (() => {
      const files = { ...inputs.handoffByHand };
      const slugs = [];
      for (const [sender, title, body, stamp, to, answers, says, sent, follows] of inputs.handoffScript) {
        const thread = answers !== null ? slugs[answers] : '';
        const after = follows !== null ? slugs[follows] : '';
        const message = core.handoffMessage(sender, title, body, stamp, Object.keys(files), { to, thread, says, sent, after });
        files[message.path] = message.text;
        slugs.push(message.path.split('/').pop().slice(0, -3));
      }
      const threads = core.handoffThreads(Object.entries(files).map(([p, t]) => core.noteFromFile(p, t, 0)));
      return {
        files,
        threads,
        inbox: inputs.handoffDevices.map((device) => core.handoffInbox(threads, device)),
        waits: threads.map((thread) => inputs.handoffDevices.map((device) => core.handoffWaitsFor(thread, device))),
        keys: [...inputs.handoffDevices, '  MacBook\tPro  15,4 ', null].map((device) => core.deviceKey(device)),
      };
    })(),
    vibe: ((vn) => ({
      types: vn.map((n) => n.type),
      statuses: vn.map((n) => n.status),
      logs: vn.map((n) => core.sessionLog(n.body)),
      minutes: vn.map((n) => core.sessionMinutes(n)),
      stats: core.vibeStats(vn, '2026-11-05', 7),
      empty: core.vibeStats([], '2026-03-01', 3),
      path: core.notePath('session', 'Dark mode', ['sessions/2026-11-05-dark-mode.md'], '2026-11-05'),
      kinds: inputs.skills.map(([t, k]) => core.logKind(t, k)),
      names: inputs.skills.map(([t]) => core.skillName(t)),
    }))(Object.entries(inputs.vibe).map(([p, t]) => core.noteFromFile(p, t, 0))),
  };
}
"""


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class ParityTests(unittest.TestCase):
    def test_python_and_js_agree(self):
        demo = json.loads((FIXTURES / "demo_brain.json").read_text(encoding="utf-8"))["files"]
        vault = json.loads((FIXTURES / "vault_brain.json").read_text(encoding="utf-8"))["files"]
        expected = python_results(demo, vault)
        server, url = start_static_server()
        try:
            with sync_playwright() as p:
                browser = launch_browser(p)
                page = browser.new_page()
                page.goto(url + "core.js")  # any same-origin page can import the module
                inputs = {
                    "notes": NOTE_TEXTS,
                    "titles": TITLES,
                    "captures": CAPTURES,
                    "code": CODE,
                    "edits": EDITS,
                    "links": LINKS,
                    "vault": VAULT_NOTES,
                    "sync": SYNC_CASES,
                    "vibe": VIBE_NOTES,
                    "skills": SKILL_CASES,
                    "handoffScript": HANDOFF_SCRIPT,
                    "handoffByHand": HANDOFF_BY_HAND,
                    "handoffDevices": HANDOFF_DEVICES,
                }
                actual = page.evaluate(JS_HARNESS, [inputs, demo, vault])
                browser.close()
        finally:
            server.shutdown()
        for key in expected:
            with self.subTest(key=key):
                self.assertEqual(json.loads(json.dumps(expected[key])), actual[key])


if __name__ == "__main__":
    unittest.main()

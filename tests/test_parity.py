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
CAPTURES = ["todo: ship v1 #release #Web", "idea", "Decision: use SQLite\nbecause offline", "log: fixed #bug-1", "http: not a prefix", "#only #tags", "note:   spaced   title  "]
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
CODE = "x = 1  # TODO: fix\n// FIXME(juwen): leak */\n<!-- HACK temp -->\n-- XXX sql\nprint('TODO no')\n  * TODO in docblock\n"


def python_results(demo_files):
    notes = [core.note_from_file(p, t, 0) for p, t in demo_files.items()]
    return {
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
    }


JS_HARNESS = """
async ([inputs, demoFiles]) => {
  const core = await import('./core.js');
  const notes = Object.entries(demoFiles).map(([p, t]) => core.noteFromFile(p, t, 0));
  return {
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
  };
}
"""


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class ParityTests(unittest.TestCase):
    def test_python_and_js_agree(self):
        demo = json.loads((FIXTURES / "demo_brain.json").read_text(encoding="utf-8"))["files"]
        expected = python_results(demo)
        server, url = start_static_server()
        try:
            with sync_playwright() as p:
                browser = launch_browser(p)
                page = browser.new_page()
                page.goto(url + "core.js")  # any same-origin page can import the module
                inputs = {"notes": NOTE_TEXTS, "titles": TITLES, "captures": CAPTURES, "code": CODE, "links": LINKS, "vault": VAULT_NOTES, "sync": SYNC_CASES}
                actual = page.evaluate(JS_HARNESS, [inputs, demo])
                browser.close()
        finally:
            server.shutdown()
        for key in expected:
            with self.subTest(key=key):
                self.assertEqual(json.loads(json.dumps(expected[key])), actual[key])


if __name__ == "__main__":
    unittest.main()

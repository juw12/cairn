import json
import unittest
from pathlib import Path

from cairn import core

FIXTURES = Path(__file__).parent / "fixtures"


class FrontmatterTests(unittest.TestCase):
    def test_round_trip_preserves_unknown_and_block_fields(self):
        text = (
            "---\ntitle: Ship v1\ntype: task\nstatus: doing\ntags: [release, \"a, b\"]\n"
            "aliases:\n  - one\n  - two\nweird:\n  nested: 1\n---\nBody [[x]]\n"
        )
        parsed = core.parse_note(text)
        self.assertEqual(parsed["meta"]["tags"], ["release", "a, b"])
        self.assertEqual(parsed["meta"]["aliases"], ["one", "two"])
        self.assertEqual(parsed["meta"]["weird"], {"raw": "weird:\n  nested: 1"})
        again = core.parse_note(core.serialize_note(parsed["meta"], parsed["body"]))
        self.assertEqual(again, parsed)

    def test_no_frontmatter_is_all_body(self):
        self.assertEqual(core.parse_note("# Hello\n"), {"meta": {}, "body": "# Hello\n"})

    def test_inline_comment_and_quotes(self):
        meta = core.parse_note('---\ntype: task   # comment\ntitle: "a: b"\n---\n')["meta"]
        self.assertEqual(meta, {"type": "task", "title": "a: b"})

    def test_values_that_need_quotes_round_trip(self):
        meta = {"title": "Decide: SQLite #1", "tags": ["x"]}
        text = core.serialize_note(meta, "")
        self.assertEqual(core.parse_note(text)["meta"], meta)


class NamingTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(core.slugify("Café au lait: déjà vu!"), "cafe-au-lait-deja-vu")
        self.assertEqual(core.slugify("???"), "untitled")
        self.assertLessEqual(len(core.slugify("word " * 40)), 60)

    def test_note_path_collisions_and_numbering(self):
        existing = ["tasks/ship-v1.md", "decisions/0002-old.md"]
        self.assertEqual(core.note_path("task", "Ship v1", existing, "2026-11-05"), "tasks/ship-v1-2.md")
        self.assertEqual(core.note_path("decision", "Use SQLite", existing, "2026-11-05"), "decisions/0003-use-sqlite.md")
        self.assertEqual(core.note_path("journal", "", existing, "2026-11-05"), "journal/2026-11-05.md")


class CaptureTests(unittest.TestCase):
    def test_prefix_routing_and_tags(self):
        result = core.parse_capture("todo: ship v1 #release #Web")
        self.assertEqual(result["type"], "task")
        self.assertEqual(result["title"], "ship v1")
        self.assertEqual(result["tags"], ["release", "web"])
        self.assertEqual(result["status"], "todo")

    def test_default_is_idea_and_unknown_prefix_kept(self):
        result = core.parse_capture("http: maybe not a prefix")
        self.assertEqual(result["type"], "idea")
        self.assertEqual(result["title"], "http: maybe not a prefix")

    def test_decision_and_body(self):
        result = core.parse_capture("decision: use SQLite\nbecause offline")
        self.assertEqual((result["type"], result["status"], result["body"]), ("decision", "proposed", "because offline"))


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.notes = [
            core.note_from_file("ideas/a.md", "---\ntitle: Alpha\n---\nSee [[Beta Note]] and [[ghost]]."),
            core.note_from_file("notes/beta-note.md", "# Beta Note\nBack to [[a|alpha]]."),
            core.note_from_file("notes/c.md", "Links [[beta-note]] twice [[beta-note]]."),
        ]

    def test_links_backlinks_graph(self):
        self.assertEqual(core.extract_links(self.notes[0]["body"]), ["beta-note", "ghost"])
        self.assertEqual(core.backlinks(self.notes, "notes/beta-note.md"), ["ideas/a.md", "notes/c.md"])
        graph = core.build_graph(self.notes)
        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(len(graph["edges"]), 2)  # a<->beta deduplicated, c->beta

    def test_type_and_title_inference(self):
        note = self.notes[1]
        self.assertEqual((note["type"], note["title"], note["status"]), ("note", "Beta Note", ""))
        self.assertEqual(self.notes[0]["type"], "idea")
        self.assertEqual(self.notes[0]["status"], "raw")


class TodoScanTests(unittest.TestCase):
    def test_comment_styles(self):
        text = "x = 1  # TODO: fix\n// FIXME(juwen): leak */\n<!-- HACK temp -->\nprint('TODO not a comment')\n"
        items = core.scan_todos(text, "a.py")
        self.assertEqual([(i["line"], i["tag"], i["text"]) for i in items], [(1, "TODO", "fix"), (2, "FIXME", "leak"), (3, "HACK", "temp")])


class ContextPackTests(unittest.TestCase):
    def test_demo_brain_pack(self):
        demo = json.loads((FIXTURES / "demo_brain.json").read_text(encoding="utf-8"))
        notes = [core.note_from_file(p, t) for p, t in demo["files"].items()]
        pack = core.context_pack(notes, "trailmix", "2026-11-05")
        for note in notes:
            if note["type"] == "task" and note["status"] in ("todo", "doing"):
                self.assertIn(note["title"], pack)
            if note["type"] == "decision" and note["status"] == "accepted":
                self.assertIn(note["title"], pack)
        self.assertNotIn("Dark mode", pack)  # done tasks stay out
        self.assertLess(len(pack.splitlines()), 200)

    def test_decision_template_has_four_headings(self):
        for heading in ("## Context", "## Options", "## Decision", "## Consequences"):
            self.assertIn(heading, core.TEMPLATES["decision"])


if __name__ == "__main__":
    unittest.main()

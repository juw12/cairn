"""cairn publish --site: a plain-HTML public page from publish: true notes, with nothing private in it."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from cairn import publish, site
from cairn.cli import main

WORK = (
    "---\ntitle: Built X\nclaim: Built X & shipped it\nrole: author\nupdated: 2026-11-10\n"
    "evidence: [https://github.com/me/x, https://example.org/demo/]\ntags: [tools]\npublish: true\n---\n"
    "# Built X\n\nSee [[Secret plan]] and [[Other public|the other one]]. ![[diagram.png]]\n\n"
    "<script>alert(1)</script> and [click](javascript:alert(2)) and https://example.org/page.\n"
)
OTHER = "---\ntitle: Other public\ndate: 2026-11-01\npublish: true\n---\nback to [[Built X]]\n"
PRIVATE = "---\ntitle: Secret plan\n---\nsalary numbers\n"
VETOED = "---\ntitle: Draft\npublish: true\nprivate: true\n---\nnot yet ready\n"


def make_source(root: Path) -> Path:
    files = {
        "notes/built-x.md": WORK,
        "Other public.md": OTHER,
        "Secret plan.md": PRIVATE,
        "notes/draft.md": VETOED,
        ".obsidian/workspace.md": "---\npublish: true\n---\nhidden folder text\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class SiteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source = make_source(self.tmp / "vault")
        self.out = self.tmp / "site"

    def tearDown(self):
        self._tmp.cleanup()

    def everything(self) -> str:
        return "\n".join(path.read_text(encoding="utf-8") for path in sorted(self.out.rglob("*")) if path.is_file())

    def test_only_public_notes_get_pages_and_nothing_private_is_written(self):
        report = site.build(self.source, self.out, "Ada Lovelace")
        self.assertEqual(report["notes"], 2)
        pages = sorted(path.relative_to(self.out).as_posix() for path in self.out.rglob("*.html"))
        self.assertEqual(pages, ["Other public.html", "index.html", "notes/built-x.html"])
        text = self.everything()
        for private in ("salary numbers", "not yet ready", "hidden folder text", "diagram.png"):
            self.assertNotIn(private, text)

    def test_index_lists_the_claim_with_its_evidence(self):
        site.build(self.source, self.out, "Ada Lovelace")
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("<h1>Ada Lovelace</h1>", index)
        self.assertIn("1 piece of work", index)
        self.assertIn("Built X &amp; shipped it", index)
        self.assertIn('<a href="https://github.com/me/x">github.com/me/x</a>', index)
        self.assertIn('<a href="notes/built-x.html">Built X</a>', index)
        self.assertIn('<a href="Other%20public.html">Other public</a>', index)  # no claim: listed under Notes

    def test_note_text_is_escaped_and_only_web_links_survive(self):
        site.build(self.source, self.out)
        page = (self.out / "notes/built-x.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("javascript:", page)
        self.assertIn('<a href="https://example.org/page">https://example.org/page</a>.', page)
        self.assertEqual(page.count("<h1>"), 1)  # the note's own "# Built X" is not repeated

    def test_links_between_public_notes_are_relative_and_private_ones_are_text(self):
        site.build(self.source, self.out)
        page = (self.out / "notes/built-x.html").read_text(encoding="utf-8")
        self.assertIn('<a href="../Other%20public.html">the other one</a>', page)
        self.assertIn("See Secret plan and", page)
        self.assertIn('href="../site.css"', page)
        back = (self.out / "Other public.html").read_text(encoding="utf-8")
        self.assertIn('<a href="notes/built-x.html">Built X</a>', back)

    def test_rebuild_removes_unpublished_pages_and_keeps_hand_added_files(self):
        site.build(self.source, self.out)
        (self.out / "CNAME").write_text("example.org\n", encoding="utf-8")
        self.assertEqual(site.build(self.source, self.out), {"write": [], "delete": [], "notes": 2})
        (self.source / "Other public.md").write_text(OTHER.replace("publish: true\n", ""), encoding="utf-8")
        report = site.build(self.source, self.out)
        self.assertEqual(report["delete"], ["Other public.html"])
        self.assertFalse((self.out / "Other public.html").exists())
        self.assertTrue((self.out / "CNAME").exists())
        self.assertNotIn("Other%20public.html", (self.out / "index.html").read_text(encoding="utf-8"))

    def test_markdown_blocks(self):
        rendered = site.render_markdown(
            "## Plan\n\n- one\n  still one\n  - nested `a<b`\n- [x] done\n\n1. first\n\n"
            "| a | b |\n|---|---|\n| 1 | **2** |\n\n> quoted *text*\n\n```\n<b>raw</b>\n```\n\n---\nlast line\n"
        )
        self.assertIn("<h2>Plan</h2>", rendered)
        self.assertIn("<ul><li>one still one<ul><li>nested <code>a&lt;b</code></li></ul></li><li>&#9745; done</li></ul>", rendered)
        self.assertIn("<ol><li>first</li></ol>", rendered)
        self.assertIn("<th>a</th><th>b</th>", rendered)
        self.assertIn("<td>1</td><td><strong>2</strong></td>", rendered)
        self.assertIn("<blockquote><p>quoted <em>text</em></p></blockquote>", rendered)
        self.assertIn("<pre><code>&lt;b&gt;raw&lt;/b&gt;</code></pre>", rendered)
        self.assertIn("<hr>\n<p>last line</p>", rendered)

    def test_index_has_a_search_box_that_stays_hidden_without_javascript(self):
        site.build(self.source, self.out, "Ada Lovelace")
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn('<form class="search" id="search" role="search" hidden>', index)
        self.assertIn('placeholder="Search 2 notes"', index)
        self.assertIn('<script src="search.js" defer></script>', index)
        self.assertIn('<div id="listing">', index)
        page = (self.out / "notes/built-x.html").read_text(encoding="utf-8")
        self.assertNotIn("search.js", page)  # a note's page needs no script at all

    def test_search_script_carries_public_words_only_and_cannot_add_markup(self):
        site.build(self.source, self.out)
        script = (self.out / "search.js").read_text(encoding="utf-8")
        self.assertTrue(script.isascii())
        notes = json.loads(script[script.index("var notes = ") + len("var notes = "):script.index(";\n  var MOST")])
        self.assertEqual([note["u"] for note in notes], ["Other%20public.html", "notes/built-x.html"])
        built = notes[1]
        self.assertEqual((built["t"], built["d"], built["g"]), ("Built X", "2026-11-10", ["tools"]))
        self.assertTrue(built["x"].startswith("Built X & shipped it See Secret plan and the other one."))
        for private in ("salary numbers", "not yet ready", "hidden folder text", "diagram.png"):
            self.assertNotIn(private, script)
        self.assertNotIn("innerHTML", script)  # note text only ever goes in as text nodes

    def test_plain_text_drops_the_markdown_marks(self):
        text = site.plain_text(
            "## Plan\n\n- [x] ship **v1** with `cairn`\n> see [[notes/Road map|the map]] and [docs](https://example.org/d)\n\n"
            "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode line\n```\n![shot](https://example.org/s.png)\n"
        )
        self.assertEqual(text, "Plan ship v1 with cairn see the map and docs a b 1 2 code line")

    def test_every_page_says_what_it_is_for_search_engines(self):
        site.build(self.source, self.out, "Ada Lovelace")
        page = (self.out / "notes/built-x.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="description" content="Built X &amp; shipped it">', page)
        self.assertIn("<title>Built X &amp; shipped it · Ada Lovelace</title>", page)  # the claim: words people search for
        self.assertIn("<h1>Built X</h1>", page)
        self.assertIn("<title>Other public · Ada Lovelace</title>", (self.out / "Other public.html").read_text(encoding="utf-8"))
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Ada Lovelace · public brain: 1 piece of work with evidence</title>", index)
        other = (self.out / "Other public.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="description" content="back to Built X">', other)
        self.assertNotIn("canonical", page)  # no address was given, so none is claimed
        self.assertFalse((self.out / "sitemap.xml").exists())

    def test_url_adds_each_pages_address_and_a_sitemap(self):
        report = site.build(self.source, self.out, "Ada", "https://ada.example/brain")
        self.assertIn("sitemap.xml", report["write"])
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="canonical" href="https://ada.example/brain/">', index)
        other = (self.out / "Other public.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="canonical" href="https://ada.example/brain/Other%20public.html">', other)
        sitemap = (self.out / "sitemap.xml").read_text(encoding="utf-8")
        ElementTree.fromstring(sitemap)  # well-formed
        self.assertIn("<url><loc>https://ada.example/brain/</loc><lastmod>2026-11-10</lastmod></url>", sitemap)
        self.assertIn("<url><loc>https://ada.example/brain/notes/built-x.html</loc><lastmod>2026-11-10</lastmod></url>", sitemap)
        self.assertIn("<url><loc>https://ada.example/brain/Other%20public.html</loc><lastmod>2026-11-01</lastmod></url>", sitemap)
        self.assertEqual(sitemap.count("<url>"), 3)
        self.assertIn("Sitemap: https://ada.example/brain/sitemap.xml", (self.out / "robots.txt").read_text(encoding="utf-8"))
        for bad in ("ada.example/brain", "javascript:alert(1)", 'https://ada.example/"><script>'):
            with self.assertRaises(publish.PublishError):
                site.build(self.source, self.out, "Ada", bad)

    def test_pages_describe_themselves_in_the_form_search_engines_read(self):
        (self.source / "tricky.md").write_text(
            '---\ntitle: "A </script><script>alert(1)</script> title"\npublish: true\n---\nbody\n', encoding="utf-8"
        )
        site.build(self.source, self.out, "Ada Lovelace", "https://ada.example/brain/")

        def structured(page: str) -> dict:
            text = (self.out / page).read_text(encoding="utf-8")
            blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)
            self.assertEqual(len(blocks), 1, page)
            return json.loads(blocks[0])

        note = structured("notes/built-x.html")
        self.assertEqual((note["@type"], note["name"], note["headline"]), ("Article", "Built X", "Built X & shipped it"))
        self.assertEqual(note["author"], {"@type": "Person", "name": "Ada Lovelace"})
        self.assertEqual((note["dateModified"], note["keywords"]), ("2026-11-10", "tools"))
        self.assertEqual(note["url"], "https://ada.example/brain/notes/built-x.html")
        self.assertEqual(note["citation"], ["https://github.com/me/x", "https://example.org/demo/"])
        index = structured("index.html")
        self.assertEqual((index["@type"], index["url"]), ("CollectionPage", "https://ada.example/brain/"))
        items = index["mainEntity"]["itemListElement"]
        self.assertEqual(index["mainEntity"]["numberOfItems"], len(items))
        self.assertIn({"@type": "ListItem", "position": 1, "name": "Built X", "url": "https://ada.example/brain/notes/built-x.html"}, items)
        # A title cannot end the block and start a script of its own.
        tricky = (self.out / "tricky.html").read_text(encoding="utf-8")
        self.assertEqual(structured("tricky.html")["name"], "A </script><script>alert(1)</script> title")
        self.assertNotIn("<script>alert(1)", tricky)
        self.assertNotIn("<script>alert(1)", (self.out / "index.html").read_text(encoding="utf-8"))

    def test_a_note_named_index_does_not_replace_the_front_page(self):
        (self.source / "index.md").write_text("---\ntitle: My index note\npublish: true\n---\nbody\n", encoding="utf-8")
        site.build(self.source, self.out, "Ada")
        self.assertIn("<h1>Ada</h1>", (self.out / "index.html").read_text(encoding="utf-8"))

    def test_refuses_to_write_into_the_brain_itself(self):
        with self.assertRaises(publish.PublishError):
            site.build(self.source, self.source)

    def test_cli_publish_site(self):
        code = main(["publish", "--source", str(self.source), "--site", str(self.out), "--title", "Ada"])
        self.assertEqual(code, 0)
        self.assertIn("<h1>Ada</h1>", (self.out / "index.html").read_text(encoding="utf-8"))
        code = main(["publish", "--source", str(self.source), "--site", str(self.out), "--title", "Ada", "--url", "https://ada.example/"])
        self.assertEqual(code, 0)
        self.assertIn("<loc>https://ada.example/</loc>", (self.out / "sitemap.xml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

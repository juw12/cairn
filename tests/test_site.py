"""cairn publish --site: a plain-HTML public page from publish: true notes, with nothing private in it."""

import tempfile
import unittest
from pathlib import Path

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

    def test_refuses_to_write_into_the_brain_itself(self):
        with self.assertRaises(publish.PublishError):
            site.build(self.source, self.source)

    def test_cli_publish_site(self):
        code = main(["publish", "--source", str(self.source), "--site", str(self.out), "--title", "Ada"])
        self.assertEqual(code, 0)
        self.assertIn("<h1>Ada</h1>", (self.out / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

"""Search engines can find Cairn: the app names its one address, says what it is, and has a sitemap."""

import json
import re
import unittest
from pathlib import Path
from xml.etree import ElementTree

DOCS = Path(__file__).resolve().parent.parent / "docs"
ADDRESS = "https://juw12.github.io/cairn/"


class FindableTests(unittest.TestCase):
    def setUp(self):
        self.index = (DOCS / "index.html").read_text(encoding="utf-8")

    def test_the_app_names_one_address_whatever_the_query(self):
        self.assertEqual(re.findall(r'<link rel="canonical" href="([^"]+)">', self.index), [ADDRESS])
        self.assertIn(f'<meta property="og:url" content="{ADDRESS}">', self.index)

    def test_what_the_app_is_can_be_read_by_a_machine(self):
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', self.index, re.S)
        self.assertEqual(len(blocks), 1)
        about = json.loads(blocks[0])
        self.assertEqual((about["@type"], about["name"], about["url"]), ("SoftwareApplication", "Cairn", ADDRESS))
        self.assertEqual(about["offers"]["price"], "0")

    def test_a_reader_without_javascript_gets_words_and_links(self):
        words = re.search(r"<noscript>(.*?)</noscript>", self.index, re.S)[1]
        self.assertIn("<h1>Cairn", words)
        self.assertIn('href="https://juw12.github.io/p/cairn/"', words)

    def test_the_sitemap_lists_the_same_address(self):
        root = ElementTree.fromstring((DOCS / "sitemap.xml").read_text(encoding="utf-8"))
        space = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        self.assertEqual([loc.text for loc in root.findall("s:url/s:loc", space)], [ADDRESS])

    def test_settings_shows_the_public_page_as_a_link_and_everything_else_as_text(self):
        actions = (DOCS / "actions.js").read_text(encoding="utf-8")
        body = actions[actions.index("function publishSite(last)"):]
        body = body[:body.index("\n}\n")]
        self.assertIn('href="${esc(last.site)}"', body)
        self.assertIn("esc(last.siteNote", body)
        self.assertIn("data-publish-site>${publishSite(info.lastPublish)}", actions)


if __name__ == "__main__":
    unittest.main()

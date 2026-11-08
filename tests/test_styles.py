"""The stylesheet parses as intended: one unclosed block silently scopes every later rule to it."""

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def strip_comments_and_strings(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", '""', css)


class StylesheetTests(unittest.TestCase):
    def test_every_block_is_closed(self):
        for sheet in DOCS.glob("*.css"):
            depth = 0
            for number, line in enumerate(strip_comments_and_strings(sheet.read_text(encoding="utf-8")).splitlines(), 1):
                depth += line.count("{") - line.count("}")
                self.assertGreaterEqual(depth, 0, f"{sheet.name}:{number} closes a block that was never opened")
            self.assertEqual(depth, 0, f"{sheet.name} leaves {depth} block(s) open")


if __name__ == "__main__":
    unittest.main()

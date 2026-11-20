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

    def test_recap_rows_are_not_sized_by_a_baseline(self):
        # Safari on a phone sizes a baseline-aligned grid row by one line of text, so a title that wraps
        # ran into the rows under it. The rows start at the top and the title is allowed to wrap.
        css = strip_comments_and_strings((DOCS / "styles.css").read_text(encoding="utf-8"))
        row = re.search(r"^\.page-recap li \{([^}]*)\}", css, re.M).group(1)
        self.assertIn("align-items: start", row)
        title = re.search(r"^\.page-recap \.txt \.link-button \{([^}]*)\}", css, re.M).group(1)
        self.assertIn("white-space: normal", title)
        self.assertIn("display: inline", title)


if __name__ == "__main__":
    unittest.main()

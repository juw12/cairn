"""The service worker's offline list: one module missing from it and the app cannot start without a network."""

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
IMPORT = re.compile(r'^\s*(?:import|export)\s(?:[^;"]*?\sfrom\s*)?"\./([^"]+)"', re.M)


def offline_list() -> list:
    block = re.search(r"const SHELL = \[(.*?)\];", (DOCS / "sw.js").read_text(encoding="utf-8"), re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


def modules_loaded_at_start(entry: str = "app.js") -> set:
    """Every module the page pulls in through static imports, starting from the one index.html loads."""
    seen, todo = set(), [entry]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo += IMPORT.findall((DOCS / name).read_text(encoding="utf-8"))
    return seen


class OfflineListTests(unittest.TestCase):
    def test_every_module_loaded_at_start_is_kept_for_offline(self):
        self.assertGreater(len(modules_loaded_at_start()), 30, "the import scan found too little to mean anything")
        self.assertEqual(sorted(modules_loaded_at_start() - set(offline_list())), [])

    def test_every_listed_file_exists(self):
        # cache.addAll() rejects the whole install when a single file is missing.
        self.assertEqual([name for name in offline_list() if name != "./" and not (DOCS / name).is_file()], [])


if __name__ == "__main__":
    unittest.main()

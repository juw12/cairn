"""The Diary page's figures (docs/diary.js), checked with Node when it is installed."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class DiaryModuleTests(unittest.TestCase):
    def test_diary_module(self):
        result = subprocess.run(
            ["node", str(ROOT / "tests" / "diary_check.mjs")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("diary checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()

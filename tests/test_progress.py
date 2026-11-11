"""Live progress, time left and effort (docs/progress.js), checked with Node when it is installed."""

import io
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cairn.cli import main

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class ProgressModuleTests(unittest.TestCase):
    def test_progress_module(self):
        result = subprocess.run(
            ["node", str(ROOT / "tests" / "progress_check.mjs")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("progress checks passed", result.stdout)


class ProgressFlagTests(unittest.TestCase):
    """`cairn vibe log --progress N` writes the "[N%]" marker the Vibe page reads as the agent's own progress."""

    def run_cli(self, root: str, *args: str) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(["--root", root, *args])

    def test_progress_flag_writes_the_marker(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.run_cli(root, "init"), 0)
            self.assertEqual(self.run_cli(root, "vibe", "start", "add dark mode"), 0)
            self.assertEqual(self.run_cli(root, "vibe", "log", "--progress", "40", "wiring the UI"), 0)
            sessions = list((Path(root) / "brain" / "sessions").glob("*.md"))
            self.assertEqual(len(sessions), 1)
            self.assertIn("note: [40%] wiring the UI", sessions[0].read_text(encoding="utf-8"))

    def test_progress_out_of_range_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.run_cli(root, "init"), 0)
            self.assertEqual(self.run_cli(root, "vibe", "start", "x"), 0)
            self.assertEqual(self.run_cli(root, "vibe", "log", "--progress", "140", "too far"), 2)


if __name__ == "__main__":
    unittest.main()

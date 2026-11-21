"""The MCPB bundle's smoke test (mcpb/smoke_test.py): the bundle must serve the tools of the code, and no
number kept by hand somewhere else may stop a publish when a tool is added."""

import contextlib
import importlib.util
import io
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from cairn import mcp

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("mcpb_smoke_test", ROOT / "mcpb" / "smoke_test.py")
smoke_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke_test)

NAMES = [tool["name"] for tool in mcp.TOOLS]


def build_bundle(target: Path) -> Path:
    """What the publish workflow assembles: the package under server/, the manifest and the licence."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / "cairn").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                bundle.write(path, Path("server") / path.relative_to(ROOT))
        bundle.write(ROOT / "mcpb" / "manifest.json", "manifest.json")
        bundle.write(ROOT / "LICENSE", "LICENSE")
    return target


class SmokeTestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.bundle = str(build_bundle(cls.tmp / "cairn.mcpb"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_smoke(self, *args, expected=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = smoke_test.main([self.bundle, *args], expected=expected)
        return code, out.getvalue(), err.getvalue()

    def test_the_tools_come_from_the_code_beside_the_script(self):
        self.assertEqual(smoke_test.tools_of_this_checkout(), NAMES)
        self.assertIn("handoffs", NAMES)

    def test_a_bundle_of_this_code_passes_with_no_number_given(self):
        code, out, err = self.run_smoke()
        self.assertEqual(code, 0, err)
        self.assertIn(f"{len(NAMES)} tools: ", out)

    def test_a_number_written_before_tools_were_added_does_not_stop_a_publish(self):
        # The publish workflow passes the count from the day it was written. It is a floor, not a match.
        workflow = (ROOT / ".github" / "workflows" / "publish-mcp.yml").read_text(encoding="utf-8")
        written = int(re.search(r"--expect-tools (\d+)", workflow)[1])
        self.assertLessEqual(written, len(NAMES))
        code, _, err = self.run_smoke("--expect-tools", str(written))
        self.assertEqual(code, 0, err)

    def test_a_bundle_with_fewer_tools_than_the_floor_fails(self):
        code, _, err = self.run_smoke("--expect-tools", str(len(NAMES) + 1))
        self.assertEqual(code, 1)
        self.assertIn(f"expected {len(NAMES) + 1} tools or more, the bundle has {len(NAMES)}", err)

    def test_a_bundle_that_does_not_serve_the_tools_of_the_code_fails_and_says_which(self):
        # The code has a tool the bundle lacks (a bundle packed from an older checkout) ...
        code, _, err = self.run_smoke(expected=[*NAMES, "a_tool_added_since"])
        self.assertEqual(code, 1)
        self.assertIn("missing a_tool_added_since", err)
        # ... or the bundle serves one the code no longer has.
        code, _, err = self.run_smoke(expected=[name for name in NAMES if name != "handoffs"])
        self.assertEqual(code, 1)
        self.assertIn("not in the code handoffs", err)
        # A high floor does not hide a wrong list, and the right list in another order is fine.
        self.assertEqual(self.run_smoke("--expect-tools", "1", expected=NAMES[:-1])[0], 1)
        self.assertEqual(self.run_smoke(expected=list(reversed(NAMES)))[0], 0)


if __name__ == "__main__":
    unittest.main()

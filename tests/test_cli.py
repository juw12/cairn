import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from cairn import core
from cairn.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--root", str(self.tmp), *args])
        return code, out.getvalue()

    def test_init_is_idempotent(self):
        self.run_cli("init")
        for folder in core.FOLDERS.values():
            self.assertTrue((self.tmp / "brain" / folder).is_dir())
        readme = self.tmp / "brain" / "README.md"
        readme.write_text("mine", encoding="utf-8")
        _, out = self.run_cli("init")
        self.assertIn("nothing changed", out)
        self.assertEqual(readme.read_text(encoding="utf-8"), "mine")

    def test_add_list_context(self):
        self.run_cli("add", "todo: ship v1 #release")
        self.run_cli("add", "decision:", "use SQLite")
        self.run_cli("add", "an idea with no prefix")
        task = (self.tmp / "brain" / "tasks" / "ship-v1.md").read_text(encoding="utf-8")
        meta = core.parse_note(task)["meta"]
        self.assertEqual((meta["status"], meta["tags"]), ("todo", ["release"]))
        self.assertTrue((self.tmp / "brain" / "decisions" / "0001-use-sqlite.md").exists())
        _, listing = self.run_cli("list", "--type", "task")
        self.assertIn("[todo] ship v1 #release", listing)
        self.run_cli("context")
        pack = (self.tmp / "brain" / "CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("ship v1", pack)
        self.assertIn("use SQLite", pack)

    def test_existing_dot_brain_is_used(self):
        (self.tmp / ".brain").mkdir()
        self.run_cli("add", "note: hello")
        self.assertTrue((self.tmp / ".brain" / "notes" / "hello.md").exists())


if __name__ == "__main__":
    unittest.main()

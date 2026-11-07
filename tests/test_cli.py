import contextlib
import datetime as dt
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


    def journal(self, day):
        return (self.tmp / "brain" / "journal" / f"{day}.md").read_text(encoding="utf-8")

    def test_log_appends_to_todays_page_and_shows_it(self):
        today = dt.date.today().isoformat()
        self.run_cli("log", "walked", "to", "work")
        self.run_cli("add", "log: lunch with @sam #health")
        page = self.journal(today)
        self.assertEqual(page.count("## Log"), 1)
        self.assertRegex(page, r"(?m)^- \d\d:\d\d walked to work$")
        self.assertRegex(page, r"(?m)^- \d\d:\d\d lunch with @sam #health$")
        # "add log:" no longer makes a second dated page.
        self.assertEqual(sorted(p.name for p in (self.tmp / "brain" / "journal").glob("*.md")), [f"{today}.md"])
        _, shown = self.run_cli("log")
        self.assertIn(today, shown)
        self.assertIn("walked to work", shown)
        self.assertIn("lunch with @sam #health", shown)

    def test_log_prefix_creates_linked_note(self):
        today = dt.date.today().isoformat()
        _, out = self.run_cli("log", "todo: book the train #travel")
        self.assertIn("Added task", out)
        self.assertTrue((self.tmp / "brain" / "tasks" / "book-the-train.md").exists())
        self.assertIn("Task: [[book-the-train|book the train]]", self.journal(today))
        _, shown = self.run_cli("log")
        self.assertIn("Task: book the train", shown)
        self.assertNotIn("[[", shown)

    def test_log_other_days_and_ranges(self):
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        self.run_cli("log", "--day", "yesterday", "fixed the build")
        self.run_cli("log", "shipped it")
        self.assertIn("fixed the build", self.journal(yesterday))
        _, one = self.run_cli("log")
        self.assertNotIn("fixed the build", one)
        _, two = self.run_cli("log", "--days", "2")
        self.assertIn("fixed the build", two)
        self.assertIn("shipped it", two)
        _, empty = self.run_cli("log", "--day", "2001-01-01")
        self.assertIn("Nothing logged", empty)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, _ = self.run_cli("log", "--day", "someday", "x")
        self.assertEqual(code, 2)
        self.assertIn("Not a date", err.getvalue())


if __name__ == "__main__":
    unittest.main()

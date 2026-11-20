"""`cairn connect`: the cairn-memory skill reaches the AI apps on this computer, and only where it should."""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cairn import connect, core
from cairn.brain import Brain
from cairn.cli import main


class ConnectCase(unittest.TestCase):
    """A made-up home folder with Vector and Claude Code installed, and a project with a brain."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "home"
        self.settings = self.tmp / "cairn-home"
        (self.home / ".vector").mkdir(parents=True)
        (self.home / ".claude").mkdir()
        self.brain = Brain(self.tmp / "project")
        self.brain.init()
        self.vector_skill = self.home / ".vector" / "skills" / "cairn-memory"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def connect(self, *apps, auto=False, brain=None):
        rows = connect.connect(brain or self.brain, list(apps) or None, auto=auto, home=self.home, settings_folder=self.settings)
        return {row["app"]: row["state"] for row in rows}

    def run_script(self, name, *args):
        return subprocess.run(
            [sys.executable, str(self.vector_skill / name), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )


class ConnectTests(ConnectCase):
    def test_puts_the_skill_in_each_installed_app_and_leaves_the_others_alone(self):
        self.assertEqual(self.connect(), {"vector": "connected", "claude": "connected", "opencode": "absent", "codex": "absent"})
        for name in ("SKILL.md", "recall.py", "remember.py", "common.py", "config.json", connect.MARKER):
            self.assertTrue((self.vector_skill / name).is_file(), name)
        self.assertTrue((self.home / ".claude" / "skills" / "cairn-memory" / "SKILL.md").is_file())
        # an app that is not installed gets no folder made for it
        self.assertFalse((self.home / ".config").exists())
        self.assertFalse((self.home / ".codex").exists())
        self.assertFalse((self.home / ".agents").exists())

    def test_the_skill_is_filled_in_for_this_brain(self):
        self.connect("vector")
        guide = (self.vector_skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("{{", guide)
        self.assertIn(str(self.brain.dir), guide)
        self.assertIn(self.vector_skill.as_posix() + "/recall.py", guide)
        front = re.match(r"^---\n(.*?)\n---\n", guide, re.S).group(1)
        fields = dict(line.split(":", 1) for line in front.splitlines())
        self.assertEqual(fields["name"].strip(), "cairn-memory")
        self.assertIn("Use when", fields["description"])
        self.assertLessEqual(len(fields["description"].strip()), 1024)
        config = json.loads((self.vector_skill / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["brain"], config["write_folder"]), (str(self.brain.dir), ""))
        self.assertTrue((Path(config["package_parent"]) / "cairn" / "connect.py").is_file())

    def test_a_second_run_changes_nothing_and_a_damaged_file_is_put_back(self):
        self.connect("vector")
        before = (self.vector_skill / "SKILL.md").stat().st_mtime_ns
        self.assertEqual(self.connect("vector"), {"vector": "unchanged"})
        self.assertEqual((self.vector_skill / "SKILL.md").stat().st_mtime_ns, before)
        (self.vector_skill / "recall.py").write_text("broken", encoding="utf-8")
        self.assertEqual(self.connect("vector"), {"vector": "updated"})
        self.assertEqual((self.vector_skill / "recall.py").read_text(encoding="utf-8"),
                         (connect.SOURCE / "recall.py").read_text(encoding="utf-8"))

    def test_a_skill_someone_else_made_is_never_touched(self):
        self.vector_skill.mkdir(parents=True)
        (self.vector_skill / "SKILL.md").write_text("my own memory skill", encoding="utf-8")
        self.assertEqual(self.connect("vector"), {"vector": "kept"})
        self.assertEqual(self.connect(auto=True), {"vector": "kept"})
        self.assertEqual((self.vector_skill / "SKILL.md").read_text(encoding="utf-8"), "my own memory skill")
        self.assertEqual([row["state"] for row in connect.disconnect(["vector"], home=self.home)], ["kept"])
        self.assertTrue((self.vector_skill / "SKILL.md").is_file())

    def test_opening_cairn_connects_vector_only(self):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            rows = connect.auto_connect(self.brain, home=self.home, settings_folder=self.settings)
        self.assertEqual({row["app"]: row["state"] for row in rows}, {"vector": "connected"})
        self.assertIn("Vector: connected.", said.getvalue())
        self.assertFalse((self.home / ".claude" / "skills").exists())

    def test_opening_a_second_brain_does_not_take_the_skill_away(self):
        self.connect(auto=True)
        other = Brain(self.tmp / "other")
        other.init()
        self.assertEqual(self.connect(auto=True, brain=other), {"vector": "other-brain"})
        self.assertIn(str(self.brain.dir), (self.vector_skill / "config.json").read_text(encoding="utf-8"))
        # asked by name, it moves
        self.assertEqual(self.connect("vector", brain=other), {"vector": "updated"})
        self.assertEqual(json.loads((self.vector_skill / "config.json").read_text(encoding="utf-8"))["brain"], str(other.dir))

    def test_it_can_be_turned_off(self):
        connect.set_auto(False, self.settings)
        self.assertEqual(connect.auto_connect(self.brain, home=self.home, settings_folder=self.settings), [])
        self.assertFalse(self.vector_skill.exists())
        connect.set_auto(True, self.settings)
        with mock.patch.dict(os.environ, {connect.OFF_SWITCH: "1"}):
            self.assertEqual(connect.auto_connect(self.brain, home=self.home, settings_folder=self.settings), [])
        self.assertFalse(self.vector_skill.exists())

    def test_a_failure_never_stops_cairn_from_opening(self):
        with mock.patch.object(connect, "connect", side_effect=OSError("disk full")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connect.auto_connect(self.brain, home=self.home, settings_folder=self.settings), [])

    def test_disconnect_removes_what_cairn_put_there(self):
        self.connect()
        states = {row["app"]: row["state"] for row in connect.disconnect(home=self.home)}
        self.assertEqual(states, {"vector": "removed", "claude": "removed", "opencode": "absent", "codex": "absent"})
        self.assertFalse(self.vector_skill.exists())
        self.assertTrue((self.home / ".vector").is_dir())

    def test_status_reads_without_writing(self):
        states = {row["app"]: row["state"] for row in connect.status(self.brain, home=self.home, settings_folder=self.settings)}
        self.assertEqual(states, {"vector": "not connected", "claude": "not connected", "opencode": "absent", "codex": "absent"})
        self.assertFalse(self.vector_skill.exists())
        self.connect("vector")
        connect.set_write_folder(self.brain, "devices/laptop", self.settings)
        states = {row["app"]: row["state"] for row in connect.status(self.brain, home=self.home, settings_folder=self.settings)}
        self.assertEqual(states["vector"], "out of date")

    def test_write_folder_is_a_folder_inside_the_brain(self):
        self.assertEqual(connect.set_write_folder(self.brain, "devices\\laptop/", self.settings), "devices/laptop")
        self.assertEqual(connect.write_folder_for(self.brain, self.settings), "devices/laptop")
        for bad in ("../outside", "/etc", "C:/Users", "a//b", "a/./b"):
            with self.subTest(folder=bad), self.assertRaises(connect.ConnectError):
                connect.set_write_folder(self.brain, bad, self.settings)
        self.assertEqual(connect.set_write_folder(self.brain, "", self.settings), "")
        self.assertEqual(connect.write_folder_for(self.brain, self.settings), "")

    def test_every_file_the_skill_needs_ships_with_cairn(self):
        for name in ("SKILL.md", *connect.COPIED):
            self.assertTrue((connect.SOURCE / name).is_file(), name)
        guide = (connect.SOURCE / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", guide))), ["{{BRAIN}}", "{{PYTHON}}", "{{SKILL}}", "{{WRITE_RULES}}"])


class SkillScriptsTests(ConnectCase):
    """The two scripts, run the way an assistant runs them: as files in the app's skills folder."""

    def test_remember_then_recall(self):
        self.connect("vector")
        saved = self.run_script("remember.py", "note", "Coffee order", "--body", "Flat white, no sugar.", "--tags", "preferences")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        note = self.brain.dir / "notes" / "coffee-order.md"
        self.assertIn(f"ok  on this computer: {note}", saved.stdout)
        self.assertIn("no git remote", saved.stdout)
        meta = core.parse_note(note.read_text(encoding="utf-8"))["meta"]
        self.assertEqual((meta["type"], meta["tags"]), ("note", ["preferences"]))

        found = self.run_script("recall.py", "search", "coffee order")
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("notes/coffee-order.md", found.stdout)
        read = self.run_script("recall.py", "read", "coffee-order")
        self.assertIn("Flat white, no sugar.", read.stdout)
        self.assertEqual(self.run_script("recall.py", "recent").returncode, 0)
        self.assertEqual(self.run_script("recall.py", "brief").returncode, 0)

    def test_a_decision_is_saved_accepted_and_listed_as_a_rule(self):
        self.connect("vector")
        saved = self.run_script("remember.py", "decision", "Use SQLite", "--context", "Need a cache.", "--decision", "SQLite, not Redis.")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        note = core.parse_note((self.brain.dir / "decisions" / "0001-use-sqlite.md").read_text(encoding="utf-8"))
        self.assertEqual(note["meta"]["status"], "accepted")
        self.assertIn("## Decision\n\nSQLite, not Redis.", note["body"])
        self.assertIn("0001-use-sqlite.md", self.run_script("recall.py", "rules").stdout)

    def test_with_a_folder_of_its_own_this_computer_writes_only_there(self):
        connect.set_write_folder(self.brain, "devices/laptop", self.settings)
        self.connect("vector")
        self.assertIn("writes only inside `devices/laptop/`", (self.vector_skill / "SKILL.md").read_text(encoding="utf-8"))
        decisions = self.brain.dir / "devices" / "laptop" / "decisions"
        decisions.mkdir(parents=True)
        (decisions / "mac-0007-an-older-one.md").write_text("---\ntitle: An older one\ntype: decision\n---\n", encoding="utf-8")
        before = sorted(path.relative_to(self.brain.dir).as_posix() for path in self.brain.dir.rglob("*.md"))

        self.assertEqual(self.run_script("remember.py", "decision", "Ship on Fridays", "--decision", "Yes.").returncode, 0)
        self.assertEqual(self.run_script("remember.py", "note", "Lesson learnt", "--body", "Check twice.").returncode, 0)
        after = sorted(path.relative_to(self.brain.dir).as_posix() for path in self.brain.dir.rglob("*.md"))
        new = sorted(set(after) - set(before))
        self.assertEqual(len(new), 2, new)
        self.assertEqual(new[0], "devices/laptop/decisions/mac-0008-ship-on-fridays.md")
        self.assertRegex(new[1], r"^devices/laptop/notes/\d{4}-\d{2}-\d{2}-lesson-learnt\.md$")
        meta = core.parse_note((self.brain.dir / new[0]).read_text(encoding="utf-8"))["meta"]
        self.assertEqual((meta["status"], meta["tags"]), ("accepted", ["laptop"]))

        rules = self.run_script("recall.py", "rules").stdout
        self.assertIn("An older one", rules)
        self.assertIn("Ship on Fridays", rules)
        # the same note again is refused, not overwritten
        again = self.run_script("remember.py", "note", "Lesson learnt", "--body", "Something else.")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already exists", again.stderr)
        self.assertIn("Check twice.", (self.brain.dir / new[1]).read_text(encoding="utf-8"))

    def test_a_secret_is_refused(self):
        self.connect("vector")
        refused = self.run_script("remember.py", "note", "Login", "--body", "password: hunter2-hunter2")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("looks like a password", refused.stderr)
        self.assertFalse((self.brain.dir / "notes" / "login.md").exists())


class ConnectCliTests(ConnectCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(Path, "home", return_value=self.home), \
                mock.patch.dict(os.environ, {"CAIRN_HOME": str(self.settings)}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--root", str(self.brain.root), "connect", *args])
        return code, out.getvalue(), err.getvalue()

    def test_connect_status_and_remove(self):
        code, out, _ = self.run_cli("--status")
        self.assertEqual(code, 0)
        self.assertIn("Vector: installed, not connected", out)
        self.assertIn("OpenCode: not on this computer", out)
        code, out, _ = self.run_cli("vector", "--write-folder", "devices/laptop")
        self.assertEqual(code, 0)
        self.assertIn("written only in devices/laptop/", out)
        self.assertIn("Vector: connected.", out)
        self.assertEqual(json.loads((self.vector_skill / "config.json").read_text(encoding="utf-8"))["write_folder"], "devices/laptop")
        self.assertFalse((self.home / ".claude" / "skills").exists())
        code, out, _ = self.run_cli("--remove", "vector")
        self.assertIn("Vector: disconnected", out)
        self.assertFalse(self.vector_skill.exists())

    def test_wrong_names_and_folders_are_explained(self):
        code, _, err = self.run_cli("notepad")
        self.assertEqual(code, 1)
        self.assertIn("Unknown app: notepad", err)
        code, _, err = self.run_cli("--write-folder", "../outside")
        self.assertEqual(code, 1)
        self.assertIn("inside the brain", err)

    def test_off_and_on(self):
        code, out, _ = self.run_cli("--off")
        self.assertEqual(code, 0)
        self.assertIs(connect.read_settings(self.settings)["auto"], False)
        self.assertFalse(self.vector_skill.exists())
        self.run_cli("--on")
        self.assertIs(connect.read_settings(self.settings)["auto"], True)


if __name__ == "__main__":
    unittest.main()

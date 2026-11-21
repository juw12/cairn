"""`cairn connect`: the cairn-memory skill reaches the AI apps on this computer, and only where it should."""
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from cairn import connect, core
from cairn.brain import Brain
from cairn.cli import main
from cairn.memory_skill import cloud


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
        for name in ("SKILL.md", "recall.py", "remember.py", "common.py", "cloud.py", "config.json", connect.MARKER):
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

    def test_the_scripts_get_a_python_with_a_console(self):
        # The Windows desktop app runs on pythonw.exe, which has no console to print a search to.
        folder = self.tmp / "Python313"
        folder.mkdir()
        (folder / "pythonw.exe").write_bytes(b"")
        with mock.patch.object(sys, "executable", str(folder / "pythonw.exe")):
            self.assertEqual(connect.script_python(), str(folder / "pythonw.exe"))  # no python.exe beside it: keep it
            (folder / "python.exe").write_bytes(b"")
            self.assertEqual(connect.script_python(), str(folder / "python.exe"))
        with mock.patch.object(sys, "executable", str(folder / "python.exe")):
            self.assertEqual(connect.script_python(), str(folder / "python.exe"))

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

    def test_the_check_reads_the_git_remote_and_the_scripts_leave_no_files_behind(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.brain.root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                           check=True, capture_output=True)

        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("remote", "add", "origin", str(remote))
        git("add", "-A")
        git("commit", "-q", "-m", "first")
        git("push", "-q", "-u", "origin", "main")
        self.connect("vector")

        saved = self.run_script("remember.py", "note", "Remote check", "--body", "Saved, not sent yet.")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertIn("not committed yet", saved.stdout)
        self.assertIn(f"Copy on the git remote: {remote}", saved.stdout)
        self.assertIn("0 commit(s) not sent; 1 changed file(s) waiting", saved.stdout)
        git("add", "-A")
        git("commit", "-q", "-m", "second")
        checked = self.run_script("remember.py", "check")
        self.assertIn("1 commit(s) not sent; 0 changed file(s) waiting", checked.stdout)

        self.run_script("recall.py", "search", "remote check")
        self.assertEqual(sorted(path.name for path in self.vector_skill.iterdir()),
                         sorted(["SKILL.md", "recall.py", "remember.py", "common.py", "cloud.py", "config.json", connect.MARKER]))
        self.assertEqual(self.connect("vector"), {"vector": "unchanged"})

    def test_a_secret_is_refused(self):
        self.connect("vector")
        refused = self.run_script("remember.py", "note", "Login", "--body", "password: hunter2-hunter2")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("looks like a password", refused.stderr)
        self.assertFalse((self.brain.dir / "notes" / "login.md").exists())


class FakeGitHub:
    """The few GitHub answers the skill asks for, over real HTTP, for one repo held in memory."""

    def __init__(self, files: dict[str, str]):
        self.files = {path: body.encode("utf-8") for path, body in files.items()}
        self.commits = 1
        self.asked: list[str] = []
        self.down = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, status, body, kind="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urllib.parse.unquote(self.path.split("?")[0])
                owner.asked.append("GET " + path)
                if owner.down:
                    return self.answer(503, {"message": "down"})
                if self.headers.get("Authorization") != "Bearer test-token":
                    return self.answer(401, {"message": "Bad credentials"})
                base = "/repos/me/brain"
                if path == base:
                    return self.answer(200, {"default_branch": "main"})
                if path == base + "/branches/main":
                    return self.answer(200, {"commit": {"sha": owner.commit(), "commit": {"committer": {"date": "2026-11-21T02:00:00Z"}}}})
                if path.startswith(base + "/git/trees/"):
                    tree = [{"path": name, "type": "blob", "sha": owner.sha(data)} for name, data in sorted(owner.files.items())]
                    return self.answer(200, {"tree": tree, "truncated": False})
                if path.startswith(base + "/git/blobs/"):
                    wanted = path.rsplit("/", 1)[1]
                    for data in owner.files.values():
                        if owner.sha(data) == wanted:
                            return self.answer(200, {"encoding": "base64", "content": base64.b64encode(data).decode("ascii")})
                    return self.answer(404, {"message": "Not Found"})
                if path.startswith(base + "/zipball/"):
                    packed = io.BytesIO()
                    with zipfile.ZipFile(packed, "w") as archive:
                        for name, data in owner.files.items():
                            archive.writestr("me-brain-abc123/" + name, data)
                    return self.answer(200, packed.getvalue(), "application/zip")
                if path.startswith(base + "/contents/"):
                    name = path[len(base + "/contents/"):]
                    if name in owner.files:
                        return self.answer(200, {"path": name, "size": len(owner.files[name]), "sha": owner.sha(owner.files[name])})
                return self.answer(404, {"message": "Not Found"})

            def do_PUT(self):
                path = urllib.parse.unquote(self.path)
                owner.asked.append("PUT " + path)
                if owner.down:
                    return self.answer(503, {"message": "down"})
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                name = path[len("/repos/me/brain/contents/"):]
                if name in owner.files:
                    return self.answer(422, {"message": "Invalid request. \"sha\" wasn't supplied."})
                owner.files[name] = base64.b64decode(body["content"])
                owner.commits += 1
                owner.messages = [*getattr(owner, "messages", []), body["message"]]
                return self.answer(201, {"content": {"path": name, "sha": owner.sha(owner.files[name])}, "commit": {"sha": owner.commit()}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def sha(data: bytes) -> str:
        return hashlib.sha1(data).hexdigest()

    def commit(self) -> str:
        return hashlib.sha1(str(self.commits).encode()).hexdigest()

    def count(self, part: str) -> int:
        return sum(1 for line in self.asked if part in line)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class CloudMemoryTests(ConnectCase):
    """The brain's folder is not on this computer: the scripts read the notes from GitHub and save there."""

    def setUp(self):
        super().setUp()
        self.github = FakeGitHub({
            "decisions/0001-use-sqlite.md": "---\ntitle: Use SQLite\ntype: decision\nstatus: accepted\ntags: [storage]\n---\n## Decision\n\nSQLite, not Redis.\n",
            "decisions/0002-maybe-later.md": "---\ntitle: Maybe later\ntype: decision\nstatus: proposed\n---\n",
            "notes/coffee-order.md": "---\ntitle: Coffee order\ntype: note\ntags: [preferences]\n---\nFlat white, no sugar. 咖啡\n",
            "journal/" + __import__("datetime").date.today().isoformat() + ".md": "---\ntitle: today\ntype: journal\n---\n## Log\n\n- 09:00 shipped the thing\n",
            "devices/laptop/decisions/mac-0007-an-older-one.md": "---\ntitle: An older one\ntype: decision\nstatus: accepted\n---\n",
            "Projects/picture.png": "not a note",
        })
        self.addCleanup(self.github.close)
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8", "CAIRN_GITHUB_API": self.github.url,
                    "GITHUB_TOKEN": "test-token", "CAIRN_HOME": str(self.settings)}
        self.env.pop("CAIRN_GITHUB_TOKEN", None)
        self.cache = self.settings / "cloud" / "me_brain"

    def connect_cloud(self, *args):
        out = io.StringIO()
        with mock.patch.object(Path, "home", return_value=self.home), \
                mock.patch.dict(os.environ, {"CAIRN_HOME": str(self.settings)}), contextlib.redirect_stdout(out):
            code = main(["--root", str(self.tmp / "nowhere"), "connect", "vector", "--cloud", "me/brain", *args])
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    def run_script(self, name, *args, python=sys.executable):
        return subprocess.run([python, str(self.vector_skill / name), *args], capture_output=True, text=True,
                              encoding="utf-8", timeout=120, env=self.env)

    def test_a_computer_that_never_had_the_folder_recalls_from_github(self):
        said = self.connect_cloud()
        self.assertIn("Vector: connected.", said)
        guide = (self.vector_skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("on GitHub, `me/brain`", guide)
        self.assertNotIn("{{", guide)
        config = json.loads((self.vector_skill / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["brain"], config["cloud"]["repo"]), ("", "me/brain"))

        found = self.run_script("recall.py", "search", "coffee order")
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn("From GitHub (me/brain, 5 notes", found.stdout)
        self.assertIn("- notes/coffee-order.md (note) Coffee order #preferences", found.stdout)
        self.assertIn("咖啡", found.stdout)
        self.assertEqual(self.github.count("/zipball/"), 0)  # five notes come one by one
        self.assertFalse((self.cache / "Projects").exists())  # pictures are not fetched

        self.assertIn("SQLite, not Redis.", self.run_script("recall.py", "read", "use-sqlite").stdout)
        rules = self.run_script("recall.py", "rules").stdout
        self.assertIn("0001-use-sqlite.md", rules)
        self.assertNotIn("maybe-later", rules)
        self.assertIn("shipped the thing", self.run_script("recall.py", "recent").stdout)
        self.assertIn("No notes match", self.run_script("recall.py", "search", "nothing-like-this").stdout)
        # all of that asked GitHub for the list of notes once
        self.assertEqual(self.github.count("/git/trees/"), 1)

    def test_a_big_brain_comes_in_one_download_and_then_only_what_changed(self):
        for number in range(cloud.ONE_BY_ONE + 5):
            self.github.files[f"notes/filler-{number}.md"] = f"---\ntitle: Filler {number}\ntype: note\n---\nfiller text {number}\n".encode()
        self.connect_cloud()
        self.assertIn("Filler 147", self.run_script("recall.py", "search", "filler 147").stdout)
        self.assertEqual((self.github.count("/zipball/"), self.github.count("/git/blobs/")), (1, 0))

        self.github.files["notes/new-on-another-computer.md"] = b"---\ntitle: New elsewhere\ntype: note\n---\nwritten on the other laptop\n"
        del self.github.files["notes/coffee-order.md"]
        self.github.commits += 1
        state = json.loads((self.cache / cloud.STATE).read_text(encoding="utf-8"))
        state["checked"] = 0  # the minute has passed
        (self.cache / cloud.STATE).write_text(json.dumps(state), encoding="utf-8")
        self.assertIn("notes/new-on-another-computer.md", self.run_script("recall.py", "search", "other laptop").stdout)
        self.assertIn("No notes match", self.run_script("recall.py", "search", "coffee order").stdout)
        self.assertEqual((self.github.count("/zipball/"), self.github.count("/git/blobs/")), (1, 1))

    def test_remember_saves_on_github_and_never_replaces(self):
        self.connect_cloud()
        saved = self.run_script("remember.py", "note", "Tea order", "--body", "Green, no milk.", "--tags", "preferences")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertIn("ok  on GitHub: me/brain/notes/tea-order.md", saved.stdout)
        note = core.parse_note(self.github.files["notes/tea-order.md"].decode("utf-8"))
        self.assertEqual((note["meta"]["title"], note["meta"]["type"], note["meta"]["tags"]), ("Tea order", "note", ["preferences"]))
        self.assertIn("Green, no milk.", note["body"])
        self.assertTrue(self.github.messages[-1].endswith("[skip ci]"))
        self.assertIn("notes/tea-order.md", self.run_script("recall.py", "search", "tea order").stdout)

        again = self.run_script("remember.py", "note", "Tea order", "--body", "Something else.")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already exists", again.stderr)
        self.assertIn(b"Green, no milk.", self.github.files["notes/tea-order.md"])

        decided = self.run_script("remember.py", "decision", "Ship on Fridays", "--decision", "Yes: \"always\".")
        self.assertEqual(decided.returncode, 0, decided.stderr)
        note = core.parse_note(self.github.files["decisions/0003-ship-on-fridays.md"].decode("utf-8"))
        self.assertEqual(note["meta"]["status"], "accepted")
        refused = self.run_script("remember.py", "note", "Login", "--body", "token: abcdef123456")
        self.assertEqual(refused.returncode, 1)
        self.assertNotIn("notes/login.md", self.github.files)
        self.assertIn("The brain is on GitHub: me/brain", self.run_script("remember.py", "check").stdout)

    def test_this_computers_own_folder_is_kept_on_github_too(self):
        self.connect_cloud("--write-folder", "devices/laptop")
        self.assertEqual(self.run_script("remember.py", "decision", "Ship on Fridays", "--decision", "Yes.").returncode, 0)
        self.assertEqual(self.run_script("remember.py", "note", "Lesson learnt", "--body", "Check twice.").returncode, 0)
        new = sorted(path for path in self.github.files if "ship-on" in path or "lesson" in path)
        self.assertEqual(new[0], "devices/laptop/decisions/mac-0008-ship-on-fridays.md")
        self.assertRegex(new[1], r"^devices/laptop/notes/\d{4}-\d{2}-\d{2}-lesson-learnt\.md$")
        meta = core.parse_note(self.github.files[new[0]].decode("utf-8"))["meta"]
        self.assertEqual((meta["status"], meta["tags"]), ("accepted", ["laptop"]))
        rules = self.run_script("recall.py", "rules").stdout
        self.assertIn("An older one", rules)
        self.assertIn("Ship on Fridays", rules)

    def test_a_brain_whose_folder_was_lost_carries_on_from_github(self):
        # a normal brain, pushed to GitHub, connected while its folder was there
        subprocess.run(["git", "init", "-q", str(self.brain.root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.brain.root), "remote", "add", "origin", "https://github.com/me/brain.git"], check=True, capture_output=True)
        self.connect("vector")
        config = json.loads((self.vector_skill / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["cloud"], {"repo": "me/brain", "folder": "brain", "branch": ""})
        config["cloud"]["folder"] = ""  # the made-up GitHub keeps the notes at the top of the repo
        (self.vector_skill / "config.json").write_text(json.dumps(config), encoding="utf-8")

        self.brain.create("note", "Only here", [], "local text", "2026-11-21")
        local = self.run_script("recall.py", "search", "only here")
        self.assertIn("notes/only-here.md", local.stdout)
        self.assertNotIn("From GitHub", local.stdout)
        asked = self.run_script("recall.py", "search", "coffee order", "--cloud")
        self.assertIn("From GitHub (me/brain", asked.stdout)
        self.assertIn("notes/coffee-order.md", asked.stdout)

        shutil.rmtree(self.brain.root)  # the local copy goes
        after = self.run_script("recall.py", "search", "coffee order")
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertIn("From GitHub (me/brain", after.stdout)
        self.assertIn("notes/coffee-order.md", after.stdout)
        self.assertEqual(self.run_script("remember.py", "note", "After the loss", "--body", "Still remembering.").returncode, 0)
        self.assertIn("notes/after-the-loss.md", self.github.files)

    def test_without_a_connection_the_last_fetched_notes_still_answer(self):
        self.connect_cloud()
        self.run_script("recall.py", "search", "coffee")
        state = json.loads((self.cache / cloud.STATE).read_text(encoding="utf-8"))
        state["checked"] = 0
        (self.cache / cloud.STATE).write_text(json.dumps(state), encoding="utf-8")
        self.github.down = True
        offline = self.run_script("recall.py", "search", "coffee order")
        self.assertEqual(offline.returncode, 0, offline.stderr)
        self.assertIn("GitHub was not reached", offline.stdout)
        self.assertIn("notes/coffee-order.md", offline.stdout)
        refused = self.run_script("remember.py", "note", "Offline", "--body", "x y z")
        self.assertEqual(refused.returncode, 1)
        self.assertNotIn("notes/offline.md", self.github.files)

    def test_no_sign_in_is_said_plainly(self):
        self.connect_cloud()
        self.env["GITHUB_TOKEN"] = "wrong"
        refused = self.run_script("recall.py", "search", "coffee")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("gh auth login", refused.stderr)

    def test_names_and_front_matter_match_cairns_own(self):
        for title in ("Ship on Fridays", "Déjà vu: again & again", "  ", "a" * 90, "咖啡 order #2"):
            with self.subTest(title=title):
                self.assertEqual(cloud.slugify(title), core.slugify(title))
        text = cloud.note_text('He said: "yes" #1', "decision", ["a", "b"], "body\n", "2026-11-21", "accepted")
        note = core.parse_note(text)
        self.assertEqual(note["meta"], {"title": 'He said: "yes" #1', "type": "decision", "status": "accepted",
                                        "tags": ["a", "b"], "created": "2026-11-21"})
        self.assertEqual(note["body"], "body\n")
        self.assertEqual(cloud.repo_of("https://github.com/me/brain.git"), "me/brain")
        self.assertEqual(cloud.repo_of("git@github.com:me/my.brain"), "me/my.brain")
        self.assertEqual(cloud.repo_of("https://gitlab.com/me/brain.git"), "")

    def test_cloud_takes_only_a_github_repo(self):
        err = io.StringIO()
        with mock.patch.object(Path, "home", return_value=self.home), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, {"CAIRN_HOME": str(self.settings)}):
            code = main(["--root", str(self.tmp), "connect", "vector", "--cloud", "not a repo"])
        self.assertEqual(code, 1)
        self.assertIn("owner/name", err.getvalue())
        self.assertFalse(self.vector_skill.exists())


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

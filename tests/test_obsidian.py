"""Obsidian vault import + two-way sync (cairn/obsidian.py, core.plan_sync, CLI and API)."""

import contextlib
import http.client
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cairn import core
from cairn.brain import Brain
from cairn.cli import main
from cairn.obsidian import ObsidianSync, SyncError
from cairn.server import make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

H = lambda h, m=0: {"hash": h, "mtime": m}  # noqa: E731


class PlanTests(unittest.TestCase):
    def test_first_sync_imports_and_exports(self):
        plan = core.plan_sync({"a.md": H("1"), "same.md": H("s")}, {"b.md": H("2"), "same.md": H("s")}, {})
        self.assertEqual(plan, [{"path": "a.md", "op": "pull"}, {"path": "b.md", "op": "push"}])

    def test_edits_flow_from_the_side_that_changed(self):
        manifest = {"a.md": "1", "b.md": "2"}
        plan = core.plan_sync({"a.md": H("1x"), "b.md": H("2")}, {"a.md": H("1"), "b.md": H("2y")}, manifest)
        self.assertEqual(plan, [{"path": "a.md", "op": "pull"}, {"path": "b.md", "op": "push"}])

    def test_deletes_propagate_but_lose_to_edits(self):
        manifest = {"gone-in-vault.md": "1", "gone-in-brain.md": "2", "edited.md": "3"}
        plan = core.plan_sync(
            {"gone-in-brain.md": H("2"), "edited.md": H("3-new")},
            {"gone-in-vault.md": H("1")},
            manifest,
        )
        self.assertEqual(plan, [
            {"path": "edited.md", "op": "pull"},  # deleted in brain but edited in the vault: the edit wins
            {"path": "gone-in-brain.md", "op": "delete-vault"},
            {"path": "gone-in-vault.md", "op": "delete-brain"},
        ])

    def test_both_changed_is_a_conflict_won_by_the_newer_side(self):
        plan = core.plan_sync({"a.md": H("v", 10)}, {"a.md": H("b", 20)}, {"a.md": "old"})
        self.assertEqual(plan, [{"path": "a.md", "op": "conflict", "keep": "brain"}])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = self.tmp / "My Vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        (self.vault / ".obsidian" / "workspace.md").write_text("never synced", encoding="utf-8")
        (self.vault / "Projects").mkdir()
        (self.vault / "Projects" / "Trail App.md").write_text(
            "---\ntags: [hiking]\naliases: [trails]\n---\n# Trail App\nSee [[Offline Maps|maps]] and [[2026-11-05]].\n",
            encoding="utf-8",
        )
        (self.vault / "Offline Maps.md").write_text("Tiles, storage, [[Projects/Trail App#Goals]].\n", encoding="utf-8")
        (self.vault / "Daily").mkdir()
        (self.vault / "Daily" / "2026-11-05.md").write_text("- walked the ridge\n", encoding="utf-8")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.brain = Brain(self.repo)
        self.brain.init()
        self.sync = ObsidianSync(self.brain)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch_later(self, path: Path, text: str):
        path.write_text(text, encoding="utf-8")
        stamp = time.time() + 5
        os.utime(path, (stamp, stamp))

    def test_import_keeps_files_byte_for_byte_and_links_resolve(self):
        self.sync.link(str(self.vault))
        report = self.sync.sync("2026-11-06")
        self.assertEqual(sorted(report["pulled"]), [
            "obsidian/Daily/2026-11-05.md", "obsidian/Offline Maps.md", "obsidian/Projects/Trail App.md",
        ])
        self.assertFalse((self.brain.dir / "obsidian" / ".obsidian").exists())
        original = (self.vault / "Projects" / "Trail App.md").read_text(encoding="utf-8")
        self.assertEqual((self.brain.dir / "obsidian" / "Projects" / "Trail App.md").read_text(encoding="utf-8"), original)
        notes = self.brain.notes()
        by_path = {n["path"]: n for n in notes}
        self.assertEqual(by_path["obsidian/Daily/2026-11-05.md"]["type"], "journal")
        self.assertEqual(by_path["obsidian/Projects/Trail App.md"]["tags"], ["hiking"])
        edges = {tuple(sorted((e["source"], e["target"]))) for e in core.build_graph(notes)["edges"]}
        self.assertIn(("obsidian/Offline Maps.md", "obsidian/Projects/Trail App.md"), edges)
        self.assertIn(("obsidian/Daily/2026-11-05.md", "obsidian/Projects/Trail App.md"), edges)
        # The state file never ends up in git.
        self.assertEqual((self.brain.dir / ".cairn" / ".gitignore").read_text(encoding="utf-8").splitlines()[-1], "*")
        self.assertEqual(self.sync.sync("2026-11-06")["changes"], 0, "a second sync with no edits does nothing")

    def test_two_way_edits_new_notes_and_deletes(self):
        self.sync.link(str(self.vault))
        self.sync.sync()
        brain_note = self.brain.dir / "obsidian" / "Offline Maps.md"
        brain_note.write_text("Edited in Cairn.\n", encoding="utf-8")
        (self.brain.dir / "obsidian" / "From Cairn.md").write_text("new in cairn\n", encoding="utf-8")
        (self.vault / "Daily" / "2026-11-05.md").write_text("- edited in Obsidian\n", encoding="utf-8")
        (self.vault / "Projects" / "Trail App.md").unlink()
        report = self.sync.sync()
        self.assertEqual((self.vault / "Offline Maps.md").read_text(encoding="utf-8"), "Edited in Cairn.\n")
        self.assertTrue((self.vault / "From Cairn.md").exists())
        self.assertEqual((self.brain.dir / "obsidian" / "Daily" / "2026-11-05.md").read_text(encoding="utf-8"), "- edited in Obsidian\n")
        self.assertFalse((self.brain.dir / "obsidian" / "Projects" / "Trail App.md").exists())
        self.assertTrue((self.brain.dir / ".cairn" / "trash" / "obsidian" / "Projects" / "Trail App.md").exists())
        self.assertEqual(report["deletedInBrain"], ["obsidian/Projects/Trail App.md"])
        # Deleting in Cairn moves the vault copy to Obsidian's own .trash folder.
        (self.brain.dir / "obsidian" / "From Cairn.md").unlink()
        self.sync.sync()
        self.assertFalse((self.vault / "From Cairn.md").exists())
        self.assertTrue((self.vault / ".trash" / "From Cairn.md").exists())

    def test_conflict_keeps_both_versions(self):
        self.sync.link(str(self.vault))
        self.sync.sync()
        (self.vault / "Offline Maps.md").write_text("vault version\n", encoding="utf-8")
        self.touch_later(self.brain.dir / "obsidian" / "Offline Maps.md", "brain version (newer)\n")
        report = self.sync.sync("2026-11-07")
        self.assertEqual(report["conflicts"], [{
            "path": "obsidian/Offline Maps.md", "copy": "obsidian/Offline Maps (conflict 2026-11-07).md", "kept": "brain",
        }])
        for root in (self.vault, self.brain.dir / "obsidian"):
            self.assertEqual((root / "Offline Maps.md").read_text(encoding="utf-8"), "brain version (newer)\n")
            self.assertEqual((root / "Offline Maps (conflict 2026-11-07).md").read_text(encoding="utf-8"), "vault version\n")
        self.assertEqual(self.sync.sync()["changes"], 0)

    def test_whole_brain_mode_keeps_cairn_files_out_of_the_vault(self):
        (self.vault / "README.md").write_text("vault readme\n", encoding="utf-8")
        self.sync.link(str(self.vault), "")
        report = self.sync.sync()
        self.assertIn("Offline Maps.md", report["pulled"])
        self.assertNotIn("README.md", report["pulled"] + report["pushed"])
        self.assertEqual((self.vault / "README.md").read_text(encoding="utf-8"), "vault readme\n")
        self.assertNotEqual((self.brain.dir / "README.md").read_text(encoding="utf-8"), "vault readme\n")
        # A note created in Cairn's own folders reaches the vault.
        (self.brain.dir / "ideas" / "new-idea.md").write_text("---\ntitle: New idea\n---\n", encoding="utf-8")
        self.assertEqual(self.sync.sync()["pushed"], ["ideas/new-idea.md"])
        self.assertTrue((self.vault / "ideas" / "new-idea.md").exists())

    def test_empty_vault_never_wipes_the_brain(self):
        self.sync.link(str(self.vault))
        self.sync.sync()
        for path in list(self.vault.rglob("*.md")):
            if ".obsidian" not in path.parts:
                path.unlink()
        with self.assertRaises(SyncError):
            self.sync.sync()
        self.assertTrue((self.brain.dir / "obsidian" / "Offline Maps.md").exists())

    def test_refuses_nested_folders_and_bad_paths(self):
        with self.assertRaises(SyncError):
            self.sync.link(str(self.tmp / "nope"))
        with self.assertRaises(SyncError):
            self.sync.link(str(self.repo))  # the brain would sit inside the vault
        with self.assertRaises(SyncError):
            self.sync.link(str(self.vault), "../escape")

    def test_cli_imports_then_syncs(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--root", str(self.repo), "obsidian", str(self.vault), "--folder", "vault"])
        self.assertEqual(code, 0)
        self.assertIn("3 change(s)", out.getvalue())
        self.assertTrue((self.brain.dir / "vault" / "Offline Maps.md").exists())
        (self.brain.dir / "vault" / "Offline Maps.md").write_text("from cli\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.repo), "obsidian"]), 0)
        self.assertEqual((self.vault / "Offline Maps.md").read_text(encoding="utf-8"), "from cli\n")

    def test_api_link_sync_unlink(self):
        server = make_server(self.brain, port=0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def call(method, path, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers={"X-Cairn": "1", "Content-Type": "application/json"})
            response = conn.getresponse()
            data = json.loads(response.read())
            conn.close()
            return response.status, data

        try:
            status, data = call("GET", "/api/obsidian")
            self.assertEqual((status, data["linked"]), (200, False))
            status, data = call("POST", "/api/obsidian/link", {"vault": str(self.tmp / "missing")})
            self.assertEqual(status, 400)
            status, data = call("POST", "/api/obsidian/link", {"vault": str(self.vault), "folder": "obsidian"})
            self.assertEqual(status, 200)
            self.assertEqual(len(data["report"]["pulled"]), 3)
            self.assertEqual(data["status"]["synced"], 3)
            status, files = call("GET", "/api/files")
            self.assertIn("obsidian/Offline Maps.md", [f["path"] for f in files["files"]])
            status, data = call("POST", "/api/obsidian/sync", {})
            self.assertEqual(data["report"]["changes"], 0)
            status, data = call("POST", "/api/obsidian/unlink", {})
            self.assertFalse(data["status"]["linked"])
        finally:
            server.shutdown()
            server.server_close()

    @unittest.skipIf(sync_playwright is None, "playwright not installed")
    def test_local_app_links_a_vault_from_the_ui(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from webserver import launch_browser

        server = make_server(self.brain, port=0)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        errors = []
        try:
            with sync_playwright() as p:
                browser = launch_browser(p)
                page = browser.new_page(viewport={"width": 1280, "height": 860})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/#/obsidian")
                page.wait_for_selector("#obsidian-form #ob-vault")
                page.fill("#ob-vault", str(self.vault))
                page.click('#obsidian-form button[type="submit"]')
                page.wait_for_selector(".sync-card.linked")
                self.assertIn("3 notes in sync", page.inner_text(".sync-card.linked"))
                self.assertTrue((self.brain.dir / "obsidian" / "Offline Maps.md").exists())
                # An edit made in Obsidian shows up after "Sync now", without reloading the page.
                (self.vault / "Offline Maps.md").write_text("Edited in Obsidian.\n", encoding="utf-8")
                page.click('[data-action="obsidian-sync"]')
                page.wait_for_function(
                    "() => window.cairn.state.files.find((f) => f.path === 'obsidian/Offline Maps.md')?.text === 'Edited in Obsidian.\\n'"
                )
                page.click('#nav [data-go="map"]')
                page.wait_for_selector("#map-svg")
                ids = page.evaluate("[...document.querySelectorAll('#map-svg .map-node')].map((n) => n.dataset.node)")
                self.assertIn("obsidian/Projects/Trail App.md", ids)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

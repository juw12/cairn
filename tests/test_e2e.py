"""Gates B2 and B5: the real web app in a headless browser (demo mode, GitHub Pages runtime)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

VIEWS = ["home", "ideas", "board", "decisions", "notes", "journal", "map", "code", "context"]


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = start_static_server()
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()

    def open(self, query="?demo=1", viewport=(1440, 900), theme="light"):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('cairn.theme', '{theme}')")
        page.goto(self.url + query)
        page.wait_for_selector("#nav button")
        self.addCleanup(context.close)
        return page

    def note_text(self, page, path):
        return page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p)?.text ?? null", path)

    def go(self, page, view):
        page.click(f'#nav [data-go="{view}"]')
        page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)

    def test_every_view_renders_without_errors(self):
        page = self.open()
        for view in VIEWS:
            self.go(page, view)
            self.assertTrue(page.inner_text("#view").strip(), view)
            if view == "map":
                self.assertEqual(page.locator(".map-node").count(), 18)
        self.assertEqual(self.errors, [])

    def test_capture_routes_to_task_with_tags(self):
        page = self.open()
        page.fill("#capture-input", "todo: ship the e2e test #release")
        self.assertEqual(page.inner_text("#capture-type"), "Task")
        page.press("#capture-input", "Enter")
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'tasks/ship-the-e2e-test.md')")
        text = self.note_text(page, "tasks/ship-the-e2e-test.md")
        self.assertIn("status: todo", text)
        self.assertIn("tags: [release]", text)
        self.assertEqual(self.errors, [])

    def test_log_capture_appends_to_today(self):
        page = self.open()
        page.fill("#capture-input", "log: wrote the tests")
        page.press("#capture-input", "Enter")
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path.startsWith('journal/') && f.text.includes('wrote the tests'))")

    def test_drag_idea_between_columns(self):
        page = self.open()
        self.go(page, "ideas")
        card = '.item-card[data-drag="ideas/share-trip-link.md"]'
        page.drag_and_drop(card, '.column[data-column="exploring"]')
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'ideas/share-trip-link.md').text.includes('status: exploring')")
        self.assertEqual(page.locator(f'.column[data-column="exploring"] {card}').count(), 1)

    def test_edit_persists_and_promote_links_back(self):
        page = self.open()
        self.go(page, "ideas")
        page.click('.item-card[data-drag="ideas/voice-notes-on-trail.md"]')
        page.wait_for_selector("#ed-body")
        page.fill("#ed-body", "Edited body from the e2e test.\n")
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'ideas/voice-notes-on-trail.md').text.includes('Edited body')")
        page.click('[data-action="spark"]')
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'ideas/voice-notes-on-trail.md').text.includes('### Spark:')")
        page.click('[data-action="promote"]')
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'tasks/voice-notes-pinned-to-the-map.md')")
        task = self.note_text(page, "tasks/voice-notes-pinned-to-the-map.md")
        idea = self.note_text(page, "ideas/voice-notes-on-trail.md")
        self.assertIn("[[voice-notes-on-trail]]", task)
        self.assertIn("[[voice-notes-pinned-to-the-map]]", idea)
        self.assertIn("status: ready", idea)
        page.reload()
        page.wait_for_selector("#nav button")
        self.assertIn("Edited body", self.note_text(page, "ideas/voice-notes-on-trail.md"))
        self.assertEqual(self.errors, [])

    def test_palette_finds_body_text(self):
        page = self.open()
        page.keyboard.press("Control+k")
        page.fill(".palette input", "switchbacks")  # only in code; should find nothing in notes
        page.fill(".palette input", "Strava")
        self.assertIn("GPX import", page.inner_text(".palette-list"))
        page.keyboard.press("Enter")
        page.wait_for_selector(".drawer")

    def test_code_inbox_makes_tracked_task(self):
        page = self.open()
        self.go(page, "code")
        page.click('[data-action="todo-task"][data-source="src/maps/download.ts:88"]')
        page.wait_for_function("() => window.cairn.state.notes.some((n) => n.meta.source === 'src/maps/download.ts:88')")
        self.assertGreaterEqual(page.locator(".tracked").count(), 2)

    def test_first_run_guide_on_empty_brain(self):
        page = self.open("?empty=1")
        page.evaluate("localStorage.removeItem('cairn.guide')")
        page.reload()
        page.wait_for_selector("#guide")
        self.assertEqual(page.locator(".guide-step.done").count(), 0)
        page.fill("#capture-input", "my first idea")
        page.press("#capture-input", "Enter")
        page.wait_for_selector(".guide-step.done")
        self.assertEqual(page.locator(".guide-step.done").count(), 1)

    def test_mobile_and_dark_have_no_horizontal_scroll(self):
        for theme in ("light", "dark"):
            page = self.open(viewport=(390, 844), theme=theme)
            for view in VIEWS:
                page.evaluate("(v) => { location.hash = '#/' + v; dispatchEvent(new PopStateEvent('popstate')); }", view)
                page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)
                overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
                self.assertLessEqual(overflow, 0, f"{theme}/{view} scrolls sideways by {overflow}px")
            self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), theme)
            self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

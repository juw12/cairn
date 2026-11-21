"""A newer build: the page finds it by itself, says so, and Version history names the build the page really runs."""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import install_github_mock, launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

VERSIONS = json.loads((Path(__file__).resolve().parent.parent / "docs" / "versions.json").read_text(encoding="utf-8"))
NOW = VERSIONS["releases"][0]["build"]
NEXT = NOW + 1


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class NewerBuildTests(unittest.TestCase):
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

    def open(self, viewport=(1280, 900)):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        install_github_mock(context)
        # What the site serves: today's history, or (after publish()) one more build on top of it.
        self.site = {"newer": False, "down": False, "asked": 0}

        def versions(route):
            self.site["asked"] += 1
            if self.site["down"]:
                return route.abort()
            served = json.loads(json.dumps(VERSIONS))
            if self.site["newer"]:
                served["releases"].insert(0, {"build": NEXT, "date": "2099-01-01", "title": "The next one", "changes": ["Something new"]})
            route.fulfill(status=200, content_type="application/json", body=json.dumps(served))

        context.route("**/versions.json", versions)
        context.set_default_timeout(60_000)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script("localStorage.setItem('cairn.theme', 'light'); localStorage.setItem('cairn.tour', 'done');")
        page.goto(self.url + "?demo=1#/board", wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector("#nav button")
        page.wait_for_function("() => window.cairn.state.build.page > 0")
        self.addCleanup(context.close)
        return page

    def come_back(self, page):
        """What a phone does when Cairn comes back to the front."""
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

    def test_the_page_knows_its_own_build_and_offers_nothing_when_it_is_the_newest(self):
        page = self.open()
        self.assertEqual(page.evaluate("window.cairn.state.build"), {"page": NOW, "newest": 0})
        self.assertEqual(page.evaluate("window.cairn.lookForUpdate()"), "current")
        self.assertEqual(page.locator(".foot-update").count(), 0)
        self.assertEqual(page.locator(".toast").count(), 0)
        # With no network nothing newer can be known, and nothing is offered.
        self.site["down"] = True
        self.assertEqual(page.evaluate("window.cairn.lookForUpdate()"), "unknown")
        self.assertEqual(page.locator(".foot-update").count(), 0)
        self.assertEqual(self.errors, [])

    def test_a_newer_build_is_said_and_one_press_takes_it(self):
        page = self.open()
        page.evaluate("window.CAIRN_UPDATE_IDLE_MS = 600000")  # you are busy: nothing reloads by itself
        self.site["newer"] = True
        self.come_back(page)
        page.wait_for_selector(".toast")
        self.assertEqual(page.inner_text(".toast span"), f"Cairn build {NEXT} is ready.")
        self.assertEqual(page.inner_text(".toast button"), "Update now")
        # The sidebar keeps offering it after the message has gone.
        self.assertEqual(page.inner_text(".foot-update").strip(), f"Update to build {NEXT}")
        # Looking again does not say it twice.
        self.come_back(page)
        page.wait_for_timeout(400)
        self.assertEqual(page.locator(".toast").count(), 1)
        self.assertEqual(page.evaluate("location.hash"), "#/board", "busy: still on the old page")

        # Version history names the build this page runs, not the newest one in the list, and offers the newer one.
        page.evaluate("document.querySelector('[data-action=\"versions\"]').click()")
        page.wait_for_selector(".modal.versions .ver")
        self.assertIn(f"You are on build {NOW} ", page.inner_text(".modal.versions"))
        self.assertIn(f"Build {NEXT} is out", page.inner_text(".modal.versions .ver-newer"))
        rows = page.eval_on_selector_all(".modal.versions .ver", "nodes => nodes.slice(0, 2).map((n) => [n.querySelector('.ver-build').textContent, n.open, !!n.querySelector('.pill')])")
        self.assertEqual(rows, [[f"Build {NEXT}", False, False], [f"Build {NOW}", True, True]])

        # One press reloads into the new build, on the same page, and the offer is gone.
        with page.expect_navigation():
            page.click(".modal.versions [data-action='update-now']")
        page.wait_for_selector("#nav button")
        page.wait_for_function("(next) => window.cairn.state.build.page === next", arg=NEXT)
        self.assertEqual(page.evaluate("location.hash"), "#/board")
        self.assertEqual(page.locator(".foot-update").count(), 0)
        page.wait_for_function("() => [...document.querySelectorAll('.toast')].some((t) => /updated to the latest version/.test(t.textContent))")
        self.assertEqual(self.errors, [])

    def test_coming_back_after_a_while_takes_the_newer_build_without_a_press(self):
        page = self.open(viewport=(390, 844))
        # Nothing was touched for longer than the pause the update waits for: the phone was in a pocket.
        page.evaluate("window.CAIRN_UPDATE_IDLE_MS = 0")
        self.site["newer"] = True
        with page.expect_navigation():
            self.come_back(page)
        page.wait_for_selector("#nav button, [data-action=toggle-nav]")
        page.wait_for_function("(next) => window.cairn.state.build.page === next", arg=NEXT)
        # One reload, not a loop: the page now runs the newest build and looks no further.
        asked = self.site["asked"]
        page.wait_for_timeout(1200)
        self.assertEqual(page.evaluate("window.cairn.state.build"), {"page": NEXT, "newest": 0})
        self.assertEqual(self.site["asked"], asked)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

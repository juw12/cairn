"""Gates B2 and B5: the real web app in a headless browser (demo mode, GitHub Pages runtime)."""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import go_to, install_github_mock, launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

VIEWS = ["home", "ideas", "board", "decisions", "notes", "journal", "map", "canvas", "code", "vibe", "autopilot", "context", "plugins", "people", "proof"]

# The sidebar has one entry per hub; a hub with several pages shows them as tabs (docs/state.js HUBS).
HUBS = {
    "today": ["home"],
    "plan": ["board", "ideas", "decisions"],
    "notes": ["notes", "journal"],
    "map": ["map", "canvas"],
    "build": ["vibe", "projects", "autopilot", "code"],
    "founder": ["founder"],
    "integrations": ["context", "obsidian", "plugins"],
    "people": ["people", "proof"],
}


# The demo brain has 27 notes: the original 18 plus 9 dated journal pages (docs/store.js DEMO_DAYS),
# whatever today's date is (a dated page never overwrites the fixed sample journal page).
DEMO_NOTES = 27


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

    def open(self, query="?demo=1", viewport=(1440, 900), theme="light", wait="#nav button", setup=None):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        self.github_log = install_github_mock(context)
        if setup:
            setup(self.github_log)
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('cairn.theme', '{theme}')")
        page.goto(self.url + query)
        page.wait_for_selector(wait)
        self.addCleanup(context.close)
        return page

    def note_text(self, page, path):
        return page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p)?.text ?? null", path)

    def go(self, page, view):
        go_to(page, view)
        page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)

    def sample_week(self, page):
        page.click('[data-action="vibe-sample"]')
        page.wait_for_function("() => window.cairn.state.notes.filter((n) => n.type === 'session').length === 4")
        page.wait_for_selector(".vibe-overview")

    def start_session(self, page, title):
        page.click('[data-action="vibe-start"]')
        page.fill(".modal input", title)
        page.click('.modal button[type="submit"]')
        page.wait_for_selector(".ov-lane")
        return page.evaluate("(t) => window.cairn.state.notes.find((n) => n.type === 'session' && n.title === t).path", title)

    def overview_tiles(self, page):
        return page.eval_on_selector_all(
            "button.ov-tile",
            """nodes => nodes.map((n) => ({
                key: n.dataset.outcome,
                count: Number(n.querySelector('.ov-count').dataset.count),
                share: parseInt(n.querySelector('.ov-share').textContent, 10),
                pressed: n.getAttribute('aria-pressed') === 'true',
                off: n.disabled,
            }))""",
        )

    def test_notes_list_pages_big_vaults(self):
        page = self.open()
        page.evaluate("""() => {
            const s = window.cairn.state;
            const extra = Array.from({ length: 650 }, (_, i) => ({
                ...s.notes[0], path: `notes/bulk-${i}.md`, slug: `bulk-${i}`, title: `Bulk ${i}`, tags: [], mtime: i,
            }));
            s.notes = s.notes.concat(extra);
        }""")
        self.go(page, "notes")
        total = page.evaluate("() => window.cairn.state.notes.length")
        self.assertEqual(page.locator("#notes-list .row, #notes-list > *").count(), 300)
        self.assertIn(f"300 of {total}", page.inner_text(".list-more"))
        page.click('[data-action="notes-more"]')
        self.assertEqual(page.locator("#notes-list > *").count(), 600)
        page.click('[data-action="notes-more"]')
        self.assertEqual(page.locator("#notes-list > *").count(), total)
        self.assertEqual(page.locator(".list-more").count(), 0)
        # A search starts again from one page.
        page.fill("#notes-search", "bulk")
        page.wait_for_function("() => document.querySelectorAll('#notes-list > *').length === 300")
        self.assertEqual(self.errors, [])

    def test_context_view_offers_mcp_setup_for_each_assistant(self):
        page = self.open()
        self.go(page, "context")
        # Outside `cairn serve` the page can't know local paths, so it points at the CLI instead.
        self.assertIn("cairn mcp --print-config", page.inner_text(".mcp-card pre"))
        self.assertEqual(page.locator("[data-mcp-client]").count(), 0)
        # In server mode /api/info carries the launch entry; every client gets a ready-to-paste snippet.
        entry = {
            "name": "cairn-demo",
            "command": "C:\\Program Files\\Python313\\python.exe",
            "args": ["-m", "cairn", "--root", "C:\\work\\my app", "mcp"],
            "env": {"PYTHONPATH": "C:\\tools\\cairn"},
        }
        page.evaluate("(e) => { Object.assign(window.cairn.state.info, { mode: 'server', mcp: e }); window.cairn.rerender(); }", entry)
        snippet = json.loads(page.inner_text(".mcp-card pre"))
        self.assertEqual(snippet["mcpServers"]["cairn-demo"]["args"], entry["args"])
        page.click('[data-mcp-client="vscode"]')
        snippet = json.loads(page.inner_text(".mcp-card pre"))
        self.assertEqual(snippet["servers"]["cairn-demo"]["type"], "stdio")
        self.assertIn(".vscode/mcp.json", page.inner_text(".mcp-card"))
        page.click('[data-mcp-client="opencode"]')
        snippet = json.loads(page.inner_text(".mcp-card pre"))
        self.assertEqual(snippet["mcp"]["cairn-demo"]["command"][0], entry["command"])
        self.assertEqual(snippet["mcp"]["cairn-demo"]["environment"], entry["env"])
        page.click('[data-mcp-client="claude-code"]')
        line = page.inner_text(".mcp-card pre")
        self.assertTrue(line.startswith("claude mcp add cairn-demo --env PYTHONPATH="), line)
        self.assertIn(' -- "C:\\Program Files\\Python313\\python.exe" -m cairn --root "C:\\work\\my app" mcp', line)
        self.assertEqual(page.get_attribute('[data-mcp-client="claude-code"]', "aria-pressed"), "true")
        self.assertEqual(self.errors, [])

    def test_demo_brain_is_the_same_whatever_the_date(self):
        import datetime

        # 2026-11-05 used to clash with the fixture's fixed 2026-11-04 page and drop a note.
        for day in ("2026-11-05", "2026-11-08", "2027-01-01"):
            context = self.browser.new_context()
            install_github_mock(context)
            page = context.new_page()
            page.clock.install(time=datetime.datetime.fromisoformat(day + "T10:00:00"))
            page.goto(self.url + "?demo=1")
            page.wait_for_selector("#nav button")
            notes = page.evaluate("() => window.cairn.state.notes.length")
            journal = page.evaluate("() => window.cairn.state.files.filter((f) => f.path.startsWith('journal/')).length")
            context.close()
            self.assertEqual((day, notes, journal), (day, DEMO_NOTES, 10))

    def test_every_view_renders_without_errors(self):
        page = self.open()
        for view in VIEWS:
            self.go(page, view)
            self.assertTrue(page.inner_text("#view").strip(), view)
            if view == "map":
                self.assertEqual(page.locator(".map-node").count(), DEMO_NOTES)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ sidebar hubs and the tabs inside them

    def nav_state(self, page):
        return page.evaluate("""() => ({
            view: window.cairn.state.view,
            hubs: [...document.querySelectorAll('#nav button[aria-current="page"]')].map((b) => b.dataset.hub),
            tabs: [...document.querySelectorAll('.hub-tabs .hub-tab')].map((b) => b.dataset.go),
            tab: document.querySelector('.hub-tabs [aria-current="page"]')?.dataset.go ?? null,
        })""")

    def test_sidebar_has_eight_entries_and_no_group_labels(self):
        page = self.open()
        entries = page.eval_on_selector_all("#nav button", "els => els.map((e) => e.querySelector('span').textContent)")
        self.assertEqual(entries, ["Today", "Plan", "Notes", "Map", "Build", "Founder Studio", "Integrations", "People"])
        self.assertEqual(page.locator("#nav .nav-label").count(), 0)
        self.assertEqual(page.eval_on_selector_all("#nav button", "els => els.map((e) => e.dataset.hub)"), list(HUBS))
        self.assertEqual(self.errors, [])

    def test_every_old_address_opens_its_page_inside_a_hub(self):
        page = self.open()
        for hub, views in HUBS.items():
            tabs = views if len(views) > 1 else []
            for view in views:
                page.goto(f"{self.url}?demo=1#/{view}")
                page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)
                expected = {"view": view, "hubs": [hub], "tabs": tabs, "tab": view if tabs else None}
                self.assertEqual(self.nav_state(page), expected)
                self.assertTrue(page.inner_text("#view").strip(), view)
        self.assertEqual(sum(len(views) for views in HUBS.values()), 18)
        self.assertEqual(self.errors, [])

    def test_a_sidebar_entry_reopens_the_tab_used_last(self):
        page = self.open()
        self.assertEqual(page.get_attribute('#nav [data-hub="plan"]', "data-go"), "board")
        self.go(page, "ideas")
        self.go(page, "home")
        page.click('#nav [data-hub="plan"]')
        page.wait_for_function("() => window.cairn.state.view === 'ideas'")
        self.assertEqual(self.nav_state(page)["tab"], "ideas")
        # It is remembered in this browser, so it is still the choice after a reload.
        self.go(page, "home")
        page.reload()
        page.wait_for_selector("#nav button")
        self.assertEqual(page.evaluate("() => window.cairn.state.view"), "home")
        self.assertEqual(page.get_attribute('#nav [data-hub="plan"]', "data-go"), "ideas")
        self.assertEqual(self.errors, [])

    def test_sidebar_entries_and_tabs_are_buttons_that_keep_the_keyboard_focus(self):
        page = self.open()
        self.go(page, "board")
        page.focus('.hub-tabs [data-go="ideas"]')
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.cairn.state.view === 'ideas'")
        focused = page.evaluate("() => [document.activeElement.className, document.activeElement.dataset.go]")
        self.assertEqual(focused, ["hub-tab active", "ideas"])
        page.focus('#nav [data-hub="notes"]')
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.cairn.state.view === 'notes'")
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.hub"), "notes")
        reachable = page.eval_on_selector_all("#nav button, .hub-tabs .hub-tab", "els => els.map((e) => e.tagName === 'BUTTON' && e.tabIndex === 0)")
        self.assertEqual(reachable, [True] * 10)
        self.assertEqual(self.errors, [])

    def test_tabs_fit_a_phone_and_the_drawer_closes_after_a_choice(self):
        page = self.open(viewport=(390, 844))
        for view in ("obsidian", "decisions", "code"):
            page.goto(f"{self.url}?demo=1#/{view}")
            page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - innerWidth"), 0, view)
            strip = page.locator(".hub-tabs").bounding_box()
            self.assertGreaterEqual(strip["x"], 0, view)
            self.assertLessEqual(strip["x"] + strip["width"], 390, view)
            self.assertTrue(page.is_visible(f'.hub-tabs [data-go="{view}"]'), view)
        page.click("#menu-btn")
        page.click('#nav [data-hub="notes"]')
        page.wait_for_function("() => window.cairn.state.view === 'notes'")
        self.assertFalse(page.evaluate("() => document.querySelector('#app').classList.contains('nav-open')"))
        self.assertEqual(self.errors, [])

    def test_palette_reaches_every_page_and_lands_inside_its_hub(self):
        page = self.open()
        labels = page.evaluate("() => import('./state.js').then((m) => m.VIEWS.map((v) => [v.id, v.label]))")
        self.assertEqual(sorted(view for view, _ in labels), sorted(view for views in HUBS.values() for view in views))
        page.keyboard.press("Control+k")
        page.wait_for_selector(".palette input")
        for view, label in labels:
            # The palette lists six commands at a time, so ask for each page by name.
            page.fill(".palette input", f"Go to {label}")
            shown = page.eval_on_selector_all(".palette-item .label", "els => els.map((e) => e.textContent.trim())")
            self.assertIn(f"Go to {label}", shown, view)
        page.fill(".palette input", "Go to Proof")
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.cairn.state.view === 'proof'")
        self.assertEqual(self.nav_state(page), {"view": "proof", "hubs": ["people"], "tabs": ["people", "proof"], "tab": "proof"})
        self.assertEqual(self.errors, [])

    def test_vibe_session_feedback_and_stats(self):
        page = self.open()
        self.go(page, "vibe")
        self.assertIn("Nothing live", page.inner_text(".vibe-live"))
        page.click('[data-action="vibe-start"]')
        page.fill(".modal input", "Add dark mode")
        page.click('.modal button[type="submit"]')
        page.wait_for_selector(".vibe-live h2")
        self.assertEqual(page.inner_text('#nav [data-views~="vibe"] .count'), "live")
        path = page.evaluate("window.cairn.state.notes.find((n) => n.type === 'session').path")
        self.assertRegex(path, r"^sessions/\d{4}-\d{2}-\d{2}-add-dark-mode\.md$")

        page.select_option(".vibe-log-form select", "pass")
        page.fill(".vibe-log-form input", "pytest: 12 passed")
        page.press(".vibe-log-form input", "Enter")
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('pass: pytest: 12 passed')", arg=path)
        self.assertIn("pytest: 12 passed", page.inner_text(".vibe-log"))

        page.fill("#fb-text", "The toggle is hard to find")
        page.select_option("#fb-form select[name=rating]", "3")
        page.fill("#fb-form input[name=from]", "@alice")
        page.click('#fb-form button[type="submit"]')
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'feedback/the-toggle-is-hard-to-find.md')")
        feedback = self.note_text(page, "feedback/the-toggle-is-hard-to-find.md")
        self.assertIn("rating: 3", feedback)
        self.assertIn('from: "@alice"', feedback)
        self.assertIn("about: \"[[", feedback)

        page.click('[data-action="vibe-end"][data-result="shipped"]')
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('status: shipped')", arg=path)
        text = self.note_text(page, path)
        self.assertIn("ended: ", text)
        self.assertIn("end: shipped", text)
        figures = page.inner_text(".vibe-figures")
        self.assertIn("100%", figures)  # ship rate and checks green
        self.assertIn("3.0", figures)  # average rating
        self.assertEqual(page.locator('#nav [data-views~="vibe"] .count').count(), 0)

        page.select_option('[data-fb-status="feedback/the-toggle-is-hard-to-find.md"]', "addressed")
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'feedback/the-toggle-is-hard-to-find.md').text.includes('status: addressed')")
        self.assertEqual(self.errors, [])

    def test_vibe_skills_and_parallel_sessions(self):
        page = self.open()
        self.go(page, "vibe")
        for title in ("Map tiles", "Share link"):
            page.click('[data-action="vibe-start"]')
            page.fill(".modal input", title)
            page.click('.modal button[type="submit"]')
            page.wait_for_function("(t) => window.cairn.state.notes.some((n) => n.type === 'session' && n.title === t)", arg=title)
        page.wait_for_function("() => document.querySelectorAll('.vibe-live').length === 2")
        self.assertEqual(page.inner_text('#nav [data-views~="vibe"] .count'), "2 live")
        self.assertIn("Share link", page.inner_text(".vibe-live >> nth=0"))  # newest first

        # A note that starts with a slash command is logged as a skill run, on the session whose form was used.
        older = page.evaluate("window.cairn.state.notes.find((n) => n.title === 'Map tiles').path")
        form = f'.vibe-log-form[data-session="{older}"]'
        page.fill(f"{form} input", "/refine app trailmix · 6.5 -> 8.7")
        page.press(f"{form} input", "Enter")
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('skill: /refine app trailmix')", arg=older)
        page.fill(f"{form} input", "/council tooltips")
        page.press(f"{form} input", "Enter")
        page.wait_for_selector(".skill-chip")
        chips = page.inner_text(".vibe-skills")
        self.assertIn("/refine", chips)
        self.assertIn("/council", chips)

        page.click(f'[data-action="vibe-end"][data-result="partial"][data-path="{older}"]')
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('status: partial')", arg=older)
        self.assertEqual(page.inner_text('#nav [data-views~="vibe"] .count'), "live")
        self.assertEqual(page.locator(".vibe-live").count(), 1)
        self.assertEqual(self.errors, [])

    def test_demo_can_load_a_sample_week(self):
        page = self.open()
        self.go(page, "vibe")
        page.click('[data-action="vibe-sample"]')
        page.wait_for_function("() => window.cairn.state.notes.filter((n) => n.type === 'session').length === 4")
        self.assertEqual(page.evaluate("window.cairn.state.notes.filter((n) => n.type === 'feedback').length"), 3)
        self.assertIn("4", page.inner_text(".vibe-figures"))
        self.assertEqual(page.locator(".skill-chip").count(), 4)
        self.assertEqual(page.locator('[data-action="vibe-sample"]').count(), 0)
        self.assertEqual(self.errors, [])

    def test_vibe_outcome_chips_and_folding_days(self):
        page = self.open()
        self.go(page, "vibe")
        figures = page.inner_text(".vibe-figures")
        self.assertIn("no checks logged yet", figures)  # a dash always says why
        self.assertIn("no ratings yet", figures)
        self.assertIn("no feedback yet", figures)
        tile = page.locator('.vibe-figures [data-action="vibe-why"]').first
        tile.click()  # a tap shows the same explanation the hover tooltip gives
        page.wait_for_selector(".vibe-figures .figure.open .why")
        self.assertEqual(page.locator(".vibe-figures .figure.open .why").inner_text(), tile.get_attribute("title"))
        page.keyboard.press("Enter")  # and the keyboard closes it again
        page.wait_for_function("() => !document.querySelector('.vibe-figures .why')")
        page.click('[data-action="vibe-sample"]')
        page.wait_for_function("() => window.cairn.state.notes.filter((n) => n.type === 'session').length === 4")
        rows = page.locator(".session-row").count()
        chip = page.locator('.vibe-outcomes [data-action="vibe-outcome"]').first
        outcome = chip.get_attribute("data-outcome")
        count = int(chip.locator("b").inner_text())
        self.assertLess(count, rows, "the sample week mixes outcomes")

        chip.click()
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=count)
        self.assertEqual(page.locator(f".session-row .dot.s-{outcome}").count(), count)
        self.assertEqual(page.locator('.vibe-outcomes [aria-pressed="true"]').count(), 1)
        self.assertIn("Show every outcome", page.inner_text(".vibe-sessions"))
        page.click(f'.vibe-outcomes [data-outcome="{outcome}"]')  # pressing it again shows everything
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)

        head = page.locator(".day-head").first
        in_day = page.locator(".day-group").first.locator(".session-row").count()
        self.assertEqual(head.get_attribute("aria-expanded"), "true")
        head.click()
        page.wait_for_function("() => document.querySelector('.day-head').getAttribute('aria-expanded') === 'false'")
        self.assertEqual(page.locator(".session-row").count(), rows - in_day)
        self.assertEqual(page.evaluate("document.activeElement.classList.contains('day-head')"), True)
        page.keyboard.press("Enter")  # a folded day opens again from the keyboard
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)

        # Fold every day at once, then unfold them all again.
        page.click('[data-action="vibe-fold-all"]')
        page.wait_for_function("() => document.querySelectorAll('.session-row').length === 0")
        self.assertEqual(page.locator('.day-head[aria-expanded="true"]').count(), 0)
        self.assertIn("Unfold all days", page.inner_text(".vibe-sessions"))
        page.click('[data-action="vibe-fold-all"]')
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)

        # A bar in the chart opens just that day, even when every day is folded.
        page.click('[data-action="vibe-fold-all"]')
        page.wait_for_function("() => document.querySelectorAll('.session-row').length === 0")
        bar = page.locator('.vibe-activity [data-action="vibe-jump"]').last
        day = bar.get_attribute("data-day")
        bar.click()
        page.wait_for_function("(d) => document.querySelector(`.day-head[data-day='${d}']`)?.getAttribute('aria-expanded') === 'true'", arg=day)
        self.assertEqual(page.locator('.day-head[aria-expanded="true"]').count(), 1)
        self.assertGreater(page.locator(".session-row").count(), 0)
        self.assertEqual(page.evaluate("document.activeElement.dataset.day"), day)
        # The jumped-to day offers a way back up, which lands on the bar it came from.
        page.click(f'.day-back[data-day="{day}"]')
        page.wait_for_function("() => !document.querySelector('.day-back')")
        self.assertEqual(page.evaluate("document.activeElement.dataset.action"), "vibe-jump")
        self.assertEqual(page.evaluate("document.activeElement.dataset.day"), day)
        page.click('[data-action="vibe-fold-all"]')  # back to every day open for the checks below
        page.click('[data-action="vibe-fold-all"]')
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)

        # With history, the idle card is one line that points at the last session.
        idle = page.inner_text(".vibe-live.idle")
        self.assertIn("Last session:", idle)
        self.assertEqual(page.locator(".vibe-live.idle [data-open]").count(), 1)

        # The chart keeps "no git" apart from "no code change", like the chips do.
        has_no_git = page.locator('.vibe-outcomes [data-outcome="unknown"]').count() > 0
        legend = page.inner_text(".vibe-activity header")
        self.assertIn("no code change", legend)
        self.assertNotIn("no code change or no git", legend)
        self.assertEqual("no git" in legend, has_no_git)
        self.assertEqual(page.locator(".vibe-activity .bar.nogit").count() > 0, has_no_git)
        # Each bar's tooltip names the day and splits it by outcome, like the chips.
        tips = page.eval_on_selector_all(".vibe-activity svg g title", "nodes => nodes.map((n) => n.textContent)")
        busy = [tip for tip in tips if " 0 sessions" not in tip and "session" in tip and "·" in tip]
        self.assertTrue(busy, tips)
        self.assertTrue(any(word in busy[0] for word in ("shipped", "no code change", "no git", "live", "partial", "dropped")), busy[0])
        self.assertEqual(self.errors, [])

    def test_vibe_overview_counts_every_status_and_filters_the_list(self):
        page = self.open()
        self.go(page, "vibe")
        self.assertEqual(page.locator(".vibe-overview").count(), 0)  # nothing to count yet: the empty card stands in
        self.sample_week(page)
        tiles = self.overview_tiles(page)
        self.assertEqual([t["key"] for t in tiles], ["live", "shipped", "partial", "dropped", "done", "unknown"])
        self.assertEqual(sum(t["count"] for t in tiles), 4)  # every session sits under exactly one status
        self.assertEqual(sum(t["share"] for t in tiles), 100)
        for tile in tiles:
            self.assertEqual(tile["off"], tile["count"] == 0, tile)  # an empty status shows 0 and cannot be pressed
            self.assertFalse(tile["pressed"], tile)
        self.assertEqual(page.locator(".ov-bar .ov-seg").count(), 6)
        self.assertIn("4 sessions", page.inner_text(".vibe-overview > header"))
        # The numbers count up as the page opens and land on the real counts.
        page.wait_for_function("() => [...document.querySelectorAll('.ov-count')].every((n) => n.textContent === n.dataset.count)")
        count_of = {t["key"]: t["count"] for t in tiles}
        chips = page.eval_on_selector_all('.vibe-outcomes [data-action="vibe-outcome"]', "nodes => nodes.map((n) => [n.dataset.outcome, Number(n.querySelector('b').textContent)])")
        self.assertTrue(chips)
        for outcome, count in chips:
            self.assertEqual(count_of[outcome], count, outcome)  # the tiles and the legend chips agree
        rows = page.locator(".session-row").count()
        shipped = count_of["shipped"]
        self.assertTrue(0 < shipped < rows, count_of)

        tile = '.ov-tile[data-outcome="shipped"]'
        page.click(tile)
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=shipped)
        self.assertEqual(page.locator(".session-row .dot.s-shipped").count(), shipped)
        self.assertEqual(page.get_attribute(tile, "aria-pressed"), "true")
        self.assertEqual(page.locator('.ov-tile[aria-pressed="true"]').count(), 1)
        pressed_chips = page.eval_on_selector_all('.vibe-outcomes [aria-pressed="true"]', "nodes => nodes.map((n) => n.dataset.outcome)")
        self.assertEqual(pressed_chips, ["shipped"])  # the same filter the legend chip sets
        self.assertEqual(page.locator(".ov-peek li").count(), shipped)
        page.wait_for_function("(sel) => document.activeElement.matches(sel)", arg=tile)  # focus stays on the tile
        page.keyboard.press("Enter")  # pressing it again clears the filter
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)
        self.assertEqual(page.locator('.ov-tile[aria-pressed="true"], .vibe-outcomes [aria-pressed="true"]').count(), 0)
        self.assertEqual(page.locator(".ov-peek").count(), 0)

        # A legend chip presses the matching tile, and "Clear filter" lets go of it.
        page.click('.vibe-outcomes [data-outcome="partial"]')
        page.wait_for_selector('.ov-tile[data-outcome="partial"][aria-pressed="true"]')
        self.assertEqual(page.locator(".session-row").count(), count_of["partial"])
        page.click(".ov-clear")
        page.wait_for_function("(n) => document.querySelectorAll('.session-row').length === n", arg=rows)
        self.assertEqual(page.locator('.ov-tile[aria-pressed="true"]').count(), 0)
        self.assertEqual(self.errors, [])

    def test_vibe_overview_follows_a_live_session(self):
        page = self.open()
        self.go(page, "vibe")
        self.sample_week(page)
        self.assertEqual(page.locator(".ov-lane").count(), 0)  # lanes are only for sessions that are running
        self.assertTrue(page.is_disabled('.ov-tile[data-outcome="live"]'))
        shipped = {t["key"]: t["count"] for t in self.overview_tiles(page)}["shipped"]
        page.wait_for_timeout(800)  # let the opening draw-in finish, so what follows is a change to a settled page

        path = self.start_session(page, "Wire the overview")
        lane = f'.ov-lane[data-path="{path}"]'
        page.wait_for_selector(lane)
        self.assertEqual(page.locator(".ov-lane").count(), 1)
        self.assertEqual(page.inner_text(f"{lane} .ov-title"), "Wire the overview")
        self.assertIn("1 live now", page.inner_text(".vibe-overview > header"))
        tiles = self.overview_tiles(page)
        self.assertEqual({t["key"]: t["count"] for t in tiles}["live"], 1)
        self.assertEqual(sum(t["count"] for t in tiles), 5)
        self.assertEqual(sum(t["share"] for t in tiles), 100)
        # Only what changed moves: the new lane comes in and the live tile bumps.
        self.assertEqual(page.locator(f"{lane}.ov-new").count(), 1)
        self.assertEqual(page.eval_on_selector_all(".ov-tile.ov-bump", "nodes => nodes.map((n) => n.dataset.outcome)"), ["live"])

        # The clock ticks in place, without redrawing the page.
        clock = f"{lane} .ov-clock"
        shown = page.inner_text(clock)
        self.assertRegex(shown, r"^\d+:\d{2}(:\d{2})?$")
        page.evaluate("document.querySelector('.vibe-overview').dataset.kept = 'yes'")
        page.wait_for_function("([sel, was]) => document.querySelector(sel).textContent !== was", arg=[clock, shown])
        self.assertEqual(page.get_attribute(".vibe-overview", "data-kept"), "yes")

        # A new step shows in the lane as soon as it is logged, with no reload.
        steps = int(page.get_attribute(lane, "data-steps"))
        page.evaluate("window.__sameLoad = true")
        page.select_option(".vibe-log-form select", "pass")
        page.fill(".vibe-log-form input", "overview probe green")
        page.press(".vibe-log-form input", "Enter")
        page.wait_for_function("(sel) => document.querySelector(sel)?.textContent === 'overview probe green'", arg=f"{lane} .ov-step .txt")
        self.assertEqual(int(page.get_attribute(lane, "data-steps")), steps + 1)
        self.assertEqual(page.locator(f"{lane} .ov-step.k-pass").count(), 1)
        self.assertEqual(page.locator(f"{lane} .ov-track i").count(), steps + 1)
        self.assertEqual(page.inner_text(f"{lane} .ov-checks .check-pass").strip(), "1")
        self.assertEqual(page.locator(f"{lane}.ov-stepped").count(), 1)
        self.assertTrue(page.evaluate("window.__sameLoad"))

        # The step line jumps down to that session's own card.
        page.click(f"{lane} .ov-lane-log")
        page.wait_for_function("() => Boolean(document.activeElement.closest('.vibe-live'))")

        # Ending it moves it from live to shipped.
        page.click(f'[data-action="vibe-end"][data-result="shipped"][data-path="{path}"]')
        page.wait_for_function("() => document.querySelectorAll('.ov-lane').length === 0")
        tiles = self.overview_tiles(page)
        after = {t["key"]: t for t in tiles}
        self.assertEqual(after["live"]["count"], 0)
        self.assertTrue(after["live"]["off"])
        self.assertEqual(after["shipped"]["count"], shipped + 1)
        self.assertEqual(sum(t["count"] for t in tiles), 5)
        self.assertNotIn("live now", page.inner_text(".vibe-overview > header"))
        self.assertEqual(self.errors, [])

    def test_vibe_overview_draw_in_carries_on_through_a_redraw(self):
        page = self.open()
        self.go(page, "vibe")
        # Timed inside the page, so a slow machine cannot stretch the gaps between the steps.
        seen = page.evaluate(
            """async () => {
                const drawn = new Promise((resolve) => {
                    const watch = new MutationObserver(() => {
                        if (!document.querySelector('.vibe-overview')) return;
                        watch.disconnect();
                        resolve(performance.now());
                    });
                    watch.observe(document.body, { childList: true, subtree: true });
                });
                document.querySelector('[data-action="vibe-sample"]').click();
                const at = await drawn;
                const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const read = () => {
                    const band = document.querySelector('.vibe-overview');
                    const bar = getComputedStyle(band.querySelector('.ov-bar'));
                    return {
                        since: performance.now() - at,
                        enter: band.classList.contains('ov-enter'),
                        into: parseFloat(band.style.getPropertyValue('--ov-into')),
                        name: bar.animationName,
                        delay: parseFloat(bar.animationDelay),
                    };
                };
                const first = read();
                await wait(250);
                window.cairn.rerender();
                const during = read();
                while (performance.now() - at < 800) await wait(20);
                window.cairn.rerender();
                const after = read();
                return { first, during, after };
            }"""
        )
        first, during, after = seen["first"], seen["during"], seen["after"]
        self.assertTrue(first["enter"], seen)
        self.assertEqual(first["into"], 0, seen)
        self.assertEqual(first["name"], "ov-reveal", seen)
        if during["since"] < 500:  # a stalled machine may already be past the draw-in; then there is nothing to carry on
            self.assertTrue(during["enter"], seen)
            self.assertLessEqual(during["into"], -240, seen)  # it picks up where it was; it does not start over
            self.assertLessEqual(during["delay"], -0.24, seen)
        self.assertFalse(after["enter"], seen)  # once drawn, a redraw leaves it alone
        self.assertEqual(after["name"], "none", seen)
        page.wait_for_function("() => [...document.querySelectorAll('.ov-count')].every((n) => n.textContent === n.dataset.count)")
        self.assertEqual(self.errors, [])

    def test_vibe_overview_keeps_still_with_reduced_motion(self):
        page = self.open()
        page.emulate_media(reduced_motion="reduce")
        self.go(page, "vibe")
        self.sample_week(page)
        still = """() => ({
            moving: document.querySelectorAll('.ov-enter, .ov-new, .ov-stepped, .ov-bump').length,
            settled: [...document.querySelectorAll('.ov-count')].every((n) => n.textContent === n.dataset.count),
            bar: getComputedStyle(document.querySelector('.ov-bar')).animationName,
            slide: getComputedStyle(document.querySelector('.ov-seg')).transitionDuration,
        })"""
        self.assertEqual(page.evaluate(still), {"moving": 0, "settled": True, "bar": "none", "slide": "0s"})

        self.start_session(page, "Quiet session")
        page.select_option(".vibe-log-form select", "pass")
        page.fill(".vibe-log-form input", "still green")
        page.press(".vibe-log-form input", "Enter")
        page.wait_for_function("() => document.querySelector('.ov-lane .ov-step .txt')?.textContent === 'still green'")
        self.assertEqual(page.evaluate(still), {"moving": 0, "settled": True, "bar": "none", "slide": "0s"})
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.ov-pulse')).animationName"), "none")
        # Still is not frozen: the clock keeps the time.
        shown = page.inner_text(".ov-lane .ov-clock")
        page.wait_for_function("(was) => document.querySelector('.ov-lane .ov-clock').textContent !== was", arg=shown)
        self.assertEqual(self.errors, [])

    def test_vibe_overview_on_a_phone_in_dark_mode(self):
        page = self.open(viewport=(390, 844), theme="dark")
        page.evaluate("location.hash = '#/vibe'")
        page.wait_for_selector(".vibe-figures")
        self.sample_week(page)
        self.start_session(page, "Pocket check")
        overflow = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        self.assertLessEqual(page.evaluate(overflow), 0)
        sizes = page.eval_on_selector_all("button.ov-tile", "nodes => nodes.map((n) => Math.min(n.getBoundingClientRect().width, n.getBoundingClientRect().height))")
        self.assertEqual(len(sizes), 6)
        self.assertGreaterEqual(min(sizes), 44, sizes)  # every status is big enough to tap
        page.click('.ov-tile[data-outcome="shipped"]')
        page.wait_for_selector('.ov-tile[data-outcome="shipped"][aria-pressed="true"]')
        self.assertGreater(page.locator(".ov-peek li").count(), 0)
        self.assertEqual(page.locator(".session-row").count(), page.locator(".ov-peek li").count())
        self.assertLessEqual(page.evaluate(overflow), 0)
        self.assertEqual(self.errors, [])

    def test_feedback_capture_prefix(self):
        page = self.open()
        page.fill("#capture-input", "fb: offline maps saved my hike #maps")
        self.assertEqual(page.inner_text("#capture-type"), "Feedback")
        page.press("#capture-input", "Enter")
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'feedback/offline-maps-saved-my-hike.md')")
        self.assertIn("status: new", self.note_text(page, "feedback/offline-maps-saved-my-hike.md"))
        self.assertEqual(self.errors, [])

    def test_vibe_view_on_a_phone_in_dark_mode(self):
        page = self.open(viewport=(390, 844), theme="dark")
        page.evaluate("location.hash = '#/vibe'")
        page.wait_for_selector(".vibe-figures")
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 0)
        fold = page.locator("details.vibe-feedback")
        self.assertEqual(fold.count(), 1, "empty feedback folds to one line on a phone")
        self.assertFalse(page.is_visible("#fb-text"))
        page.click("details.vibe-feedback > summary")
        page.wait_for_selector("#fb-text", state="visible")
        page.evaluate("window.cairn.rerender()")
        self.assertTrue(page.is_visible("#fb-text"), "an opened block stays open when the view re-renders")
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

    def test_typing_right_after_opening_stays_in_the_body(self):
        page = self.open()
        self.go(page, "ideas")
        page.click('.item-card[data-drag="ideas/voice-notes-on-trail.md"]')
        # Click into the body immediately, before the drawer's own focus timer fires.
        page.click("#ed-body")
        page.keyboard.type("quick words")
        page.wait_for_timeout(120)
        self.assertEqual(page.input_value("#ed-title"), "Voice notes pinned to the map")
        self.assertIn("quick words", page.input_value("#ed-body"))
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

    def test_autopilot_queue_in_demo(self):
        page = self.open()
        self.go(page, "autopilot")
        self.assertIn("needs Cairn on this computer", page.inner_text("#view"))
        page.fill("#ap-queue-text", "Add CSV export to the report page #auto")
        page.press("#ap-queue-text", "Enter")
        page.wait_for_selector(".ap-q")
        task = page.evaluate("() => window.cairn.state.notes.find((n) => n.tags.includes('queue'))")
        self.assertEqual(task["title"], "Add CSV export to the report page")
        self.assertEqual(task["tags"], ["queue", "auto"])
        self.assertEqual(page.inner_text('.hub-tabs [data-go="autopilot"] .count'), "1")
        page.click('.ap-q [data-flag="needs-me"]')
        page.wait_for_selector(".ap-q.needs-me")
        page.click('.ap-q [data-action="ap-q-done"]')
        page.wait_for_selector(".ap-queue .empty")
        self.assertEqual(self.errors, [])

    def test_founder_studio_wears_cairns_controls_and_follows_its_theme(self):
        """The studio inside Cairn uses Cairn's control shapes and colours, in light and in dark.

        The studio's Python engine (a large download from another site) is replaced by one that never
        starts: this test is about the page's chrome, so it stays offline.
        """

        engine_requests = []

        def idle_engine(route):
            engine_requests.append(route.request.url)
            route.fulfill(
                content_type="text/javascript",
                headers={"access-control-allow-origin": "*"},
                body="export const loadPyodide = () => new Promise(() => {});",
            )

        def open_studio(viewport, theme):
            page = self.open(viewport=viewport, theme=theme)
            page.route("**/pyodide.mjs", idle_engine)
            engine_requests.clear()
            # By address, not by the sidebar: on a phone the sidebar sits in a closed drawer.
            with page.expect_request("**/pyodide.mjs"):
                page.evaluate("location.hash = '#/founder'")
            page.wait_for_function("window.cairn.state.view === 'founder'")
            frame = page.wait_for_selector("#founder-stage iframe").content_frame()
            frame.wait_for_selector("#runBtn")
            frame.wait_for_function("document.readyState !== 'loading'")
            self.assertTrue(engine_requests, "the studio's engine did not come from this test's stand-in")
            return page, frame

        cairn_colours = """() => {
            const colour = (token) => {
                const probe = document.body.appendChild(document.createElement("i"));
                probe.style.color = `var(${token})`;
                const value = getComputedStyle(probe).color;
                probe.remove();
                return value;
            };
            return {
                theme: document.documentElement.dataset.theme,
                paper: colour("--bg"),
                ink: colour("--ink"),
                accent: colour("--accent"),
                overflow: document.documentElement.scrollWidth - window.innerWidth,
            };
        }"""
        studio_look = """() => {
            const style = (selector) => getComputedStyle(document.querySelector(selector));
            const height = (selector) => document.querySelector(selector).getBoundingClientRect().height;
            const run = style("#runBtn");
            const mode = style(".mode .seg.active");
            const tab = style(".tabs .tab.active");
            return {
                embedded: document.documentElement.classList.contains("embedded"),
                theme: document.documentElement.dataset.theme,
                paper: getComputedStyle(document.body).backgroundColor,
                text: getComputedStyle(document.body).color,
                run: { fill: run.backgroundColor, text: run.color, radius: run.borderTopLeftRadius, height: height("#runBtn") },
                project: { radius: style("#projectSelect").borderTopLeftRadius, height: height("#projectSelect") },
                mode: { fill: mode.backgroundColor, shadow: mode.boxShadow, underline: mode.borderBottomColor, height: height(".mode .seg.active") },
                tab: { weight: tab.fontWeight, underline: tab.borderBottomColor },
                overflow: document.documentElement.scrollWidth - window.innerWidth,
            };
        }"""

        def check(page, frame, theme, control_height):
            cairn = page.evaluate(cairn_colours)
            look = frame.evaluate(studio_look)
            where = f"{theme}, {page.viewport_size['width']}px wide"
            self.assertEqual(cairn["theme"], theme, where)
            self.assertTrue(look["embedded"], where)
            self.assertEqual(look["theme"], theme, f"the studio is not in Cairn's theme ({where})")
            self.assertEqual(look["paper"], cairn["paper"], f"the studio's paper is not Cairn's ({where})")
            self.assertEqual(look["text"], cairn["ink"], f"the studio's text is not Cairn's ink ({where})")
            self.assertEqual(look["run"]["fill"], cairn["ink"], f"Run panel is not an ink button ({where})")
            self.assertEqual(look["run"]["text"], cairn["paper"], where)
            self.assertEqual(look["run"]["radius"], "6px", where)
            self.assertEqual(look["project"]["radius"], "6px", where)
            self.assertAlmostEqual(look["run"]["height"], control_height, delta=0.5, msg=where)
            self.assertAlmostEqual(look["project"]["height"], control_height, delta=0.5, msg=where)
            self.assertEqual(look["mode"]["fill"], "rgba(0, 0, 0, 0)", f"the mode switch still has a filled chip ({where})")
            self.assertEqual(look["mode"]["shadow"], "none", where)
            self.assertEqual(look["mode"]["underline"], cairn["accent"], f"the mode switch is not underlined in the accent ({where})")
            self.assertEqual(look["tab"]["weight"], "600", where)
            self.assertEqual(look["tab"]["underline"], cairn["accent"], f"the open result tab is not underlined in the accent ({where})")
            self.assertLessEqual(cairn["overflow"], 0, f"Cairn scrolls sideways ({where})")
            self.assertLessEqual(look["overflow"], 0, f"the studio scrolls sideways ({where})")
            return look

        page, frame = open_studio((1280, 900), "light")
        check(page, frame, "light", 34)
        frame.evaluate("window.sameStudio = true")
        page.click('[data-action="theme"]')
        frame.wait_for_function("document.documentElement.dataset.theme === 'dark'")
        check(page, frame, "dark", 34)
        self.assertTrue(frame.evaluate("window.sameStudio === true"), "changing the theme reloaded the studio")
        self.assertEqual(self.errors, [])

        page, frame = open_studio((390, 844), "dark")
        look = check(page, frame, "dark", 44)
        self.assertGreaterEqual(look["mode"]["height"], 44, "the mode switch is a comfortable tap target on a phone")
        self.assertEqual(self.errors, [])

    def test_mobile_and_dark_have_no_horizontal_scroll(self):
        for theme, width in (("light", 390), ("dark", 390), ("light", 320)):
            page = self.open(viewport=(width, 844), theme=theme)
            for view in VIEWS:
                page.evaluate("(v) => { location.hash = '#/' + v; dispatchEvent(new PopStateEvent('popstate')); }", view)
                page.wait_for_function("(v) => window.cairn.state.view === v", arg=view)
                overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
                self.assertLessEqual(overflow, 0, f"{theme}/{width}px/{view} scrolls sideways by {overflow}px")
            self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), theme)
            self.assertEqual(self.errors, [])


    def test_small_phone_welcome_and_capture_fit(self):
        page = self.open(query="", viewport=(320, 640), wait="#welcome:not([hidden])")
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        self.assertLessEqual(overflow, 0, f"welcome scrolls sideways by {overflow}px at 320px")
        page = self.open(viewport=(320, 640))
        fits = page.evaluate("""() => {
            const input = document.querySelector('#capture-input');
            const style = getComputedStyle(input);
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
            const room = input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
            return { text: ctx.measureText(input.placeholder).width, room };
        }""")
        self.assertLessEqual(fits["text"], fits["room"], f"capture placeholder is clipped: {fits}")

    def test_map_labels_never_overlap(self):
        for theme, viewport in (("light", (1440, 900)), ("dark", (1024, 768))):
            page = self.open(query="?demo=1#/map", viewport=viewport, theme=theme, wait="#map-svg text")
            overlaps = page.evaluate("""() => {
                const boxes = [...document.querySelectorAll('#map-svg text')]
                    .filter((t) => getComputedStyle(t).opacity !== '0')
                    .map((t) => [t.textContent, t.getBoundingClientRect()]);
                const pairs = [];
                boxes.forEach(([na, a], i) => boxes.slice(i + 1).forEach(([nb, b]) => {
                    if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) pairs.push([na, nb]);
                }));
                return pairs;
            }""")
            self.assertEqual(overlaps, [], f"{theme}: overlapping map labels")
            # Labels hidden for lack of room still appear when their dot is hovered or focused.
            quiet_id = page.evaluate("document.querySelector('#map-svg .map-node:has(text.quiet)')?.dataset.node ?? null")
            if quiet_id:
                node = page.locator(f'#map-svg .map-node[data-node="{quiet_id}"]')
                node.focus()
                page.wait_for_timeout(200)
                self.assertEqual(node.locator("text").evaluate("(t) => getComputedStyle(t).opacity"), "1")
            self.assertEqual(self.errors, [])


    # ------------------------------------------------------------ map: Obsidian-plus features

    FAKE_VAULT = """
    (() => {
      class FileHandle {
        constructor(dir, name) { this.kind = 'file'; this.dir = dir; this.name = name; }
        async getFile() {
          const entry = this.dir.files.get(this.name);
          return { lastModified: entry.mtime, size: entry.text.length, text: async () => entry.text };
        }
        async createWritable() {
          const dir = this.dir; const name = this.name; let buffer = '';
          return { write: async (t) => { buffer += t; }, close: async () => { dir.files.set(name, { text: buffer, mtime: Date.now() }); } };
        }
      }
      class DirHandle {
        constructor(name) { this.kind = 'directory'; this.name = name; this.files = new Map(); this.dirs = new Map(); }
        async *entries() {
          for (const [n, d] of this.dirs) yield [n, d];
          for (const n of this.files.keys()) yield [n, new FileHandle(this, n)];
        }
        async getDirectoryHandle(name, opts = {}) {
          if (!this.dirs.has(name)) { if (!opts.create) throw new DOMException('missing', 'NotFoundError'); this.dirs.set(name, new DirHandle(name)); }
          return this.dirs.get(name);
        }
        async getFileHandle(name, opts = {}) {
          if (!this.files.has(name)) { if (!opts.create) throw new DOMException('missing', 'NotFoundError'); this.files.set(name, { text: '', mtime: Date.now() }); }
          return new FileHandle(this, name);
        }
        async removeEntry(name) { this.files.delete(name); }
        async queryPermission() { return 'granted'; }
        async requestPermission() { return 'granted'; }
        async isSameEntry(other) { return other === this; }
      }
      const vault = new DirHandle('My Vault');
      const put = async (path, text) => {
        const parts = path.split('/'); const name = parts.pop(); let dir = vault;
        for (const p of parts) dir = await dir.getDirectoryHandle(p, { create: true });
        dir.files.set(name, { text, mtime: Date.now() - 60000 });
      };
      window.__vault = { root: vault, put, read: async (path) => {
        const parts = path.split('/'); const name = parts.pop(); let dir = vault;
        for (const p of parts) { dir = dir.dirs.get(p); if (!dir) return null; }
        return dir.files.get(name)?.text ?? null;
      } };
      window.showDirectoryPicker = async () => vault;
    })()
    """

    def test_obsidian_vault_imports_and_syncs_both_ways(self):
        page = self.open(query="?demo=1#/obsidian", wait="#obsidian-form")
        page.evaluate(self.FAKE_VAULT)
        page.evaluate(r"""async () => {
            await window.__vault.put('.obsidian/app.md', 'settings, never synced');
            await window.__vault.put('Projects/Trail Plan.md', '---\ntags: [hiking]\n---\nSee [[Gear List|gear]] and [[Competitor scan]].\n');
            await window.__vault.put('Gear List.md', 'Tent, stove.\n');
            await window.__vault.put('Daily/2026-11-05.md', '- packed\n');
        }""")
        page.click('#obsidian-form button[type="submit"]')
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'obsidian/Projects/Trail Plan.md')")
        paths = page.evaluate("window.cairn.state.files.map((f) => f.path).filter((p) => p.startsWith('obsidian/'))")
        self.assertEqual(sorted(paths), ["obsidian/Daily/2026-11-05.md", "obsidian/Gear List.md", "obsidian/Projects/Trail Plan.md"])
        types = page.evaluate("Object.fromEntries(window.cairn.state.notes.filter((n) => n.path.startsWith('obsidian/')).map((n) => [n.path, n.type]))")
        self.assertEqual(types["obsidian/Daily/2026-11-05.md"], "journal")
        page.wait_for_selector(".sync-card.linked")
        self.assertIn("3 notes in sync", page.inner_text(".sync-card.linked"))
        # Obsidian links resolve on the map, including links into the existing Cairn notes.
        edges = page.evaluate(r"""async () => {
            const core = window.cairn.core;
            return core.buildGraph(window.cairn.state.notes).edges.filter((e) => e.source.startsWith('obsidian/')).map((e) => e.target).sort();
        }""")
        self.assertEqual(edges, ["notes/competitor-scan.md", "obsidian/Gear List.md"])
        # Edit in Cairn -> lands in the vault; edit in the vault -> lands in Cairn.
        page.evaluate(r"""async () => {
            const state = window.cairn.state;
            const f = state.files.find((x) => x.path === 'obsidian/Gear List.md');
            const { mtime } = await state.store.write(f.path, 'Tent, stove, map.\n', f.mtime);
            f.text = 'Tent, stove, map.\n'; f.mtime = mtime;
            await window.__vault.put('Daily/2026-11-05.md', '- packed\n- left at dawn\n');
            const dir = window.__vault.root.dirs.get('Daily'); dir.files.get('2026-11-05.md').mtime = Date.now();
        }""")
        page.click('[data-action="obsidian-sync"]')
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'obsidian/Daily/2026-11-05.md').text.includes('left at dawn')")
        self.assertEqual(page.evaluate("window.__vault.read('Gear List.md')"), "Tent, stove, map.\n")
        self.assertIsNone(page.evaluate("window.cairn.state.files.find((f) => f.path.includes('.obsidian')) || null"))
        self.assertIn("from Obsidian", page.inner_text(".sync-report"))
        self.assertEqual(self.errors, [])

    def test_map_alt_drag_links_missing_notes_and_growth(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        # Two notes with no link between them, both on screen.
        pair = page.evaluate(r"""() => {
            const { state, core } = window.cairn;
            const g = core.buildGraph(state.notes);
            const linked = new Set(g.edges.flatMap((e) => [e.source + '|' + e.target, e.target + '|' + e.source]));
            const ids = g.nodes.map((n) => n.id).filter((id) => document.querySelector(`#map-svg .map-node[data-node="${id}"]`));
            for (const a of ids) for (const b of ids) if (a !== b && !linked.has(a + '|' + b)) return [a, b];
            return null;
        }""")
        self.assertIsNotNone(pair)
        source, target = pair

        def centre(node_id):
            box = page.locator(f'#map-svg .map-node[data-node="{node_id}"] circle').bounding_box()
            return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

        (ax, ay), (bx, by) = centre(source), centre(target)
        page.keyboard.down("Alt")
        page.mouse.move(ax, ay)
        page.mouse.down()
        page.mouse.move((ax + bx) / 2, (ay + by) / 2, steps=6)
        page.mouse.move(bx, by, steps=6)
        page.mouse.up()
        page.keyboard.up("Alt")
        page.wait_for_function(
            r"""([a, b]) => {
                const { state, core } = window.cairn;
                return core.buildGraph(state.notes).edges.some((e) => (e.source === a && e.target === b) || (e.source === b && e.target === a));
            }""",
            arg=[source, target],
        )
        self.assertIn("Linked", page.inner_text(".toast"))

        # Missing notes: link to a note that doesn't exist, show missing notes, double-click to create it.
        page.evaluate(r"""async () => {
            const { state, core } = window.cairn;
            const f = state.files.find((x) => x.path === 'notes/architecture.md');
            const text = f.text.replace(/\s*$/, '') + '\n\nSee [[trail-snacks]].\n';
            const { mtime } = await state.store.write(f.path, text, f.mtime);
            f.text = text;
            f.mtime = mtime;
            const i = state.notes.findIndex((n) => n.path === f.path);
            state.notes[i] = core.noteFromFile(f.path, text, mtime);
            window.cairn.rerender();
        }""")
        page.wait_for_selector('[data-map="show-missing"]')
        page.click('[data-map="show-missing"]')
        page.wait_for_selector("#map-svg .map-ghost")
        ghost = page.evaluate("document.querySelector('#map-svg .map-ghost').dataset.node")
        page.locator(f'#map-svg .map-ghost[data-node="{ghost}"]').dispatch_event("dblclick")
        page.wait_for_function(
            "(id) => !document.querySelector(`#map-svg .map-ghost[data-node=\"${id}\"]`)",
            arg=ghost,
        )
        unresolved = page.evaluate("window.cairn.core.buildGraph(window.cairn.state.notes).unresolved.map((u) => '?' + u.target.toLowerCase())")
        self.assertNotIn(ghost, unresolved)
        self.assertEqual(ghost, "?trail-snacks")
        created = page.evaluate("window.cairn.state.notes.find((n) => n.path === 'notes/trail-snacks.md')?.title")
        self.assertEqual(created, "Trail snacks")

        # Growth time-lapse: everything starts hidden, then the whole map is back.
        page.click('[data-map="grow"]')
        page.wait_for_selector("#map-growth:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#map-svg .future')", timeout=15000)
        self.assertEqual(self.errors, [])

    def test_map_3d_mode_turns_selects_and_remembers(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        page.evaluate("localStorage.removeItem('cairn.map')")
        page.click('[data-map="view"][data-view="3d"]')
        page.wait_for_selector("#map-canvas")
        self.assertEqual(page.locator("#map-svg").count(), 0)
        page.wait_for_function("() => window.cairn.map3d && window.cairn.map3d.settled", timeout=15000)
        # Every note has a place in space (canvases too), and the layout really uses depth.
        bodies = page.evaluate("window.cairn.map3d.bodies()")
        canvases = [b for b in bodies if str(b["id"]).startswith("canvas:")]
        self.assertEqual(len(bodies) - len(canvases), DEMO_NOTES)
        self.assertEqual(len(canvases), 1, "the demo canvas is on the map")
        zs = [b["z"] for b in bodies]
        self.assertGreater(max(zs) - min(zs), 40, "the 3D layout should not be flat")
        # Dragging turns the map (a trackball: any direction, no limit).
        if page.get_attribute('[data-map="spin"]', "aria-pressed") == "true":
            page.click('[data-map="spin"]')
        before = page.evaluate("window.cairn.map3d.camera.rot")
        box = page.locator("#map-canvas").bounding_box()
        x, y = box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.75
        page.mouse.move(x, y)
        page.mouse.down()
        for i in range(1, 16):
            page.mouse.move(x + i * 14, y - i * 10)
        page.mouse.up()
        self.assertNotEqual(page.evaluate("window.cairn.map3d.camera.rot"), before)
        page.keyboard.press("f")
        page.wait_for_timeout(600)
        # Clicking a dot selects it and opens the card, as in 2D.
        hub = page.evaluate("""() => {
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const deg = new Map();
            g.edges.forEach((e) => { deg.set(e.source, (deg.get(e.source) || 0) + 1); deg.set(e.target, (deg.get(e.target) || 0) + 1); });
            return [...deg.entries()].sort((a, b) => b[1] - a[1])[0][0];
        }""")
        pos = page.evaluate("(id) => window.cairn.map3d.screenPos(id)", hub)
        page.mouse.click(box["x"] + pos["x"], box["y"] + pos["y"])
        page.wait_for_selector("#map-card:not([hidden])")
        title = page.evaluate("(p) => window.cairn.state.notes.find((n) => n.path === p).title", hub)
        self.assertEqual(page.inner_text("#map-card strong"), title)
        # "Watch it grow" replays the brain in 3D too, then everything is back.
        page.click('[data-map="grow"]')
        page.wait_for_selector("#map-growth:not([hidden])")
        page.wait_for_selector("#map-growth", state="hidden", timeout=15000)
        # The choice is remembered; "2" goes back to the flat map.
        page.reload()
        page.wait_for_selector("#map-canvas")
        page.locator("#map-canvas").focus()
        page.keyboard.press("2")
        page.wait_for_selector("#map-svg text")
        self.assertEqual(page.locator(".map-node").count(), DEMO_NOTES)
        self.assertEqual(self.errors, [])

    def test_map_select_keyboard_local_and_path(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        page.evaluate("localStorage.removeItem('cairn.map')")
        hub = page.evaluate("""() => {
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const deg = new Map();
            g.edges.forEach((e) => { deg.set(e.source, (deg.get(e.source) || 0) + 1); deg.set(e.target, (deg.get(e.target) || 0) + 1); });
            return [...deg.entries()].sort((a, b) => b[1] - a[1])[0][0];
        }""")
        node = page.locator(f'#map-svg .map-node[data-node="{hub}"]')
        node.locator("circle").click()  # the dot itself: the group's box also spans the label, which ignores the pointer
        self.assertIn("selected", node.get_attribute("class"))
        self.assertTrue(page.locator("#map-card").is_visible())
        # Arrow keys walk to a linked note.
        before = hub
        for key in ("ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"):
            page.keyboard.press(key)
            now = page.evaluate("document.querySelector('#map-svg .map-dot.selected')?.dataset.node")
            if now != before:
                break
        self.assertNotEqual(now, before, "arrow keys should move the selection")
        # L opens the local map of the selected note; Escape goes back to the whole map.
        page.keyboard.press("l")
        page.wait_for_selector(".map-chip.local")
        self.assertEqual(page.locator(".log-sheet").count(), 0, "L on the Map is the local map, not the voice log")
        self.assertLess(page.locator(".map-node").count(), DEMO_NOTES)
        page.locator("#map-svg .map-dot.selected").focus()
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('.map-chip.local')")
        self.assertEqual(page.locator(".map-node").count(), DEMO_NOTES)
        # Path between two notes on the same island.
        ends = page.evaluate("""async () => {
            const G = await import('./graph.js');
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const adj = G.adjacency(g);
            const big = G.components(adj)[0];
            let best = null;
            for (const a of big) for (const b of big) {
                const p = G.shortestPath(adj, a, b);
                if (p && (!best || p.length > best.length)) best = p;
            }
            const title = (id) => window.cairn.state.notes.find((n) => n.path === id).title;
            return { from: title(best[0]), to: title(best.at(-1)), steps: best.length };
        }""")
        page.fill('#map-path [name="from"]', ends["from"])
        page.fill('#map-path [name="to"]', ends["to"])
        page.click('#map-path button[type="submit"]')
        page.wait_for_selector(".path-steps li")
        self.assertEqual(page.locator(".path-steps li").count(), ends["steps"])
        self.assertEqual(page.locator(".map-edge.on-path").count(), ends["steps"] - 1)
        self.assertEqual(self.errors, [])

    def test_map_filters_and_settings_persist(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        tasks = page.evaluate("window.cairn.state.notes.filter((n) => n.type === 'task').length")
        page.click('[data-map="type"][data-type="task"]')
        page.wait_for_function("(n) => document.querySelectorAll('.map-node').length === n", arg=tasks)
        page.check('[data-map-setting="arrows"]')
        page.wait_for_selector(".map-edges.arrows", state="attached")
        page.reload()
        page.wait_for_selector("#map-svg text")
        self.assertEqual(page.locator(".map-node").count(), tasks)
        page.click('[data-map="clear-filters"]')
        page.wait_for_function("() => document.querySelectorAll('.map-node').length === %d" % DEMO_NOTES)
        orphans = page.evaluate("""() => {
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const linked = new Set(g.edges.flatMap((e) => [e.source, e.target]));
            return g.nodes.filter((n) => !linked.has(n.id)).length;
        }""")
        page.uncheck('[data-map-setting="orphans"]')
        page.wait_for_function("(n) => document.querySelectorAll('.map-node').length === n", arg=DEMO_NOTES - orphans)
        page.check('[data-map-setting="orphans"]')
        page.uncheck('[data-map-setting="arrows"]')
        self.assertEqual(self.errors, [])

    def test_map_suggested_link_is_written_without_touching_the_rest(self):
        page = self.open(query="?demo=1#/map", wait="#map-svg text")
        page.evaluate("localStorage.removeItem('cairn.map')")
        info = page.evaluate("""async () => {
            const { saveNote, state } = await import('./state.js');
            const notes = state.notes;
            const target = notes.find((n) => n.title.trim().includes(' ') && !notes.some((o) => o !== n && o.title === n.title));
            const g = window.cairn.core.buildGraph(notes);
            const linked = new Set(g.edges.filter((e) => e.source === target.path || e.target === target.path).flatMap((e) => [e.source, e.target]));
            const source = notes.find((n) => n.path !== target.path && !linked.has(n.path));
            await saveNote(source.path, { body: source.body.trimEnd() + `\\n\\nWe should revisit ${target.title} soon.\\n` });
            window.cairn.rerender();
            return { source: source.path, target: target.path, title: target.title, edges: g.edges.length };
        }""")
        before = self.note_text(page, info["source"])
        row = page.locator(f'.suggestion[data-a="{info["source"]}"][data-b="{info["target"]}"]')
        row.locator('[data-map="link"]').click()
        page.wait_for_function("(p) => window.cairn.state.files.find((f) => f.path === p).text.includes('[[')", arg=info["source"])
        after = self.note_text(page, info["source"])
        link = after[after.index("[["):after.index("]]") + 2] if after.count("[[") > before.count("[[") else None
        self.assertIsNotNone(link)
        self.assertEqual(after.replace(link, info["title"], 1), before, "only the mention may change")
        edges = page.evaluate("window.cairn.core.buildGraph(window.cairn.state.notes).edges.length")
        self.assertEqual(edges, info["edges"] + 1)
        self.assertEqual(self.errors, [])

    def test_graph_algorithms(self):
        page = self.open()
        result = page.evaluate("""async () => {
            const G = await import('./graph.js');
            const core = window.cairn.core;
            const notes = [
                core.noteFromFile('a.md', '# Alpha\\nSee [[b]]. Glow is great; glow is a word. `Beta Note` in code. [[c|Gamma]]'),
                core.noteFromFile('b.md', '---\\ntitle: Beta Note\\n---\\nlinks [[c]]'),
                core.noteFromFile('c.md', '# Gamma\\nmentions Beta Note and Glow'),
                core.noteFromFile('Glow.md', '# Glow\\nproject'),
                core.noteFromFile('d.md', '# Delta\\nalone'),
            ];
            const graph = core.buildGraph(notes);
            const adj = G.adjacency(graph);
            const mentions = G.unlinkedMentions(notes, graph).map((m) => `${m.from}>${m.to}:${m.phrase}x${m.count}`);
            return {
                path: G.shortestPath(adj, 'a.md', 'c.md'),
                none: G.shortestPath(adj, 'a.md', 'd.md'),
                near: [...G.neighbourhood(adj, 'b.md', 1).keys()].sort(),
                islands: G.components(adj).map((c) => c.length),
                mentions,
                linked: G.linkMention('`Glow` and [[x|Glow]] then Glow here', 'Glow', 'Glow'),
                aliased: G.linkMention('see beta note', 'beta note', 'b'),
                retarget: G.retargetLink('[[Alpha — Notes#Plan|the plan]] and `[[Alpha — Notes]]`', 'Alpha — Notes', 'Alpha - Notes'),
            };
        }""")
        self.assertEqual(result["path"], ["a.md", "c.md"])
        self.assertIsNone(result["none"])
        self.assertEqual(result["near"], ["a.md", "b.md", "c.md"])
        self.assertEqual(result["islands"], [3, 1, 1])
        # One-word names count only with the same spelling; code and existing links never count.
        self.assertEqual(sorted(result["mentions"]), ["a.md>Glow.md:Glowx1", "c.md>Glow.md:Glowx1"])
        self.assertEqual(result["linked"], "`Glow` and [[x|Glow]] then [[Glow]] here")
        self.assertEqual(result["aliased"], "see [[b|beta note]]")
        self.assertEqual(result["retarget"], "[[Alpha - Notes#Plan|the plan]] and `[[Alpha — Notes]]`")

    # ------------------------------------------------------------ network: profiles + read-only brains

    def test_github_input_parsing(self):
        page = self.open()
        results = page.evaluate(
            """async () => {
                const gh = await import('./github.js');
                return ['juw12', '@juw12', 'https://github.com/juw12', 'github.com/juw12/cairn/', 'juw12/cairn',
                        'https://github.com/juw12/cairn/tree/main/brain', 'not a user!', '', 'a/b/c..x']
                    .map((v) => gh.parseGithubInput(v));
            }"""
        )
        self.assertEqual(results[0], {"owner": "juw12", "repo": None})
        self.assertEqual(results[1], {"owner": "juw12", "repo": None})
        self.assertEqual(results[2], {"owner": "juw12", "repo": None})
        self.assertEqual(results[3], {"owner": "juw12", "repo": "cairn"})
        self.assertEqual(results[4], {"owner": "juw12", "repo": "cairn"})
        self.assertEqual(results[5], {"owner": "juw12", "repo": "cairn"})
        self.assertIsNone(results[6])
        self.assertIsNone(results[7])

    def test_profile_shows_proof_of_work(self):
        page = self.open("?u=juw12")
        page.wait_for_selector(".proof-grid")
        profile = page.inner_text(".profile-card")
        self.assertIn("Juwen Lie", profile)
        self.assertIn("@juw12", profile)
        proof = page.inner_text(".proof-grid")
        self.assertIn("Get five people to publish a brain", proof)  # doing
        self.assertIn("Explore: look up people and open their brains", proof)  # shipped
        self.assertIn("GitHub is the backend", proof)  # accepted decision
        self.assertIn("AI agents as brain members", proof)  # ready + help-wanted
        self.assertNotIn("Join-request inbox", proof)  # todo tasks are not claimed as work
        self.assertGreaterEqual(page.locator(".repo-row").count(), 1)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ proof of work: claims, levels, vouches

    def test_proof_rules(self):
        page = self.open()
        result = page.evaluate("""async () => {
            const P = await import('./proof.js');
            const core = window.cairn.core;
            const ev = (u) => P.parseEvidence(u);
            const claim = { slug: 'built-x', title: 'Built X' };
            const vouch = (meta) => '---\\n' + Object.entries(meta).map(([k, v]) => k + ': ' + v).join('\\n') + '\\n---\\nSaw it ship.\\n';
            const fileLink = ev('https://github.com/alice/brain/blob/main/vouches/bob/built-x.md');
            return {
                kinds: ['https://github.com/a/b', 'https://github.com/a/b/pull/3', 'https://github.com/a/b/commit/abc1234',
                        'https://github.com/a/b/releases/tag/v1.0', 'https://github.com/a/b/tree/main/src', 'https://example.com/x',
                        'https://github.com/a/b/issues/2'].map((u) => ev(u).kind),
                ownRepo: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { owner: 'bob', stars: 0, forks: 0 }).level,
                starred: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { owner: 'Bob', stars: 3, forks: 0 }).level,
                forked: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { owner: 'bob', stars: 0, forks: 1 }).level,
                notMine: P.judgeEvidence('bob', ev('https://github.com/eve/x'), { owner: 'eve', stars: 99, forks: 9 }).ok,
                fork: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { owner: 'bob', fork: true }).ok,
                prOther: P.judgeEvidence('bob', ev('https://github.com/eve/x/pull/1'), { owner: 'eve', author: 'bob', merged: true }).level,
                prOwn: P.judgeEvidence('bob', ev('https://github.com/bob/x/pull/1'), { owner: 'bob', author: 'bob', merged: true }).level,
                prOpen: P.judgeEvidence('bob', ev('https://github.com/eve/x/pull/1'), { owner: 'eve', author: 'bob', merged: false }).ok,
                prStolen: P.judgeEvidence('bob', ev('https://github.com/eve/x/pull/1'), { owner: 'eve', author: 'eve', merged: true }).ok,
                commit: P.judgeEvidence('bob', ev('https://github.com/eve/x/commit/abcdef1'), { owner: 'eve', author: 'bob' }).level,
                missing: P.judgeEvidence('bob', ev('https://github.com/bob/x'), { error: 'Not found on GitHub.' }).note,
                vouchOk: P.judgeVouch('bob', claim, fileLink, vouch({ vouch_for: 'bob', claim: 'built-x', relation: 'client' })).level,
                vouchSelf: P.judgeVouch('alice', claim, fileLink, vouch({ vouch_for: 'alice', claim: 'built-x' })).ok,
                vouchWrongClaim: P.judgeVouch('bob', claim, fileLink, vouch({ vouch_for: 'bob', claim: 'other' })).ok,
                vouchWrongPerson: P.judgeVouch('bob', claim, fileLink, vouch({ vouch_for: 'carol', claim: 'built-x' })).ok,
                vouchMissing: P.judgeVouch('bob', claim, fileLink, null).ok,
                levelNone: P.claimLevel([]),
                levelBest: P.claimLevel([{ ok: true, level: 2 }, { ok: false, level: 4 }, { ok: true, level: 3 }]),
                isPublic: [{ publish: 'true' }, { publish: true }, { publish: 'yes' }, { publish: 'true', private: 'true' }, {}].map(P.isPublic),
                claims: P.claimsFromNotes([
                    core.noteFromFile('n/a.md', '---\\nclaim: Did A\\nevidence: [https://github.com/a/b]\\ncreated: 2026-01-01\\n---\\n'),
                    core.noteFromFile('n/b.md', '---\\ntitle: no claim\\n---\\n'),
                    core.noteFromFile('n/c.md', '---\\nclaim: Did C\\nevidence:\\n  - https://github.com/a/c/pull/1\\npublish: true\\ncreated: 2026-02-01\\n---\\n'),
                ]).map((c) => [c.slug, c.evidence.map((e) => e.kind).join(','), c.isPublic]),
                url: P.vouchUrl({ subject: 'Bob', claim, voucher: 'alice', repo: 'brain', today: '2026-11-07' }),
                link: P.vouchLink({ subject: 'Bob', claim, voucher: 'alice', repo: 'brain' }),
            };
        }""")
        self.assertEqual(result["kinds"], ["repo", "pr", "commit", "release", "repo", "link", "link"])
        self.assertEqual((result["ownRepo"], result["starred"], result["forked"]), (2, 3, 3))
        self.assertFalse(result["notMine"])
        self.assertFalse(result["fork"])
        self.assertEqual((result["prOther"], result["prOwn"]), (3, 2))
        self.assertFalse(result["prOpen"])
        self.assertFalse(result["prStolen"])
        self.assertEqual(result["commit"], 2)
        self.assertEqual(result["missing"], "Not found on GitHub.")
        self.assertEqual(result["vouchOk"], 4)
        self.assertFalse(result["vouchSelf"])
        self.assertFalse(result["vouchWrongClaim"])
        self.assertFalse(result["vouchWrongPerson"])
        self.assertFalse(result["vouchMissing"])
        self.assertEqual((result["levelNone"], result["levelBest"]), (1, 3))
        self.assertEqual(result["isPublic"], [True, True, False, False, False])
        self.assertEqual(result["claims"], [["c", "pr", True], ["a", "repo", False]])
        self.assertTrue(result["url"].startswith("https://github.com/alice/brain/new/main?filename=vouches%2Fbob%2Fbuilt-x.md&value="))
        self.assertIn("vouch_for%3A%20Bob", result["url"])
        self.assertEqual(result["link"], "https://github.com/alice/brain/blob/main/vouches/bob/built-x.md")
        # The publish rule is the same in Python (cairn publish) and JS (the app's Public/Private pill).
        from cairn.publish import is_public
        cases = [{"publish": "true"}, {"publish": True}, {"publish": "yes"}, {"publish": "true", "private": "true"}, {}]
        self.assertEqual([is_public(c) for c in cases], result["isPublic"])

    def test_profile_claims_are_checked_on_github(self):
        def setup(mock):
            claims = {
                "brain/notes/claim-pr.md": "---\nclaim: Fixed offline sync in someone/tool\nevidence: [https://github.com/someone/tool/pull/7]\ncreated: 2026-11-03\n---\n",
                "brain/notes/claim-vouched.md": "---\nclaim: Ran the Trailmix beta\nevidence: [https://github.com/juw12/cairn/commit/abcdef1]\nattested_by: [https://github.com/alice/brain/blob/main/vouches/juw12/claim-vouched.md]\ncreated: 2026-11-02\n---\n",
                "brain/notes/claim-stolen.md": "---\nclaim: Wrote the parser\nevidence: [https://github.com/someone/tool/pull/8]\ncreated: 2026-11-01\n---\n",
                "brain/notes/claim-bare.md": "---\nclaim: Mentored three interns\ncreated: 2026-10-01\n---\n",
            }
            for path, text in claims.items():
                mock.set_text("juw12/cairn", path, text)
            mock.extra["/repos/someone/tool/pulls/7"] = {"user": {"login": "juw12"}, "merged_at": "2026-11-03T10:00:00Z"}
            mock.extra["/repos/someone/tool/pulls/8"] = {"user": {"login": "someone"}, "merged_at": "2026-11-01T10:00:00Z"}
            mock.extra["/repos/juw12/cairn/commits/abcdef1"] = {"author": {"login": "juw12"}, "commit": {"author": {"date": "2026-11-02T09:00:00Z"}}}
            mock.repos["alice/brain"] = {
                "meta": dict(mock.repos["juw12/cairn"]["meta"], name="brain", full_name="alice/brain", owner={"login": "alice"}),
                "files": {"vouches/juw12/claim-vouched.md": "---\nvouch_for: juw12\nclaim: claim-vouched\nrelation: client\n---\nI ran the beta with Juwen.\n"},
            }

        page = self.open("?u=juw12", setup=setup)
        page.wait_for_selector(".claims .lvl-summary", timeout=15000)
        level = lambda slug: page.inner_text(f'.claim[data-claim="{slug}"] .lvl-badge')  # noqa: E731
        self.assertIn("L3", level("claim-built-cairn"))  # own repo, 3 stars
        self.assertIn("L3", level("claim-pr"))  # merged into someone else's repo
        self.assertIn("L4", level("claim-vouched"))  # alice vouched in her own repo
        self.assertIn("L1", level("claim-stolen"))  # PR opened by someone else
        self.assertIn("L1", level("claim-bare"))  # no evidence at all
        stolen = page.inner_text('.claim[data-claim="claim-stolen"]')
        self.assertIn("Opened by @someone, not @juw12", stolen)
        # Per level: L1 stolen + bare, L2 none, L3 repo + merged PR, L4 the vouched one.
        self.assertEqual(page.locator(".claims .lvl-num").all_inner_texts(), ["2", "0", "2", "1"])
        # Signed out, so anyone may vouch: the button opens GitHub's new-file page in the voucher's repo.
        self.assertGreaterEqual(page.locator('.claim [data-action="vouch"]').count(), 5)
        self.assertEqual(self.errors, [])

    def test_proof_view_lists_claims_and_creates_one(self):
        page = self.open()
        self.go(page, "proof")
        self.assertIn("No claims yet", page.inner_text("#view"))
        page.click('[data-action="new-claim"]')
        page.fill(".modal input", "Shipped offline maps")
        page.click(".modal button[type=submit]")
        page.wait_for_selector(".drawer")
        text = page.evaluate("window.cairn.state.files.find((f) => f.text.includes('claim: Shipped offline maps'))?.text")
        self.assertIsNotNone(text)
        self.assertIn("evidence: []", text)
        self.assertIn("publish: false", text)
        page.keyboard.press("Escape")
        self.go(page, "proof")
        card = page.inner_text(".claim")
        self.assertIn("Shipped offline maps", card)
        self.assertIn("Private", card)
        self.assertIn("Not checked", card)
        self.assertEqual(self.errors, [])

    def test_proof_view_of_someone_elses_brain_checks_itself(self):
        page = self.open("?brain=juw12/cairn#/proof", wait=".claim")
        page.wait_for_selector(".lvl-summary", timeout=15000)
        self.assertIn("L3", page.inner_text('.claim[data-claim="claim-built-cairn"] .lvl-badge'))
        self.assertEqual(page.inner_text('.hub-tabs [data-go="proof"] .count'), "1")
        self.assertEqual(page.locator('[data-action="new-claim"]').count(), 0)  # read-only: no create button
        self.assertEqual(self.errors, [])

    def test_unknown_user_and_bad_input(self):
        page = self.open()
        go_to(page, "people")
        page.fill("#people-query", "nobody-here-123")
        page.click("#people-form button[type=submit]")
        page.wait_for_selector(".notice")
        self.assertIn("Not found", page.inner_text("#view"))
        page.fill("#people-query", "not a user!")
        page.click("#people-form button[type=submit]")
        page.wait_for_function("() => document.querySelector('#view').innerText.includes(\"doesn't look like\")")

    def test_typing_survives_background_render(self):
        page = self.open()
        go_to(page, "people")
        page.fill("#people-query", "juw12")
        page.evaluate("window.cairn.rerender()")
        self.assertEqual(page.input_value("#people-query"), "juw12")
        self.assertEqual(self.errors, [])

    def test_read_only_brain(self):
        page = self.open("?brain=juw12/cairn")
        page.wait_for_selector(".ro-banner")
        self.assertTrue(page.locator("#capture-form").is_hidden())
        self.assertEqual(page.evaluate("window.cairn.state.notes.length"), 19)
        join = page.get_attribute(".ro-banner a.btn.primary", "href")
        self.assertTrue(join.startswith("https://github.com/juw12/cairn/issues/new?title="), join)
        self.assertIn("Request%20to%20join", join)
        self.go(page, "board")
        self.assertEqual(page.locator('.item-card[draggable="true"]').count(), 0)
        self.assertEqual(page.locator(".quick-add").count(), 0)
        page.click('.item-card[data-open="tasks/mcp-server.md"]')
        page.wait_for_selector(".drawer")
        self.assertTrue(page.is_disabled("#ed-title"))
        self.assertTrue(page.locator('[data-action="delete"]').is_hidden())
        self.assertEqual(page.locator("#ed-body").count(), 0)
        page.keyboard.press("Escape")
        # No create buttons are offered on someone else's brain...
        self.assertEqual(page.locator('.page-head [data-action="new"]').count(), 0)
        # ...and the action guard still refuses if one is triggered anyway (e.g. a stale render).
        before = page.evaluate("window.cairn.state.files.length")
        page.evaluate(
            """() => {
                const button = document.createElement("button");
                button.dataset.action = "new";
                button.dataset.type = "task";
                button.id = "stale-new";
                document.querySelector("main").appendChild(button);
            }"""
        )
        page.click("#stale-new")
        page.wait_for_selector(".toast")
        self.assertEqual(page.evaluate("window.cairn.state.files.length"), before)
        self.assertEqual(self.errors, [])

    def test_welcome_explore_box(self):
        page = self.open("", wait=".choices")
        page.evaluate("localStorage.clear()")
        page.reload()
        page.wait_for_selector("#explore-form")
        page.fill("#explore-input", "https://github.com/juw12")
        page.press("#explore-input", "Enter")
        page.wait_for_selector(".profile-card")
        self.assertIn("u=juw12", page.url)


if __name__ == "__main__":
    unittest.main()

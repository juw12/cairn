"""Canvas, tabs and community plugins in the real web app (headless browser, demo mode)."""

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

CANVAS = "canvases/v1 launch plan.canvas"
BUNDLED = ["note-queries", "calendar", "writing-stats", "recent-files"]


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class WorkspaceTests(unittest.TestCase):
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

    def open(self, query="?demo=1", viewport=(1440, 900)):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        install_github_mock(context)
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script("localStorage.setItem('cairn.theme', 'light')")
        page.goto(self.url + query)
        page.wait_for_selector("#nav button")
        self.addCleanup(context.close)
        return page

    def palette_labels(self, page):
        page.wait_for_timeout(150)
        return page.eval_on_selector_all(".palette-item .label", "els => els.map((e) => e.textContent.trim())")

    def canvas_data(self, page, path=CANVAS):
        return page.evaluate("(p) => JSON.parse(window.cairn.state.files.find((f) => f.path === p).text)", path)

    def open_canvas(self, page):
        page.click('#nav [data-go="canvas"]')
        page.click(f'.cv-card[data-cv-open="{CANVAS}"]')
        page.wait_for_selector(".cv-node")

    # ------------------------------------------------------------ canvas

    def test_canvas_renders_an_obsidian_board_and_keeps_its_format(self):
        page = self.open()
        self.open_canvas(page)
        self.assertEqual(page.locator(".cv-node").count(), 9)
        self.assertEqual(page.locator(".cv-edge").count(), 4)
        self.assertEqual(page.locator(".cv-group").count(), 1)
        # Notes on the board show their real content and open in the editor.
        gpx = page.locator('[data-node="f-gpx"]')
        self.assertIn("GPX import", gpx.inner_text())
        gpx.locator("a.wikilink").click()
        page.wait_for_selector("#ed-body")
        self.assertEqual(page.evaluate("() => window.cairn.state.open"), "tasks/gpx-import.md")
        page.keyboard.press("Escape")
        self.assertIn("decided by", page.text_content("#cv-edges"))
        self.assertEqual(self.errors, [])

    def test_canvas_edit_move_connect_undo_and_save(self):
        page = self.open()
        self.open_canvas(page)
        before = self.canvas_data(page)
        # Drag a card: it moves on the grid and the file is saved as tab-indented JSON Canvas.
        box = page.locator('[data-node="t-goal"]').bounding_box()
        page.mouse.move(box["x"] + 30, box["y"] + 15)
        page.mouse.down()
        page.mouse.move(box["x"] + 130, box["y"] + 15, steps=6)
        page.mouse.up()
        page.wait_for_function("(p) => JSON.parse(window.cairn.state.files.find((f) => f.path === p).text).nodes.find((n) => n.id === 't-goal').x > -80", arg=CANVAS)
        text = page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p).text", CANVAS)
        self.assertIn('\n\t"nodes": [', text)
        moved = next(n for n in self.canvas_data(page)["nodes"] if n["id"] == "t-goal")
        self.assertGreater(moved["x"], -80)
        self.assertEqual(moved["x"] % 10, 0)
        # Double-click the empty board: a new card, typed straight away.
        wrap = page.locator("#cv-wrap").bounding_box()
        page.mouse.dblclick(wrap["x"] + wrap["width"] - 100, wrap["y"] + wrap["height"] - 80)
        page.wait_for_selector(".cv-editor")
        page.keyboard.type("Pack the **headlamp**")
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
        data = self.canvas_data(page)
        card = next(n for n in data["nodes"] if n.get("text") == "Pack the **headlamp**")
        self.assertEqual(card["type"], "text")
        self.assertIn("<strong>headlamp</strong>", page.locator(f'[data-node="{card["id"]}"]').inner_html())
        # Connect the goal card to it by dragging the goal's right handle onto the new card.
        page.locator('[data-node="t-goal"]').hover()
        handle = page.locator('[data-node="t-goal"] .cv-handle[data-side="right"]').bounding_box()
        target = page.locator(f'[data-node="{card["id"]}"]').bounding_box()
        page.mouse.move(handle["x"] + 6, handle["y"] + 6)
        page.mouse.down()
        page.mouse.move(target["x"] + target["width"] / 2, target["y"] + target["height"] / 2, steps=10)
        page.mouse.up()
        page.wait_for_timeout(800)
        data = self.canvas_data(page)
        self.assertTrue(any(e["fromNode"] == "t-goal" and e["toNode"] == card["id"] and e["fromSide"] == "right" for e in data["edges"]))
        # Colour it, then delete it with the keyboard; its edges go too.
        page.locator(f'[data-node="{card["id"]}"]').click()
        page.click('[data-cv-color="4"]')
        page.wait_for_timeout(700)
        self.assertEqual(next(n for n in self.canvas_data(page)["nodes"] if n["id"] == card["id"])["color"], "4")
        page.keyboard.press("Delete")
        page.wait_for_timeout(700)
        data = self.canvas_data(page)
        self.assertFalse(any(n["id"] == card["id"] for n in data["nodes"]))
        self.assertFalse(any(card["id"] in (e["fromNode"], e["toNode"]) for e in data["edges"]))
        # Undo brings it back; unknown fields Obsidian may add survive every save.
        page.keyboard.press("Control+z")
        page.wait_for_timeout(700)
        self.assertTrue(any(n["id"] == card["id"] for n in self.canvas_data(page)["nodes"]))
        self.assertEqual(len(before["edges"]) + 1, len(self.canvas_data(page)["edges"]))
        self.assertEqual(self.errors, [])

    def test_new_canvas_add_note_and_unknown_fields_survive(self):
        page = self.open()
        page.click('#nav [data-go="canvas"]')
        page.click("[data-cv-new]")
        page.fill(".modal input", "Ideas wall")
        page.press(".modal input", "Enter")
        page.wait_for_selector(".cv-wrap")
        path = "canvases/Ideas wall.canvas"
        self.assertEqual(self.canvas_data(page, path), {"nodes": [], "edges": []})
        page.click('[data-cv="add-note"]')
        page.fill(".palette input", "ideas/offline-maps.md")
        page.press(".palette input", "Enter")
        page.wait_for_timeout(800)
        nodes = self.canvas_data(page, path)["nodes"]
        self.assertEqual([n["file"] for n in nodes], ["ideas/offline-maps.md"])
        # A field Cairn doesn't know (written by a future Obsidian) is kept on save.
        page.evaluate("""(p) => {
          const file = window.cairn.state.files.find((f) => f.path === p);
          const data = JSON.parse(file.text);
          data.nodes[0].styleAttributes = { shape: "pill" };
          data.futureKey = 42;
          file.text = JSON.stringify(data, null, "\\t");
          window.cairn.rerender();
        }""", path)
        page.wait_for_selector(".cv-node")
        page.click('[data-cv="add-link"]')
        page.fill(".modal input", "example.com/trails")
        page.press(".modal input", "Enter")
        page.wait_for_timeout(800)
        data = self.canvas_data(page, path)
        self.assertEqual(data["futureKey"], 42)
        self.assertEqual(data["nodes"][0]["styleAttributes"], {"shape": "pill"})
        self.assertEqual(data["nodes"][1]["url"], "https://example.com/trails")
        self.assertEqual(self.errors, [])

    def test_canvas_is_read_only_in_someone_elses_brain(self):
        page = self.open()
        page.evaluate("() => { window.cairn.state.info.readOnly = true; }")
        self.open_canvas(page)
        self.assertEqual(page.locator(".cv-handle").count(), 0)
        self.assertEqual(page.locator('[data-cv="add-text"]').count(), 0)
        before = page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p).text", CANVAS)
        wrap = page.locator("#cv-wrap").bounding_box()
        page.mouse.dblclick(wrap["x"] + wrap["width"] - 100, wrap["y"] + wrap["height"] - 80)
        page.keyboard.press("Delete")
        page.wait_for_timeout(600)
        self.assertEqual(page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p).text", CANVAS), before)

    # ------------------------------------------------------------ tabs

    def test_tabs_open_switch_close_and_reopen(self):
        page = self.open()
        page.click('#nav [data-go="ideas"]')
        page.click('.item-card[data-open="ideas/offline-maps.md"]')
        page.wait_for_selector("#ed-body")
        self.assertEqual(page.locator(".tab-strip").count(), 0, "one note, no tab strip")
        page.keyboard.press("Escape")
        page.click('.item-card[data-open="ideas/share-trip-link.md"]', modifiers=["Control"])
        page.wait_for_selector(".tab-strip")
        tabs = lambda: page.eval_on_selector_all(".note-tab", "els => els.map((e) => e.dataset.tab)")  # noqa: E731
        self.assertEqual(tabs(), ["ideas/offline-maps.md", "ideas/share-trip-link.md"])
        # A plain click on a link inside the note replaces the current tab.
        page.keyboard.press("Escape")
        page.click('.item-card[data-open="ideas/weather-overlay.md"]')
        page.wait_for_selector(".tab-strip")
        self.assertEqual(tabs(), ["ideas/offline-maps.md", "ideas/weather-overlay.md"])
        # Middle-click opens another one in a new tab.
        page.keyboard.press("Escape")
        page.click('.item-card[data-open="ideas/voice-notes-on-trail.md"]', button="middle")
        page.wait_for_selector('.note-tab[data-tab="ideas/voice-notes-on-trail.md"].on')
        self.assertEqual(len(tabs()), 3)
        page.keyboard.press("Control+Alt+ArrowLeft")
        page.wait_for_function("() => window.cairn.state.open === 'ideas/weather-overlay.md'")
        page.click('.note-tab[data-tab="ideas/offline-maps.md"]')
        page.wait_for_function("() => window.cairn.state.open === 'ideas/offline-maps.md'")
        page.click('[data-tab-close="ideas/offline-maps.md"]')
        page.wait_for_function("() => window.cairn.state.open === 'ideas/weather-overlay.md'")
        self.assertEqual(tabs(), ["ideas/weather-overlay.md", "ideas/voice-notes-on-trail.md"])
        page.keyboard.press("Control+Alt+t")
        page.wait_for_function("() => window.cairn.state.open === 'ideas/offline-maps.md'")
        self.assertEqual(len(tabs()), 3)
        # Tabs are remembered after a reload and survive a rename.
        page.reload()
        page.wait_for_selector("#nav button")
        page.click('#nav [data-go="ideas"]')
        page.click('.item-card[data-open="ideas/weather-overlay.md"]')
        page.wait_for_selector(".tab-strip")
        self.assertEqual(len(tabs()), 3)
        page.click('[data-action="rename"]')
        page.fill(".modal input", "ideas/weather-layer")
        page.press(".modal input", "Enter")
        page.wait_for_selector('.note-tab[data-tab="ideas/weather-layer.md"].on')
        self.assertNotIn("ideas/weather-overlay.md", tabs())
        self.assertEqual(len(tabs()), 3)
        self.assertEqual(self.errors, [])

    def test_deleting_a_note_closes_its_tab(self):
        page = self.open()
        page.click('#nav [data-go="ideas"]')
        page.click('.item-card[data-open="ideas/offline-maps.md"]')
        page.wait_for_selector("#ed-body")
        page.keyboard.press("Escape")
        page.click('.item-card[data-open="ideas/ar-peak-finder.md"]', modifiers=["Control"])
        page.wait_for_selector(".tab-strip")
        page.click('[data-action="delete"]')
        page.click(".modal button[type=submit]")
        page.wait_for_function("() => !window.cairn.state.files.some((f) => f.path === 'ideas/ar-peak-finder.md')")
        self.assertEqual(page.evaluate("() => window.cairn.state.tabs"), ["ideas/offline-maps.md"])

    # ------------------------------------------------------------ plugins

    def test_plugins_are_off_until_turned_on_and_bundled_ones_work(self):
        page = self.open()
        page.click('#nav [data-go="plugins"]')
        self.assertIn("Community plugins are off", page.inner_text("#view"))
        self.assertEqual(page.locator("[data-plugin-install]").count(), 0)
        page.click('[data-plugins-toggle="on"]')
        page.wait_for_selector('[data-plugin-install="plugins/note-queries.js"]')
        for plugin in BUNDLED:
            page.click(f'[data-plugin-install="plugins/{plugin}.js"]')
            page.wait_for_selector(f'.plugin-row[data-plugin="{plugin}"]')
        self.assertEqual(page.eval_on_selector_all(".plugin-row", "els => els.map((e) => e.dataset.plugin)"), BUNDLED)
        self.assertEqual(page.locator(".plugin-error").count(), 0)
        # Views: calendar and stats appear in the sidebar and render.
        page.click('#nav [data-go="plugin:calendar:calendar"]')
        page.wait_for_selector('.cal-cell.has[data-cal-day="2026-11-04"]')
        page.click('#nav [data-go="plugin:writing-stats:stats"]')
        page.wait_for_selector(".ws-tiles")
        self.assertIn("18", page.inner_text(".ws-tiles"))
        # Code blocks: a ```query block in a note becomes a live table.
        page.click('#nav [data-go="notes"]')
        page.click('#view [data-open="notes/architecture.md"]')
        page.fill("#ed-body", "```query\ntype: task\nstatus: todo, doing\nsort: title asc\nshow: table\n```\n")
        page.click('[data-editor-mode="preview"]')
        page.wait_for_selector(".nq-table")
        titles = page.eval_on_selector_all(".nq-table tbody a", "els => els.map((e) => e.textContent)")
        self.assertEqual(titles, sorted(titles))
        self.assertEqual(len(titles), 4)
        page.keyboard.press("Escape")
        # Commands show up in the palette; events reach plugins (recent files).
        page.keyboard.press("Control+k")
        page.fill(".palette input", "Word count of this note")
        self.assertEqual(self.palette_labels(page).count("Word count of this note"), 1)
        page.keyboard.press("Escape")
        page.click('#nav [data-go="plugin:recent-files:recent"]')
        page.wait_for_selector('.rf-list [data-open="notes/architecture.md"]')
        # Everything comes back after a reload, and turning plugins off removes their views.
        page.reload()
        page.wait_for_selector('#nav [data-go="plugin:calendar:calendar"]')
        page.click('#nav [data-go="plugins"]')
        page.click('[data-plugins-toggle="off"]')
        page.wait_for_selector('[data-plugins-toggle="on"]')
        self.assertEqual(page.locator('#nav [data-go^="plugin:"]').count(), 0)
        self.assertEqual(self.errors, [])

    def test_disable_remove_and_install_from_a_link(self):
        page = self.open()
        # A plugin from "GitHub": served by the mock at raw.githubusercontent.com/<owner>/<repo>/HEAD/main.js.
        code = (
            'export const manifest = { id: "hello-world", name: "Hello world", version: "0.1.0" };\n'
            "export default function activate(cairn) {\n"
            '  cairn.addCommand({ id: "hi", name: "Say hello from a plugin", run: () => cairn.toast("Hello!") });\n'
            '  cairn.registerCodeBlock("shout", (source, el) => { el.textContent = source.toUpperCase(); });\n'
            "}\n"
        )
        page.context.route(
            "https://raw.githubusercontent.com/someone/cairn-hello/HEAD/main.js",
            lambda route: route.fulfill(status=200, content_type="text/javascript", body=code, headers={"Access-Control-Allow-Origin": "*"}),
        )
        page.click('#nav [data-go="plugins"]')
        page.click('[data-plugins-toggle="on"]')
        page.fill("#plugin-install-form input", "someone/cairn-hello")
        page.press("#plugin-install-form input", "Enter")
        page.click(".modal button[type=submit]")
        page.wait_for_selector('.plugin-row[data-plugin="hello-world"]')
        self.assertIn("someone/cairn-hello", page.inner_text('.plugin-row[data-plugin="hello-world"]'))
        page.keyboard.press("Control+k")
        page.fill(".palette input", "Say hello from a plugin")
        self.assertEqual(self.palette_labels(page).count("Say hello from a plugin"), 1)
        page.keyboard.press("Escape")
        # Turning it off removes its command; removing it deletes it.
        page.uncheck('[data-plugin-enable="hello-world"]')
        page.wait_for_function("() => !JSON.parse(localStorage.getItem('cairn.plugins.v1')).installed['hello-world'].enabled")
        page.keyboard.press("Control+k")
        page.fill(".palette input", "Say hello from a plugin")
        self.assertNotIn("Say hello from a plugin", self.palette_labels(page))
        page.keyboard.press("Escape")
        page.click('[data-plugin-remove="hello-world"]')
        page.click(".modal button[type=submit]")
        page.wait_for_function("() => !JSON.parse(localStorage.getItem('cairn.plugins.v1')).installed['hello-world']")
        # A file that isn't a plugin is refused with a clear message.
        page.context.route(
            "https://example.com/not-a-plugin.js",
            lambda route: route.fulfill(status=200, content_type="text/javascript", body="export const x = 1;", headers={"Access-Control-Allow-Origin": "*"}),
        )
        page.fill("#plugin-install-form input", "https://example.com/not-a-plugin.js")
        page.press("#plugin-install-form input", "Enter")
        page.click(".modal button[type=submit]")
        page.wait_for_selector(".toast.error")
        self.assertIn("manifest", page.inner_text(".toast.error"))

    def test_plugin_api_validates_manifests(self):
        page = self.open()
        result = page.evaluate("""async () => {
          const plugins = await import('./plugins.js');
          return {
            ok: plugins.validManifest({ id: 'good-one', name: 'Good', version: '1.0.0' }),
            badId: plugins.validManifest({ id: 'Bad Id', name: 'x', version: '1' }),
            noName: plugins.validManifest({ id: 'abc', version: '1' }),
            future: plugins.validManifest({ id: 'abc', name: 'x', version: '1', minApi: 99 }),
            repo: plugins.pluginSource('juw12/cairn-plugin'),
            gh: plugins.pluginSource('https://github.com/juw12/cairn-plugin'),
            url: plugins.pluginSource('https://cdn.example.com/p.js'),
            http: plugins.pluginSource('http://insecure.example.com/p.js'),
            bundled: plugins.pluginSource('plugins/calendar.js'),
          };
        }""")
        self.assertEqual(result["ok"], "")
        self.assertIn("lowercase", result["badId"])
        self.assertIn("name", result["noName"])
        self.assertIn("newer Cairn", result["future"])
        self.assertEqual(result["repo"]["url"], "https://raw.githubusercontent.com/juw12/cairn-plugin/HEAD/main.js")
        self.assertEqual(result["gh"]["url"], result["repo"]["url"])
        self.assertEqual(result["url"]["url"], "https://cdn.example.com/p.js")
        self.assertIsNone(result["http"])
        self.assertEqual(result["bundled"]["url"], "plugins/calendar.js")

    def test_canvas_parsing_rules(self):
        page = self.open()
        result = page.evaluate("""async () => {
          const c = await import('./canvas.js');
          return {
            empty: c.parseCanvas(''),
            obsidianEmpty: c.parseCanvas('{}'),
            broken: c.parseCanvas('{nope'),
            array: c.parseCanvas('[]'),
            dangling: c.parseCanvas(JSON.stringify({ nodes: [{ id: 'a', x: 1, y: 2, width: 10, height: 10 }], edges: [{ id: 'e', fromNode: 'a', toNode: 'gone' }] })),
            colors: [c.colorOf('1'), c.colorOf('#abc'), c.colorOf('red'), c.colorOf('')],
            serialized: c.serializeCanvas({ nodes: [], edges: [] }),
            side: c.nearestSide({ x: 0, y: 0, width: 100, height: 50 }, 120, 20),
          };
        }""")
        self.assertEqual(result["empty"], {"nodes": [], "edges": []})
        self.assertEqual(result["obsidianEmpty"], {"nodes": [], "edges": []})
        self.assertIsNone(result["broken"])
        self.assertIsNone(result["array"])
        self.assertEqual(result["dangling"]["edges"], [])
        self.assertEqual(result["colors"][1:], ["#abc", None, None])
        self.assertTrue(result["colors"][0].startswith("#"))
        self.assertEqual(result["serialized"], '{\n\t"nodes": [],\n\t"edges": []\n}')
        self.assertEqual(result["side"], "right")


if __name__ == "__main__":
    unittest.main()

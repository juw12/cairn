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

    # ------------------------------------------------------------ canvas: selection, clipboard, drop, references

    def selected_ids(self, page):
        return page.eval_on_selector_all("#cv-world [data-node].selected", "els => els.map((e) => e.dataset.node).sort()")

    def test_marquee_selects_and_duplicate_copies_with_arrows(self):
        page = self.open()
        self.open_canvas(page)
        wrap = page.locator("#cv-wrap").bounding_box()
        goal = page.locator('[data-node="t-goal"]').bounding_box()
        why = page.locator('[data-node="f-idea"]').bounding_box()
        # Shift + drag from empty board across two cards selects both (and nothing is moved or panned).
        view_before = page.evaluate("() => document.querySelector('#cv-world').style.transform")
        page.keyboard.down("Shift")
        page.mouse.move(wrap["x"] + 8, wrap["y"] + 8)
        page.mouse.down()
        page.mouse.move(goal["x"] + 20, why["y"] + 20, steps=6)
        page.mouse.up()
        page.keyboard.up("Shift")
        chosen = self.selected_ids(page)
        self.assertTrue({"t-goal", "f-idea"} <= set(chosen), chosen)
        self.assertNotIn("g-beta", chosen)  # a group joins only when the box holds all of it
        self.assertEqual(page.evaluate("() => document.querySelector('#cv-world').style.transform"), view_before)
        self.assertIn(f"{len(chosen)} cards", page.text_content("#cv-select-bar"))
        before = self.canvas_data(page)
        page.keyboard.press("Control+d")
        page.wait_for_timeout(700)
        after = self.canvas_data(page)
        self.assertEqual(len(after["nodes"]), len(before["nodes"]) + len(chosen))
        copies = [n for n in after["nodes"] if n["id"] not in {m["id"] for m in before["nodes"]}]
        originals = {n["id"]: n for n in before["nodes"]}
        self.assertEqual(sorted(c["x"] for c in copies), sorted(originals[i]["x"] + 40 for i in chosen))
        self.assertEqual(sorted(self.selected_ids(page)), sorted(c["id"] for c in copies))
        # Undo removes the copies in one step.
        page.keyboard.press("Control+z")
        page.wait_for_timeout(700)
        self.assertEqual(len(self.canvas_data(page)["nodes"]), len(before["nodes"]))
        self.assertEqual(self.errors, [])

    def test_copy_paste_cards_text_and_links(self):
        page = self.open()
        self.open_canvas(page)
        group_id = "g-beta"
        page.click('[data-node="t-goal"]', position={"x": 30, "y": 12})
        # Copy puts the selection on the clipboard as JSON Canvas text (pasteable into Obsidian).
        copied = page.evaluate("""() => {
          const data = new DataTransfer();
          document.dispatchEvent(new ClipboardEvent("copy", { clipboardData: data, bubbles: true, cancelable: true }));
          return data.getData("text/plain");
        }""")
        fragment = json.loads(copied)
        self.assertEqual([n["id"] for n in fragment["nodes"]], ["t-goal"])
        self.assertEqual(fragment["edges"], [])
        paste = """(text) => {
          const data = new DataTransfer();
          data.setData("text/plain", text);
          document.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
        }"""
        before = len(self.canvas_data(page)["nodes"])
        page.evaluate(paste, copied)
        page.wait_for_timeout(700)
        data = self.canvas_data(page)
        self.assertEqual(len(data["nodes"]), before + 1)
        pasted = data["nodes"][-1]
        self.assertNotEqual(pasted["id"], "t-goal")
        self.assertEqual(pasted["text"], fragment["nodes"][0]["text"])
        # A web address becomes a link card, other text a text card, [[a note]] a note card.
        page.evaluate(paste, "https://example.com/maps")
        page.evaluate(paste, "Remember to test on a real trail")
        page.evaluate(paste, "[[user-interviews]]")
        page.wait_for_timeout(700)
        tail = self.canvas_data(page)["nodes"][-3:]
        self.assertEqual(tail[0]["type"], "link")
        self.assertEqual(tail[0]["url"], "https://example.com/maps")
        self.assertEqual(tail[1]["type"], "text")
        self.assertEqual(tail[1]["text"], "Remember to test on a real trail")
        self.assertEqual(tail[2]["type"], "file")
        self.assertEqual(tail[2]["file"], "notes/user-interviews.md")
        # Copying a group brings the cards inside it and the arrows between them.
        page.keyboard.press("Escape")  # clear the selection so its toolbar doesn't cover the group label
        page.click(f'[data-node="{group_id}"] .cv-group-label')
        self.assertEqual(self.selected_ids(page), [group_id])
        copied = page.evaluate("""() => {
          const data = new DataTransfer();
          document.dispatchEvent(new ClipboardEvent("copy", { clipboardData: data, bubbles: true, cancelable: true }));
          return data.getData("text/plain");
        }""")
        self.assertGreaterEqual(len(json.loads(copied)["nodes"]), 4)
        self.assertEqual(self.errors, [])

    def test_notes_panel_places_and_drops_notes(self):
        page = self.open()
        self.open_canvas(page)
        page.click('[data-cv="add-note"]')
        page.wait_for_selector("#cv-panel:not([hidden])")
        page.fill("#cv-panel-filter", "competitor")
        self.assertEqual(page.locator("#cv-panel-list [data-cv-place]").count(), 1)
        before = len(self.canvas_data(page)["nodes"])
        page.click('#cv-panel-list [data-cv-place="notes/competitor-scan.md"]')
        page.wait_for_timeout(700)
        nodes = self.canvas_data(page)["nodes"]
        self.assertEqual(len(nodes), before + 1)
        self.assertEqual(nodes[-1]["file"], "notes/competitor-scan.md")
        # Dropping a note (as the panel's drag does) puts it where it was dropped.
        wrap = page.locator("#cv-wrap").bounding_box()
        page.evaluate("""([x, y]) => {
          const data = new DataTransfer();
          data.setData("application/x-cairn-note", "notes/architecture.md");
          const target = document.querySelector("#cv-wrap");
          target.dispatchEvent(new DragEvent("dragover", { dataTransfer: data, clientX: x, clientY: y, bubbles: true, cancelable: true }));
          target.dispatchEvent(new DragEvent("drop", { dataTransfer: data, clientX: x, clientY: y, bubbles: true, cancelable: true }));
        }""", [wrap["x"] + 200, wrap["y"] + 150])
        page.wait_for_timeout(700)
        dropped = self.canvas_data(page)["nodes"][-1]
        self.assertEqual(dropped["file"], "notes/architecture.md")
        drop_world = page.evaluate("""([x, y]) => {
          const world = document.querySelector("#cv-world").style.transform.match(/translate\\(([-\\d.]+)px, ([-\\d.]+)px\\) scale\\(([-\\d.]+)\\)/);
          const rect = document.querySelector("#cv-wrap").getBoundingClientRect();
          return [(x - rect.left - Number(world[1])) / Number(world[3]), (y - rect.top - Number(world[2])) / Number(world[3])];
        }""", [wrap["x"] + 200, wrap["y"] + 150])
        self.assertLess(abs(dropped["x"] + dropped["width"] / 2 - drop_world[0]), 15)
        page.keyboard.press("Escape")
        self.assertEqual(self.errors, [])

    def test_canvases_count_as_backlinks_and_show_on_the_map(self):
        page = self.open()
        links = page.evaluate("""() => window.cairn.core.canvasLinks(
          [
            { path: "canvases/a.canvas", text: JSON.stringify({ nodes: [
              { id: "1", type: "file", file: "tasks/gpx-import.md" },
              { id: "2", type: "text", text: "see [[user-interviews]] and [[nowhere]]" },
              { id: "3", type: "file", file: "tasks/gpx-import.md" } ] }) },
            { path: "canvases/broken.canvas", text: "{ not json" },
          ],
          window.cairn.state.notes,
        )""")
        self.assertEqual(links, [
            {"canvas": "canvases/a.canvas", "note": "tasks/gpx-import.md"},
            {"canvas": "canvases/a.canvas", "note": "notes/user-interviews.md"},
        ])
        # The demo board holds tasks/gpx-import.md: its drawer lists the board under "On canvases".
        page.click('#nav [data-go="notes"]')
        page.click('#view [data-open="tasks/gpx-import.md"]')
        page.wait_for_selector(".on-canvases [data-open-canvas]")
        self.assertEqual(page.get_attribute(".on-canvases [data-open-canvas]", "data-open-canvas"), CANVAS)
        page.click(".on-canvases [data-open-canvas]")
        page.wait_for_selector(".cv-node")
        self.assertEqual(page.evaluate("() => window.cairn.state.canvas"), CANVAS)
        # The Map shows the board as a square joined to the notes it holds.
        page.click('#nav [data-go="map"]')
        page.wait_for_selector(f'.map-canvas[data-node="canvas:{CANVAS}"]')
        self.assertGreaterEqual(page.locator(".map-edge-canvas").count(), 3)
        self.assertEqual(self.errors, [])

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
        page.wait_for_selector("#cv-panel:not([hidden])")
        page.fill("#cv-panel-filter", "ideas/offline-maps.md")
        page.press("#cv-panel-filter", "Enter")
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

    def test_split_view_shows_two_notes_side_by_side(self):
        page = self.open()
        page.click('#nav [data-go="ideas"]')
        page.click('.item-card[data-open="ideas/offline-maps.md"]')
        page.wait_for_selector("#ed-body")
        side_title = page.evaluate("() => window.cairn.state.notes.find((n) => n.path === 'ideas/share-trip-link.md').title")
        # The columns button picks a note to open beside the current one.
        page.click("[data-split-toggle]")
        page.fill(".palette input", side_title)
        page.keyboard.press("Enter")
        page.wait_for_selector('.split-pane[data-split-path="ideas/share-trip-link.md"]')
        self.assertTrue(page.locator(".drawer.has-split").is_visible())
        self.assertTrue(page.locator("#ed-body").is_visible(), "the main note stays editable")
        main_box = page.locator(".drawer-panes > .drawer-body").bounding_box()
        side_box = page.locator(".split-pane").bounding_box()
        self.assertGreater(side_box["x"], main_box["x"] + main_box["width"] / 2, "side by side, not stacked")
        self.assertLess(abs(side_box["y"] - main_box["y"]), 80, "both panes start on the same row")
        # Both notes can be edited at once and each saves to its own file.
        page.click('[data-split-mode="write"]')
        page.fill("#split-body", "Written in the side pane.")
        page.fill("#ed-body", "Written in the main pane.")
        page.wait_for_function("""() => {
            const text = (p) => window.cairn.state.files.find((f) => f.path === p).text;
            return text('ideas/share-trip-link.md').includes('Written in the side pane.')
                && text('ideas/offline-maps.md').includes('Written in the main pane.');
        }""")
        # Swap sides, then the layout is remembered after a reload.
        page.click(".split-title")
        page.wait_for_function("() => window.cairn.state.open === 'ideas/share-trip-link.md' && window.cairn.state.split === 'ideas/offline-maps.md'")
        page.reload()
        page.wait_for_selector("#nav button")
        page.click('#nav [data-go="ideas"]')
        page.click('.item-card[data-open="ideas/share-trip-link.md"]')
        page.wait_for_selector('.split-pane[data-split-path="ideas/offline-maps.md"]')
        # Close it; Ctrl+Alt+\\ opens the picker again.
        page.click("[data-split-close]")
        page.wait_for_selector(".split-pane", state="detached")
        self.assertIsNone(page.evaluate("() => window.cairn.state.split"))
        page.keyboard.press("Control+Alt+Backslash")
        page.wait_for_selector(".palette input")
        page.keyboard.press("Escape")
        page.wait_for_selector(".palette-wrap", state="detached")
        # Alt+click on a tab opens that tab beside the current note.
        page.keyboard.press("Escape")
        page.wait_for_selector("#drawer-layer", state="detached")
        page.click('.item-card[data-open="ideas/weather-overlay.md"]', modifiers=["Control"])
        page.wait_for_selector('.note-tab[data-tab="ideas/weather-overlay.md"].on')
        page.click('.note-tab[data-tab="ideas/share-trip-link.md"]', modifiers=["Alt"])
        page.wait_for_selector('.split-pane[data-split-path="ideas/share-trip-link.md"]')
        self.assertTrue(page.locator('.note-tab[data-tab="ideas/share-trip-link.md"].beside').count() == 1)
        # On a phone the two notes stack and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(200)
        self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
        main_box = page.locator(".drawer-panes > .drawer-body").bounding_box()
        side_box = page.locator(".split-pane").bounding_box()
        self.assertGreaterEqual(side_box["y"], main_box["y"] + main_box["height"] - 2, "stacked on a phone")
        self.assertEqual(self.errors, [])

    def test_question_mark_shows_every_shortcut(self):
        page = self.open()
        page.keyboard.press("Shift+Slash")
        page.wait_for_selector(".modal.shortcuts")
        sheet = page.text_content(".modal.shortcuts")
        for heading in ("Everywhere", "Tabs", "Split view", "Writing", "Canvas"):
            self.assertIn(heading, sheet)
        self.assertIn("Duplicate the selection", sheet)
        self.assertGreater(page.locator(".modal.shortcuts kbd").count(), 30)
        # Escape closes only the sheet; the palette command opens it again.
        page.keyboard.press("Escape")
        page.wait_for_selector(".modal-wrap", state="detached")
        page.keyboard.press("Control+k")
        page.fill(".palette input", "keyboard shortcuts")
        page.keyboard.press("Enter")
        page.wait_for_selector(".modal.shortcuts")
        page.click(".modal.shortcuts [data-close]")
        page.wait_for_selector(".modal-wrap", state="detached")
        # Typing a ? in a text field is just a question mark.
        page.focus("#capture-input")
        page.keyboard.type("why?")
        self.assertEqual(page.locator(".modal.shortcuts").count(), 0)
        self.assertEqual(self.errors, [])

    def test_canvas_opens_readable_on_a_phone(self):
        page = self.open(viewport=(390, 844))
        page.evaluate("() => document.querySelector('#nav [data-go=\"canvas\"]').click()")
        page.click(f'.cv-card[data-cv-open="{CANVAS}"]')
        page.wait_for_selector(".cv-node")
        page.wait_for_function("() => /scale\\(/.test(document.querySelector('#cv-world').style.transform)")
        page.wait_for_timeout(150)
        zoom = page.evaluate("() => parseFloat(document.querySelector('#cv-world').style.transform.match(/scale\\(([\\d.]+)\\)/)[1])")
        self.assertGreaterEqual(zoom, 0.5, "cards are readable on first open, not shrunk to fit")
        # The first card sits in view near the top-left; Fit still shows the whole board.
        wrap = page.locator("#cv-wrap").bounding_box()
        boxes = page.eval_on_selector_all(".cv-node", "els => els.map((e) => e.getBoundingClientRect()).map((r) => [r.left, r.top])")
        self.assertTrue(any(wrap["x"] <= x < wrap["x"] + wrap["width"] and wrap["y"] <= y < wrap["y"] + wrap["height"] for x, y in boxes))
        page.click('[data-cv="fit"]')
        page.wait_for_timeout(350)
        fitted = page.evaluate("() => parseFloat(document.querySelector('#cv-world').style.transform.match(/scale\\(([\\d.]+)\\)/)[1])")
        self.assertLess(fitted, zoom)
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

    def test_plugin_settings_problems_and_the_brains_plugin_list(self):
        page = self.open()
        page.click('#nav [data-go="plugins"]')
        page.click('[data-plugins-toggle="on"]')
        page.click('[data-plugin-install="plugins/writing-stats.js"]')
        page.wait_for_selector('.plugin-row[data-plugin="writing-stats"]')
        # A settings page drawn from the plugin's own description of its options.
        page.click('[data-plugin-settings="writing-stats"]')
        page.wait_for_selector('[data-plugin-form="writing-stats"]')
        self.assertEqual(page.input_value('[data-setting="wordsPerMinute"]'), "230")
        self.assertEqual(page.input_value('[data-setting="chartDays"]'), "30")
        page.select_option('[data-setting="chartDays"]', "14")
        page.fill('[data-setting="wordsPerMinute"]', "100")
        page.press('[data-setting="wordsPerMinute"]', "Tab")
        stored = lambda key: page.evaluate("(k) => JSON.parse(localStorage.getItem('cairn.plugin.writing-stats.' + k))", key)  # noqa: E731
        self.assertEqual(stored("chartDays"), "14")
        self.assertEqual(stored("wordsPerMinute"), 100)
        page.click('#nav [data-go="plugin:writing-stats:stats"]')
        page.wait_for_selector(".ws-tiles")
        self.assertIn("last 14 days", page.inner_text("#view"))
        page.click('#nav [data-go="plugins"]')
        # The settings stay open while you look at the result.
        page.wait_for_selector('[data-plugin-form="writing-stats"]')
        self.assertEqual(page.get_attribute('[data-plugin-settings="writing-stats"]', "aria-expanded"), "true")
        page.click('[data-plugin-settings-reset="writing-stats"]')
        page.wait_for_function("() => JSON.parse(localStorage.getItem('cairn.plugin.writing-stats.chartDays')) === '30'")
        # A plugin that fails while running is marked on its row until it is restarted.
        page.evaluate("""async () => {
            const plugins = await import('./plugins.js');
            const code = 'export const manifest = { id: "broken", name: "Broken", version: "1.0.0" };'
              + 'export default function (cairn) { cairn.addCommand({ id: "boom", name: "Explode now", run() { throw new Error("kaboom"); } }); }';
            const url = URL.createObjectURL(new Blob([code], { type: 'text/javascript' }));
            await plugins.installPlugin({ url, label: 'a test link', spec: 'https://example.com/broken.js' }, { quiet: true });
        }""")
        page.keyboard.press("Control+k")
        page.fill(".palette input", "Explode now")
        page.keyboard.press("Enter")
        page.click('#nav [data-go="home"]')
        page.click('#nav [data-go="plugins"]')
        page.wait_for_selector('.plugin-row.has-error[data-plugin="broken"]')
        self.assertIn("kaboom", page.inner_text('.plugin-row[data-plugin="broken"]'))
        self.assertIn("1 with a problem", page.inner_text("#view"))
        page.click('[data-plugin-restart="broken"]')
        page.wait_for_selector('.plugin-row[data-plugin="broken"]:not(.has-error)')
        # The plugin list can travel with the brain: a note naming where each plugin comes from.
        page.click("[data-plugins-save-list]")
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'cairn/plugins.md')")
        listed = page.evaluate("() => window.cairn.state.notes.find((n) => n.path === 'cairn/plugins.md').meta.plugins")
        self.assertEqual(sorted(listed), ["https://example.com/broken.js", "plugins/writing-stats.js"])
        text = page.evaluate("() => window.cairn.state.files.find((f) => f.path === 'cairn/plugins.md').text")
        self.assertNotIn("export default", text, "only sources travel, never code")
        # Another device added the calendar to the list: this one offers to install it.
        page.evaluate("""async () => {
            const { saveNote } = await import('./state.js');
            await saveNote('cairn/plugins.md', { properties: { plugins: ['plugins/writing-stats.js', 'https://example.com/broken.js', 'plugins/calendar.js'] } });
        }""")
        page.click('#nav [data-go="home"]')
        page.click('#nav [data-go="plugins"]')
        page.wait_for_selector(".plugin-brain-missing")
        self.assertIn("plugins/calendar.js", page.inner_text(".plugin-brain-missing"))
        page.click("[data-plugins-install-list]")
        page.click(".modal button[type=submit]")
        page.wait_for_selector('.plugin-row[data-plugin="calendar"]')
        page.wait_for_selector(".plugin-brain-missing", state="detached")
        self.assertTrue(page.locator("[data-plugins-save-list]").is_disabled(), "list and device agree")
        # Removing a plugin keeps the brain's list in step.
        page.click('[data-plugin-remove="broken"]')
        page.click(".modal button[type=submit]")
        page.wait_for_function("""() => {
            const note = window.cairn.state.notes.find((n) => n.path === 'cairn/plugins.md');
            return note && !note.meta.plugins.includes('https://example.com/broken.js');
        }""")
        self.assertEqual([e for e in self.errors if "kaboom" not in e], [])

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

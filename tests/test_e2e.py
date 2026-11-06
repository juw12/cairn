"""Gates B2 and B5: the real web app in a headless browser (demo mode, GitHub Pages runtime)."""

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

VIEWS = ["home", "ideas", "board", "decisions", "notes", "journal", "map", "code", "context", "people"]


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

    def open(self, query="?demo=1", viewport=(1440, 900), theme="light", wait="#nav button"):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        self.github_log = install_github_mock(context)
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
        for theme, viewport in (("light", (1440, 900)), ("dark", (1024, 768)), ("light", (390, 844))):
            page = self.open(query="?demo=1#/map", viewport=viewport, theme=theme, wait="#map-canvas")
            page.evaluate("window.cairn.map.settle()")
            labels = page.evaluate("window.cairn.map.labels()")
            self.assertGreater(len(labels), 5, f"{theme}: hardly any labels drawn")
            overlaps = [
                (a["title"], b["title"])
                for i, a in enumerate(labels)
                for b in labels[i + 1:]
                if a["box"][0] < b["box"][2] and b["box"][0] < a["box"][2] and a["box"][1] < b["box"][3] and b["box"][1] < a["box"][3]
            ]
            self.assertEqual(overlaps, [], f"{theme}: overlapping map labels")
            self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ map interactions

    def map_page(self, query="?demo=1#/map"):
        page = self.open(query=query, wait="#map-canvas")
        page.evaluate("window.cairn.map.reset(); window.cairn.rerender()")
        page.wait_for_selector("#map-canvas")
        page.evaluate("window.cairn.map.settle()")
        return page

    def point(self, page, node_id):
        spot = page.evaluate("(id) => window.cairn.map.screen(id)", node_id)
        self.assertIsNotNone(spot, f"{node_id} is not on the map")
        return spot["x"], spot["y"]

    def test_map_select_path_and_local_graph(self):
        page = self.map_page()
        self.assertEqual(page.locator(".map-node").count(), 18, "every note stays reachable by keyboard")
        page.mouse.click(*self.point(page, "ideas/share-trip-link.md"))
        self.assertEqual(page.evaluate("window.cairn.map.selected()"), "ideas/share-trip-link.md")
        self.assertIn("Share a live trip link", page.inner_text("#map-inspector"))
        # Shift-click a note two hops away: the inspector shows the path through the shared neighbour.
        page.keyboard.down("Shift")
        page.mouse.click(*self.point(page, "notes/architecture.md"))
        page.keyboard.up("Shift")
        path = page.evaluate("window.cairn.map.path()")
        self.assertEqual(path[0], "ideas/share-trip-link.md")
        self.assertEqual(path[-1], "notes/architecture.md")
        self.assertGreaterEqual(len(path), 3)
        self.assertIn("path", page.inner_text("#map-inspector").lower())
        # Local graph keeps only the selected note and its neighbours.
        page.mouse.click(*self.point(page, "notes/user-interviews.md"))
        page.click('[data-map-toggle="local"]')
        page.wait_for_selector("#map-canvas")
        visible = page.evaluate("window.cairn.map.nodes().map((n) => n.id)")
        self.assertIn("notes/user-interviews.md", visible)
        self.assertLess(len(visible), 8)
        page.click('[data-map-toggle="local"]')
        # Search highlights and Enter jumps to the match.
        page.fill("#map-search", "pmtiles")
        page.press("#map-search", "Enter")
        self.assertEqual(page.evaluate("window.cairn.map.selected()"), "decisions/0001-use-pmtiles-for-offline-maps.md")
        page.dblclick("#map-canvas", position={"x": 5, "y": 400})  # empty space zooms, never errors
        self.assertEqual(self.errors, [])

    def test_map_alt_drag_writes_a_link_and_ghosts_become_notes(self):
        page = self.map_page()
        source, target = "tasks/dark-mode.md", "notes/design-principles.md"
        nodes = page.evaluate("window.cairn.map.nodes().map((n) => n.id)")
        if target not in nodes:
            target = "notes/architecture.md"
        page.keyboard.down("Alt")
        x, y = self.point(page, source)
        page.mouse.move(x, y)
        page.mouse.down()
        tx, ty = self.point(page, target)
        page.mouse.move((x + tx) / 2, (y + ty) / 2, steps=4)
        page.mouse.move(tx, ty, steps=4)
        page.mouse.up()
        page.keyboard.up("Alt")
        slug = target.split("/")[-1][:-3]
        page.wait_for_function("([p, s]) => window.cairn.state.files.find((f) => f.path === p).text.includes(`[[${s}]]`)", arg=[source, slug])
        # A [[link]] to a missing note shows as a hollow "unwritten" node; double-click writes it.
        page.evaluate(r"""async () => {
            const state = window.cairn.state;
            const f = state.files.find((x) => x.path === 'tasks/dark-mode.md');
            const { mtime } = await state.store.write(f.path, f.text + '\nSee [[Night sky palette]].\n', f.mtime);
            f.text += '\nSee [[Night sky palette]].\n';
            f.mtime = mtime;
            window.cairn.state.notes = state.files.filter((x) => !['CONTEXT.md', 'README.md'].includes(x.path)).map((x) => window.cairn.core.noteFromFile(x.path, x.text, x.mtime));
            window.cairn.rerender();
        }""")
        page.wait_for_selector("#map-canvas")
        page.evaluate("window.cairn.map.settle()")
        self.assertIn("ghost:night-sky-palette", page.evaluate("window.cairn.map.nodes().map((n) => n.id)"))
        page.mouse.dblclick(*self.point(page, "ghost:night-sky-palette"))
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'notes/night-sky-palette.md')")
        page.wait_for_function("() => window.cairn.map.nodes().some((n) => n.id === 'notes/night-sky-palette.md')")
        self.assertEqual(self.errors, [])

    def test_map_drag_pins_and_controls_work(self):
        page = self.map_page()
        x, y = self.point(page, "notes/competitor-scan.md")
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 60, y + 40, steps=6)
        page.mouse.up()
        pinned = {n["id"]: n["pinned"] for n in page.evaluate("window.cairn.map.nodes()")}
        self.assertTrue(pinned["notes/competitor-scan.md"])
        for control in ("zoom-in", "zoom-out", "fit", "play", "settings"):
            page.click(f'[data-map="{control}"]')
        page.click('[data-map="unpin"]')
        self.assertFalse(any(n["pinned"] for n in page.evaluate("window.cairn.map.nodes()")))
        for colour in ("folder", "cluster", "status", "type"):
            page.select_option("#map-color", colour)
            page.wait_for_selector("#map-legend .legend-item")
        page.click('[data-map-toggle="tags"]')
        page.wait_for_function("() => window.cairn.map.nodes().some((n) => n.kind === 'tag')")
        page.click('#map-legend [data-map-type="task"]')
        page.wait_for_function("() => !window.cairn.map.nodes().some((n) => n.id.startsWith('tasks/'))")
        page.wait_for_timeout(300)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ Obsidian sync in the browser

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

    def test_unknown_user_and_bad_input(self):
        page = self.open()
        page.click('#nav [data-go="people"]')
        page.fill("#people-query", "nobody-here-123")
        page.click("#people-form button[type=submit]")
        page.wait_for_selector(".notice")
        self.assertIn("Not found", page.inner_text("#view"))
        page.fill("#people-query", "not a user!")
        page.click("#people-form button[type=submit]")
        page.wait_for_function("() => document.querySelector('#view').innerText.includes(\"doesn't look like\")")

    def test_typing_survives_background_render(self):
        page = self.open()
        page.click('#nav [data-go="people"]')
        page.fill("#people-query", "juw12")
        page.evaluate("window.cairn.rerender()")
        self.assertEqual(page.input_value("#people-query"), "juw12")
        self.assertEqual(self.errors, [])

    def test_read_only_brain(self):
        page = self.open("?brain=juw12/cairn")
        page.wait_for_selector(".ro-banner")
        self.assertTrue(page.locator("#capture-form").is_hidden())
        self.assertEqual(page.evaluate("window.cairn.state.notes.length"), 18)
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

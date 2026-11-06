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
        # Every note has a place in space, and the layout really uses depth.
        bodies = page.evaluate("window.cairn.map3d.bodies()")
        self.assertEqual(len(bodies), 18)
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
        self.assertEqual(page.locator(".map-node").count(), 18)
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
        node.click()
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
        self.assertLess(page.locator(".map-node").count(), 18)
        page.locator("#map-svg .map-dot.selected").focus()
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('.map-chip.local')")
        self.assertEqual(page.locator(".map-node").count(), 18)
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
        page.wait_for_function("() => document.querySelectorAll('.map-node').length === 18")
        orphans = page.evaluate("""() => {
            const g = window.cairn.core.buildGraph(window.cairn.state.notes);
            const linked = new Set(g.edges.flatMap((e) => [e.source, e.target]));
            return g.nodes.filter((n) => !linked.has(n.id)).length;
        }""")
        page.uncheck('[data-map-setting="orphans"]')
        page.wait_for_function("(n) => document.querySelectorAll('.map-node').length === n", arg=18 - orphans)
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

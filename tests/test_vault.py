"""Obsidian-style features in the browser (Markdown flavour, rename, [[ suggestions, split view,
embeds, properties, folders, bookmarks, templates, the first-run tour) and /api/raw on the server."""

import http.client
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import install_github_mock, launch_browser, start_static_server  # noqa: E402

from cairn.brain import Brain  # noqa: E402
from cairn.server import make_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

VAULT = {
    "notes/alpha.md": "---\ntitle: Alpha\ntags: [hub]\ndue: 2026-12-01\n---\n# Alpha\n\nSee [[bravo-note]] and [[bravo-note#Part two|the second part]].\n\n![[bravo-note#Part two]]\n",
    "notes/bravo-note.md": "---\ntitle: Bravo Note\n---\n# Bravo\n\nIntro.\n\n## Part two\n\nSecond part text #release\n\n## Part three\n\nThird.\n",
    "notes/charlie.md": "---\ntitle: Charlie\n---\nCharlie writes about Bravo Note in plain text.\n",
    "Templates/Meeting.md": "---\ntitle: Meeting\nkind: meeting\n---\n# {{title}}\n\nDate: {{date}}\n\n- [ ] Agenda\n",
    "ideas/deep/nested-idea.md": "---\ntitle: Nested idea\ntype: idea\nstatus: raw\ntags: [release]\n---\nAn idea deep in folders.\n",
}

MARKDOWN = r"""# Top
Text with ==mark== and #topic/sub and `code #no` and [[Gone]].
Line two.

> [!tip]- Folded tip
> Inside **bold**
> - [ ] callout task

> [!warning] Heads up
> Careful.

- one
  - two
    - three
- [x] done task

Footnote here[^n].

[^n]: The footnote.

%% hidden %%

<script>alert(1)</script> [bad](javascript:alert(1)) ![x](https://example.com/a.png) ![[pic.png|120]]
"""


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class VaultFeatureTests(unittest.TestCase):
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

    def open(self, files=VAULT, viewport=(1440, 900), tour=False):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        install_github_mock(context)
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script("localStorage.setItem('cairn.theme', 'light')")
        if tour:
            page.add_init_script("window.CAIRN_TOUR = true")
        page.goto(self.url + "?empty=1")
        page.wait_for_selector("#nav button")
        if files is not None:
            page.evaluate(
                """async (files) => {
                    const stamp = Date.now();
                    const entries = Object.fromEntries(Object.entries(files).map(([p, t], i) => [p, { text: t, mtime: stamp + i }]));
                    window.cairn.state.store.reset(entries);
                    await window.cairn.reload();
                }""",
                files,
            )
        self.addCleanup(context.close)
        return page

    def text(self, page, path):
        return page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p)?.text ?? null", path)

    def open_note(self, page, path, mode="write"):
        page.evaluate("(m) => { window.cairn.state.editorMode = m; }", mode)
        page.evaluate("(p) => document.querySelector('#view').insertAdjacentHTML('beforeend', `<button id='opener' data-open='${p}'>o</button>`)", path)
        page.click("#opener")
        page.wait_for_selector(".drawer")

    # ------------------------------------------------------------ rendering

    def test_markdown_obsidian_flavour_and_safety(self):
        page = self.open(files=None)
        out = page.evaluate(
            """async (src) => {
                const md = await import('./md.js');
                const div = document.createElement('div');
                div.innerHTML = md.renderMarkdown(src, () => null);
                return {
                    html: div.innerHTML,
                    callouts: [...div.querySelectorAll('.callout')].map((c) => [c.dataset.callout, c.tagName, c.querySelector('.callout-title-text').textContent]),
                    marks: div.querySelectorAll('mark').length,
                    tags: [...div.querySelectorAll('[data-tag-link]')].map((a) => a.dataset.tagLink),
                    nestedDepth: div.querySelectorAll('ul ul ul').length,
                    tasks: [...div.querySelectorAll('input[data-task]')].map((b) => [b.dataset.task, b.checked]),
                    footnotes: div.querySelectorAll('.footnotes li').length,
                    scripts: div.querySelectorAll('script').length,
                    badHref: div.querySelector('a[href^="javascript"]') !== null,
                    assets: [...div.querySelectorAll('img[data-asset]')].map((i) => [i.dataset.asset, i.getAttribute('width')]),
                    external: div.querySelector('img[src="https://example.com/a.png"]') !== null,
                    missing: div.querySelector('[data-missing="Gone"]') !== null,
                    hidden: div.textContent.includes('hidden'),
                    breaks: div.querySelector('p br') !== null,
                    toggled: md.toggleTask(src, 0).includes('> - [x] callout task'),
                    heads: md.headings(src).map((h) => h.id),
                    words: md.wordCount('one two three'),
                };
            }""",
            MARKDOWN,
        )
        self.assertEqual(out["callouts"], [["tip", "DETAILS", "Folded tip"], ["warning", "DIV", "Heads up"]])
        self.assertEqual(out["marks"], 1)
        self.assertEqual(out["tags"], ["topic/sub"])
        self.assertEqual(out["nestedDepth"], 1)
        self.assertEqual(out["tasks"], [["0", False], ["1", True]])
        self.assertEqual(out["footnotes"], 1)
        self.assertEqual(out["scripts"], 0)
        self.assertFalse(out["badHref"])
        self.assertEqual(out["assets"], [["pic.png", "120"]])
        self.assertTrue(out["external"])
        self.assertTrue(out["missing"])
        self.assertFalse(out["hidden"])
        self.assertTrue(out["breaks"])
        self.assertTrue(out["toggled"])
        self.assertEqual(out["heads"], ["top"])
        self.assertEqual(out["words"], 3)
        self.assertIn("&lt;script&gt;", out["html"])

    def test_embeds_heading_links_outline_and_preview(self):
        page = self.open()
        self.open_note(page, "notes/alpha.md", mode="preview")
        embed = page.locator("#ed-preview .embed")
        self.assertEqual(embed.count(), 1)
        self.assertIn("Second part text", embed.inner_text())
        self.assertNotIn("Third", embed.inner_text())
        link = page.locator('#ed-preview a.wikilink[data-heading="part-two"]').first
        self.assertEqual(link.inner_text(), "the second part")
        link.click()
        page.wait_for_function("() => window.cairn.state.open === 'notes/bravo-note.md'")
        self.assertGreaterEqual(page.locator(".outline-item").count(), 3)
        self.assertEqual(self.errors, [])

    def test_missing_link_creates_the_note(self):
        page = self.open({"notes/a.md": "---\ntitle: A\n---\nLink to [[Brand New Idea]].\n"})
        self.open_note(page, "notes/a.md", mode="preview")
        page.click('[data-missing="Brand New Idea"]')
        page.wait_for_function("() => window.cairn.state.notes.some((n) => n.title === 'Brand New Idea')")
        page.wait_for_function("() => window.cairn.state.open && window.cairn.state.open !== 'notes/a.md'")
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ editing

    def test_link_suggestions_complete_a_wiki_link(self):
        page = self.open()
        self.open_note(page, "notes/charlie.md")
        page.click("#ed-body")
        page.keyboard.press("Control+End")
        page.keyboard.type("\nAlso [[brav")
        page.wait_for_selector(".link-suggest .suggest-item")
        self.assertIn("Bravo Note", page.inner_text(".link-suggest"))
        page.keyboard.press("Enter")
        self.assertEqual(page.locator(".link-suggest").count(), 0)
        self.assertIn("[[bravo-note|Bravo Note]]", page.input_value("#ed-body"))
        page.keyboard.type(" and [[bravo-note#Part")
        page.wait_for_selector(".link-suggest .suggest-item")
        page.keyboard.press("Enter")
        self.assertIn("[[bravo-note#Part two]]", page.input_value("#ed-body"))
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'notes/charlie.md').text.includes('[[bravo-note#Part two]]')")
        self.assertEqual(self.errors, [])

    def test_lists_continue_on_enter(self):
        page = self.open({"notes/l.md": "---\ntitle: L\n---\n- [ ] first"})
        self.open_note(page, "notes/l.md")
        page.click("#ed-body")
        page.keyboard.press("Control+End")
        page.keyboard.press("Enter")
        page.keyboard.type("second")
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")
        self.assertEqual(page.input_value("#ed-body"), "- [ ] first\n- [ ] second\n")

    def test_split_view_updates_live(self):
        page = self.open()
        self.open_note(page, "notes/charlie.md")
        page.click('[data-editor-mode="split"]')
        page.wait_for_selector(".drawer.wide .split-editor #ed-preview")
        page.fill("#ed-body", "Now ==highlighted== text")
        page.wait_for_selector("#ed-preview mark")
        self.assertIn("3 words", page.inner_text("#word-count"))
        self.assertEqual(self.errors, [])

    def test_properties_edit_and_remove(self):
        page = self.open()
        self.open_note(page, "notes/alpha.md")
        page.fill('[data-prop="due"]', "2027-01-02")
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'notes/alpha.md').text.includes('due: 2027-01-02')")
        page.click('[data-prop-remove="due"]')
        page.wait_for_function("() => !window.cairn.state.files.find((f) => f.path === 'notes/alpha.md').text.includes('due:')")
        text = self.text(page, "notes/alpha.md")
        self.assertIn("tags: [hub]", text)
        self.assertIn("title: Alpha", text)
        self.assertEqual(self.errors, [])

    def test_rename_moves_the_file_and_updates_every_link(self):
        page = self.open()
        self.open_note(page, "notes/bravo-note.md")
        page.click('[data-action="bookmark"]')
        page.click('[data-action="rename"]')
        page.fill(".modal input", "archive/bravo-renamed")
        page.click('.modal button[type="submit"]')
        page.wait_for_function("() => window.cairn.state.open === 'archive/bravo-renamed.md'")
        self.assertIsNone(self.text(page, "notes/bravo-note.md"))
        alpha = self.text(page, "notes/alpha.md")
        self.assertIn("[[bravo-renamed]]", alpha)
        self.assertIn("[[bravo-renamed#Part two|the second part]]", alpha)
        self.assertIn("![[bravo-renamed#Part two]]", alpha)
        self.assertNotIn("bravo-note", alpha)
        self.assertTrue(page.evaluate("JSON.stringify(Object.entries(localStorage)).includes('archive/bravo-renamed.md')"))
        self.assertEqual(self.errors, [])

    def test_unlinked_mention_gets_linked(self):
        page = self.open()
        self.open_note(page, "notes/bravo-note.md")
        page.click('details[data-panel="mentions"] > summary')
        page.wait_for_selector('[data-link-mention="notes/charlie.md"]')
        page.click('[data-link-mention="notes/charlie.md"]')
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'notes/charlie.md').text.includes('[[bravo-note|Bravo Note]]')")
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------ finding things

    def test_notes_search_operators_folders_and_bookmarks(self):
        page = self.open()
        page.click('#nav [data-go="notes"]')
        page.fill("#notes-search", "tag:release")
        page.wait_for_function("() => document.querySelectorAll('#notes-list .list-row').length === 2")
        page.fill("#notes-search", "tag:release -path:ideas")
        page.wait_for_function("() => document.querySelectorAll('#notes-list .list-row').length === 1")
        page.fill("#notes-search", '"plain text"')
        page.wait_for_function("() => document.querySelector('#notes-list').innerText.includes('Charlie')")
        page.fill("#notes-search", "")
        page.click('[data-notes-layout="folders"]')
        page.wait_for_selector('details[data-folder="ideas/deep"]')
        page.click('details[data-folder="ideas"] > summary')
        page.wait_for_function("() => window.cairn.state.closedFolders?.has('ideas')")
        page.evaluate("window.cairn.rerender()")
        self.assertFalse(page.locator('details[data-folder="ideas"]').evaluate("(d) => d.open"))
        self.open_note(page, "notes/charlie.md")
        page.click('[data-action="bookmark"]')
        page.keyboard.press("Escape")
        page.click('[data-filter="bookmarks"]')
        page.click('[data-notes-layout="list"]')
        self.assertEqual(page.locator("#notes-list .list-row").count(), 1)
        self.assertIn("Charlie", page.inner_text("#notes-list"))
        self.assertEqual(self.errors, [])

    def test_tag_chip_in_a_note_searches_the_tag(self):
        page = self.open()
        self.open_note(page, "notes/bravo-note.md", mode="preview")
        page.click('#ed-preview [data-tag-link="release"]')
        page.wait_for_function("() => window.cairn.state.view === 'notes' && !window.cairn.state.open")
        self.assertEqual(page.input_value("#notes-search"), "tag:release")

    def test_palette_templates_random_and_create(self):
        page = self.open()
        page.keyboard.press("Control+k")
        page.fill(".palette input", "new note from template")
        page.keyboard.press("Enter")
        page.wait_for_selector('.palette-item:has-text("Meeting")')
        page.keyboard.press("Enter")
        page.fill(".modal input", "Kickoff")
        page.click('.modal button[type="submit"]')
        page.wait_for_function("() => window.cairn.state.notes.some((n) => n.title === 'Kickoff')")
        note = page.evaluate("window.cairn.state.notes.find((n) => n.title === 'Kickoff')")
        self.assertIn("# Kickoff", note["body"])
        self.assertRegex(note["body"], r"Date: \d{4}-\d{2}-\d{2}")
        self.assertEqual(note["meta"]["kind"], "meeting")
        page.keyboard.press("Escape")
        page.keyboard.press("Control+k")
        page.fill(".palette input", "Something entirely new")
        self.assertIn("Create note", page.inner_text(".palette-list"))
        page.keyboard.press("Escape")
        page.keyboard.press("Control+k")
        page.fill(".palette input", "random note")
        page.keyboard.press("Enter")
        page.wait_for_selector(".drawer")
        self.assertEqual(self.errors, [])

    def test_hover_preview_shows_the_linked_note(self):
        page = self.open()
        self.open_note(page, "notes/alpha.md", mode="preview")
        page.hover('#ed-preview a.wikilink[data-open="notes/bravo-note.md"]:not([data-heading])')
        page.wait_for_selector(".hover-card")
        self.assertIn("Bravo", page.inner_text(".hover-card"))

    # ------------------------------------------------------------ first-run tour

    def test_update_waits_for_a_pause_then_reloads_in_place(self):
        page = self.open()
        page.evaluate("history.replaceState(null, '', location.pathname + '#/notes')")  # what a desktop page looks like
        self.open_note(page, "notes/alpha.md")
        page.evaluate("window.CAIRN_UPDATE_IDLE_MS = 60000; window.CAIRN_UPDATE_RETRY_MS = 200")
        page.keyboard.press("Shift")
        self.assertEqual(page.evaluate("window.cairn.applyUpdate()"), "waiting", "never reloads while you're busy")
        page.wait_for_timeout(500)
        self.assertEqual(page.locator(".drawer").count(), 1)
        with page.expect_navigation():
            page.evaluate("window.CAIRN_UPDATE_IDLE_MS = 0")  # the next retry finds you idle
        page.wait_for_selector(".toast >> text=Cairn updated")
        page.wait_for_selector(".drawer")
        self.assertIn("Alpha", page.inner_text(".drawer"))
        self.assertTrue(page.evaluate("location.hash").startswith("#/notes"))
        self.assertIsNone(page.evaluate("localStorage.getItem('cairn.updated')"))
        self.assertEqual(self.errors, [])

    def test_maths_and_diagrams_render_and_fall_back_to_source(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        install_github_mock(context)
        fake_katex = "window.katex = { renderToString: (tex, o) => `<span class=\"katex\">${o.displayMode ? 'D' : 'I'}:${tex.replace(/</g, '&lt;')}</span>` };"
        fake_mermaid = "window.mermaid = { initialize() {}, async render(id, src) { return { svg: `<svg id=\"${id}\" data-lines=\"${src.split('\\n').length}\"></svg>` }; } };"
        context.route("https://rich.test/katex.js", lambda route: route.fulfill(body=fake_katex, content_type="text/javascript"))
        context.route("https://rich.test/katex.css", lambda route: route.fulfill(body=".katex{}", content_type="text/css"))
        context.route("https://rich.test/mermaid.js", lambda route: route.fulfill(body=fake_mermaid, content_type="text/javascript"))
        context.route("https://rich.test/missing.js", lambda route: route.fulfill(status=404, body=""))
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("""
            localStorage.setItem('cairn.theme', 'light');
            window.CAIRN_RICH_SOURCES = {
              katex: { js: { url: 'https://rich.test/katex.js' }, css: { url: 'https://rich.test/katex.css' } },
              mermaid: { js: { url: 'https://rich.test/mermaid.js' } },
            };
        """)
        page.goto(self.url + "?empty=1")
        page.wait_for_selector("#nav button")
        note = "---\ntitle: Rich\n---\nEnergy $E=mc^2$ costs $5 and $6 today.\n\n$$\n\\int_0^1 x\\,dx\n$$\n\n```mermaid\ngraph TD\nA-->B\n```\n"
        page.evaluate("(text) => window.cairn.state.store.reset({'rich.md': {text, mtime: Date.now()}})", note)
        page.evaluate("() => window.cairn.reload()")
        page.evaluate("() => document.querySelector('#view').insertAdjacentHTML('beforeend', `<button id='opener' data-open='rich.md'>o</button>`)")
        page.evaluate("() => { window.cairn.state.editorMode = 'preview'; }")
        page.click("#opener")
        page.wait_for_selector(".drawer .math-block .katex")
        self.assertEqual(page.inner_text(".drawer .math-inline.rendered"), "I:E=mc^2")
        self.assertIn("D:", page.inner_text(".drawer .math-block"))
        self.assertIn("$5 and $6", page.inner_text(".drawer"), "money is not maths")
        page.wait_for_selector(".drawer .mermaid-diagram svg[data-lines='2']")
        self.assertEqual(page.locator(".drawer pre code.language-mermaid").count(), 0)
        # Offline: the renderer can't load, the source stays readable.
        page.evaluate("() => { window.CAIRN_RICH_SOURCES.katex.js.url = 'https://rich.test/missing.js'; }")
        offline = context.new_page()
        offline.add_init_script("""
            window.CAIRN_RICH_SOURCES = { katex: { js: { url: 'https://rich.test/missing.js' } }, mermaid: { js: { url: 'https://rich.test/missing.js' } } };
        """)
        offline.goto(self.url + "?empty=1")
        offline.wait_for_selector("#nav button")
        offline.evaluate("(text) => window.cairn.state.store.reset({'rich.md': {text, mtime: Date.now()}})", note)
        offline.evaluate("() => window.cairn.reload()")
        offline.evaluate("() => document.querySelector('#view').insertAdjacentHTML('beforeend', `<button id='opener' data-open='rich.md'>o</button>`)")
        offline.evaluate("() => { window.cairn.state.editorMode = 'preview'; }")
        offline.click("#opener")
        offline.wait_for_selector(".drawer pre.math[data-rendered='source']")
        self.assertIn("\\int_0^1", offline.inner_text(".drawer pre.math"))
        offline.wait_for_selector(".drawer code.language-mermaid[data-rendered='source']")
        self.assertEqual(errors, [])
        context.close()

    def test_tour_runs_once_on_first_open(self):
        page = self.open(tour=True)
        page.wait_for_selector(".tour .tour-card")
        self.assertIn("Welcome to Cairn", page.inner_text(".tour-card"))
        page.keyboard.press("ArrowRight")
        page.wait_for_function("() => document.querySelector('.tour-card h2').textContent === 'Capture anything'")
        page.wait_for_timeout(450)  # the spotlight glides to its target
        hole = page.locator(".tour-hole").bounding_box()
        target = page.locator("#capture-form").bounding_box()
        self.assertLess(abs(hole["x"] - (target["x"] - 6)), 2)
        page.keyboard.press("ArrowLeft")
        page.wait_for_function("() => document.querySelector('.tour-card h2').textContent === 'Welcome to Cairn'")
        for _ in range(11):
            page.keyboard.press("ArrowRight")
        page.wait_for_selector(".tour", state="detached")
        self.assertEqual(page.evaluate("localStorage.getItem('cairn.tour.v1')"), "done")
        page.reload()
        page.wait_for_selector("#nav button")
        page.wait_for_timeout(800)
        self.assertEqual(page.locator(".tour").count(), 0)
        page.click('[data-action="tour"]')
        page.wait_for_selector(".tour")
        page.click('[data-tour="skip"]')
        page.wait_for_selector(".tour", state="detached")
        self.assertEqual(self.errors, [])

    def test_tour_on_a_phone_opens_the_menu_for_sidebar_steps(self):
        page = self.open(viewport=(390, 844), tour=True)
        page.wait_for_selector(".tour .tour-card")
        for _ in range(3):
            page.keyboard.press("ArrowRight")
        page.wait_for_function("() => document.querySelector('.tour-card h2').textContent === 'Get it done'")
        self.assertTrue(page.evaluate("document.querySelector('#app').classList.contains('nav-open')"))
        page.wait_for_timeout(300)
        card = page.locator(".tour-card").bounding_box()
        self.assertGreaterEqual(card["x"], 0)
        self.assertLessEqual(card["x"] + card["width"], 390)
        page.keyboard.press("Escape")
        page.wait_for_selector(".tour", state="detached")
        self.assertFalse(page.evaluate("document.querySelector('#app').classList.contains('nav-open')"))

    def test_no_tour_for_visitors(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 860})
        install_github_mock(context)
        page = context.new_page()
        page.add_init_script("window.CAIRN_TOUR = true")
        page.goto(self.url + "?u=juw12")
        page.wait_for_selector(".profile-card")
        page.wait_for_timeout(800)
        self.assertEqual(page.locator(".tour").count(), 0)
        context.close()


class AttachmentServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.brain = Brain(self.tmp)
        self.brain.init()
        (self.brain.dir / "attachments").mkdir()
        (self.brain.dir / "attachments" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        (self.brain.dir / "notes" / "local.png").write_bytes(b"\x89PNGlocal")
        (self.brain.dir / "draw.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", encoding="utf-8")
        (self.tmp / "secret.png").write_bytes(b"outside the brain")
        self.server = make_server(self.brain, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        conn.close()
        return response.status, headers, body

    def test_serves_attachments_found_like_obsidian(self):
        status, headers, body = self.get("/api/raw?path=pic.png&from=notes/x.md")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertTrue(body.endswith(b"fake"))
        status, _, body = self.get("/api/raw?path=local.png&from=notes/x.md")
        self.assertEqual((status, body), (200, b"\x89PNGlocal"))
        status, headers, _ = self.get("/api/raw?path=draw.svg")
        self.assertEqual(status, 200)
        self.assertIn("sandbox", headers["Content-Security-Policy"])

    def test_refuses_anything_outside_the_brain_or_not_an_attachment(self):
        for path in ("../secret.png", "..%2Fsecret.png", "notes/../../secret.png", "C:/Windows/win.ini", "README.md", "notes/x.md", ""):
            status, _, body = self.get(f"/api/raw?path={path}&from=notes/x.md")
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"outside the brain", body)
        status, _, _ = self.get("/api/raw?path=pic.png", host="evil.example")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()

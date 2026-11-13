"""Signed-in editing of a brain stored on GitHub (rubric_signin.md gates G2-G6, G8).

Runs the static site in a real browser against a stateful fake GitHub (tests/webserver.py),
so every save, conflict and delete goes through the same API calls the live site makes.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import CORS, MOCK_REPO, OTHER_TOKEN, VALID_TOKEN, go_to, install_github_mock, launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

REQUIRE = os.environ.get("CAIRN_REQUIRE_BROWSER") == "1"
REPO = "juw12/cairn"


def session_json(token=VALID_TOKEN, login="juw12"):
    return json.dumps({"token": token, "method": "token", "login": login, "name": login, "avatar": "", "scopes": ["public_repo"], "remember": False})


@unittest.skipIf(sync_playwright is None and not REQUIRE, "playwright not installed")
class SignInTests(unittest.TestCase):
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

    def open(self, query, *, session=None, push=True, wait="#nav button", live_ms=None):
        context = self.browser.new_context(viewport={"width": 1280, "height": 860})
        self.addCleanup(context.close)
        self.mock = install_github_mock(context, push=push)
        page = context.new_page()
        self.errors = []
        self.authorized_hosts = set()
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", self.note_console)
        page.on("request", self.note_request)
        page.add_init_script("localStorage.setItem('cairn.theme', 'light'); window.CAIRN_TEST_POLL_MS = 50;")
        if live_ms:
            page.add_init_script(f"window.CAIRN_TEST_LIVE_MS = {live_ms};")
        if session:
            page.add_init_script(f"if (!sessionStorage.getItem('cairn.session')) sessionStorage.setItem('cairn.session', {json.dumps(session)});")
        page.goto(self.url + query)
        page.wait_for_selector(wait)
        return page

    def note_console(self, message):
        # Chrome logs every 4xx as "Failed to load resource"; the expected ones are part of these tests.
        expected = message.text.startswith("Failed to load resource") and any(code in message.text for code in ("401", "403", "409", "422", "ERR_INTERNET_DISCONNECTED"))
        if message.type == "error" and not expected:
            self.errors.append(message.text)

    def note_request(self, request):
        if "authorization" in request.headers:
            self.authorized_hosts.add(urlparse(request.url).netloc)

    def storage(self, page):
        return page.evaluate("() => ({ session: sessionStorage.getItem('cairn.session'), local: localStorage.getItem('cairn.session') })")

    def wait_for_remote(self, page, path, predicate, timeout=8000):
        deadline = timeout / 100
        for _ in range(int(deadline)):
            text = self.mock.text(REPO, path)
            if predicate(text):
                return text
            page.wait_for_timeout(100)
        self.fail(f"{path} never reached the expected state on GitHub; last: {self.mock.text(REPO, path)!r}")

    def sign_in_with_token(self, page, token=VALID_TOKEN, remember=False):
        page.click('#sidebar-foot [data-action="sign-in"]')
        page.wait_for_selector("#signin-dialog")
        if remember:
            page.check("#signin-remember")
        page.fill("#token-input", token)
        page.click("#token-submit")

    # ------------------------------------------------------------------ G3, G4

    def test_vibe_follows_a_github_brain_live_when_signed_in(self):
        page = self.open(f"?brain={REPO}#/board", session=session_json())
        page.wait_for_selector(".board")
        # An agent on another machine pushes a live session while you watch.
        self.mock.set_text(REPO, "brain/sessions/2026-11-07-remote-agent.md",
                           "---\ntitle: Remote agent\ntype: session\nstatus: live\nstarted: 2026-11-07T09:00\n---\n## Log\n\n- 09:00 start: Remote agent\n- 09:05 skill: /refine 6.0 -> 8.8\n")
        go_to(page, "vibe")
        page.wait_for_function("() => document.querySelector('.vibe-live h2')?.innerText.includes('Remote agent')", timeout=8000)
        self.assertIn("Following GitHub", page.inner_text(".vibe-live"))
        # Later polls are conditional: GitHub answers 304 for an unchanged folder, which costs no rate limit.
        for _ in range(50):
            if any(listing["conditional"] for listing in self.mock.listings):
                break
            page.wait_for_timeout(100)
        self.assertTrue(any(listing["conditional"] for listing in self.mock.listings))
        self.mock.set_text(REPO, "brain/sessions/2026-11-07-remote-agent.md",
                           self.mock.text(REPO, "brain/sessions/2026-11-07-remote-agent.md") + "- 09:20 pass: pytest · 40 passed\n")
        page.wait_for_function("() => document.querySelector('.vibe-log')?.innerText.includes('40 passed')", timeout=8000)
        self.assertEqual(self.errors, [])

    def test_vibe_overview_follows_a_remote_session_as_it_runs(self):
        page = self.open(f"?brain={REPO}#/board", session=session_json())
        page.wait_for_selector(".board")
        path = "brain/sessions/2026-11-07-remote-agent.md"
        self.mock.set_text(REPO, path, "---\ntitle: Remote agent\ntype: session\nstatus: live\nstarted: 2026-11-07T09:00\n---\n## Log\n\n- 09:00 start: Remote agent\n- 09:05 skill: /refine 6.0 -> 8.8\n")
        go_to(page, "vibe")
        page.wait_for_function("() => document.querySelector('.ov-lane .ov-step .txt')?.textContent.includes('/refine 6.0')", timeout=8000)
        self.assertEqual(page.locator(".ov-lane").count(), 1)
        self.assertEqual(page.inner_text(".ov-lane .ov-title"), "Remote agent")
        self.assertEqual(page.get_attribute('.ov-tile[data-outcome="live"] .ov-count', "data-count"), "1")

        # The agent logs a check: the lane shows it on the next poll, on the same page load.
        steps = int(page.get_attribute(".ov-lane", "data-steps"))
        page.evaluate("window.__sameLoad = true")
        self.mock.set_text(REPO, path, self.mock.text(REPO, path) + "- 09:20 pass: pytest · 40 passed\n")
        page.wait_for_function("() => document.querySelector('.ov-lane .ov-step .txt')?.textContent.includes('40 passed')", timeout=8000)
        self.assertEqual(int(page.get_attribute(".ov-lane", "data-steps")), steps + 1)
        self.assertEqual(page.locator(".ov-lane .ov-step.k-pass").count(), 1)
        self.assertTrue(page.evaluate("window.__sameLoad"))

        # The agent finishes: the lane goes and the session is counted as shipped.
        ended = self.mock.text(REPO, path).replace("status: live", "status: shipped\nended: 2026-11-07T09:30") + "- 09:30 end: shipped\n"
        self.mock.set_text(REPO, path, ended)
        page.wait_for_function("() => !document.querySelector('.ov-lane') && document.querySelector('.ov-tile[data-outcome=\"shipped\"] .ov-count')?.dataset.count === '1'", timeout=8000)
        self.assertTrue(page.is_disabled('.ov-tile[data-outcome="live"]'))
        self.assertEqual(self.errors, [])

    def test_vibe_on_someone_elses_brain_signed_out_does_not_poll(self):
        page = self.open(f"?brain={REPO}", wait=".ro-banner")
        go_to(page, "vibe")
        page.wait_for_selector(".vibe-figures")
        page.wait_for_timeout(400)
        self.assertEqual(self.mock.listings, [])
        self.assertEqual(self.errors, [])

    def test_sign_in_then_create_edit_move_delete_on_github(self):
        page = self.open(f"?brain={REPO}", wait=".ro-banner")
        self.assertTrue(page.locator("#capture-form").is_hidden())

        # A wrong token is explained, not accepted.
        self.sign_in_with_token(page, token="not-a-token")
        page.wait_for_selector("#signin-error:not([hidden])")
        self.assertIn("doesn't look like a GitHub token", page.inner_text("#signin-error"))

        page.fill("#token-input", VALID_TOKEN)
        page.click("#token-submit")
        page.wait_for_function("() => window.cairn?.state?.info?.mode === 'github' && !window.cairn.state.info.readOnly")
        self.assertEqual(page.locator(".ro-banner").count(), 0)
        self.assertIn("@juw12", page.inner_text("#sidebar-foot"))
        stored = self.storage(page)
        self.assertIsNotNone(stored["session"])
        self.assertIsNone(stored["local"], "token must not be in localStorage unless 'Remember' is ticked")

        # Create: quick capture becomes a new file in the repo (PUT without sha).
        page.fill("#capture-input", "todo: write from my phone #mobile")
        page.press("#capture-input", "Enter")
        created = self.wait_for_remote(page, "brain/tasks/write-from-my-phone.md", lambda t: t is not None)
        self.assertIn("tags: [mobile]", created)

        # Edit: a burst of typing becomes one commit, sent with the file's current sha.
        go_to(page, "board")
        page.click('.item-card[data-open="tasks/write-from-my-phone.md"]')
        page.wait_for_selector("#ed-body")
        before = len(self.mock.writes())
        page.click("#ed-body")
        page.keyboard.type(" Typed on the train.", delay=15)
        self.wait_for_remote(page, "brain/tasks/write-from-my-phone.md", lambda t: t and "Typed on the train." in t)
        page.wait_for_timeout(300)
        self.assertEqual(len(self.mock.writes()) - before, 1, "typing a sentence should be one commit, not one per keystroke")
        page.wait_for_function("() => document.querySelector('#save-state')?.textContent === 'Saved to GitHub'")

        # Move: status change is saved.
        page.select_option("#ed-status", "doing")
        self.wait_for_remote(page, "brain/tasks/write-from-my-phone.md", lambda t: t and "status: doing" in t)

        # Delete: DELETE with sha removes it from the repo.
        page.click('[data-action="delete"]')
        page.click(".modal .btn.danger")
        self.wait_for_remote(page, "brain/tasks/write-from-my-phone.md", lambda t: t is None)

        self.assertEqual(self.authorized_hosts, {"api.github.com"}, "the token may only ever go to api.github.com")
        self.assertEqual(self.errors, [])

    def test_remember_on_device_and_sign_out(self):
        page = self.open(f"?brain={REPO}", wait=".ro-banner")
        self.sign_in_with_token(page, remember=True)
        page.wait_for_function("() => window.cairn?.state?.info && !window.cairn.state.info.readOnly")
        stored = self.storage(page)
        self.assertIsNone(stored["session"])
        self.assertIsNotNone(stored["local"])
        page.click('[data-action="sign-out"]')
        page.wait_for_selector(".ro-banner")
        stored = self.storage(page)
        self.assertIsNone(stored["session"])
        self.assertIsNone(stored["local"])
        self.assertTrue(page.locator('#sidebar-foot [data-action="sign-in"]').is_visible())
        self.assertTrue(page.locator("#capture-form").is_hidden())
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------------ G5

    def test_conflict_keeps_my_text(self):
        page = self.open(f"?brain={REPO}#/board", session=session_json())
        page.wait_for_function("() => window.cairn.state.info && !window.cairn.state.info.readOnly")
        page.click('.item-card[data-open="tasks/mcp-server.md"]')
        page.wait_for_selector("#ed-body")
        # Another device saves first.
        self.mock.set_text(REPO, "brain/tasks/mcp-server.md", "---\ntitle: MCP server for AI assistants\ntype: task\nstatus: doing\n---\nEdited on my laptop.\n")
        page.click("#ed-body")
        page.keyboard.type(" My phone edit.")
        page.wait_for_selector(".held-card")
        self.assertIn("changed somewhere else", page.inner_text(".held-card"))
        self.assertEqual(page.inner_text("#save-state"), "Not saved", "the header must not say Saved while my text is held")
        self.assertIn("Edited on my laptop.", page.evaluate("() => window.cairn.state.files.find((f) => f.path === 'tasks/mcp-server.md').text"))
        self.assertIn("My phone edit.", page.evaluate("() => window.cairn.state.held.text"))
        page.click('[data-action="held-use"]')
        self.wait_for_remote(page, "brain/tasks/mcp-server.md", lambda t: t and "My phone edit." in t)
        page.wait_for_selector(".held-card", state="detached")
        self.assertEqual(self.errors, [])

    def test_offline_save_keeps_text_and_retries(self):
        page = self.open(f"?brain={REPO}#/board", session=session_json())
        page.wait_for_function("() => window.cairn.state.info && !window.cairn.state.info.readOnly")

        def drop_writes(route):
            if route.request.method == "PUT":
                route.abort("internetdisconnected")
            else:
                route.fallback()

        page.route("https://api.github.com/repos/**", drop_writes)
        page.click('.item-card[data-open="tasks/mcp-server.md"]')
        page.wait_for_selector("#ed-body")
        page.click("#ed-body")
        page.keyboard.type(" Typed on a train.")
        page.wait_for_selector(".held-card")
        self.assertIn("Can't reach GitHub", page.inner_text(".held-card"))
        self.assertIn("Typed on a train.", page.evaluate("() => window.cairn.state.held.text"))
        self.assertNotIn("Typed on a train.", self.mock.text(REPO, "brain/tasks/mcp-server.md"))
        page.unroute("https://api.github.com/repos/**", drop_writes)
        page.click('[data-action="held-use"]')
        self.wait_for_remote(page, "brain/tasks/mcp-server.md", lambda t: t and "Typed on a train." in t)
        page.wait_for_selector(".held-card", state="detached")
        self.assertEqual(self.errors, [])

    def test_sign_in_from_welcome_lands_in_your_brain(self):
        page = self.open("", wait=".welcome-account")
        page.click('.welcome-account [data-action="sign-in"]')
        page.wait_for_selector("#signin-dialog")
        page.fill("#token-input", VALID_TOKEN)
        page.click("#token-submit")
        # One brain (juw12/cairn) means no list to pick from: it opens, editable.
        page.wait_for_url(lambda url: "brain=juw12%2Fcairn" in url)
        page.wait_for_function("() => window.cairn?.state?.info?.repoName === 'cairn' && !window.cairn.state.info.readOnly")
        self.assertEqual(page.locator(".ro-banner").count(), 0)
        self.assertEqual(self.errors, [])

    def test_rate_limited_save_keeps_text(self):
        page = self.open(f"?brain={REPO}#/board", session=session_json())
        page.wait_for_function("() => window.cairn.state.info && !window.cairn.state.info.readOnly")

        def rate_limit_writes(route):
            if route.request.method == "PUT":
                route.fulfill(
                    status=403,
                    content_type="application/json",
                    body=json.dumps({"message": "API rate limit exceeded"}),
                    headers={**CORS, "x-ratelimit-remaining": "0"},
                )
            else:
                route.fallback()

        page.route("https://api.github.com/repos/**", rate_limit_writes)
        page.click('.item-card[data-open="tasks/mcp-server.md"]')
        page.wait_for_selector("#ed-body")
        page.click("#ed-body")
        page.keyboard.type(" Typed during a busy hour.")
        page.wait_for_selector(".held-card")
        self.assertIn("hourly limit", page.inner_text(".held-card"))
        self.assertIn("Typed during a busy hour.", page.evaluate("() => window.cairn.state.held.text"))
        page.unroute("https://api.github.com/repos/**", rate_limit_writes)
        page.click('[data-action="held-use"]')
        self.wait_for_remote(page, "brain/tasks/mcp-server.md", lambda t: t and "Typed during a busy hour." in t)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------------ G6

    def test_signed_in_without_push_access_stays_read_only(self):
        page = self.open(f"?brain={REPO}", session=session_json(OTHER_TOKEN, "someone-else"), wait=".ro-banner")
        self.assertTrue(page.locator("#capture-form").is_hidden())
        self.assertEqual(page.locator('.page-head [data-action="new"]').count(), 0)
        self.assertIn("@someone-else", page.inner_text("#sidebar-foot"))
        self.assertEqual(self.mock.writes(), [])
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------------ G8

    def test_revoked_token_signs_out_and_keeps_text(self):
        page = self.open(f"?brain={REPO}#/board", session=session_json())
        page.wait_for_function("() => window.cairn.state.info && !window.cairn.state.info.readOnly")
        page.click('.item-card[data-open="tasks/mcp-server.md"]')
        page.wait_for_selector("#ed-body")
        self.mock.revoked = True
        page.click("#ed-body")
        page.keyboard.type(" Written after the token was revoked.")
        page.wait_for_selector(".toast.error")
        page.wait_for_function("() => [...document.querySelectorAll('.toast')].some((t) => t.textContent.includes('expired or was revoked'))")
        page.wait_for_function("() => window.cairn.state.info.readOnly === true")
        stored = self.storage(page)
        self.assertIsNone(stored["session"])
        self.assertIsNone(stored["local"])
        self.assertIn("revoked", page.evaluate("() => window.cairn.state.held?.text || ''"))
        page.wait_for_selector(".held-card")
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------------ S1: your brains, create, device flow

    def test_create_a_brain_from_people(self):
        page = self.open("?u=juw12", session=session_json(), wait=".my-brains")
        page.wait_for_selector(".my-brains .repo-row")
        self.assertIn("juw12/cairn", page.inner_text(".my-brains"))
        page.fill("#new-brain-name", "brain")
        page.press("#new-brain-name", "Enter")
        page.wait_for_url(lambda url: "brain=juw12%2Fbrain" in url)
        page.wait_for_function("() => window.cairn?.state?.info?.repoName === 'brain' && !window.cairn.state.info.readOnly")
        self.assertIn("cairn-brain", self.mock.repos["juw12/brain"]["meta"]["topics"])
        self.assertTrue(page.evaluate("() => window.cairn.state.files.some((f) => f.path === 'tasks/capture-three-ideas.md')"))
        # Taking an existing name is explained.
        page.goto(self.url + "?u=juw12")
        page.wait_for_selector(".my-brains .repo-row")
        page.fill("#new-brain-name", "brain")
        page.press("#new-brain-name", "Enter")
        page.wait_for_function("() => [...document.querySelectorAll('.toast')].some((t) => t.textContent.includes('already have a repo'))")
        self.assertEqual(self.errors, [])

    def test_device_flow_sign_in(self):
        page = self.open(f"?brain={REPO}", wait=".ro-banner")
        polls = {"count": 0}

        def relay(route):
            path = urlparse(route.request.url).path
            headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type, Accept", "Access-Control-Allow-Methods": "POST, OPTIONS"}
            if route.request.method == "OPTIONS":
                return route.fulfill(status=204, headers=headers)
            if path == "/device/code":
                body = {"device_code": "dev-code-123456", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device", "interval": 5, "expires_in": 900}
            else:
                polls["count"] += 1
                body = {"error": "authorization_pending"} if polls["count"] < 2 else {"access_token": VALID_TOKEN, "token_type": "bearer", "scope": "public_repo"}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body), headers=headers)

        page.context.route("https://auth.example.test/**", relay)
        page.evaluate("() => document.querySelector('meta[name=cairn-auth]').setAttribute('content', 'https://auth.example.test')")
        page.click('#sidebar-foot [data-action="sign-in"]')
        page.click("[data-signin=device]")
        page.wait_for_selector("#device-code")
        self.assertEqual(page.inner_text("#device-code"), "WDJB-MJHT")
        page.wait_for_function("() => window.cairn?.state?.info && !window.cairn.state.info.readOnly", timeout=15000)
        self.assertGreaterEqual(polls["count"], 2)
        self.assertEqual(json.loads(self.storage(page)["session"])["method"], "device")
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------------ Obsidian vault repo (no brain/ folder)

    def test_obsidian_vault_repo_opens_at_root_and_edits_stay_lossless(self):
        page = self.open("?u=juw12", session=session_json(), wait="#view")
        home = "---\r\ntags:\r\n  - hub\r\nstatus: active\r\n---\r\n# Home\r\nSee [[Projects/Alpha|alpha]].\r\n"
        self.mock.repos["juw12/vault"] = {
            "meta": dict(MOCK_REPO, name="vault", full_name="juw12/vault", html_url="https://github.com/juw12/vault", topics=[]),
            "files": {
                ".obsidian/app.json": "{}",
                ".trash/old.md": "# Old\n",
                "Home.md": home,
                "Projects/Alpha.md": "# Alpha\nBack to [[Home]].\n",
            },
        }
        page.goto(self.url + "?brain=juw12/vault")
        page.wait_for_function("() => window.cairn?.state?.info?.repoName === 'vault' && window.cairn.state.files.length === 2")
        paths = page.evaluate("window.cairn.state.files.map((f) => f.path).sort()")
        self.assertEqual(paths, ["Home.md", "Projects/Alpha.md"])
        edges = page.evaluate("window.cairn.core.buildGraph(window.cairn.state.notes).edges.length")
        self.assertEqual(edges, 1)
        page.evaluate("""async () => {
            const { saveNote } = await import('./state.js');
            await saveNote('Home.md', { status: 'done' });
        }""")
        page.wait_for_function("() => window.cairn.state.files.find((f) => f.path === 'Home.md').text.includes('status: done')")
        self.assertEqual(self.mock.text("juw12/vault", "Home.md"), home.replace("status: active", "status: done"))
        self.assertNotIn("brain/Home.md", self.mock.repos["juw12/vault"]["files"])
        self.assertEqual(self.errors, [])


    # ------------------------------------------------------------------ phone / home-screen app

    def test_github_brain_syncs_live_without_reloading(self):
        # The phone (or its home-screen app) opens the vault from GitHub; an edit made on the PC shows up
        # by itself, the way the PC window follows the phone.
        page = self.open(f"?brain={REPO}#/board", session=session_json(), live_ms=200)
        page.wait_for_selector(".board")
        path = "brain/ideas/edited-on-the-pc.md"
        self.mock.set_text(REPO, path, "---\ntitle: Edited on the PC\ntype: idea\nstatus: raw\n---\nFrom the desktop.\n")
        page.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'ideas/edited-on-the-pc.md')", timeout=8000)
        page.wait_for_selector(".toast:has-text('updated from your other device')")
        # Unchanged polls are conditional and answered 304, which costs no GitHub rate limit.
        for _ in range(50):
            if any(tree["unchanged"] for tree in self.mock.trees):
                break
            page.wait_for_timeout(100)
        self.assertTrue(any(tree["unchanged"] for tree in self.mock.trees))

        # Your own save from this page never comes back as an "update from another device".
        page.evaluate("""async () => {
            const { saveNote } = await import('./state.js');
            await saveNote('ideas/edited-on-the-pc.md', { status: 'done' });
        }""")
        self.wait_for_remote(page, path, lambda text: "status: done" in (text or ""))
        page.wait_for_timeout(800)
        self.assertEqual(page.locator(".toast:has-text('updated from your other device')").count(), 1)

        # A note deleted on the PC disappears here too.
        del self.mock.repos[REPO]["files"][path]
        page.wait_for_function("() => !window.cairn.state.files.some((f) => f.path === 'ideas/edited-on-the-pc.md')", timeout=8000)
        self.assertEqual(self.errors, [])

    def test_reopening_shows_the_device_copy_at_once_then_catches_up(self):
        page = self.open(f"?brain={REPO}#/board", session=session_json())
        page.wait_for_selector(".board")
        page.wait_for_timeout(1500)  # the device copy is saved a moment after loading
        # While the app is closed, the PC changes a note.
        path = "brain/ideas/while-you-were-away.md"
        self.mock.set_text(REPO, path, "---\ntitle: While you were away\ntype: idea\nstatus: raw\n---\nNew.\n")
        self.mock.trees.clear()
        graphql_before = sum(1 for r in self.mock.requests if r["url"].endswith("/graphql"))

        context = page.context
        page.close()  # the app is closed meanwhile
        again = context.new_page()
        again.on("pageerror", lambda e: self.errors.append(str(e)))
        again.add_init_script(f"sessionStorage.setItem('cairn.session', {json.dumps(session_json())});")
        again.goto(self.url + f"?brain={REPO}#/board")
        again.wait_for_selector(".board")
        self.assertFalse(again.is_visible(".welcome-loading"), "no loading screen when the vault is on the device")
        # It catches up by itself: one conditional tree request, then only the changed note is fetched.
        again.wait_for_function("() => window.cairn.state.files.some((f) => f.path === 'ideas/while-you-were-away.md')", timeout=8000)
        self.assertTrue(self.mock.trees and self.mock.trees[0]["conditional"])
        fetches = sum(1 for r in self.mock.requests if r["url"].endswith("/graphql")) - graphql_before
        self.assertLessEqual(fetches, 1, "only the changed note is downloaded, not the whole vault")
        self.assertEqual(self.errors, [])

    def test_signing_out_forgets_the_device_copy(self):
        page = self.open(f"?brain={REPO}#/board", session=session_json())
        page.wait_for_selector(".board")
        page.wait_for_function("""async () => {
            const { readBrain, brainKey } = await import('./brain-cache.js');
            return Boolean(await readBrain(brainKey('juw12', 'juw12/cairn')));
        }""", timeout=8000)
        page.evaluate("window.dispatchEvent(new CustomEvent('cairn:signed-out', { detail: {} }))")
        page.wait_for_function("""async () => {
            const { readBrain, brainKey } = await import('./brain-cache.js');
            return !(await readBrain(brainKey('juw12', 'juw12/cairn')));
        }""", timeout=8000)

if __name__ == "__main__":
    unittest.main()

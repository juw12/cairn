"""Reading GitHub through the GitHub CLI's sign-in of the computer Cairn runs on: what is passed on, what is
refused, and the Projects page with it, without it, and when GitHub cannot be asked at all."""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import launch_browser  # noqa: E402

from cairn import cloud_backup, github_read  # noqa: E402
from cairn.brain import Brain  # noqa: E402
from cairn.phone import make_phone_handler  # noqa: E402
from cairn.server import make_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

SECRET = "cli-secret-grace"


class Answer:
    """What urlopen gives back, as far as `read` looks at it."""

    def __init__(self, status=200, body=b"[]", headers=None):
        self.status = status
        self.body = body
        self.headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def switched_on(test):
    """The off switch the browser tests set is taken away for one test, and the token is asked for again."""
    for patch in (mock.patch.dict(os.environ), mock.patch.object(github_read, "_token", {"value": "", "at": float("-inf")})):
        patch.start()
        test.addCleanup(patch.stop)
    os.environ.pop(github_read.OFF_SWITCH, None)


class ReadTests(unittest.TestCase):
    def setUp(self):
        switched_on(self)
        self.asked: list[urllib.request.Request] = []

    def opener(self, answer):
        def open_it(request):
            self.asked.append(request)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return open_it

    def test_only_reads_of_users_orgs_repos_and_search(self):
        for path in (
            "/user",
            "/user/repos?affiliation=owner&sort=pushed&per_page=100&page=2",
            "/users/juw12/repos?type=owner&per_page=100",
            "/orgs/acme/repos",
            "/repos/juw12/cairn",
            "/repos/juw12/cairn/contents/",
            "/repos/juw12/cairn/contents/docs/My%20File.md?ref=main",
            "/search/repositories?q=user:juw12+topic:cairn-brain&sort=updated",
            "/rate_limit",
        ):
            self.assertTrue(github_read.allowed(path), path)
        for path in (
            "",
            "user",
            "/user/keys",
            "/user/emails",
            "/gists",
            "/notifications",
            "/repos/juw12/cairn/../../user/keys",
            "/users/juw12/repos?x=1 HTTP/1.1",
            "/users/juw12/repos#x",
            "//evil.example/users/x",
            "https://evil.example/users/x",
            "/graphql",
        ):
            self.assertFalse(github_read.allowed(path), path)

    def test_a_read_goes_out_as_the_cli_and_only_some_headers_come_back(self):
        answer = Answer(200, b'[{"name": "cairn"}]', {"ETag": 'W/"abc"', "X-RateLimit-Remaining": "4999", "X-RateLimit-Limit": "5000", "Set-Cookie": "x=1", "X-OAuth-Scopes": "repo"})
        status, headers, body = github_read.read("/users/juw12/repos?per_page=100", find=lambda: SECRET, opener=self.opener(answer))
        self.assertEqual((status, body), (200, b'[{"name": "cairn"}]'))
        self.assertEqual(headers, {"Content-Type": "application/json; charset=utf-8", "ETag": 'W/"abc"', "X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999"})
        request = self.asked[0]
        self.assertEqual(request.full_url, "https://api.github.com/users/juw12/repos?per_page=100")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {SECRET}")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
        self.assertNotIn(SECRET, json.dumps(headers) + body.decode())

    def test_the_form_of_the_answer_and_a_known_etag_are_passed_to_github(self):
        gone = urllib.error.HTTPError("u", 304, "Not Modified", {"ETag": 'W/"abc"'}, io.BytesIO(b""))
        status, headers, body = github_read.read("/repos/a/b/readme", accept="application/vnd.github.html", if_none_match='W/"abc"', find=lambda: SECRET, opener=self.opener(gone))
        self.assertEqual((status, headers, body), (304, {"ETag": 'W/"abc"'}, b""))
        self.assertEqual(self.asked[0].get_header("Accept"), "application/vnd.github.html")
        self.assertEqual(self.asked[0].get_header("If-none-match"), 'W/"abc"')
        # An Accept that is not one of GitHub's forms is not passed on.
        github_read.read("/repos/a/b", accept="text/html, */*", find=lambda: SECRET, opener=self.opener(Answer()))
        self.assertEqual(self.asked[1].get_header("Accept"), "application/vnd.github+json")

    def test_githubs_own_refusals_are_the_answer(self):
        missing = urllib.error.HTTPError("u", 404, "Not Found", {"Content-Type": "application/json"}, io.BytesIO(b'{"message": "Not Found"}'))
        status, _, body = github_read.read("/repos/a/b", find=lambda: SECRET, opener=self.opener(missing))
        self.assertEqual((status, json.loads(body)), (404, {"message": "Not Found"}))
        limited = urllib.error.HTTPError("u", 403, "Forbidden", {"X-RateLimit-Remaining": "0"}, io.BytesIO(b'{"message": "API rate limit exceeded"}'))
        status, headers, _ = github_read.read("/repos/a/b", find=lambda: SECRET, opener=self.opener(limited))
        self.assertEqual((status, headers), (403, {"X-RateLimit-Remaining": "0"}))

    def test_what_github_is_never_asked(self):
        with self.assertRaises(github_read.ReadError) as refused:
            github_read.read("/user/keys", find=lambda: SECRET, opener=self.opener(Answer()))
        self.assertEqual(refused.exception.status, 400)
        with self.assertRaises(github_read.ReadError) as signed_out:
            github_read.read("/user", find=lambda: "", opener=self.opener(Answer()))
        self.assertEqual(signed_out.exception.status, 503)
        self.assertIn("gh auth login", str(signed_out.exception))
        self.assertEqual(self.asked, [])
        github_read.forget()
        with self.assertRaises(github_read.ReadError) as offline:
            github_read.read("/user", find=lambda: SECRET, opener=self.opener(urllib.error.URLError("no route to host")))
        self.assertEqual(offline.exception.status, 502)
        self.assertNotIn(SECRET, str(offline.exception))

    def test_the_cli_is_asked_once_a_minute_and_again_after_a_switch_or_a_revoked_sign_in(self):
        calls = []

        def find():
            calls.append(1)
            return SECRET

        for _ in range(3):
            github_read.read("/user", find=find, opener=self.opener(Answer()))
        self.assertEqual(len(calls), 1)
        github_read.forget()  # the account was switched in Cairn
        github_read.read("/user", find=find, opener=self.opener(Answer()))
        self.assertEqual(len(calls), 2)
        revoked = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"message": "Bad credentials"}'))
        with self.assertRaises(github_read.ReadError) as refused:
            github_read.read("/user", find=find, opener=self.opener(revoked))
        self.assertEqual(refused.exception.status, 503)
        github_read.read("/user", find=find, opener=self.opener(Answer()))
        self.assertEqual(len(calls), 3)

    def test_the_off_switch(self):
        with mock.patch.object(cloud_backup, "gh", return_value="/usr/bin/gh"):
            self.assertTrue(github_read.enabled())
            os.environ[github_read.OFF_SWITCH] = "1"
            self.assertFalse(github_read.enabled())
        with self.assertRaises(github_read.ReadError) as off:
            github_read.read("/user", find=lambda: SECRET, opener=self.opener(Answer()))
        self.assertEqual((off.exception.status, self.asked), (503, []))
        with mock.patch.dict(os.environ), mock.patch.object(cloud_backup, "gh", return_value=""):
            os.environ.pop(github_read.OFF_SWITCH, None)
            self.assertFalse(github_read.enabled(), "no GitHub CLI on this computer")


class RouteTests(unittest.TestCase):
    """GET /api/github/read on the local server, and not on the phone's listener."""

    def setUp(self):
        switched_on(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        brain = Brain(Path(self.tmp.name))
        brain.init()
        self.server = make_server(brain, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        gh = mock.patch.object(cloud_backup, "gh", return_value="/usr/bin/gh")
        gh.start()
        self.addCleanup(gh.stop)

    def get(self, path, headers=None):
        request = urllib.request.Request(self.base + path, headers={"X-Cairn": "1", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, dict(error.headers), error.read()

    def test_the_answer_is_passed_on_with_its_status_and_headers(self):
        seen = {}

        def read(target, accept="", if_none_match=""):
            seen.update(target=target, accept=accept, etag=if_none_match)
            return 200, {"Content-Type": "application/json; charset=utf-8", "X-RateLimit-Remaining": "4990"}, b'[{"name": "cairn"}]'

        with mock.patch.object(github_read, "read", read):
            status, headers, body = self.get("/api/github/read?path=%2Fusers%2Fjuw12%2Frepos%3Fper_page%3D100%26page%3D1", {"X-GitHub-Accept": "application/vnd.github.html", "If-None-Match": 'W/"abc"'})
            self.assertEqual((status, json.loads(body)), (200, [{"name": "cairn"}]))
            self.assertEqual(headers["X-RateLimit-Remaining"], "4990")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(seen, {"target": "/users/juw12/repos?per_page=100&page=1", "accept": "application/vnd.github.html", "etag": 'W/"abc"'})
            self.assertTrue(json.loads(self.get("/api/info")[2])["githubRead"])

    def test_refusals_say_why_and_a_page_without_the_header_gets_nothing(self):
        with mock.patch.object(github_read, "token", return_value=""):
            status, _, body = self.get("/api/github/read?path=%2Fuser")
            self.assertEqual(status, 503)
            self.assertIn("gh auth login", json.loads(body)["error"])
            self.assertEqual(self.get("/api/github/read?path=%2Fuser%2Fkeys")[0], 400)
        with mock.patch.object(github_read, "read", side_effect=AssertionError("must not be asked")):
            request = urllib.request.Request(self.base + "/api/github/read?path=%2Fuser")  # a link, an <img>: no X-Cairn
            with self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(refused.exception.code, 403)
            refused.exception.close()

    def test_a_paired_phone_does_not_read_through_the_computers_sign_in(self):
        phone = mock.Mock()
        phone.store.check.return_value = True
        handler = make_phone_handler(self.server.RequestHandlerClass, phone)
        self.assertFalse(handler.github_moves)
        from http.server import ThreadingHTTPServer

        listener = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=listener.serve_forever, daemon=True).start()
        self.addCleanup(listener.server_close)
        self.addCleanup(listener.shutdown)
        self.base = f"http://127.0.0.1:{listener.server_address[1]}"
        with mock.patch.object(github_read, "read", side_effect=AssertionError("must not be asked")):
            status, _, body = self.get("/api/github/read?path=%2Fuser")
            self.assertEqual((status, json.loads(body)["error"]), (404, "unknown endpoint"))
            self.assertFalse(json.loads(self.get("/api/info")[2])["githubRead"])


def repo(name, owner="juw12", **extra):
    return {
        "name": name, "full_name": f"{owner}/{name}", "html_url": f"https://github.com/{owner}/{name}", "owner": {"login": owner},
        "description": f"About {name}", "homepage": "", "has_pages": False, "license": None, "topics": [], "size": 500,
        "stargazers_count": 1, "forks_count": 0, "fork": False, "archived": False, "private": False,
        "created_at": "2026-06-01T00:00:00Z", "pushed_at": "2026-11-20T00:00:00Z", "language": "Python", **extra,
    }


PUBLIC = [repo("cairn", topics=["notes"]), repo("rocket", topics=["game"])]
LET_INTO = [repo("ledger", private=True, description="Family ledger"), repo("cairn", topics=["notes"]), repo("elsewhere", owner="someone", private=True)]


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class ProjectsPageTests(unittest.TestCase):
    """The Projects page on the computer itself: asked as the CLI's account, falling back, and the kept list."""

    def setUp(self):
        switched_on(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        brain = Brain(Path(self.tmp.name))
        brain.init()
        self.server = make_server(brain, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = launch_browser(self.pw)
        self.addCleanup(self.browser.close)
        self.signed_in = True
        self.through_computer: list[str] = []
        self.straight: list[str] = []
        self.github_up = True
        self.errors: list[str] = []
        for patch in (mock.patch.object(cloud_backup, "gh", return_value="/usr/bin/gh"), mock.patch.object(github_read, "read", self.read)):
            patch.start()
            self.addCleanup(patch.stop)

    def read(self, target, accept="", if_none_match=""):
        """The computer's sign-in is grace, who juw12 let into one private repo."""
        if not self.signed_in:
            raise github_read.ReadError(503, "The GitHub CLI is not signed in on this computer. Sign in once with: gh auth login")
        self.through_computer.append(target)
        path = target.split("?")[0]
        page_one = "page=1" in target
        if path == "/user":
            data = {"login": "grace"}
        elif path == "/users/juw12/repos":
            data = PUBLIC if page_one else []
        elif path == "/user/repos":
            data = LET_INTO if page_one and "affiliation=collaborator" in target else []
        elif path.endswith("/contents/"):
            data = [{"name": "README.md"}, {"name": "tests"}]
        elif path == "/repos/juw12/cairn":
            data = PUBLIC[0]
        else:
            return 404, {"Content-Type": "application/json"}, b'{"message": "Not Found"}'
        return 200, {"Content-Type": "application/json; charset=utf-8", "X-RateLimit-Remaining": "4990"}, json.dumps(data).encode()

    def open(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 1000})
        self.addCleanup(context.close)
        cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "x-ratelimit-remaining"}

        def github(route):
            """GitHub asked straight from the page, without a sign-in: public repos only, or its hourly limit."""
            url = route.request.url
            self.straight.append(url)
            path = url.split("api.github.com", 1)[1].split("?")[0]
            if not self.github_up:
                return route.fulfill(status=403, headers={**cors, "x-ratelimit-remaining": "0", "Content-Type": "application/json"}, body='{"message": "API rate limit exceeded"}')
            if path == "/users/juw12/repos":
                return route.fulfill(status=200, headers={**cors, "Content-Type": "application/json"}, body=json.dumps(PUBLIC if "page=1" in url else []))
            if path.endswith("/contents/"):
                return route.fulfill(status=200, headers={**cors, "Content-Type": "application/json"}, body='[{"name": "README.md"}]')
            if path == "/repos/juw12/cairn":
                return route.fulfill(status=200, headers={**cors, "Content-Type": "application/json"}, body=json.dumps(PUBLIC[0]))
            return route.fulfill(status=404, headers={**cors, "Content-Type": "application/json"}, body='{"message": "Not Found"}')

        context.route("https://api.github.com/**", github)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.add_init_script("localStorage.setItem('cairn.projects.owner', '\"juw12\"')")
        page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/#/projects")
        return page

    def names(self, page):
        return sorted(page.eval_on_selector_all(".proj-card .proj-title a", "els => els.map((e) => e.textContent)"))

    def test_the_page_asks_as_the_computers_account_and_shows_the_private_repos_it_was_let_into(self):
        page = self.open()
        page.wait_for_selector(".proj-card")
        self.assertEqual(self.names(page), ["cairn", "ledger", "rocket"], "the owner's private repo; not someone else's, and cairn once")
        self.assertEqual(self.straight, [], "nothing was asked anonymously")
        self.assertIn("asked GitHub as grace (this computer's sign-in)", page.inner_text(".proj-status"))
        self.assertNotIn("sign in to check all", page.inner_text(".proj-status"))
        self.assertEqual(page.locator('.proj-card[data-project="juw12/ledger"] .proj-meta').inner_text().count("private"), 1)
        kinds = page.eval_on_selector_all('[data-action="projects-filter"][data-kind="shown"]', "els => els.map((e) => e.textContent.replace(/\\s+/g, ' ').trim())")
        self.assertEqual(kinds, ["Public 2", "Private 1"])
        self.assertTrue(any(target.startswith("/repos/juw12/ledger/contents/") for target in self.through_computer), "tests and CI of a private repo are looked up too")
        self.assertEqual(self.errors, [])

    def test_without_a_cli_sign_in_the_page_asks_github_itself_as_before(self):
        self.signed_in = False
        page = self.open()
        page.wait_for_selector(".proj-card")
        self.assertEqual(self.names(page), ["cairn", "rocket"])
        self.assertEqual(self.through_computer, [])
        self.assertTrue(self.straight)
        self.assertNotIn("asked GitHub as", page.inner_text(".proj-status"))
        self.assertEqual(self.errors, [])

    def test_when_github_cannot_be_asked_the_list_of_the_last_visit_is_shown(self):
        self.signed_in = False
        page = self.open()
        page.wait_for_selector(".proj-card")
        # GitHub's hourly limit for anonymous lookups is reached: a new visit still shows the list.
        self.github_up = False
        page.evaluate("() => sessionStorage.clear()")
        page.reload()
        page.wait_for_selector(".proj-card")
        self.assertEqual(self.names(page), ["cairn", "rocket"])
        status = page.inner_text(".proj-status")
        self.assertIn("GitHub could not be asked now: this is the list of", status)
        self.assertIn("hourly limit for anonymous lookups", status)
        self.assertEqual(page.locator("text=Couldn't load the repos").count(), 0)
        # Nothing kept for another owner: the page says it could not load, as before.
        page.fill("#projects-owner", "nobody")
        page.click('#projects-form button[type="submit"]')
        page.wait_for_selector("text=Couldn't load the repos")
        # GitHub answers again: Re-rank shows the fresh list and the notice goes.
        self.github_up = True
        page.fill("#projects-owner", "juw12")
        page.click('#projects-form button[type="submit"]')
        page.wait_for_function("() => /Re-ranked/.test(document.querySelector('.proj-status')?.textContent || '')")
        self.assertEqual(self.names(page), ["cairn", "rocket"])
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

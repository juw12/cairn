"""cairn dashboards: local dashboard pages copied to the private relay repo for a phone to read."""

import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cairn import dashboards, relay


class FakeGitHub:
    """Just enough of the Git Data API: blobs, trees, commits and one branch ref."""

    def __init__(self) -> None:
        self.repo = "me/cairn-relay"
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, list] = {}
        self.commits: dict[str, dict] = {}
        self.refs: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def call(self, method, path, body=None, etag=""):
        self.calls.append((method, path))
        prefix = f"/repos/{self.repo}"
        assert path.startswith(prefix), path
        path = path[len(prefix):]
        if method == "POST" and path == "/git/blobs":
            data = base64.b64decode(body["content"])
            sha = dashboards.git_blob_sha(data)
            self.blobs[sha] = data
            return 201, {"sha": sha}, {}
        if method == "POST" and path == "/git/trees":
            for item in body["tree"]:
                assert item["sha"] in self.blobs, "tree points at a blob GitHub doesn't have"
            sha = f"tree{len(self.trees)}"
            self.trees[sha] = body["tree"]
            return 201, {"sha": sha}, {}
        if method == "POST" and path == "/git/commits":
            sha = f"commit{len(self.commits)}"
            self.commits[sha] = body
            return 201, {"sha": sha}, {}
        if method == "PATCH" and path.startswith("/git/refs/heads/"):
            name = path.rsplit("/", 1)[1]
            if name not in self.refs:
                return 422, {"message": "Reference does not exist"}, {}
            self.refs[name] = body["sha"]
            return 200, {}, {}
        if method == "POST" and path == "/git/refs":
            self.refs[body["ref"].rsplit("/", 1)[1]] = body["sha"]
            return 201, {}, {}
        return 404, None, {}

    def branch_files(self) -> dict[str, bytes]:
        commit = self.commits[self.refs[dashboards.BRANCH]]
        return {item["path"]: self.blobs[item["sha"]] for item in self.trees[commit["tree"]]}


class DashboardsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patches = [
            mock.patch.object(dashboards, "config_path", return_value=self.tmp / "dashboards.json"),
            mock.patch.object(dashboards, "state_path", return_value=self.tmp / "state.json"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.page_a = self.tmp / "a.html"
        self.page_a.write_text("<html><head></head><body>A1</body></html>", encoding="utf-8")
        self.page_b = self.tmp / "b.html"
        self.page_b.write_text("<p>B1</p>", encoding="utf-8")
        self.now = 1_000_000.0
        self.gh = FakeGitHub()

    def publisher(self):
        return dashboards.Publisher(self.gh, {"name": "Work PC"}, clock=lambda: self.now)

    def pages(self):
        return [
            {"id": "alpha", "title": "Alpha", "file": str(self.page_a)},
            {"id": "beta", "title": "Beta", "file": str(self.page_b)},
        ]

    def test_add_list_remove(self):
        dashboards.add_page("Mill ennium", str(self.page_a), "Millennium", 'python "C:/x y/build.py" --fast')
        dashboards.add_page("beta", str(self.page_b))
        pages = dashboards.load_pages()
        self.assertEqual([p["id"] for p in pages], ["millennium", "beta"])
        self.assertEqual(pages[0]["build"], ["python", "C:/x y/build.py", "--fast"])
        self.assertEqual(pages[1]["title"], "Beta")
        dashboards.add_page("beta", str(self.page_a), "Beta 2")  # same id replaces
        self.assertEqual(len(dashboards.load_pages()), 2)
        self.assertTrue(dashboards.remove_page("beta"))
        self.assertFalse(dashboards.remove_page("beta"))

    def test_collect_reads_pages_and_reports_problems(self):
        spawned = []

        def spawn(args, **kwargs):
            spawned.append((args, kwargs))
            return mock.Mock(pid=4321)

        pages = self.pages() + [{"id": "gone", "file": str(self.tmp / "missing.html")}]
        pages[1]["build"] = ["python", "build.py"]
        with mock.patch.object(dashboards, "build_log", return_value=self.tmp / "beta.log"), \
                mock.patch.object(Path, "home", return_value=self.tmp):
            entries, files = dashboards.collect(pages, spawn=spawn, clock=lambda: self.page_b.stat().st_mtime + 60)
        self.assertEqual(set(files), {"alpha.html", "beta.html"})
        self.assertEqual(entries[0]["sha"], dashboards.git_blob_sha(self.page_a.read_bytes()))
        self.assertNotIn("error", entries[0])
        # the build runs in the background with no stdin (never waits on a console), one per page
        self.assertEqual(spawned[0][0], ["python", "build.py"])
        self.assertIs(spawned[0][1]["stdin"], subprocess.DEVNULL)
        self.assertEqual((self.tmp / ".cairn" / "dashboards-build-beta.pid").read_text(), "4321")
        self.assertNotIn("error", entries[1])  # fresh page: no warning
        self.assertIn("no page yet", entries[2]["error"])
        self.assertNotIn("sha", entries[2])

    def test_a_running_build_is_not_started_twice_and_stale_pages_are_flagged(self):
        spawn = mock.Mock(return_value=mock.Mock(pid=1))
        page = {"id": "beta", "file": str(self.page_b), "build": ["python", "build.py"]}
        (self.tmp / ".cairn").mkdir()
        (self.tmp / ".cairn" / "dashboards-build-beta.pid").write_text("999")
        (self.tmp / "beta.log").write_text("Traceback\nValueError: boom\n", encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.tmp), \
                mock.patch.object(dashboards, "build_log", return_value=self.tmp / "beta.log"), \
                mock.patch.object(dashboards, "_alive", return_value=True):
            self.assertEqual(dashboards.start_build(page, spawn), "")
            spawn.assert_not_called()
            entries, _ = dashboards.collect([page], spawn=spawn, clock=lambda: self.page_b.stat().st_mtime + 3600)
        self.assertIn("ValueError: boom", entries[0]["error"])
        self.assertIn("sha", entries[0])  # the old copy is still shown

    def test_blob_sha_matches_git(self):
        self.assertEqual(dashboards.git_blob_sha(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")

    def test_first_publish_creates_an_orphan_branch(self):
        entries, files = dashboards.collect(self.pages())
        self.assertEqual(self.publisher().publish(entries, files), "published")
        commit = self.gh.commits[self.gh.refs[dashboards.BRANCH]]
        self.assertEqual(commit["parents"], [])
        branch = self.gh.branch_files()
        self.assertEqual(branch["alpha.html"], self.page_a.read_bytes())
        status = json.loads(branch["status.json"])
        self.assertEqual(status["pc"], "Work PC")
        self.assertEqual([p["id"] for p in status["pages"]], ["alpha", "beta"])
        self.assertEqual(status["pages"][0]["sha"], dashboards.git_blob_sha(self.page_a.read_bytes()))

    def test_unchanged_pages_are_not_sent_again(self):
        entries, files = dashboards.collect(self.pages())
        self.publisher().publish(entries, files)
        calls = len(self.gh.calls)
        self.now += 60
        self.assertEqual(self.publisher().publish(*dashboards.collect(self.pages())), "unchanged")
        self.assertEqual(len(self.gh.calls), calls)

        self.page_b.write_text("<p>B2</p>", encoding="utf-8")
        self.now += 60
        self.assertEqual(self.publisher().publish(*dashboards.collect(self.pages())), "published")
        blob_posts = [c for c in self.gh.calls[calls:] if c == ("POST", f"/repos/{self.gh.repo}/git/blobs")]
        self.assertEqual(len(blob_posts), 2)  # only beta.html and status.json
        self.assertEqual(self.gh.branch_files()["beta.html"], b"<p>B2</p>")
        self.assertEqual(self.gh.branch_files()["alpha.html"], self.page_a.read_bytes())

    def test_heartbeat_republishes_so_the_phone_knows_the_pc_is_alive(self):
        self.publisher().publish(*dashboards.collect(self.pages()))
        self.now += dashboards.HEARTBEAT_SECONDS + 1
        self.assertEqual(self.publisher().publish(*dashboards.collect(self.pages())), "published")
        self.assertEqual(len(self.gh.commits), 2)
        self.assertTrue(all(commit["parents"] == [] for commit in self.gh.commits.values()))

    def test_github_failure_is_reported(self):
        self.gh.call = lambda *args, **kwargs: (403, {"message": "nope"}, {})
        with self.assertRaises(relay.RelayError):
            self.publisher().publish(*dashboards.collect(self.pages()))
        self.assertFalse((self.tmp / "state.json").exists())  # next run tries again

    def test_phone_link_carries_the_sign_in_in_the_fragment(self):
        with mock.patch.object(relay, "load_config", return_value={"repo": "me/cairn-relay", "token": "sealed"}), \
                mock.patch.object(relay, "open_token", return_value="ghp_example"):
            url, repo = dashboards.phone_link()
        self.assertEqual(repo, "me/cairn-relay")
        self.assertTrue(url.startswith(dashboards.WEB_PAGE + "?repo="))
        self.assertIn("signin=ghp_example", url.split("#", 1)[1])
        self.assertIn("repo=me/cairn-relay", url)

    def test_phone_link_needs_the_relay(self):
        with mock.patch.object(relay, "load_config", return_value=None):
            with self.assertRaises(relay.RelayError):
                dashboards.phone_link()


class DashPageTests(unittest.TestCase):
    """The phone page: frames are sandboxed without same-origin, and the page uses Cairn's sign-in."""

    def test_frames_cannot_reach_the_sign_in(self):
        script = (Path(__file__).resolve().parent.parent / "docs" / "dash.js").read_text(encoding="utf-8")
        sandbox = [line for line in script.splitlines() if 'setAttribute("sandbox"' in line]
        self.assertEqual(len(sandbox), 1)
        self.assertIn('"allow-scripts allow-popups allow-popups-to-escape-sandbox"', sandbox[0])
        self.assertNotIn("allow-same-origin", sandbox[0])
        self.assertIn('from "./auth.js"', script)
        self.assertIn("history.replaceState", script)  # the token leaves the address bar

    def test_page_installs_as_its_own_home_screen_app(self):
        docs = Path(__file__).resolve().parent.parent / "docs"
        page = (docs / "dash.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="manifest" href="dash.webmanifest">', page)
        self.assertIn('name="apple-mobile-web-app-capable" content="yes"', page)
        manifest = json.loads((docs / "dash.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["display"], "standalone")
        self.assertNotIn("start_url", manifest)  # the icon reopens dash.html?repo=..., not Cairn's home
        self.assertIn("dash.webmanifest", (docs / "sw.js").read_text(encoding="utf-8"))

    def test_home_screen_app_can_sign_in_without_safari_storage(self):
        script = (Path(__file__).resolve().parent.parent / "docs" / "dash.js").read_text(encoding="utf-8")
        self.assertIn("data-paste", script)  # paste the sign-in link inside the home-screen app
        self.assertIn("keepRepo", script)  # the repo stays in the address the icon is made from
        auth = (Path(__file__).resolve().parent.parent / "docs" / "auth.js").read_text(encoding="utf-8")
        self.assertIn("cairn_session", auth)  # remembered sign-in mirrored to a cookie iOS copies over


class ScheduleTest(unittest.TestCase):
    def test_schedule_repeats_forever_and_restarts_at_sign_in(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(dashboards.sys, "platform", "win32"), \
                mock.patch.object(dashboards.subprocess, "run", return_value=completed) as run:
            self.assertEqual(dashboards.schedule(5), dashboards.TASK_NAME)
        script = run.call_args.args[0][-1]
        self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes 5)", script)
        self.assertIn("-AtLogOn", script)
        self.assertIn("$logon.Repetition = $trigger.Repetition", script)
        self.assertIn("-Trigger @($trigger, $logon)", script)
        self.assertIn("-StartWhenAvailable", script)
        self.assertIn("-DontStopIfGoingOnBatteries", script)


if __name__ == "__main__":
    unittest.main()

"""cairn dashboards: local dashboard pages copied to the private relay repo for a phone to read."""

import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cairn import dashboards, relay

try:
    from test_relay import FakeGitHub as FakeContents  # the Contents API half (unittest discover -s tests)
except ImportError:
    from tests.test_relay import FakeGitHub as FakeContents


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


class ActionTests(unittest.TestCase):
    """Buttons pressed on the phone: each request runs once, with the page's own command, or not at all."""

    def setUp(self):
        self.now = 1_800_000_000.0
        self.gh = FakeContents()
        self.ran: list[list[str]] = []
        self.refreshed: list[str] = []
        self.result = subprocess.CompletedProcess([], 0, "working\nSent 3 e-mails.\n", "")
        self.page = {"id": "reach", "file": "x.html", "cwd": "E:/reach", "actions": ["python", "E:/reach/phone_action.py"]}
        self.worker = self.make([self.page, {"id": "plain", "file": "y.html"}])

    def make(self, pages):
        def run(args, **options):
            self.ran.append(args)
            self.options = options
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        return dashboards.ActionWorker(self.gh, pages, clock=lambda: self.now, run=run,
                                       refresh=lambda page: self.refreshed.append(page["id"]), threaded=False)

    def press(self, name="promote:retryall", page="reach", age=0.0, request_id="", **extra):
        sent = int((self.now - age) * 1000)
        request_id = request_id or f"{sent:013d}-{len(self.gh.files):04x}"
        request = {"id": request_id, "page": page, "name": name, "at": sent, **extra}
        self.gh.put_file(f"dashboards/{page}/requests/{request_id}.json", json.dumps(request))
        return request_id

    def answer(self, request_id, page="reach"):
        text = self.gh.text(f"dashboards/{page}/results/{request_id}.json")
        return json.loads(text) if text else None

    def test_a_request_runs_once_with_the_pages_command_and_is_answered(self):
        request_id = self.press()
        self.worker.step()
        self.assertEqual(self.ran, [["python", "E:/reach/phone_action.py", "promote:retryall"]])
        self.assertEqual(self.options["cwd"], "E:/reach")
        self.assertIs(self.options["stdin"], subprocess.DEVNULL)
        self.assertNotIn("shell", self.options)  # the name is one argument, never a command line
        answer = self.answer(request_id)
        self.assertEqual((answer["state"], answer["ok"], answer["message"]), ("done", True, "Sent 3 e-mails."))
        self.assertIsNone(self.gh.text(f"dashboards/reach/requests/{request_id}.json"))  # taken
        self.assertEqual(self.refreshed, ["reach"])  # the phone gets a fresh page afterwards
        for _ in range(3):
            self.worker.step()
        self.assertEqual(len(self.ran), 1)

    def test_a_failed_command_tells_its_last_error_line(self):
        self.result = subprocess.CompletedProcess([], 2, "ignored\n", "Traceback\nThat button only works on your PC.\n")
        request_id = self.press("open_folder")
        self.worker.step()
        answer = self.answer(request_id)
        self.assertEqual((answer["ok"], answer["message"]), (False, "That button only works on your PC."))

    def test_a_command_that_never_ends_or_never_starts_is_answered(self):
        self.result = subprocess.TimeoutExpired("x", 600)
        first = self.press()
        self.worker.step()
        self.assertFalse(self.answer(first)["ok"])
        self.assertIn("Still running", self.answer(first)["message"])
        self.result = FileNotFoundError("no python here")
        second = self.press(request_id=f"{int(self.now * 1000):013d}-beef")
        self.worker.step()
        self.assertIn("couldn't start", self.answer(second)["message"])

    def test_old_and_future_requests_are_refused_not_run(self):
        old = self.press(age=dashboards.ACTION_MAX_AGE_S + 5)
        future = self.press(age=-(dashboards.ACTION_CLOCK_SLACK_S + 5))
        fine = self.press(age=-30)  # a phone clock half a minute ahead is normal
        self.worker.step()
        self.assertEqual(len(self.ran), 1)
        for request_id in (old, future):
            answer = self.answer(request_id)
            self.assertFalse(answer["ok"])
            self.assertIn("too old", answer["message"])
            self.assertIsNone(self.gh.text(f"dashboards/reach/requests/{request_id}.json"))
        self.assertTrue(self.answer(fine)["ok"])

    def test_only_a_request_this_worker_deleted_is_run(self):
        request_id = self.press()
        real_call = self.gh.call

        def gone(method, path, body=None, etag=""):  # the phone took it back between our read and delete
            if method == "DELETE":
                self.gh.files.pop(f"dashboards/reach/requests/{request_id}.json", None)
                return 404, {"message": "Not Found"}, {}
            return real_call(method, path, body, etag)

        self.gh.call = gone
        self.worker.step()
        self.assertEqual(self.ran, [])
        self.assertIsNone(self.answer(request_id))

    def test_a_claim_github_refused_is_tried_again_later_and_still_runs_once(self):
        request_id = self.press()
        real_call = self.gh.call
        failing = [True]

        def flaky(method, path, body=None, etag=""):
            if method == "DELETE" and failing[0]:
                return 500, None, {}
            return real_call(method, path, body, etag)

        self.gh.call = flaky
        self.worker.step()
        self.assertEqual(self.ran, [])
        failing[0] = False
        self.worker.step()  # the listing did not change: nothing to see yet
        self.assertEqual(self.ran, [])
        self.now += 61
        self.worker.step()
        self.assertEqual(len(self.ran), 1)
        self.assertTrue(self.answer(request_id)["ok"])

    def test_two_workers_never_run_the_same_request(self):
        other = self.make([self.page])
        request_id = self.press()
        text = self.gh.read(f"dashboards/reach/requests/{request_id}.json")
        self.assertTrue(text)
        other.gh = FakeContents()  # a second PC with its own view of the same files
        other.gh.files = self.gh.files
        self.worker.step()
        other.step()
        self.assertEqual(len(self.ran), 1)

    def test_unreadable_requests_are_refused(self):
        stamp = int(self.now * 1000)
        self.gh.put_file(f"dashboards/reach/requests/{stamp:013d}-aaaa.json", "{not json")
        no_name = self.press(name="", request_id=f"{stamp:013d}-bbbb")
        long_name = self.press(name="x" * (dashboards.ACTION_NAME_MAX + 1), request_id=f"{stamp:013d}-cccc")
        control = self.press(name="send\nnow", request_id=f"{stamp:013d}-dddd")
        self.gh.put_file("dashboards/reach/requests/notes.json", json.dumps({"name": "promote:retryall", "at": stamp}))
        self.worker.step()
        self.assertEqual(self.ran, [])
        self.assertIn("couldn't read", self.answer(f"{stamp:013d}-aaaa")["message"])
        for request_id in (no_name, long_name, control):
            self.assertFalse(self.answer(request_id)["ok"])
        self.assertIsNone(self.answer("notes"))  # not an id the phone page makes: removed, never answered
        self.assertEqual([p for p in self.gh.files if "/requests/" in p and p.endswith(".json")], [])

    def test_the_request_cannot_choose_another_page_or_command(self):
        request_id = self.press(page="reach", command="format c:", actions=["evil"])
        json_path = f"dashboards/reach/requests/{request_id}.json"
        request = json.loads(self.gh.text(json_path))
        request["page"] = "plain"
        self.gh.put_file(json_path, json.dumps(request))
        self.worker.step()
        self.assertEqual(self.ran, [["python", "E:/reach/phone_action.py", "promote:retryall"]])

    def test_pages_without_a_command_are_never_listened_to(self):
        self.press(page="plain")
        self.worker.step()
        self.assertEqual(self.ran, [])
        self.assertFalse(any("/plain/" in path for _, path in self.gh.calls))
        self.assertIsNotNone(self.gh.text([p for p in self.gh.files if "/plain/requests/" in p][0]))

    def test_an_idle_worker_costs_one_unchanged_listing(self):
        self.worker.step()  # no folder yet: it is made, so later listings can answer "unchanged"
        self.assertEqual(self.gh.text("dashboards/reach/requests/.keep"), "")
        self.worker.step()
        self.gh.calls.clear()
        self.worker.step()
        self.assertEqual(self.gh.calls, [("GET", "/repos/me/cairn-relay/contents/dashboards/reach/requests")])

    def test_busy_worker_refuses_more_than_a_few_at_once(self):
        busy = mock.Mock()
        busy.is_alive.return_value = True
        self.worker.running = [busy] * dashboards.ACTIONS_AT_ONCE
        request_id = self.press()
        self.worker.poll()
        self.assertEqual(self.ran, [])
        self.assertIn("busy", self.answer(request_id)["message"])

    def test_old_answers_are_swept(self):
        old = f"{int((self.now - dashboards.RESULT_KEEP_S - 60) * 1000):013d}-aaaa"
        fresh = f"{int(self.now * 1000):013d}-bbbb"
        for request_id in (old, fresh):
            self.gh.put_file(f"dashboards/reach/results/{request_id}.json", "{}")
        self.assertEqual(self.worker.sweep(), 1)
        self.assertIsNone(self.answer(old))
        self.assertEqual(self.answer(fresh), {})

    def test_threaded_work_is_answered_too(self):
        self.worker.threaded = True
        request_id = self.press()
        self.worker.step()
        for thread in self.worker.running:
            thread.join(10)
        self.assertTrue(self.answer(request_id)["ok"])
        self.assertEqual(self.refreshed, ["reach"])


class ActionSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patch = mock.patch.object(dashboards, "config_path", return_value=self.tmp / "dashboards.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.page = self.tmp / "a.html"
        self.page.write_text("<p>A</p>", encoding="utf-8")

    def test_add_stores_the_command_and_the_phone_is_told_the_page_takes_actions(self):
        dashboards.add_page("reach", str(self.page), build="python build.py", actions='python "E:/x y/phone_action.py"')
        dashboards.add_page("plain", str(self.page))
        pages = dashboards.load_pages()
        self.assertEqual(pages[0]["actions"], ["python", "E:/x y/phone_action.py"])
        self.assertNotIn("actions", pages[1])
        spawn = mock.Mock()
        entries, _ = dashboards.collect(pages, spawn=spawn, build=False)
        spawn.assert_not_called()  # build=False: the page was just rebuilt by the worker
        self.assertIs(entries[0]["actions"], True)
        self.assertNotIn("actions", entries[1])
        self.assertNotIn("phone_action", json.dumps(entries))  # the command itself never leaves the PC

    def test_refresh_builds_the_page_then_publishes_without_building_again(self):
        ran = []
        with mock.patch.object(dashboards, "publish") as publish:
            dashboards.refresh_page({"id": "reach", "build": ["python", "build.py"], "cwd": "E:/reach"},
                                    run=lambda args, **options: ran.append((args, options["cwd"])))
        self.assertEqual(ran, [(["python", "build.py"], "E:/reach")])
        publish.assert_called_once_with(build=False)

    def test_worker_task_is_looked_after_every_minute_and_has_no_time_limit(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(dashboards.sys, "platform", "win32"), \
                mock.patch.object(dashboards.subprocess, "run", return_value=completed) as run:
            self.assertEqual(dashboards.schedule_actions(), dashboards.ACTIONS_TASK_NAME)
        script = run.call_args.args[0][-1]
        self.assertIn("'-m cairn dashboards actions'", script)
        self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes 1)", script)
        self.assertIn("-ExecutionTimeLimit ([TimeSpan]::Zero)", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)  # one worker at a time
        self.assertIn("-AtLogOn", script)

    def test_the_worker_makes_room_when_cairn_updates_itself(self):
        dashboards.add_page("reach", str(self.page), actions="python phone_action.py")
        stamps = iter([1.0, 1.0, 2.0])
        steps = []
        said = []
        with mock.patch.object(relay, "load_config", return_value={"repo": "me/cairn-relay"}), \
                mock.patch.object(relay, "connect", return_value=FakeContents()), \
                mock.patch.object(dashboards, "_code_stamp", side_effect=lambda: next(stamps)), \
                mock.patch.object(dashboards, "ACTIONS_POLL_S", 0), \
                mock.patch.object(dashboards.ActionWorker, "step", lambda worker: steps.append(1)):
            dashboards.run_actions(log=said.append)
        self.assertEqual(len(steps), 2)
        self.assertIn("updated", said[-1])

    def test_the_worker_needs_a_page_with_a_command(self):
        dashboards.add_page("plain", str(self.page))
        with mock.patch.object(relay, "load_config", return_value={"repo": "me/cairn-relay"}), \
                mock.patch.object(relay, "connect", return_value=FakeContents()):
            with self.assertRaises(ValueError):
                dashboards.run_actions(log=lambda line: None)


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

    def test_buttons_are_sent_for_the_frame_that_asked_and_only_when_the_pc_takes_them(self):
        script = (Path(__file__).resolve().parent.parent / "docs" / "dash.js").read_text(encoding="utf-8")
        self.assertIn("frame.iframe.contentWindow === event.source", script)  # which page: never from the message
        self.assertIn("if (!page?.actions)", script)
        self.assertIn("/contents/dashboards/${encodeURIComponent(page.id)}", script)
        self.assertIn("/requests/${id}.json", script)
        self.assertIn("/results/${id}.json", script)
        self.assertIn('method: "DELETE"', script)  # a request the PC didn't take is taken back
        helper = script.split("const HELPER = `", 1)[1].split("`;", 1)[0]
        self.assertNotIn("authFetch", helper)  # the frame's own script never talks to GitHub
        self.assertIn("window.name", helper)  # what the page remembers survives a live update

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

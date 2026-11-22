"""Priority: the running sessions in the order their user gave them (cairn/rank.py, docs/rank.js)."""

import contextlib
import datetime as dt
import http.client
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import launch_browser, start_static_server  # noqa: E402

from cairn import cli, core, rank, vibe  # noqa: E402
from cairn.brain import Brain  # noqa: E402
from cairn.server import make_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

MAC = "MacBook Pro 15,4"
ASUS = "ASUS EXPERTBOOK P5405CSA"
NEON = "Neon Coast 1.6: touch controls"
LAB = "Lab writer"
BITS = "Bits and Tickers interview"

FILE_TEXTS = [
    "",
    "no list here\n",
    "1. 2026-11-22-a\n2. 2026-11-22-b\n",
    "---\ntype: settings\n---\n\n# Cairn priority\n\ntext\n\n1. b\n2. a\n3. b\n",
    "  3) sessions/2026-11-22-c.md  \r\n1. [[2026-11-22-a|the first]]\r\n- not a line\n2.no space\n10. z\n",
    "1. sessions\\2026-11-22-d.MD\n2. \n3.   spaced name  \n",
]
ORDERS = [[], ["a"], ["2026-11-22-b", "2026-11-22-a", "x"]]


class RankCase(unittest.TestCase):
    """A brain two computers share: this one is the Mac. Three sessions run, one of them on the ASUS."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.brain = Brain(self.tmp / "repo", "brain")
        self.brain.init()
        patch = mock.patch.dict(os.environ, {"CAIRN_DEVICE": MAC, "CAIRN_HOME": str(self.tmp / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # started a minute apart, so the order they started in is known: Neon, Lab, Bits
        self.neon = self.session(NEON, "09:00")
        self.lab = self.session(LAB, "09:01", device=ASUS)
        self.bits = self.session(BITS, "09:02")

    def session(self, title: str, clock: str, device: str = MAC, day: str = "") -> str:
        day = day or dt.date.today().isoformat()
        with mock.patch.dict(os.environ, {"CAIRN_DEVICE": device}):
            note = vibe.start(self.brain, title, agent="vector")
        text = core.update_note_text(note["text"], {"started": f"{day}T{clock}"})
        text = re.sub(r"^- \d\d:\d\d start:", f"- {clock} start:", text, flags=re.M)
        self.brain.write(note["path"], text, note["mtime"])
        return note["path"].rsplit("/", 1)[-1][:-3]

    def now(self, clock: str = "09:30") -> dt.datetime:
        return dt.datetime.fromisoformat(f"{dt.date.today().isoformat()}T{clock}")

    def titles(self, reading: dict | None = None) -> list[str]:
        return [entry["title"] for entry in (reading or rank.status(self.brain, self.now()))["sessions"]]

    def order_file(self) -> list[str]:
        return rank.parse(self.brain.read(rank.FILE)["text"])


class OrderTests(RankCase):
    def test_until_one_is_placed_they_stand_in_the_order_they_started(self):
        reading = rank.status(self.brain, self.now())
        self.assertFalse(reading["ranked"])
        self.assertEqual(self.titles(reading), [NEON, LAB, BITS])
        self.assertEqual([entry["place"] for entry in reading["sessions"]], [1, 2, 3])
        self.assertIsNone(rank.file(self.brain), "looking at the order writes nothing")

    def test_one_move_to_the_top_puts_every_session_in_the_file(self):
        after = rank.move(self.brain, self.bits, "top", self.now())
        self.assertTrue(after["ranked"])
        self.assertEqual(self.titles(after), [BITS, NEON, LAB])
        self.assertEqual(self.order_file(), [self.bits, self.neon, self.lab])
        self.assertIn("most important one now", after["message"])

    def test_up_down_bottom_and_a_number(self):
        rank.move(self.brain, self.lab, "up", self.now())
        self.assertEqual(self.titles(), [LAB, NEON, BITS])
        rank.move(self.brain, self.lab, "down", self.now())
        self.assertEqual(self.titles(), [NEON, LAB, BITS])
        rank.move(self.brain, self.neon, "bottom", self.now())
        self.assertEqual(self.titles(), [LAB, BITS, NEON])
        after = rank.move(self.brain, self.neon, "2", self.now())
        self.assertEqual(self.titles(), [LAB, NEON, BITS])
        self.assertIn("number 2 of 3", after["message"])
        rank.move(self.brain, self.lab, "99", self.now())
        self.assertEqual(self.titles(), [NEON, BITS, LAB], "a number past the end is the bottom")

    def test_the_ends_stay_where_they_are(self):
        rank.move(self.brain, self.neon, "up", self.now())
        rank.move(self.brain, self.bits, "down", self.now())
        self.assertEqual(self.titles(), [NEON, LAB, BITS])

    def test_a_few_words_of_its_goal_find_the_session_on_any_computer(self):
        rank.move(self.brain, "lab writ", "top", self.now())
        self.assertEqual(self.titles()[0], LAB, "the order is one for every computer's sessions")

    def test_words_that_fit_none_or_two_are_refused_with_a_sentence(self):
        self.session("Neon Coast 1.7: sound", "09:03")
        with self.assertRaisesRegex(rank.RankError, "more than one running session"):
            rank.move(self.brain, "neon coast", "top", self.now())
        with self.assertRaisesRegex(rank.RankError, "No running session fits"):
            rank.move(self.brain, "no such thing", "top", self.now())
        with self.assertRaisesRegex(rank.RankError, "Where to"):
            rank.move(self.brain, self.neon, "sideways", self.now())
        self.assertIsNone(rank.file(self.brain), "a refusal writes nothing")

    def test_a_new_session_joins_at_the_bottom_until_it_is_moved(self):
        rank.move(self.brain, self.bits, "top", self.now())
        new = self.session("Tilik on the ASUS", "08:00")  # started before every other one, and still last
        reading = rank.status(self.brain, self.now())
        self.assertEqual(self.titles(reading), [BITS, NEON, LAB, "Tilik on the ASUS"])
        self.assertEqual([entry["listed"] for entry in reading["sessions"]], [True, True, True, False])
        self.assertIn("new, not placed yet", rank.summary(reading))
        rank.move(self.brain, new, "up", self.now())
        self.assertEqual(self.order_file(), [self.bits, self.neon, new, self.lab])

    def test_a_session_that_ended_leaves_the_order_and_the_file_at_the_next_change(self):
        rank.move(self.brain, self.lab, "top", self.now())
        with mock.patch.dict(os.environ, {"CAIRN_DEVICE": ASUS}):
            vibe.end(self.brain, "shipped", "done", session_slug=self.lab)
        self.assertEqual(self.titles(), [NEON, BITS])
        self.assertIn(self.lab, self.order_file(), "the file is not rewritten behind the user's back")
        rank.move(self.brain, self.bits, "top", self.now())
        self.assertEqual(self.order_file(), [self.bits, self.neon])

    def test_a_session_left_open_for_a_day_is_not_running(self):
        yesterday = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        self.session("Forgotten", "09:00", day=yesterday)
        self.assertEqual(self.titles(), [NEON, LAB, BITS])

    def test_an_order_of_ended_sessions_only_is_no_order(self):
        self.brain.write(rank.FILE, rank.file_text(["2026-01-01-long-gone"]), None)
        reading = rank.status(self.brain, self.now())
        self.assertFalse(reading["ranked"])
        self.assertEqual(rank.notice(self.brain, self.neon, self.now()), "")

    def test_set_order_takes_a_whole_list_and_keeps_what_it_leaves_out(self):
        after = rank.set_order(self.brain, [self.bits, "not-running", self.bits], self.now())
        self.assertEqual(self.titles(after), [BITS, NEON, LAB])
        self.assertEqual(self.order_file(), [self.bits, self.neon, self.lab])

    def test_reset_forgets_the_order(self):
        rank.move(self.brain, self.bits, "top", self.now())
        after = rank.reset(self.brain)
        self.assertFalse(after["ranked"])
        self.assertEqual(self.order_file(), [])
        self.assertEqual(rank.notice(self.brain, self.bits, self.now()), "")

    def test_a_file_edited_by_hand_is_read(self):
        self.brain.write(rank.FILE, f"my own words\n\n1. [[{self.lab}]]\n2) sessions/{self.bits}.md\n", None)
        self.assertEqual(self.titles(), [LAB, BITS, NEON])

    def test_the_order_file_is_not_a_note(self):
        rank.move(self.brain, self.bits, "top", self.now())
        self.assertIn(rank.FILE, core.SPECIAL_FILES)
        self.assertNotIn(rank.FILE, [note["path"] for note in self.brain.notes()])


class NoticeTests(RankCase):
    def test_nothing_is_said_until_the_user_has_placed_one(self):
        for name in (self.neon, self.lab, self.bits, ""):
            self.assertEqual(rank.notice(self.brain, name, self.now()), "")

    def test_the_first_is_told_it_is_the_most_important(self):
        rank.move(self.brain, self.bits, "top", self.now())
        text = rank.notice(self.brain, self.bits, self.now())
        self.assertTrue(text.startswith("PRIORITY (set in Cairn): this chat is number 1 of 3 running sessions, the most important one."))
        self.assertIn("The order is the user's.", text)
        self.assertNotIn("Before it", text)

    def test_the_others_are_told_their_number_and_who_comes_before(self):
        rank.move(self.brain, self.lab, "top", self.now())
        text = rank.notice(self.brain, f"sessions/{self.bits}.md", self.now())
        self.assertIn("this chat is number 3 of 3 running sessions.", text)
        self.assertIn(f"Before it: 1. {LAB} (on {ASUS}); 2. {NEON}.", text)
        self.assertIn("the lower number goes first", text)

    def test_a_long_line_before_it_names_the_first_five(self):
        extra = [self.session(f"Extra {number}", f"09:1{number}") for number in range(5)]
        rank.move(self.brain, extra[-1], "bottom", self.now())
        text = rank.notice(self.brain, extra[-1], self.now())
        self.assertIn("number 8 of 8", text)
        self.assertIn("; and 2 more.", text)

    def test_a_new_session_is_told_it_is_not_placed_yet(self):
        rank.move(self.brain, self.bits, "top", self.now())
        new = self.session("Tilik on the ASUS", "09:20")
        self.assertIn("It is new and not placed yet, so it comes last for now.", rank.notice(self.brain, new, self.now()))

    def test_an_ended_session_and_a_lone_one_are_told_nothing(self):
        rank.move(self.brain, self.bits, "top", self.now())
        vibe.end(self.brain, "shipped", "done", session_slug=self.neon)
        self.assertEqual(rank.notice(self.brain, self.neon, self.now()), "")
        with mock.patch.dict(os.environ, {"CAIRN_DEVICE": ASUS}):
            vibe.end(self.brain, "shipped", "done", session_slug=self.lab)
        self.assertEqual(rank.notice(self.brain, self.bits, self.now()), "", "one running session has nobody to give way to")


class CommandTests(RankCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--root", str(self.brain.root), *args])
        return code, out.getvalue(), err.getvalue()

    def test_rank_lists_them_and_says_none_is_placed(self):
        code, out, _ = self.run_cli("vibe", "rank")
        self.assertEqual(code, 0)
        self.assertIn("None is placed yet", out)
        self.assertLess(out.index(NEON), out.index(BITS))

    def test_rank_moves_one_and_prints_the_new_order(self):
        code, out, _ = self.run_cli("vibe", "rank", "bits", "and", "tickers", "--to", "top")
        self.assertEqual(code, 0)
        self.assertIn(f"{BITS} is the most important one now.", out)
        self.assertIn("The running sessions, the most important first:", out)
        self.assertEqual(self.order_file()[0], self.bits)
        code, out, _ = self.run_cli("vibe", "rank", "--session", self.bits, "--to", "bottom")
        self.assertEqual((code, self.order_file()[-1]), (0, self.bits))

    def test_a_step_logged_after_that_tells_the_chat_its_place(self):
        self.run_cli("vibe", "rank", self.lab, "--to", "top")
        code, out, _ = self.run_cli("vibe", "log", "a step", "--session", self.bits)
        self.assertEqual(code, 0)
        self.assertIn("Logged.", out)
        self.assertIn("PRIORITY (set in Cairn): this chat is number 3 of 3", out)
        _, started, _ = self.run_cli("vibe", "start", "One more")
        self.assertIn("this chat is number 4 of 4 running sessions. It is new and not placed yet", started)

    def test_a_step_logged_before_any_order_says_nothing_of_it(self):
        _, out, _ = self.run_cli("vibe", "log", "a step", "--session", self.bits)
        self.assertNotIn("PRIORITY", out)

    def test_reset_and_refusals(self):
        self.run_cli("vibe", "rank", self.lab, "--to", "top")
        code, out, _ = self.run_cli("vibe", "rank", "--reset")
        self.assertEqual(code, 0)
        self.assertIn("The order is forgotten.", out)
        code, _, err = self.run_cli("vibe", "rank", "nothing like it", "--to", "top")
        self.assertEqual(code, 1)
        self.assertIn("No running session fits", err)
        code, _, err = self.run_cli("vibe", "rank", "--to", "top")
        self.assertEqual(code, 1)
        self.assertIn("Which session?", err)


class ServerTests(RankCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.brain, port=0)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"X-Cairn": "1", "Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}"}
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read() or b"{}")
        connection.close()
        return response.status, data

    def test_the_live_page_gets_the_order_file(self):
        status, data = self.call("GET", "/api/vibe")
        self.assertEqual(status, 200)
        self.assertIsNone(data["rank"])
        rank.move(self.brain, self.bits, "top", self.now())
        _, data = self.call("GET", "/api/vibe")
        self.assertEqual(data["rank"]["path"], rank.FILE)
        self.assertEqual(rank.parse(data["rank"]["text"]), [self.bits, self.neon, self.lab])


@unittest.skipUnless(sync_playwright, "needs playwright")
class ParityTests(unittest.TestCase):
    """docs/rank.js and cairn/rank.py read and write the file the same way, and sort the same way."""

    @classmethod
    def setUpClass(cls):
        cls.server, base = start_static_server()
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)
        cls.page = cls.browser.new_page()
        cls.page.goto(base + "index.html")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def js(self, body: str, argument):
        return self.page.evaluate(f"async (input) => {{ const rank = await import('./rank.js'); {body} }}", argument)

    def test_parse(self):
        for text in FILE_TEXTS:
            with self.subTest(text=text):
                self.assertEqual(self.js("return rank.parseRank(input);", text), rank.parse(text))

    def test_file_text_and_back(self):
        for names in ORDERS:
            with self.subTest(names=names):
                self.assertEqual(self.js("return rank.rankText(input);", names), rank.file_text(names))
                self.assertEqual(rank.parse(rank.file_text(names)), names)

    def test_arrange_and_running(self):
        def note(slug: str, started: str, status: str = "live", log: str = "") -> dict:
            text = f"---\ntitle: {slug}\ntype: session\nstatus: {status}\nstarted: {started}\n---\n## Log\n\n{log}"
            return {"path": f"sessions/{slug}.md", "text": text}

        files = [
            note("c", "2026-11-22T09:00"),
            note("a", "2026-11-22T09:00"),
            note("b", "2026-11-22T08:00"),
            note("listed-last", "2026-11-22T07:00"),
            note("ended", "2026-11-22T07:30", status="shipped"),
            note("left-open", "2026-11-20T09:00", log="- 09:05 note: one step\n"),
            note("old-but-busy", "2026-11-20T09:00", log="- 09:05 note: one\n- 08:00 note: next day\n- 07:59 note: the day after\n"),
        ]
        names = ["listed-last", "ended", "nobody", "a"]
        stamp = "2026-11-22T10:00"
        notes = [core.note_from_file(item["path"], item["text"]) for item in files]
        minutes = core.stamp_minutes(stamp)
        expected = [entry["slug"] for entry in rank.arrange([entry for entry in notes if rank.is_running(entry, minutes)], names)]
        self.assertEqual(expected, ["listed-last", "a", "old-but-busy", "b", "c"])
        found = self.js(
            """const core = await import('./core.js');
            const notes = input.files.map((file) => core.noteFromFile(file.path, file.text, 0));
            const now = core.stampMinutes(input.stamp);
            return rank.arrange(notes.filter((note) => rank.isRunning(note, now)), input.names).map((note) => note.slug);""",
            {"files": files, "names": names, "stamp": stamp},
        )
        self.assertEqual(found, expected)

    def test_the_page_names_the_same_file(self):
        self.assertEqual(self.js("return rank.RANK_FILE;", None), rank.FILE)
        self.assertEqual(self.js("return (await import('./recap.js')).STALE_MIN;", None), rank.STALE_MIN)


@unittest.skipUnless(sync_playwright, "needs playwright")
class PageTests(RankCase):
    """The Vibe page: one list of the running sessions, moved with Top, up and down or by dragging."""

    def setUp(self):
        super().setUp()
        self.server = make_server(self.brain, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = launch_browser(self.pw)
        self.addCleanup(self.browser.close)
        self.errors: list[str] = []

    def open(self, viewport=(1280, 1200), touch=False):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, has_touch=touch, is_mobile=touch)
        self.addCleanup(context.close)
        page = context.new_page()
        page.add_init_script("window.CAIRN_TEST_POLL_MS = 150; window.CAIRN_TEST_SAVE_MS = 50")
        page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/#/vibe")
        page.wait_for_selector(".vibe-rank")
        return page

    @staticmethod
    def shown(page) -> list[str]:
        return page.locator(".vibe-rank .rank-title").all_inner_texts()

    def wait_for_file(self, page, expected: list[str]):
        page.wait_for_function("() => !document.querySelector('.rank-row.unplaced')")
        for _ in range(100):
            if rank.file(self.brain) and self.order_file() == expected:
                return
            page.wait_for_timeout(50)
        self.fail(f"the brain holds {rank.listed(self.brain)}, not {expected}")

    def test_the_block_lists_every_computers_running_sessions_and_says_none_is_placed(self):
        page = self.open()
        block = page.locator(".vibe-rank")
        self.assertEqual(self.shown(page), [NEON, LAB, BITS])
        self.assertIn("3 running · not arranged yet", block.locator("header").inner_text())
        self.assertIn(ASUS, block.locator(".rank-row", has_text=LAB).inner_text(), "with two computers a row says which one")
        self.assertEqual(block.locator(".rank-row.unplaced").count(), 3)
        self.assertEqual(block.locator("[data-action=vibe-rank-reset]").count(), 0)
        self.assertEqual(page.locator(".rc-rank").count(), 0, "the live recap has no numbers before there is an order")
        self.assertIsNone(rank.file(self.brain), "opening the page writes nothing")
        self.assertEqual(self.errors, [])

    def test_top_up_and_down_move_a_row_and_the_brain_keeps_the_order(self):
        for viewport, touch in (((1280, 1200), False), ((390, 844), True)):
            with self.subTest(viewport=viewport):
                rank.reset(self.brain)
                page = self.open(viewport, touch)
                row = lambda title: page.locator(".vibe-rank .rank-row", has_text=title)  # noqa: E731
                row(BITS).get_by_role("button", name=f"Most important: {BITS}").click()
                self.assertEqual(self.shown(page), [BITS, NEON, LAB])
                self.wait_for_file(page, [self.bits, self.neon, self.lab])
                self.assertIn("3 running · most important first", page.locator(".vibe-rank header").inner_text())
                self.assertTrue(row(BITS).get_by_role("button", name=f"Most important: {BITS}").is_disabled())
                self.assertTrue(row(LAB).get_by_role("button", name=f"Less important: {LAB}").is_disabled())

                row(LAB).get_by_role("button", name=f"More important: {LAB}").click()
                row(BITS).get_by_role("button", name=f"Less important: {BITS}").click()
                self.assertEqual(self.shown(page), [LAB, BITS, NEON])
                self.wait_for_file(page, [self.lab, self.bits, self.neon])
                self.assertEqual(self.titles(), [LAB, BITS, NEON], "the command line reads the same order")
                if touch:
                    box = row(NEON).locator(".rank-move").first.bounding_box()
                    self.assertGreaterEqual(min(box["width"], box["height"]), 44, "a finger can hit the button")
                    self.assertFalse(row(NEON).locator(".rank-grip").is_visible(), "no handle to drag with a finger")
                    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0, "the page does not scroll sideways")
                page.close()
        self.assertEqual(self.errors, [])

    def test_the_live_recap_follows_the_order_and_numbers_its_rows(self):
        page = self.open()
        page.locator(".vibe-rank .rank-row", has_text=LAB).get_by_role("button", name=f"Most important: {LAB}").click()
        page.wait_for_selector(".rc-rank")
        rows = page.locator(".rc-live .rc-row")
        self.assertEqual(rows.locator(".rc-title").all_inner_texts(), [LAB, NEON, BITS])
        self.assertEqual(rows.locator(".rc-rank").all_inner_texts(), ["1", "2", "3"])
        self.assertEqual(self.errors, [])

    def test_dragging_a_row_by_its_handle_moves_it(self):
        page = self.open()
        grip = page.locator(".vibe-rank .rank-row", has_text=BITS).locator(".rank-grip")
        target = page.locator(".vibe-rank .rank-row", has_text=NEON)
        box = target.bounding_box()
        grip.drag_to(target, target_position={"x": box["width"] / 2, "y": 3})
        page.wait_for_function(f"document.querySelector('.vibe-rank .rank-title')?.textContent === {json.dumps(BITS)}")
        self.assertEqual(self.shown(page), [BITS, NEON, LAB])
        self.wait_for_file(page, [self.bits, self.neon, self.lab])
        self.assertEqual(self.errors, [])

    def test_an_order_made_in_a_terminal_shows_on_the_page_and_so_does_a_new_session(self):
        page = self.open()
        rank.move(self.brain, self.bits, "top", self.now())
        page.wait_for_function(f"document.querySelector('.vibe-rank .rank-title')?.textContent === {json.dumps(BITS)}")
        self.assertEqual(self.shown(page), [BITS, NEON, LAB])
        self.session("Tilik on the ASUS", "09:20", device=ASUS)
        page.wait_for_function("document.querySelectorAll('.vibe-rank .rank-row').length === 4")
        last = page.locator(".vibe-rank .rank-row").last
        self.assertIn("new, not placed yet", last.inner_text())
        self.assertIn("unplaced", last.get_attribute("class"))
        self.assertEqual(self.errors, [])

    def test_forget_the_order_puts_the_list_back(self):
        rank.move(self.brain, self.bits, "top", self.now())
        page = self.open()
        self.assertEqual(self.shown(page), [BITS, NEON, LAB])
        page.locator("[data-action=vibe-rank-reset]").click()
        page.wait_for_function("document.querySelectorAll('.rank-row.unplaced').length === 3")
        self.assertEqual(self.shown(page), [NEON, LAB, BITS])
        for _ in range(100):
            if self.order_file() == []:
                break
            page.wait_for_timeout(50)
        self.assertEqual(self.order_file(), [])
        self.assertEqual(page.locator(".rc-rank").count(), 0)
        self.assertEqual(self.errors, [])

    def test_folded_the_block_names_the_most_important_one(self):
        rank.move(self.brain, self.lab, "top", self.now())
        page = self.open()
        page.locator('.vp-toggle[data-panel="priority"]').click()
        summary = page.locator('.vibe-panel[data-panel="priority"] .vp-sum')
        summary.wait_for()
        self.assertEqual(summary.inner_text(), f"1. {LAB} · 3 running")
        self.assertEqual(self.errors, [])

    def test_one_running_session_has_no_block(self):
        vibe.end(self.brain, "shipped", "done", session_slug=self.neon)
        vibe.end(self.brain, "shipped", "done", session_slug=self.bits)
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/#/vibe")
        page.wait_for_selector(".vibe-recap")
        self.assertEqual(page.locator(".vibe-rank").count(), 0)


if __name__ == "__main__":
    unittest.main()

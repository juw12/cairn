"""Hand over: another computer does the work of this computer's chats for a while (cairn/handover.py)."""

import contextlib
import datetime as dt
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import launch_browser  # noqa: E402

from cairn import cli, connect, core, handoff, handover, vibe  # noqa: E402
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
OFFICE = "Office PC"


class HandoverCase(unittest.TestCase):
    """A brain two computers share: this one is the Mac, and the ASUS has a session note of its own."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.brain = Brain(self.tmp / "repo", "brain")
        self.brain.init()
        patches = [
            mock.patch.dict(os.environ, {"CAIRN_DEVICE": MAC, "CAIRN_HOME": str(self.home)}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(handover._soon.clear)
        self.other_session(ASUS, "Lab writer")
        self.session = self.slug(vibe.start(self.brain, "Neon Coast 1.6: touch controls", agent="vector"))
        vibe.log(self.brain, "[40%] joystick drawn", session=self.session)

    @staticmethod
    def slug(note: dict) -> str:
        return note["path"].rsplit("/", 1)[-1][:-3]

    def other_session(self, device: str, title: str) -> str:
        with mock.patch.dict(os.environ, {"CAIRN_DEVICE": device}):
            return self.slug(vibe.start(self.brain, title, agent="vector"))

    def note(self, slug: str) -> dict:
        path = f"sessions/{slug}.md"
        return core.note_from_file(path, self.brain.read(path)["text"])

    def threads(self) -> list[dict]:
        return handoff.threads(self.brain)


class SwitchTests(HandoverCase):
    def test_the_work_is_done_here_until_it_is_switched(self):
        reading = handover.status(self.brain)
        self.assertEqual((reading["device"], reading["to"], reading["others"], reading["sessions"]), (MAC, "", [ASUS], []))
        self.assertEqual(handover.notice(self.brain), "")
        self.assertEqual(handover.notice(self.brain, self.session), "")

    def test_one_switch_hands_every_chat_over_and_one_takes_it_back(self):
        reading = handover.switch(self.brain, "asus", now=dt.datetime(2026, 11, 22, 10, 40))
        self.assertEqual((reading["to"], reading["since"]), (ASUS, "2026-11-22T10:40"))
        self.assertIn(ASUS, reading["message"])
        self.assertEqual(handover.status(self.brain)["to"], ASUS)
        self.assertEqual(self.threads(), [], "the switch sends nothing: each chat hands its own work over")

        back = handover.switch(self.brain, "here")
        self.assertEqual((back["to"], back["since"]), ("", ""))
        self.assertIn("done here again", back["message"])
        self.assertEqual(handover.notice(self.brain), "")

    def test_a_chat_is_told_what_to_do_while_it_is_on(self):
        handover.switch(self.brain, ASUS, now=dt.datetime(2026, 11, 22, 10, 40))
        text = handover.notice(self.brain)
        self.assertTrue(text.startswith("HAND-OVER IS ON"))
        self.assertIn(f'cairn handoff send "title" --to "{ASUS}"', text)
        self.assertIn("2026-11-22 10:40", text)
        self.assertIn("only what needs this computer", text)
        self.assertIn("cairn vibe hand here", text)

    def test_pressing_the_same_computer_twice_keeps_since(self):
        handover.switch(self.brain, ASUS, now=dt.datetime(2026, 11, 22, 10, 40))
        again = handover.switch(self.brain, "asus", now=dt.datetime(2026, 11, 22, 11, 5))
        self.assertEqual(again["since"], "2026-11-22T10:40")

    def test_the_setting_belongs_to_this_computer_and_this_brain(self):
        handover.switch(self.brain, ASUS)
        saved = json.loads((self.home / "handover.json").read_text(encoding="utf-8"))
        self.assertEqual(list(saved["brains"]), [str(self.brain.dir)])
        other = Brain(self.tmp / "other", "brain")
        other.init()
        self.assertEqual(handover.status(other)["to"], "")
        self.assertFalse(any("handover" in file["path"] for file in self.brain.files()), "nothing of it is in the brain")

    def test_a_computer_nobody_knows_is_refused_by_name(self):
        with self.assertRaises(handover.HandoverError) as caught:
            handover.switch(self.brain, "thinkpad")
        self.assertIn(ASUS, str(caught.exception))
        self.assertNotIn('"all"', str(caught.exception))
        with self.assertRaises(handover.HandoverError):
            handover.switch(self.brain, "all")

    def test_words_that_fit_two_computers_are_asked_about(self):
        self.other_session("ASUS Vivobook", "Other laptop")
        with self.assertRaises(handover.HandoverError) as caught:
            handover.switch(self.brain, "asus")
        self.assertIn("more than one", str(caught.exception))
        self.assertEqual(handover.switch(self.brain, "vivobook")["to"], "ASUS Vivobook")

    def test_its_own_name_means_here(self):
        handover.switch(self.brain, ASUS)
        self.assertEqual(handover.switch(self.brain, MAC)["to"], "")

    def test_the_live_page_gets_a_kept_reading_that_a_switch_renews(self):
        first = handover.status_soon(self.brain)
        self.assertIs(handover.status_soon(self.brain), first)
        handover.switch(self.brain, ASUS)
        self.assertEqual(handover.status_soon(self.brain)["to"], ASUS)


class OneSessionTests(HandoverCase):
    def hand(self, to: str = "asus") -> dict:
        return handover.hand_session(self.brain, self.session, to, now=dt.datetime(2026, 11, 22, 10, 41, 7))

    def test_handing_a_session_leaves_a_handoff_with_its_goal_and_log(self):
        result = self.hand()
        self.assertEqual((result["session"], result["to"]), (self.session, ASUS))
        (thread,) = self.threads()
        self.assertEqual((thread["id"], thread["from"], thread["to"], thread["open"]), (result["thread"], MAC, [ASUS], True))
        self.assertEqual(thread["title"], "Take over: Neon Coast 1.6: touch controls")
        self.assertIn("take-over-neon-coast-1-6-touch-controls", thread["id"])
        body = handoff.body_of(self.brain, result["handoff"])
        self.assertIn(f"Session note: sessions/{self.session}.md", body)
        self.assertIn("agent vector", body)
        self.assertIn("start: Neon Coast 1.6: touch controls", body)
        self.assertIn("[40%] joystick drawn", body)
        self.assertIn('Say "picked up" when you start', body)

    def test_the_session_says_so_in_its_own_note(self):
        result = self.hand()
        note = self.note(self.session)
        self.assertEqual(note["status"], "live")
        self.assertEqual((note["meta"]["handed_to"], note["meta"]["handed_at"], note["meta"]["handed_thread"]), (ASUS, "2026-11-22T10:41", result["thread"]))
        self.assertIn(f"note: handed to {ASUS} (handoff {result['thread']})", note["body"])
        (entry,) = handover.status(self.brain)["sessions"]
        self.assertEqual((entry["session"], entry["to"], entry["live"], entry["thread"]), (self.session, ASUS, True, result["thread"]))

    def test_its_chat_is_told_at_its_next_step_and_only_that_chat(self):
        result = self.hand()
        text = handover.notice(self.brain, self.session)
        self.assertTrue(text.startswith(f"THIS CHAT'S WORK WAS HANDED TO {ASUS}"))
        self.assertIn(f'cairn handoff reply "{result["thread"]}"', text)
        self.assertIn(f"cairn vibe hand here --session {self.session}", text)
        self.assertEqual(handover.notice(self.brain, f"sessions/{self.session}.md"), text, "a path names the session too")
        other = self.slug(vibe.start(self.brain, "Another chat"))
        self.assertEqual(handover.notice(self.brain, other), "")
        self.assertEqual(handover.notice(self.brain), "")

    def test_a_handed_chat_is_not_told_twice_when_all_chats_are_handed_too(self):
        self.hand()
        handover.switch(self.brain, ASUS)
        text = handover.notice(self.brain, self.session)
        self.assertIn("THIS CHAT'S WORK WAS HANDED", text)
        self.assertNotIn("HAND-OVER IS ON", text)

    def test_without_a_name_it_goes_to_the_one_other_computer(self):
        self.assertEqual(handover.hand_session(self.brain, self.session)["to"], ASUS)

    def test_without_a_name_and_two_others_it_asks_or_follows_the_switch(self):
        self.other_session(OFFICE, "Spreadsheet")
        with self.assertRaises(handover.HandoverError) as caught:
            handover.hand_session(self.brain, self.session)
        self.assertIn("which computer", str(caught.exception))
        handover.switch(self.brain, OFFICE)
        self.assertEqual(handover.hand_session(self.brain, self.session)["to"], OFFICE)

    def test_a_few_words_of_its_goal_find_the_session(self):
        self.assertEqual(handover.hand_session(self.brain, "neon coast", "asus")["session"], self.session)

    def test_a_session_is_handed_once(self):
        self.hand()
        with self.assertRaises(handover.HandoverError) as caught:
            self.hand()
        self.assertIn("Take it back first", str(caught.exception))
        self.assertEqual(len(self.threads()), 1)

    def test_another_computers_session_is_not_this_computers_to_hand(self):
        theirs = self.other_session(ASUS, "Podcast run")
        with self.assertRaises(handover.HandoverError) as caught:
            handover.hand_session(self.brain, theirs, "asus")
        self.assertIn(f"runs on {ASUS}", str(caught.exception))
        with self.assertRaises(handover.HandoverError):
            handover.hand_session(self.brain, "podcast", "asus")

    def test_taking_it_back_closes_the_conversation_and_clears_the_note(self):
        result = self.hand()
        back = handover.take_back(self.brain, self.session, now=dt.datetime(2026, 11, 22, 11, 2))
        self.assertTrue(back["closed"])
        (thread,) = self.threads()
        self.assertFalse(thread["open"])
        self.assertEqual(thread["messages"][-1]["says"], "closed")
        self.assertIn(f"Taken back by {MAC}", thread["messages"][-1]["title"])
        note = self.note(self.session)
        self.assertFalse(any(key in note["meta"] for key in handover.SESSION_KEYS))
        self.assertIn(f"note: taken back from {ASUS}", note["body"])
        self.assertIn(f"note: handed to {ASUS} (handoff {result['thread']})", note["body"], "the log keeps what happened")
        self.assertEqual(handover.notice(self.brain, self.session), "")
        self.assertEqual(handover.status(self.brain)["sessions"], [])
        self.hand()  # and it can be handed again
        self.assertEqual(len(self.threads()), 2)

    def test_a_conversation_the_other_computer_finished_is_left_as_it_ended(self):
        result = self.hand()
        handoff.answer(self.brain, result["thread"], "Shipped 1.6, 14 of 14", says="done", sender=ASUS, now=dt.datetime(2026, 11, 22, 10, 55))
        back = handover.take_back(self.brain, self.session)
        self.assertFalse(back["closed"])
        self.assertEqual(self.threads()[0]["messages"][-1]["says"], "done")

    def test_taking_back_what_was_never_handed_says_so(self):
        with self.assertRaises(handover.HandoverError) as caught:
            handover.take_back(self.brain, self.session)
        self.assertIn("not handed over", str(caught.exception))

    def test_a_long_log_goes_as_its_last_steps(self):
        for step in range(handover.LOG_LINES + 5):
            vibe.log(self.brain, f"step {step}", session=self.session)
        body = handoff.body_of(self.brain, self.hand()["handoff"])
        self.assertIn(f"the last {handover.LOG_LINES} steps", body)
        self.assertNotIn("joystick drawn", body)
        self.assertIn(f"step {handover.LOG_LINES + 4}", body)

    def test_an_ended_session_handed_long_ago_leaves_the_list(self):
        self.hand()
        vibe.end(self.brain, "partial", "left for the ASUS", session_slug=self.session)
        self.assertEqual(len(handover.status(self.brain)["sessions"]), 1, "handed today: still listed, to take back")
        path = f"sessions/{self.session}.md"
        current = self.brain.read(path)
        self.brain.write(path, core.update_note_text(current["text"], {"handed_at": "2026-11-01T09:00"}), current["mtime"])
        self.assertEqual(handover.status(self.brain)["sessions"], [])


class CommandTests(HandoverCase):
    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--root", str(self.brain.root), *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_hand_says_where_the_work_goes(self):
        code, out, _ = self.run_cli("vibe", "hand")
        self.assertEqual(code, 0)
        self.assertIn("is done here", out)
        self.assertIn(ASUS, out)

    def test_hand_a_computer_and_hand_here(self):
        code, out, _ = self.run_cli("vibe", "hand", "asus")
        self.assertEqual(code, 0)
        self.assertIn(f"goes to {ASUS} from now on", out)
        self.assertIn(f"goes to {ASUS} (since", self.run_cli("vibe", "hand")[1])
        code, out, _ = self.run_cli("vibe", "hand", "here")
        self.assertEqual(code, 0)
        self.assertIn("done here again", out)

    def test_a_step_logged_while_it_is_on_tells_the_chat(self):
        self.assertNotIn("HAND-OVER", self.run_cli("vibe", "log", "a step", "--session", self.session)[1])
        self.run_cli("vibe", "hand", "asus")
        code, out, _ = self.run_cli("vibe", "log", "another step", "--session", self.session)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Logged."))
        self.assertIn("HAND-OVER IS ON", out)
        self.assertIn("HAND-OVER IS ON", self.run_cli("vibe", "start", "A new chat")[1])

    def test_one_session_is_handed_and_taken_back_by_name(self):
        code, out, _ = self.run_cli("vibe", "hand", "asus", "--session", self.session)
        self.assertEqual(code, 0)
        self.assertIn(f"Handed to {ASUS}: Neon Coast 1.6: touch controls", out)
        self.assertIn("brain/handoffs/", out)
        self.assertIn("handed to", self.run_cli("vibe", "hand")[1])
        logged = self.run_cli("vibe", "log", "one more", "--session", self.session)[1]
        self.assertIn("THIS CHAT'S WORK WAS HANDED TO", logged)
        code, out, _ = self.run_cli("vibe", "hand", "here", "--session", self.session)
        self.assertEqual(code, 0)
        self.assertIn(f"Taken back from {ASUS}", out)
        self.assertNotIn("HANDED", self.run_cli("vibe", "log", "back at it", "--session", self.session)[1])

    def test_a_refusal_is_a_sentence_and_exit_code_one(self):
        code, _, err = self.run_cli("vibe", "hand", "thinkpad")
        self.assertEqual(code, 1)
        self.assertIn("No computer called", err)


class MemorySkillTests(HandoverCase):
    """A chat that starts reads `recall.py rules`: while the work is handed over, that is its first line."""

    def setUp(self):
        super().setUp()
        app_home = self.tmp / "apps"
        (app_home / ".vector").mkdir(parents=True)
        connect.connect(self.brain, ["vector"], home=app_home, settings_folder=self.tmp / "connect-home")
        self.skill = app_home / ".vector" / "skills" / "cairn-memory"

    def rules(self) -> str:
        done = subprocess.run([sys.executable, str(self.skill / "recall.py"), "rules"], capture_output=True, text=True,
                              encoding="utf-8", timeout=120, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_the_rules_begin_with_it_while_it_is_on_and_not_after(self):
        self.assertNotIn("HAND-OVER", self.rules())
        handover.switch(self.brain, ASUS)
        said = self.rules()
        self.assertTrue(said.startswith("HAND-OVER IS ON"), said[:200])
        self.assertIn(ASUS, said)
        handover.switch(self.brain, "here")
        self.assertNotIn("HAND-OVER", self.rules())

    def test_the_guide_says_what_that_first_line_means(self):
        guide = (self.skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("HAND-OVER IS ON", guide)
        self.assertIn("remember.py handoff", guide)


class ServerTests(HandoverCase):
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

    def test_the_live_page_is_told_where_the_work_goes(self):
        status, data = self.call("GET", "/api/vibe")
        self.assertEqual(status, 200)
        self.assertEqual((data["hand"]["to"], data["hand"]["others"]), ("", [ASUS]))

    def test_a_press_switches_all_chats_and_back(self):
        status, data = self.call("POST", "/api/vibe/hand", {"to": ASUS})
        self.assertEqual((status, data["to"], data["files"]), (200, ASUS, []))
        self.assertEqual(self.call("GET", "/api/vibe")[1]["hand"]["to"], ASUS)
        status, data = self.call("POST", "/api/vibe/hand", {"to": "here"})
        self.assertEqual((status, data["to"]), (200, ""))

    def test_a_press_on_a_row_hands_that_session_and_sends_back_what_it_wrote(self):
        with mock.patch.object(handoff, "deliver") as deliver:
            status, data = self.call("POST", "/api/vibe/hand", {"to": ASUS, "session": self.session})
        self.assertEqual(status, 200)
        self.assertEqual([entry["session"] for entry in data["sessions"]], [self.session])
        paths = [file["path"] for file in data["files"]]
        self.assertEqual(paths[0], f"sessions/{self.session}.md")
        self.assertTrue(paths[1].startswith("handoffs/"))
        self.assertIn("handed_to", data["files"][0]["text"])
        deliver.assert_called_once()
        with mock.patch.object(handoff, "deliver"):
            status, data = self.call("POST", "/api/vibe/hand", {"to": "here", "session": self.session})
        self.assertEqual((status, data["sessions"]), (200, []))

    def test_a_refusal_is_a_400_with_the_sentence(self):
        status, data = self.call("POST", "/api/vibe/hand", {"to": "thinkpad"})
        self.assertEqual(status, 400)
        self.assertIn("No computer called", data["error"])


@unittest.skipUnless(sync_playwright, "needs playwright")
class PageTests(HandoverCase):
    """The Vibe page: one chip per computer for all chats, and Hand to / Take back on a live row."""

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
        deliver = mock.patch.object(handoff, "deliver", return_value="")  # no remote here to send to
        deliver.start()
        self.addCleanup(deliver.stop)

    def open(self, viewport=(1280, 1200)):
        page = self.browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.add_init_script("window.CAIRN_TEST_POLL_MS = 150")
        page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/#/vibe")  # by its address: a phone has no sidebar to click
        page.wait_for_selector(".vibe-hand")
        return page

    def test_one_press_hands_all_chats_over_and_one_takes_it_back(self):
        page = self.open()
        block = page.locator(".vibe-hand")
        self.assertEqual(block.locator(".chip").all_inner_texts(), ["This computer", ASUS])
        self.assertEqual(block.locator(".chip.on").inner_text(), "This computer")
        self.assertIn("done on this computer", block.locator("header").inner_text())

        block.locator(".chip", has_text=ASUS).click()
        page.wait_for_function("document.querySelector('.vibe-hand .chip.on')?.textContent !== 'This computer'")
        self.assertEqual(handover.status(self.brain)["to"], ASUS)
        self.assertIn(f"goes to {ASUS} since", page.locator(".vibe-hand header").inner_text())
        self.assertEqual(self.threads(), [], "the switch itself sends nothing")

        page.locator(".vibe-hand .chip", has_text="This computer").click()
        page.wait_for_function("document.querySelector('.vibe-hand .chip.on')?.textContent === 'This computer'")
        self.assertEqual(handover.status(self.brain)["to"], "")
        self.assertEqual(self.errors, [])

    def test_a_switch_made_in_a_terminal_shows_on_the_page(self):
        page = self.open()
        with mock.patch.object(handover, "SOON_TTL_SECONDS", 0):
            handover.switch(self.brain, ASUS)
            page.wait_for_function("document.querySelector('.vibe-hand .chip.on')?.textContent !== 'This computer'")
        self.assertEqual(self.errors, [])

    def test_the_block_lists_this_computers_live_chats_to_pick_from(self):
        second = self.slug(vibe.start(self.brain, "Bits and Tickers interview", agent="vector"))
        page = self.open()
        pick = page.locator(".vibe-hand .hand-pick")
        self.assertIn("2 live chats on this computer", pick.locator("summary").inner_text())
        self.assertTrue(pick.evaluate("node => node.open"), "a short list is open")
        # both started in this minute, so which is first is not said; that both are there is
        self.assertEqual(sorted(pick.locator(".hand-what").all_inner_texts()), ["Bits and Tickers interview", "Neon Coast 1.6: touch controls"])
        self.assertNotIn("Lab writer", pick.inner_text(), "another computer's chat is not in it")

        pick.locator("li", has_text="Bits and Tickers").locator("[data-action=vibe-hand]").click()
        page.get_by_role("button", name=f"Hand to {ASUS}", exact=True).last.click()
        page.wait_for_selector(".vibe-hand .hand-out li")
        self.assertEqual([entry["session"] for entry in handover.status(self.brain)["sessions"]], [second])
        self.assertEqual(page.locator(".vibe-hand .hand-pick .hand-what").all_inner_texts(), ["Neon Coast 1.6: touch controls"])
        self.assertIn("Bits and Tickers interview", page.locator(".vibe-hand .hand-out").inner_text())
        page.locator(".vibe-hand .hand-out [data-action=vibe-hand]").click()
        page.wait_for_function("!document.querySelector('.vibe-hand .hand-out')")
        self.assertEqual(len(page.locator(".vibe-hand .hand-pick .hand-what").all_inner_texts()), 2)
        self.assertEqual(self.errors, [])

    def test_a_row_is_handed_after_a_confirmation_and_taken_back(self):
        for viewport in ((1280, 1200), (390, 844)):
            with self.subTest(viewport=viewport):
                page = self.open(viewport)
                mine = page.locator(".rc-row", has_text="Neon Coast 1.6")
                theirs = page.locator(".rc-row", has_text="Lab writer")
                self.assertEqual(mine.locator("[data-action=vibe-hand]").all_inner_texts(), [f"Hand to {ASUS}"])
                self.assertEqual(theirs.count(), 1)
                self.assertEqual(theirs.locator("[data-action=vibe-hand]").count(), 0, "another computer's chat is not this one's to hand")

                mine.locator("[data-action=vibe-hand]").click()
                confirm = page.get_by_role("button", name=f"Hand to {ASUS}", exact=True).last
                self.assertIn("what moves is the work", page.locator(".modal").inner_text())
                confirm.click()
                page.wait_for_selector(".rc-row .rc-handed")
                (entry,) = handover.status(self.brain)["sessions"]
                self.assertEqual((entry["session"], entry["to"]), (self.session, ASUS))
                self.assertEqual(len([thread for thread in self.threads() if thread["open"]]), 1)
                mine = page.locator(".rc-row", has_text="Neon Coast 1.6")
                self.assertEqual(mine.locator(".rc-handed").inner_text(), f"handed to {ASUS}")
                self.assertEqual(mine.locator("[data-action=vibe-hand]").all_inner_texts(), [f"Take back from {ASUS}"])
                self.assertEqual(page.locator(".vibe-hand .hand-out li").count(), 1)
                # nothing runs off the side of a phone
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), viewport[0])

                mine.locator("[data-action=vibe-hand]").click()
                page.wait_for_function("!document.querySelector('.rc-row .rc-handed')")
                self.assertEqual(handover.status(self.brain)["sessions"], [])
                self.assertEqual([thread["open"] for thread in self.threads()][-1], False)
                self.assertEqual(self.errors, [])
                page.close()


if __name__ == "__main__":
    unittest.main()

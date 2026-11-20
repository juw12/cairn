"""Handoffs: the conversation between the computers that share one brain (cairn/core.py, cairn/handoff.py)."""

import contextlib
import datetime as dt
import http.client
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cairn import cli, core, handoff
from cairn.brain import Brain
from cairn.server import make_server

MAC = "MacBook Pro 15,4"
ASUS = "ASUS EXPERTBOOK P5405CSA"
OFFICE = "Office PC"


def at(minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, 20, 23, minute, second)


class ThreadRuleTests(unittest.TestCase):
    """The rules alone: files in, where every conversation stands out."""

    def setUp(self):
        self.files: dict[str, str] = {}

    def say(self, sender, title, minute, to=None, thread="", says="note", body=""):
        stamp = at(minute).strftime("%Y-%m-%dT%H:%M:%S")
        message = core.handoff_message(sender, title, body, stamp, list(self.files), to=to, thread=thread, says=says)
        self.files[message["path"]] = message["text"]
        return message["path"].rsplit("/", 1)[-1][:-3]

    def threads(self):
        return core.handoff_threads([core.note_from_file(path, text) for path, text in self.files.items()])

    def thread(self, thread_id):
        return next(thread for thread in self.threads() if thread["id"] == thread_id)

    def test_a_message_is_one_file_that_names_who_said_it_and_who_it_is_for(self):
        thread_id = self.say(MAC, "Buddy 1.13.0 checked on the real Mac", 1, to=[ASUS], body="24 of 24.")
        self.assertEqual(thread_id, "2026-11-20-2301-macbook-pro-15-4-buddy-1-13-0-checked-on-the-real-mac")
        note = core.note_from_file(*next(iter(self.files.items())))
        self.assertEqual(note["path"], f"handoffs/{thread_id}.md")
        self.assertEqual(note["meta"]["type"], "handoff")
        self.assertEqual(note["type"], "note", "an app that does not know handoffs still shows it as a note")
        self.assertEqual(note["meta"]["from"], MAC)
        self.assertEqual(note["meta"]["to"], [ASUS])
        self.assertEqual(note["meta"]["created"], "2026-11-20T23:01:00")
        self.assertEqual(note["body"], "24 of 24.\n")
        # A name with a comma survives the list it is written in.
        back = self.say(ASUS, "For the Mac", 2, to=[MAC])
        self.assertEqual(self.thread(back)["to"], [MAC])
        self.assertIn('to: ["MacBook Pro 15,4"]', self.files[f"handoffs/{back}.md"])

    def test_two_messages_of_one_minute_get_two_files(self):
        first = self.say(MAC, "Same words", 1, to=[ASUS])
        second = self.say(MAC, "Same words", 1, to=[ASUS])
        self.assertEqual(second, f"{first}-2")
        self.assertEqual(len(self.threads()), 2)

    def test_it_waits_until_the_computer_it_is_for_says_done(self):
        thread_id = self.say(MAC, "Check the runner", 1, to=[ASUS])
        thread = self.thread(thread_id)
        self.assertTrue(thread["open"])
        self.assertEqual(thread["parts"], [{"device": ASUS, "state": "waiting", "at": ""}])
        self.assertEqual(core.handoff_waits_for(thread, ASUS), "waiting")
        self.assertEqual(core.handoff_waits_for(thread, "asus  expertbook p5405csa"), "waiting", "case and spacing do not count")
        self.assertEqual(core.handoff_waits_for(thread, MAC), "", "never for the one who asked")
        self.assertEqual(core.handoff_waits_for(thread, OFFICE), "")

        self.say(ASUS, "Picked up", 2, thread=thread_id, says="picked-up")
        self.say(ASUS, "Which repo?", 3, thread=thread_id)
        thread = self.thread(thread_id)
        self.assertEqual(core.handoff_waits_for(thread, ASUS), "picked-up")
        self.assertTrue(thread["open"])
        self.assertEqual([m["says"] for m in thread["messages"]], ["note", "picked-up", "note"])

        self.say(ASUS, "Runner is on the ASUS now", 4, thread=thread_id, says="done")
        thread = self.thread(thread_id)
        self.assertFalse(thread["open"])
        self.assertEqual(thread["parts"][0]["state"], "done")
        self.assertEqual(core.handoff_waits_for(thread, ASUS), "")
        self.assertEqual(thread["last"], "2026-11-20T23:04:00")
        # Done stays done: a later "picked up" from the same computer does not open it again.
        self.say(ASUS, "Picked up", 5, thread=thread_id, says="picked-up")
        self.assertFalse(self.thread(thread_id)["open"])

    def test_more_than_two_computers_each_finish_their_own_part(self):
        thread_id = self.say(ASUS, "Rebuild the pictures", 1, to=[OFFICE, MAC])
        self.say(MAC, "Done", 2, thread=thread_id, says="done")
        thread = self.thread(thread_id)
        self.assertTrue(thread["open"], "the office PC has not done its part")
        self.assertEqual([(p["device"], p["state"]) for p in thread["parts"]], [(OFFICE, "waiting"), (MAC, "done")])
        self.assertEqual(core.handoff_waits_for(thread, MAC), "")
        self.assertEqual(core.handoff_waits_for(thread, OFFICE), "waiting")
        self.say(OFFICE, "Done", 3, thread=thread_id, says="done")
        self.assertFalse(self.thread(thread_id)["open"])

    def test_a_message_for_every_computer_waits_for_each_until_its_writer_closes_it(self):
        thread_id = self.say(ASUS, "Pull Cairn", 1)
        thread = self.thread(thread_id)
        self.assertTrue(thread["everyone"])
        self.assertEqual(thread["to"], [])
        self.assertEqual(core.handoff_waits_for(thread, MAC), "waiting")
        self.assertEqual(core.handoff_waits_for(thread, "A computer nobody has heard of"), "waiting")
        self.say(MAC, "Done", 2, thread=thread_id, says="done")
        thread = self.thread(thread_id)
        self.assertEqual(core.handoff_waits_for(thread, MAC), "")
        self.assertEqual(core.handoff_waits_for(thread, OFFICE), "waiting")
        self.assertTrue(thread["open"])
        self.say(ASUS, "Everyone has it", 3, thread=thread_id, says="done")
        self.assertFalse(self.thread(thread_id)["open"])
        self.assertEqual(core.handoff_waits_for(self.thread(thread_id), OFFICE), "")

    def test_the_writer_can_close_it_and_a_computer_it_was_not_for_can_step_in(self):
        thread_id = self.say(MAC, "Check the pairing", 1, to=[ASUS])
        self.say(OFFICE, "I have Windows too, done here", 2, thread=thread_id, says="done")
        thread = self.thread(thread_id)
        self.assertTrue(thread["open"], "the ASUS was asked, not the office PC")
        self.assertEqual([(p["device"], p["state"]) for p in thread["parts"]], [(ASUS, "waiting"), (OFFICE, "done")])
        self.say(MAC, "Settled", 3, thread=thread_id, says="done")
        self.assertFalse(self.thread(thread_id)["open"])

    def test_the_first_message_leads_whatever_the_clocks_say(self):
        thread_id = self.say(MAC, "Clock test", 30, to=[ASUS])
        self.say(ASUS, "My clock is behind", 5, thread=thread_id)
        thread = self.thread(thread_id)
        self.assertEqual([m["title"] for m in thread["messages"]], ["Clock test", "My clock is behind"])
        self.assertEqual(thread["last"], "2026-11-20T23:30:00")

    def test_messages_of_one_second_read_in_the_order_that_makes_sense(self):
        thread_id = self.say(MAC, "Quick one", 1, to=[ASUS])
        self.say(ASUS, "Finished", 2, thread=thread_id, says="done")
        self.say(ASUS, "A word in between", 2, thread=thread_id)
        self.say(ASUS, "Picked up", 2, thread=thread_id, says="picked-up")
        thread = self.thread(thread_id)
        self.assertEqual([m["says"] for m in thread["messages"]], ["note", "picked-up", "note", "done"])
        self.assertFalse(thread["open"])

    def test_an_answer_that_arrives_before_its_first_message_still_shows(self):
        text = core.handoff_message(ASUS, "Picked up", "", "2026-11-20T23:02:00", [], thread="not-synced-yet", says="picked-up")
        self.files[text["path"]] = text["text"]
        thread = self.thread("not-synced-yet")
        self.assertEqual(thread["title"], "Picked up")
        self.assertTrue(thread["open"])

    def test_each_computer_sees_its_own_inbox(self):
        for_asus = self.say(MAC, "For the ASUS", 1, to=[ASUS])
        for_all = self.say(ASUS, "For all", 2)
        other = self.say(ASUS, "For the office", 3, to=[OFFICE])
        settled = self.say(MAC, "Old", 4, to=[ASUS])
        self.say(ASUS, "Done", 5, thread=settled, says="done")
        threads = self.threads()
        self.assertEqual([t["id"] for t in threads], [other, for_all, for_asus, settled], "open first, newest first")
        self.assertEqual(core.handoff_inbox(threads, MAC), {"mine": [for_all], "asked": [for_asus], "others": [other], "settled": [settled]})
        self.assertEqual(core.handoff_inbox(threads, ASUS), {"mine": [for_asus], "asked": [other, for_all], "others": [], "settled": [settled]})
        self.assertEqual(core.handoff_inbox(threads, OFFICE), {"mine": [other, for_all], "asked": [], "others": [for_asus], "settled": [settled]})
        # The web app and a phone are no computer: nothing is "theirs", everything open is listed.
        self.assertEqual(core.handoff_inbox(threads, ""), {"mine": [], "asked": [], "others": [other, for_all, for_asus], "settled": [settled]})

    def test_other_notes_are_left_alone(self):
        self.files["notes/plain.md"] = "---\ntitle: Plain\nfrom: someone\nto: [x]\n---\n"
        self.files["tasks/handoff-task.md"] = "---\ntitle: Pick it up\ntype: task\ntags: [handoff]\n---\n"
        self.assertEqual(self.threads(), [])


class HandoffBrainTests(unittest.TestCase):
    """Files on disk, the names of computers, and the command line."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.brain = Brain(self.tmp)
        self.brain.init()
        for index, device in enumerate((MAC, ASUS, OFFICE)):
            self.brain.write(
                f"sessions/2026-11-20-work-{index}.md",
                core.serialize_note({"title": f"Work {index}", "type": "session", "status": "done", "machine": device, "started": "2026-11-20T10:00"}, ""),
            )
        self.clock = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tick(self) -> dt.datetime:
        self.clock += 1
        return at(self.clock)

    def run_cli(self, device, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CAIRN_DEVICE": device}), mock.patch("sys.stdin", io.StringIO(stdin)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--root", str(self.tmp), "handoff", *args])
        return code, out.getvalue(), err.getvalue()

    def test_the_computers_are_the_ones_that_have_written_into_the_brain(self):
        self.assertEqual(handoff.known_devices(self.brain), [ASUS, MAC, OFFICE])
        self.brain.write("machines/thinkpad.md", core.serialize_note({"title": "ThinkPad X1", "type": "note", "tags": ["machine"], "machine_at": "2026-11-20T09:00", "load": "light"}, ""))
        handoff.send(self.brain, "Hello", to=["all"], sender="Phone", now=self.tick())
        self.assertEqual(handoff.known_devices(self.brain), [ASUS, MAC, OFFICE, "Phone", "ThinkPad X1"])

    def test_a_few_words_of_a_name_are_enough(self):
        names = handoff.known_devices(self.brain)
        self.assertEqual(handoff.resolve_device(names, "asus"), ASUS)
        self.assertEqual(handoff.resolve_device(names, "macbook pro 15,4"), MAC)
        self.assertEqual(handoff.resolve_device(names, "Every computer"), "all")
        with self.assertRaisesRegex(handoff.HandoffError, "No computer called"):
            handoff.resolve_device(names, "thinkpad")
        with self.assertRaisesRegex(handoff.HandoffError, "more than one"):
            handoff.resolve_device(names, "p")  # MacBook Pro, EXPERTBOOK P5405CSA, Office PC

    def test_send_answer_and_done_write_one_new_file_each_and_change_none(self):
        note = handoff.send(self.brain, "Buddy on the real Mac", "24 of 24.", to=["asus"], sender=MAC, now=self.tick())
        first = self.brain.read(note["path"])
        self.assertEqual(note["meta"]["to"], [ASUS])
        handoff.answer(self.brain, "buddy", says="picked-up", sender=ASUS, now=self.tick())
        handoff.answer(self.brain, "buddy", "Which macOS?\n\nI need it for the release notes.", sender=ASUS, now=self.tick())
        handoff.answer(self.brain, "buddy", "Released as 1.13.1.", says="done", sender=ASUS, now=self.tick())
        files = sorted(f["path"] for f in self.brain.files() if f["path"].startswith("handoffs/"))
        self.assertEqual(len(files), 4)
        self.assertEqual(self.brain.read(note["path"]), first, "the first message was never touched")
        thread = handoff.threads(self.brain)[0]
        self.assertFalse(thread["open"])
        self.assertEqual([m["title"] for m in thread["messages"]], ["Buddy on the real Mac", "Picked up", "Which macOS?", "Released as 1.13.1."])
        self.assertEqual(handoff.body_of(self.brain, thread["messages"][2]["path"]), "Which macOS?\n\nI need it for the release notes.")
        self.assertEqual(handoff.body_of(self.brain, thread["messages"][3]["path"]), "", "one line is the title alone")

    def test_what_makes_no_sense_is_refused(self):
        with self.assertRaisesRegex(handoff.HandoffError, "title"):
            handoff.send(self.brain, "  ", to=["asus"], sender=MAC)
        with self.assertRaisesRegex(handoff.HandoffError, "this computer itself"):
            handoff.send(self.brain, "To myself", to=["macbook"], sender=MAC)
        with self.assertRaisesRegex(handoff.HandoffError, "No conversation fits"):
            handoff.answer(self.brain, "nothing like it", "hello", sender=ASUS)
        with self.assertRaisesRegex(handoff.HandoffError, "What do you want to say"):
            handoff.send(self.brain, "A real one", to=["asus"], sender=MAC, now=self.tick())
            handoff.answer(self.brain, "a real one", "", sender=ASUS)
        handoff.send(self.brain, "A real two", to=["asus"], sender=MAC, now=self.tick())
        with self.assertRaisesRegex(handoff.HandoffError, "more than one conversation"):
            handoff.answer(self.brain, "a real", "which?", sender=ASUS)

    def test_a_few_words_find_the_open_conversation_before_a_settled_one(self):
        old = handoff.send(self.brain, "Check the runner", to=["asus"], sender=MAC, now=self.tick())
        handoff.answer(self.brain, old["slug"], says="done", sender=ASUS, now=self.tick())
        new = handoff.send(self.brain, "Check the runner again", to=["asus"], sender=MAC, now=self.tick())
        self.assertEqual(handoff.find_thread(handoff.threads(self.brain), "runner")["id"], new["slug"])
        self.assertEqual(handoff.find_thread(handoff.threads(self.brain), old["slug"])["id"], old["slug"])

    def test_the_command_line_carries_a_whole_conversation_between_three_computers(self):
        code, out, _ = self.run_cli(MAC, "send", "Buddy", "on", "the", "real", "Mac", "--to", "asus", "--to", "office", "-m", "Self-test: 24 of 24.")
        self.assertEqual(code, 0)
        self.assertIn(f"Left for {ASUS}, {OFFICE}: brain/handoffs/", out)
        code, out, _ = self.run_cli(MAC, "send", "Pull Cairn", "--to", "all")
        self.assertIn("Left for every computer", out)

        code, out, _ = self.run_cli(ASUS)
        self.assertEqual(code, 0)
        self.assertIn(f"For this computer ({ASUS}):", out)
        self.assertIn("Buddy on the real Mac", out)
        self.assertIn("Pull Cairn", out)
        self.assertIn(f"{ASUS} has not picked it up; {OFFICE} has not picked it up", out)

        self.assertEqual(self.run_cli(ASUS, "pickup", "buddy")[0], 0)
        self.assertIn("(picked up, not done yet)", self.run_cli(ASUS)[1])
        self.assertEqual(self.run_cli(ASUS, "reply", "buddy", "Which", "macOS?")[0], 0)
        self.assertEqual(self.run_cli(ASUS, "done", "buddy", "Released.", "--file", "-", stdin="The notes are in the release.\n")[0], 0)

        code, out, _ = self.run_cli(ASUS)
        self.assertNotIn("Buddy on the real Mac", out, "its part is done")
        code, out, _ = self.run_cli(OFFICE)
        self.assertIn("Buddy on the real Mac", out, "the office PC still has its part")
        self.assertIn(f"{ASUS} done; {OFFICE} has not picked it up", out)

        code, out, _ = self.run_cli(MAC)
        self.assertIn("Nothing is waiting for this computer.", out)
        self.assertIn("This computer is waiting for:", out)

        code, out, _ = self.run_cli(MAC, "show", "buddy")
        self.assertEqual(code, 0)
        self.assertIn("Self-test: 24 of 24.", out)
        self.assertIn("[picked up]", out)
        self.assertIn("Which macOS?", out)
        self.assertIn("The notes are in the release.", out)
        self.assertLess(out.index("Self-test: 24 of 24."), out.index("Which macOS?"))

        self.assertEqual(self.run_cli(OFFICE, "done", "buddy")[0], 0)
        code, out, _ = self.run_cli(MAC, "--all")
        self.assertIn("Settled:", out)
        self.assertLess(out.index("Settled:"), out.index("Buddy on the real Mac"))

    def test_the_command_line_says_what_is_missing(self):
        code, _, err = self.run_cli(MAC, "send", "No one named")
        self.assertEqual(code, 2)
        self.assertIn("Who is it for?", err)
        code, _, err = self.run_cli(MAC, "send", "Nobody", "--to", "thinkpad")
        self.assertEqual(code, 2)
        self.assertIn('Known: "ASUS EXPERTBOOK P5405CSA", "MacBook Pro 15,4", "Office PC"', err)
        code, _, err = self.run_cli(MAC, "done")
        self.assertEqual(code, 2)
        self.assertIn("Which conversation?", err)
        self.assertEqual([f for f in self.brain.files() if f["path"].startswith("handoffs/")], [])

    def test_listing_never_creates_a_brain(self):
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--root", str(empty), "handoff"])
        self.assertEqual(code, 0)
        self.assertIn("Nothing is waiting for this computer.", out.getvalue())
        self.assertEqual(list(empty.iterdir()), [])

    def test_the_app_is_told_which_computer_it_runs_on(self):
        with mock.patch.dict(os.environ, {"CAIRN_DEVICE": "Work laptop"}):
            server = make_server(self.brain, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                connection.request("GET", "/api/info", headers={"X-Cairn": "1"})
                info = json.loads(connection.getresponse().read())
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
        self.assertEqual(info["device"], "Work laptop")


if __name__ == "__main__":
    unittest.main()

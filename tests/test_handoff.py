"""Handoffs: the conversation between the computers that share one brain (cairn/core.py, cairn/handoff.py)."""

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

from cairn import cli, core, handoff, livesync
from cairn.brain import Brain, SyncError
from cairn.server import ChangeFeed, make_server

MAC = "MacBook Pro 15,4"
ASUS = "ASUS EXPERTBOOK P5405CSA"
OFFICE = "Office PC"


def at(minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, 20, 23, minute, second)


class ThreadRuleTests(unittest.TestCase):
    """The rules alone: files in, where every conversation stands out."""

    def setUp(self):
        self.files: dict[str, str] = {}

    def say(self, sender, title, minute, to=None, thread="", says="note", body="", sent="", after=""):
        stamp = at(minute).strftime("%Y-%m-%dT%H:%M:%S")
        message = core.handoff_message(sender, title, body, stamp, list(self.files), to=to, thread=thread, says=says, sent=sent, after=after)
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

    def test_an_answer_never_reads_before_what_it_answers_whatever_the_clocks_say(self):
        # The ASUS clock is an hour behind. By the clocks its answer came before the question it answers.
        thread_id = self.say(MAC, "Which build is live?", 30, to=[ASUS])
        question = self.say(MAC, "And is the cache cleared?", 40, thread=thread_id)
        answer = self.say(ASUS, "Build 87, and yes", 5, thread=thread_id, after=question)
        self.say(MAC, "Thanks", 45, thread=thread_id, after=answer)
        thread = self.thread(thread_id)
        self.assertEqual([m["title"] for m in thread["messages"]],
                         ["Which build is live?", "And is the cache cleared?", "Build 87, and yes", "Thanks"])
        self.assertIn(f'after: "[[{question}]]"', self.files[f"handoffs/{answer}.md"])
        # An answer to the first message itself needs no `after`: the thread names it.
        self.assertNotIn("after:", self.files[f"handoffs/{question}.md"])

    def test_messages_that_name_each_other_in_a_ring_are_all_still_shown(self):
        thread_id = self.say(MAC, "Ring", 1, to=[ASUS])
        self.files["handoffs/ring-a.md"] = f'---\ntitle: A\ntype: handoff\nfrom: {ASUS}\nthread: "[[{thread_id}]]"\nafter: "[[ring-b]]"\ncreated: 2026-11-20T23:02:00\n---\n'
        self.files["handoffs/ring-b.md"] = f'---\ntitle: B\ntype: handoff\nfrom: {ASUS}\nthread: "[[{thread_id}]]"\nafter: "[[ring-a]]"\ncreated: 2026-11-20T23:03:00\n---\n'
        self.assertEqual([m["title"] for m in self.thread(thread_id)["messages"]], ["Ring", "A", "B"])

    def test_two_time_zones_agree_on_the_order_through_the_time_in_utc(self):
        # Batam is UTC+7, Berlin UTC+1. On their own clocks the Berlin answer looks six hours early.
        thread_id = self.say(MAC, "From Batam at 23:10", 10, to=[ASUS], sent="2026-11-20T16:10:00Z")
        self.files["handoffs/berlin.md"] = (
            f'---\ntitle: From Berlin at 17:20\ntype: handoff\nfrom: {ASUS}\nthread: "[[{thread_id}]]"\n'
            'created: 2026-11-20T17:20:00\nsent: 2026-11-20T16:20:00Z\n---\n')
        self.say(MAC, "From Batam at 23:30", 30, thread=thread_id, sent="2026-11-20T16:30:00Z")
        thread = self.thread(thread_id)
        self.assertEqual([m["title"] for m in thread["messages"]], ["From Batam at 23:10", "From Berlin at 17:20", "From Batam at 23:30"])
        self.assertEqual(thread["last"], "2026-11-20T16:30:00Z")
        self.assertEqual(thread["messages"][1]["sent"], "2026-11-20T16:20:00Z")

    def test_anyone_can_close_a_conversation_for_every_computer(self):
        named = self.say(MAC, "For two", 1, to=[ASUS, OFFICE])
        self.say("Phone", "Not needed any more", 2, thread=named, says="closed")
        thread = self.thread(named)
        self.assertFalse(thread["open"])
        self.assertEqual(core.handoff_waits_for(thread, ASUS), "")
        self.assertEqual([p["state"] for p in thread["parts"]], ["waiting", "waiting"], "nobody did it; it was called off")
        everyone = self.say(ASUS, "For all", 3)
        self.say(OFFICE, "Closed", 4, thread=everyone, says="closed")
        self.assertFalse(self.thread(everyone)["open"])
        self.assertEqual(core.handoff_inbox(self.threads(), MAC)["settled"], [everyone, named])

    def test_a_phone_takes_part_like_a_computer(self):
        to_phone = self.say(MAC, "Approve the release on the phone", 1, to=["Phone"])
        to_all = self.say(ASUS, "Everyone: read this", 2)
        inbox = core.handoff_inbox(self.threads(), "phone")
        self.assertEqual(inbox["mine"], [to_all, to_phone])
        self.say("Phone", "Picked up", 3, thread=to_phone, says="picked-up")
        self.assertEqual(core.handoff_waits_for(self.thread(to_phone), "Phone"), "picked-up")
        self.say("Phone", "Approved", 4, thread=to_phone, says="done")
        self.assertFalse(self.thread(to_phone)["open"])

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

    def test_close_ends_it_for_every_computer_from_the_command_line(self):
        self.run_cli(MAC, "send", "Rebuild the pictures", "--to", "asus", "--to", "office")
        code, out, _ = self.run_cli(OFFICE, "close", "pictures", "They", "were", "rebuilt", "by", "hand.")
        self.assertEqual(code, 0)
        self.assertIn("Closed for every computer: brain/handoffs/", out)
        self.assertIn("Nothing is waiting for this computer.", self.run_cli(ASUS)[1])
        shown = self.run_cli(MAC, "show", "pictures")[1]
        self.assertIn("settled", shown)
        self.assertIn("[closed for everyone]", shown)
        self.assertIn("They were rebuilt by hand.", shown)

    def test_a_message_carries_its_time_in_utc_and_the_message_it_follows(self):
        first = handoff.send(self.brain, "Clocks", to=["asus"], sender=MAC, now=self.tick())
        self.assertRegex(first["meta"]["sent"], r"^2026-11-2\dT\d\d:\d\d:00Z$")
        self.assertRegex(first["meta"]["created"], r"^2026-11-20T23:01:00$")
        one = handoff.answer(self.brain, "clocks", "One", sender=ASUS, now=self.tick())
        two = handoff.answer(self.brain, "clocks", "Two", sender=MAC, now=self.tick())
        self.assertNotIn("after", one["meta"], "it follows the first message, which the thread already names")
        self.assertEqual(two["meta"]["after"], f"[[{one['slug']}]]")
        # shown on this computer's own clock, to the minute
        self.assertIn("2026-11-20 23:01", handoff.transcript(self.brain, handoff.threads(self.brain)[0]))

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


def git(folder: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.stderr or done.stdout}")
    return done.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class AtOnceTests(unittest.TestCase):
    """Two computers and the remote between them, all on this disk: a message leaves the moment it is
    written and the other computer has it on its next small look at the remote."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(self.remote)], check=True)
        self.mac = self.clone("mac", MAC)
        self.mac.init()
        for index, device in enumerate((MAC, ASUS)):
            self.mac.write(f"sessions/2026-11-20-work-{index}.md",
                           core.serialize_note({"title": f"Work {index}", "type": "session", "status": "done", "machine": device, "started": "2026-11-20T10:00"}, ""))
        git(self.mac.root, "add", "-A")
        git(self.mac.root, "commit", "--quiet", "-m", "start")
        git(self.mac.root, "push", "--quiet", "-u", "origin", "main")
        self.asus = self.clone("asus", ASUS)
        git(self.asus.root, "pull", "--quiet", "origin", "main")
        git(self.asus.root, "branch", "--quiet", "--set-upstream-to=origin/main", "main")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def clone(self, name: str, device: str) -> Brain:
        folder = self.tmp / name
        subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(folder)], check=True)
        git(folder, "remote", "add", "origin", str(self.remote))
        git(folder, "config", "user.name", device)
        git(folder, "config", "user.email", "test@example.com")
        git(folder, "config", "commit.gpgsign", "false")
        return Brain(folder)

    def watcher(self, brain: Brain):
        handler = type("Handler", (), {"brain": brain, "changes": ChangeFeed()})
        return livesync.LiveSync(handler), handler

    def test_nothing_is_synced_unless_the_brain_says_so(self):
        self.assertEqual(self.mac.sync_mode(), "")
        handoff.send(self.mac, "Stays here", to=["asus"], sender=MAC)
        self.assertEqual(handoff.deliver(self.mac), handoff.NEXT_SYNC)
        self.assertEqual(handoff.refresh(self.mac), "")
        with self.assertRaises(SyncError):
            self.mac.sync_now()
        live, _ = self.watcher(self.mac)
        self.assertEqual(live.tick(), "")
        self.assertTrue(self.mac.handoffs_unsent())
        self.assertEqual(git(self.remote, "rev-list", "--count", "main"), "1", "nothing left this computer")

    def test_a_message_leaves_at_once_and_arrives_on_the_other_computers_next_look(self):
        for brain in (self.mac, self.asus):
            brain.set_sync_mode("auto")
        self.assertFalse(self.mac.handoffs_unsent())
        self.assertFalse(self.asus.remote_moved())
        live, handler = self.watcher(self.asus)
        self.assertEqual(live.tick(), "", "the first look only notes where the brain stands")

        note = handoff.send(self.mac, "Check the runner", "It stopped at 19:04.", to=["asus"], sender=MAC)
        self.assertTrue(self.mac.handoffs_unsent())
        self.assertEqual(handoff.deliver(self.mac), "Sent: it is on the brain's remote now.")
        self.assertFalse(self.mac.handoffs_unsent())
        self.assertIn(note["path"], git(self.remote, "ls-tree", "-r", "--name-only", "main"))

        self.assertTrue(self.asus.remote_moved())
        self.assertEqual(live.tick(), "synced")
        self.assertFalse(self.asus.remote_moved())
        self.assertEqual(handoff.threads(self.asus)[0]["title"], "Check the runner")
        # the open pages are told which note is new, so it shows without a reload
        self.assertEqual(handler.changes.since(0, handler.changes.epoch, "a page")["paths"], [f"brain/{note['path']}".removeprefix("brain/")])
        self.assertEqual(live.tick(), "", "nothing new: nothing done")

    def test_the_command_line_sends_and_fetches_by_itself(self):
        for brain in (self.mac, self.asus):
            brain.set_sync_mode("auto")

        def run(brain: Brain, device: str, *args: str) -> str:
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"CAIRN_DEVICE": device}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                cli.main(["--root", str(brain.root), "handoff", *args])
            return out.getvalue()

        self.assertIn("Sent: it is on the brain's remote now.", run(self.mac, MAC, "send", "Check the runner", "--to", "asus"))
        listed = run(self.asus, ASUS)  # fetched before it is read
        self.assertIn(f"For this computer ({ASUS}):", listed)
        self.assertIn("Check the runner", listed)
        self.assertIn("Sent", run(self.asus, ASUS, "done", "runner", "It", "runs", "again."))
        self.assertIn("Settled:", run(self.mac, MAC, "--all"))
        # --offline touches neither the remote nor the other computer's copy
        self.assertIn("next sync", run(self.mac, MAC, "send", "Later", "--to", "asus", "--offline"))
        self.assertNotIn("Later", run(self.asus, ASUS, "--offline"))

    def test_a_sync_command_of_the_users_own_is_run_as_it_is(self):
        marker = self.tmp / "ran.txt"
        script = self.tmp / "sync.py"
        script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\nprint('Brain synced by my own script.')\n", encoding="utf-8")
        self.mac.set_sync_mode(f'"{sys.executable}" "{script}"')
        self.assertEqual(self.mac.sync_now(), "Brain synced by my own script.")
        self.assertEqual(marker.read_text(), "ran")
        self.mac.set_sync_mode(f'"{sys.executable}" -c "import sys; print(\'two devices edited one note\'); sys.exit(3)"')
        with self.assertRaisesRegex(SyncError, "two devices edited one note"):
            self.mac.sync_now()
        handoff.send(self.mac, "Will wait", to=["asus"], sender=MAC)
        self.assertIn("Not sent yet: two devices edited one note", handoff.deliver(self.mac))
        # a failed sync is not tried again every round
        live, _ = self.watcher(self.mac)
        self.assertEqual(live.tick(), "failed")
        self.mac.set_sync_mode(f'"{sys.executable}" "{script}"')
        marker.unlink()
        self.assertEqual(live.tick(), "", "it waits before the next try")
        self.assertFalse(marker.exists())
        live.wake()  # a message saved in the app asks for a round now
        self.assertEqual(live.tick(), "synced")
        self.assertTrue(marker.exists())

    def test_it_looks_more_often_while_a_conversation_is_going(self):
        for brain in (self.mac, self.asus):
            brain.set_sync_mode("auto")
        live, _ = self.watcher(self.asus)
        live.tick()
        self.assertEqual(live.wait_seconds(), livesync.EVERY_SECONDS, "a quiet brain is asked every 20 s")

        # A message arrives: for ten minutes the next look comes after 5 s, because an answer usually follows.
        handoff.send(self.mac, "Are you there?", to=["asus"], sender=MAC)
        handoff.deliver(self.mac)
        self.assertEqual(live.tick(), "synced")
        self.assertEqual(live.wait_seconds(), livesync.TALKING_EVERY_SECONDS)
        with mock.patch.object(livesync.time, "monotonic", return_value=livesync.time.monotonic() + livesync.TALKING_FOR_SECONDS + 1):
            self.assertEqual(live.wait_seconds(), livesync.EVERY_SECONDS, "and then it is quiet again")

        # The same on the side that writes: a message saved here, or one the app asks to send now.
        writer, _ = self.watcher(self.mac)
        writer.tick()
        self.assertEqual(writer.wait_seconds(), livesync.EVERY_SECONDS)
        handoff.send(self.mac, "One more", to=["asus"], sender=MAC)
        self.assertEqual(writer.tick(), "synced")
        self.assertEqual(writer.wait_seconds(), livesync.TALKING_EVERY_SECONDS)
        waker, _ = self.watcher(self.asus)
        waker.wake()
        self.assertEqual(waker.wait_seconds(), livesync.TALKING_EVERY_SECONDS)

        # A change that is no message between computers leaves the pace alone.
        self.asus.sync_now()  # it has every message so far
        quiet, _ = self.watcher(self.asus)
        self.assertEqual(quiet.tick(), "")
        self.mac.write("notes/plain.md", "---\ntitle: Plain\n---\n")
        self.mac.sync_now()
        self.assertEqual(quiet.tick(), "synced")
        self.assertEqual(quiet.wait_seconds(), livesync.EVERY_SECONDS)

    def test_a_pull_made_outside_cairn_reaches_the_open_pages_too(self):
        live, handler = self.watcher(self.asus)
        live.tick()
        self.mac.set_sync_mode("auto")
        note = handoff.send(self.mac, "Pulled by a script", to=["asus"], sender=MAC)
        handoff.deliver(self.mac)
        git(self.asus.root, "pull", "--quiet", "origin", "main")  # the user's own sync script, a terminal
        self.assertEqual(live.tick(), "changed 1")
        self.assertEqual(handler.changes.since(0, handler.changes.epoch, "a page")["paths"], [note["path"]])


if __name__ == "__main__":
    unittest.main()

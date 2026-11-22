"""Work left for an AI app of this computer is started by Cairn, and `ask` waits for its answer (cairn/runner.py)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cairn import cli, core, handoff, handover, runner, vibe, watch  # noqa: F401
from cairn.brain import Brain

MAC = "MacBook Pro 15,4"
ASUS = "ASUS EXPERTBOOK P5405CSA"


class RunnerCase(unittest.TestCase):
    """One brain, two computers. The Mac leaves work; the tests then look at it as the ASUS."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.brain = Brain(self.tmp / "repo", "brain")
        self.brain.init()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for patch in (
            mock.patch.dict(os.environ, {"CAIRN_DEVICE": MAC, "CAIRN_HOME": str(self.tmp / "home")}),
            mock.patch.dict(os.environ, {"CAIRN_NO_RUNNER": ""}),
            mock.patch.object(handoff, "deliver", return_value=""),
            mock.patch.object(handoff, "refresh", return_value=""),
            mock.patch.object(runner, "program", side_effect=lambda agent: f"/bin/{agent}"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(handover._soon.clear)
        with self.on(ASUS):
            vibe.start(self.brain, "Lab writer", agent="vector")
        self.session = vibe.start(self.brain, "Neon Coast 1.6: touch controls", agent="vector")["path"].rsplit("/", 1)[-1][:-3]
        self.ran: list[tuple] = []

    @staticmethod
    def on(device: str):
        return mock.patch.dict(os.environ, {"CAIRN_DEVICE": device})

    def run_fake(self, said: str = "Built. 12 tests pass.", problem: str = ""):
        def run(agent, where, title, text, folder, minutes=runner.RUN_MINUTES):
            self.ran.append((agent, where, title, text, folder))
            return said, problem
        return run

    def thread(self) -> dict:
        return handoff.threads(self.brain)[0]

    def says(self) -> list[tuple[str, str]]:
        return [(message["from"], message["says"]) for message in self.thread()["messages"]]


class Waiting(RunnerCase):
    def test_work_left_for_an_app_that_starts_itself_is_started_once(self):
        handover.hand_session(self.brain, self.session, ASUS, agent="opencode")
        self.assertEqual(runner.waiting(self.brain), [], "the computer that left it does not start it")
        with self.on(ASUS):
            self.assertEqual([(thread["title"], agent) for thread, agent in runner.waiting(self.brain)],
                             [("Take over: Neon Coast 1.6: touch controls", "opencode")])
            lines = runner.run_waiting(self.brain, self.run_fake())
            self.assertEqual(lines, ["Take over: Neon Coast 1.6: touch controls: done by OpenCode"])
            self.assertEqual(runner.run_waiting(self.brain, self.run_fake()), [], "what it answered is not run again")
        self.assertEqual(self.says(), [(MAC, "note"), (ASUS, "picked-up"), (ASUS, "done")])
        self.assertFalse(self.thread()["open"])
        agent, where, title, text, folder = self.ran[0]
        self.assertEqual((agent, where, folder, len(self.ran)), ("opencode", "/bin/opencode", self.brain.dir.parent, 1))
        self.assertIn("Goal: Neon Coast 1.6: touch controls", text)
        self.assertIn("Nobody is at this computer", text)
        done = handoff.body_of(self.brain, self.thread()["messages"][-1]["path"])
        self.assertIn("Built. 12 tests pass.", done)

    def test_while_one_run_is_at_work_no_other_starts(self):
        handover.hand_session(self.brain, self.session, ASUS, agent="opencode")
        mark = self.tmp / "home" / "runner.busy"
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text("another run", encoding="utf-8")
        with self.on(ASUS), mock.patch.object(runner.subprocess, "Popen") as started:
            self.assertEqual(runner.start_soon(self.brain), 0)
            self.assertEqual(runner.run_waiting(self.brain, self.run_fake()), [])
            started.assert_not_called()
            old = runner.time.time() - (runner.RUN_MINUTES + 11) * 60
            os.utime(mark, (old, old))  # a run that died: its mark stops counting
            self.assertEqual(len(runner.run_waiting(self.brain, self.run_fake())), 1)
        self.assertFalse(mark.exists())

    def test_an_app_that_cannot_be_started_is_left_for_its_chat(self):
        handover.hand_session(self.brain, self.session, ASUS, agent="vector")
        with self.on(ASUS):
            self.assertEqual(runner.run_waiting(self.brain, self.run_fake()), [])
        self.assertEqual(self.says(), [(MAC, "note")])

    def test_a_handoff_that_names_no_app_is_left_alone(self):
        handoff.send(self.brain, "Look at the build", "It is red since noon.", to=[ASUS])
        with self.on(ASUS):
            self.assertEqual(runner.run_waiting(self.brain, self.run_fake()), [])
        self.assertEqual(self.ran, [])

    def test_one_left_for_every_computer_is_not_started(self):
        handoff.send(self.brain, "For all of you", "Agent: opencode", to=["all"])
        with self.on(ASUS):
            self.assertEqual(runner.waiting(self.brain), [])

    def test_an_app_that_fails_leaves_it_picked_up_with_the_reason(self):
        handover.hand_session(self.brain, self.session, ASUS, agent="opencode")
        with self.on(ASUS):
            lines = runner.run_waiting(self.brain, self.run_fake("half an answer", "it ended with code 1 and no answer"))
        self.assertIn("OpenCode did not finish", lines[0])
        self.assertEqual(self.says(), [(MAC, "note"), (ASUS, "picked-up"), (ASUS, "note")])
        self.assertTrue(self.thread()["open"], "a chat there can still take it on")
        self.assertIn("half an answer", handoff.body_of(self.brain, self.thread()["messages"][-1]["path"]))

    def test_an_app_that_is_not_installed_is_said_once(self):
        handover.hand_session(self.brain, self.session, ASUS, agent="opencode")
        with self.on(ASUS), mock.patch.object(runner, "program", return_value=""):
            self.assertIn("is not installed here", runner.run_waiting(self.brain, self.run_fake())[0])
            self.assertEqual(runner.run_waiting(self.brain, self.run_fake()), [])
        self.assertEqual((self.says(), self.ran), ([(MAC, "note"), (ASUS, "note")], []))

    def test_the_watcher_s_pass_starts_it(self):
        handover.hand_session(self.brain, self.session, ASUS, agent="opencode")
        with self.on(ASUS), mock.patch.object(runner.subprocess, "Popen") as started:
            lines = watch.watch_once(self.brain, opencode=False, vector=False)
        self.assertIn("Handoffs: starting the app asked for on 1 left for this computer", lines)
        command = started.call_args.args[0]
        self.assertEqual(command[1:3] + command[-3:], ["-m", "cairn", "handoff", "run", "--offline"], "a process of its own: a pass that ends does not end the app")
        self.assertEqual(command[command.index("--root") + 1], str(self.brain.root))
        self.assertIn(str(Path(runner.__file__).resolve().parent.parent), started.call_args.kwargs["env"]["PYTHONPATH"])
        with self.on(ASUS), mock.patch.dict(os.environ, {"CAIRN_NO_RUNNER": "1"}), mock.patch.object(runner, "start_soon") as soon:
            watch.watch_once(self.brain, opencode=False, vector=False)
        soon.assert_not_called()


class Apps(unittest.TestCase):
    def test_opencode_s_answer_is_the_text_of_its_last_message(self):
        events = [
            {"type": "text", "part": {"messageID": "m1", "text": "Let me look."}},
            {"type": "tool_use", "part": {"messageID": "m1", "tool": "shell"}},
            {"type": "text", "part": {"messageID": "m2", "text": "It printed 4."}},
            {"type": "text", "part": {"messageID": "m2", "text": "Nothing else changed."}},
        ]
        output = "\n".join(["not json", *map(json.dumps, events)])
        self.assertEqual(runner.answer_of("opencode", output), ("It printed 4.\n\nNothing else changed.", ""))
        failed = json.dumps({"type": "error", "error": {"message": "the model said no"}})
        self.assertEqual(runner.answer_of("opencode", failed), ("", "the model said no"))
        self.assertEqual(runner.answer_of("another", " plain words \n"), ("plain words", ""), "an app that prints its answer as it is")

    def test_the_commands(self):
        self.assertEqual(runner.command("opencode", "/bin/opencode", "T", "do it"),
                         ["/bin/opencode", "run", "--auto", "--format", "json", "--title", "T", "do it"])

    def test_a_real_program_is_run_and_stopped_at_the_limit(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        with mock.patch.object(runner, "command", return_value=[sys.executable, "-c", "print('  hello  ')"]):
            self.assertEqual(runner.run_agent("another", "x", "T", "text", folder), ("hello", ""))
        with mock.patch.object(runner, "command", return_value=[sys.executable, "-c", "import time; time.sleep(30)"]):
            said, problem = runner.run_agent("another", "x", "T", "text", folder, minutes=0.02)
        self.assertEqual(said, "")
        self.assertIn("it was stopped after", problem)
        with mock.patch.object(runner, "command", return_value=[str(folder / "no-such-program")]):
            self.assertIn("could not be started", runner.run_agent("another", "x", "T", "text", folder)[1])


class Ask(RunnerCase):
    def answer_as_asus(self, run):
        """What the other computer does while this one waits: its watcher's pass."""
        def sleep(seconds):
            with self.on(ASUS):
                runner.run_waiting(self.brain, run)
        return mock.patch.object(runner.time, "sleep", side_effect=sleep)

    def test_ask_leaves_the_work_and_comes_back_with_the_answer(self):
        with self.answer_as_asus(self.run_fake("42 files.")):
            done, said = runner.ask(self.brain, "Phone prompt: Neon Coast", "How many files are there?", "asus", "opencode")
        self.assertEqual((done, said), (True, "42 files."))
        self.assertEqual(handover.agent_asked(handoff.body_of(self.brain, self.thread()["messages"][0]["path"])), "opencode")
        self.assertIn("How many files are there?", self.ran[0][3])

    def test_ask_says_why_when_the_app_did_not_finish(self):
        with self.answer_as_asus(self.run_fake("", "it ended with code 1 and no answer")):
            done, said = runner.ask(self.brain, "Phone prompt", "Do it.", ASUS, "opencode")
        self.assertFalse(done)
        self.assertIn("OpenCode did not finish this", said)

    def test_ask_gives_up_after_the_wait_and_leaves_the_work_there(self):
        with mock.patch.object(runner.time, "sleep"):
            done, said = runner.ask(self.brain, "Phone prompt", "Do it.", ASUS, "opencode", wait_seconds=0)
        self.assertFalse(done)
        self.assertIn(f"{ASUS} has not answered", said)
        self.assertTrue(self.thread()["open"])

    def test_ask_from_the_command_line(self):
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with self.answer_as_asus(self.run_fake("Done from the terminal.")), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--root", str(self.brain.root), "handoff", "ask", "Phone prompt: a chat", "--to", "asus",
                             "--agent", "opencode", "-m", "Say hello."])
        self.assertEqual((code, out.getvalue().strip()), (0, "Done from the terminal."))
        self.assertIn(f"Left for asus: brain/handoffs/", err.getvalue())

    def test_run_from_the_command_line_says_when_nothing_waits(self):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--root", str(self.brain.root), "handoff", "run"])
        self.assertEqual((code, out.getvalue().strip()), (0, "Nothing is left here for an app that Cairn can start."))


if __name__ == "__main__":
    unittest.main()

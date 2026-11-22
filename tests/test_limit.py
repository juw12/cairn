"""The load limit: how hard a computer may be worked for how long (cairn/limit.py)."""

import contextlib
import datetime as dt
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cairn import cli, core, handoff, handover, limit, mcp, runner, vibe, watch
from cairn.brain import Brain
from cairn.server import _with_limit

MAC = "MacBook Pro 15,4"
ASUS = "ASUS EXPERTBOOK P5405CSA"
NOW = dt.datetime(2026, 11, 22, 16, 14)
LIMIT = dict(limit.DEFAULTS)


def looks(minutes: int, cpu: int, memory: int = 70, until: dt.datetime = NOW, step: int = 1) -> list:
    """One look a minute for `minutes` minutes, the last one at `until`."""
    return [[(until - dt.timedelta(minutes=back)).isoformat(timespec="seconds"), cpu, memory]
            for back in range(minutes - 1, -1, -step)]


class TheFile(unittest.TestCase):
    def test_the_file_reads_back_what_was_written(self):
        wanted = {"on": False, "processor": 60, "minutes": 30, "memory": 85, "restart_hours": 48}
        self.assertEqual(limit.parse(limit.file_text(wanted)), wanted)
        self.assertEqual(limit.parse(limit.file_text(limit.DEFAULTS)), limit.DEFAULTS)

    def test_no_file_and_an_unreadable_line_keep_the_defaults(self):
        self.assertEqual(limit.parse(""), limit.DEFAULTS)
        self.assertEqual(limit.parse("- processor: lots\n- minutes:\n- on: maybe\n"), limit.DEFAULTS)

    def test_a_person_s_own_spelling_is_read(self):
        text = "my own words\r\n\r\nProcessor: 70%\r\n* Minutes : 45\r\n  - restart hours: 0   # never\r\n- ON: off\r\n"
        self.assertEqual(limit.parse(text), {"on": False, "processor": 70, "minutes": 45, "memory": 90, "restart_hours": 0})

    def test_a_slip_cannot_switch_the_limit_off_by_its_size(self):
        """A number outside its bounds is brought inside them: 750 is not "no limit"."""
        read = limit.parse("- processor: 750\n- minutes: 1\n- memory: 5\n- restart hours: 99999\n")
        self.assertEqual((read["processor"], read["minutes"], read["memory"], read["restart_hours"]), (100, 10, 50, 720))

    def test_the_file_is_not_a_note(self):
        self.assertIn(limit.FILE, core.SPECIAL_FILES)


class TheJudgement(unittest.TestCase):
    def test_an_hour_at_eighty_percent_is_over_though_each_minute_is_only_medium(self):
        """What happened: the level said "medium" for 100 hours, so nothing ever said stop."""
        from cairn import machine

        self.assertEqual(machine.load_level(80, 74, 80)[0], "medium")
        reading = limit.judge({"looks": looks(60, 80)}, LIMIT, NOW)
        self.assertEqual((reading["state"], reading["known"], reading["cpu_avg"]), ("over", True, 80))
        self.assertEqual(reading["why"], ["processor 80% busy on average over the last 60 min (limit 75%)"])
        self.assertEqual(reading["since"], "2026-11-22T16:14")

    def test_a_busy_quarter_of_an_hour_is_not(self):
        """A build is allowed to use the whole processor: the limit is about the stretch."""
        reading = limit.judge({"looks": looks(60, 20)[:45] + looks(15, 100)}, LIMIT, NOW)
        self.assertEqual((reading["state"], reading["cpu_avg"]), ("ok", 40))

    def test_it_is_not_judged_before_most_of_the_stretch_was_seen(self):
        self.assertEqual(limit.judge({"looks": looks(44, 100)}, LIMIT, NOW)["state"], "ok")
        self.assertEqual(limit.judge({"looks": looks(45, 100)}, LIMIT, NOW)["state"], "over")

    def test_two_looks_in_one_minute_cover_one_minute(self):
        """A watcher's pass and the minute's own look both land in a minute: 23 minutes are not an hour."""
        doubled = [look for pair in zip(looks(23, 100), looks(23, 100)) for look in pair]
        self.assertEqual(len(doubled), 46)
        self.assertEqual(limit.judge({"looks": doubled}, LIMIT, NOW)["state"], "ok")

    def test_once_over_it_has_to_fall_five_points_under_to_count_as_rested(self):
        still = limit.judge({"looks": looks(60, 72), "over_since": "2026-11-22T14:10"}, LIMIT, NOW)
        self.assertEqual((still["state"], still["since"]), ("over", "2026-11-22T14:10"))
        self.assertEqual(still["why"], ["processor 72% busy on average over the last 60 min (limit 75%, rested below 70%)"])
        self.assertEqual(limit.judge({"looks": looks(60, 72)}, LIMIT, NOW)["state"], "ok", "72 was never over by itself")
        self.assertEqual(limit.judge({"looks": looks(60, 69), "over_since": "2026-11-22T14:10"}, LIMIT, NOW)["state"], "ok")

    def test_memory_that_stays_full_is_over_and_one_full_minute_is_not(self):
        full = limit.judge({"looks": looks(15, 30, memory=94)}, LIMIT, NOW)
        self.assertEqual((full["state"], full["why"]), ("over", ["memory 94% or more in use for 15 min (limit 90%)"]))
        dipped = looks(15, 30, memory=94)
        dipped[7][2] = 70
        self.assertEqual(limit.judge({"looks": dipped}, LIMIT, NOW)["state"], "ok")
        self.assertEqual(limit.judge({"looks": looks(5, 30, memory=99)}, LIMIT, NOW)["state"], "ok")

    def test_both_reasons_are_given(self):
        reading = limit.judge({"looks": looks(60, 84, memory=94)}, LIMIT, NOW)
        self.assertEqual(len(reading["why"]), 2)

    def test_a_restart_is_due_after_the_hours_named_whatever_the_load(self):
        reading = limit.judge({"looks": looks(60, 10), "uptime_h": 100}, LIMIT, NOW)
        self.assertEqual((reading["state"], reading["restart"]), ("ok", "up 100 h since restart (due after 72 h)"))
        self.assertEqual(limit.judge({"looks": looks(60, 10), "uptime_h": 71}, LIMIT, NOW)["restart"], "")
        self.assertEqual(limit.judge({"looks": looks(60, 10), "uptime_h": 900}, {**LIMIT, "restart_hours": 0}, NOW)["restart"], "")

    def test_nothing_is_claimed_when_nobody_has_looked_lately(self):
        """Cairn was closed or the computer slept: an hour-old average says nothing about now."""
        old = limit.judge({"looks": looks(60, 100, until=NOW - dt.timedelta(minutes=6)), "over_since": "2026-11-22T14:10"}, LIMIT, NOW)
        self.assertEqual((old["state"], old["known"], old["why"]), ("ok", False, []))
        self.assertEqual(limit.judge({}, LIMIT, NOW)["known"], False)
        self.assertEqual(limit.judge(None, LIMIT, NOW)["known"], False)

    def test_switched_off_it_judges_nothing(self):
        reading = limit.judge({"looks": looks(60, 100, memory=99), "uptime_h": 500}, {**LIMIT, "on": False}, NOW)
        self.assertEqual((reading["state"], reading["known"], reading["why"], reading["restart"]), ("off", False, [], ""))

    def test_a_longer_stretch_and_a_lower_limit_are_followed(self):
        strict = {**LIMIT, "processor": 50, "minutes": 120}
        self.assertEqual(limit.judge({"looks": looks(60, 60)}, strict, NOW)["state"], "ok", "an hour is half of that stretch")
        self.assertEqual(limit.judge({"looks": looks(120, 60)}, strict, NOW)["state"], "over")

    def test_a_broken_log_is_read_as_far_as_it_goes(self):
        log = {"looks": [["not a time", 99, 99], "nonsense", [NOW.isoformat(), "99", None], [NOW.isoformat(), True, 50]] + looks(60, 80)}
        self.assertEqual(limit.judge(log, LIMIT, NOW)["cpu_avg"], 80)


class TheLooks(unittest.TestCase):
    def test_a_look_is_added_and_old_ones_are_dropped(self):
        old = looks(3, 50, until=NOW - dt.timedelta(minutes=limit.KEEP_MINUTES + 1))
        log = limit.add_look({"looks": old + looks(10, 50, until=NOW - dt.timedelta(minutes=1))}, NOW,
                             {"cpu_pct": 84, "ram_pct": 74, "commit_pct": 80, "uptime_hours": 100}, LIMIT)
        self.assertEqual(len(log["looks"]), 11)
        self.assertEqual(log["looks"][-1], ["2026-11-22T16:14:00", 84, 80], "memory is the fuller of RAM and what is promised")
        self.assertEqual(log["uptime_h"], 100)

    def test_a_computer_that_cannot_be_read_adds_nothing(self):
        log = limit.add_look({"looks": looks(5, 50)}, NOW, None, LIMIT)
        self.assertEqual(len(log["looks"]), 5)

    def test_going_over_is_remembered_with_its_time_and_forgotten_when_rested(self):
        log = {"looks": looks(59, 90, until=NOW - dt.timedelta(minutes=1))}
        log = limit.add_look(log, NOW, {"cpu_pct": 90, "ram_pct": 50}, LIMIT)
        self.assertEqual(log["over_since"], "2026-11-22T16:14")
        later = NOW + dt.timedelta(minutes=1)
        log = limit.add_look(log, later, {"cpu_pct": 90, "ram_pct": 50}, LIMIT)
        self.assertEqual(log["over_since"], "2026-11-22T16:14", "the time it went over, not the time of the last look")
        rested = NOW + dt.timedelta(minutes=90)
        log = limit.add_look({**log, "looks": looks(60, 20, until=rested - dt.timedelta(minutes=1))}, rested, {"cpu_pct": 20, "ram_pct": 50}, LIMIT)
        self.assertEqual(log["over_since"], "")

    def test_a_computer_that_slept_starts_fresh(self):
        log = {"looks": looks(60, 90), "over_since": "2026-11-22T15:00"}
        woke = NOW + dt.timedelta(hours=3)
        log = limit.add_look(log, woke, {"cpu_pct": 95, "ram_pct": 50}, LIMIT)
        self.assertEqual(log["over_since"], "", "one look after three hours of sleep is not an hour of work")
        self.assertEqual(limit.judge(log, LIMIT, woke)["state"], "ok")


class BrainCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.brain = Brain(self.tmp / "repo", "brain")
        self.brain.init()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for patch in (
            mock.patch.dict(os.environ, {"CAIRN_DEVICE": MAC, "CAIRN_HOME": str(self.tmp / "home"), "CAIRN_NO_RUNNER": ""}),
            mock.patch.object(handoff, "deliver", return_value=""),
            mock.patch.object(handoff, "refresh", return_value=""),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(handover._soon.clear)

    def work(self, minutes: int = 60, cpu: int = 84, memory: int = 80, uptime: int = 100, until: dt.datetime | None = None):
        """This computer worked that hard for that long, a look a minute, the last look now."""
        until = until or dt.datetime.now().replace(microsecond=0)
        reading = None
        for back in range(minutes - 1, -1, -1):
            reading = limit.look(self.brain, {"cpu_pct": cpu, "ram_pct": memory, "commit_pct": None, "uptime_hours": uptime},
                                 until - dt.timedelta(minutes=back))
        return reading

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--root", str(self.brain.root), *args])
        return code, out.getvalue(), err.getvalue()


class ThisComputer(BrainCase):
    def test_the_looks_are_kept_next_to_the_watcher_s_state_never_in_the_notes(self):
        self.work(3)
        path = limit.log_path(self.brain)
        self.assertEqual(path.name, limit.LOG_FILE)
        self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["looks"]), 3)
        self.assertEqual([note["path"] for note in self.brain.notes() if "limit" in note["path"].lower()], [])

    def test_an_hour_of_work_puts_it_over_and_the_brain_s_own_limit_is_followed(self):
        self.assertEqual(self.work(60, cpu=84)["state"], "over")
        self.assertTrue(limit.over(self.brain))
        limit.set_limits(self.brain, processor=95)
        self.assertFalse(limit.over(self.brain), "the same hour is under a limit of 95")
        self.assertEqual(limit.limits(self.brain)["processor"], 95)
        self.assertEqual(limit.parse(self.brain.read(limit.FILE)["text"])["minutes"], 60, "what was not named keeps its number")

    def test_a_number_out_of_bounds_is_refused_with_its_bounds(self):
        with self.assertRaisesRegex(limit.LimitError, "processor takes a number from 30 to 100, not 5"):
            limit.set_limits(self.brain, processor=5)
        self.assertIsNone(limit.file(self.brain), "nothing was written")

    def test_a_log_that_cannot_be_read_holds_nothing_back(self):
        limit.log_path(self.brain).write_text("{ half a file", encoding="utf-8")
        self.assertFalse(limit.over(self.brain))
        self.assertEqual(limit.status(self.brain)["known"], False)

    def test_the_watcher_s_look_at_the_day_is_the_look_the_limit_is_judged_from(self):
        now = dt.datetime.now().replace(microsecond=0)
        for back in range(59, -1, -1):
            watch.sync_activity(self.brain, now - dt.timedelta(minutes=back), idle=0,
                                load={"cpu_pct": 84, "ram_pct": 74, "commit_pct": 80, "uptime_hours": 100, "level": "medium"}, name=MAC)
        reading = limit.status(self.brain)
        self.assertEqual((reading["state"], reading["cpu_avg"]), ("over", 84))
        self.assertEqual(reading["restart"], "up 100 h since restart (due after 72 h)")


class WhatIsSaid(BrainCase):
    def test_an_assistant_is_told_at_its_next_step_and_only_while_it_is_over(self):
        code, out, _ = self.run_cli("vibe", "start", "Neon Coast 1.6: touch controls", "--agent", "vector")
        self.assertEqual(code, 0)
        self.assertNotIn("LOAD LIMIT", out)
        self.work(60, cpu=84, uptime=10)
        code, out, _ = self.run_cli("vibe", "log", "tests green")
        self.assertIn("LOAD LIMIT (set in Cairn): this computer is over its limit since ", out)
        self.assertIn("processor 84% busy on average over the last 60 min (limit 75%)", out)
        self.assertIn("Start nothing heavy here until it has rested", out)
        self.assertNotIn("RESTART DUE", out)
        code, out, _ = self.run_cli("vibe", "start", "Lab writer", "--agent", "vector")
        self.assertIn("LOAD LIMIT", out, "a chat that starts now is told too")
        self.work(60, cpu=20, uptime=10)
        code, out, _ = self.run_cli("vibe", "log", "carrying on")
        self.assertNotIn("LOAD LIMIT", out)

    def test_a_restart_that_is_due_is_said_without_calling_it_over(self):
        self.work(60, cpu=10, uptime=100)
        text = limit.notice(self.brain)
        self.assertIn("RESTART DUE (set in Cairn): this computer is up 100 h since restart (due after 72 h).", text)
        self.assertIn("Do not restart it yourself.", text)
        self.assertNotIn("LOAD LIMIT", text)

    def test_an_assistant_that_never_logs_asks_the_mcp_tool(self):
        self.assertNotIn("LOAD LIMIT", mcp._tool_priority(self.brain, {}))
        self.work(60, cpu=84)
        told = mcp._tool_priority(self.brain, {})
        self.assertTrue(told.startswith("No session is running, so there is no order."))
        self.assertIn("LOAD LIMIT (set in Cairn)", told)

    def test_the_command_shows_the_limit_this_computer_and_the_others(self):
        self.work(60, cpu=84, uptime=10)
        self.brain.write("machines/asus-expertbook-p5405csa.md", core.serialize_note(
            {"title": ASUS, "type": "note", "tags": ["machine"], "machine_at": "2026-11-22T16:14", "load": "medium", "limit": "over",
             "limit_since": "2026-11-22T09:03", "limit_why": "processor 82% busy on average over the last 60 min (limit 75%)",
             "limit_restart": "up 100 h since restart (due after 72 h)"}, "text\n"))
        self.brain.write("machines/old-laptop.md", core.serialize_note(
            {"title": "Old laptop", "type": "note", "tags": ["machine"], "machine_at": "2026-11-01T08:00", "load": "light"}, "text\n"))
        self.brain.write("machines/asus-expertbook-p5405csa-activity.md", core.serialize_note(
            {"title": f"{ASUS} activity", "type": "note", "tags": ["activity"]}, "text\n"))
        code, out, _ = self.run_cli("vibe", "limit")
        self.assertEqual(code, 0)
        self.assertIn("Load limit (CAIRN-LIMIT.md, the same for every computer of this brain): on", out)
        self.assertIn("processor 75% on average over 60 min · memory 90% for 15 min · restart after 72 h", out)
        self.assertIn(f"This computer ({MAC}): OVER its limit since ", out)
        self.assertIn(f"{ASUS}: OVER its limit since 2026-11-22 09:03: processor 82% busy on average over the last 60 min (limit 75%); "
                      "due for a restart: up 100 h since restart (due after 72 h) (its note of 2026-11-22 16:14)", out)
        self.assertIn("Old laptop: its note does not say yet (it needs this build of Cairn)", out)
        self.assertNotIn("activity:", out, "only notes of computers are read")
        self.assertIsNone(limit.file(self.brain), "looking writes nothing")

    def test_the_command_changes_it_and_switches_it(self):
        code, out, _ = self.run_cli("vibe", "limit", "--processor", "70", "--minutes", "45")
        self.assertEqual(code, 0)
        self.assertIn("Written to CAIRN-LIMIT.md", out)
        self.assertIn("processor 70% on average over 45 min", out)
        self.assertEqual(limit.limits(self.brain), {"on": True, "processor": 70, "minutes": 45, "memory": 90, "restart_hours": 72})
        code, out, _ = self.run_cli("vibe", "limit", "off")
        self.assertIn("): OFF", out)
        self.assertIn("not judged, the limit is off", out)
        self.assertEqual(limit.limits(self.brain)["on"], False)
        self.assertEqual(limit.limits(self.brain)["processor"], 70, "switching it off keeps the numbers")
        code, out, _ = self.run_cli("vibe", "limit", "on")
        self.assertEqual(limit.limits(self.brain)["on"], True)
        code, _, err = self.run_cli("vibe", "limit", "--memory", "20")
        self.assertEqual(code, 1)
        self.assertIn("memory takes a number from 50 to 100, not 20", err)
        code, _, err = self.run_cli("vibe", "limit", "harder")
        self.assertEqual(code, 1)
        self.assertIn("takes on or off", err)

    def test_before_the_first_look_the_command_says_so(self):
        code, out, _ = self.run_cli("vibe", "limit")
        self.assertIn("not measured yet", out)


class TheNote(BrainCase):
    READING = {"name": MAC, "at": "2026-11-22T16:14", "cpu": "Intel Core i5", "cores": 8, "ram_gb": 8.0, "level": "medium",
               "cpu_pct": 84, "ram_pct": 74, "commit_pct": None, "power": "plugged in 100%", "uptime": "up 100 h (4 d 4 h) since restart",
               "reasons": ["CPU 84% busy"], "top": [], "net": None}

    def test_the_computer_s_note_says_it_for_every_device(self):
        over = limit.judge({"looks": looks(60, 84), "uptime_h": 100}, LIMIT, NOW)
        path, text = watch.machine_note({**self.READING, "limit": over})
        meta = core.parse_note(text)["meta"]
        self.assertEqual((meta["limit"], meta["limit_since"]), ("over", "2026-11-22T16:14"))
        self.assertEqual(meta["limit_why"], "processor 84% busy on average over the last 60 min (limit 75%)")
        self.assertEqual(meta["limit_restart"], "up 100 h since restart (due after 72 h)")
        self.assertIn("- Over its load limit since 2026-11-22 16:14: processor 84% busy on average over the last 60 min (limit 75%). "
                      "Cairn starts no handed-over work here and tells its assistants to start nothing heavy until it has rested. "
                      "Due for a restart: up 100 h since restart (due after 72 h)", text)

    def test_under_its_limit_the_note_says_ok_and_adds_no_line(self):
        _, text = watch.machine_note({**self.READING, "limit": limit.judge({"looks": looks(60, 20)}, LIMIT, NOW)})
        self.assertEqual(core.parse_note(text)["meta"]["limit"], "ok")
        self.assertNotIn("load limit", text)

    def test_a_computer_nobody_measured_claims_nothing(self):
        for reading in (self.READING, {**self.READING, "limit": limit.judge({}, LIMIT, NOW)}):
            _, text = watch.machine_note(reading)
            self.assertFalse(core.parse_note(text)["meta"].get("limit"))

    def test_going_over_and_resting_are_written_at_once(self):
        vibe.start(self.brain, "Neon Coast 1.6: touch controls", agent="vector")
        ok = limit.judge({"looks": looks(60, 20)}, LIMIT, NOW)
        over = limit.judge({"looks": looks(60, 84)}, LIMIT, NOW)
        self.assertTrue(watch.sync_machine(self.brain, {**self.READING, "limit": ok}))
        self.assertFalse(watch.sync_machine(self.brain, {**self.READING, "limit": ok}), "the same is not written again")
        self.assertTrue(watch.sync_machine(self.brain, {**self.READING, "limit": over}), "over is, though load and time are the same")
        self.assertFalse(watch.sync_machine(self.brain, {**self.READING, "limit": over}))
        self.assertTrue(watch.sync_machine(self.brain, {**self.READING, "limit": ok}), "and rested is")

    def test_the_watcher_adds_where_this_computer_stands(self):
        vibe.start(self.brain, "Neon Coast 1.6: touch controls", agent="vector")
        self.work(60, cpu=84)
        self.assertTrue(watch.sync_machine(self.brain, dict(self.READING)))
        meta = core.parse_note(self.brain.read(f"machines/{core.slugify(MAC)}.md")["text"])["meta"]
        self.assertEqual(meta["limit"], "over")

    def test_the_live_page_gets_it_with_the_reading(self):
        self.work(60, cpu=84)
        self.assertEqual(_with_limit(dict(self.READING), self.brain)["limit"]["state"], "over")
        self.assertIsNone(_with_limit(None, self.brain))


class HandedOverWork(BrainCase):
    """The Mac leaves work for OpenCode on the ASUS; the tests then look at it as the ASUS."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(runner, "program", side_effect=lambda agent: f"/bin/{agent}")
        patch.start()
        self.addCleanup(patch.stop)
        with self.on(ASUS):
            vibe.start(self.brain, "Lab writer", agent="vector")
        session = vibe.start(self.brain, "Neon Coast 1.6: touch controls", agent="vector")["path"].rsplit("/", 1)[-1][:-3]
        handover.hand_session(self.brain, session, ASUS, agent="opencode")

    @staticmethod
    def on(device: str):
        return mock.patch.dict(os.environ, {"CAIRN_DEVICE": device})

    def test_it_waits_while_the_computer_is_over_its_limit_and_starts_when_it_has_rested(self):
        self.work(60, cpu=84)
        with self.on(ASUS), mock.patch.object(runner.subprocess, "Popen") as started:
            lines = watch.watch_once(self.brain, opencode=False, vector=False)
        started.assert_not_called()
        self.assertIn("Handoffs: work left for this computer waits, it is over its load limit (cairn vibe limit)", lines)
        with self.on(ASUS):
            self.assertEqual(len(runner.waiting(self.brain)), 1, "nothing was answered: it still waits")
        self.work(60, cpu=20)
        with self.on(ASUS), mock.patch.object(runner.subprocess, "Popen") as started:
            lines = watch.watch_once(self.brain, opencode=False, vector=False)
        started.assert_called_once()
        self.assertIn("Handoffs: starting the app asked for on 1 left for this computer", lines)

    def test_a_computer_over_its_limit_with_nothing_waiting_says_nothing(self):
        self.work(60, cpu=84)
        with self.on(MAC), mock.patch.object(runner.subprocess, "Popen") as started:
            lines = watch.watch_once(self.brain, opencode=False, vector=False)
        started.assert_not_called()
        self.assertEqual([line for line in lines if line.startswith("Handoffs")], [])


if __name__ == "__main__":
    unittest.main()

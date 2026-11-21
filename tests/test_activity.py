"""The day's use of this computer: screen time, processor and memory, added up look by look."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cairn import activity, core, machine, watch
from cairn.brain import Brain

START = dt.datetime(2026, 3, 4, 9, 0, 0)
LOAD = {"cpu_pct": 40, "ram_pct": 60, "level": "light"}


def looks(log, start, minutes, idle=0, load=LOAD):
    """One look a minute for `minutes` minutes after `start`."""
    for minute in range(1, minutes + 1):
        log = activity.add_look(log, start + dt.timedelta(minutes=minute), idle, load)
    return log


class AddLookTests(unittest.TestCase):
    def test_the_first_look_adds_no_time(self):
        log = activity.add_look(None, START, 0, LOAD)
        day = log["days"]["2026-03-04"]
        self.assertEqual((day["awake"], day["screen"], day["cpu_n"]), (0, 0, 0))
        self.assertEqual(log["at"], "2026-03-04T09:00:00")

    def test_minutes_at_the_computer_count_as_screen_time_in_their_hour(self):
        log = looks(activity.add_look(None, START, 0, LOAD), START, 30)
        figures = activity.day_figures(log["days"]["2026-03-04"])
        self.assertEqual((figures["awake"], figures["screen"]), (30, 30))
        self.assertEqual(figures["hours"][9], 30)
        self.assertEqual(sum(figures["hours"]), 30)
        self.assertEqual((figures["first"], figures["last"]), ("09:01", "09:30"))
        self.assertEqual((figures["cpu"], figures["cpu_peak"], figures["ram"], figures["ram_peak"]), (40, 40, 60, 60))

    def test_time_away_from_the_keyboard_is_awake_but_not_screen_time(self):
        log = activity.add_look(None, START, 0, LOAD)
        log = activity.add_look(log, START + dt.timedelta(seconds=60), 30, LOAD)  # used half a minute ago
        log = activity.add_look(log, START + dt.timedelta(seconds=120), 150, LOAD)  # 30 s past the grace
        log = activity.add_look(log, START + dt.timedelta(seconds=180), 900, LOAD)  # long gone
        day = log["days"]["2026-03-04"]
        self.assertEqual(day["awake"], 180)
        self.assertEqual(day["screen"], 60 + 30 + 0)
        self.assertEqual(day["last"], "09:02")

    def test_a_long_gap_is_sleep_and_adds_nothing(self):
        log = looks(activity.add_look(None, START, 0, LOAD), START, 5)
        log = activity.add_look(log, START + dt.timedelta(hours=3), 0, LOAD)
        self.assertEqual(log["days"]["2026-03-04"]["awake"], 300)
        log = activity.add_look(log, START + dt.timedelta(hours=3, minutes=1), 0, LOAD)
        self.assertEqual(log["days"]["2026-03-04"]["awake"], 360)

    def test_peaks_averages_and_heavy_minutes(self):
        log = activity.add_look(None, START, 0, LOAD)
        log = activity.add_look(log, START + dt.timedelta(minutes=1), 0, {"cpu_pct": 20, "ram_pct": 50, "level": "light"})
        log = activity.add_look(log, START + dt.timedelta(minutes=2), 0, {"cpu_pct": 96, "ram_pct": 92, "level": "heavy"})
        log = activity.add_look(log, START + dt.timedelta(minutes=3), 0, {"cpu_pct": None, "ram_pct": 71, "level": "light"})
        figures = activity.day_figures(log["days"]["2026-03-04"])
        self.assertEqual((figures["cpu"], figures["cpu_peak"]), (58, 96))
        self.assertEqual((figures["ram"], figures["ram_peak"], figures["heavy"]), (71, 92, 1))

    def test_a_computer_that_cannot_tell_idle_time_leaves_screen_time_out(self):
        log = looks(activity.add_look(None, START, None, LOAD), START, 10, idle=None)
        figures = activity.day_figures(log["days"]["2026-03-04"])
        self.assertEqual((figures["awake"], figures["screen"], figures["hours"]), (10, "", []))
        self.assertNotIn("screen", activity.day_line("2026-03-04", figures))

    def test_a_new_day_starts_its_own_count_and_old_days_leave_the_state(self):
        log = None
        for offset in range(10):
            start = START + dt.timedelta(days=offset)
            log = looks(activity.add_look(log, start, 0, LOAD), start, 2)
        self.assertEqual(len(log["days"]), activity.STATE_DAYS)
        self.assertEqual(sorted(log["days"])[-1], "2026-03-13")
        self.assertEqual(log["days"]["2026-03-13"]["awake"], 120)


class NoteTests(unittest.TestCase):
    def test_a_line_reads_back_as_written(self):
        log = looks(activity.add_look(None, START, 0, LOAD), START, 90)
        figures = activity.day_figures(log["days"]["2026-03-04"])
        line = activity.day_line("2026-03-04", figures)
        self.assertTrue(line.startswith("- 2026-03-04 | screen 90 | awake 90 | cpu 40 | cpu_peak 40 | ram 60 | ram_peak 60 | heavy 0 | first 09:01 | last 10:30 | hours 0 0"))
        self.assertEqual(activity.parse_line(line), ("2026-03-04", figures))
        self.assertIsNone(activity.parse_line("- not a day | screen 4"))
        self.assertIsNone(activity.parse_line("Written by the Cairn session watcher"))

    def test_the_idle_time_of_a_mac_is_read_from_ioreg(self):
        text = '    | |   "HIDIdleTime" = 4875123456\n    | |   "HIDParameters" = {}'
        self.assertAlmostEqual(activity.parse_ioreg_idle(text), 4.875123456)
        self.assertIsNone(activity.parse_ioreg_idle("nothing here"))
        self.assertIsNone(activity.parse_ioreg_idle(""))

    def test_the_note_names_its_computer_and_keeps_older_days(self):
        name = "MacBook Pro 15,4"
        self.assertEqual(activity.note_path(name), "machines/macbook-pro-15-4-activity.md")
        first = looks(activity.add_look(None, START, 0, LOAD), START, 60)
        text = activity.note_text(name, first["days"], "", START + dt.timedelta(hours=1))
        later = START + dt.timedelta(days=1)
        second = looks(activity.add_look(None, later, 0, LOAD), later, 20)
        text = activity.note_text(name, second["days"], text, later + dt.timedelta(minutes=20))
        parsed = core.parse_note(text)
        meta = parsed["meta"]
        self.assertEqual((meta["title"], meta["type"], meta["tags"], meta["machine"]), ("MacBook Pro 15,4 activity", "note", ["activity"], name))
        self.assertEqual(meta["activity_at"], "2026-03-05T09:20")
        self.assertNotIn("machine_at", meta, "it is not a load note: the Vibe page must not read it as one")
        self.assertNotIn("publish", meta)
        days = activity.parse_days(parsed["body"])
        self.assertEqual(list(days), ["2026-03-05", "2026-03-04"])
        self.assertEqual((days["2026-03-04"]["screen"], days["2026-03-05"]["screen"]), (60, 20))
        self.assertIn("No window titles, app names or keys are read", parsed["body"])

    def test_only_the_last_days_are_kept(self):
        old = "\n".join(f"- {(START - dt.timedelta(days=offset)).strftime('%Y-%m-%d')} | awake 5" for offset in range(1, 200))
        text = activity.note_text("PC", {}, core.serialize_note({"title": "PC activity"}, old + "\n"), START)
        self.assertEqual(len(activity.parse_days(core.parse_note(text)["body"])), activity.KEEP_DAYS)

    def test_when_the_note_is_due(self):
        empty = activity.add_look(None, START, 0, LOAD)
        self.assertFalse(activity.note_due(empty, START), "nothing to write yet")
        log = looks(empty, START, 2)
        now = START + dt.timedelta(minutes=2)
        self.assertTrue(activity.note_due(log, now), "never written")
        log["written"] = now.isoformat()
        self.assertFalse(activity.note_due(log, now + dt.timedelta(minutes=activity.NOTE_MINUTES - 1)))
        self.assertTrue(activity.note_due(log, now + dt.timedelta(minutes=activity.NOTE_MINUTES)))
        log["written"] = "2026-03-04T23:59:00"
        self.assertTrue(activity.note_due(log, dt.datetime(2026, 3, 5, 0, 0, 30)), "a new day")

    def test_a_lost_state_carries_on_from_the_note(self):
        log = looks(activity.add_look(None, START, 0, LOAD), START, 45)
        text = activity.note_text("PC", log["days"], "", START)
        seeded = activity.seed_days(text)
        figures = activity.day_figures(seeded["2026-03-04"])
        self.assertEqual((figures["screen"], figures["awake"], figures["cpu"], figures["ram_peak"]), (45, 45, 40, 60))
        self.assertEqual(figures["hours"][9], 45)


class SyncTests(unittest.TestCase):
    NAME = "ACME Roadrunner 14"
    PATH = "machines/acme-roadrunner-14-activity.md"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = Brain(Path(self.tmp.name))
        self.brain.init()

    def tearDown(self):
        self.tmp.cleanup()

    def look(self, minute: int, idle=0) -> bool:
        return watch.sync_activity(self.brain, START + dt.timedelta(minutes=minute), idle, LOAD, self.NAME)

    def days(self) -> dict:
        return activity.parse_days(core.parse_note(self.brain.read(self.PATH)["text"])["body"])

    def test_the_note_is_written_with_no_session_live_and_then_twice_an_hour(self):
        self.assertFalse(self.look(0), "the first look has nothing to say")
        self.assertFalse(self.brain.resolve(self.PATH).exists())
        self.assertTrue(self.look(1), "a minute of use is written")
        self.assertEqual(self.days()["2026-03-04"]["screen"], 1)
        written = [minute for minute in range(2, 62) if self.look(minute)]
        self.assertEqual(written, [1 + activity.NOTE_MINUTES, 1 + 2 * activity.NOTE_MINUTES])
        self.assertEqual(self.days()["2026-03-04"]["screen"], 61)
        self.assertFalse((self.brain.dir / "machines" / "acme-roadrunner-14.md").exists(), "the load note is another matter")

    def test_a_lost_state_does_not_start_the_day_again(self):
        for minute in range(0, 12):
            self.look(minute)
        self.assertTrue(self.look(40))  # written again half an hour on (the gap itself adds nothing)
        self.assertEqual(self.days()["2026-03-04"]["awake"], 11)
        watch._state_path(self.brain).unlink()
        self.look(41)
        self.assertEqual(self.days()["2026-03-04"]["awake"], 11, "the day did not start again from nothing")
        self.look(42)
        self.assertEqual(watch._load_state(self.brain)[watch.ACTIVITY_KEY]["days"]["2026-03-04"]["awake"], 12 * 60)

    def test_a_note_changed_under_the_watcher_is_left_for_the_next_pass(self):
        self.look(0)
        with mock.patch.object(self.brain, "write", side_effect=OSError("in use")):
            self.assertFalse(self.look(1))
        self.assertTrue(self.look(2))
        self.assertEqual(self.days()["2026-03-04"]["awake"], 2)

    def test_a_pass_notes_the_day_only_when_asked(self):
        with mock.patch.object(watch, "sync_activity", return_value=True) as sync:
            quiet = watch.watch_once(self.brain, opencode=False, vector=False)
            sync.assert_not_called()
            lines = watch.watch_once(self.brain, opencode=False, vector=False, laptop=True)
        self.assertEqual(quiet, [])
        self.assertEqual(lines, ["Laptop: the day's use noted in machines/"])


class ReadingTests(unittest.TestCase):
    def test_idle_time_never_raises_and_is_a_number_or_nothing(self):
        idle = activity.idle_seconds()
        self.assertTrue(idle is None or idle >= 0)

    def test_the_light_reading_has_the_three_figures_or_is_nothing(self):
        reading = machine.load_now()
        if reading is not None:
            self.assertEqual(sorted(reading), ["cpu_pct", "level", "ram_pct"])
            self.assertIn(reading["level"], machine.LEVELS)

    def test_a_reading_that_fails_is_nothing(self):
        with mock.patch.object(machine, "_memory", side_effect=RuntimeError("no")):
            self.assertIsNone(machine.load_now())


if __name__ == "__main__":
    unittest.main()

"""The computer under the sessions: what it is and how loaded it is (cairn/machine.py)."""

import threading
import time
import unittest
from unittest import mock

from cairn import machine

GB = 2**30


class LoadLevelTests(unittest.TestCase):
    def test_idle_is_light_and_gives_no_reasons(self):
        self.assertEqual(machine.load_level(12, 40, 35), ("light", []))

    def test_a_busy_processor_or_fuller_memory_is_medium(self):
        self.assertEqual(machine.load_level(50, 40), ("medium", ["CPU 50% busy"]))
        self.assertEqual(machine.load_level(10, 75), ("medium", ["memory 75% full"]))
        self.assertEqual(machine.load_level(49, 74), ("light", []))

    def test_flat_out_is_heavy_and_lists_the_worst_first(self):
        self.assertEqual(machine.load_level(85, 60), ("heavy", ["CPU 85% busy"]))
        self.assertEqual(machine.load_level(60, 94), ("heavy", ["memory 94% full", "CPU 60% busy"]))
        level, reasons = machine.load_level(20, 50, 92)
        self.assertEqual(level, "heavy")
        self.assertIn("92%", reasons[0])

    def test_a_missing_reading_is_left_out_rather_than_guessed(self):
        self.assertEqual(machine.load_level(None, 91), ("heavy", ["memory 91% full"]))
        self.assertEqual(machine.load_level(None, None), ("light", []))


class NameTests(unittest.TestCase):
    def test_processor_names_lose_the_legal_marks(self):
        self.assertEqual(machine.clean_cpu_name("Intel(R) Core(TM) Ultra 7 155H"), "Intel Core Ultra 7 155H")
        self.assertEqual(machine.clean_cpu_name("Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz"), "Intel Core i7-9750H")
        self.assertEqual(machine.clean_cpu_name("AMD Ryzen 7 5800X 8-Core Processor"), "AMD Ryzen 7 5800X")
        self.assertEqual(machine.clean_cpu_name(None), "")

    def test_the_brand_is_said_once(self):
        self.assertEqual(
            machine.machine_name("ASUSTeK COMPUTER INC.", "ASUS VIVOBOOK X1_X1"),
            "ASUS VIVOBOOK X1",
        )
        self.assertEqual(machine.machine_name("LENOVO", "ThinkPad X1 Carbon"), "LENOVO ThinkPad X1 Carbon")
        self.assertEqual(machine.machine_name("Dell Inc.", "Dell XPS 13"), "Dell XPS 13")

    def test_placeholders_from_a_self_built_pc_are_dropped(self):
        self.assertEqual(machine.machine_name("System manufacturer", "System Product Name"), "")
        self.assertEqual(machine.machine_name("Gigabyte", "To Be Filled By O.E.M."), "Gigabyte")
        self.assertEqual(machine.machine_name("", ""), "")


class ProcessTests(unittest.TestCase):
    def test_copies_of_one_program_count_as_one_app(self):
        rows = [
            (1, "python.exe", 1 * GB, 30.0),
            (2, "python.exe", 1 * GB, 14.0),
            (3, "llama-server.exe", 12 * GB, 5.0),
            (4, "notepad.exe", GB // 10, 0.0),
        ]
        previous = {1: 10.0, 2: 10.0, 3: 5.0}
        # 10 seconds on 4 cores is 40 processor-seconds: python used 24 of them.
        groups = machine.group_processes(rows, previous, 10, 4)
        self.assertEqual([g["name"] for g in groups], ["llama-server", "python", "notepad"])
        python = groups[1]
        self.assertEqual((python["count"], python["ram_gb"], python["cpu_pct"]), (2, 2.0, 60))
        self.assertEqual(machine.top_text(python), "python x2: 2.0 GB, 60% CPU")
        self.assertEqual(machine.top_text(groups[0]), "llama-server: 12.0 GB")

    def test_uptime_reads_in_minutes_then_hours_then_hours_with_days(self):
        self.assertEqual(machine.uptime_text(12 * 60 + 59), "up 12 min since restart")
        self.assertEqual(machine.uptime_text(5 * 3600 + 3599), "up 5 h since restart")
        self.assertEqual(machine.uptime_text(47 * 3600), "up 47 h since restart")
        self.assertEqual(machine.uptime_text(76 * 3600 + 60), "up 76 h (3 d 4 h) since restart")
        self.assertEqual(machine.uptime_text(None), "")
        self.assertEqual(machine.uptime_text(-1), "")

    def test_a_first_look_has_memory_but_no_processor_share(self):
        groups = machine.group_processes([(1, "node.exe", 2 * GB, 99.0)], {}, 0, 8)
        self.assertEqual(groups, [{"name": "node", "count": 1, "ram_gb": 2.0, "cpu_pct": 0}])

    def test_a_small_app_that_eats_the_processor_still_shows(self):
        rows = [(pid, f"big{pid}.exe", (10 - pid) * GB, 0.0) for pid in range(1, 6)]
        rows.append((9, "spinner.exe", GB // 100, 8.0))
        groups = machine.group_processes(rows, {9: 0.0}, 10, 1, limit=4)
        self.assertEqual(len(groups), 5)
        self.assertEqual(groups[-1]["name"], "spinner")
        self.assertEqual(groups[-1]["cpu_pct"], 80)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        saved = (machine._specs, list(machine._cpu_samples), dict(machine._processes))
        self.addCleanup(self.restore, saved)
        machine._specs = None
        machine._cpu_samples.clear()
        machine._processes.update(at=0.0, times={}, top=[])

    def restore(self, saved):
        machine._specs = saved[0]
        machine._cpu_samples.clear()
        machine._cpu_samples.extend(saved[1])
        machine._processes.clear()
        machine._processes.update(saved[2])

    def patched(self, memory, cpu_times):
        registry = {"SystemManufacturer": "ACME Corp", "SystemProductName": "Roadrunner 14", "ProcessorNameString": "Intel(R) Core(TM) i5 CPU @ 2.40GHz"}
        return (
            mock.patch.object(machine, "_memory", return_value=memory),
            mock.patch.object(machine, "_cpu_times", side_effect=cpu_times),
            mock.patch.object(machine, "_process_rows", return_value=[(1, "python.exe", 3 * GB, 1.0)]),
            mock.patch.object(machine, "_power", return_value="on battery 62%"),
            mock.patch.object(machine, "_uptime_seconds", return_value=76 * 3600.0),
            mock.patch.object(machine, "_registry", side_effect=lambda key, name: registry[name]),
            mock.patch.object(machine.time, "sleep"),
            mock.patch.object(machine, "IS_MAC", False),  # the registry stands in for the model on any system
            mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": ""}),
        )

    def test_a_reading_has_the_specs_the_load_and_what_uses_it(self):
        memory = {"total": 16 * GB, "free": 1 * GB, "ram_pct": 94, "commit_pct": 70}
        patches = self.patched(memory, [(100, 1000), (190, 1100)])
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        reading = machine.snapshot()
        self.assertEqual((reading["name"], reading["cpu"], reading["ram_gb"]), ("ACME Roadrunner 14", "Intel Core i5", 16.0))
        self.assertGreater(reading["cores"], 0)
        self.assertEqual((reading["cpu_pct"], reading["ram_pct"], reading["ram_free_gb"]), (90, 94, 1.0))
        self.assertEqual(reading["level"], "heavy")
        self.assertEqual(reading["reasons"], ["CPU 90% busy", "memory 94% full"])
        self.assertEqual(reading["power"], "on battery 62%")
        self.assertEqual(reading["uptime"], "up 76 h (3 d 4 h) since restart")
        self.assertEqual(reading["top"], ["python: 3.0 GB"])
        self.assertRegex(reading["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d$")
        # Nothing that names the person or this one computer.
        self.assertEqual(
            set(reading),
            {"name", "cpu", "cores", "ram_gb", "at", "cpu_pct", "ram_pct", "ram_free_gb", "commit_pct", "power", "uptime", "level", "reasons", "top"},
        )

    def test_no_reading_where_memory_cannot_be_read(self):
        patches = self.patched(None, [(100, 1000)])
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.assertIsNone(machine.snapshot())

    def test_a_failing_reader_never_raises(self):
        with mock.patch.object(machine, "_memory", side_effect=OSError("denied")):
            self.assertIsNone(machine.snapshot())


class SnapshotSoonTests(unittest.TestCase):
    """The live page must not wait on a reading: `snapshot` is replaced by a slow one."""

    def setUp(self):
        saved = dict(machine._soon)
        self.addCleanup(machine._soon.update, saved)
        machine._soon.update(at=float("-inf"), value=None, thread=None)

    def _settle(self):
        for _ in range(200):
            if machine._soon["thread"] is None:
                return
            time.sleep(0.01)
        self.fail("background reading did not finish")

    def test_a_slow_reading_does_not_block_and_arrives_later(self):
        release = threading.Event()
        calls = []

        def slow():
            calls.append(1)
            release.wait(5)
            return {"level": "heavy"}

        with mock.patch.object(machine, "snapshot", slow), mock.patch.object(machine, "SOON_FIRST_WAIT_SECONDS", 0.05):
            started = time.monotonic()
            self.assertIsNone(machine.snapshot_soon())
            self.assertIsNone(machine.snapshot_soon())
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(len(calls), 1)  # one run at a time, however often the page asks
            release.set()
            self._settle()
            self.assertEqual(machine.snapshot_soon(), {"level": "heavy"})
            self.assertEqual(len(calls), 1)  # fresh: served from the last reading

    def test_a_failed_reading_keeps_the_last_one(self):
        with mock.patch.object(machine, "snapshot", return_value={"level": "light"}):
            self.assertEqual(machine.snapshot_soon(), {"level": "light"})
            self._settle()
        machine._soon["at"] = float("-inf")  # stale: the next call reads again
        with mock.patch.object(machine, "snapshot", return_value=None):
            self.assertEqual(machine.snapshot_soon(), {"level": "light"})
            self._settle()


VM_STAT = """Mach Virtual Memory Statistics: (page size of 4096 bytes)
Pages free:                              100000.
Pages active:                           1500000.
Pages inactive:                          800000.
Pages speculative:                       148576.
Pages throttled:                              0.
Pages wired down:                        700000.
Pages purgeable:                          20000.
"Translation faults":                 123456789.
Pages occupied by compressor:            945728.
"""

PS = """    1  12345   1:02.50 /sbin/launchd
  412 524288  83:20.00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  413 262144   0:10.00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  900   2048 1-00:00:10 opencode
garbage line
"""


class MacTests(unittest.TestCase):
    """macOS readings are parsed from what its own tools print. No Mac runs these tests: the samples are the
    documented output formats, so a real Mac is still the last word."""

    def test_the_model_reads_with_spaces(self):
        self.assertEqual(machine.mac_model_name("MacBookPro15,1\n"), "MacBook Pro 15,1")
        self.assertEqual(machine.mac_model_name("Macmini8,1"), "Mac mini 8,1")
        self.assertEqual(machine.mac_model_name("Mac14,2"), "Mac 14,2")
        self.assertEqual(machine.mac_model_name("VMware7,1"), "VMware7,1")
        self.assertEqual(machine.mac_model_name(""), "")

    def test_memory_counts_cached_pages_as_free(self):
        total = 16 * GB  # 4,194,304 pages of 4096 bytes
        memory = machine.parse_vm_stat(VM_STAT, total)
        self.assertEqual(memory["free"], (100000 + 800000 + 148576) * 4096)
        self.assertEqual((memory["total"], memory["ram_pct"], memory["commit_pct"]), (total, 75, None))
        self.assertIsNone(machine.parse_vm_stat("", total))
        self.assertIsNone(machine.parse_vm_stat(VM_STAT, 0))

    def test_power_and_processes(self):
        battery = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t62%; discharging; 3:10 remaining present: true\n"
        self.assertEqual(machine.parse_pmset(battery), "on battery 62%")
        self.assertEqual(machine.parse_pmset(battery.replace("Battery Power", "AC Power")), "plugged in 62%")
        self.assertEqual(machine.parse_pmset("Now drawing from 'AC Power'\n"), "", "a Mac mini has no battery")
        self.assertEqual(machine.clock_seconds("1:02.50"), 62.5)
        self.assertEqual(machine.clock_seconds("12:34:56"), 45296)
        self.assertEqual(machine.clock_seconds("nonsense"), 0.0)
        rows = machine.parse_ps(PS)
        self.assertEqual(rows[0], (1, "launchd", 12345 * 1024, 62.5))
        self.assertEqual(rows[1][:3], (412, "Google Chrome", 524288 * 1024), "a path with spaces keeps its last part")
        self.assertEqual(rows[3], (900, "opencode", 2048 * 1024, 86410.0))
        self.assertEqual(len(rows), 4)
        groups = machine.group_processes(rows, {}, 0, 8)
        self.assertEqual(machine.top_text(groups[0]), "Google Chrome x2: 0.8 GB")

    def test_a_mac_reading_from_its_tools(self):
        answers = {
            "hw.memsize": str(16 * GB), "kern.boottime": "{ sec = 1000, usec = 0 } Thu Jan  1 00:16:40 1970",
            "hw.model": "MacBookPro15,1", "machdep.cpu.brand_string": "Intel(R) Core(TM) i9-9880H CPU @ 2.30GHz",
        }

        def run(command):
            if command[0] == "sysctl":
                return answers[command[-1]] + "\n"
            return {"vm_stat": VM_STAT, "ps": PS, "pmset": "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged;\n"}[command[0]]

        saved = (machine._specs, machine._mac_total)
        machine._specs, machine._mac_total = None, 0
        self.addCleanup(lambda: (setattr(machine, "_specs", saved[0]), setattr(machine, "_mac_total", saved[1])))
        with mock.patch.object(machine, "IS_MAC", True), mock.patch.object(machine, "_run", side_effect=run), \
                mock.patch.object(machine.time, "time", return_value=1000 + 7200.0), \
                mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": ""}):
            self.assertEqual(machine._mac_memory()["ram_pct"], 75)
            self.assertEqual(machine._mac_uptime_seconds(), 7200.0)
            with mock.patch.object(machine, "_memory", side_effect=machine._mac_memory):
                specs = machine.specs()
                self.assertEqual((specs["name"], specs["cpu"], specs["ram_gb"]), ("MacBook Pro 15,1", "Intel Core i9-9880H", 16.0))
                self.assertEqual(machine.device_name(), "MacBook Pro 15,1")

    def test_a_tool_that_is_missing_gives_no_reading(self):
        with mock.patch.object(machine.subprocess, "run", side_effect=FileNotFoundError("no such tool")):
            self.assertEqual(machine._run(["vm_stat"]), "")
        with mock.patch.object(machine, "_run", return_value=""):
            self.assertIsNone(machine.parse_vm_stat(machine._run(["vm_stat"]), 16 * GB))
            self.assertIsNone(machine._mac_uptime_seconds())
            self.assertEqual(machine.parse_ps(machine._run(["ps"])), [])


class DeviceNameTests(unittest.TestCase):
    def test_the_model_names_the_computer_unless_you_name_it(self):
        with mock.patch.object(machine, "specs", return_value={"name": "ACME Roadrunner 14"}):
            with mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": ""}):
                self.assertEqual(machine.device_name(), "ACME Roadrunner 14")
            with mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": "  Work   laptop "}):
                self.assertEqual(machine.device_name(), "Work laptop")
        with mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": ""}):
            with mock.patch.object(machine, "specs", return_value={"name": ""}):
                self.assertEqual(machine.device_name(), "This computer")
            with mock.patch.object(machine, "specs", side_effect=OSError("denied")):
                self.assertEqual(machine.device_name(), "This computer")


if __name__ == "__main__":
    unittest.main()

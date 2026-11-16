"""The app puts itself back to normal priority when it was started low."""

import ctypes
import subprocess
import sys
import unittest

from cairn.priority import BELOW_NORMAL, NORMAL, keep_normal_priority


@unittest.skipUnless(sys.platform == "win32", "process priority classes are Windows-only")
class PriorityTests(unittest.TestCase):
    def setUp(self):
        from ctypes import wintypes

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
        self.kernel.GetPriorityClass.restype = wintypes.DWORD
        self.kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.process = self.kernel.GetCurrentProcess()
        original = self.kernel.GetPriorityClass(self.process)
        self.addCleanup(self.kernel.SetPriorityClass, self.process, original)

    def current(self):
        return self.kernel.GetPriorityClass(self.process)

    def test_a_low_priority_start_is_put_back_to_normal(self):
        self.assertTrue(self.kernel.SetPriorityClass(self.process, BELOW_NORMAL))
        self.assertEqual(self.current(), BELOW_NORMAL)
        keep_normal_priority()
        self.assertEqual(self.current(), NORMAL)

    def test_normal_priority_is_left_alone(self):
        self.assertTrue(self.kernel.SetPriorityClass(self.process, NORMAL))
        keep_normal_priority()
        self.assertEqual(self.current(), NORMAL)

    def test_another_process_is_put_back_to_normal(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW,
        )
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        handle = int(child._handle)
        self.assertEqual(self.kernel.GetPriorityClass(handle), BELOW_NORMAL)
        keep_normal_priority(child.pid)
        self.assertEqual(self.kernel.GetPriorityClass(handle), NORMAL)

    def test_a_process_that_is_gone_is_ignored(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=subprocess.CREATE_NO_WINDOW)
        child.wait()
        keep_normal_priority(child.pid)  # must not raise


if __name__ == "__main__":
    unittest.main()

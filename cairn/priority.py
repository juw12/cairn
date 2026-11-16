"""Keep the app at normal process priority.

Windows hands a low priority down from whatever started the app (a scheduled task, a background
shell). On a busy computer the app then gets almost no processor time: it can take minutes just to
import, and every page hangs. This module imports nothing heavy so the launcher can call it first.
"""

import os

IDLE = 0x40
BELOW_NORMAL = 0x4000
NORMAL = 0x20
SET_INFORMATION = 0x0200
QUERY_LIMITED_INFORMATION = 0x1000


def keep_normal_priority(pid: int | None = None) -> None:
    """Put a low-priority process (this one, or `pid`) back to normal; never above. Does nothing off Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
        kernel.GetPriorityClass.restype = wintypes.DWORD
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        if pid is None:
            process = kernel.GetCurrentProcess()
        else:
            process = kernel.OpenProcess(SET_INFORMATION | QUERY_LIMITED_INFORMATION, False, pid)
            if not process:
                return
        try:
            if kernel.GetPriorityClass(process) in (IDLE, BELOW_NORMAL):
                kernel.SetPriorityClass(process, NORMAL)
        finally:
            if pid is not None:
                kernel.CloseHandle(process)
    except (OSError, AttributeError):
        pass

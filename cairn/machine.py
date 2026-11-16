"""What this computer is and how loaded it is right now, with nothing but the standard library.

The Vibe page used to call a session "light" from the size of the work alone, while the laptop under it
could be flat out. This reads the real thing: the model and CPU, how busy the processors have been over
the last half minute, how full the memory is, and which apps take the most.

Everything here is machine-wide. It says how the laptop feels, not which session caused it.
Readings come from Windows (kernel32 through ctypes, and the registry). On other systems, or when a
call fails, `snapshot()` returns None and the page simply shows no laptop figures.
"""

from __future__ import annotations

import collections
import datetime as dt
import os
import re
import threading
import time

LEVELS = ("light", "medium", "heavy")
CPU_WINDOW_SECONDS = 30  # the CPU figure is an average over this long, so one spike does not flip the level
CPU_BASELINE_MAX_SECONDS = 120  # the watcher looks once a minute: its average runs from its previous look
FIRST_SAMPLE_SECONDS = 0.25
PROCESS_REFRESH_SECONDS = 15  # listing every process is the costly part: reuse it between page polls
TOP_GROUPS = 4
CPU_MEDIUM, CPU_HEAVY = 50, 85
RAM_MEDIUM, RAM_HEAVY = 75, 90
COMMIT_HEAVY = 90  # memory promised to apps against RAM plus page file: past this, Windows is paging hard
PLACEHOLDER_NAMES = ("system product name", "to be filled by o.e.m.", "default string", "system manufacturer", "none")

_lock = threading.Lock()
_cpu_samples: collections.deque = collections.deque(maxlen=64)  # (monotonic seconds, busy, total)
_processes: dict = {"at": 0.0, "times": {}, "top": []}
_specs: dict | None = None


# ------------------------------------------------------------ the level (pure)


def load_level(cpu_pct, ram_pct, commit_pct=None) -> tuple[str, list[str]]:
    """Light, medium or heavy, and the readings that put it there."""
    heavy, medium = [], []
    if cpu_pct is not None:
        text = f"CPU {cpu_pct}% busy"
        (heavy if cpu_pct >= CPU_HEAVY else medium if cpu_pct >= CPU_MEDIUM else []).append(text)
    if ram_pct is not None:
        text = f"memory {ram_pct}% full"
        (heavy if ram_pct >= RAM_HEAVY else medium if ram_pct >= RAM_MEDIUM else []).append(text)
    if commit_pct is not None and commit_pct >= COMMIT_HEAVY:
        heavy.append(f"{commit_pct}% of RAM plus page file promised to apps")
    if heavy:
        return "heavy", heavy + medium
    if medium:
        return "medium", medium
    return "light", []


def clean_cpu_name(name: str) -> str:
    """"Intel(R) Core(TM) Ultra 7 155H" and "... CPU @ 2.60GHz" read better without the legal marks."""
    name = re.sub(r"\((?:R|TM|C)\)", "", str(name or ""), flags=re.I)
    name = re.sub(r"\s+(?:CPU\s+)?@\s*[\d.]+\s*[GM]Hz", "", name, flags=re.I)
    name = re.sub(r"\b\d+-Core Processor\b|\bProcessor\b|\bCPU\b", "", name)
    return " ".join(name.split())


def machine_name(manufacturer: str, product: str) -> str:
    """One readable name from the BIOS strings, leaving out placeholders and a repeated brand."""
    def real(value) -> str:
        value = " ".join(str(value or "").split())
        return "" if value.casefold() in PLACEHOLDER_NAMES else value

    manufacturer, product = real(manufacturer), real(product)
    if "_" in product:
        head, tail = product.rsplit("_", 1)
        if tail and head.endswith(tail):
            product = head  # "VivoBook X1_X1"
    if not product:
        return manufacturer
    brand = manufacturer.split(" ")[0].casefold() if manufacturer else ""
    first = product.split(" ")[0].casefold()
    if not brand or brand.startswith(first) or first.startswith(brand):  # "ASUSTeK" in front of "ASUS VivoBook"
        return product
    short = re.sub(r",?\s+(?:Inc\.?|Corp\.?|Corporation|Co\.,? Ltd\.?|COMPUTER INC\.?|Technology)$", "", manufacturer, flags=re.I)
    return f"{short} {product}"


def group_processes(rows, previous_times: dict, seconds: float, cores: int, limit: int = TOP_GROUPS) -> list[dict]:
    """Processes with one name (15 copies of one tool) count as one app. `rows` is (pid, name, ram bytes,
    cpu seconds); CPU share is the time used since `previous_times`, as a part of every core."""
    groups: dict[str, dict] = {}
    for pid, name, ram, cpu_seconds in rows:
        key = re.sub(r"\.exe$", "", str(name), flags=re.I)
        group = groups.setdefault(key, {"name": key, "count": 0, "ram": 0, "cpu": 0.0})
        group["count"] += 1
        group["ram"] += ram
        if pid in previous_times and seconds > 0:
            group["cpu"] += max(0.0, cpu_seconds - previous_times[pid])
    capacity = seconds * max(1, cores)
    out = []
    for group in groups.values():
        cpu_pct = round(group["cpu"] * 100 / capacity) if capacity > 0 else 0
        out.append({"name": group["name"], "count": group["count"], "ram_gb": round(group["ram"] / 2**30, 1), "cpu_pct": min(100, cpu_pct)})
    by_ram = sorted(out, key=lambda g: -g["ram_gb"])[:limit]
    by_cpu = [g for g in sorted(out, key=lambda g: -g["cpu_pct"])[:limit] if g["cpu_pct"] >= 5 and g not in by_ram]
    return sorted(by_ram + by_cpu, key=lambda g: (-g["ram_gb"], -g["cpu_pct"]))


def top_text(group: dict) -> str:
    """"opencode x15: 5.2 GB, 20% CPU"."""
    count = f" x{group['count']}" if group.get("count", 1) > 1 else ""
    cpu = f", {group['cpu_pct']}% CPU" if group.get("cpu_pct") else ""
    return f"{group['name']}{count}: {group['ram_gb']} GB{cpu}"


# ------------------------------------------------------------ Windows readings


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _filetime(value) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def _cpu_times() -> tuple[int, int] | None:
    """(busy, total) processor time since boot, all cores together."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not _kernel32().GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    total = _filetime(kernel) + _filetime(user)  # kernel time includes idle time
    return total - _filetime(idle), total


def _memory() -> dict | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not _kernel32().GlobalMemoryStatusEx(ctypes.byref(status)) or not status.ullTotalPhys:
        return None
    commit_limit = status.ullTotalPageFile  # "page file" here is the commit limit: RAM plus page file
    return {
        "total": status.ullTotalPhys,
        "free": status.ullAvailPhys,
        "ram_pct": round((status.ullTotalPhys - status.ullAvailPhys) * 100 / status.ullTotalPhys),
        "commit_pct": round((commit_limit - status.ullAvailPageFile) * 100 / commit_limit) if commit_limit else None,
    }


def _power() -> str:
    """"plugged in", "on battery 62%", or "" for a desktop or an unknown state."""
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes

    class PowerStatus(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    status = PowerStatus()
    if not _kernel32().GetSystemPowerStatus(ctypes.byref(status)):
        return ""
    if status.BatteryFlag & 128 or status.BatteryFlag == 255:  # no battery: a desktop
        return ""
    percent = f" {status.BatteryLifePercent}%" if status.BatteryLifePercent <= 100 else ""
    if status.ACLineStatus == 1:
        return f"plugged in{percent}"
    return f"on battery{percent}" if status.ACLineStatus == 0 else ""


def _process_rows() -> list[tuple[int, str, int, float]]:
    """(pid, image name, working set bytes, CPU seconds) for every process this user may look at."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = _kernel32()
    kernel32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD)
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    invalid = wintypes.HANDLE(-1).value
    listing = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not listing or listing == invalid:
        return []
    rows = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(listing, ctypes.byref(entry))
        while more:
            pid = entry.th32ProcessID
            handle = kernel32.OpenProcess(0x1000, False, pid) if pid else None  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                try:
                    counters = MemoryCounters()
                    counters.cb = ctypes.sizeof(counters)
                    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
                    if kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                        cpu_seconds = 0.0
                        if kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                            cpu_seconds = (_filetime(kernel) + _filetime(user)) / 1e7
                        rows.append((pid, entry.szExeFile, counters.WorkingSetSize, cpu_seconds))
                finally:
                    kernel32.CloseHandle(handle)
            more = kernel32.Process32NextW(listing, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(listing)
    return rows


def _registry(key: str, name: str) -> str:
    if os.name != "nt":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            return str(winreg.QueryValueEx(handle, name)[0])
    except OSError:
        return ""


# ------------------------------------------------------------ what the page gets


def specs() -> dict:
    """The model, processor, cores and memory. Read once: they do not change while the app runs.
    No computer name, user name or serial number: only what the box says on the outside."""
    global _specs
    if _specs is None:
        bios = r"HARDWARE\DESCRIPTION\System\BIOS"
        memory = _memory()
        _specs = {
            "name": machine_name(_registry(bios, "SystemManufacturer"), _registry(bios, "SystemProductName")),
            "cpu": clean_cpu_name(_registry(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")),
            "cores": os.cpu_count() or 0,
            "ram_gb": round(memory["total"] / 2**30, 1) if memory else 0,
        }
    return dict(_specs)


def _cpu_percent(now: float) -> int | None:
    """How busy the processors were, averaged from a sample about one window old until now."""
    current = _cpu_times()
    if current is None:
        return None
    while _cpu_samples and now - _cpu_samples[0][0] > CPU_BASELINE_MAX_SECONDS:
        _cpu_samples.popleft()
    while len(_cpu_samples) > 1 and now - _cpu_samples[1][0] >= CPU_WINDOW_SECONDS:
        _cpu_samples.popleft()  # keep one sample that is a full window old to measure from
    if not _cpu_samples:
        _cpu_samples.append((now, *current))
        time.sleep(FIRST_SAMPLE_SECONDS)  # nothing to compare with yet: take a short first look
        current = _cpu_times() or current
        now += FIRST_SAMPLE_SECONDS
    _, busy, total = _cpu_samples[0]
    if now - _cpu_samples[-1][0] >= 1:
        _cpu_samples.append((now, *current))
    if current[1] <= total:
        return None
    return max(0, min(100, round((current[0] - busy) * 100 / (current[1] - total))))


def _top_processes(now: float, cores: int) -> list[dict]:
    if now - _processes["at"] < PROCESS_REFRESH_SECONDS and _processes["times"]:
        return _processes["top"]
    rows = _process_rows()
    if not rows:
        return []
    top = group_processes(rows, _processes["times"], now - _processes["at"] if _processes["times"] else 0, cores)
    _processes.update(at=now, times={pid: cpu for pid, _, _, cpu in rows}, top=top)
    return top


def snapshot() -> dict | None:
    """The laptop and its load right now, or None where it cannot be read. Never raises."""
    try:
        with _lock:
            memory = _memory()
            if memory is None:
                return None
            now = time.monotonic()
            reading = specs()
            cpu_pct = _cpu_percent(now)
            level, reasons = load_level(cpu_pct, memory["ram_pct"], memory["commit_pct"])
            reading.update({
                "at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
                "cpu_pct": cpu_pct,
                "ram_pct": memory["ram_pct"],
                "ram_free_gb": round(memory["free"] / 2**30, 1),
                "commit_pct": memory["commit_pct"],
                "power": _power(),
                "level": level,
                "reasons": reasons,
                "top": [top_text(group) for group in _top_processes(now, reading["cores"])],
            })
            return reading
    except Exception:  # a reading is a nicety: it must never take the watcher or a request down
        return None

"""What this computer is and how loaded it is right now, with nothing but the standard library.

The Vibe page used to call a session "light" from the size of the work alone, while the laptop under it
could be flat out. This reads the real thing: the model and CPU, how busy the processors have been over
the last half minute, how full the memory is, and which apps take the most.

Everything here is machine-wide. It says how the laptop feels, not which session caused it.
Readings come from Windows (kernel32 through ctypes, and the registry) or from macOS (sysctl, vm_stat,
pmset, ps and one Mach call). On other systems, or when a call fails, `snapshot()` returns None and the
page simply shows no laptop figures.

`device_name()` is what this computer is called on session notes, so the Vibe page can tell two computers
that share one brain apart.

`network()` is the other half of how fast an assistant feels: which network the computer is on (the Wi-Fi's
name where the system gives it, its signal and link rate), and whether and how quickly the internet answers.
An assistant that runs online waits on that; a model that runs on the computer itself does not.
"""

from __future__ import annotations

import collections
import datetime as dt
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

LEVELS = ("light", "medium", "heavy")
CPU_WINDOW_SECONDS = 30  # the CPU figure is an average over this long, so one spike does not flip the level
CPU_BASELINE_MAX_SECONDS = 120  # the watcher looks once a minute: its average runs from its previous look
FIRST_SAMPLE_SECONDS = 0.25
PROCESS_REFRESH_SECONDS = 15  # listing every process is the costly part: reuse it between page polls
SOON_TTL_SECONDS = 5  # the live page asks every few seconds: one reading serves that long
SOON_FIRST_WAIT_SECONDS = 2
TOP_GROUPS = 4
CPU_MEDIUM, CPU_HEAVY = 50, 85
RAM_MEDIUM, RAM_HEAVY = 75, 90
COMMIT_HEAVY = 90  # memory promised to apps against RAM plus page file: past this, Windows is paging hard
PLACEHOLDER_NAMES = ("system product name", "to be filled by o.e.m.", "default string", "system manufacturer", "none")

NET_LEVELS = ("good", "slow", "offline")
NET_SLOW_MS = 500  # a small request that takes this long to come back: an online assistant feels it in every step
NET_REFRESH_SECONDS = 30  # one look at the internet (a request of under 1 kB) this often while a page or watcher asks
NET_WIFI_REFRESH_SECONDS = 60  # asking the system about the Wi-Fi is the costly part, and it changes rarely
NET_PROBE_TIMEOUT = 4
NET_FIRST_WAIT_SECONDS = 1.5
# Pages that answer "204, nothing" and exist to be asked this. Plain http on purpose: a network that wants a
# sign-in page first can then answer with that page, which is how it is told from a working one.
NET_PROBES = ("http://www.gstatic.com/generate_204", "http://cp.cloudflare.com/generate_204")
NET_SPEED_URL = "http://speed.cloudflare.com/__down?bytes=8000000"
NET_SPEED_SECONDS = 4  # a speed measurement stops after this long or 8 MB, whichever comes first
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # a console tool must not flash a window under pythonw

_lock = threading.Lock()
_cpu_samples: collections.deque = collections.deque(maxlen=64)  # (monotonic seconds, busy, total)
_processes: dict = {"at": 0.0, "times": {}, "top": []}
_specs: dict | None = None
_soon_lock = threading.Lock()
_soon: dict = {"at": float("-inf"), "value": None, "thread": None}
IS_MAC = sys.platform == "darwin"
_mac_total = 0  # bytes of memory, read once
_net_lock = threading.Lock()
_net: dict = {
    "at": float("-inf"), "value": None, "thread": None,
    "wifi_at": float("-inf"), "wifi": {},  # the Wi-Fi as the system last described it
    "bytes": None,  # (monotonic seconds, bytes in, bytes out) at the previous look
    "delays": collections.deque(maxlen=3),  # the last answers of the internet in ms; None for no answer
    "speed": None,  # the last measurement someone asked for
}


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


def uptime_text(seconds) -> str:
    """"up 12 min since restart", "up 5 h since restart", "up 76 h (3 d 4 h) since restart", or ""."""
    if seconds is None or seconds < 0:
        return ""
    minutes = int(seconds // 60)
    hours = minutes // 60
    if hours < 1:
        return f"up {minutes} min since restart"
    if hours < 48:
        return f"up {hours} h since restart"
    return f"up {hours} h ({hours // 24} d {hours % 24} h) since restart"


def uptime_hours(seconds) -> int | None:
    """Whole hours since the computer last started, None where that cannot be read."""
    return None if seconds is None or seconds < 0 else int(seconds // 3600)


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
        return _mac_cpu_times() if IS_MAC else None
    import ctypes
    from ctypes import wintypes

    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not _kernel32().GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    total = _filetime(kernel) + _filetime(user)  # kernel time includes idle time
    return total - _filetime(idle), total


def _memory() -> dict | None:
    if os.name != "nt":
        return _mac_memory() if IS_MAC else None
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
        return parse_pmset(_run(["pmset", "-g", "batt"])) if IS_MAC else ""
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


def _uptime_seconds() -> float | None:
    """Seconds since the computer last started, sleep included: the figure Task Manager calls "Up time"."""
    if os.name != "nt":
        return _mac_uptime_seconds() if IS_MAC else None
    import ctypes

    kernel32 = _kernel32()
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong  # the default int would cut it off after 24.9 days
    return kernel32.GetTickCount64() / 1000


def _process_rows() -> list[tuple[int, str, int, float]]:
    """(pid, image name, working set bytes, CPU seconds) for every process this user may look at."""
    if os.name != "nt":
        return parse_ps(_run(["ps", "-A", "-o", "pid=,rss=,time=,comm="])) if IS_MAC else []
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


# ------------------------------------------------------------ macOS readings
# The same figures on a Mac, from the tools macOS ships with (sysctl, vm_stat, pmset, ps) and one Mach call.
# Each reading stands alone: one that fails leaves its figure out and never raises.


def _run(command: list[str]) -> str:
    """What a small system tool prints, or "" when it is missing, slow or fails."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout or ""


def mac_model_name(model: str) -> str:
    """"MacBookPro15,1" as "MacBook Pro 15,1": the model identifier, spaced so it reads."""
    model = " ".join(str(model or "").split())
    match = re.fullmatch(r"(MacBook|iMac|Mac)(Pro|Air|mini|Studio)?(\d+,\d+)", model)
    return " ".join(part for part in match.groups() if part) if match else model


def parse_vm_stat(text: str, total: int) -> dict | None:
    """Memory from `vm_stat`, in the shape `_memory` gives. Free means ready for an app to take: free,
    cached (inactive) and speculative pages. The rest is in use: app memory, wired and compressed."""
    size = re.search(r"page size of (\d+) bytes", text or "")
    pages = {m.group(1).strip().lower(): int(m.group(2)) for m in re.finditer(r"^(.+?):\s+(\d+)\.?\s*$", text or "", flags=re.M)}
    if not size or not total or "pages free" not in pages:
        return None
    ready = sum(pages.get(key, 0) for key in ("pages free", "pages inactive", "pages speculative")) * int(size.group(1))
    ready = min(ready, total)
    return {"total": total, "free": ready, "ram_pct": round((total - ready) * 100 / total), "commit_pct": None}


def parse_pmset(text: str) -> str:
    """`pmset -g batt` as "plugged in 85%", "on battery 62%", or "" for a Mac without a battery."""
    if "InternalBattery" not in (text or ""):
        return ""
    found = re.search(r"(\d{1,3})%", text)
    percent = f" {found.group(1)}%" if found else ""
    if "'AC Power'" in text:
        return f"plugged in{percent}"
    return f"on battery{percent}" if "'Battery Power'" in text else ""


def clock_seconds(text: str) -> float:
    """CPU time as `ps` prints it ("1:02.50", "12:34:56", "2-03:04:05") in seconds."""
    days, _, rest = str(text).strip().rpartition("-")
    seconds = 0.0
    try:
        for part in rest.split(":"):
            seconds = seconds * 60 + float(part)
        return seconds + (int(days) * 86400 if days else 0)
    except ValueError:
        return 0.0


def parse_ps(text: str) -> list[tuple[int, str, int, float]]:
    """Rows of `ps -A -o pid=,rss=,time=,comm=` as (pid, name, memory bytes, CPU seconds)."""
    rows = []
    for line in (text or "").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        name = parts[3].strip().rstrip("/").rsplit("/", 1)[-1]  # the command comes as a full path, spaces and all
        rows.append((int(parts[0]), name, int(parts[1]) * 1024, clock_seconds(parts[2])))
    return rows


def _mac_cpu_times() -> tuple[int, int] | None:
    """(busy, total) processor ticks since boot from Mach's host_statistics (HOST_CPU_LOAD_INFO)."""
    try:
        import ctypes
        import ctypes.util

        libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib")
        libc.mach_host_self.restype = ctypes.c_uint
        libc.host_statistics.restype = ctypes.c_int
        libc.host_statistics.argtypes = (ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint))
        ticks = (ctypes.c_uint * 4)()  # user, system, idle, nice
        count = ctypes.c_uint(4)
        if libc.host_statistics(libc.mach_host_self(), 3, ticks, ctypes.byref(count)) != 0:
            return None
        user, system, idle, nice = (int(value) for value in ticks)
    except Exception:  # no such library or call on this system: the page shows memory without a CPU figure
        return None
    busy = user + system + nice
    return busy, busy + idle


def _mac_memory() -> dict | None:
    global _mac_total
    if not _mac_total:
        size = _run(["sysctl", "-n", "hw.memsize"]).strip()
        _mac_total = int(size) if size.isdigit() else 0
    return parse_vm_stat(_run(["vm_stat"]), _mac_total)


def _mac_uptime_seconds() -> float | None:
    started = re.search(r"sec\s*=\s*(\d+)", _run(["sysctl", "-n", "kern.boottime"]))
    return max(0.0, time.time() - int(started.group(1))) if started else None


# ------------------------------------------------------------ the network (pure)
# What the system's own tools print, read into one small dictionary. Each parser takes text and never raises,
# so it can be tested on any computer with what a Mac or a Windows PC printed.

VIRTUAL_ADAPTER = re.compile(r"vethernet|loopback|vmware|virtualbox|bluetooth|tailscale|zerotier|wireguard|vpn|wsl|hyper-v|npcap", re.I)
WIFI_WORD = re.compile(r"wi-?fi|wlan|wireless|airport", re.I)


def signal_word(dbm=None, pct=None) -> str:
    """How well the Wi-Fi is heard: "strong", "good", "fair" or "weak". macOS gives dBm; Windows gives a
    percentage, which is (dBm + 100) x 2."""
    if dbm is None and pct is not None:
        dbm = pct / 2 - 100
    if dbm is None:
        return ""
    return "strong" if dbm >= -55 else "good" if dbm >= -67 else "fair" if dbm >= -75 else "weak"


def mbit(value) -> str:
    """A rate as it is said: "0.2", "7.5", "12", "192"."""
    value = float(value)
    return f"{value:.1f}" if value < 10 else str(round(value))


def band_text(ghz) -> str:
    """"2.4 GHz", "5 GHz", "6 GHz" from the 2, 2.4, 5 or 6 the tools print."""
    ghz = float(ghz)
    return "2.4 GHz" if ghz < 3 else f"{ghz:g} GHz"


def parse_airport(text: str) -> dict:
    """The Wi-Fi a Mac is on, from `system_profiler SPAirPortDataType`; {} when it is on none. macOS 14.4 and
    later print "<redacted>" where the name goes unless the asking app may use Location Services."""
    lines = (text or "").splitlines()
    depth = lambda line: len(line) - len(line.lstrip())
    for index, line in enumerate(lines):
        if line.strip() != "Current Network Information:":
            continue
        block = []
        for follower in lines[index + 1:]:
            if follower.strip() and depth(follower) <= depth(line):
                break
            if follower.strip():
                block.append(follower)
        if not block or not block[0].rstrip().endswith(":"):
            continue  # an interface that is on no network of its own (awdl0)
        name = block[0].strip()[:-1]
        fields = {}
        for detail in block[1:]:
            if depth(detail) <= depth(block[0]):
                break
            key, _, value = detail.strip().partition(":")
            fields[key.strip().lower()] = value.strip()
        hidden = name == "<redacted>"
        found = {"connected": True, "name": "" if hidden else name, "name_hidden": hidden}
        if fields.get("phy mode"):
            found["radio"] = fields["phy mode"]
        channel = re.match(r"(\d+)(?:\s*\(([\d.]+)\s*GHz)?", fields.get("channel", ""))
        if channel:
            found["channel"] = int(channel.group(1))
            if channel.group(2):
                found["band"] = band_text(channel.group(2))
        if fields.get("security"):
            found["security"] = fields["security"]
        signal = re.match(r"(-?\d+)\s*dBm", fields.get("signal / noise", ""))
        if signal:
            found["signal_dbm"] = int(signal.group(1))
        rate = re.match(r"([\d.]+)", fields.get("transmit rate", ""))
        if rate and float(rate.group(1)) > 0:
            found["link_mbps"] = float(rate.group(1))
        return found
    return {}


def parse_netsh_wlan(text: str) -> dict:
    """The Wi-Fi a Windows PC is on, from `netsh wlan show interfaces`; {} when it is on none. Windows 11 24H2
    and later answer with a sentence about location permission instead: then only {"name_hidden": True}."""
    text = text or ""
    name = re.search(r"^\s*SSID\s*:\s?(.*)$", text, flags=re.M)
    if not name:
        return {"name_hidden": True} if re.search(r"location", text, flags=re.I) else {}
    found = {"connected": True, "name": name.group(1).strip(), "name_hidden": False}
    radio = re.search(r":\s*(802\.11[a-z]+)\s*$", text, flags=re.M | re.I)
    if radio:
        found["radio"] = radio.group(1)
    band = re.search(r":\s*([\d.]+)\s*GHz\s*$", text, flags=re.M | re.I)
    if band:
        found["band"] = band_text(band.group(1))
    channel = re.search(r"^\s*Channel\s*:\s*(\d+)\s*$", text, flags=re.M | re.I)
    if channel:
        found["channel"] = int(channel.group(1))
    security = re.search(r"^\s*Authentication\s*:\s*(.+?)\s*$", text, flags=re.M | re.I)
    if security:
        found["security"] = security.group(1)
    signal = re.search(r":\s*(\d{1,3})\s*%\s*$", text, flags=re.M)
    if signal:
        found["signal_pct"] = min(100, int(signal.group(1)))
    rates = [float(rate) for rate in re.findall(r"\(Mbps\)\s*:\s*([\d.]+)", text)]  # receive, then transmit
    if rates and rates[-1] > 0:
        found["link_mbps"] = rates[-1]
    return found


def parse_netsh_interfaces(text: str) -> list[str]:
    """The names of the connected adapters, from `netsh interface show interface` ("Wi-Fi", "Ethernet")."""
    names = []
    for line in (text or "").splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[1].lower() == "connected":
            names.append(parts[3].strip())
    return names


def windows_link(wlan: dict, connected: list[str]) -> dict:
    """What a Windows PC is on: the Wi-Fi netsh described, else the first real adapter that is connected."""
    if wlan.get("connected"):
        return {"kind": "wifi", **wlan}
    real = [name for name in connected if not VIRTUAL_ADAPTER.search(name)]
    if any(WIFI_WORD.search(name) for name in real):
        hidden = bool(wlan.get("name_hidden"))
        return {"kind": "wifi", "name": "", "name_hidden": hidden, **({"hidden_by": "Windows"} if hidden else {})}
    if real:
        return {"kind": "wired", "via": real[0]}
    return {"kind": "none"}


def parse_default_route(text: str) -> str:
    """The interface a Mac sends to the internet through, from `route -n get default`; "" when there is none."""
    found = re.search(r"^\s*interface:\s*(\S+)", text or "", flags=re.M)
    return found.group(1) if found else ""


def parse_hardware_ports(text: str) -> dict[str, str]:
    """{"en0": "Wi-Fi", "en5": "USB 10/100/1000 LAN"} from `networksetup -listallhardwareports`."""
    return {device: port.strip() for port, device in re.findall(r"^Hardware Port:\s*(.+)\nDevice:\s*(\S+)", text or "", flags=re.M)}


def hotspot_text(text: str) -> str:
    """"iPhone hotspot" or "phone hotspot" when the address a Mac was handed (`ipconfig getpacket`) is one a
    phone gives out, else "". A hotspot is mobile data: slower and less even than a line, and usually counted."""
    router = re.search(r"^router[^{\n]*\{\s*([\d.]+)", text or "", flags=re.M)
    sender = re.search(r"^sname\s*=\s*(\S+)", text or "", flags=re.M)
    address = router.group(1) if router else ""
    if address == "172.20.10.1" or (sender and sender.group(1).lower() == "iphone"):
        return "iPhone hotspot"
    return "phone hotspot" if address == "192.168.43.1" else ""


def parse_netstat_bytes(text: str) -> tuple[int, int] | None:
    """(bytes in, bytes out) since the system started: of one interface from a Mac's `netstat -ibn -I en0`,
    of every adapter together from Windows' `netstat -e`."""
    text = text or ""
    if "Ibytes" in text:
        for line in text.splitlines():
            parts = line.split()
            if "<Link#" in line and len(parts) >= 7 and parts[-5].isdigit() and parts[-2].isdigit():
                return int(parts[-5]), int(parts[-2])
        return None
    row = re.search(r"^\s*\D[^\d\n]*?\s(\d+)\s+(\d+)\s*$", text, flags=re.M)  # the first row of figures is the bytes
    return (int(row.group(1)), int(row.group(2))) if row else None


def traffic_rate(previous, current) -> tuple[float | None, float | None]:
    """(down, up) in Mbit/s between two looks of (seconds, bytes in, bytes out); (None, None) when there is
    no earlier look, no time between them, or the counters started again."""
    if not previous or not current:
        return None, None
    seconds = current[0] - previous[0]
    down, up = current[1] - previous[1], current[2] - previous[2]
    if seconds < 1 or down < 0 or up < 0:
        return None, None
    return round(down * 8 / 1e6 / seconds, 1), round(up * 8 / 1e6 / seconds, 1)


def net_level(internet: str, delay_ms) -> tuple[str, str]:
    """Good, slow or offline for an assistant that runs online, and the reason when it is not good."""
    if internet == "sign-in":
        return "offline", "this network asks for a sign-in page before it lets the internet through"
    if internet != "ok":
        return "offline", "the internet does not answer"
    if delay_ms is not None and delay_ms >= NET_SLOW_MS:
        return "slow", f"the internet takes {delay_ms} ms to answer"
    return "good", ""


def link_text(net: dict) -> str:
    """"Wi-Fi HomeNet · 802.11ac 5 GHz · signal strong (-37 dBm) · link 192 Mbit/s"."""
    kind = net.get("kind")
    if kind == "none":
        return "Not on a network"
    parts = []
    if kind == "wifi":
        if net.get("name"):
            parts.append(f"Wi-Fi {net['name']}")
        elif net.get("name_hidden"):
            parts.append(f"Wi-Fi ({net.get('hidden_by') or 'the system'} keeps its name from apps)")
        else:
            parts.append("Wi-Fi")
        parts.append(net.get("via") or "")
    elif kind == "wired":
        parts.append(f"Cable ({net['via']})" if net.get("via") else "Cable")
    else:
        parts.append("Connected")
    parts.append(" ".join(part for part in (net.get("radio"), net.get("band")) if part))
    if net.get("signal"):
        figure = f"{net['signal_dbm']} dBm" if net.get("signal_dbm") is not None else f"{net['signal_pct']}%" if net.get("signal_pct") is not None else ""
        parts.append(f"signal {net['signal']}" + (f" ({figure})" if figure else ""))
    if net.get("link_mbps"):
        parts.append(f"link {mbit(net['link_mbps'])} Mbit/s")
    return " · ".join(part for part in parts if part)


def reach_text(net: dict) -> str:
    """"Internet answers in 85 ms · in use now 1.2 down, 0.1 up Mbit/s"."""
    internet = net.get("internet")
    if internet == "ok":
        text = f"Internet answers in {net['delay_ms']} ms" if net.get("delay_ms") is not None else "Internet answers"
    elif internet == "sign-in":
        text = "This network asks for a sign-in page first"
    else:
        text = "No internet"
    if net.get("down_mbps") is not None and net.get("up_mbps") is not None:
        text += f" · in use now {mbit(net['down_mbps'])} down, {mbit(net['up_mbps'])} up Mbit/s"
    return text


def speed_text(speed: dict | None) -> str:
    """"measured 12 Mbit/s down at 10:42", or "" when nobody asked for a measurement yet."""
    if not speed or speed.get("down_mbps") is None:
        return ""
    return f"measured {mbit(speed['down_mbps'])} Mbit/s down at {str(speed.get('at', ''))[11:16]}"


# ------------------------------------------------------------ the network (readings)


def _mac_link(now: float) -> dict:
    device = parse_default_route(_run(["route", "-n", "get", "default"]))
    if not device:
        return {"kind": "none"}
    ports = parse_hardware_ports(_run(["networksetup", "-listallhardwareports"]))
    wifi_device = next((name for name, port in ports.items() if WIFI_WORD.search(port)), "")
    link = {"kind": "wired", "via": ports[device], "device": device} if device in ports and device != wifi_device else {"kind": "other", "device": device}
    if wifi_device and (device == wifi_device or device not in ports):  # on the Wi-Fi, or through a tunnel over it
        if now - _net["wifi_at"] >= NET_WIFI_REFRESH_SECONDS:
            _net.update(wifi_at=now, wifi=parse_airport(_run(["system_profiler", "SPAirPortDataType"])))
        wifi = _net["wifi"]
        if wifi or device == wifi_device:
            link = {"kind": "wifi", **wifi, "device": wifi_device, "via": hotspot_text(_run(["ipconfig", "getpacket", wifi_device]))}
            if wifi.get("name_hidden"):
                link["hidden_by"] = "macOS"
    return link


def _win_link() -> dict:
    wlan = parse_netsh_wlan(_run(["netsh", "wlan", "show", "interfaces"]))
    connected = [] if wlan.get("connected") else parse_netsh_interfaces(_run(["netsh", "interface", "show", "interface"]))
    return windows_link(wlan, connected)


def _bytes_now(link: dict) -> tuple[int, int] | None:
    if IS_MAC:
        return parse_netstat_bytes(_run(["netstat", "-ibn", "-I", link["device"]])) if link.get("device") else None
    return parse_netstat_bytes(_run(["netstat", "-e"]))


def _probe_internet() -> tuple[str, int | None]:
    """("ok", ms) when the internet answered, ("sign-in", None) when something else answered in its place (a
    hotel's or a hotspot's sign-in page), ("none", None) when nothing did. Goes through the system's proxy."""
    answered = False
    for url in NET_PROBES:
        started = time.perf_counter()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "cairn", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(request, timeout=NET_PROBE_TIMEOUT) as reply:
                status = reply.status
                reply.read(64)
        except urllib.error.HTTPError:
            answered = True
            continue
        except Exception:  # no route, no name, a timeout, a cut line: this one did not answer
            continue
        if status == 204:
            return "ok", round((time.perf_counter() - started) * 1000)
        answered = True
    return ("sign-in" if answered else "none"), None


def network() -> dict | None:
    """The network this computer is on and how the internet answers right now, or None where it cannot be
    read. It asks the system's tools and sends one tiny request, so it can take seconds: the page uses
    `network_soon`. Never raises.

    No address of this computer, no router address and no access point id: the name of the Wi-Fi is the one
    thing here that says where the computer is, and it stays in your own notes."""
    try:
        if not IS_MAC and os.name != "nt":
            return None
        now = time.monotonic()
        link = _mac_link(now) if IS_MAC else _win_link()
        counted = _bytes_now(link)
        look = (now, *counted) if counted else None
        down, up = traffic_rate(_net["bytes"], look)
        _net["bytes"] = look
        internet, delay = _probe_internet()
        _net["delays"].append(delay)
        recent = sorted(value for value in _net["delays"] if value is not None)
        level, why = net_level(internet, recent[len(recent) // 2] if recent else None)  # the middle of the last three
        reading = {key: value for key, value in link.items() if key not in ("connected", "device")}
        reading.update({
            "signal": signal_word(link.get("signal_dbm"), link.get("signal_pct")) if link.get("kind") == "wifi" else "",
            "internet": internet,
            "delay_ms": delay,
            "down_mbps": down,
            "up_mbps": up,
            "level": level,
            "why": why,
            "at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
        })
        reading["link"] = link_text(reading)
        reading["reach"] = reach_text(reading)
        reading["speed"] = speed_text(_net["speed"])
        return reading
    except Exception:  # a reading is a nicety: it must never take the watcher or a request down
        return None


def network_soon(wait: float = NET_FIRST_WAIT_SECONDS) -> dict | None:
    """`network` without the wait: the last reading at once, refreshed in the background at most every
    NET_REFRESH_SECONDS and one run at a time. Only the very first call waits, up to `wait` seconds."""
    with _net_lock:
        thread = _net["thread"]
        if thread is None and time.monotonic() - _net["at"] >= NET_REFRESH_SECONDS:
            def refresh() -> None:
                try:
                    reading = network()
                    if reading is not None:
                        _net["value"] = reading
                finally:
                    _net["at"] = time.monotonic()
                    _net["thread"] = None

            thread = _net["thread"] = threading.Thread(target=refresh, daemon=True)
            thread.start()
    if _net["value"] is None and thread is not None and wait > 0:
        thread.join(wait)
    return _net["value"]


def measure_speed() -> dict:
    """How fast the internet comes down right now: a download that stops after NET_SPEED_SECONDS or 8 MB,
    whichever is first. Only when someone asks, because on a phone's hotspot those megabytes are counted.
    Gives {down_mbps, mb, seconds, at, text}; raises OSError when the internet cannot be reached."""
    request = urllib.request.Request(NET_SPEED_URL, headers={"User-Agent": "cairn", "Cache-Control": "no-cache"})
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=NET_PROBE_TIMEOUT * 2) as reply:
            started = time.perf_counter()  # the clock starts when the answer starts, not while the line is set up
            seconds = 0.0
            while seconds < NET_SPEED_SECONDS:
                chunk = reply.read1(65536)
                seconds = time.perf_counter() - started
                if not chunk:
                    break
                received += len(chunk)
    except OSError:
        if not received:
            raise
        seconds = time.perf_counter() - started  # the line broke part way: what came still says how fast
    except Exception as error:  # a cut answer that is not an OSError
        raise OSError(str(error)) from error
    if not received or seconds <= 0:
        raise OSError("the speed test sent nothing back")
    speed = {
        "down_mbps": round(received * 8 / 1e6 / seconds, 1 if received * 8 / 1e6 / seconds < 10 else 0),
        "mb": round(received / 1e6, 1),
        "seconds": round(seconds, 1),
        "at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
    }
    speed["text"] = speed_text(speed)
    _net["speed"] = speed
    if _net["value"] is not None:  # the page shows it on its next look, not one reading later
        _net["value"] = {**_net["value"], "speed": speed["text"]}
        if _soon["value"] is not None and _soon["value"].get("net"):
            _soon["value"] = {**_soon["value"], "net": _net["value"]}
    return dict(speed)


# ------------------------------------------------------------ what the page gets


def specs() -> dict:
    """The model, processor, cores and memory. Read once: they do not change while the app runs.
    No computer name, user name or serial number: only what the box says on the outside."""
    global _specs
    if _specs is None:
        bios = r"HARDWARE\DESCRIPTION\System\BIOS"
        memory = _memory()
        if IS_MAC:
            name = mac_model_name(_run(["sysctl", "-n", "hw.model"]))
            cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        else:
            name = machine_name(_registry(bios, "SystemManufacturer"), _registry(bios, "SystemProductName"))
            cpu = _registry(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
        _specs = {
            "name": name,
            "cpu": clean_cpu_name(cpu),
            "cores": os.cpu_count() or 0,
            "ram_gb": round(memory["total"] / 2**30, 1) if memory else 0,
        }
    return dict(_specs)


def device_name() -> str:
    """What this computer is called on its session notes and in the device switch: the model, as on its
    machines/ note. Two computers of one model share that name, so $CAIRN_DEVICE can give one its own
    ("Work laptop"). Never the computer name or the user name."""
    override = " ".join(os.environ.get("CAIRN_DEVICE", "").split())
    if override:
        return override
    try:
        return specs()["name"] or "This computer"
    except Exception:  # a name is a nicety too
        return "This computer"


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


def snapshot(net_wait: float = NET_FIRST_WAIT_SECONDS) -> dict | None:
    """The laptop and its load right now, or None where it cannot be read. Never raises.
    `net` is the network it is on (see `network`), or None while the first look at it is still out;
    `net_wait` is how long this call may wait for that first look."""
    try:
        net = network_soon(net_wait)  # before the lock: it may wait, and needs nothing the lock guards
        with _lock:
            memory = _memory()
            if memory is None:
                return None
            now = time.monotonic()
            reading = specs()
            reading["name"] = device_name()
            cpu_pct = _cpu_percent(now)
            level, reasons = load_level(cpu_pct, memory["ram_pct"], memory["commit_pct"])
            reading.update({
                "at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
                "cpu_pct": cpu_pct,
                "ram_pct": memory["ram_pct"],
                "ram_free_gb": round(memory["free"] / 2**30, 1),
                "commit_pct": memory["commit_pct"],
                "power": _power(),
                "uptime": uptime_text(_uptime_seconds()),
                "level": level,
                "reasons": reasons,
                "top": [top_text(group) for group in _top_processes(now, reading["cores"])],
                "net": net,
            })
            return reading
    except Exception:  # a reading is a nicety: it must never take the watcher or a request down
        return None


def load_now() -> dict | None:
    """Only how busy the processors and how full the memory are right now: {cpu_pct, ram_pct, commit_pct,
    uptime_hours, level}. Cheap enough for every watcher pass (no process list, no specs). None where it
    cannot be read. The load limit (cairn/limit.py) is judged from these looks."""
    try:
        with _lock:
            memory = _memory()
            if memory is None:
                return None
            cpu_pct = _cpu_percent(time.monotonic())
            level, _ = load_level(cpu_pct, memory["ram_pct"], memory["commit_pct"])
            return {"cpu_pct": cpu_pct, "ram_pct": memory["ram_pct"], "commit_pct": memory["commit_pct"],
                    "uptime_hours": uptime_hours(_uptime_seconds()), "level": level}
    except Exception:  # a reading is a nicety: it must never take the watcher down
        return None


def snapshot_soon() -> dict | None:
    """`snapshot` for the live page, which must never wait on it.

    The laptop this exists for is the busy one, and there a reading (listing every process, the first
    ctypes import) can take many seconds. So the last reading is returned at once and refreshed in the
    background, one run at a time. None until the first reading is in; a failed run keeps the last one."""
    with _soon_lock:
        thread = _soon["thread"]
        if thread is None and time.monotonic() - _soon["at"] >= SOON_TTL_SECONDS:
            def refresh() -> None:
                try:
                    reading = snapshot()
                    if reading is not None:
                        _soon["value"] = reading
                finally:
                    _soon["at"] = time.monotonic()
                    _soon["thread"] = None

            thread = _soon["thread"] = threading.Thread(target=refresh, daemon=True)
            thread.start()
    if _soon["value"] is None and thread is not None:
        thread.join(SOON_FIRST_WAIT_SECONDS)
    return _soon["value"]

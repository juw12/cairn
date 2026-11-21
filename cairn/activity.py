"""How much this computer was used each day: screen time, processor and memory, hour by hour.

The session watcher already looks once a minute. On each look this adds what happened since the look
before to the day's figures: the computer was awake, somebody was at it (a key or the mouse moved in the
last two minutes), how busy the processors were and how full the memory was. Nothing else is read: no
window titles, no app names, no keys.

The figures live in the watcher's own state file and are written, about twice an hour, to one rolling
note per computer, `machines/<computer>-activity.md`: one line a day, newest first. Every computer that
shares the brain writes its own note, so the Diary page can show them side by side without a list of
devices kept anywhere.

Standard library only. Windows (user32 through ctypes) and macOS (ioreg) can tell when the last key or
mouse move was; elsewhere screen time is left out and the rest is still noted.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys

from . import core

IDLE_GRACE_SECONDS = 120  # no key and no mouse for this long: nobody is at the computer
GAP_SECONDS = 300  # a look this long after the last one: the computer slept or the watcher was off
NOTE_MINUTES = 30  # the note is written again after this long, and at once on a new day
KEEP_DAYS = 120  # lines kept in the note
STATE_DAYS = 7  # days kept in the watcher's state; older ones live in the note only
NOTE_SUFFIX = "-activity"
FIELDS = ("screen", "awake", "cpu", "cpu_peak", "ram", "ram_peak", "heavy", "first", "last")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLOCK = re.compile(r"^\d{2}:\d{2}$")
_IOREG_IDLE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


# ------------------------------------------------------------ when the last key or mouse move was


def parse_ioreg_idle(text: str) -> float | None:
    """Seconds since the last input from `ioreg -c IOHIDSystem`, which counts in nanoseconds."""
    found = _IOREG_IDLE.search(text or "")
    return int(found.group(1)) / 1_000_000_000 if found else None


def idle_seconds() -> float | None:
    """Seconds since the last key press or mouse move, or None where that cannot be read. Never raises."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class LastInput(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

            info = LastInput()
            info.cbSize = ctypes.sizeof(info)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            ticks = ctypes.windll.kernel32.GetTickCount()
            return ((ticks - info.dwTime) & 0xFFFFFFFF) / 1000  # both wrap at 32 bits
        if sys.platform == "darwin":
            done = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True,
                                  timeout=5, encoding="utf-8", errors="replace")
            return parse_ioreg_idle(done.stdout)
    except Exception:  # a reading is a nicety: it must never take the watcher down
        return None
    return None


# ------------------------------------------------------------ adding one look to the day (pure)


def _new_day() -> dict:
    return {"awake": 0, "screen": 0, "seen": 0, "hours": [0] * 24, "cpu_sum": 0, "cpu_n": 0, "cpu_peak": 0,
            "ram_sum": 0, "ram_n": 0, "ram_peak": 0, "heavy": 0, "first": "", "last": ""}


def _whole(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def add_look(log: dict | None, now: dt.datetime, idle: float | None, load: dict | None) -> dict:
    """The log with what happened since the look before added to `now`'s day. Seconds throughout.

    `log` is {"at": the last look, "written": when the note was last written, "days": {day: counts}}.
    A first look, or one more than GAP_SECONDS after the last, adds no time: the computer was asleep or
    the watcher was not running, and nobody can say which."""
    log = log if isinstance(log, dict) else {}
    days = {key: {**_new_day(), **value} for key, value in (log.get("days") or {}).items()
            if _DAY.match(str(key)) and isinstance(value, dict)}
    try:
        step = (now - dt.datetime.fromisoformat(str(log.get("at", "")))).total_seconds()
    except ValueError:
        step = 0
    step = int(step) if 0 < step <= GAP_SECONDS else 0
    today = now.strftime("%Y-%m-%d")
    day = days.setdefault(today, _new_day())
    hours = [_whole(value) for value in (list(day.get("hours") or []) + [0] * 24)[:24]]
    day["awake"] = _whole(day["awake"]) + step
    if idle is not None and step:
        used = max(0, step - max(0, int(idle) - IDLE_GRACE_SECONDS))
        day["seen"] = _whole(day["seen"]) + step
        day["screen"] = _whole(day["screen"]) + used
        hours[now.hour] += used
        if used:
            clock = now.strftime("%H:%M")
            day["first"] = day["first"] or clock
            day["last"] = clock
    day["hours"] = hours
    if load and step:
        for key in ("cpu", "ram"):
            value = load.get(f"{key}_pct")
            if value is None:
                continue
            day[f"{key}_sum"] = _whole(day[f"{key}_sum"]) + _whole(value)
            day[f"{key}_n"] = _whole(day[f"{key}_n"]) + 1
            day[f"{key}_peak"] = max(_whole(day[f"{key}_peak"]), _whole(value))
        if load.get("level") == "heavy":
            day["heavy"] = _whole(day["heavy"]) + step
    kept = sorted(days)[-STATE_DAYS:]
    return {"at": now.isoformat(timespec="seconds"), "written": str(log.get("written", "")),
            "days": {key: days[key] for key in kept}}


def day_figures(day: dict) -> dict:
    """One day's counts as the note carries them: whole minutes and whole percents, "" for what is unknown."""
    minutes = lambda seconds: round(_whole(seconds) / 60)  # noqa: E731
    known = _whole(day.get("seen")) > 0
    average = lambda key: round(_whole(day.get(f"{key}_sum")) / _whole(day.get(f"{key}_n"))) if _whole(day.get(f"{key}_n")) else ""  # noqa: E731
    return {
        "screen": minutes(day.get("screen")) if known else "",
        "awake": minutes(day.get("awake")),
        "cpu": average("cpu"),
        "cpu_peak": _whole(day.get("cpu_peak")) if _whole(day.get("cpu_n")) else "",
        "ram": average("ram"),
        "ram_peak": _whole(day.get("ram_peak")) if _whole(day.get("ram_n")) else "",
        "heavy": minutes(day.get("heavy")),
        "first": str(day.get("first") or ""),
        "last": str(day.get("last") or ""),
        "hours": [minutes(value) for value in (list(day.get("hours") or []) + [0] * 24)[:24]] if known else [],
    }


# ------------------------------------------------------------ the note (pure)


def day_line(date: str, figures: dict) -> str:
    """`- 2026-11-21 | screen 212 | awake 300 | cpu 23 | ... | hours 0 0 12 ...`; unknown figures are left out."""
    parts = [date]
    parts += [f"{key} {figures[key]}" for key in FIELDS if figures.get(key) not in ("", None)]
    if figures.get("hours"):
        parts.append("hours " + " ".join(str(_whole(value)) for value in figures["hours"]))
    return "- " + " | ".join(parts)


def parse_line(line: str) -> tuple[str, dict] | None:
    """(date, figures) from a line `day_line` wrote, or None for any other line."""
    line = line.strip()
    if not line.startswith("- "):
        return None
    parts = [part.strip() for part in line[2:].split("|")]
    if not _DAY.match(parts[0]):
        return None
    figures: dict = {}
    for part in parts[1:]:
        key, _, value = part.partition(" ")
        value = value.strip()
        if key == "hours":
            figures["hours"] = [_whole(item) for item in value.split()][:24]
        elif key in ("first", "last"):
            if _CLOCK.match(value):
                figures[key] = value
        elif key in FIELDS and re.fullmatch(r"\d+", value):
            figures[key] = int(value)
    return parts[0], figures


def parse_days(text: str) -> dict[str, dict]:
    """Every day line of an activity note: {date: figures}."""
    found = {}
    for line in (text or "").splitlines():
        parsed = parse_line(line)
        if parsed:
            found[parsed[0]] = parsed[1]
    return found


def note_path(name: str, folder: str = "machines") -> str:
    return f"{folder}/{core.slugify(name) or 'this-computer'}{NOTE_SUFFIX}.md"


def note_text(name: str, days: dict, existing_text: str, now: dt.datetime) -> str:
    """The whole note: the days in `days` (counts from the watcher) as they are now, and every older line
    the note already had. Newest first, KEEP_DAYS lines at most."""
    lines = parse_days(core.parse_note(existing_text)["body"]) if existing_text else {}
    for date, counts in (days or {}).items():
        figures = day_figures(counts)
        if figures["awake"] or figures["screen"]:
            lines[date] = figures
    meta = {
        "title": f"{name} activity",
        "type": "note",
        "tags": ["activity"],
        "machine": name,
        "activity_at": now.strftime("%Y-%m-%dT%H:%M"),
    }
    body = [
        "Written by the Cairn session watcher, about twice an hour, so the Diary page can show how much this",
        "computer was used each day. One line a day, in minutes and percent: `screen` is time with a key or the",
        "mouse used in the last two minutes, `awake` is time the computer was on, `cpu` and `ram` are the day's",
        "averages with their peaks, `heavy` is time under heavy load, `hours` is screen minutes for each hour",
        "from midnight. No window titles, app names or keys are read.",
        "",
    ]
    body += [day_line(date, lines[date]) for date in sorted(lines, reverse=True)[:KEEP_DAYS]]
    return core.serialize_note(meta, "\n".join(body) + "\n")


def note_due(log: dict, now: dt.datetime) -> bool:
    """Time to write the note: there is a day to write, and it was never written, is NOTE_MINUTES old,
    or a new day began."""
    if not any(_whole(day.get("awake")) for day in (log.get("days") or {}).values()):
        return False
    try:
        written = dt.datetime.fromisoformat(str(log.get("written", "")))
    except ValueError:
        return True
    return written.date() != now.date() or abs((now - written).total_seconds()) >= NOTE_MINUTES * 60


def seed_days(text: str) -> dict:
    """Counts to carry on from when the watcher's state was lost: the last days of the note, as seconds.
    Averages restart from the note's figure with the weight of one look, which is close enough."""
    days = {}
    for date, figures in sorted(parse_days(core.parse_note(text)["body"]).items())[-STATE_DAYS:]:
        day = _new_day()
        day["awake"] = _whole(figures.get("awake")) * 60
        if "screen" in figures:
            day["screen"] = _whole(figures["screen"]) * 60
            day["seen"] = max(day["awake"], 1)
            day["hours"] = [_whole(value) * 60 for value in (figures.get("hours", []) + [0] * 24)[:24]]
        for key in ("cpu", "ram"):
            if key in figures:
                weight = max(1, round(day["awake"] / 60))
                day[f"{key}_sum"], day[f"{key}_n"] = _whole(figures[key]) * weight, weight
                day[f"{key}_peak"] = _whole(figures.get(f"{key}_peak", figures[key]))
        day["heavy"] = _whole(figures.get("heavy")) * 60
        day["first"], day["last"] = figures.get("first", ""), figures.get("last", "")
        days[date] = day
    return days

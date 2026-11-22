"""The load limit: how hard a computer may be worked, and for how long, before Cairn says stop.

Assistants do not get tired, and a laptop does. One that ran several of them for 100 hours at about 80% of
its processor, with its memory nearly all promised, lost its drive in the middle of the work: the load note
called every one of those hours "medium", so nothing ever said stop. A level describes a moment. The limit
describes a stretch of time: the processor's average over the last hour and memory that stays full. A
computer that is over it and has also been up for days is due for a restart.

The limit is one small file in the brain, CAIRN-LIMIT.md, so every computer of the brain follows the same
one and any device can change it (`cairn vibe limit`, or by hand):

    - on: yes
    - processor: 75
    - minutes: 60
    - memory: 90
    - restart hours: 72

Each computer measures itself: the watcher's look once a minute (cairn/watch.py) is kept for a few hours in
a file of its own next to the watcher's state, never in the brain. Over the limit:

- an assistant that logs its steps with `cairn vibe` is told at its next step to start nothing heavy
  (`notice`, the same way it is told its place in the user's order);
- Cairn starts no work another computer left for an app of this one until it is back under (cairn/runner.py
  is not called by the watcher's pass);
- the computer's note in machines/ says so, so every device and the phone show it.

Cairn stops and slows no program. Holding the busy ones back is the work of Spry, which follows a limit of
the same shape on the computer it runs on. Cairn reads no temperature either: it limits the work that makes
the heat.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

from . import core
from .brain import Brain, ConflictError

FILE = "CAIRN-LIMIT.md"
LOG_FILE = "cairn-limit.json"  # this computer's looks, next to the watcher's state: never synced
DEFAULTS = {"on": True, "processor": 75, "minutes": 60, "memory": 90, "restart_hours": 72}
BOUNDS = {"processor": (30, 100), "minutes": (10, 240), "memory": (50, 100), "restart_hours": (0, 720)}
KEEP_MINUTES = BOUNDS["minutes"][1]  # looks older than the longest stretch a limit can name are dropped
MEMORY_MINUTES = 15  # memory counts once it has stayed full this long: one big build is not a full computer
COVERED = 0.75  # a stretch is judged once looks cover this much of it: an hour's average needs most of an hour
REST_POINTS = 5  # once over, the processor's average has to fall this far below the limit to count as rested
FRESH_SECONDS = 300  # with no look for this long nobody is measuring, and nothing is said either way
MACHINE_FOLDER = "machines"

_KEYS = {"on": "on", "processor": "processor", "minutes": "minutes", "memory": "memory", "restart hours": "restart_hours"}
_LINE = re.compile(r"^[ \t]*[-*]?[ \t]*(on|processor|minutes|memory|restart hours)[ \t]*:[ \t]*([^\s#]+)", re.M | re.I)
_YES = ("yes", "on", "true", "1")
_NO = ("no", "off", "false", "0")

INTRO = """---
type: settings
---

# Cairn load limit

How hard a computer of this brain may be worked before Cairn says stop. Every computer follows the same
limit and measures itself. Over it, the assistants there are told to start nothing heavy, Cairn starts no
handed-over work there, and the computer's card on Vibe says so, until it has rested. Change the numbers
here, or with `cairn vibe limit`.

- processor: the processor's average use in percent, over the minutes below
- memory: memory in use in percent (RAM, or RAM plus page file promised to apps) that stays there 15 minutes
- restart hours: a computer that is over the limit and has been up this many hours is due for a restart (0: never said)
"""


class LimitError(ValueError):
    """A limit that was not changed, with a sentence a person can act on."""


# ------------------------------------------------------------ the file


def parse(text: str) -> dict:
    """The limit a file names. A line that is missing or cannot be read keeps the default, and a number
    outside its bounds is brought inside them: a typing slip must not switch the limit off."""
    limits = dict(DEFAULTS)
    for match in _LINE.finditer(str(text or "").replace("\r\n", "\n")):
        key, value = _KEYS[" ".join(match.group(1).lower().split())], match.group(2).strip().lower().rstrip("%")
        if key == "on":
            if value in _YES + _NO:
                limits["on"] = value in _YES
            continue
        try:
            number = int(float(value))
        except ValueError:
            continue
        low, high = BOUNDS[key]
        limits[key] = min(high, max(low, number))
    return limits


def file_text(limits: dict) -> str:
    """The whole file for these numbers."""
    return (f"{INTRO}\n- on: {'yes' if limits['on'] else 'no'}\n- processor: {limits['processor']}\n"
            f"- minutes: {limits['minutes']}\n- memory: {limits['memory']}\n- restart hours: {limits['restart_hours']}\n")


def file(brain: Brain) -> dict | None:
    """The file as it is ({path, text, mtime}), None while there is none."""
    try:
        return brain.read(FILE)
    except (OSError, ValueError):
        return None


def limits(brain: Brain) -> dict:
    """The limit of this brain: the file's, or the defaults while there is no file."""
    current = file(brain)
    return parse(current["text"]) if current else dict(DEFAULTS)


def set_limits(brain: Brain, **changes) -> dict:
    """Change some of the numbers (or `on`) and write the file. Returns the limit as it is now."""
    wanted = limits(brain)
    for key, value in changes.items():
        if value is None:
            continue
        if key == "on":
            wanted["on"] = bool(value)
            continue
        if key not in BOUNDS:
            raise LimitError(f"There is no limit called {key!r}.")
        low, high = BOUNDS[key]
        if not low <= int(value) <= high:
            raise LimitError(f"{key.replace('_', ' ')} takes a number from {low} to {high}, not {value}.")
        wanted[key] = int(value)
    text = file_text(wanted)
    for _ in range(3):  # another device or the page may be saving this second: the newest choice wins
        current = file(brain)
        try:
            brain.write(FILE, text, current["mtime"] if current else None)
            return wanted
        except ConflictError:
            continue
    raise LimitError("The limit is being written right now. Try again in a moment.")


# ------------------------------------------------------------ the looks (pure)


def _when(stamp) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(stamp))
    except ValueError:
        return None


def _number(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _looks(log, now: dt.datetime) -> list[tuple[dt.datetime, int | None, int | None]]:
    """The looks of a log that still count: read whole, not from the future, not older than is kept."""
    kept = []
    for look in (log or {}).get("looks") or []:
        if not isinstance(look, (list, tuple)) or len(look) != 3:
            continue
        at = _when(look[0])
        if at is None or not 0 <= (now - at).total_seconds() <= KEEP_MINUTES * 60:
            continue
        kept.append((at, _number(look[1]), _number(look[2])))
    return kept


def judge(log, limit: dict, now: dt.datetime) -> dict:
    """What the looks say against the limit: {state: off | ok | over, known, why, since, cpu_avg, restart}.
    `known` is False while nobody has looked lately (Cairn was closed, the computer slept): then nothing
    is claimed. `since` is when it went over; `restart` is a sentence when it is over and has also been up
    longer than the limit names."""
    reading = {"state": "ok" if limit["on"] else "off", "known": False, "why": [], "since": "", "cpu_avg": None,
               "restart": "", "limits": dict(limit)}
    looks = _looks(log, now)
    if not limit["on"] or not looks or (now - looks[-1][0]).total_seconds() > FRESH_SECONDS:
        return reading
    reading["known"] = True
    minutes = limit["minutes"]
    was_over = bool((log or {}).get("over_since"))

    stretch = [(at, cpu) for at, cpu, _ in looks if cpu is not None and (now - at).total_seconds() <= minutes * 60]
    if stretch:
        reading["cpu_avg"] = round(sum(cpu for _, cpu in stretch) / len(stretch))
    # Covered minutes, not looks: a pass and the minute's own look can both land in one minute.
    if len({at.strftime("%Y-%m-%dT%H:%M") for at, _ in stretch}) >= COVERED * minutes:
        rested_below = limit["processor"] - REST_POINTS
        if reading["cpu_avg"] >= (rested_below if was_over else limit["processor"]):
            rest = f", rested below {rested_below}%" if reading["cpu_avg"] < limit["processor"] else ""
            reading["why"].append(f"processor {reading['cpu_avg']}% busy on average over the last {minutes} min "
                                  f"(limit {limit['processor']}%{rest})")

    recent = [(at, memory) for at, _, memory in looks if memory is not None and (now - at).total_seconds() <= MEMORY_MINUTES * 60]
    if recent and len({at.strftime("%Y-%m-%dT%H:%M") for at, _ in recent}) >= COVERED * MEMORY_MINUTES:
        lowest = min(memory for _, memory in recent)
        if lowest >= limit["memory"]:
            reading["why"].append(f"memory {lowest}% or more in use for {MEMORY_MINUTES} min (limit {limit['memory']}%)")

    if reading["why"]:
        reading["state"] = "over"
        reading["since"] = str((log or {}).get("over_since") or now.strftime("%Y-%m-%dT%H:%M"))
    # Hours up do a computer no harm by themselves: one that idles for two weeks is not nagged about.
    # A restart is due when it is over its limit and has also been up that long.
    hours = _number((log or {}).get("uptime_h"))
    if reading["state"] == "over" and limit["restart_hours"] and hours is not None and hours >= limit["restart_hours"]:
        reading["restart"] = f"up {hours} h since restart (due after {limit['restart_hours']} h)"
    return reading


def add_look(log, now: dt.datetime, load: dict | None, limit: dict) -> dict:
    """The log with this look added and what `judge` then says kept in it: `load` is `machine.load_now()`
    ({cpu_pct, ram_pct, commit_pct, uptime_hours}), or None where the computer cannot be read."""
    looks = [[at.isoformat(timespec="seconds"), cpu, memory] for at, cpu, memory in _looks(log, now)]
    after = {"looks": looks, "over_since": str((log or {}).get("over_since") or ""), "uptime_h": (log or {}).get("uptime_h")}
    if load:
        memory = [value for value in (_number(load.get("ram_pct")), _number(load.get("commit_pct"))) if value is not None]
        looks.append([now.isoformat(timespec="seconds"), _number(load.get("cpu_pct")), max(memory) if memory else None])
        after["uptime_h"] = _number(load.get("uptime_hours"))
    reading = judge(after, limit, now)
    # A computer nobody measured for a while starts fresh: it rested, or was off.
    after["over_since"] = reading["since"] if reading["state"] == "over" else ""
    return after


# ------------------------------------------------------------ this computer


def log_path(brain: Brain) -> Path:
    git_dir = brain.root / ".git"
    return (git_dir if git_dir.is_dir() else brain.dir) / LOG_FILE


def _load(brain: Brain) -> dict:
    try:
        log = json.loads(log_path(brain).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return log if isinstance(log, dict) else {}


def look(brain: Brain, load: dict | None, now: dt.datetime | None = None) -> dict:
    """Add one look at this computer (the watcher's, once a minute) and return what `judge` says now."""
    now = now or dt.datetime.now()
    limit = limits(brain)
    log = add_look(_load(brain), now, load, limit)
    path = log_path(brain)
    try:
        spare = path.with_name(path.name + ".new")  # written whole, then swapped in: a reader never sees half a file
        spare.write_text(json.dumps(log), encoding="utf-8")
        os.replace(spare, path)
    except OSError:
        pass  # a look is a nicety: the next one is in a minute
    return judge(log, limit, now)


def status(brain: Brain, now: dt.datetime | None = None) -> dict:
    """This computer against the limit, from the looks already taken. Reads two small files and nothing else."""
    return judge(_load(brain), limits(brain), now or dt.datetime.now())


def over(brain: Brain, now: dt.datetime | None = None) -> bool:
    try:
        return status(brain, now)["state"] == "over"
    except Exception:  # noqa: BLE001 - a limit that cannot be read holds nothing back
        return False


# ------------------------------------------------------------ what is said


def _clock(stamp: str) -> str:
    return stamp.replace("T", " ") if stamp else ""


def note_fields(reading: dict | None) -> dict:
    """What a computer's note in machines/ says about its limit: empty until it is measured."""
    if not reading or not reading.get("known"):
        return {"limit": "", "limit_why": "", "limit_since": "", "limit_restart": ""}
    return {"limit": reading["state"], "limit_why": "; ".join(reading["why"]), "limit_since": reading["since"],
            "limit_restart": reading["restart"]}


def note_line(fields: dict) -> str:
    """The sentence of that note's text, "" when there is nothing to say."""
    parts = []
    if fields.get("limit") == "over":
        since = f" since {_clock(fields['limit_since'])}" if fields.get("limit_since") else ""
        parts.append(f"Over its load limit{since}: {fields.get('limit_why', '')}. Cairn starts no handed-over work "
                     "here and tells its assistants to start nothing heavy until it has rested")
    if fields.get("limit_restart"):
        parts.append(f"Due for a restart: {fields['limit_restart']}")
    return ". ".join(parts)


def notice(brain: Brain, now: dt.datetime | None = None) -> str:
    """What an assistant working on this computer has to know: "" while it is under its limit."""
    reading = status(brain, now)
    lines = []
    if reading["state"] == "over":
        lines.append(f"LOAD LIMIT (set in Cairn): this computer is over its limit since {_clock(reading['since'])}: "
                     f"{'; '.join(reading['why'])}.\n"
                     "  Start nothing heavy here until it has rested: no build, test run, render, download, browser fleet or\n"
                     "  second assistant. Let what runs finish, or pause it. Work that can wait, waits, and you log that you wait;\n"
                     "  work another computer can do goes there as a handoff (cairn handoff send).\n"
                     "  `cairn vibe limit` shows the figures. The limit is the user's: change it only when they say so.")
    if reading["restart"]:
        lines.append(f"RESTART DUE (set in Cairn): this computer is over its limit and {reading['restart']}.\n"
                     "  Tell the user, once. Do not restart it yourself.")
    return "\n".join(lines)


def others(brain: Brain) -> list[dict]:
    """What the other computers' notes say about their limit: [{name, at, limit, why, since, restart}]."""
    found = []
    try:
        paths = sorted((brain.dir / MACHINE_FOLDER).glob("*.md"))
    except OSError:
        return found
    for path in paths:
        try:
            meta = core.parse_note(path.read_text(encoding="utf-8"))["meta"]
        except (OSError, ValueError):
            continue
        tags = meta.get("tags") or []
        if "machine" not in (tags if isinstance(tags, list) else [tags]):
            continue
        found.append({"name": str(meta.get("title") or path.stem), "at": str(meta.get("machine_at") or ""),
                      "limit": str(meta.get("limit") or ""), "why": str(meta.get("limit_why") or ""),
                      "since": str(meta.get("limit_since") or ""), "restart": str(meta.get("limit_restart") or "")})
    return found


def summary(brain: Brain, device: str = "", now: dt.datetime | None = None) -> str:
    """The limit, this computer against it, and what the notes of the brain's computers say."""
    reading = status(brain, now)
    limit = reading["limits"]
    restart = f"due when over it and up {limit['restart_hours']} h" if limit["restart_hours"] else "never said"
    lines = [
        f"Load limit ({FILE}, the same for every computer of this brain): {'on' if limit['on'] else 'OFF'}",
        f"  processor {limit['processor']}% on average over {limit['minutes']} min · memory {limit['memory']}% for "
        f"{MEMORY_MINUTES} min · restart {restart}",
    ]
    if reading["state"] == "off":
        here = "not judged, the limit is off"
    elif not reading["known"]:
        here = "not measured yet (Cairn's watcher looks once a minute while it runs)"
    elif reading["state"] == "over":
        here = f"OVER its limit since {_clock(reading['since'])}: {'; '.join(reading['why'])}"
    else:
        average = "" if reading["cpu_avg"] is None else f" (processor {reading['cpu_avg']}% on average over the last {limit['minutes']} min at most)"
        here = f"under its limit{average}"
    lines.append(f"This computer{f' ({device})' if device else ''}: {here}")
    if reading["restart"]:
        lines.append(f"  Due for a restart: {reading['restart']}")
    key = core.device_key(device) if device else ""
    for other in others(brain):
        if key and core.device_key(other["name"]) == key:
            continue
        said = {"over": f"OVER its limit since {_clock(other['since'])}: {other['why']}", "ok": "under its limit",
                "off": "the limit is off"}.get(other["limit"], "its note does not say yet (it needs this build of Cairn)")
        due = f"; due for a restart: {other['restart']}" if other["restart"] else ""
        lines.append(f"{other['name']}: {said}{due} (its note of {_clock(other['at']) or 'no time'})")
    lines.append("Change it: cairn vibe limit --processor 70 --minutes 45 --memory 90 --restart-hours 72 · cairn vibe limit off | on")
    return "\n".join(lines)

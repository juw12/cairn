"""Priority: which of the running sessions matter most, in the order their user gave them.

Several assistants work at once, on one computer or on several, and only the user knows which piece of
work has to be finished first. They say so in one place (the Vibe page's Priority block, or `cairn vibe
rank`): the running sessions in one list, the most important at the top and the others under it in order.

The order is one small file in the brain, CAIRN-PRIORITY.md, so every computer and the phone show the same
list and any of them can change it. It holds the sessions' names, one numbered line each:

    1. 2026-11-22-neon-coast-touch-controls
    2. 2026-11-22-lab-writer

A running session that is not listed yet (a new one) comes after the listed ones, in the order it started,
so nothing has to be done for it until the user wants it higher. A listed session that ended is dropped the
next time the order is written. The same rules are in docs/rank.js; tests/test_rank.py holds them together.

How an assistant learns its place: `notice` is printed by `cairn vibe start`, `log` and `check`. Nothing
reaches into the AI app, and nothing is stopped or slowed by Cairn: the order tells each assistant which
work goes first where two of them need the same thing. A session a watcher found by itself (an OpenCode
chat that never runs `cairn vibe`) is listed and ordered like the others, and is not told.
"""

from __future__ import annotations

import datetime as dt
import re

from . import core, vibe
from .brain import Brain, ConflictError

FILE = "CAIRN-PRIORITY.md"
STALE_MIN = 1440  # no step for a day and still marked live: left open, not running (see docs/recap.js)
NAMED_BEFORE = 5  # how many of the sessions before it a notice names
MOVES = ("top", "up", "down", "bottom")

_LINE = re.compile(r"^[ \t]*\d+[.)][ \t]+(.+?)[ \t]*$", re.M)
_HEADER_CHARS = 2000  # a note's properties are at its top: enough to see whether it is live without parsing it
_LIVE_LINE = re.compile(r"^status:[ \t]*[\"']?live[\"']?[ \t]*$", re.M)

INTRO = """---
type: settings
---

# Cairn priority

The running sessions in the order they matter to you, the most important first. Arrange them in Cairn
(Vibe, Priority: the up and down buttons, Top, or dragging the handle) or with `cairn vibe rank`. Every
device shows the same order, and an assistant that logs its steps with `cairn vibe` is told its place at its
next step. A running session that is not listed yet comes after these, in the order it started. Editing
these lines by hand works too.
"""


class RankError(ValueError):
    """An order that was not changed, with a sentence a person can act on."""


# ------------------------------------------------------------ the file


def _name(value: str) -> str:
    """A session's name as the file may spell it: its slug, its path, or a [[link]] to it."""
    name = str(value or "").strip().removeprefix("[[").removesuffix("]]").split("|", 1)[0].strip()
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-3] if name.lower().endswith(".md") else name


def parse(text: str) -> list[str]:
    """The sessions the file lists, in its order. A name listed twice counts where it stands first."""
    names: list[str] = []
    for match in _LINE.finditer(str(text or "").replace("\r\n", "\n")):
        name = _name(match.group(1))
        if name and name not in names:
            names.append(name)
    return names


def file_text(names: list[str]) -> str:
    """The whole file for this order."""
    lines = "\n".join(f"{place}. {name}" for place, name in enumerate(names, 1))
    return f"{INTRO}\n{lines}\n" if lines else INTRO


def listed(brain: Brain) -> list[str]:
    try:
        return parse(brain.read(FILE)["text"])
    except (OSError, ValueError):
        return []


def file(brain: Brain) -> dict | None:
    """The file as the live page takes it ({path, text, mtime}), None while there is none."""
    try:
        return brain.read(FILE)
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------ the order


def _now_minutes(now: dt.datetime | None = None) -> int | None:
    return core.stamp_minutes((now or dt.datetime.now()).strftime("%Y-%m-%dT%H:%M"))


def is_running(note: dict, now_minutes: int | None) -> bool:
    """Live, and not left open: a session with no step for a day is not one to put in order."""
    if note["type"] != "session" or note["status"] != "live":
        return False
    last = core.session_last_activity(note)
    return last is None or now_minutes is None or now_minutes - last < STALE_MIN


def arrange(notes: list[dict], names: list[str]) -> list[dict]:
    """`notes` in the user's order: the listed ones as listed, then the others as they started."""
    place = {name: index for index, name in enumerate(names)}
    return sorted(notes, key=lambda note: (place.get(note["slug"], len(names)), str(note["meta"].get("started", "")), note["path"]))


def running(brain: Brain, now: dt.datetime | None = None) -> list[dict]:
    """Every running session of every computer, parsed only when its top says it is live: a brain can hold
    thousands of session notes."""
    folder = core.FOLDERS["session"] + "/"
    minutes = _now_minutes(now)
    found = []
    for item in vibe.vibe_files(brain):
        if not item["path"].startswith(folder) or not _LIVE_LINE.search(item["text"][:_HEADER_CHARS]):
            continue
        note = core.note_from_file(item["path"], item["text"], item["mtime"])
        if is_running(note, minutes):
            found.append(note)
    return found


def _entry(note: dict, place: int, names: list[str]) -> dict:
    return {
        "place": place,
        "session": note["slug"],
        "path": note["path"],
        "title": note["title"],
        "device": str(note["meta"].get("machine", "")).strip(),
        "agent": str(note["meta"].get("agent", "") or note["meta"].get("source", "")).strip(),
        "here": not vibe.elsewhere(note),
        "listed": note["slug"] in names,
    }


def status(brain: Brain, now: dt.datetime | None = None) -> dict:
    """The running sessions in order. `ranked` is False until the user has placed one of them: before
    that the order is only the order they started in, and no assistant is told a place."""
    names = listed(brain)
    notes = arrange(running(brain, now), names)
    sessions = [_entry(note, place, names) for place, note in enumerate(notes, 1)]
    return {"ranked": any(entry["listed"] for entry in sessions), "sessions": sessions}


def summary(reading: dict) -> str:
    sessions = reading["sessions"]
    if not sessions:
        return "No session is running, so there is nothing to put in order."
    head = ("The running sessions, the most important first:" if reading["ranked"] else
            "The running sessions in the order they started. None is placed yet: cairn vibe rank \"<session>\" --to top")
    lines = [head]
    for entry in sessions:
        facts = ", ".join(part for part in (entry["agent"], entry["device"]) if part)
        new = "" if entry["listed"] or not reading["ranked"] else "   (new, not placed yet)"
        lines.append(f"  {entry['place']}. {entry['title']}{' (' + facts + ')' if facts else ''}{new}\n     {entry['session']}")
    if reading["ranked"]:
        lines.append("Move one: cairn vibe rank \"<session>\" --to top | up | down | bottom | <number>")
    return "\n".join(lines)


# ------------------------------------------------------------ what an assistant is told


def _named(entry: dict) -> str:
    return f"{entry['place']}. {entry['title']}" + (f" (on {entry['device']})" if entry["device"] and not entry["here"] else "")


def notice(brain: Brain, session: str = "", now: dt.datetime | None = None) -> str:
    """What the assistant writing to `session` has to know about its place, "" until the user has put the
    running sessions in order (and for a session that is not running)."""
    wanted = _name(session)
    if not wanted or not listed(brain):  # no order yet: nothing is read beyond that one small file
        return ""
    reading = status(brain, now)
    mine = next((entry for entry in reading["sessions"] if entry["session"] == wanted), None)
    if not reading["ranked"] or mine is None:
        return ""
    total = len(reading["sessions"])
    if total == 1:
        return ""
    lines = []
    if mine["place"] == 1:
        lines.append(f"PRIORITY (set in Cairn): this chat is number 1 of {total} running sessions, the most important one.\n"
                     "  Where chats need the same thing, the others give way to this one.")
    else:
        before = reading["sessions"][: mine["place"] - 1]
        named = "; ".join(_named(entry) for entry in before[:NAMED_BEFORE])
        more = f"; and {len(before) - NAMED_BEFORE} more" if len(before) > NAMED_BEFORE else ""
        new = "" if mine["listed"] else " It is new and not placed yet, so it comes last for now."
        lines.append(f"PRIORITY (set in Cairn): this chat is number {mine['place']} of {total} running sessions.{new}\n"
                     f"  Before it: {named}{more}.\n"
                     "  Where chats need the same thing (the user's answer, a repo or a branch, this computer's processor,\n"
                     "  memory or disk, build minutes) the lower number goes first: give way to it, and log it when you wait.")
    lines.append("  The order is the user's. Change it only when they say so (cairn vibe rank).")
    return "\n".join(lines)


# ------------------------------------------------------------ changing it


def _find(sessions: list[dict], wanted: str) -> dict:
    """The running session `wanted` means: its name, or the one whose name or goal holds those words."""
    key = " ".join(_name(wanted).split()).lower()
    if not key:
        raise RankError("Which session? Give its name, or a few words of it.")
    exact = next((entry for entry in sessions if entry["session"].lower() == key), None)
    if exact:
        return exact
    matches = [entry for entry in sessions if key in entry["session"].lower() or key in entry["title"].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RankError(f'No running session fits "{wanted}". Running now: cairn vibe rank')
    raise RankError(f'"{wanted}" fits more than one running session:\n' + "\n".join(f"  {entry['session']}" for entry in matches[:12]))


def _target(to: str, at: int, count: int) -> int:
    """The index (from 0) the session at index `at` goes to."""
    word = str(to or "").strip().lower()
    if word in ("top", "first"):
        return 0
    if word in ("bottom", "last"):
        return count - 1
    if word == "up":
        return max(0, at - 1)
    if word == "down":
        return min(count - 1, at + 1)
    if word.lstrip("#").isdigit() and int(word.lstrip("#")) >= 1:
        return min(count - 1, int(word.lstrip("#")) - 1)
    raise RankError("Where to? --to top, up, down, bottom, or its number (1 is the most important).")


def _write(brain: Brain, names: list[str]) -> None:
    text = file_text(names)
    for _ in range(3):  # another device or the page may be saving this second: the newest choice wins
        current = file(brain)
        try:
            brain.write(FILE, text, current["mtime"] if current else None)
            return
        except ConflictError:
            continue
    raise RankError("The order is being written right now. Try again in a moment.")


def set_order(brain: Brain, names: list[str], now: dt.datetime | None = None) -> dict:
    """Write `names` as the order. Only running sessions are kept; the running ones it leaves out follow
    in the order they had. Returns the new `status`."""
    reading = status(brain, now)
    live = [entry["session"] for entry in reading["sessions"]]
    wanted = [name for name in dict.fromkeys(_name(name) for name in names) if name in live]
    _write(brain, wanted + [name for name in live if name not in wanted])
    return status(brain, now)


def move(brain: Brain, wanted: str, to: str, now: dt.datetime | None = None) -> dict:
    """Move one running session: to the top, one place up or down, to the bottom, or to a number. The
    whole order is written, so from then on every running session has its place. Returns the new
    `status` with the moved `session` and a `message`."""
    reading = status(brain, now)
    entry = _find(reading["sessions"], wanted)
    names = [other["session"] for other in reading["sessions"]]
    at = names.index(entry["session"])
    names.insert(_target(to, at, len(names)), names.pop(at))
    _write(brain, names)
    after = status(brain, now)
    place = next(other["place"] for other in after["sessions"] if other["session"] == entry["session"])
    count = len(after["sessions"])
    what = "the most important one" if place == 1 else f"number {place} of {count}"
    after["session"] = entry["session"]
    after["message"] = f"{entry['title']} is {what} now. A chat that logs its steps is told its place at its next one."
    return after


def reset(brain: Brain) -> dict:
    """Forget the order: the sessions are listed as they started again and no assistant is told a place."""
    if file(brain) is not None:
        _write(brain, [])
    after = status(brain)
    after["message"] = "The order is forgotten. The running sessions are listed in the order they started."
    return after

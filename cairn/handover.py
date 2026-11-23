"""Hand over: let another computer do the work of this computer's chats, for a while.

Two switches, both made in one place (the Vibe page, or `cairn vibe hand`) and both as easily made back:

- all of them: while it is on, an assistant working on this computer does not do the work itself. It leaves
  the work for the other computer as a handoff, follows the answers and checks the result. It still does
  what only this computer can do.
- one chat: its session is handed over now. Cairn writes the handoff itself, from the session's goal and
  log, and marks the session; the chat is told at its next step and adds what a log cannot show.

What moves is the work, not the conversation: an AI app keeps its chats to itself, so the other computer
gets the goal, the log and what is left, and a chat there carries it on.

How a chat learns of it: `notice` is printed by `cairn vibe start`, `log` and `check`, and by the memory
skill's `recall.py rules`, which a chat reads when it starts. Nothing reaches into the AI app.

The switch for all chats is a setting of this computer (handover.json in Cairn's own folder, next to
connect.json); a handed session says so in its own note (`handed_to`, `handed_at`, `handed_thread`), which
this computer owns and the brain's sync carries, so the other devices show it too.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path

from . import connect, core, handoff, vibe
from .brain import Brain, ConflictError

HERE_WORDS = ("", "here", "back", "off", "this", "this computer", "self", "me")
LOG_LINES = 40  # the end of a session's log that goes into the handoff
SESSION_KEYS = ("handed_to", "handed_at", "handed_thread", "handed_agent")
# The AI apps a piece of work can be given to. The ones in STARTS_ITSELF run with nobody at the computer, so
# Cairn there starts them (cairn/runner.py); the others wait until a chat in that app picks the handoff up.
AGENTS = {"vector": "Vector", "opencode": "OpenCode"}
STARTS_ITSELF = ("opencode",)
PHONE_AGENT = "opencode"  # what carries out a prompt from the phone until another one is chosen
AGENT_LINE = re.compile(r"^Agent:[ \t]*([a-z]+)[ \t\r]*$", re.M)  # the line of a handoff that names the app asked for
SOON_TTL_SECONDS = 10  # the live page shows a switch made elsewhere (a terminal, an assistant) this soon

HEADER_CHARS = 2000  # a session note's properties are at its top: enough to look for one without parsing it
_MACHINE_LINE = re.compile(r"^machine:[ \t]*(.+?)[ \t\r]*$", re.M)

_soon: dict[str, tuple[float, dict]] = {}
_soon_lock = threading.Lock()


class HandoverError(ValueError):
    """A hand-over that was not made, with a sentence a person can act on."""


# ------------------------------------------------------------ the setting of this computer


def _file(folder: Path | None = None) -> Path:
    return (folder or connect.cairn_home()) / "handover.json"


def _read(folder: Path | None = None) -> dict:
    try:
        data = json.loads(_file(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(settings: dict, folder: Path | None = None) -> None:
    path = _file(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _setting(brain: Brain, folder: Path | None = None) -> dict:
    entry = (_read(folder).get("brains") or {}).get(str(brain.dir))
    return entry if isinstance(entry, dict) and entry.get("to") else {}


def _stamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y-%m-%dT%H:%M")


def _when(stamp: str) -> str:
    return str(stamp or "").replace("T", " ")


# ------------------------------------------------------------ which AI app


def agent_id(wanted: str) -> str:
    """The app `wanted` means ("" for none): its id, its name, or the name without its spaces."""
    key = "".join(str(wanted or "").lower().replace("-", " ").split())
    if not key:
        return ""
    for known, name in AGENTS.items():
        if key in (known, "".join(name.lower().split())):
            return known
    raise HandoverError(f'No AI app "{wanted}". Known: {", ".join(AGENTS.values())}.')


def agents() -> list[dict]:
    return [{"id": known, "name": name, "starts": known in STARTS_ITSELF} for known, name in AGENTS.items()]


def agent_asked(text: str) -> str:
    """The app a handoff asks for ("" when it names none, or one this Cairn does not know)."""
    found = AGENT_LINE.search(str(text or ""))
    return found.group(1) if found and found.group(1) in AGENTS else ""


def agent_lines(agent: str, to: str) -> list[str]:
    """What a handoff says about the app that was asked for. The first line is the one Cairn reads."""
    if not agent:
        return []
    name = AGENTS[agent]
    how = (f"Asked for in Cairn: {name} does it. Cairn on {to} starts it by itself, with nobody at the computer, and answers here."
           if agent in STARTS_ITSELF else
           f"Asked for in Cairn: a {name} chat does it. Nothing can start {name} from outside, so it waits until a {name} chat on {to} picks it up.")
    return ["", f"Agent: {agent}", how]


# ------------------------------------------------------------ prompts from the phone


def phone(brain: Brain, folder: Path | None = None) -> dict:
    """Where a prompt sent from the phone to a finished chat of this computer is carried out, and by which
    app: {"to": "" for this computer, "agent"}. Read by the phone desk of Vector Relay (`cairn vibe hand phone`)."""
    entry = (_read(folder).get("phone") or {}).get(str(brain.dir))
    entry = entry if isinstance(entry, dict) else {}
    agent = entry.get("agent") if entry.get("agent") in STARTS_ITSELF else PHONE_AGENT
    return {"to": str(entry.get("to") or ""), "agent": agent}


def set_phone(brain: Brain, to: str | None = None, agent: str | None = None, folder: Path | None = None) -> dict:
    """Choose the computer (`to`, "here" for this one) and/or the app for prompts from the phone. Returns the
    new `status` with a `message`."""
    now = phone(brain, folder)
    if to is not None:
        now["to"] = _resolve(brain, to)
    if agent is not None and str(agent).strip():
        wanted = agent_id(agent)
        if wanted not in STARTS_ITSELF:
            names = " or ".join(AGENTS[known] for known in STARTS_ITSELF)
            raise HandoverError(f"A prompt from the phone needs an app that starts with nobody at the computer: {names}. "
                                f"{AGENTS[wanted]} cannot be started from outside.")
        now["agent"] = wanted
    settings = _read(folder)
    phones = dict(settings.get("phone") or {})
    phones[str(brain.dir)] = now
    settings["phone"] = phones
    _write(settings, folder)
    _forget_soon(brain)
    reading = status(brain, folder)
    reading["message"] = (f"A prompt from the phone to a finished chat of this computer is carried out on "
                          f"{now['to'] or 'this computer'}, by {AGENTS[now['agent']]}.")
    return reading


# ------------------------------------------------------------ who there is


def others(brain: Brain) -> list[str]:
    """The other computers of this brain: the ones work can be handed to."""
    here = core.device_key(handoff.this_device())
    return [name for name in handoff.known_devices(brain) if core.device_key(name) != here]


def _resolve(brain: Brain, wanted: str) -> str:
    """The computer `wanted` means, "" for this one (or one of the words for it)."""
    key = core.device_key(wanted)
    if key in HERE_WORDS or key == core.device_key(handoff.this_device()):
        return ""
    if key == core.HANDOFF_EVERYONE:
        raise HandoverError("Work is handed to one computer. Name it.")
    try:
        return handoff.resolve_device(others(brain), wanted)
    except handoff.HandoffError as error:
        raise HandoverError(str(error).replace(' Use "all" for every computer.', "")) from None


def _handed(note: dict) -> dict | None:
    to = note["meta"].get("handed_to")
    if not isinstance(to, str) or not to.strip():
        return None
    return {
        "session": note["slug"],
        "path": note["path"],
        "title": note["title"],
        "live": note["status"] == "live",
        "to": to.strip(),
        "at": str(note["meta"].get("handed_at", "")),
        "thread": str(note["meta"].get("handed_thread", "")),
        "agent": str(note["meta"].get("handed_agent", "")),
    }


def _find_session(brain: Brain, wanted: str) -> dict:
    """The session `wanted` means: its name, or the one session of this computer whose name or goal holds
    those words. A live one wins over ended ones."""
    key = " ".join(str(wanted or "").split()).lower().removesuffix(".md").rsplit("/", 1)[-1]
    if not key:
        raise HandoverError("Which session? Give its name, or a few words of it.")
    every = [note for note in vibe._notes(brain) if note["type"] == "session"]
    exact = next((note for note in every if note["slug"].lower() == key), None)
    if exact is None:
        mine = [note for note in every if not vibe.elsewhere(note)]
        matches = [note for note in mine if key in note["slug"].lower() or key in note["title"].lower()]
        live = [note for note in matches if note["status"] == "live"]
        if len(live) == 1:
            exact = live[0]
        elif len(matches) == 1:
            exact = matches[0]
        elif not matches:
            raise HandoverError(f'No session of this computer fits "{wanted}".')
        else:
            shown = live or matches
            raise HandoverError(f'"{wanted}" fits more than one session:\n' + "\n".join(f"  {note['slug']}" for note in shown[:12]))
    if vibe.elsewhere(exact):
        owner = str(exact["meta"].get("machine", "")).strip()
        raise HandoverError(f"That session runs on {owner}. Hand it over from there.")
    return exact


# ------------------------------------------------------------ reading


def _glance(brain: Brain, here: str) -> tuple[list[str], list[dict]]:
    """(the other computers that have a session note, this computer's handed sessions), from the session
    notes the live page holds anyway. A brain can hold thousands of them and the page asks every few
    seconds, so a note is parsed only when its top says it was handed over; a computer's name is read
    off its `machine:` line."""
    folder = core.FOLDERS["session"] + "/"
    found: dict[str, str] = {}
    handed: list[dict] = []
    for file in vibe.vibe_files(brain):
        if not file["path"].startswith(folder):
            continue
        top = file["text"][:HEADER_CHARS]
        line = _MACHINE_LINE.search(top)
        name = " ".join(line.group(1).strip("\"'").split()) if line else ""
        key = core.device_key(name)
        if key and key != core.device_key(here):
            found.setdefault(key, name)
        elif "handed_to:" in top:
            note = core.note_from_file(file["path"], file["text"], file["mtime"])
            if note["type"] == "session" and not vibe.elsewhere(note):
                handed.append(note)
    handed.sort(key=lambda note: (str(note["meta"].get("started", "")), note["path"]))
    return sorted(found.values(), key=str.lower), handed


def status(brain: Brain, folder: Path | None = None) -> dict:
    """Where the work of this computer's chats goes. `to` is "" while it is done here; `others` are the
    computers it can go to; `sessions` are the sessions handed over one by one that are still live or were
    handed in the last day."""
    setting = _setting(brain, folder)
    here = handoff.this_device()
    names, mine = _glance(brain, here)
    recent = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    handed = [entry for entry in map(_handed, mine) if entry and (entry["live"] or entry["at"] >= recent)]
    if setting and core.device_key(str(setting["to"])) not in map(core.device_key, names):
        names = sorted([*names, str(setting["to"])], key=str.lower)
    return {
        "device": here,
        "to": str(setting.get("to", "")),
        "since": str(setting.get("since", "")),
        "others": names,
        "sessions": handed,
        "agents": agents(),
        "phone": phone(brain, folder),
    }


def status_soon(brain: Brain) -> dict:
    """`status` for the live page, which asks every few seconds: the last reading for ten seconds."""
    key = str(brain.dir)
    with _soon_lock:
        kept = _soon.get(key)
        if kept and time.monotonic() - kept[0] < SOON_TTL_SECONDS:
            return kept[1]
    reading = status(brain)
    with _soon_lock:
        _soon[key] = (time.monotonic(), reading)
    return reading


def _forget_soon(brain: Brain) -> None:
    with _soon_lock:
        _soon.pop(str(brain.dir), None)


def summary(reading: dict) -> str:
    lines = []
    if reading["to"]:
        lines.append(f"The work of this computer's chats goes to {reading['to']} (since {_when(reading['since'])}). Back: cairn vibe hand here")
    elif reading["others"]:
        lines.append("The work of this computer's chats is done here. To hand it over: cairn vibe hand \"<computer>\"   (" + ", ".join(reading["others"]) + ")")
    else:
        lines.append("The work of this computer's chats is done here. No other computer has written into this brain yet, so there is nobody to hand it to.")
    if reading.get("phone"):
        route = reading["phone"]
        lines.append(f"A prompt from the phone to a finished chat is carried out on {route['to'] or 'this computer'}, by {AGENTS[route['agent']]}. "
                     "Change: cairn vibe hand phone \"<computer>\" --agent NAME")
    for entry in reading["sessions"]:
        lines.append(f"  handed to {entry['to']}{' (' + AGENTS[entry['agent']] + ')' if entry['agent'] in AGENTS else ''} {_when(entry['at'])}: {entry['title']}\n    {entry['session']}   (back: cairn vibe hand here --session {entry['session']})")
    return "\n".join(lines)


def notice(brain: Brain, session: str = "", folder: Path | None = None) -> str:
    """What an assistant on this computer has to know before it goes on, "" when the work is done here.
    `session` is the session it writes to; one that was handed over is told so first."""
    parts = []
    wanted = str(session or "").strip().removesuffix(".md").rsplit("/", 1)[-1]
    if wanted:
        entry = None
        try:
            path = f"{core.FOLDERS['session']}/{wanted}.md"
            note = core.note_from_file(path, brain.read(path)["text"])
            entry = None if vibe.elsewhere(note) else _handed(note)
        except (OSError, ValueError):
            pass  # no such note: nothing to say about it
        if entry:
            thread = entry["thread"] or "its handoff"
            parts.append(
                f"THIS CHAT'S WORK WAS HANDED TO {entry['to']} ({_when(entry['at'])}, in Cairn). Stop doing it here.\n"
                f"  Add what the log does not show (a branch that is not pushed, files only on this computer, what is half done):\n"
                f"    cairn handoff reply \"{thread}\" \"...\"\n"
                f"  Then follow its answers (cairn handoff show \"{thread}\") and check the result.\n"
                f"  Taken back in Cairn (Vibe), or: cairn vibe hand here --session {entry['session']}"
            )
    setting = _setting(brain, folder)
    if setting and not parts:
        to = str(setting["to"])
        parts.append(
            f"HAND-OVER IS ON (set in Cairn, {_when(setting.get('since', ''))}): the work of this computer's chats goes to {to}.\n"
            f"  Do not do the work here. Leave it for {to} as a handoff that says what done looks like, with the numbers\n"
            f"  to report, and what must not be done without the user:\n"
            f"    cairn handoff send \"title\" --to \"{to}\" --file details.md\n"
            f"  Then follow its answers (cairn handoff) and check the result. Do here only what needs this computer:\n"
            f"  its files, its apps, a sign-in only it has, or a step that cannot wait.\n"
            f"  Switched back in Cairn (Vibe), or: cairn vibe hand here"
        )
    return "\n".join(parts)


# ------------------------------------------------------------ all chats


def switch(brain: Brain, to: str, folder: Path | None = None, now: dt.datetime | None = None) -> dict:
    """Hand the work of every chat on this computer to `to`, or take it back ("here"). Returns the new
    `status` with a `message`. Sessions handed over one by one are left as they are."""
    name = _resolve(brain, to)
    settings = _read(folder)
    brains = dict(settings.get("brains") or {})
    before = _setting(brain, folder)
    if name:
        if core.device_key(str(before.get("to", ""))) != core.device_key(name):
            brains[str(brain.dir)] = {"to": name, "since": _stamp(now)}
        message = (f"The work of this computer's chats goes to {name} from now on. Each chat is told at its next step, "
                   "and a new chat when it starts. What is running is not interrupted.")
    else:
        brains.pop(str(brain.dir), None)
        message = ("The work of this computer's chats is done here again." if before
                   else "The work of this computer's chats is done here.")
    settings["brains"] = brains
    _write(settings, folder)
    _forget_soon(brain)
    reading = status(brain, folder)
    reading["message"] = message
    return reading


# ------------------------------------------------------------ one chat


def _log_tail(note: dict) -> list[str]:
    lines = [line.rstrip() for line in note["body"].split("\n") if line.lstrip().startswith("- ")]
    return lines[-LOG_LINES:]


def _handoff_text(note: dict, here: str, to: str, stamp: str, agent: str = "") -> tuple[str, str]:
    title = f"Take over: {note['title']}"
    meta = note["meta"]
    facts = [f"Session note: {note['path']}"]
    if meta.get("started"):
        facts.append(f"started {_when(str(meta['started']))}")
    if meta.get("agent"):
        facts.append(f"agent {meta['agent']}")
    lines = [
        f"{here} handed the work of this chat to {to} in Cairn on {_when(stamp)}. Carry it on from where the log ends. "
        f"The chat on {here} stops doing it and follows this conversation.",
        "",
        f"Goal: {note['title']}",
        ", ".join(facts),
        *agent_lines(agent, to),
    ]
    if meta.get("task"):
        lines.append(f"Task: {meta['task']}")
    tail = _log_tail(note)
    if tail:
        lines += ["", f"Its log so far{' (the last ' + str(LOG_LINES) + ' steps)' if len(tail) == LOG_LINES else ''}:"]
        lines += tail
    lines += [
        "",
        f"A log does not show everything. What is only on {here} (a branch that is not pushed, a half-made change, a file) "
        "the chat there adds below at its next step: ask here when something is missing before guessing.",
        "Say \"picked up\" when you start and \"done\" with the result and its numbers. What needs the user's own word "
        "(mail to other people, payments, final submits, deleting work, publishing) still needs it.",
    ]
    return title, "\n".join(lines)


def _mark(brain: Brain, path: str, changes: dict | None, line: str) -> None:
    """Write the hand-over into the session's own note (or take it out again, with `changes` None)."""
    for _ in range(3):  # its chat may be writing a step this second
        current = brain.read(path)
        body = core.parse_note(current["text"])["body"].rstrip() + f"\n- {vibe._clock()} note: {line}\n"
        if changes is None:
            text = vibe._drop_properties(core.update_note_text(current["text"], {}, body), SESSION_KEYS)
        else:
            text = core.update_note_text(current["text"], changes, body)
        try:
            brain.write(path, text, current["mtime"])
            return
        except ConflictError:
            continue
        finally:
            _forget_soon(brain)
    raise HandoverError("The session's note is being written right now. Try again in a moment.")


def hand_session(brain: Brain, wanted: str, to: str = "", folder: Path | None = None, now: dt.datetime | None = None,
                 agent: str = "") -> dict:
    """Hand one session of this computer to `to` (default: where all chats go, else the one other
    computer), to be done there by `agent` (an AI app; none: whichever chat picks it up). Leaves the
    handoff and marks the session. Returns {"session", "to", "agent", "thread", "handoff", "message"}; the
    caller sends the brain on its way (handoff.deliver)."""
    agent = agent_id(agent)
    note = _find_session(brain, wanted)
    already = _handed(note)
    if already:
        raise HandoverError(f"That session was handed to {already['to']} {_when(already['at'])}. Take it back first: cairn vibe hand here --session {note['slug']}")
    name = _resolve(brain, to) if str(to or "").strip() else ""
    if not name:
        candidates = others(brain)
        name = str(_setting(brain, folder).get("to", "")) or (candidates[0] if len(candidates) == 1 else "")
    if not name:
        known = ", ".join(others(brain)) or "none yet"
        raise HandoverError(f"Hand it to which computer? Known: {known}.")
    here = handoff.this_device()
    stamp = _stamp(now)
    title, body = _handoff_text(note, here, name, stamp, agent)
    try:
        message = handoff.send(brain, title, body, to=[name], now=now)
    except handoff.HandoffError as error:
        raise HandoverError(str(error)) from None
    thread = message["slug"]
    marks = {"handed_to": name, "handed_at": stamp, "handed_thread": thread, **({"handed_agent": agent} if agent else {})}
    who = f"{name}, {AGENTS[agent]}" if agent else name
    _mark(brain, note["path"], marks, f"handed to {who} (handoff {thread})")
    if agent in STARTS_ITSELF:
        then = f" Cairn on {name} starts {AGENTS[agent]} on it by itself."
    elif agent:
        then = f" It waits there until a {AGENTS[agent]} chat picks it up: {AGENTS[agent]} cannot be started from outside."
    else:
        then = ""
    return {
        "session": note["slug"],
        "path": note["path"],
        "to": name,
        "agent": agent,
        "thread": thread,
        "handoff": message["path"],
        "message": f"Handed to {who}: {note['title']}. Its chat here is told at its next step.{then}",
    }


def take_back(brain: Brain, wanted: str, now: dt.datetime | None = None) -> dict:
    """Take one handed session back to this computer. A conversation the other computer has not finished
    is closed, so it stops; one it has finished is left as it ended."""
    note = _find_session(brain, wanted)
    entry = _handed(note)
    if not entry:
        raise HandoverError("That session is not handed over: its work is done here.")
    closed = False
    thread = next((t for t in handoff.threads(brain) if t["id"] == entry["thread"]), None) if entry["thread"] else None
    if thread and thread["open"]:
        text = (f"Taken back by {handoff.this_device()}\n\nThis work goes on there again. Stop here. If something is half done "
                "(a branch, a change that is not pushed), say so in a new message before leaving it.")
        try:
            handoff.answer(brain, thread["id"], text, says="closed", now=now)
            closed = True
        except handoff.HandoffError:
            pass  # the note below still says it; the other computer reads the session too
    _mark(brain, note["path"], None, f"taken back from {entry['to']}")
    return {
        "session": note["slug"],
        "path": note["path"],
        "to": "",
        "thread": entry["thread"],
        "closed": closed,
        "message": f"Taken back from {entry['to']}: {note['title']}." + (" Its conversation is closed, so that computer stops." if closed else ""),
    }

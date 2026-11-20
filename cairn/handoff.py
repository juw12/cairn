"""Handoffs: a conversation between the computers that share one brain.

A computer leaves a message for one, several or all of the others: what it found, what is left hanging,
what only another computer can do. The others say "picked up", answer, and say "done". The rules of a
conversation are in core.py (shared with the web app); this module reads and writes the files and finds
out which computer this is.

Every message is its own file in handoffs/, written once and never edited, so two computers never write
the same file and the brain's git remote carries the conversation without anything to merge.
"""

from __future__ import annotations

import datetime as dt

from . import core, machine
from .brain import Brain


class HandoffError(ValueError):
    pass


TITLE_LIMIT = 80
DEFAULT_TITLES = {"picked-up": "Picked up", "done": "Done"}


def this_device() -> str:
    """What this computer is called in the brain (the name on its session notes; $CAIRN_DEVICE changes it)."""
    return machine.device_name()


def _stamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")


def _when(stamp: str) -> str:
    """A message's time as people read it: to the minute."""
    return stamp.replace("T", " ")[:16]


def threads(brain: Brain) -> list[dict]:
    return core.handoff_threads(brain.notes())


def known_devices(brain: Brain) -> list[str]:
    """Every computer this brain has heard of: the ones on session notes, the ones with a machines/ note
    and the ones a handoff names. One spelling each, sorted by name."""
    found: dict[str, str] = {}

    def add(name) -> None:
        name = " ".join(str(name or "").split())
        key = core.device_key(name)
        if key and key != core.HANDOFF_EVERYONE:
            found.setdefault(key, name)

    for note in brain.notes():
        meta = note["meta"]
        if note["type"] == "session":
            add(meta.get("machine") if isinstance(meta.get("machine"), str) else "")
        elif isinstance(meta.get("machine_at"), str) and meta.get("machine_at"):
            add(note["title"])
        elif core.is_handoff(note):
            add(meta.get("from") if isinstance(meta.get("from"), str) else "")
            for name in meta.get("to") if isinstance(meta.get("to"), list) else [meta.get("to")]:
                add(name if isinstance(name, str) else "")
    return sorted(found.values(), key=str.lower)


def resolve_device(names: list[str], wanted: str) -> str:
    """The computer `wanted` means: its exact name, or the one known name that holds those words
    ("asus" for "ASUS EXPERTBOOK P5405CSA"). "all" is every computer."""
    key = core.device_key(wanted)
    if not key:
        raise HandoffError("Which computer is it for?")
    if key in (core.HANDOFF_EVERYONE, "everyone", "every computer"):
        return core.HANDOFF_EVERYONE
    for name in names:
        if core.device_key(name) == key:
            return name
    matches = [name for name in names if key in core.device_key(name)]
    if len(matches) == 1:
        return matches[0]
    listed = ", ".join(f'"{name}"' for name in names) or "none yet"
    if matches:
        raise HandoffError(f'"{wanted}" fits more than one computer: ' + ", ".join(f'"{name}"' for name in matches) + ".")
    raise HandoffError(f'No computer called "{wanted}" has written into this brain. Known: {listed}. Use "all" for every computer.')


def find_thread(all_threads: list[dict], wanted: str) -> dict:
    """The conversation `wanted` means: its id, or the one whose id or title holds those words."""
    key = " ".join(str(wanted or "").split()).lower()
    if not key:
        raise HandoffError("Which conversation? Give its name, or a few words of it.")
    for thread in all_threads:
        if thread["id"].lower() == key:
            return thread
    matches = [t for t in all_threads if key in t["id"].lower() or key in t["title"].lower()]
    open_matches = [t for t in matches if t["open"]]
    if len(open_matches) == 1:
        return open_matches[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise HandoffError(f'No conversation fits "{wanted}". `cairn handoff --all` lists them.')
    raise HandoffError(f'"{wanted}" fits more than one conversation:\n' + "\n".join(f"  {t['id']}" for t in matches))


def _title_and_body(text: str, fallback: str) -> tuple[str, str]:
    """A message's title is its first line; a longer text is also its body."""
    text = str(text or "").strip()
    first = " ".join(text.split("\n", 1)[0].split())
    if not first:
        return fallback, ""
    title = first if len(first) <= TITLE_LIMIT else first[: TITLE_LIMIT - 3].rstrip() + "..."
    return title, "" if text == title else text


def _write(brain: Brain, message: dict) -> dict:
    brain.write(message["path"], message["text"], None)
    return core.note_from_file(message["path"], message["text"])


def send(brain: Brain, title: str, body: str = "", to: list[str] | None = None,
         sender: str = "", now: dt.datetime | None = None) -> dict:
    """Start a conversation: leave a message for the computers in `to` (none or "all": every computer)."""
    title = " ".join(str(title or "").split())
    if not title:
        raise HandoffError("What is the message? Give it a title.")
    sender = " ".join(str(sender).split()) or this_device()
    names = known_devices(brain)
    recipients = [resolve_device(names, name) for name in (to or [])]
    if core.HANDOFF_EVERYONE in recipients:
        recipients = []
    if recipients and all(core.device_key(name) == core.device_key(sender) for name in recipients):
        raise HandoffError("That is this computer itself. Name another computer, or \"all\".")
    existing = [f["path"] for f in brain.files()]
    return _write(brain, core.handoff_message(sender, title, body, _stamp(now), existing, to=recipients))


def answer(brain: Brain, wanted: str, text: str = "", says: str = "note",
           sender: str = "", now: dt.datetime | None = None) -> dict:
    """Add to a conversation: a note, "picked-up" (this computer has taken it on) or "done"."""
    if says not in core.HANDOFF_SAYS:
        raise HandoffError(f"A message says one of: {', '.join(core.HANDOFF_SAYS)}.")
    thread = find_thread(threads(brain), wanted)
    title, body = _title_and_body(text, DEFAULT_TITLES.get(says, ""))
    if not title:
        raise HandoffError("What do you want to say?")
    sender = " ".join(str(sender).split()) or this_device()
    existing = [f["path"] for f in brain.files()]
    return _write(brain, core.handoff_message(sender, title, body, _stamp(now), existing, thread=thread["id"], says=says))


def body_of(brain: Brain, path: str) -> str:
    return core.parse_note(brain.read(path)["text"])["body"].strip()


# ------------------------------------------------------------ what the command line prints

STATE_WORDS = {"waiting": "has not picked it up", "picked-up": "picked it up", "done": "done"}


def _for_whom(thread: dict) -> str:
    return "every computer" if thread["everyone"] else ", ".join(thread["to"]) or "itself"


def thread_line(thread: dict) -> list[str]:
    parts = "; ".join(f"{part['device']} {STATE_WORDS[part['state']]}" for part in thread["parts"])
    count = len(thread["messages"])
    lines = [f"  {thread['title']}"]
    lines.append(f"    from {thread['from'] or 'someone'} to {_for_whom(thread)} · {_when(thread['messages'][0]['at'])} · {count} message{'' if count == 1 else 's'}")
    if parts:
        lines.append(f"    {parts}")
    lines.append(f"    {thread['id']}")
    return lines


def overview(brain: Brain, device: str = "", everything: bool = False) -> str:
    """What `cairn handoff` prints: what waits for this computer, what it is waiting for, and with
    `everything` the conversations between other computers and the settled ones."""
    device = " ".join(str(device).split()) or this_device()
    all_threads = threads(brain)
    by_id = {thread["id"]: thread for thread in all_threads}
    inbox = core.handoff_inbox(all_threads, device)
    sections = [
        ("mine", f"For this computer ({device})", "Nothing is waiting for this computer."),
        ("asked", "This computer is waiting for", ""),
    ]
    if everything:
        sections += [("others", "Between other computers", ""), ("settled", "Settled", "")]
    out: list[str] = []
    for key, heading, empty in sections:
        ids = inbox[key]
        if not ids and not empty:
            continue
        out.append(f"{heading}:" if ids else empty)
        for thread_id in ids:
            thread = by_id[thread_id]
            lines = thread_line(thread)
            if key == "mine" and core.handoff_waits_for(thread, device) == "picked-up":
                lines[0] += "   (picked up, not done yet)"
            out.extend(lines)
        out.append("")
    if inbox["mine"]:
        out.append('Read one: cairn handoff show "<a few words of its name>"   then: cairn handoff pickup ... / done ... / reply ... "text"')
    elif not everything and (inbox["others"] or inbox["settled"]):
        out.append(f"{len(inbox['others'])} between other computers, {len(inbox['settled'])} settled: cairn handoff --all")
    return "\n".join(out).rstrip() + "\n"


def transcript(brain: Brain, thread: dict) -> str:
    """One conversation in full, oldest message first."""
    out = [thread["title"], f"from {thread['from'] or 'someone'} to {_for_whom(thread)} · {'open' if thread['open'] else 'settled'}", f"{thread['id']}", ""]
    for message in thread["messages"]:
        mark = {"picked-up": " [picked up]", "done": " [done]"}.get(message["says"], "")
        out.append(f"--- {message['from'] or 'someone'} · {_when(message['at'])}{mark}")
        if message["path"] != thread["path"] or message["title"] != thread["title"]:
            out.append(message["title"])
        body = body_of(brain, message["path"])
        if body:
            out.append(body)
        out.append("")
    for part in thread["parts"]:
        out.append(f"{part['device']}: {STATE_WORDS[part['state']]}")
    return "\n".join(out).rstrip() + "\n"

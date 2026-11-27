"""A chat is on the Vibe page from its first step, without being asked.

Cairn does not read an assistant's chats: they are the app's own files. What it can go by is what a chat
does in the open. A chat that has this brain as its memory runs the memory skill, first of all when it
starts. So the skill checks the chat in every time it runs (`cairn connect` puts the skill there):

* the first command opens a live session note for the chat, before the chat has said what it is doing;
* every later command is a sign of life (`turn_at`), written at most once in BEAT_MINUTES;
* what the chat saves (a note, a decision, a message for another computer, a journal line) is logged as a
  step of that session by itself;
* the chat names its work, logs its steps and says how it ended with the skill's own commands
  (`remember.py begin`, `step`, `finish`).

Which chat is calling is told by the folder the command runs in: an AI app starts a chat's commands in the
chat's own working folder. No file of the app is opened for it. Two chats that share one project folder
share one session. Which session belongs to which folder is one small file next to the watcher's state; it
never leaves this computer.

Only the one note of the chat is read and written here, so a command of the skill costs the same in a brain
with ten session notes as in one with ten thousand.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path

from . import core, machine, vibe
from .brain import Brain, ConflictError

BEAT_MINUTES = 5  # a sign of life is written this often at most, so a busy chat is not one commit a command
REST_MINUTES = 10  # after a chat said how it ended, its closing commands (a record, a handoff) open nothing new
REOPEN_HOURS = 12  # a session ended for silence longer ago than this is not gone on with: a new one is opened
ADOPT_MINUTES = 15  # `cairn vibe start` run elsewhere names a chat's nameless session that is this young
KEEP_DAYS = 30  # a folder no chat has used for this long is forgotten
STATE_FILE = "cairn-chats.json"
UNNAMED = "unnamed"  # on the note while the chat has not said what it is doing
RESULTS = (*vibe.RESULTS, "done")  # done: it answered, nothing was built
TITLE_CHARS = 160
STEP_CHARS = 300
LOCK_WAIT_SECONDS = 3
LOCK_STALE_SECONDS = 30
CONTINUED = ", continued"


class ChatError(ValueError):
    """What was asked cannot be written; the message says why."""


def _now() -> dt.datetime:
    return dt.datetime.now()


def _stamp(now: dt.datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M")


def _one_line(text, limit: int) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------- which chat


def _folder(folder) -> Path:
    try:
        return Path(folder).resolve()
    except OSError:
        return Path(folder)


def chat_key(folder) -> str:
    """The chat's name on its note: a short digest of its working folder, the same on every call."""
    return "c" + hashlib.sha1(os.path.normcase(str(_folder(folder))).encode("utf-8")).hexdigest()[:9]


def _shown(folder) -> str:
    """The folder as the note says it: under the home folder as ~/..., so no user name is written."""
    path = _folder(folder)
    try:
        return "~/" + path.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------- the small file that remembers


def _state_path(brain: Brain) -> Path:
    git_dir = brain.root / ".git"
    return (git_dir if git_dir.is_dir() else brain.dir) / STATE_FILE


def _load(brain: Brain) -> dict:
    try:
        state = json.loads(_state_path(brain).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save(brain: Brain, state: dict, now: dt.datetime) -> None:
    oldest = (now - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M")
    kept = {key: entry for key, entry in state.items() if isinstance(entry, dict) and str(entry.get("seen", "")) >= oldest}
    path = _state_path(brain)
    try:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(kept, indent=1), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


@contextlib.contextmanager
def _locked(brain: Brain):
    """One command of a chat at a time: two that start together must not open two notes."""
    lock = _state_path(brain).with_name(STATE_FILE + ".lock")
    waited_until = time.monotonic() + LOCK_WAIT_SECONDS
    held = False
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            held = True
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.unlink()
                    continue
            except OSError:
                continue
            if time.monotonic() >= waited_until:
                break  # somebody holds it for long: go on without, a doubled note is the smaller harm
            time.sleep(0.05)
        except OSError:
            break
    try:
        yield
    finally:
        if held:
            with contextlib.suppress(OSError):
                lock.unlink()


# ---------------------------------------------------------------- the chat's note


def _read(brain: Brain, path: str) -> dict | None:
    if not path:
        return None
    try:
        file = brain.read(path)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    note = core.note_from_file(file["path"], file["text"], file["mtime"])
    note["text"] = file["text"]
    return note if note["type"] == "session" else None


def _slug(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".md")


def _write(brain: Brain, note: dict, changes: dict | None = None, line: str = "", drop: tuple[str, ...] = (),
           now: dt.datetime | None = None, goal: str = "") -> dict:
    """Change the note's properties, add one log line, or both. Read again first: the watcher and the sync
    write these notes too."""
    now = now or _now()
    for _ in range(3):
        current = _read(brain, note["path"])
        if current is None:
            raise ChatError(f"The chat's note is gone: {note['path']}")
        body = None
        if line or goal:
            body = current["body"]
            if goal:
                body = _with_goal(body, goal)
            if line:
                body = body.rstrip() + f"\n- {now.strftime('%H:%M')} {line}\n"
        text = core.update_note_text(current["text"], {**(changes or {}), "turn_at": _stamp(now)}, body)
        if drop:
            text = vibe._drop_properties(text, drop)
        try:
            brain.write(note["path"], text, current["mtime"])
        except ConflictError:
            continue
        return _read(brain, note["path"]) or current
    raise ChatError(f"The chat's note is being written by something else: {note['path']}")


def _with_goal(body: str, goal: str) -> str:
    """The paragraph under "## Goal" becomes the work the chat named."""
    lines = body.split("\n")
    for index, line in enumerate(lines):
        if line.strip().lower() == "## goal":
            end = next((later for later in range(index + 1, len(lines)) if lines[later].startswith("## ")), len(lines))
            return "\n".join([*lines[: index + 1], "", goal, "", *lines[end:]])
    return body


def _open(brain: Brain, folder, agent: str, title: str, unnamed: bool, now: dt.datetime) -> dict:
    sessions = core.FOLDERS["session"]
    try:
        taken = [f"{sessions}/{name}" for name in os.listdir(brain.dir / sessions)]
    except OSError:
        taken = []
    today = now.date().isoformat()
    path = core.note_path("session", title, taken, today)
    meta = {"title": title, "type": "session", "status": "live", "tags": [], "created": today, "started": _stamp(now),
            "machine": machine.device_name()}
    if agent:
        meta["agent"] = agent
    head = vibe._head(brain)
    if head:
        meta["base"] = head
    meta["chat"] = chat_key(folder)
    meta["folder"] = _shown(folder)
    meta["turn_at"] = _stamp(now)
    if unnamed:
        meta[UNNAMED] = "yes"
    body = f"## Goal\n\n{title}\n\n## Log\n\n- {now.strftime('%H:%M')} start: {title}\n"
    brain.write(path, core.serialize_note(meta, body), None)
    return _read(brain, path)


def _placeholder(agent: str, now: dt.datetime) -> str:
    who = agent.strip().capitalize() if agent.strip() else "Assistant"
    return f"{who} chat, goal not named yet ({now.strftime('%H:%M')})"


def _is_unnamed(note: dict) -> bool:
    return bool(str(note["meta"].get(UNNAMED, "")).strip())


def _minutes_since(note: dict, key: str, now: dt.datetime) -> int | None:
    then = core.stamp_minutes(note["meta"].get(key))
    return None if then is None else core.stamp_minutes(_stamp(now)) - then


def _just_ended(note: dict, now: dt.datetime) -> bool:
    since = _minutes_since(note, "ended", now)
    return since is not None and 0 <= since < REST_MINUTES


def _long_ago(note: dict, now: dt.datetime) -> bool:
    """Its last sign of life is so old that what runs in the folder now is other work, or another chat."""
    last = core.session_last_activity(note)
    return last is not None and core.stamp_minutes(_stamp(now)) - last >= REOPEN_HOURS * 60


def _answer(note: dict, state: str) -> dict:
    return {"path": note["path"], "slug": _slug(note["path"]), "title": note["title"], "state": state,
            "unnamed": _is_unnamed(note), "status": note["status"]}


# ---------------------------------------------------------------- what a chat does


def session_of(brain: Brain, folder) -> dict | None:
    """The note of the chat that works in `folder`, whatever its status; None when no chat checked in there."""
    entry = _load(brain).get(chat_key(folder))
    return _read(brain, str(entry.get("path", ""))) if isinstance(entry, dict) else None


def live_slug(brain: Brain, folder) -> str:
    """The session `cairn vibe log` means when it is run in a chat's folder and names none."""
    note = session_of(brain, folder)
    return _slug(note["path"]) if note and note["status"] == "live" and not vibe.elsewhere(note) else ""


def checkin(brain: Brain, folder, agent: str = "", task: str = "", now: dt.datetime | None = None,
            wake: bool = False) -> dict | None:
    """A command of the chat in `folder` ran. Returns its session: path, slug, title, unnamed, and state,
    one of opened, reopened, named, live, resting (it said how it ended a moment ago: nothing is opened).

    `task` is the work in the chat's own words; it names a session that has no name yet, and after a session
    that ended it starts the next one. `wake` is for a command that is itself a step: it never rests."""
    now = now or _now()
    task = _one_line(task, TITLE_CHARS)
    key = chat_key(folder)
    with _locked(brain):
        state = _load(brain)
        entry = state.get(key) if isinstance(state.get(key), dict) else {}
        note = _read(brain, str(entry.get("path", "")))
        if note is not None and vibe.elsewhere(note):
            note = None  # a note another computer wrote under this name: this computer starts its own
        if note is not None and note["status"] == "live":
            answer = _carry_on(brain, note, task, now)
        elif note is not None and note["meta"].get("closed_by") == "watcher" and not _long_ago(note, now):
            # Ended for silence, not by the chat: the same work goes on.
            note = _write(brain, note, {"status": "live"}, "note: at work again", drop=("ended", "closed_by"), now=now)
            answer = _carry_on(brain, note, task, now)
            answer["state"] = "reopened"
        elif note is not None and not task and not wake and _just_ended(note, now):
            return _answer(note, "resting")
        else:
            if task:
                title, unnamed = task, False
            elif note is not None and not _is_unnamed(note) and not _long_ago(note, now):
                title, unnamed = note["title"].removesuffix(CONTINUED) + CONTINUED, False
            else:
                title, unnamed = _placeholder(agent, now), True
            answer = _answer(_open(brain, folder, agent, title, unnamed, now), "opened")
        state[key] = {"path": answer["path"], "folder": _shown(folder), "seen": _stamp(now)}
        _save(brain, state, now)
    return answer


def _carry_on(brain: Brain, note: dict, task: str, now: dt.datetime) -> dict:
    if task and _is_unnamed(note):
        note = _write(brain, note, {"title": task}, f"prompt: {task}", drop=(UNNAMED,), now=now, goal=task)
        return _answer(note, "named")
    if task and task != note["title"]:
        return _answer(_write(brain, note, line=f"prompt: {task}", now=now), "live")
    quiet = _minutes_since(note, "turn_at", now)
    if quiet is None or quiet >= BEAT_MINUTES:
        with contextlib.suppress(ChatError):
            note = _write(brain, note, now=now)
    return _answer(note, "live")


def step(brain: Brain, folder, text: str, kind: str = "note", progress: int | None = None, agent: str = "",
         now: dt.datetime | None = None) -> dict:
    """One line in the chat's session: what was just done."""
    if kind not in core.LOG_KINDS or kind in ("start", "end"):
        raise ChatError(f"Unknown kind {kind!r}; use one of note, prompt, pass, fail, commit, skill")
    text = _one_line(text, STEP_CHARS)
    if not text:
        raise ChatError("Nothing to log.")
    if progress is not None:
        if not 0 <= progress <= 100:
            raise ChatError("--progress takes a percentage from 0 to 100")
        text = f"[{progress}%] {text}"
    now = now or _now()
    answer = checkin(brain, folder, agent, now=now, wake=True)
    note = _read(brain, answer["path"])
    return _answer(_write(brain, note, line=f"{core.log_kind(text, kind)}: {text}", now=now), answer["state"])


def finish(brain: Brain, folder, result: str = "shipped", summary: str = "", agent: str = "",
           now: dt.datetime | None = None) -> dict:
    """The chat says how its work ended. The note stays; the next task of the chat opens a new one."""
    if result not in RESULTS:
        raise ChatError(f"Result must be one of {', '.join(RESULTS)}")
    now = now or _now()
    answer = checkin(brain, folder, agent, now=now, wake=True)
    note = _read(brain, answer["path"])
    summary = _one_line(summary, STEP_CHARS)
    line = "end: " + result + (f": {summary}" if summary else "")
    note = _write(brain, note, {"status": result, "ended": _stamp(now)}, line, now=now)
    return _answer(note, "ended")


def mirror(brain: Brain, folder, text: str, now: dt.datetime | None = None) -> bool:
    """A journal line written from a chat's folder is a step of that chat too. Only for a folder a chat
    has checked in from, and only while its session runs. Never the reason the journal line fails."""
    try:
        note = session_of(brain, folder)
        if note is None or vibe.elsewhere(note) or not (note["status"] == "live" or note["meta"].get("closed_by") == "watcher"):
            return False
        step(brain, folder, text, now=now)
    except Exception:  # noqa: BLE001 - the journal line is already written
        return False
    return True


def adopt(brain: Brain, folder, title: str, agent: str = "", now: dt.datetime | None = None) -> dict | None:
    """`cairn vibe start` in the folder of a chat whose session has no name yet names that session, so one
    chat is one note. None when there is nothing to adopt: the session is then started the usual way."""
    title = _one_line(title, TITLE_CHARS)
    now = now or _now()
    note = session_of(brain, folder)
    if note is None and agent:
        note = _young_nameless(brain, agent, now)
    if not title or note is None or note["status"] != "live" or not _is_unnamed(note) or vibe.elsewhere(note):
        return None
    changes = {"title": title, **({"agent": agent} if agent else {})}
    return _write(brain, note, changes, f"prompt: {title}", drop=(UNNAMED,), now=now, goal=title)


def _young_nameless(brain: Brain, agent: str, now: dt.datetime) -> dict | None:
    """An assistant that starts its session with `cairn vibe start --agent ...` from another folder than its
    chat's (after a `cd`) is the chat that checked in a moment ago under the same app's name, when there is
    exactly one such chat without a name. With two there is no telling, and nothing is adopted."""
    found = []
    for entry in _load(brain).values():
        note = _read(brain, str(entry.get("path", ""))) if isinstance(entry, dict) else None
        if note is None or note["status"] != "live" or not _is_unnamed(note) or vibe.elsewhere(note):
            continue
        age = _minutes_since(note, "started", now)
        if str(note["meta"].get("agent", "")).lower() == agent.lower() and age is not None and 0 <= age <= ADOPT_MINUTES:
            found.append(note)
    return found[0] if len(found) == 1 else None


def point(brain: Brain, folder, path: str, now: dt.datetime | None = None) -> bool:
    """A chat that is known here started another session by name (`cairn vibe start`): its signs of life
    and its saved notes go to that one from now on. Nothing happens in a folder no chat checked in from."""
    now = now or _now()
    key = chat_key(folder)
    with _locked(brain):
        state = _load(brain)
        note = _read(brain, path)
        if key not in state or note is None:
            return False
        _write(brain, note, {"chat": key, "folder": _shown(folder)}, now=now)
        state[key] = {"path": path, "folder": _shown(folder), "seen": _stamp(now)}
        _save(brain, state, now)
    return True

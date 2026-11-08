"""Auto-detect AI coding sessions so the Vibe page fills itself in (`cairn vibe watch`).

Two sources, both read-only and both optional:

* **OpenCode** keeps every chat in a local SQLite database. We read only the session table (title,
  folder, model, cost) and message timestamps, never message text. Each OpenCode session becomes one
  session note, kept in sync on every pass. OpenCode does not record what a session changed, so when
  the session's folder is a git repo, commits made there while the session ran are the evidence.
* **Vector** (or any desktop assistant whose chats we cannot read) is detected from the outside: the
  app's process is running *and* the repo changes. That opens a live session, new commits are logged as
  they land, and the session ends when the app closes or the repo goes quiet.

Sessions are ordinary `brain/sessions/*.md` notes (`source: opencode` / `source: vector`), so stats,
feedback and the web app treat them like hand-made ones. A session counts as live while it has activity
in the last `idle` minutes; after that it is `shipped` if it changed code and `done` if it did not
(a research or Q&A chat: it counts toward time, not toward the ship rate). Time is active time from
message timestamps, so long pauses do not count.
A status you change by hand is kept.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import core, vibe
from .brain import Brain

IDLE_MINUTES = 30
ACTIVE_GAP_MINUTES = 10  # a pause longer than this between messages is not counted as working time
DAYS = 14
EVERY_SECONDS = 60
VECTOR_PROCESSES = ("Vector.exe", "Vector")
STARTUP_NAME = "Cairn vibe watch.vbs"


class WatchError(RuntimeError):
    pass


# ------------------------------------------------------------ time helpers


def _stamp(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M")


def _clock(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).date().isoformat()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _norm_dir(path: str) -> str:
    """Comparable form of a folder: long names (no 8.3 short names), forward slashes, any case."""
    text = str(path)
    try:
        if text and os.path.exists(text):
            text = os.path.realpath(text)
    except OSError:
        pass
    return text.replace("\\", "/").rstrip("/").casefold()


def _inside(directory: str, root: str) -> bool:
    directory, root = _norm_dir(directory), _norm_dir(root)
    return bool(root) and (directory == root or directory.startswith(root + "/"))


def _one_line(text) -> str:
    return " ".join(str(text or "").split())


# ------------------------------------------------------------ OpenCode


def opencode_db_path() -> Path:
    """Where OpenCode keeps its database ($CAIRN_OPENCODE_DB overrides)."""
    override = os.environ.get("CAIRN_OPENCODE_DB", "").strip()
    if override:
        return Path(override).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base / "opencode" / "opencode.db"


def _model_name(value) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        parsed = json.loads(text)
    except ValueError:
        return _one_line(text)[:60]
    if isinstance(parsed, dict):
        return _one_line(parsed.get("modelID") or parsed.get("id") or parsed.get("name") or "")[:60]
    return _one_line(parsed)[:60]


def read_opencode(db_path: Path, since_ms: int, only_inside: str = "") -> list[dict]:
    """Top-level OpenCode sessions active since `since_ms` (subagent runs are counted, not listed).

    Opens the database read-only and selects only session-table columns: no prompts, no replies."""
    if not db_path.is_file():
        return []
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as error:
        raise WatchError(f"Could not open the OpenCode database: {error}") from error
    try:
        columns = {row[1] for row in connection.execute("pragma table_info(session)")}
        if not {"id", "title", "time_created", "time_updated"} <= columns:
            return []
        wanted = [
            "id", "parent_id", "title", "directory", "time_created", "time_updated", "time_archived",
            "summary_additions", "summary_deletions", "summary_files", "model", "cost",
        ]
        present = [name for name in wanted if name in columns]
        rows = connection.execute(
            f"select {', '.join(present)} from session where time_updated >= ? order by time_created",
            (since_ms,),
        ).fetchall()
        records = [dict(zip(present, row)) for row in rows]
        activity = _message_times(connection, [str(r["id"]) for r in records if not r.get("parent_id")])
    except sqlite3.Error as error:
        raise WatchError(f"Could not read OpenCode sessions: {error}") from error
    finally:
        connection.close()

    subagents: dict[str, int] = {}
    for record in records:
        parent = record.get("parent_id")
        if parent:
            subagents[parent] = subagents.get(parent, 0) + 1
    sessions = []
    for record in records:
        if record.get("parent_id") or record.get("time_archived"):
            continue
        directory = str(record.get("directory") or "")
        if only_inside and not _inside(directory, only_inside):
            continue
        stamps = activity.get(str(record["id"]), [])
        sessions.append({
            "id": str(record["id"]),
            "title": _one_line(record.get("title")) or "OpenCode session",
            "directory": directory,
            "created": int(stamps[0]) if stamps else int(record["time_created"]),
            # OpenCode touches time_updated for housekeeping too; the last message is the real last activity.
            "updated": int(stamps[-1]) if stamps else int(record["time_updated"]),
            "minutes": active_minutes(stamps) if stamps else None,
            "spans": active_spans(stamps),
            "messages": len(stamps),
            "added": int(record.get("summary_additions") or 0),
            "removed": int(record.get("summary_deletions") or 0),
            "files": int(record.get("summary_files") or 0),
            "model": _model_name(record.get("model")),
            "cost": float(record.get("cost") or 0),
            "subagents": subagents.get(str(record["id"]), 0),
            "commits": 0,
        })
    return sessions


def _message_times(connection: sqlite3.Connection, session_ids: list[str]) -> dict[str, list[int]]:
    """Message timestamps per session (timestamps only; message content is never selected)."""
    columns = {row[1] for row in connection.execute("pragma table_info(message)")}
    if not session_ids or not {"session_id", "time_created"} <= columns:
        return {}
    times: dict[str, list[int]] = {}
    for start in range(0, len(session_ids), 500):
        chunk = session_ids[start:start + 500]
        query = (
            f"select session_id, time_created from message where session_id in ({','.join('?' for _ in chunk)}) "
            "order by session_id, time_created"
        )
        for session_id, created in connection.execute(query, chunk):
            if created is not None:
                times.setdefault(str(session_id), []).append(int(created))
    return times


def active_minutes(stamps: list[int], gap_minutes: int = ACTIVE_GAP_MINUTES) -> int:
    """Working time from message timestamps: add up the gaps, but never more than `gap_minutes` per gap."""
    cap = gap_minutes * 60_000
    total = sum(min(later - earlier, cap) for earlier, later in zip(stamps, stamps[1:]))
    return max(1, round(total / 60_000)) if len(stamps) > 1 else 1


def active_spans(stamps: list[int], gap_minutes: int = ACTIVE_GAP_MINUTES) -> list[tuple[int, int]]:
    """When the session was working, as (start ms, minutes): each message counts until the next one,
    for at most `gap_minutes`, and touching stretches are merged. Same total as active_minutes."""
    if not stamps:
        return []
    cap = gap_minutes * 60_000
    pieces = [(earlier, earlier + min(later - earlier, cap)) for earlier, later in zip(stamps, stamps[1:])]
    if not pieces:
        pieces = [(stamps[0], stamps[0] + 60_000)]
    merged: list[list[int]] = []
    for start, end in sorted(pieces):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, max(1, round((end - start) / 60_000))) for start, end in merged]


def _spans_text(spans: list[tuple[int, int]]) -> str:
    return " ".join(f"{_stamp(start)}/{minutes}" for start, minutes in spans)


def git_executable() -> str | None:
    """git on PATH, else the usual Windows install folders (a startup task may not inherit PATH)."""
    found = shutil.which("git")
    if found:
        return found
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        for relative in (("Programs", "Git", "cmd", "git.exe"), ("Git", "cmd", "git.exe")):
            candidate = Path(base, *relative) if base else None
            if candidate and candidate.is_file():
                return str(candidate)
    return None


def folder_commit_times(directory: str, since_ms: int, git: str | None = None) -> list[int] | None:
    """Commit times (ms) on the local branches of the repo at `directory` since `since_ms`, leaving out
    merges and bot commits.
    None when the folder is not a git repo (or git is missing): then there is no evidence either way."""
    git = git or git_executable()
    if not git or not directory or not Path(directory).is_dir():
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [git, "-C", directory, "log", "--branches", "--no-merges", f"--since=@{since_ms // 1000}", "--format=%ct %an"],
            capture_output=True, text=True, timeout=20, creationflags=flags,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    times = []
    for line in result.stdout.splitlines():
        stamp, _, author = line.partition(" ")
        # Bots (CI, dependabot) commit on their own; merges only bring in work done elsewhere.
        if stamp.isdigit() and "[bot]" not in author:
            times.append(int(stamp) * 1000)
    return times


def attach_commits(sessions: list[dict], git: str | None = None) -> None:
    """Count commits made in each session's folder while it ran (one git call per folder)."""
    by_folder: dict[str, list[dict]] = {}
    for session in sessions:
        if session.get("directory"):
            by_folder.setdefault(session["directory"], []).append(session)
    grace = ACTIVE_GAP_MINUTES * 60_000
    for directory, group in by_folder.items():
        times = folder_commit_times(directory, min(s["created"] for s in group) - grace, git)
        for session in group:
            session["repo"] = times is not None
            if times:
                session["commits"] = sum(1 for t in times if session["created"] - 60_000 <= t <= session["updated"] + grace)


def _opencode_note(session: dict, status: str) -> tuple[dict, str]:
    meta = {
        "title": session["title"],
        "type": "session",
        "status": status,
        "tags": [],
        "created": _day(session["created"]),
        "started": _stamp(session["created"]),
        "agent": "opencode",
        "source": "opencode",
        "external": session["id"],
    }
    if session["model"]:
        meta["model"] = session["model"]
    if session["directory"]:
        meta["folder"] = session["directory"]
    if status != "live":
        meta["ended"] = _stamp(session["updated"])
    if session.get("minutes") is not None:
        meta["minutes"] = str(session["minutes"])
    if session.get("spans"):
        meta["spans"] = _spans_text(session["spans"])
    meta["added"] = str(session["added"])
    meta["removed"] = str(session["removed"])
    meta["files"] = str(session["files"])
    if session.get("commits"):
        meta["commits"] = str(session["commits"])
    if session.get("repo") is False:
        meta["git"] = "no"  # no commit evidence either way: the folder is not a git repo
    if session["cost"] > 0:
        meta["cost"] = f"{session['cost']:.2f}"
    lines = [f"- {_clock(session['created'])} start: {session['title']} (OpenCode{', ' + session['model'] if session['model'] else ''})"]
    if session.get("messages"):
        lines.append(f"- {_clock(session['updated'])} note: {session['messages']} messages, about {session['minutes']} min of activity")
    if session["subagents"]:
        lines.append(f"- {_clock(session['updated'])} note: {session['subagents']} subagent task{'s' if session['subagents'] != 1 else ''}")
    if session["files"] or session["added"] or session["removed"]:
        lines.append(f"- {_clock(session['updated'])} note: {session['files']} files changed, +{session['added']} −{session['removed']}")
    if session.get("commits"):
        lines.append(f"- {_clock(session['updated'])} note: {session['commits']} commit{'s' if session['commits'] != 1 else ''} in this folder while it ran")
    if status != "live":
        if status in ("shipped", "partial"):
            outcome = "code changed"
        elif session.get("repo"):
            outcome = "no commits in its folder"
        else:
            outcome = "no commits seen (folder is not a git repo)"
        lines.append(f"- {_clock(session['updated'])} end: {status}: auto-detected, {outcome}")
    body = f"## Goal\n\n{session['title']}\n\n## Log\n\n" + "\n".join(lines) + "\n"
    return meta, body


def _external_index(brain: Brain, source: str) -> dict[str, dict]:
    index = {}
    for note in brain.notes():
        if note["type"] == "session" and note["meta"].get("source") == source and note["meta"].get("external"):
            index[str(note["meta"]["external"])] = note
    return index


def sync_opencode(brain: Brain, sessions: list[dict], now_ms: int, idle_minutes: int = IDLE_MINUTES) -> dict:
    """Create or refresh one session note per OpenCode session. Returns counts."""
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    existing = _external_index(brain, "opencode")
    paths = [f["path"] for f in brain.files()]
    for session in sessions:
        active = now_ms - session["updated"] < idle_minutes * 60_000
        changed_code = session["added"] + session["removed"] + session["files"] + session.get("commits", 0) > 0
        computed = "live" if active else ("shipped" if changed_code else "done")
        note = existing.get(session["id"])
        if note is None:
            meta, body = _opencode_note(session, computed)
            path = core.note_path("session", session["title"], paths, meta["created"])
            brain.write(path, core.serialize_note(meta, body), None)
            paths.append(path)
            counts["created"] += 1
            continue
        current_status = str(note["meta"].get("status", ""))
        # Keep a status set by hand; only sessions still marked live (or picking up new work) follow the source.
        recorded_end = core.stamp_minutes(str(note["meta"].get("ended", "")))
        new_activity = recorded_end is not None and core.stamp_minutes(_stamp(session["updated"])) > recorded_end
        status = computed if (current_status == "live" or new_activity or current_status not in core.STATUSES["session"]) else current_status
        meta, body = _opencode_note(session, status)
        current = brain.read(note["path"])
        changes = {key: value for key, value in meta.items() if key not in ("tags", "created")}
        text = core.update_note_text(current["text"], changes, body)
        if status == "live":
            text = _drop_property(text, "ended")
        if text == current["text"]:
            counts["unchanged"] += 1
            continue
        brain.write(note["path"], text, current["mtime"])
        counts["updated"] += 1
    return counts


def _drop_property(text: str, key: str) -> str:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            break
        if lines[index].split(":", 1)[0].strip() == key:
            del lines[index]
            break
    return "\n".join(lines)


# ------------------------------------------------------------ Vector (detected from outside)


def process_running(names=VECTOR_PROCESSES) -> bool:
    """Is any process with one of these image names running? Uses the OS process list only."""
    wanted = {name.casefold() for name in names}
    try:
        if os.name == "nt":
            output = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="replace",
            ).stdout
            running = {line.split('","', 1)[0].strip('"').casefold() for line in output.splitlines() if line.startswith('"')}
            return bool(wanted & running)
        output = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=20).stdout
        running = {Path(line.strip()).name.casefold() for line in output.splitlines() if line.strip()}
        return bool(wanted & running)
    except (OSError, subprocess.SubprocessError):
        return False


def _state_path(brain: Brain) -> Path:
    git_dir = brain.root / ".git"
    return (git_dir if git_dir.is_dir() else brain.dir) / "cairn-watch.json"


def _load_state(brain: Brain) -> dict:
    try:
        return json.loads(_state_path(brain).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(brain: Brain, state: dict) -> None:
    try:
        _state_path(brain).write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass


def repo_fingerprint(brain: Brain) -> str:
    """Changes whenever the code changes: HEAD plus the working-tree status.

    The brain folder is left out, so the watcher's own session notes never count as activity."""
    if not brain.git_available():
        return ""
    head = brain._git("rev-parse", "HEAD").stdout.strip()
    status = brain._git("status", "--porcelain", "-uall").stdout
    try:
        folder = brain.dir.resolve().relative_to(brain.root.resolve()).as_posix()
    except ValueError:
        folder = ""
    if folder == ".":
        folder = ""
    lines = [
        line for line in status.splitlines()
        if not (folder and line[3:].strip().strip('"').startswith(folder + "/"))
    ]
    digest = hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:12]
    return f"{head[:12]}:{digest}"


def _vector_live(brain: Brain) -> dict | None:
    live = [n for n in vibe.live_sessions(brain) if n["meta"].get("source") == "vector"]
    return live[-1] if live else None


def _vector_self_reported(brain: Brain) -> bool:
    """True when the assistant in Vector is reporting its own session (cairn vibe start --agent vector)."""
    return any(
        str(n["meta"].get("agent", "")).lower() == "vector" and n["meta"].get("source") != "vector"
        for n in vibe.live_sessions(brain)
    )


def sync_vector(brain: Brain, running: bool, now_ms: int, idle_minutes: int = IDLE_MINUTES) -> str:
    """One watcher tick for Vector. Returns what happened: '', 'started', 'activity', 'ended'."""
    state = _load_state(brain)
    fingerprint = repo_fingerprint(brain)
    if not fingerprint:
        return ""
    previous = state.get("vector_fingerprint")
    state["vector_fingerprint"] = fingerprint
    session = _vector_live(brain)
    action = ""
    changed = previous is not None and previous != fingerprint
    if running and changed:
        state["vector_last_activity"] = now_ms
        if session is None and _vector_self_reported(brain):
            action = ""  # the assistant is already logging this session itself; don't open a duplicate
        elif session is None:
            branch = brain._git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or "detached"
            note = vibe.start(brain, f"Vector session on {brain.project_name} ({branch})", agent="vector")
            current = brain.read(note["path"])
            brain.write(note["path"], core.update_note_text(current["text"], {"source": "vector"}), current["mtime"])
            state["vector_logged"] = []
            action = "started"
        else:
            action = "activity"
        session = _vector_live(brain)
        if session is not None:
            logged = set(state.get("vector_logged", []))
            activity = vibe.git_activity(brain, str(session["meta"].get("started", "")), str(session["meta"].get("base", "")))
            for commit in reversed(activity["commits"]):
                if commit["hash"] not in logged:
                    vibe.log(brain, commit["subject"], kind="commit", session=session["slug"])
                    logged.add(commit["hash"])
            state["vector_logged"] = sorted(logged)
    elif session is not None:
        last = int(state.get("vector_last_activity") or 0)
        quiet = now_ms - last >= idle_minutes * 60_000 if last else True
        if not running or quiet:
            activity = vibe.git_activity(brain, str(session["meta"].get("started", "")), str(session["meta"].get("base", "")))
            # Committed work shipped; changes still sitting uncommitted (or none at all) are partial.
            result = "shipped" if activity["commits"] else "partial"
            reason = "Vector closed" if not running else f"no repo activity for {idle_minutes} min"
            detail = f"{len(activity['commits'])} commit(s)" if activity["commits"] else "nothing committed yet"
            vibe.end(brain, result, f"auto-detected, {reason}, {detail}", session_slug=session["slug"])
            state.pop("vector_last_activity", None)
            state["vector_logged"] = []
            action = "ended"
    _save_state(brain, state)
    return action


# ------------------------------------------------------------ the loop


def watch_once(brain: Brain, *, opencode: bool = True, vector: bool = True, everywhere: bool = False,
               days: int = DAYS, idle_minutes: int = IDLE_MINUTES, now_ms: int | None = None,
               vector_names=VECTOR_PROCESSES, db_path: Path | None = None, running: bool | None = None,
               git: str | None = None) -> list[str]:
    """One pass over every source. Returns human-readable lines describing what changed."""
    now_ms = _now_ms() if now_ms is None else now_ms
    messages = []
    if opencode:
        path = db_path or opencode_db_path()
        sessions = read_opencode(path, now_ms - days * 86_400_000, "" if everywhere else str(brain.root))
        pending = needs_refresh(brain, sessions, now_ms, idle_minutes)
        attach_commits(pending, git)
        counts = sync_opencode(brain, pending, now_ms, idle_minutes)
        if counts["created"] or counts["updated"]:
            messages.append(f"OpenCode: {counts['created']} new, {counts['updated']} updated session(s)")
    if vector:
        is_running = process_running(vector_names) if running is None else running
        action = sync_vector(brain, is_running, now_ms, idle_minutes)
        if action == "started":
            messages.append("Vector: session started (app open and the repo changed)")
        elif action == "ended":
            messages.append("Vector: session ended")
    return messages


def needs_refresh(brain: Brain, sessions: list[dict], now_ms: int, idle_minutes: int = IDLE_MINUTES) -> list[dict]:
    """Sessions whose note is missing, still live, from an older watcher, or has new activity.
    The rest are finished and already written, so a pass costs nothing for them (no git calls)."""
    existing = _external_index(brain, "opencode")
    pending = []
    for session in sessions:
        note = existing.get(session["id"])
        # Notes from an older watcher get one refresh to add spans and commits (sessions with no messages have none).
        older = "spans" not in note["meta"] and bool(session["spans"]) if note is not None else False
        if note is None or note["meta"].get("status") == "live" or older:
            pending.append(session)
            continue
        recorded_end = core.stamp_minutes(str(note["meta"].get("ended", "")))
        if recorded_end is None or core.stamp_minutes(_stamp(session["updated"])) > recorded_end:
            pending.append(session)
        elif now_ms - session["updated"] < idle_minutes * 60_000:
            pending.append(session)
    return pending


def watch(brain: Brain, every: int = EVERY_SECONDS, echo=print, **options) -> None:
    echo(f"Watching for AI coding sessions every {every}s (Ctrl+C to stop). Sessions land in brain/sessions/.")
    while True:
        try:
            stamp = dt.datetime.now().strftime("%H:%M")
            for message in watch_once(brain, **options):
                echo(f"{stamp} {message}")
        except WatchError as error:
            echo(f"warning: {error}")
        time.sleep(max(5, every))


# ------------------------------------------------------------ start with Windows


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def install_startup(brain: Brain, args: list[str]) -> Path:
    """Start the watcher, hidden, every time you sign in to Windows."""
    if os.name != "nt":
        raise WatchError("Auto-start is set up for Windows. Elsewhere, add `cairn vibe watch` to your login items.")
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    runner = windowless if windowless.exists() else python
    command = [str(runner), "-m", "cairn", "--root", str(brain.root), "vibe", "watch", *args]
    quoted = " ".join(f'""{part}""' for part in command)
    # Windows starts this from its own folder, so point the working folder at the one holding the
    # `cairn` package; otherwise `-m cairn` only works when Cairn was pip-installed.
    package_parent = Path(__file__).resolve().parent.parent
    script = (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.CurrentDirectory = "{package_parent}"\r\n'
        f'shell.Run "{quoted}", 0, False\r\n'
    )
    target = startup_folder() / STARTUP_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8")
    return target


def uninstall_startup() -> bool:
    target = startup_folder() / STARTUP_NAME
    if target.exists():
        target.unlink()
        return True
    return False

"""Auto-detect AI coding sessions so the Vibe page fills itself in (`cairn vibe watch`).

Two sources, both read-only and both optional:

* **OpenCode** keeps every chat in a local SQLite database. We read only the session table (title,
  folder, model, tokens, cost) and message timestamps and types, never message text, unless you switch
  on `--last-reply`: then the closing reply of a finished turn is copied in, cut to one short line.
  Each OpenCode session becomes one session note, kept in sync on every pass; a script that starts
  hundreds of one-prompt chats a day becomes one note per folder per day. OpenCode does not record what
  a session changed, so when the session's folder is a git repo, commits made there while the session
  ran are the evidence.
* **Vector** (or any desktop assistant whose chats we cannot read) is detected from the outside: the
  app's process is running *and* the repo changes. That opens a live session, new commits are logged as
  they land, and the session ends when the app closes or the repo goes quiet.

Sessions are ordinary `brain/sessions/*.md` notes (`source: opencode` / `source: vector`), so stats,
feedback and the web app treat them like hand-made ones. A session is live only while it is working:
an OpenCode session ends the moment its turn finishes (a new prompt opens it again), one whose turn never
finished ends after `idle` minutes without a message, and so does one that was deleted from the database.
A session an assistant reports itself (`cairn vibe start`) ends after REPORTED_IDLE_MINUTES without a
step, and opens again when the assistant logs to it. Ended sessions are `shipped` if they changed code
and `done` if they did not (a research or Q&A chat: it counts toward time, not toward the ship rate).
Time is active time from message timestamps, so long pauses do not count.
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

from . import core, github_copy, github_usage, machine, vibe
from .brain import Brain, ConflictError

IDLE_MINUTES = 30
REPORTED_IDLE_MINUTES = 240  # an assistant reporting its own session can think for hours between steps
CLOSED_BY = "watcher"  # marks a session the watcher ended for silence, so its next step can open it again
ACTIVE_GAP_MINUTES = 10  # a pause longer than this between messages is not counted as working time
DAYS = 14
EVERY_SECONDS = 60
VECTOR_PROCESSES = ("Vector.exe", "Vector")
STARTUP_NAME = "Cairn vibe watch.vbs"
CLAIM_STALE_SECONDS = 5 * 60  # a watcher that has not checked in for this long has stopped
SESSION_TABLES = ("session_v2", "session")  # OpenCode 2.2 renamed its tables; newest first
MESSAGE_TABLES = ("session_message", "message")
TOKEN_COLUMNS = ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")
QUIET_TYPES = ("model-switched", "agent-switched", "system")  # bookkeeping messages: not a turn starting or ending
BATCH_RUNS = 20  # this many one-prompt runs in one folder in one day is a script, not a person
REPLY_CHARS = 280
MACHINE_FOLDER = "machines"
HISTORY_KEY = "opencode_history"  # watch state: computer -> when its whole OpenCode history was read
MACHINE_NOTE_MINUTES = 15  # an unchanged reading is written again after this long, so a reader can tell it is current
MACHINE_MOVE_POINTS = 10  # CPU or memory moving this many points is worth a new note


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


def _connect(db_path: Path) -> sqlite3.Connection:
    """The OpenCode database, opened read-only."""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as error:
        raise WatchError(f"Could not open the OpenCode database: {error}") from error


def _table(connection: sqlite3.Connection, names: tuple[str, ...], needed: set[str]) -> tuple[str, set[str]]:
    """The first of `names` that exists with the `needed` columns, and all its columns.
    OpenCode renamed its tables in 2.2 (session -> session_v2, message -> session_message)."""
    for name in names:
        columns = {row[1] for row in connection.execute(f"pragma table_info({name})")}
        if needed <= columns:
            return name, columns
    return "", set()


def _model_effort(value) -> str:
    """The reasoning effort OpenCode stores next to the model (its "variant"), when there is one."""
    try:
        parsed = json.loads(str(value or ""))
    except ValueError:
        return ""
    if isinstance(parsed, dict):
        return _one_line(parsed.get("variant") or parsed.get("effort") or "")[:30]
    return ""


def _tokens(record: dict) -> dict:
    """Tokens of one session row: what went in, what came out (reasoning included), what the cache served."""
    return {
        "in": int(record.get("tokens_input") or 0),
        "out": int(record.get("tokens_output") or 0) + int(record.get("tokens_reasoning") or 0),
        "cache": int(record.get("tokens_cache_read") or 0) + int(record.get("tokens_cache_write") or 0),
    }


def _turn(last_type: str, outcome) -> str:
    """Where the conversation stands, from the type of its last message: OpenCode writes an `idle`
    message when a turn ends. "" when the database does not record message types."""
    if not last_type:
        return ""
    if last_type != "idle":
        return "working"
    return "failed" if str(outcome or "") == "failed" else "done"


def read_opencode(db_path: Path, since_ms: int, only_inside: str = "") -> list[dict]:
    """Top-level OpenCode sessions active since `since_ms` (subagent runs are counted, not listed).

    Opens the database read-only and selects only session-table columns and message times and types:
    no prompts, no replies."""
    if not db_path.is_file():
        return []
    connection = _connect(db_path)
    try:
        table, columns = _table(connection, SESSION_TABLES, {"id", "title", "time_created", "time_updated"})
        if not table:
            return []
        wanted = [
            "id", "parent_id", "title", "directory", "time_created", "time_updated", "time_archived",
            "summary_additions", "summary_deletions", "summary_files", "model", "cost", "idle_outcome",
            *TOKEN_COLUMNS,
        ]
        present = [name for name in wanted if name in columns]
        rows = connection.execute(
            f"select {', '.join(present)} from {table} where time_updated >= ? order by time_created",
            (since_ms,),
        ).fetchall()
        records = [dict(zip(present, row)) for row in rows]
        activity = _message_activity(connection, [str(r["id"]) for r in records if not r.get("parent_id")])
    except sqlite3.Error as error:
        raise WatchError(f"Could not read OpenCode sessions: {error}") from error
    finally:
        connection.close()

    has_tokens = "tokens_input" in columns
    parents = {str(r["id"]): str(r["parent_id"]) for r in records if r.get("parent_id")}
    subagents: dict[str, int] = {}
    tokens: dict[str, dict] = {}
    for record in records:
        parent = record.get("parent_id")
        if parent:
            subagents[parent] = subagents.get(parent, 0) + 1
        # A subagent burns its own tokens; they belong to the conversation that started it.
        root = str(record["id"])
        for _ in range(20):
            if root not in parents:
                break
            root = parents[root]
        total = tokens.setdefault(root, {"in": 0, "out": 0, "cache": 0})
        for key, amount in _tokens(record).items():
            total[key] += amount
    sessions = []
    for record in records:
        if record.get("parent_id") or record.get("time_archived"):
            continue
        directory = str(record.get("directory") or "")
        if only_inside and not _inside(directory, only_inside):
            continue
        seen = activity.get(str(record["id"]), {})
        stamps = seen.get("times", [])
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
            "effort": _model_effort(record.get("model")),
            "cost": float(record.get("cost") or 0),
            "tokens": tokens.get(str(record["id"])) if has_tokens else None,
            "prompts": seen.get("prompts"),
            "turn": _turn(seen.get("last", ""), record.get("idle_outcome")),
            "subagents": subagents.get(str(record["id"]), 0),
            "commits": 0,
        })
    return sessions


def _message_activity(connection: sqlite3.Connection, session_ids: list[str]) -> dict[str, dict]:
    """Per session: message `times` (sorted), and where the table records message types, how many
    `prompts` you sent and the `last` message type. Message content is never selected."""
    table, columns = _table(connection, MESSAGE_TABLES, {"session_id", "time_created"})
    if not session_ids or not table:
        return {}
    typed = "type" in columns
    order = "seq" if "seq" in columns else "time_created"
    seen: dict[str, dict] = {}
    for start in range(0, len(session_ids), 500):
        chunk = session_ids[start:start + 500]
        query = (
            f"select session_id, time_created, {'type' if typed else 'null'} from {table} "
            f"where session_id in ({','.join('?' for _ in chunk)}) order by session_id, {order}"
        )
        for session_id, created, kind in connection.execute(query, chunk):
            entry = seen.setdefault(str(session_id), {"times": [], "prompts": 0 if typed else None, "last": ""})
            if created is not None:
                entry["times"].append(int(created))
            if kind == "user":
                entry["prompts"] += 1
            if typed and kind not in QUIET_TYPES:
                entry["last"] = str(kind or "")
    for entry in seen.values():
        entry["times"].sort()
    return seen


def _folder_label(directory: str) -> str:
    return Path(str(directory).replace("\\", "/")).name or "OpenCode"


def rollup_batches(sessions: list[dict], keep_ids=(), threshold: int = BATCH_RUNS) -> list[dict]:
    """Fold scripted one-prompt runs into one session per folder per day.

    A loop that starts a fresh one-prompt chat hundreds of times a day would otherwise bury the page (and
    the brain) in notes. A folder counts as scripted once it has `threshold` such runs in a single day;
    from then on its one-prompt runs are summed into a daily "automated runs" session, so their time and
    tokens still count. Runs that already have a note (`keep_ids`) and real conversations are left alone."""
    keep = set(keep_ids)

    def short(session: dict) -> bool:
        return session.get("prompts") is not None and session["prompts"] <= 1 and not session["subagents"]

    per_day: dict[tuple[str, str], int] = {}
    for session in sessions:
        if short(session):
            key = (_norm_dir(session["directory"]), _day(session["created"]))
            per_day[key] = per_day.get(key, 0) + 1
    scripted = {folder for (folder, _), count in per_day.items() if count >= threshold}
    if not scripted:
        return sessions
    kept, groups = [], {}
    for session in sessions:
        folder = _norm_dir(session["directory"])
        if folder in scripted and short(session) and session["id"] not in keep:
            groups.setdefault((folder, _day(session["created"])), []).append(session)
        else:
            kept.append(session)
    for (folder, day), members in groups.items():
        kept.append(_batch_session(folder, day, members))
    return sorted(kept, key=lambda session: session["created"])


def _batch_session(folder: str, day: str, members: list[dict]) -> dict:
    latest = max(members, key=lambda session: session["updated"])
    pieces = sorted((start, start + minutes * 60_000) for member in members for start, minutes in member["spans"])
    merged: list[list[int]] = []
    for start, end in pieces:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    spans = [(start, max(1, round((end - start) / 60_000))) for start, end in merged]
    models: dict[str, int] = {}
    for member in members:
        if member["model"]:
            models[member["model"]] = models.get(member["model"], 0) + 1
    model = max(models, key=models.get) if models else ""
    counted = [member["tokens"] for member in members if member.get("tokens")]
    return {
        "id": f"batch-{hashlib.sha1(folder.encode('utf-8')).hexdigest()[:10]}-{day}",
        "title": f"{_folder_label(latest['directory'])}: automated runs",
        "directory": latest["directory"],
        "created": min(member["created"] for member in members),
        "updated": latest["updated"],
        "minutes": sum(minutes for _, minutes in spans) if spans else None,
        "spans": spans,
        "messages": sum(member["messages"] for member in members),
        "added": sum(member["added"] for member in members),
        "removed": sum(member["removed"] for member in members),
        "files": sum(member["files"] for member in members),
        "model": model,
        "effort": next((member["effort"] for member in members if member["model"] == model and member["effort"]), ""),
        "cost": sum(member["cost"] for member in members),
        "tokens": {key: sum(tokens[key] for tokens in counted) for key in ("in", "out", "cache")} if counted else None,
        "prompts": sum(member["prompts"] or 0 for member in members),
        "turn": latest["turn"],
        "subagents": 0,
        "commits": 0,
        "runs": len(members),
        "failed_runs": sum(1 for member in members if member["turn"] == "failed"),
        "lookup": latest["id"],
    }


def attach_details(db_path: Path, sessions: list[dict], last_reply: bool = False) -> None:
    """Fill in what the session table leaves out, for the sessions about to be written.

    The model and effort are read from the last reply's metadata when the session row has none. Only
    with `last_reply` switched on is any message text read: the closing reply of a finished turn,
    cut to one short line, so you can see how a session ended from another device."""
    wanted = [s for s in sessions if not s.get("model") or (last_reply and s.get("turn") in ("done", "failed") and not s.get("runs"))]
    if not wanted or not db_path.is_file():
        return
    try:
        connection = _connect(db_path)
    except WatchError:
        return
    try:
        table, columns = _table(connection, MESSAGE_TABLES, {"session_id", "type", "seq", "data"})
        if not table:
            return
        for session in wanted:
            ident = session.get("lookup") or session["id"]
            if not session.get("model"):
                row = connection.execute(
                    f"select json_extract(data, '$.model.id'), json_extract(data, '$.model.variant') from {table} "
                    "where session_id = ? and type = 'assistant' order by seq desc limit 1", (ident,),
                ).fetchone()
                if row and row[0]:
                    session["model"] = _one_line(row[0])[:60]
                    session["effort"] = _one_line(row[1] or "")[:30]
            if last_reply and session.get("turn") in ("done", "failed") and not session.get("runs"):
                rows = connection.execute(
                    f"select data from {table} where session_id = ? and type = 'assistant' order by seq desc limit 5",
                    (ident,),
                ).fetchall()
                session["reply"] = next((text for text in (_reply_text(data) for (data,) in rows) if text), "")
    except sqlite3.Error:
        return  # details are a bonus: the session itself is still written
    finally:
        connection.close()


def _reply_text(data) -> str:
    """The last piece of text in one assistant message, on one line and cut to REPLY_CHARS."""
    try:
        content = json.loads(str(data or "")).get("content") or []
    except (ValueError, AttributeError):
        return ""
    for part in reversed(content if isinstance(content, list) else []):
        if isinstance(part, dict) and part.get("type") == "text":
            text = _one_line(part.get("text"))
            if text:
                return text if len(text) <= REPLY_CHARS else text[:REPLY_CHARS - 1].rstrip() + "…"
    return ""


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
        "machine": machine.device_name(),  # which computer it ran on: its database is the one being read
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
    if session.get("effort"):
        meta["effort"] = session["effort"]
    tokens = session.get("tokens")
    if tokens and (tokens["in"] or tokens["out"] or tokens["cache"]):
        meta["tokens"] = str(tokens["in"] + tokens["out"])  # what the model read and wrote; cached context is extra
        meta["tokens_in"] = str(tokens["in"])
        meta["tokens_out"] = str(tokens["out"])
        meta["tokens_cache"] = str(tokens["cache"])
    if session.get("prompts"):
        meta["prompts"] = str(session["prompts"])
    turn = session.get("turn", "")
    if turn:
        meta["turn"] = turn
        meta["turn_at"] = _stamp(session["updated"])
    if session.get("runs"):
        meta["runs"] = str(session["runs"])
    lines = [f"- {_clock(session['created'])} start: {session['title']} (OpenCode{', ' + session['model'] if session['model'] else ''})"]
    if session.get("messages"):
        lines.append(f"- {_clock(session['updated'])} note: {session['messages']} messages, about {session['minutes']} min of activity")
    if session["subagents"]:
        lines.append(f"- {_clock(session['updated'])} note: {session['subagents']} subagent task{'s' if session['subagents'] != 1 else ''}")
    if session["files"] or session["added"] or session["removed"]:
        lines.append(f"- {_clock(session['updated'])} note: {session['files']} files changed, +{session['added']} −{session['removed']}")
    if session.get("commits"):
        lines.append(f"- {_clock(session['updated'])} note: {session['commits']} commit{'s' if session['commits'] != 1 else ''} in this folder while it ran")
    if session.get("runs"):
        failed = session.get("failed_runs", 0)
        lines.append(f"- {_clock(session['updated'])} note: {session['runs']} automated runs{f' ({failed} stopped with an error)' if failed else ''}")
    if status != "live":
        if status in ("shipped", "partial"):
            outcome = "code changed"
        elif session.get("repo"):
            outcome = "no commits in its folder"
        else:
            outcome = "no commits seen (folder is not a git repo)"
        if session.get("runs"):
            pass
        elif turn == "failed":
            outcome += ", last turn stopped with an error"
        elif turn == "working":
            outcome += ", stopped mid-turn (no finish recorded)"
        lines.append(f"- {_clock(session['updated'])} end: {status}: auto-detected, {outcome}")
    body = f"## Goal\n\n{session['title']}\n\n## Log\n\n" + "\n".join(lines) + "\n"
    if session.get("reply"):
        body += f"\n## Last reply\n\n> {session['reply']}\n"
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
        # Nobody is at the keyboard of a finished turn: the session is over until a new prompt arrives.
        # A script's daily batch stays open between its runs, and so does a database without message types.
        turn = session.get("turn", "")
        finished = turn in ("done", "failed") and not session.get("runs")
        computed = "live" if active and not finished else ("shipped" if changed_code else "done")
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
        # A prompt sent in the same minute the last turn ended, or a commit landing just after it.
        resumed = computed == "live" and turn == "working" and str(note["meta"].get("turn", "")) in ("done", "failed")
        late_change = current_status == "done" and computed == "shipped" and active
        follow = current_status == "live" or new_activity or resumed or late_change
        status = computed if (follow or current_status not in core.STATUSES["session"]) else current_status
        meta, body = _opencode_note(session, status)
        current = brain.read(note["path"])
        kept = ("tags", "created", "machine") if str(note["meta"].get("machine", "")).strip() else ("tags", "created")
        changes = {key: value for key, value in meta.items() if key not in kept}
        text = core.update_note_text(current["text"], changes, body)
        if status == "live":
            text = _drop_property(text, "ended")
        if text == current["text"]:
            counts["unchanged"] += 1
            continue
        brain.write(note["path"], text, current["mtime"])
        counts["updated"] += 1
    return counts


def _close(brain: Brain, note: dict, status: str, last: int, reason: str, extra: dict | None = None) -> bool:
    """End a live session note at `last` (stamp_minutes units), the moment it last did anything."""
    ended = core.minutes_stamp(last)
    try:
        current = brain.read(note["path"])
        body = core.parse_note(current["text"])["body"].rstrip() + f"\n- {ended[11:]} end: {status}: {reason}\n"
        text = core.update_note_text(current["text"], {"status": status, "ended": ended, **(extra or {})}, body)
        brain.write(note["path"], text, current["mtime"])
    except (ConflictError, OSError):  # edited or synced under us: the next pass closes it
        return False
    return True


def elsewhere(note: dict) -> bool:
    """True for a session another computer wrote (its `machine` names a different one). Two computers can
    share a brain: each watcher ends only its own sessions, since it cannot see the other's apps or database.
    A note with no `machine` is from before computers were named and is treated as this one's."""
    owner = str(note["meta"].get("machine", "")).strip()
    return bool(owner) and owner != machine.device_name()


def close_missing_opencode(brain: Brain, seen_ids, now_ms: int, idle_minutes: int = IDLE_MINUTES) -> int:
    """End live OpenCode notes whose session is no longer in the database. Returns how many.

    A script that deletes its sessions when it is done (or an archived or very old session) never shows
    up again, so nothing would ever close its note. It waits `idle_minutes` from the note's own last
    activity first, so a session that is merely slow to appear is left alone."""
    seen = set(seen_ids)
    now = core.stamp_minutes(_stamp(now_ms))
    closed = 0
    for external, note in _external_index(brain, "opencode").items():
        if note["status"] != "live" or external in seen or elsewhere(note):
            continue
        last = core.session_last_activity(note)
        if last is None or now - last < idle_minutes:
            continue
        figures = (str(note["meta"].get(key, "")).strip() for key in ("added", "removed", "files", "commits"))
        status = "shipped" if any(figure.isdigit() and int(figure) for figure in figures) else "done"
        closed += _close(brain, note, status, last, "auto-detected, the session is no longer in OpenCode")
    return closed


def close_silent_reported(brain: Brain, now_ms: int, idle_minutes: int = REPORTED_IDLE_MINUTES) -> int:
    """End live sessions an assistant (or you) started by hand and then left: no step for `idle_minutes`.

    Nobody said how it went, so it ends as `done`, which counts toward time and not toward the ship
    rate. It is marked `closed_by: watcher`; the next `cairn vibe log --session <slug>` opens it again."""
    now = core.stamp_minutes(_stamp(now_ms))
    closed = 0
    for note in vibe.live_sessions(brain):
        if note["meta"].get("source") == "opencode" or elsewhere(note):
            continue  # the OpenCode database decides when these end; another computer ends its own
        last = core.session_last_activity(note)
        if last is None or now - last < idle_minutes:
            continue
        hours = idle_minutes / 60
        waited = f"{hours:g} h" if idle_minutes >= 60 else f"{idle_minutes} min"
        closed += _close(brain, note, "done", last, f"auto-ended, no step for {waited} (outcome not reported)", {"closed_by": CLOSED_BY})
    return closed


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


def _claim_path(brain: Brain) -> Path:
    return _state_path(brain).with_name("cairn-watch.owner")


def claim_watch(brain: Brain, owner: str, now: float | None = None) -> bool:
    """Only one watcher writes a brain's session notes at a time: the desktop app, the startup watcher
    and a terminal can all be running. The holder renews its claim every pass; a claim nobody renewed
    for CLAIM_STALE_SECONDS is free to take. Returns True when `owner` may run this pass."""
    now = time.time() if now is None else now
    path = _claim_path(brain)
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        held = {}
    holder = held.get("owner") if isinstance(held, dict) else None
    try:
        renewed = float(held.get("at", 0)) if holder else 0.0
    except (TypeError, ValueError):
        renewed = 0.0
    if holder and holder != owner and now - renewed < CLAIM_STALE_SECONDS:
        return False
    try:
        path.write_text(json.dumps({"owner": owner, "at": now}), encoding="utf-8")
    except OSError:
        pass  # a read-only brain still gets watched; it just cannot tell other watchers
    return True


def release_watch(brain: Brain, owner: str) -> None:
    """Hand the brain back right away (on quit or when watching is switched off)."""
    path = _claim_path(brain)
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("owner") == owner:
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def watch_owner(kind: str) -> str:
    return f"{kind}:{os.getpid()}"


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
    live = [n for n in vibe.live_sessions(brain) if n["meta"].get("source") == "vector" and not elsewhere(n)]
    return live[-1] if live else None


def _vector_self_reported(brain: Brain) -> bool:
    """True when the assistant in Vector is reporting its own session (cairn vibe start --agent vector)."""
    return any(
        str(n["meta"].get("agent", "")).lower() == "vector" and n["meta"].get("source") != "vector"
        for n in vibe.live_sessions(brain) if not elsewhere(n)
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


# ------------------------------------------------------------ this computer's load


def machine_note(reading: dict) -> tuple[str, str]:
    """(path, text) of the note that carries a `machine.snapshot()` reading to readers that only have
    the notes, such as a phone opening the brain from GitHub. One note per computer model."""
    def text(value) -> str:
        return "" if value is None else str(value)

    name = text(reading.get("name")) or "This computer"
    reasons = "; ".join(reading.get("reasons") or [])
    top = "; ".join(reading.get("top") or [])
    meta = {
        "title": name,
        "type": "note",
        "tags": ["machine"],
        "machine_at": text(reading.get("at")),
        "machine_cpu": text(reading.get("cpu")),
        "machine_cores": text(reading.get("cores")),
        "machine_ram_gb": text(reading.get("ram_gb")),
        "load": text(reading.get("level")),
        "load_cpu": text(reading.get("cpu_pct")),
        "load_ram": text(reading.get("ram_pct")),
        "load_commit": text(reading.get("commit_pct")),
        "load_power": text(reading.get("power")),
        "machine_uptime": text(reading.get("uptime")),
        "load_why": reasons,
        "load_top": top,
    }
    lines = [
        "Written by the Cairn session watcher while an AI coding session is live, so the Vibe page can show",
        "how loaded this computer was. It is the whole computer, not one session. Rewritten on every change.",
        "",
        f"- {meta['machine_cpu']}, {meta['machine_cores']} cores, {meta['machine_ram_gb']} GB memory",
        f"- {meta['load'].capitalize()} load at {meta['machine_at'].replace('T', ' ')}" + (f": {reasons}" if reasons else ""),
    ]
    lines += [f"- {meta['load_power'].capitalize()}"] if meta["load_power"] else []
    lines += [f"- {meta['machine_uptime'].capitalize()}"] if meta["machine_uptime"] else []
    lines += [f"- Using the most: {top}"] if top else []
    path = f"{MACHINE_FOLDER}/{core.slugify(name) or 'this-computer'}.md"
    return path, core.serialize_note(meta, "\n".join(lines) + "\n")


def _machine_changed(old: dict, reading: dict) -> bool:
    """Worth writing again: another level, CPU or memory moved a good way, or the note is getting old."""
    if str(old.get("load", "")) != str(reading.get("level", "")):
        return True
    for key, now in (("load_cpu", reading.get("cpu_pct")), ("load_ram", reading.get("ram_pct"))):
        try:
            if now is not None and abs(int(old.get(key)) - int(now)) >= MACHINE_MOVE_POINTS:
                return True
        except (TypeError, ValueError):
            return True  # nothing comparable was written before
    try:
        written = dt.datetime.fromisoformat(str(old.get("machine_at", "")))
        age = dt.datetime.fromisoformat(str(reading.get("at", ""))) - written
    except ValueError:
        return True
    return abs(age.total_seconds()) >= MACHINE_NOTE_MINUTES * 60


def sync_machine(brain: Brain, reading: dict | None = None) -> bool:
    """Note this computer's load while a session is live. True when the note was written.
    Quiet on purpose: an idle brain, an unreadable machine and an unchanged reading write nothing."""
    if not any(not elsewhere(note) for note in vibe.live_sessions(brain)):
        return False  # nothing live on this computer
    reading = machine.snapshot() if reading is None else reading
    if not reading:
        return False
    path, text = machine_note(reading)
    try:
        if not brain.resolve(path).exists():
            brain.write(path, text)
            return True
        current = brain.read(path)
        if not _machine_changed(core.parse_note(current["text"])["meta"], reading):
            return False
        brain.write(path, text, current["mtime"])
        return True
    except (ConflictError, OSError):  # edited or synced under us: the next pass writes it
        return False


# ------------------------------------------------------------ the loop


def watch_once(brain: Brain, *, opencode: bool = True, vector: bool = True, everywhere: bool = False,
               days: int = DAYS, idle_minutes: int = IDLE_MINUTES, now_ms: int | None = None,
               vector_names=VECTOR_PROCESSES, db_path: Path | None = None, running: bool | None = None,
               git: str | None = None, last_reply: bool = False, laptop: bool = False,
               github: bool = False, github_accounts=(), repo_copy: bool = False) -> list[str]:
    """One pass over every source. Returns human-readable lines describing what changed."""
    now_ms = _now_ms() if now_ms is None else now_ms
    messages = []
    if opencode:
        path = db_path or opencode_db_path()
        # The first pass on a computer takes every session OpenCode still has, so its history arrives whole.
        # After that (or with --days N given once it has run) only the last `days` are read. --days 0 reads all.
        device = machine.device_name()
        history = _load_state(brain).get(HISTORY_KEY)
        first = path.is_file() and not (isinstance(history, dict) and history.get(device))
        since_ms = 0 if first or days <= 0 else now_ms - days * 86_400_000
        sessions = read_opencode(path, since_ms, "" if everywhere else str(brain.root))
        sessions = rollup_batches(sessions, _external_index(brain, "opencode"))
        pending = needs_refresh(brain, sessions, now_ms, idle_minutes)
        attach_commits(pending, git)
        attach_details(path, pending, last_reply)
        counts = sync_opencode(brain, pending, now_ms, idle_minutes)
        if counts["created"] or counts["updated"]:
            messages.append(f"OpenCode: {counts['created']} new, {counts['updated']} updated session(s)")
        # Only where OpenCode lives: on another device the database is simply not there.
        gone = close_missing_opencode(brain, [s["id"] for s in sessions], now_ms, idle_minutes) if path.is_file() else 0
        if gone:
            messages.append(f"OpenCode: {gone} session(s) ended, no longer in OpenCode")
        if first:
            state = _load_state(brain)
            done = state.get(HISTORY_KEY)
            state[HISTORY_KEY] = {**(done if isinstance(done, dict) else {}), device: _stamp(now_ms)}
            _save_state(brain, state)
    if vector:
        is_running = process_running(vector_names) if running is None else running
        action = sync_vector(brain, is_running, now_ms, idle_minutes)
        if action == "started":
            messages.append("Vector: session started (app open and the repo changed)")
        elif action == "ended":
            messages.append("Vector: session ended")
    silent = close_silent_reported(brain, now_ms)
    if silent:
        messages.append(f"Sessions: {silent} ended after {REPORTED_IDLE_MINUTES // 60} h without a step")
    if laptop and sync_machine(brain):
        messages.append("Laptop: load noted in machines/")
    if github and github_usage.sync(brain):
        messages.append(f"GitHub: Actions minutes noted in {github_usage.FOLDER}/")
    # Other accounts are asked for by name, so they are noted even where the main one is left to another computer.
    for account in github_usage.sync_accounts(brain, github_accounts):
        messages.append(f"GitHub: Actions minutes of {account} noted in {github_usage.FOLDER}/{account}/")
    if repo_copy and github_copy.sync_soon(brain):
        messages.append("GitHub: bringing this computer's copy of your repos up to date")
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
    owner = watch_owner("cli")
    standing_by = False
    try:
        while True:
            stamp = dt.datetime.now().strftime("%H:%M")
            if not claim_watch(brain, owner):
                if not standing_by:
                    echo(f"{stamp} Another watcher (the Cairn app or a second terminal) is updating this brain; standing by.")
                standing_by = True
            else:
                if standing_by:
                    echo(f"{stamp} Taking over: the other watcher stopped.")
                standing_by = False
                try:
                    for message in watch_once(brain, **options):
                        echo(f"{stamp} {message}")
                except WatchError as error:
                    echo(f"warning: {error}")
            time.sleep(max(5, every))
    finally:
        release_watch(brain, owner)


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

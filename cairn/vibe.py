"""Vibe sessions: live progress and results of AI-assisted coding, kept as plain Markdown.

A session is `brain/sessions/YYYY-MM-DD-slug.md` with `status: live` while you work and a log of
timestamped steps (`- 14:02 pass: pytest (12s)`). Your assistant appends to it through the CLI
(`cairn vibe log`, `cairn vibe check -- pytest`), the app shows it as it happens, and `cairn vibe end`
records the result and the git churn. Feedback is `brain/feedback/slug.md` with an optional 1-5 rating.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shlex
import subprocess
import time

from . import core
from .brain import Brain

RESULTS = ("shipped", "partial", "dropped")
MAX_COMMITS = 20
MAX_CHANGED = 50


class VibeError(RuntimeError):
    pass


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M")


def _clock() -> str:
    return dt.datetime.now().strftime("%H:%M")


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


# ------------------------------------------------------------ files


def vibe_files(brain: Brain) -> list[dict]:
    """Only the session and feedback notes: cheap enough to poll every few seconds."""
    out = []
    for folder in (core.FOLDERS["session"], core.FOLDERS["feedback"]):
        base = brain.dir / folder
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            try:
                out.append(brain.read(brain.relative(path)))
            except (OSError, UnicodeDecodeError):
                continue
    return out


def _notes(brain: Brain) -> list[dict]:
    return [core.note_from_file(f["path"], f["text"], f["mtime"]) for f in vibe_files(brain)]


def live_sessions(brain: Brain) -> list[dict]:
    """Every live session, oldest first. Parallel agents (one per terminal or worktree) each keep one."""
    live = [n for n in _notes(brain) if n["type"] == "session" and n["status"] == "live"]
    return sorted(live, key=lambda n: (str(n["meta"].get("started", "")), n["path"]))


def live_session(brain: Brain, slug: str = "") -> dict | None:
    """The session to write to: `slug` (or $CAIRN_SESSION) if given, else the newest live one."""
    live = live_sessions(brain)
    wanted = (slug or os.environ.get("CAIRN_SESSION", "")).strip().removesuffix(".md").rsplit("/", 1)[-1]
    if wanted:
        return next((n for n in live if n["slug"] == wanted), None)
    return live[-1] if live else None


def _require_live(brain: Brain, slug: str = "") -> dict:
    session = live_session(brain, slug)
    if session:
        return session
    wanted = slug or os.environ.get("CAIRN_SESSION", "")
    if wanted:
        raise VibeError(f"No live session called {wanted!r}. Live now: {', '.join(n['slug'] for n in live_sessions(brain)) or 'none'}")
    raise VibeError('No live session. Start one with: cairn vibe start "what you are building"')


def _save(brain: Brain, note: dict, changes: dict | None = None, line: str = "") -> dict:
    current = brain.read(note["path"])
    body = None
    if line:
        body = core.parse_note(current["text"])["body"].rstrip() + f"\n- {_clock()} {line}\n"
    text = core.update_note_text(current["text"], changes or {}, body)
    brain.write(note["path"], text, current["mtime"])
    return brain.read(note["path"])


# ------------------------------------------------------------ session lifecycle


def start(brain: Brain, title: str, task: str = "", agent: str = "") -> dict:
    title = _one_line(title)
    if not title:
        raise VibeError("Give the session a goal, e.g. cairn vibe start \"add dark mode\"")
    today = dt.date.today().isoformat()
    existing = [f["path"] for f in brain.files()]
    path = core.note_path("session", title, existing, today)
    meta = {"title": title, "type": "session", "status": "live", "tags": [], "created": today, "started": now_stamp()}
    if task:
        meta["task"] = f"[[{task.strip().removeprefix('[[').removesuffix(']]')}]]"
    if agent:
        meta["agent"] = _one_line(agent)
    head = _head(brain)
    if head:
        meta["base"] = head
    body = f"## Goal\n\n{title}\n\n## Log\n\n- {_clock()} start: {title}\n"
    brain.write(path, core.serialize_note(meta, body), None)
    return brain.read(path)


def log(brain: Brain, text: str, kind: str = "note", session: str = "") -> dict:
    if kind not in core.LOG_KINDS:
        raise VibeError(f"Unknown kind {kind!r}; use one of {', '.join(core.LOG_KINDS)}")
    text = _one_line(text)
    if not text:
        raise VibeError("Nothing to log.")
    return _save(brain, _require_live(brain, session), line=f"{core.log_kind(text, kind)}: {text}")


def check(brain: Brain, command: list[str], echo=print, session_slug: str = "") -> tuple[int, dict]:
    """Run a command (tests, build, lint), stream its output and log pass/fail with its last line."""
    session = _require_live(brain, session_slug)
    if not command:
        raise VibeError("Give a command to run, e.g. cairn vibe check -- python -m pytest")
    shown = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    started = time.monotonic()
    last = ""
    process = subprocess.Popen(
        shown, shell=True, cwd=str(brain.root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        echo(line.rstrip("\n"))
        if line.strip():
            last = line.strip()
    code = process.wait()
    seconds = int(time.monotonic() - started + 0.5)
    summary = f"{shown} ({seconds}s)"
    if last:
        summary += f" · {_one_line(last)[:120]}"
    if code:
        summary += f" · exit {code}"
    saved = _save(brain, session, line=f"{'pass' if code == 0 else 'fail'}: {summary}")
    return code, saved


def end(brain: Brain, result: str = "shipped", summary: str = "", session_slug: str = "") -> dict:
    if result not in RESULTS:
        raise VibeError(f"Result must be one of {', '.join(RESULTS)}")
    session = _require_live(brain, session_slug)
    changes = {"status": result, "ended": now_stamp()}
    activity = git_activity(brain, str(session["meta"].get("started", "")), str(session["meta"].get("base", "")))
    if activity["available"]:
        commits = activity["commits"]
        changes["commits"] = str(len(commits))
        changes["added"] = str(sum(c["added"] for c in commits) + activity["added"])
        changes["removed"] = str(sum(c["removed"] for c in commits) + activity["removed"])
    note = result + (f": {_one_line(summary)}" if summary else "")
    return _save(brain, session, changes, line=f"end: {note}")


def add_feedback(brain: Brain, text: str, rating: int | None = None, source: str = "", about: str = "") -> dict:
    text = text.strip()
    if not text:
        raise VibeError("What did they say?")
    if rating is not None and not 1 <= rating <= 5:
        raise VibeError("Rating is 1 to 5.")
    first = _one_line(text.split("\n", 1)[0])
    title = first if len(first) <= 80 else first[:77].rstrip() + "..."
    today = dt.date.today().isoformat()
    if not about:
        sessions = sorted(
            (n for n in _notes(brain) if n["type"] == "session"),
            key=lambda n: (str(n["meta"].get("started", "")), n["path"]),
        )
        about = sessions[-1]["slug"] if sessions else ""
    meta = {"title": title, "type": "feedback", "status": "new", "tags": [], "created": today}
    if source:
        meta["from"] = _one_line(source)
    if rating is not None:
        meta["rating"] = str(rating)
    if about:
        meta["about"] = f"[[{about.strip().removeprefix('[[').removesuffix(']]')}]]"
    body = text + "\n" if text != title else ""
    existing = [f["path"] for f in brain.files()]
    path = core.note_path("feedback", title, existing, today)
    brain.write(path, core.serialize_note(meta, body), None)
    return brain.read(path)


# ------------------------------------------------------------ live git activity


def _numstat(lines: list[str]) -> tuple[int, int]:
    added = removed = 0
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            added += int(parts[0])
            removed += int(parts[1])
    return added, removed


def _head(brain: Brain) -> str:
    if not brain.git_available():
        return ""
    result = brain._git("rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def git_activity(brain: Brain, since: str = "", base: str = "") -> dict:
    """Uncommitted churn plus the commits made during a session, for the whole repo.

    `base` is the commit the session started from (exact); `since` its start time (fallback for sessions
    started outside a git repo or by hand)."""
    if not brain.git_available():
        return {"available": False, "branch": "", "head": "", "changed": [], "added": 0, "removed": 0, "commits": []}
    branch = brain._git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or "detached"
    status = brain._git("status", "--porcelain", "-uall").stdout.splitlines()
    changed = [line[3:].strip().strip('"') for line in status if len(line) > 3]
    diff = brain._git("diff", "--numstat", "HEAD")
    added, removed = _numstat(diff.stdout.splitlines()) if diff.returncode == 0 else (0, 0)
    commits: list[dict] = []
    head = _head(brain)
    if base and re.fullmatch(r"[0-9a-f]{7,64}", base) and brain._git("cat-file", "-e", f"{base}^{{commit}}").returncode == 0:
        scope = [f"{base}..HEAD"]
    elif core.stamp_minutes(since) is not None:
        scope = [f"--since={since.replace('T', ' ')}"]
    else:
        scope = []
    if scope and head:
        result = brain._git("log", *scope, f"-{MAX_COMMITS}", "--numstat", "--pretty=format:\x1e%h\t%ct\t%s")
        if result.returncode == 0:
            for block in result.stdout.split("\x1e")[1:]:
                head, *rest = block.strip("\n").split("\n")
                parts = head.split("\t", 2)
                if len(parts) < 3:
                    continue
                plus, minus = _numstat(rest)
                commits.append({
                    "hash": parts[0], "time": int(parts[1]) * 1000, "subject": parts[2],
                    "added": plus, "removed": minus,
                })
    return {
        "available": True,
        "branch": branch,
        "head": head,
        "changed": changed[:MAX_CHANGED],
        "changedCount": len(changed),
        "added": added,
        "removed": removed,
        "commits": commits,
    }


def stats_text(brain: Brain) -> str:
    stats = core.vibe_stats(brain.notes(), dt.date.today().isoformat())
    live = live_sessions(brain)
    hours, minutes = divmod(stats["minutes"], 60)
    skills = ", ".join(f"{name} x{count}" for name, count in sorted(stats["skills"].items(), key=lambda kv: -kv[1]))
    lines = [
        "Live now: " + ("; ".join(f"{n['title']} [{n['slug']}] since {n['meta'].get('started', '?')}" for n in live) or "nothing"),
        f"Sessions: {stats['sessions']} · shipped {stats['shipped']} · partial {stats['partial']} · dropped {stats['dropped']}"
        + (f" · done {stats['done']}" if stats["done"] else "")
        + (f" · ship rate {stats['ship_rate']}%" if stats["ship_rate"] is not None else ""),
        f"Time vibing: {hours}h {minutes:02d}m"
        + (f" ({stats['agent_minutes'] // 60}h {stats['agent_minutes'] % 60:02d}m across sessions, {stats['parallel']}x parallel)"
           if stats["parallel"] and stats["parallel"] >= 1.1 else "")
        + f" · prompts {stats['prompts']}",
        f"Checks: {stats['passed']} passed · {stats['failed']} failed"
        + (f" · {stats['pass_rate']}% green" if stats["pass_rate"] is not None else ""),
        f"Code: {stats['commits']} commits · +{stats['added']} −{stats['removed']}",
        f"Feedback: {stats['feedback']} · {stats['feedback_open']} open · {stats['feedback_addressed']} addressed"
        + (f" · avg {stats['rating']}/5" if stats["rating"] is not None else ""),
        f"Skills: {skills or 'none yet'}",
    ]
    return "\n".join(lines)

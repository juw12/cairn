"""Autopilot: give Cairn a goal once; it runs your coding agent round after round until it is done.

No "go", "done?" or "continue": after every round the agent reports `STATUS: DONE | CONTINUE |
BLOCKED <reason>` and `NEXT: <step>`, and the autopilot calls it again with the next step. When a
goal is done it gets a couple of improvement rounds, then the next `#queue #auto` task is pulled
from the brain. It stops only for hard stops (money, sending to people, final submit, passwords)
and tells you with a desktop notification. Your comments are optional steering.

The first prompt of every goal is the precedent brief (see precedent.py), so the agent starts
with what you asked for after similar goals, your profile and the working loop.

Engines: `opencode` (`opencode run --format json`, resumes the same session each round) or any
command that takes the prompt as its last argument (`claude -p`, `codex exec`, ...).
Off until you switch it on (Autopilot page or `cairn autopilot on`): it runs an agent that edits
files by itself. State: ~/.cairn/autopilot (jobs.json, settings.json, logs/). Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import precedent
from .brain import Brain

DEFAULT_SETTINGS = {
    "enabled": False,
    "engine": "opencode",
    "command": "",
    "opencode_flags": "--standalone --auto",
    "workdir_root": "",
    "round_timeout_min": 45,
    "max_rounds_per_goal": 40,
    "improve_rounds_after_done": 2,
    "auto_pull_queue": True,
}

STATUS_PATTERN = re.compile(r"STATUS:\s*(DONE|CONTINUE|BLOCKED)\b[:\s-]*(.*)", re.IGNORECASE)
NEXT_PATTERN = re.compile(r"NEXT:\s*(.+)", re.IGNORECASE)
PROXY_ERRORS = ("UnsupportedContentType", "ECONNREFUSED", "407", "Proxy Authentication")
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
HEARTBEAT_SECONDS = 20
STALE_SECONDS = 90

PROTOCOL = """
SCOPE: work ONLY on the goal above. Do not start other goals, queue items or projects -
the autopilot schedules those itself. Improvements must be improvements OF this goal.
At the very end of EVERY reply write exactly these two lines:
STATUS: DONE            (goal fully done AND verified the way the owner would check it)
   or STATUS: CONTINUE  (more work remains; you will be called again automatically)
   or STATUS: BLOCKED <one-line reason>  (only for a hard stop: money, sending to other people,
      final submit, deleting data, a password/2FA/CAPTCHA only the owner can enter)
NEXT: <the single most valuable next step or improvement>
Never ask "should I...?" - decide using the precedents and profile, and write your assumption instead.
"""

LOCK = threading.RLock()


def state_dir() -> Path:
    return precedent.home_dir() / "autopilot"


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


# ---------------------------------------------------------------- settings and jobs


def settings() -> dict:
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in _load(state_dir() / "settings.json", {}).items() if k in DEFAULT_SETTINGS})
    return merged


def update_settings(changes: dict) -> dict:
    with LOCK:
        merged = settings()
        for key, value in changes.items():
            if key not in DEFAULT_SETTINGS:
                continue
            default = DEFAULT_SETTINGS[key]
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = max(1, int(value))
            else:
                value = str(value)
            merged[key] = value
        if merged["engine"] not in ("opencode", "command"):
            merged["engine"] = "opencode"
        _save(state_dir() / "settings.json", merged)
        return merged


def jobs() -> list[dict]:
    return _load(state_dir() / "jobs.json", [])


def _save_jobs(job_list: list[dict]) -> None:
    _save(state_dir() / "jobs.json", job_list)


def get_job(job_id: str) -> dict | None:
    return next((job for job in jobs() if job["id"] == job_id), None)


def update_job(job_id: str, **changes) -> dict | None:
    with LOCK:
        job_list = jobs()
        for job in job_list:
            if job["id"] == job_id:
                job.update(changes)
                job["updated"] = _now()
                _save_jobs(job_list)
                return job
    return None


def log_path(job_id: str) -> Path:
    return state_dir() / "logs" / f"{re.sub(r'[^a-zA-Z0-9_-]', '', job_id)}.log"


def append_log(job_id: str, text: str) -> None:
    path = log_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def read_log(job_id: str, since: int = 0) -> dict:
    path = log_path(job_id)
    data = path.read_bytes() if path.exists() else b""
    since = max(0, min(int(since or 0), len(data)))
    return {"text": data[since:].decode("utf-8", "replace"), "size": len(data)}


def default_workdir(goal: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower())[:40].strip("-") or "goal"
    root = settings()["workdir_root"].strip()
    base = Path(root) if root else Path.home() / "Documents" / "Cairn Autopilot"
    return str(base / f"{dt.datetime.now():%Y%m%d}-{slug}")


def create_job(goal: str, workdir: str = "", source: str = "app", queue_path: str = "") -> dict:
    goal = " ".join((goal or "").split())
    if not goal:
        raise ValueError("Give the goal.")
    job = {
        "id": uuid.uuid4().hex[:10],
        "goal": goal,
        "workdir": (workdir or "").strip() or default_workdir(goal),
        "status": "queued",
        "source": source,
        "queue_path": queue_path,
        "session_id": "",
        "rounds": 0,
        "improve_rounds": 0,
        "silent_rounds": 0,
        "pending_comments": [],
        "comments": [],
        "last_status": "",
        "blocked_reason": "",
        "next_hint": "",
        "summary": "",
        "created": _now(),
        "updated": _now(),
    }
    with LOCK:
        job_list = jobs()
        job_list.append(job)
        _save_jobs(job_list)
    append_log(job["id"], f"[{_now()}] Goal received: {goal}\nWorking folder: {job['workdir']}\n")
    return job


def add_comment(job_id: str, text: str) -> dict | None:
    text = (text or "").strip()
    with LOCK:
        job = get_job(job_id)
        if not job or not text:
            return None
        status = "queued" if job["status"] in ("blocked", "done", "stopped") else job["status"]
        append_log(job_id, f"\n[{_now()}] Your comment: {text}\n")
        return update_job(
            job_id,
            pending_comments=job["pending_comments"] + [text],
            comments=job["comments"] + [{"at": _now(), "text": text}],
            status=status,
            blocked_reason="",
        )


def delete_job(job_id: str) -> None:
    with LOCK:
        _save_jobs([job for job in jobs() if job["id"] != job_id])
    log_path(job_id).unlink(missing_ok=True)


# ---------------------------------------------------------------- prompts


def first_prompt(brain: Brain, job: dict) -> str:
    return f"""You are the owner's autopilot, working fully on your own. Nobody will answer questions.

GOAL: {job['goal']}
Working folder (create it if needed; put outputs here unless the goal names another place): {job['workdir']}

{precedent.brief(brain, job['goal'])}

Start now: write a short Definition of Done (literal ask + hidden asks + how you will prove each),
then do as much real work as you can in this turn.
{PROTOCOL}"""


def next_prompt(job: dict, mode: str) -> str:
    comments = ""
    if job["pending_comments"]:
        joined = "\n".join(f"- {text}" for text in job["pending_comments"])
        comments = f"\nNEW COMMENTS FROM THE OWNER (highest priority, adjust the plan):\n{joined}\n"
    if mode == "improve":
        body = (
            "The goal is marked done. Improve THIS goal's result the way the owner would ask next "
            "(see the profile and precedents) - only if it is a real, useful product. If it was a one-off "
            "task, a test, or nothing meaningful is left, change nothing and reply STATUS: DONE. "
            f"Suggested: {job['next_hint'] or 'pick the highest-value improvement'}."
        )
    else:
        body = f"Continue the goal. Last planned next step: {job['next_hint'] or 'continue the plan'}. Keep going without asking."
    return f"GOAL: {job['goal']}\n{comments}\n{body}\n{PROTOCOL}"


def parse_reply(text: str) -> tuple[str, str, str]:
    """(status, detail, next) from the last STATUS / NEXT lines of a reply."""
    status_match = None
    for status_match in STATUS_PATTERN.finditer(text or ""):
        pass
    next_match = None
    for next_match in NEXT_PATTERN.finditer(text or ""):
        pass
    status = status_match.group(1).upper() if status_match else ""
    detail = status_match.group(2).strip() if status_match else ""
    hint = next_match.group(1).strip() if next_match else ""
    return status, detail, hint


# ---------------------------------------------------------------- engine


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


class Engine:
    """Runs one turn of the coding agent and streams what it does into the job log."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.direct = False

    def env(self) -> dict:
        env = dict(os.environ)
        for key in PROXY_KEYS:
            value = env.get(key, "")
            if self.direct and value:
                env.pop(key, None)
                continue
            # A proxy on localhost that is not running would make every turn fail.
            match = re.match(r"https?://(localhost|127\.0\.0\.1):(\d+)", value)
            if match and not _port_open("127.0.0.1", int(match.group(2))):
                env.pop(key, None)
        return env

    def command(self, job: dict, prompt: str) -> list[str]:
        config = settings()
        if config["engine"] == "command" and config["command"].strip():
            # The prompt goes wherever {prompt} is written, or last when it isn't.
            parts = shlex.split(config["command"], posix=os.name != "nt")
            if any("{prompt}" in part for part in parts):
                return [part.replace("{prompt}", prompt) for part in parts]
            return parts + [prompt]
        exe = shutil.which("opencode") or "opencode"
        # --auto approves tool permissions (nobody is there to click), --standalone uses a private server.
        flags = shlex.split(config["opencode_flags"], posix=os.name != "nt")
        command = [exe, "run", *flags, "--format", "json", "--title", f"Autopilot: {job['goal'][:60]}"]
        if job["session_id"]:
            command += ["--session", job["session_id"]]
        return command + [prompt]

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()

    def run(self, job: dict, prompt: str) -> tuple[str, str, str]:
        """One turn. Returns (reply text, session id, error). Retries once without a dead proxy."""
        text, session_id, error, raw = self._run_once(job, prompt)
        if error and not text and not self.direct and any(marker in raw for marker in PROXY_ERRORS):
            self.direct = True
            append_log(job["id"], "  (the proxy blocked the AI gateway - retrying with a direct connection)\n")
            text, session_id, error, raw = self._run_once(job, prompt)
        if error and raw:
            append_log(job["id"], f"  engine said: {raw[-400:]}\n")
        return text, session_id, error

    def _run_once(self, job: dict, prompt: str) -> tuple[str, str, str, str]:
        workdir = Path(job["workdir"])
        workdir.mkdir(parents=True, exist_ok=True)
        json_events = settings()["engine"] == "opencode"
        texts: list[str] = []
        raw_errors: list[str] = []
        session_id = job["session_id"]
        try:
            self.process = subprocess.Popen(
                self.command(job, prompt),
                cwd=str(workdir),
                env=self.env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            return "", session_id, f"could not start the agent: {error}", str(error)
        timer = threading.Timer(settings()["round_timeout_min"] * 60, self.stop)
        timer.start()
        try:
            for line in self.process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                if not json_events:
                    texts.append(line)
                    append_log(job["id"], line + "\n")
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    raw_errors.append(line)
                    append_log(job["id"], line + "\n")
                    continue
                session_id = event.get("sessionID") or session_id
                part = event.get("part") or {}
                kind = event.get("type")
                if kind == "error":
                    raw_errors.append(json.dumps(event.get("error", event)))
                elif kind == "text" and part.get("text"):
                    texts.append(part["text"])
                    append_log(job["id"], part["text"].strip() + "\n")
                elif kind in ("tool_use", "tool"):
                    tool = part.get("tool") or part.get("name") or "tool"
                    state = part.get("state") or {}
                    tool_input = state.get("input") or {}
                    detail = ""
                    if isinstance(tool_input, dict):
                        for key in ("command", "filePath", "path", "url", "pattern", "description"):
                            if tool_input.get(key):
                                detail = str(tool_input[key])
                                break
                    title = (detail or state.get("title") or "").replace("\n", " ")[:160]
                    append_log(job["id"], f"  > {tool}: {title}\n")
            self.process.wait()
        finally:
            timer.cancel()
        code = self.process.returncode
        self.process = None
        error = f"the agent exited with code {code}" if code not in (0, None) else ""
        return "\n".join(texts), session_id, error, "\n".join(raw_errors)


def notify(title: str, message: str) -> None:
    """A desktop notification (Windows balloon); silently skipped elsewhere."""
    if os.name != "nt":
        return
    clean = lambda s: s.replace("'", "").replace("\n", " ")[:200]  # noqa: E731
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
        f"$n.ShowBalloonTip(8000,'{clean(title)}','{clean(message)}','Info');"
        "Start-Sleep 9;$n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        pass


# ---------------------------------------------------------------- the worker


class Autopilot:
    """One background worker per machine (a lock file with a heartbeat keeps a second Cairn out)."""

    def __init__(self, brain: Brain, engine: Engine | None = None, notifier=notify):
        self.brain = brain
        self.engine = engine or Engine()
        self.notify = notifier
        self.current: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.owner = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"

    # -- single worker per machine
    def _lock_path(self) -> Path:
        return state_dir() / "worker.lock"

    def claim(self) -> bool:
        path = self._lock_path()
        held = _load(path, {})
        fresh = held and time.time() - float(held.get("at", 0)) < STALE_SECONDS
        if fresh and held.get("owner") != self.owner:
            return False
        _save(path, {"owner": self.owner, "pid": os.getpid(), "at": time.time()})
        return True

    def release(self) -> None:
        if _load(self._lock_path(), {}).get("owner") == self.owner:
            self._lock_path().unlink(missing_ok=True)

    # -- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with LOCK:
            job_list = jobs()
            for job in job_list:
                if job["status"] == "running":
                    job["status"] = "queued"
            _save_jobs(job_list)
        self._thread = threading.Thread(target=self._loop, name="cairn-autopilot", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self.engine.stop()
        self.release()

    def stop_job(self, job_id: str) -> None:
        update_job(job_id, status="stopped")
        if self.current == job_id:
            self.engine.stop()
        append_log(job_id, f"\n[{_now()}] Stopped by you.\n")

    def status(self) -> dict:
        held = _load(self._lock_path(), {})
        running_here = held.get("owner") == self.owner and time.time() - float(held.get("at", 0)) < STALE_SECONDS
        elsewhere = bool(held) and not running_here and time.time() - float(held.get("at", 0)) < STALE_SECONDS
        return {"current": self.current, "worker": "here" if running_here else ("elsewhere" if elsewhere else "idle")}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not settings()["enabled"] or not self.claim():
                    self.release() if not settings()["enabled"] else None
                    self._stop.wait(5)
                    continue
                job = self.pick()
                if job is None:
                    self._stop.wait(5)
                    continue
                self.work_on(job)
            except Exception as error:  # noqa: BLE001 - the worker must survive anything
                sys.stderr.write(f"autopilot: {error}\n")
                self._stop.wait(10)
        self.release()

    def pick(self) -> dict | None:
        for job in jobs():
            if job["status"] in ("queued", "running"):
                return job
        if settings()["auto_pull_queue"]:
            return self.pull_from_queue()
        return None

    def pull_from_queue(self) -> dict | None:
        taken = {job.get("queue_path") for job in jobs() if job.get("queue_path")}
        item = precedent.queue_next(self.brain, auto_only=True, exclude=taken)
        if not item:
            return None
        return create_job(item["text"], source="queue", queue_path=item["path"])

    def work_on(self, job: dict) -> None:
        config = settings()
        mode = "first" if job["rounds"] == 0 else ("improve" if job["last_status"] == "DONE" else "continue")
        prompt = first_prompt(self.brain, job) if mode == "first" else next_prompt(job, mode)
        round_number = job["rounds"] + 1
        update_job(job["id"], status="running", pending_comments=[])
        self.current = job["id"]
        append_log(job["id"], f"\n=== Round {round_number} ({mode}) {_now()} ===\n")
        text, session_id, error = self.engine.run(job, prompt)
        self.current = None
        self.claim()
        job = get_job(job["id"])
        if job is None or job["status"] == "stopped":
            return
        status, detail, hint = parse_reply(text)
        changes = {
            "rounds": round_number,
            "session_id": session_id,
            "next_hint": hint or job["next_hint"],
            "summary": text.strip()[-1500:] or job["summary"],
            "last_status": status or "CONTINUE",
        }
        if error and not text:
            silent = job["silent_rounds"] + 1
            append_log(job["id"], f"[{_now()}] {error} (attempt {silent})\n")
            changes["silent_rounds"] = silent
            if silent >= 3:
                changes.update(status="blocked", blocked_reason=f"The agent keeps failing: {error}")
                self.notify("Cairn autopilot needs you", changes["blocked_reason"])
            else:
                changes["status"] = "queued"
                self._stop.wait(20)
            update_job(job["id"], **changes)
            return
        changes["silent_rounds"] = 0
        if status == "BLOCKED":
            changes.update(status="blocked", blocked_reason=detail or "needs your input")
            self.notify("Cairn autopilot needs you", f"{job['goal'][:60]}: {changes['blocked_reason']}")
        elif status == "DONE":
            improved = job["improve_rounds"] + (1 if mode == "improve" else 0)
            changes["improve_rounds"] = improved
            if improved >= config["improve_rounds_after_done"] or round_number >= config["max_rounds_per_goal"]:
                changes["status"] = "done"
                update_job(job["id"], **changes)
                self.finish(get_job(job["id"]))
                return
            changes["status"] = "queued"
        elif round_number >= config["max_rounds_per_goal"]:
            changes.update(status="blocked", blocked_reason=f"Reached {round_number} rounds; comment to keep going")
        else:
            changes["status"] = "queued"
        update_job(job["id"], **changes)

    def finish(self, job: dict) -> None:
        summary = " ".join(job.get("summary", "").split())[-400:]
        if job.get("queue_path"):
            try:
                precedent.queue_done(self.brain, job["queue_path"], f"autopilot, {job['rounds']} rounds. {summary[-240:]}")
            except Exception as error:  # noqa: BLE001 - the task may have been moved or edited
                append_log(job["id"], f"  (could not mark the queue task done: {error})\n")
        corrections = " | ".join(comment["text"] for comment in job.get("comments", []))
        precedent.log_lesson(job["goal"], summary, corrections)
        append_log(job["id"], f"\n[{_now()}] Done after {job['rounds']} rounds.\n")
        self.notify("Cairn autopilot finished", job["goal"][:120])


def state(worker: Autopilot | None = None) -> dict:
    return {
        "jobs": list(reversed(jobs())),
        "settings": settings(),
        "worker": worker.status() if worker else {"current": None, "worker": "idle"},
        "engine_found": bool(shutil.which("opencode")) if settings()["engine"] == "opencode" else True,
        "state_dir": str(state_dir()),
    }


def import_legacy_jobs(app_dir: Path) -> int:
    """Bring the old Precedent Agent app's job history (and logs) over. Running jobs come in stopped."""
    old = _load(Path(app_dir) / "state" / "jobs.json", [])
    known = {job["id"] for job in jobs()}
    added = 0
    with LOCK:
        job_list = jobs()
        for job in old:
            if job.get("id") in known:
                continue
            job = dict(job)
            job.setdefault("queue_path", "")
            job.pop("queue_line", None)
            if job.get("status") in ("running", "queued"):
                job["status"] = "stopped"
            for key, value in (("pending_comments", []), ("comments", []), ("summary", ""), ("next_hint", ""),
                               ("blocked_reason", ""), ("session_id", ""), ("rounds", 0), ("improve_rounds", 0),
                               ("silent_rounds", 0), ("last_status", ""), ("workdir", ""), ("source", "app")):
                job.setdefault(key, value)
            job_list.append(job)
            source_log = Path(app_dir) / "state" / "logs" / f"{job['id']}.log"
            if source_log.exists():
                log_path(job["id"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_log, log_path(job["id"]))
            added += 1
        _save_jobs(job_list)
    return added

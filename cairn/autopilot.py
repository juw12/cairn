"""Autopilot (Cairn Pro): hand a goal over once, an AI coding agent carries it to done.

You type what you want. Autopilot turns it into a task on the board, reads the brain for
precedents (CONTEXT.md, decisions, similar notes, your `autopilot.md` defaults), then runs a
local coding agent (OpenCode) round after round on its own - no "go", "done?" or "continue" -
until the goal is done and verified. Progress is written into the task note, so the brain
remembers what happened. Comments are optional steering. It stops only for hard stops
(money, sending to people, final submit, deleting data, credentials).

Licensing: Autopilot is Cairn Pro. Keys are sold on Gumroad and checked with Gumroad's public
license API (no server of ours). One free trial goal per machine. Everything else in Cairn
stays free. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import core
from .brain import Brain, ConflictError

# ------------------------------------------------------------------ plan / license

GUMROAD_PRODUCT_ID = os.environ.get("CAIRN_PRO_PRODUCT", "")  # set once the Gumroad product exists
BUY_URL = os.environ.get("CAIRN_PRO_BUY_URL", "https://juw12.gumroad.com/l/cairn-pro")
VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"
TRIAL_GOALS = 1
REVERIFY_AFTER = timedelta(days=7)
OFFLINE_GRACE = timedelta(days=30)


class PlanError(Exception):
    """Autopilot needs Cairn Pro (or a free trial goal)."""


def config_dir() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or (Path.home() / ".cairn"))


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def gumroad_verify(key: str, product_id: str) -> dict:
    """Ask Gumroad whether a license key is a live purchase. Returns {valid, email, reason}."""
    data = urllib.parse.urlencode({
        "product_id": product_id,
        "license_key": key,
        "increment_uses_count": "false",
    }).encode("utf-8")
    request = urllib.request.Request(VERIFY_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except (ValueError, OSError):
            raise ConnectionError(f"Gumroad answered {error.code}") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise ConnectionError(f"could not reach Gumroad: {error}") from error
    if not payload.get("success"):
        return {"valid": False, "email": "", "reason": payload.get("message") or "That key is not valid."}
    purchase = payload.get("purchase") or {}
    for flag, reason in (
        ("refunded", "This purchase was refunded."),
        ("chargebacked", "This purchase was charged back."),
        ("subscription_cancelled_at", "The subscription was cancelled."),
        ("subscription_failed_at", "The last subscription payment failed."),
    ):
        if purchase.get(flag):
            return {"valid": False, "email": purchase.get("email", ""), "reason": reason}
    return {"valid": True, "email": purchase.get("email", ""), "reason": ""}


class License:
    """Cairn Pro state for this machine, kept in ~/.cairn (never inside a brain or repo)."""

    def __init__(self, home: Path | None = None, verify=gumroad_verify, product_id: str | None = None):
        self.home = home or config_dir()
        self.verify = verify
        self.product_id = GUMROAD_PRODUCT_ID if product_id is None else product_id
        self.lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.home / "license.json"

    @property
    def trial_path(self) -> Path:
        return self.home / "trial.json"

    def _record(self) -> dict:
        return _load(self.path, {})

    def trial_left(self) -> int:
        used = int(_load(self.trial_path, {}).get("goals_used", 0))
        return max(0, TRIAL_GOALS - used)

    def use_trial(self) -> None:
        data = _load(self.trial_path, {})
        data["goals_used"] = int(data.get("goals_used", 0)) + 1
        _save(self.trial_path, data)

    def is_pro(self) -> bool:
        record = self._record()
        if not record.get("key") or not record.get("valid"):
            return False
        checked = datetime.fromisoformat(record.get("checked", "1970-01-01T00:00:00"))
        if datetime.now() - checked < REVERIFY_AFTER or not self.product_id:
            return True
        try:
            result = self.verify(record["key"], self.product_id)
        except ConnectionError:
            return datetime.now() - checked < OFFLINE_GRACE
        record.update(valid=result["valid"], checked=datetime.now().isoformat(timespec="seconds"), reason=result["reason"])
        _save(self.path, record)
        return result["valid"]

    def status(self) -> dict:
        pro = self.is_pro()
        record = self._record()
        return {
            "plan": "pro" if pro else ("trial" if self.trial_left() else "free"),
            "pro": pro,
            "email": record.get("email", "") if pro else "",
            "trialLeft": self.trial_left(),
            "buyUrl": BUY_URL,
            "checkoutReady": bool(self.product_id),
            "reason": "" if pro else record.get("reason", ""),
        }

    def activate(self, key: str) -> dict:
        key = key.strip()
        if not key:
            raise ValueError("Paste the license key from your Gumroad receipt.")
        if not self.product_id:
            raise ValueError("Cairn Pro checkout is not set up yet (no Gumroad product id).")
        with self.lock:
            result = self.verify(key, self.product_id)
            if not result["valid"]:
                raise ValueError(result["reason"] or "That key is not valid.")
            _save(self.path, {
                "key": key,
                "valid": True,
                "email": result["email"],
                "checked": datetime.now().isoformat(timespec="seconds"),
            })
        return self.status()

    def deactivate(self) -> dict:
        if self.path.exists():
            self.path.unlink()
        return self.status()

    def can_start(self) -> str:
        """'pro', 'trial', or raise PlanError."""
        if self.is_pro():
            return "pro"
        if self.trial_left() > 0:
            return "trial"
        raise PlanError("Autopilot is part of Cairn Pro. Your free trial goal is used up.")


# ------------------------------------------------------------------ engine (OpenCode)

PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
PROXY_ERRORS = ("UnsupportedContentType", "ECONNREFUSED", "407", "Proxy Authentication")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


class OpenCodeEngine:
    """Runs one agent turn with `opencode run --format json` and streams its events."""

    name = "OpenCode"

    def __init__(self, timeout_min: int = 45):
        self.timeout_s = timeout_min * 60
        self.direct = False
        self.process = None

    def available(self) -> bool:
        return shutil.which("opencode") is not None

    def _env(self) -> dict:
        env = dict(os.environ)
        for key in PROXY_KEYS:
            value = env.get(key, "")
            local = re.match(r"https?://(localhost|127\.0\.0\.1):(\d+)", value)
            if self.direct or (local and not _port_open(int(local.group(2)))):
                env.pop(key, None)
        return env

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()

    def run(self, prompt: str, session_id: str, cwd: Path, on_line) -> tuple[str, str, str]:
        text, session_id, error, raw = self._run_once(prompt, session_id, cwd, on_line)
        if error and not text and not self.direct and any(marker in raw for marker in PROXY_ERRORS):
            self.direct = True
            on_line("(a proxy blocked the AI service - retrying with a direct connection)")
            text, session_id, error, raw = self._run_once(prompt, session_id, cwd, on_line)
        if error and raw:
            on_line(f"engine said: {raw[-400:]}")
        return text, session_id, error

    def _run_once(self, prompt, session_id, cwd, on_line):
        exe = shutil.which("opencode")
        if not exe:
            return "", session_id, "OpenCode is not installed (https://opencode.ai)", ""
        command = [exe, "run", "--standalone", "--auto", "--format", "json", "--title", "Cairn Autopilot"]
        if session_id:
            command += ["--session", session_id]
        command.append(prompt)
        self.process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=self._env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        timer = threading.Timer(self.timeout_s, self.process.kill)
        timer.start()
        texts, raw = [], []
        try:
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    raw.append(line)
                    on_line(line)
                    continue
                session_id = event.get("sessionID") or session_id
                part = event.get("part") or {}
                kind = event.get("type")
                if kind == "error":
                    raw.append(json.dumps(event.get("error", event)))
                elif kind == "text" and part.get("text"):
                    texts.append(part["text"])
                    on_line(part["text"].strip())
                elif kind in ("tool_use", "tool"):
                    on_line("  > " + describe_tool(part))
            self.process.wait()
        finally:
            timer.cancel()
        code = self.process.returncode
        error = f"engine exited with code {code}" if code not in (0, None) else ""
        return "\n".join(texts), session_id, error, "\n".join(raw)


def describe_tool(part: dict) -> str:
    tool = part.get("tool") or part.get("name") or "tool"
    state = part.get("state") or {}
    tool_input = state.get("input") or {}
    detail = ""
    if isinstance(tool_input, dict):
        for key in ("command", "filePath", "path", "url", "pattern", "description"):
            if tool_input.get(key):
                detail = str(tool_input[key])
                break
    detail = (detail or state.get("title") or "").replace("\n", " ")[:160]
    return f"{tool}: {detail}" if detail else tool


# ------------------------------------------------------------------ autopilot

STATUS_RE = re.compile(r"STATUS:\s*(DONE|CONTINUE|BLOCKED)\b[:\s-]*(.*)", re.IGNORECASE)
NEXT_RE = re.compile(r"NEXT:\s*(.+)", re.IGNORECASE)
WORD_RE = re.compile(r"[a-z0-9]{3,}|[\u4e00-\u9fff]")
STOP_WORDS = set("the and for with that this from into make your have will can not but are was".split())

PROTOCOL = """SCOPE: work only on this goal, inside this project. Do not start other goals.
HARD STOPS (never do these yourself - reply STATUS: BLOCKED and say what you need): spending
money, sending anything to other people, final submit on company systems, deleting data you did
not create, typing passwords / 2FA / CAPTCHA.
Never ask "should I...?": decide from the brain's precedents and write your assumption.
End EVERY reply with exactly these two lines:
STATUS: DONE | STATUS: CONTINUE | STATUS: BLOCKED <reason>
NEXT: <the single most valuable next step>"""


def last_match(pattern: re.Pattern, text: str):
    found = None
    for found in pattern.finditer(text):
        pass
    return found


def parse_reply(text: str) -> tuple[str, str, str]:
    status_match = last_match(STATUS_RE, text)
    next_match = last_match(NEXT_RE, text)
    status = status_match.group(1).upper() if status_match else ""
    detail = status_match.group(2).strip() if status_match else ""
    hint = next_match.group(1).strip() if next_match else ""
    return status, detail, hint


class Autopilot:
    def __init__(self, brain: Brain, engine=None, license: License | None = None,
                 home: Path | None = None, max_rounds: int = 30, improve_rounds: int = 1):
        self.brain = brain
        self.engine = engine or OpenCodeEngine()
        self.license = license or License(home)
        self.home = home or config_dir()
        self.max_rounds = max_rounds
        self.improve_rounds = improve_rounds
        self.lock = threading.RLock()
        self.worker = None
        self.current = None
        project_key = hashlib.sha1(str(brain.root.resolve()).encode("utf-8")).hexdigest()[:12]
        self.dir = self.home / "autopilot" / project_key

    # ---------------------------------------------------------- storage

    def jobs(self) -> list[dict]:
        return _load(self.dir / "jobs.json", [])

    def _save_jobs(self, jobs: list[dict]) -> None:
        _save(self.dir / "jobs.json", jobs)

    def get(self, job_id: str) -> dict | None:
        return next((job for job in self.jobs() if job["id"] == job_id), None)

    def _update(self, job_id: str, **changes) -> dict | None:
        with self.lock:
            jobs = self.jobs()
            for job in jobs:
                if job["id"] == job_id:
                    job.update(changes)
                    job["updated"] = datetime.now().isoformat(timespec="seconds")
                    self._save_jobs(jobs)
                    return job
        return None

    def log_path(self, job_id: str) -> Path:
        return self.dir / "logs" / f"{job_id}.log"

    def log(self, job_id: str, text: str) -> None:
        path = self.log_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text.rstrip("\n") + "\n")

    def read_log(self, job_id: str, since: int = 0) -> dict:
        path = self.log_path(job_id)
        data = path.read_bytes() if path.exists() else b""
        return {"text": data[since:].decode("utf-8", "replace"), "size": len(data)}

    # ---------------------------------------------------------- task note in the brain

    def _note_append(self, path: str, line: str, status: str | None = None) -> None:
        for _ in range(3):
            try:
                note = self.brain.read(path)
            except Exception:
                return
            text = note["text"]
            if status:
                text = re.sub(r"(?m)^status:\s*\S+", f"status: {status}", text, count=1)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            text = text.rstrip("\n") + f"\n- {stamp} {line}\n"
            try:
                self.brain.write(path, text, note["mtime"])
                return
            except ConflictError:
                time.sleep(0.2)

    # ---------------------------------------------------------- public actions

    def state(self) -> dict:
        return {
            "license": self.license.status(),
            "engine": {"name": self.engine.name, "available": self.engine.available()},
            "current": self.current,
            "jobs": list(reversed(self.jobs())),
        }

    def start(self, goal: str) -> dict:
        goal = goal.strip()
        if not goal:
            raise ValueError("Tell Autopilot what you want.")
        plan = self.license.can_start()
        if not self.engine.available():
            raise ValueError("Autopilot needs OpenCode installed on this computer (https://opencode.ai).")
        title = goal if len(goal) <= 80 else goal[:77].rstrip() + "..."
        body = f"Goal (handed to Autopilot): {goal}\n\n## Autopilot log\n"
        note = self.brain.create("task", title, tags=["autopilot"], body=body, today=date.today().isoformat())
        self._note_append(note["path"], "Autopilot started.", status="doing")
        if plan == "trial":
            self.license.use_trial()
        job = {
            "id": uuid.uuid4().hex[:10],
            "goal": goal,
            "note": note["path"],
            "plan": plan,
            "status": "queued",
            "session": "",
            "rounds": 0,
            "improved": 0,
            "failures": 0,
            "pending": [],
            "comments": [],
            "last": "",
            "next": "",
            "reason": "",
            "created": datetime.now().isoformat(timespec="seconds"),
            "updated": datetime.now().isoformat(timespec="seconds"),
        }
        with self.lock:
            jobs = self.jobs()
            jobs.append(job)
            self._save_jobs(jobs)
        self.log(job["id"], f"Goal: {goal}\nTask note: {note['path']}")
        self._ensure_worker()
        return job

    def comment(self, job_id: str, text: str) -> dict:
        job = self.get(job_id)
        if not job:
            raise ValueError("No such goal.")
        text = text.strip()
        if not text:
            raise ValueError("Empty comment.")
        status = "queued" if job["status"] in ("blocked", "done", "stopped") else job["status"]
        self.log(job_id, f"\nYou: {text}")
        self._note_append(job["note"], f"Comment: {text}")
        job = self._update(job_id, pending=job["pending"] + [text], comments=job["comments"] + [text], status=status, reason="")
        self._ensure_worker()
        return job

    def stop(self, job_id: str) -> dict:
        job = self._update(job_id, status="stopped")
        if self.current == job_id:
            self.engine.stop()
        self.log(job_id, "\nStopped.")
        return job or {}

    def resume(self, job_id: str) -> dict:
        job = self._update(job_id, status="queued", reason="")
        self._ensure_worker()
        return job or {}

    def remove(self, job_id: str) -> dict:
        if self.current == job_id:
            self.engine.stop()
        with self.lock:
            self._save_jobs([job for job in self.jobs() if job["id"] != job_id])
        return {"ok": True}

    # ---------------------------------------------------------- worker

    def _ensure_worker(self) -> None:
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.worker = threading.Thread(target=self._loop, name="cairn-autopilot", daemon=True)
            self.worker.start()

    def _next_job(self) -> dict | None:
        return next((job for job in self.jobs() if job["status"] in ("queued", "running")), None)

    def _loop(self) -> None:
        while True:
            job = self._next_job()
            if job is None:
                return
            try:
                self.run_round(job)
            except Exception as error:  # never let one bad round kill the worker
                self.log(job["id"], f"Autopilot error: {error}")
                self._update(job["id"], status="blocked", reason=str(error))
                self.current = None

    def precedents(self, goal: str, limit: int = 5) -> str:
        words = {word for word in WORD_RE.findall(goal.lower()) if word not in STOP_WORDS}
        scored = []
        for note in self.brain.notes():
            haystack = (note.get("title", "") + " " + note.get("body", "")[:4000]).lower()
            score = sum(1 for word in words if word in haystack)
            if score:
                scored.append((score, note))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        lines = []
        for _, note in scored[:limit]:
            snippet = re.sub(r"\s+", " ", note.get("body", ""))[:180]
            lines.append(f"- {note['path']} ({note.get('type', 'note')}): {note.get('title', '')} - {snippet}")
        return "\n".join(lines)

    def _brain_file(self, name: str) -> str:
        path = self.brain.dir / name
        return str(path) if path.exists() else ""

    def first_prompt(self, job: dict) -> str:
        extras = []
        for name, why in (("CONTEXT.md", "project brief"), ("autopilot.md", "the owner's standing defaults and preferences")):
            found = self._brain_file(name)
            if found:
                extras.append(f"- {found} ({why})")
        decisions = self.brain.dir / "decisions"
        if decisions.exists():
            extras.append(f"- {decisions} (decisions in force - respect them)")
        precedents = self.precedents(job["goal"]) or "- none found"
        return (
            "You are Cairn Autopilot, working fully on your own; the owner will not answer questions.\n"
            f"GOAL: {job['goal']}\n"
            f"Project folder: {self.brain.root}\n"
            f"Task note for this goal (the brain logs your progress here): {self.brain.dir / job['note']}\n\n"
            "Read first:\n" + ("\n".join(extras) or "- (no brief yet)") + "\n\n"
            "Related notes in the brain (precedents - what was decided or done before):\n" + precedents + "\n\n"
            "Start: write a short Definition of Done (literal ask + what the owner will obviously want next + how you "
            "will prove each item), then do as much real work as you can this turn and verify it.\n\n" + PROTOCOL
        )

    def next_prompt(self, job: dict, mode: str) -> str:
        comments = ""
        if job["pending"]:
            comments = "NEW COMMENTS FROM THE OWNER (highest priority):\n" + "\n".join(f"- {c}" for c in job["pending"]) + "\n\n"
        if mode == "improve":
            body = ("The goal is done. Improve THIS result once the way the owner would ask next, only if it is a real "
                    "product (not a one-off or a test). If nothing meaningful is left, change nothing and reply STATUS: DONE. "
                    f"Suggested: {job['next'] or 'your call'}.")
        else:
            body = f"Continue the goal without asking. Planned next step: {job['next'] or 'continue the plan'}."
        return f"GOAL: {job['goal']}\n\n{comments}{body}\n\n{PROTOCOL}"

    def run_round(self, job: dict) -> None:
        mode = "first" if job["rounds"] == 0 else ("improve" if job["last"] == "DONE" else "continue")
        prompt = self.first_prompt(job) if mode == "first" else self.next_prompt(job, mode)
        number = job["rounds"] + 1
        self._update(job["id"], status="running", pending=[])
        self.current = job["id"]
        self.log(job["id"], f"\n=== Round {number} ({mode}) {datetime.now():%H:%M:%S} ===")
        text, session, error = self.engine.run(prompt, job["session"], self.brain.root, lambda line: self.log(job["id"], line))
        self.current = None
        job = self.get(job["id"])
        if job is None or job["status"] == "stopped":
            return

        status, detail, hint = parse_reply(text)
        changes = {"rounds": number, "session": session, "next": hint or job["next"], "last": status or "CONTINUE"}
        if error and not text:
            failures = job["failures"] + 1
            changes["failures"] = failures
            self.log(job["id"], f"{error} (attempt {failures})")
            if failures >= 3:
                changes.update(status="blocked", reason=f"The AI engine keeps failing: {error}")
                self._note_append(job["note"], f"Needs you: {changes['reason']}")
            else:
                changes["status"] = "queued"
            self._update(job["id"], **changes)
            return

        changes["failures"] = 0
        if status == "BLOCKED":
            changes.update(status="blocked", reason=detail or "needs your input")
            self._note_append(job["note"], f"Needs you: {changes['reason']}")
        elif status == "DONE":
            improved = job["improved"] + (1 if mode == "improve" else 0)
            changes["improved"] = improved
            if improved >= self.improve_rounds or number >= self.max_rounds:
                changes["status"] = "done"
                self._note_append(job["note"], f"Done after {number} rounds. Next idea: {changes['next'] or '-'}", status="done")
            else:
                changes["status"] = "queued"
                self._note_append(job["note"], f"Round {number}: done, polishing once more.")
        elif number >= self.max_rounds:
            changes.update(status="blocked", reason=f"Reached {number} rounds; comment to keep going.")
            self._note_append(job["note"], f"Paused: {changes['reason']}")
        else:
            changes["status"] = "queued"
            if hint:
                self._note_append(job["note"], f"Round {number}: next - {hint}")
        self._update(job["id"], **changes)

"""Your local dashboards on your phone, from any network, through the private relay repo.

Some tools only have a dashboard on this PC (a local web page or an HTML file the tool rebuilds).
This module copies the newest version of each one to the `dashboards` branch of the private relay
repo (see cairn/relay.py), and docs/dash.html shows them on a phone that is signed in to GitHub. The
phone talks to GitHub, never to this PC, so it works on mobile data and behind the office proxy.

    cairn dashboards add millennium "E:/.../dashboard/index.html" --title Millennium
    cairn dashboards add reachpilot "E:/.../generated/phone_snapshot.html" --build "python E:/.../phone_snapshot.py"
    cairn dashboards publish        build + upload (only what changed; the phone polls every 30 s)
    cairn dashboards schedule       publish every 5 minutes with Windows Task Scheduler
    cairn dashboards link           print the phone link and its QR code

The list lives in ~/.cairn/dashboards.json. Every publish replaces the branch with one fresh commit
(no history), so the repo doesn't grow by a full dashboard every few minutes. The repo must stay
private: the pages can hold personal data.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import relay
from .brain import NO_WINDOW

BRANCH = "dashboards"
STATUS_FILE = "status.json"
HEARTBEAT_SECONDS = 10 * 60  # publish at least this often so the phone can tell the PC is alive
BREAKAWAY = 0x01000000 if sys.platform == "win32" else 0  # CREATE_BREAKAWAY_FROM_JOB
STALE_SECONDS = 30 * 60  # a built page older than this gets a warning on the phone
MAX_BYTES = 20 * 1024 * 1024
WEB_PAGE = "https://juw12.github.io/cairn/dash.html"
TASK_NAME = "Cairn dashboards to phone"


def config_path() -> Path:
    return Path.home() / ".cairn" / "dashboards.json"


def state_path() -> Path:
    return Path.home() / ".cairn" / "dashboards-state.json"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate a BOM from Notepad/PowerShell
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def load_pages() -> list[dict]:
    pages = _read_json(config_path(), {}).get("pages", [])
    return [page for page in pages if isinstance(page, dict) and page.get("id") and page.get("file")]


def save_pages(pages: list[dict]) -> None:
    _write_json(config_path(), {"pages": pages})


def add_page(page_id: str, file: str, title: str = "", build: str = "", cwd: str = "") -> dict:
    page_id = "".join(ch for ch in page_id.lower() if ch.isalnum() or ch in "-_")
    if not page_id:
        raise ValueError("Give the dashboard a short id, like `millennium`.")
    page = {"id": page_id, "title": title or page_id.replace("-", " ").title(), "file": str(Path(file))}
    if build:
        page["build"] = build_args(build)
    if cwd:
        page["cwd"] = str(Path(cwd))
    pages = [existing for existing in load_pages() if existing["id"] != page_id] + [page]
    save_pages(pages)
    return page


def remove_page(page_id: str) -> bool:
    pages = load_pages()
    kept = [page for page in pages if page["id"] != page_id]
    save_pages(kept)
    return len(kept) != len(pages)


def git_blob_sha(data: bytes) -> str:
    """The sha GitHub gives this content as a blob, so unchanged pages are never uploaded twice."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _iso(seconds: float) -> str:
    return dt.datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")


def build_args(command) -> list[str]:
    """A build command as a list (stored that way) or one string ("python E:/x/build.py")."""
    if isinstance(command, list):
        return [str(arg) for arg in command]
    args = shlex.split(str(command), posix=False)
    return [arg[1:-1] if len(arg) > 1 and arg[0] == arg[-1] == '"' else arg for arg in args]


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def build_log(page_id: str) -> Path:
    return Path.home() / ".cairn" / f"dashboards-build-{page_id}.log"


def start_build(page: dict, spawn=subprocess.Popen, alive=None) -> str:
    """Start a page's build in the background and return at once; the next publish picks up the new
    file. A slow build (a busy PC can take minutes) never holds back the other dashboards, and only
    one build per page runs at a time. Returns "" or a short reason the build couldn't start."""
    alive = alive or _alive
    state_file = Path.home() / ".cairn" / f"dashboards-build-{page['id']}.pid"
    try:
        if alive(int(state_file.read_text().strip() or 0)):
            return ""
    except (OSError, ValueError):
        pass
    try:
        args = build_args(page["build"])
        log_file = build_log(page["id"])
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "w", encoding="utf-8") as log:
            options = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=page.get("cwd") or None)
            try:
                # Outlive this run even when Task Scheduler closes its job when the publish ends.
                process = spawn(args, creationflags=NO_WINDOW | BREAKAWAY, **options)
            except OSError:
                process = spawn(args, creationflags=NO_WINDOW, **options)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(str(process.pid))
    except OSError as error:
        return f"build couldn't start: {error}"
    return ""


def build_problem(page: dict, now: float, page_mtime: float | None) -> str:
    """Tell the phone when a built page is going stale, with the last line of its build log."""
    if page_mtime is not None and now - page_mtime < STALE_SECONDS:
        return ""
    try:
        lines = [line for line in build_log(page["id"]).read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        lines = []
    last = lines[-1][:200] if lines else "no output yet"
    return f"page not rebuilt for a while; last build said: {last}"


def collect(pages: list[dict], spawn=subprocess.Popen, clock=time.time) -> tuple[list[dict], dict[str, bytes]]:
    """Kick off builds and read every page as it is now. The newest good copy is always used; the
    phone is told when a page is getting old and why."""
    entries: list[dict] = []
    files: dict[str, bytes] = {}
    for page in pages:
        error = start_build(page, spawn) if page.get("build") else ""
        path = Path(page["file"])
        entry = {"id": page["id"], "title": page.get("title") or page["id"], "file": f"{page['id']}.html"}
        mtime = None
        try:
            data = path.read_bytes()
            if len(data) > MAX_BYTES:
                raise OSError(f"page is over {MAX_BYTES // (1024 * 1024)} MB")
            mtime = path.stat().st_mtime
            files[entry["file"]] = data
            entry.update(sha=git_blob_sha(data), bytes=len(data), updated=_iso(mtime))
        except OSError as problem:
            error = error or f"no page yet ({problem.strerror or problem})"
        if page.get("build") and not error:
            error = build_problem(page, clock(), mtime)
        if error:
            entry["error"] = error
        entries.append(entry)
    return entries, files


class Publisher:
    """Replaces the `dashboards` branch with one orphan commit holding the newest pages."""

    def __init__(self, github: relay.GitHub, device: dict | None = None, clock=time.time) -> None:
        self.gh = github
        self.device = device or {}
        self.clock = clock

    def _api(self, method: str, path: str, body: dict | None = None):
        status, data, _ = self.gh.call(method, f"/repos/{self.gh.repo}{path}", body)
        return status, data

    def upload_blob(self, data: bytes) -> str:
        status, result = self._api("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if status != 201 or not isinstance(result, dict):
            raise relay.RelayError(f"uploading a page: GitHub answered {status}")
        return result["sha"]

    def point_branch(self, commit: str) -> None:
        status, _ = self._api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": commit, "force": True})
        if status in (404, 422):  # first publish: the branch doesn't exist yet
            status, _ = self._api("POST", "/git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": commit})
            if status == 201:
                return
        if status != 200:
            raise relay.RelayError(f"moving the {BRANCH} branch: GitHub answered {status}")

    def publish(self, entries: list[dict], files: dict[str, bytes], force: bool = False) -> str:
        state = _read_json(state_path(), {})
        now = self.clock()
        fingerprint = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        if not force and state.get("fingerprint") == fingerprint and now - state.get("published", 0) < HEARTBEAT_SECONDS:
            return "unchanged"
        known: dict[str, str] = state.get("blobs", {})  # blobs in the branch right now: safe to reuse
        tree = []
        for name, data in files.items():
            sha = git_blob_sha(data)
            if known.get(name) != sha:
                sha = self.upload_blob(data)
            tree.append({"path": name, "mode": "100644", "type": "blob", "sha": sha})
        status = {
            "published": _iso(now),
            "pc": self.device.get("name") or "",
            "heartbeatSeconds": HEARTBEAT_SECONDS,
            "pages": entries,
        }
        status_data = json.dumps(status, indent=2).encode("utf-8")
        tree.append({"path": STATUS_FILE, "mode": "100644", "type": "blob", "sha": self.upload_blob(status_data)})
        code, made = self._api("POST", "/git/trees", {"tree": tree})
        if code != 201 or not isinstance(made, dict):
            raise relay.RelayError(f"saving the pages: GitHub answered {code}")
        code, commit = self._api("POST", "/git/commits", {
            "message": f"Dashboards from {self.device.get('name') or 'this PC'} at {status['published']}",
            "tree": made["sha"],
            "parents": [],
        })
        if code != 201 or not isinstance(commit, dict):
            raise relay.RelayError(f"saving the pages: GitHub answered {code}")
        self.point_branch(commit["sha"])
        _write_json(state_path(), {
            "fingerprint": fingerprint,
            "published": now,
            "blobs": {item["path"]: item["sha"] for item in tree if item["path"] != STATUS_FILE},
        })
        return "published"


def publish(force: bool = False, spawn=subprocess.Popen) -> tuple[str, list[dict]]:
    pages = load_pages()
    if not pages:
        raise ValueError("No dashboards yet. Add one with `cairn dashboards add <id> <file>`.")
    config = relay.load_config()
    if not config:
        raise relay.RelayError("Set up the private relay repo first: `cairn relay setup`.")
    entries, files = collect(pages, spawn)
    result = Publisher(relay.connect(config), relay.device_of(config)).publish(entries, files, force)
    return result, entries


def phone_link() -> tuple[str, str]:
    """The link a phone opens (it carries this PC's GitHub sign-in in the #fragment, which never
    reaches a server) and the relay repo it reads."""
    config = relay.load_config()
    if not config:
        raise relay.RelayError("Set up the private relay repo first: `cairn relay setup`.")
    token = relay.open_token(config.get("token", ""))
    if not token:
        raise relay.RelayError("The relay sign-in is missing. Run `cairn relay setup` again.")
    return f"{WEB_PAGE}#signin={token}&repo={config['repo']}", config["repo"]


def schedule(minutes: int = 5) -> str:
    """Publish every few minutes with Windows Task Scheduler, for good: it repeats with no end date, also
    starts at every Windows sign-in (so a reboot never stops it), catches up after sleep and runs on
    battery. Runs pythonw straight from the app folder (no cmd.exe), so no console window flashes up;
    a run still going makes the next one wait."""
    if sys.platform != "win32":
        raise OSError("Scheduling is Windows-only; use cron with `cairn dashboards publish` elsewhere.")
    python = Path(sys.executable)
    quiet = python.with_name("pythonw.exe")
    exe = quiet if quiet.exists() else python
    app = Path(__file__).resolve().parent.parent

    def quote(text) -> str:
        return "'" + str(text).replace("'", "''") + "'"

    script = (
        f"$action = New-ScheduledTaskAction -Execute {quote(exe)} -Argument '-m cairn dashboards publish' "
        f"-WorkingDirectory {quote(app)}; "
        f"$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes {int(minutes)}); "
        "$logon = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\\$env:USERNAME\"; "
        "$logon.Repetition = $trigger.Repetition; "
        "$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable "
        "-ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName {quote(TASK_NAME)} -Action $action -Trigger @($trigger, $logon) -Settings $settings -Force | Out-Null"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip() or "Register-ScheduledTask failed")
    return TASK_NAME

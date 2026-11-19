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

A page's buttons can also work from the phone. Give the page a command and run the worker:

    cairn dashboards add reachpilot <file> --actions "python E:/.../phone_action.py"
    cairn dashboards actions        run the worker in this terminal
    cairn dashboards schedule       also keeps the worker running in the background

The phone writes dashboards/<id>/requests/<request>.json ({"id", "page", "name", "at"}) on the main
branch of the relay repo. The worker takes each request once (it deletes the file before it runs
anything), runs the page's command with the button's name as its single argument, and answers in
dashboards/<id>/results/<request>.json ({"state": running|done, "ok", "message"}): the command's
last line of output is what the phone shows. Then it rebuilds that page and publishes. Cairn never
decides what a name may do: the command must refuse every name it does not know.

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
import re
import shlex
import subprocess
import sys
import threading
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
ACTIONS_TASK_NAME = "Cairn dashboard actions"
ACTIONS_FOLDER = "dashboards"  # on the relay repo's main branch: dashboards/<id>/requests/ and results/
ACTIONS_POLL_S = 5
ACTION_MAX_AGE_S = 5 * 60  # an older request is refused: nothing runs by surprise after the PC was off
ACTION_CLOCK_SLACK_S = 2 * 60  # a phone's clock may run a little ahead of this PC's
ACTION_TIMEOUT_S = 10 * 60
ACTION_NAME_MAX = 600
ACTION_MESSAGE_MAX = 500
ACTIONS_AT_ONCE = 4
RESULT_KEEP_S = 24 * 3600
SWEEP_EVERY_S = 3600
BUILD_WAIT_S = 5 * 60


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


def add_page(page_id: str, file: str, title: str = "", build: str = "", cwd: str = "", actions: str = "") -> dict:
    page_id = "".join(ch for ch in page_id.lower() if ch.isalnum() or ch in "-_")
    if not page_id:
        raise ValueError("Give the dashboard a short id, like `millennium`.")
    page = {"id": page_id, "title": title or page_id.replace("-", " ").title(), "file": str(Path(file))}
    if build:
        page["build"] = build_args(build)
    if cwd:
        page["cwd"] = str(Path(cwd))
    if actions:
        page["actions"] = build_args(actions)
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


def collect(pages: list[dict], spawn=subprocess.Popen, clock=time.time, build: bool = True) -> tuple[list[dict], dict[str, bytes]]:
    """Kick off builds and read every page as it is now. The newest good copy is always used; the
    phone is told when a page is getting old and why. build=False only reads (the pages were just built)."""
    entries: list[dict] = []
    files: dict[str, bytes] = {}
    for page in pages:
        error = start_build(page, spawn) if page.get("build") and build else ""
        path = Path(page["file"])
        entry = {"id": page["id"], "title": page.get("title") or page["id"], "file": f"{page['id']}.html"}
        if page.get("actions"):
            entry["actions"] = True  # the phone page lets this dashboard's buttons ask the PC
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


def publish(force: bool = False, spawn=subprocess.Popen, build: bool = True) -> tuple[str, list[dict]]:
    pages = load_pages()
    if not pages:
        raise ValueError("No dashboards yet. Add one with `cairn dashboards add <id> <file>`.")
    config = relay.load_config()
    if not config:
        raise relay.RelayError("Set up the private relay repo first: `cairn relay setup`.")
    entries, files = collect(pages, spawn, build=build)
    result = Publisher(relay.connect(config), relay.device_of(config)).publish(entries, files, force)
    return result, entries


# ============================================================ buttons pressed on the phone

ACTION_ID_RE = re.compile(r"^\d{13}-[a-f0-9]{4,16}$")  # made by the phone page: <milliseconds>-<random>


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _code_stamp() -> float:
    """Changes when Cairn updates itself, so the long-running worker can make room for the new version."""
    return max((_mtime(path) for path in Path(__file__).resolve().parent.glob("*.py")), default=0.0)


def refresh_page(page: dict, run=subprocess.run) -> None:
    """Rebuild one page now (waiting for it) and publish, so the phone sees what an action changed."""
    if page.get("build"):
        try:
            run(build_args(page["build"]), cwd=page.get("cwd") or None, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=BUILD_WAIT_S, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass  # the regular publish builds it again within minutes
    try:
        publish(build=False)
    except (relay.RelayError, ValueError, OSError):
        pass


class ActionWorker:
    """Takes the requests a phone leaves in the relay repo and runs each one once with its page's command."""

    def __init__(self, github: relay.GitHub, pages: list[dict], clock=time.time, run=subprocess.run,
                 refresh=refresh_page, threaded: bool = True) -> None:
        self.gh = github
        self.clock = clock
        self.run = run
        self.refresh = refresh
        self.threaded = threaded
        self.pages: dict[str, dict] = {}
        self.set_pages(pages)
        self.etags: dict[str, str] = {}
        self.recheck: dict[str, float] = {}  # page id -> when to list its requests again after a failed claim
        self.handled: set[str] = set()
        self.running: list[threading.Thread] = []
        self.swept = 0.0
        self.github_lock = threading.RLock()  # relay.GitHub remembers file shas: one caller at a time
        self.refresh_lock = threading.Lock()

    def set_pages(self, pages: list[dict]) -> None:
        self.pages = {page["id"]: page for page in pages if page.get("actions")}

    def poll(self) -> int:
        taken = 0
        for page_id in list(self.pages):
            folder = f"{ACTIONS_FOLDER}/{page_id}/requests"
            with self.github_lock:
                status, items, etag = self.gh.list_dir(folder, self.etags.get(page_id, ""))
                if status == 404:  # with the folder there, an unchanged listing is a free 304
                    self.gh.write(f"{folder}/.keep", "", f"cairn dashboards: {page_id} requests folder")
            if status != 200:
                continue
            self.etags[page_id] = etag
            names = sorted(item["name"] for item in items if item["type"] == "file" and item["name"].endswith(".json"))
            for name in names:
                if not self.take(page_id, f"{folder}/{name}"):
                    self.recheck[page_id] = self.clock() + 60
                taken += 1
        return taken

    def take(self, page_id: str, path: str) -> bool:
        """Claim one request by deleting its file, then start it. Only a request this worker deleted
        itself is ever run, so nothing runs twice: not after a restart, not with two workers, and not
        after the phone took the request back. False = couldn't claim it now; look again later."""
        request_id = path.rsplit("/", 1)[-1][:-5]
        with self.github_lock:
            try:
                text = self.gh.read(path)
            except (relay.RelayError, OSError):
                return False
            if text is None:
                return True  # the phone took it back
            try:
                status, _, _ = self.gh.call("DELETE", self.gh._contents(path), {
                    "message": f"cairn dashboards: took {request_id}", "sha": self.gh.shas.get(path, "")})
            except OSError:
                return False
            self.gh.shas.pop(path, None)
        if status == 404:
            return True
        if status != 200:
            return False
        if request_id in self.handled or not ACTION_ID_RE.match(request_id):
            return True
        self.handled.add(request_id)
        try:
            request = json.loads(text)
        except ValueError:
            request = None
        problem = self.problem(request)
        if problem:
            self.answer(page_id, request_id, "done", False, problem)
            return True
        self.answer(page_id, request_id, "running")
        page, name = self.pages[page_id], request["name"]
        if self.threaded:
            thread = threading.Thread(target=self.work, args=(page, request_id, name), name=f"action-{request_id}")
            thread.start()
            self.running.append(thread)
        else:
            self.work(page, request_id, name)
        return True

    def problem(self, request) -> str:
        """Why this request is not run, or "". What the name may do is the page's own command's call."""
        if not isinstance(request, dict) or not isinstance(request.get("name"), str):
            return "Your PC couldn't read that request."
        name = request["name"]
        if not name or len(name) > ACTION_NAME_MAX or any(ord(letter) < 32 for letter in name):
            return "Your PC doesn't accept that button's name."
        try:
            sent = float(request.get("at") or 0)
        except (TypeError, ValueError):
            sent = 0.0
        if sent > 1e11:
            sent /= 1000  # the phone sends milliseconds
        age = self.clock() - sent
        if age > ACTION_MAX_AGE_S or age < -ACTION_CLOCK_SLACK_S:
            return "That request was too old when your PC saw it, so it was not run. Press the button again."
        if sum(thread.is_alive() for thread in self.running) >= ACTIONS_AT_ONCE:
            return "Your PC is busy with earlier requests. Try again in a minute."
        return ""

    def work(self, page: dict, request_id: str, name: str) -> None:
        try:
            done = self.run(build_args(page["actions"]) + [name], cwd=page.get("cwd") or None, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=ACTION_TIMEOUT_S, creationflags=NO_WINDOW)
            ok = done.returncode == 0
            output = (done.stdout if ok else done.stderr or done.stdout) or ""
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            message = lines[-1] if lines else ("Done." if ok else "That didn't work.")
        except subprocess.TimeoutExpired:
            ok, message = False, f"Still running after {ACTION_TIMEOUT_S // 60} minutes. Look at the dashboard in a while."
        except (OSError, ValueError) as error:
            ok, message = False, f"Your PC couldn't start it: {error}"
        self.answer(page["id"], request_id, "done", ok, message)
        with self.refresh_lock:
            self.refresh(page)

    def answer(self, page_id: str, request_id: str, state: str, ok: bool | None = None, message: str = "") -> None:
        body = {"id": request_id, "state": state, "at": int(self.clock() * 1000)}
        if state == "done":
            body.update(ok=bool(ok), message=message[:ACTION_MESSAGE_MAX])
        path = f"{ACTIONS_FOLDER}/{page_id}/results/{request_id}.json"
        for attempt in range(3 if state == "done" else 1):  # the final answer is worth a second try
            try:
                with self.github_lock:
                    self.gh.write(path, json.dumps(body), f"cairn dashboards: {page_id} {state}")
                return
            except (relay.RelayError, OSError):
                if attempt < 2 and state == "done":
                    time.sleep(2)

    def sweep(self) -> int:
        """Answers older than a day are deleted, so the folder doesn't grow."""
        removed = 0
        for page_id in list(self.pages):
            with self.github_lock:
                status, items, _ = self.gh.list_dir(f"{ACTIONS_FOLDER}/{page_id}/results")
            for item in items if status == 200 else []:
                stamp = item["name"].split("-", 1)[0]
                if item["type"] == "file" and stamp.isdigit() and self.clock() - int(stamp) / 1000 > RESULT_KEEP_S:
                    with self.github_lock:
                        self.gh.delete(item["path"], "cairn dashboards: old answer")
                    removed += 1
        return removed

    def step(self) -> None:
        now = self.clock()
        for page_id, when in list(self.recheck.items()):
            if now >= when:
                self.etags.pop(page_id, None)
                del self.recheck[page_id]
        self.running = [thread for thread in self.running if thread.is_alive()]
        self.poll()
        if now - self.swept > SWEEP_EVERY_S:
            self.swept = now
            self.sweep()


def run_actions(stop: threading.Event | None = None, log=print) -> None:
    """The worker loop. It ends when Cairn's own code changes; the scheduled task starts the new version."""
    config = relay.load_config()
    if not config:
        raise relay.RelayError("Set up the private relay repo first: `cairn relay setup`.")
    worker = ActionWorker(relay.connect(config), load_pages())
    if not worker.pages:
        raise ValueError('No dashboard takes actions yet. Add one with `cairn dashboards add <id> <file> --actions "command"`.')
    stop = stop or threading.Event()
    code, listed = _code_stamp(), _mtime(config_path())
    log(f"Waiting for buttons pressed on the phone: {', '.join(worker.pages)} (repo {config['repo']}). Ctrl+C stops.")
    while not stop.is_set():
        wait = ACTIONS_POLL_S
        try:
            worker.step()
        except (relay.RelayError, OSError) as error:
            log(f"dashboards actions: {error}")
            wait = 30
            try:
                worker.gh = relay.connect(config)  # a new sign-in or another route may be what it needs
            except (relay.RelayError, OSError):
                pass
        if _code_stamp() != code:
            log("Cairn was updated: stopping so the new version takes over.")
            break
        if _mtime(config_path()) != listed:
            listed = _mtime(config_path())
            worker.set_pages(load_pages())
        stop.wait(wait)


def phone_link() -> tuple[str, str]:
    """The link a phone opens (it carries this PC's GitHub sign-in in the #fragment, which never
    reaches a server) and the relay repo it reads."""
    config = relay.load_config()
    if not config:
        raise relay.RelayError("Set up the private relay repo first: `cairn relay setup`.")
    token = relay.open_token(config.get("token", ""))
    if not token:
        raise relay.RelayError("The relay sign-in is missing. Run `cairn relay setup` again.")
    return f"{WEB_PAGE}?repo={config['repo']}#signin={token}&repo={config['repo']}", config["repo"]


def _register_task(name: str, argument: str, minutes: int, time_limit: str) -> str:
    """A Windows task that runs pythonw straight from the app folder (no cmd.exe, so no console window
    flashes up), repeats with no end date, also starts at every Windows sign-in (so a reboot never
    stops it), catches up after sleep and runs on battery; a run still going makes the next one wait."""
    if sys.platform != "win32":
        raise OSError("Scheduling is Windows-only; use cron with `cairn dashboards publish` elsewhere.")
    python = Path(sys.executable)
    quiet = python.with_name("pythonw.exe")
    exe = quiet if quiet.exists() else python
    app = Path(__file__).resolve().parent.parent

    def quote(text) -> str:
        return "'" + str(text).replace("'", "''") + "'"

    script = (
        f"$action = New-ScheduledTaskAction -Execute {quote(exe)} -Argument {quote(argument)} "
        f"-WorkingDirectory {quote(app)}; "
        f"$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes {int(minutes)}); "
        "$logon = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\\$env:USERNAME\"; "
        "$logon.Repetition = $trigger.Repetition; "
        "$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable "
        f"-ExecutionTimeLimit {time_limit} -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName {quote(name)} -Action $action -Trigger @($trigger, $logon) -Settings $settings -Force | Out-Null"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip() or "Register-ScheduledTask failed")
    return name


def schedule(minutes: int = 5) -> str:
    """Publish every few minutes with Windows Task Scheduler, for good."""
    return _register_task(TASK_NAME, "-m cairn dashboards publish", minutes, "(New-TimeSpan -Minutes 15)")


def schedule_actions() -> str:
    """Keep the action worker running: it never ends on its own (no time limit), and Task Scheduler
    looks every minute whether it still runs, so it is back within a minute of a crash or an update."""
    return _register_task(ACTIONS_TASK_NAME, "-m cairn dashboards actions", 1, "([TimeSpan]::Zero)")

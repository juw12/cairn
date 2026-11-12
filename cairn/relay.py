"""Chat from anywhere: a private GitHub repo carries chat between your phone and this PC.

The phone can't reach the PC from outside the office network, but both can reach GitHub. Several PCs
signed in to the same GitHub account can share one relay repo; each works in its own folder, so the
phone can see every PC and choose which one it talks to:

    devices/<pc>/requests/<id>.json   written by the phone: {"id", "action": send|stop|delete, "thread", "text", "model", "at"}
    devices/<pc>/threads/<chat>.json  written by that PC: the chat (as in ~/.cairn/chats) plus "request", the
                                      last request id it handled, so the phone knows its message arrived
    devices/<pc>/index.json           written by that PC: {"device": {"id", "name"}, "updated", "pcSeen",
                                      "available", "models", "chats": [...]}

This worker polls requests/ with an ETag (a 304 answer is free against the rate limit), runs each
request through cairn.chat exactly like the local Chat page does, deletes the request, and uploads
the chat as the answer streams in. Anyone who can push to the relay repo can make this PC run
OpenCode prompts, so keep the repo private and the token to yourself.

    cairn relay setup [--repo owner/name] [--proxy URL] [--name "Work PC"]   create or join the repo
    cairn relay run                                                          run the worker in this terminal
The desktop app runs the worker on its own once setup has been done.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import chat

API = "https://api.github.com"
DEFAULT_NAME = "cairn-relay"
POLL_S = 4
PARTIAL_S = 8
HEARTBEAT_S = 10 * 60
MAX_REQUEST_AGE_S = 60 * 60
CHAT_ID_RE = re.compile(r"^[A-Za-z0-9-]{4,40}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{4,60}$")
DEVICE_ID_RE = re.compile(r"^[a-z0-9-]{1,48}$")
README = """# Cairn relay

Private mailbox between Cairn on your phone and Cairn on your PC (see `cairn relay --help`).
Each PC has a folder in `devices/`: your phone writes its `requests/`, the PC answers in its
`threads/` and `index.json`.
Keep this repo private: anyone who can push here can make your PC run OpenCode prompts.
"""


class RelayError(Exception):
    pass


# ============================================================ config and token


def config_path() -> Path:
    return chat.home() / "relay.json"


def load_config() -> dict | None:
    try:
        config = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return config if isinstance(config, dict) and "/" in str(config.get("repo", "")) else None


def save_config(config: dict) -> None:
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(config, indent=1), encoding="utf-8")


def make_device(name: str = "") -> dict:
    """This PC's identity in the relay repo: a readable, stable id plus a name you can change."""
    host = platform.node() or os.environ.get("COMPUTERNAME", "") or "pc"
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")[:30] or "pc"
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    suffix = hashlib.sha1(f"{host}|{user}".encode()).hexdigest()[:4]
    return {"id": f"{slug}-{suffix}", "name": (name or host).strip()[:60]}


def device_of(config: dict) -> dict:
    """The device saved in relay.json, created (and saved) the first time it is needed."""
    device = config.get("device") if isinstance(config.get("device"), dict) else {}
    if not DEVICE_ID_RE.match(str(device.get("id") or "")):
        device = make_device(str(device.get("name") or ""))
        config["device"] = device
        if config.get("repo"):
            save_config(config)
    return {"id": device["id"], "name": str(device.get("name") or device["id"])}


def find_gh() -> str:
    found = shutil.which("gh")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidate = local / "Programs" / "GitHubCLI" / "bin" / "gh.exe"
    return str(candidate) if os.environ.get("LOCALAPPDATA") and candidate.exists() else ""


# The token is kept in relay.json encrypted for this Windows user (DPAPI), so the desktop app doesn't
# depend on the GitHub CLI's keyring, which can hang on locked-down company laptops.
def _blob(data: bytes):
    import ctypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    return DataBlob, DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes

    DataBlob, blob_in, _keep = _blob(data)
    blob_out = DataBlob()
    crypt = ctypes.windll.crypt32
    call = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    if protect:
        ok = call(ctypes.byref(blob_in), "cairn relay", None, None, None, 0, ctypes.byref(blob_out))
    else:
        ok = call(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise RelayError("Windows couldn't decrypt the saved token; run `cairn relay setup` again.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def seal_token(token: str) -> str:
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi(token.encode(), True)).decode()
    return "plain:" + token


def open_token(sealed: str) -> str:
    kind, _, value = str(sealed or "").partition(":")
    if kind == "dpapi" and os.name == "nt":
        return _dpapi(base64.b64decode(value), False).decode()
    return value if kind == "plain" else ""


def get_token(use_saved: bool = True) -> str:
    for name in ("CAIRN_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    saved = (load_config() or {}).get("token") if use_saved else ""
    if saved:
        return open_token(saved)
    gh = find_gh()
    if gh:
        try:
            result = subprocess.run(
                [gh, "auth", "token"], capture_output=True, text=True, timeout=15,
                stdin=subprocess.DEVNULL, **chat._no_window(),
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    # Git's own credential store: whatever `git push` to GitHub already uses (never prompts here).
    try:
        result = subprocess.run(
            ["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=45, **chat._no_window(),
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
        )
        for line in result.stdout.splitlines():
            if line.startswith("password=") and len(line) > 12:
                return line[len("password="):].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raise RelayError("No GitHub token: sign in with `gh auth login` or set CAIRN_GITHUB_TOKEN.")


# ============================================================ GitHub contents API


class GitHub:
    """The few Contents API calls the relay needs, with the file shas remembered between calls."""

    def __init__(self, repo: str, token: str, proxy: str = "") -> None:
        self.repo = repo
        self.token = token
        # With a proxy (office network) keep a direct route too: a laptop carried home can't reach the proxy.
        self.openers = [urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))] if proxy else []
        self.openers.append(urllib.request.build_opener(urllib.request.ProxyHandler({})))
        self.shas: dict[str, str] = {}

    def call(self, method: str, path: str, body: dict | None = None, etag: str = "") -> tuple[int, object, dict]:
        url = path if path.startswith("http") else f"{API}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("User-Agent", "cairn-relay")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if etag:
            request.add_header("If-None-Match", etag)
        for attempt, opener in enumerate(list(self.openers)):
            try:
                with opener.open(request, timeout=30) as response:
                    raw = response.read()
                    return response.status, json.loads(raw) if raw else None, dict(response.headers)
            except urllib.error.HTTPError as error:
                raw = error.read()
                try:
                    payload = json.loads(raw) if raw else None
                except ValueError:
                    payload = None
                return error.code, payload, dict(error.headers or {})
            except OSError:
                if attempt == len(self.openers) - 1:
                    raise
                self.openers.append(self.openers.pop(0))  # this route is down: lead with the other one
        raise OSError("no route to GitHub")

    def _contents(self, path: str) -> str:
        return f"/repos/{self.repo}/contents/{urllib.parse.quote(path)}"

    def list_dir(self, path: str, etag: str = "") -> tuple[int, list, str]:
        status, data, headers = self.call("GET", self._contents(path), etag=etag)
        new_etag = headers.get("ETag") or headers.get("etag") or ""
        if status == 304:
            return 304, [], etag
        if status == 404:
            return 404, [], ""
        if status != 200 or not isinstance(data, list):
            raise RelayError(f"listing {path}: GitHub answered {status}")
        for item in data:
            self.shas[item["path"]] = item["sha"]
        return 200, data, new_etag

    def read(self, path: str) -> str | None:
        status, data, _ = self.call("GET", self._contents(path))
        if status == 404:
            self.shas.pop(path, None)
            return None
        if status != 200 or not isinstance(data, dict):
            raise RelayError(f"reading {path}: GitHub answered {status}")
        self.shas[path] = data["sha"]
        return base64.b64decode(data.get("content") or "").decode("utf-8")

    def write(self, path: str, text: str, message: str) -> None:
        for attempt in range(3):
            body = {"message": message, "content": base64.b64encode(text.encode("utf-8")).decode()}
            if self.shas.get(path):
                body["sha"] = self.shas[path]
            status, data, _ = self.call("PUT", self._contents(path), body)
            if status in (200, 201):
                self.shas[path] = data["content"]["sha"]
                return
            if status in (409, 422) and attempt < 2:
                # Our sha is out of date (or missing): look up the current one and try again.
                self.read(path)
                continue
            raise RelayError(f"writing {path}: GitHub answered {status}")

    def delete(self, path: str, message: str) -> None:
        sha = self.shas.get(path)
        if not sha:
            if self.read(path) is None:
                return
            sha = self.shas[path]
        status, _, _ = self.call("DELETE", self._contents(path), {"message": message, "sha": sha})
        if status in (200, 404):
            self.shas.pop(path, None)
            return
        if status in (409, 422):
            self.shas.pop(path, None)
            if self.read(path) is not None:
                status, _, _ = self.call("DELETE", self._contents(path), {"message": message, "sha": self.shas[path]})
                self.shas.pop(path, None)
                if status in (200, 404):
                    return
            else:
                return
        raise RelayError(f"deleting {path}: GitHub answered {status}")


# ============================================================ the worker


class Relay:
    def __init__(self, github: GitHub, chats=chat, clock=time.time, device: dict | None = None) -> None:
        self.gh = github
        self.device = device
        self.prefix = f"devices/{device['id']}/" if device else ""
        self.chats = chats
        self.clock = clock
        self.requests_etag = ""
        self.handled: set[str] = set()
        self.uploaded: dict[str, float] = {}      # chat id -> "updated" we last uploaded
        self.uploaded_at: dict[str, float] = {}   # chat id -> when
        self.answered: dict[str, dict] = {}       # chat id -> {"request", "error", "text"}
        self.index_signature = ""
        self.index_at = 0.0

    def path(self, name: str) -> str:
        return self.prefix + name

    # ---------------------------------------------------- requests from the phone
    def poll_requests(self) -> int:
        status, items, etag = self.gh.list_dir(self.path("requests"), self.requests_etag)
        if status == 304:
            return 0
        if status == 404:
            self.gh.write(self.path("requests/.keep"), "", "cairn relay: requests folder")
            self.requests_etag = ""
            return 0
        self.requests_etag = etag
        files = sorted(item["name"] for item in items if item["type"] == "file" and item["name"].endswith(".json"))
        for name in files:
            self.take(self.path(f"requests/{name}"))
        return len(files)

    def take(self, path: str) -> None:
        request_id = path.rsplit("/", 1)[-1][:-5]
        if request_id not in self.handled:
            try:
                request = json.loads(self.gh.read(path) or "null")
            except ValueError:
                request = None
            self.handled.add(request_id)
            if isinstance(request, dict):
                self.handle(request_id, request)
        self.gh.delete(path, f"cairn relay: done {request_id}")

    def handle(self, request_id: str, request: dict) -> None:
        if not REQUEST_ID_RE.match(request_id):
            return
        sent_at = float(request.get("at") or 0)
        if sent_at > 1e11:
            sent_at /= 1000  # the phone sends milliseconds
        if self.clock() - sent_at > MAX_REQUEST_AGE_S:
            return  # sent long ago while the PC was off; don't surprise anyone by running it now
        thread = str(request.get("thread") or "")
        if not CHAT_ID_RE.match(thread):
            return
        action = request.get("action")
        if action == "send":
            text = str(request.get("text") or "")
            try:
                self.chats.send(thread, text, str(request.get("model") or ""))
                self.answered[thread] = {"request": request_id}
            except ValueError as error:
                self.answered[thread] = {"request": request_id, "error": str(error), "text": text}
            self.uploaded.pop(thread, None)
        elif action == "stop":
            self.chats.stop(thread)
            self.answered[thread] = {"request": request_id}
            self.uploaded.pop(thread, None)
        elif action == "delete":
            self.chats.delete(thread)
            self.answered.pop(thread, None)
            self.uploaded.pop(thread, None)
            self.gh.delete(self.path(f"threads/{thread}.json"), f"cairn relay: delete {thread}")
            self.index_signature = ""

    # ---------------------------------------------------- answers to the phone
    def thread_payload(self, chat_id: str) -> dict | None:
        loaded = self.chats.load(chat_id)
        answer = self.answered.get(chat_id, {})
        if loaded is None and not answer.get("error"):
            return None
        payload = dict(loaded or {"id": chat_id, "title": answer.get("text", "")[:60], "status": "idle", "messages": []})
        payload["messages"] = list(payload.get("messages") or [])
        if answer.get("request"):
            payload["request"] = answer["request"]
        if answer.get("error"):
            payload["error"] = answer["error"]
            now = self.clock()
            payload["messages"] += [
                {"role": "user", "text": answer.get("text", ""), "at": now},
                {"role": "assistant", "text": f"_Couldn't run that: {answer['error']}_", "at": now},
            ]
        return payload

    def push_threads(self) -> int:
        pushed = 0
        now = self.clock()
        ids = {item["id"] for item in self.chats.summary()["chats"] if item.get("id")} | set(self.answered)
        for chat_id in sorted(ids):
            loaded = self.chats.load(chat_id)
            updated = float((loaded or {}).get("updated") or 0)
            if chat_id in self.uploaded and self.uploaded[chat_id] == updated:
                continue
            running = (loaded or {}).get("status") == "running"
            if running and chat_id in self.uploaded and now - self.uploaded_at.get(chat_id, 0) < PARTIAL_S:
                continue  # stream the reply in steps, not one commit per token
            payload = self.thread_payload(chat_id)
            if payload is None:
                continue
            self.gh.write(self.path(f"threads/{chat_id}.json"), json.dumps(payload, ensure_ascii=False), f"cairn relay: {chat_id}")
            self.uploaded[chat_id] = updated
            self.uploaded_at[chat_id] = now
            if self.answered.get(chat_id, {}).get("error"):
                self.answered[chat_id].pop("error", None)
            pushed += 1
        return pushed

    def push_index(self) -> bool:
        info = self.chats.summary()
        body = {"available": info["available"], "models": info["models"], "chats": info["chats"]}
        if self.device:
            body["device"] = self.device
        signature = json.dumps(body, sort_keys=True)
        now = self.clock()
        if signature == self.index_signature and now - self.index_at < HEARTBEAT_S:
            return False
        body.update(updated=now, pcSeen=now)
        self.gh.write(self.path("index.json"), json.dumps(body, ensure_ascii=False), "cairn relay: index")
        self.index_signature = signature
        self.index_at = now
        return True

    def step(self) -> None:
        self.poll_requests()
        self.push_threads()
        self.push_index()

    def run(self, stop: threading.Event | None = None, log=print) -> None:
        stop = stop or threading.Event()
        where = f" as {self.device['name']} ({self.device['id']})" if self.device else ""
        log(f"relay: watching {self.gh.repo}{where}")
        failures = 0
        while not stop.is_set():
            try:
                self.step()
                failures = 0
            except (RelayError, OSError, ValueError, KeyError) as error:
                failures += 1
                if failures in (1, 10) or failures % 100 == 0:
                    log(f"relay: {error}")
            # Poll faster while an answer is streaming, slower after repeated failures.
            busy = any((self.chats.load(cid) or {}).get("status") == "running" for cid in list(self.uploaded))
            stop.wait(min(POLL_S * (1 + min(failures, 9)), 60) if failures else (3 if busy else POLL_S))


# ============================================================ setup and entry points


def connect(config: dict | None = None) -> GitHub:
    config = config or load_config()
    if not config:
        raise RelayError("Relay isn't set up. Run `cairn relay setup` first.")
    proxy = config.get("proxy") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    return GitHub(config["repo"], get_token(), proxy)


def setup(repo: str = "", proxy: str = "", name: str = "") -> dict:
    """Create the private relay repo if needed (or join it from another PC) and remember it in ~/.cairn/relay.json."""
    token = get_token(use_saved=False)
    probe = GitHub("", token, proxy or os.environ.get("HTTPS_PROXY", ""))
    status, user, _ = probe.call("GET", "/user")
    if status != 200:
        raise RelayError(f"GitHub didn't accept the token ({status}).")
    repo = repo or f"{user['login']}/{DEFAULT_NAME}"
    probe.repo = repo
    status, data, _ = probe.call("GET", f"/repos/{repo}")
    if status == 404:
        owner, name = repo.split("/", 1)
        path = "/user/repos" if owner.lower() == user["login"].lower() else f"/orgs/{owner}/repos"
        status, data, _ = probe.call("POST", path, {
            "name": name, "private": True, "auto_init": True, "has_issues": False, "has_wiki": False,
            "description": "Private mailbox between Cairn on my phone and my PC",
        })
        if status not in (200, 201):
            raise RelayError(f"Couldn't create {repo} ({status}).")
        time.sleep(2)
    elif status != 200:
        raise RelayError(f"Couldn't open {repo} ({status}).")
    if not data.get("private"):
        raise RelayError(f"{repo} is public. The relay repo must be private.")
    probe.write("README.md", README, "cairn relay: readme")
    if probe.list_dir("requests")[0] == 404:
        probe.write("requests/.keep", "", "cairn relay: requests folder")
    previous = load_config() or {}
    device = make_device(name) if not previous.get("device") else dict(previous["device"])
    if name:
        device["name"] = name.strip()[:60]
    config = {"repo": repo, "proxy": proxy, "token": seal_token(token), "device": device}
    save_config(config)
    if os.name != "nt":
        config_path().chmod(0o600)
    return config


def start_in_background(stop: threading.Event, log=print) -> threading.Thread | None:
    """For the desktop app: run the worker on a thread if the relay has been set up."""
    config = load_config()
    if not config or config.get("enabled") is False:
        return None

    def work() -> None:
        # Keep trying: at sign-in the network or the token store may not be ready yet.
        while not stop.is_set():
            try:
                Relay(connect(config), device=device_of(config)).run(stop, log)
                return
            except (RelayError, OSError) as error:
                log(f"relay: {error} (retrying in a minute)")
                stop.wait(60)

    thread = threading.Thread(target=work, daemon=True, name="cairn-relay")
    thread.start()
    return thread

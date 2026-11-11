"""Plain chat with your OpenCode models, from any paired device.

Each chat is a small JSON file in ~/.cairn/chats. Sending a message runs
`opencode run --format json` in the background (continuing the same OpenCode session, so the model
keeps the conversation) and streams the reply text into the chat file as it arrives; the page polls
the file. Works with any OpenCode build on PATH, including company builds such as PEGAAi OpenCode.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

CHAT_TIMEOUT_S = 15 * 60
MODELS_TTL_S = 600
MAX_MESSAGE_CHARS = 20_000

_lock = threading.Lock()
_running: dict[str, subprocess.Popen] = {}
_models_cache: dict = {"at": 0.0, "models": []}


def home() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")


def chats_dir() -> Path:
    return home() / "chats"


def workspace() -> Path:
    """Where chat runs start: an empty folder, so a chat never edits one of your repos by accident."""
    folder = home() / "chat-workspace"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def find_opencode() -> str:
    found = shutil.which("opencode")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) if os.name == "nt" else None
    if local:
        for candidate in local.glob("*pencode*/opencode.exe"):
            return str(candidate)
    return ""


def _no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def models(force: bool = False) -> list[str]:
    exe = find_opencode()
    if not exe:
        return []
    if not force and _models_cache["models"] and time.time() - _models_cache["at"] < MODELS_TTL_S:
        return list(_models_cache["models"])
    try:
        result = subprocess.run(
            [exe, "models"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, stdin=subprocess.DEVNULL, **_no_window(),
        )
        found = [line.strip() for line in result.stdout.splitlines() if "/" in line.strip() and " " not in line.strip()]
    except (OSError, subprocess.SubprocessError):
        found = []
    _models_cache.update(at=time.time(), models=found)
    return found


# ============================================================ storage


def _path(chat_id: str) -> Path:
    if not chat_id or not all(c.isalnum() or c == "-" for c in chat_id):
        raise ValueError("bad chat id")
    return chats_dir() / f"{chat_id}.json"


def load(chat_id: str) -> dict | None:
    try:
        return json.loads(_path(chat_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(chat: dict) -> None:
    chats_dir().mkdir(parents=True, exist_ok=True)
    target = _path(chat["id"])
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(chat, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, target)


def summary() -> dict:
    items = []
    if chats_dir().exists():
        for file in chats_dir().glob("*.json"):
            try:
                chat = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({k: chat.get(k) for k in ("id", "title", "model", "status", "updated")})
    items.sort(key=lambda item: item.get("updated") or 0, reverse=True)
    return {"available": bool(find_opencode()), "models": models(), "chats": items}


def delete(chat_id: str) -> None:
    stop(chat_id)
    _path(chat_id).unlink(missing_ok=True)


# ============================================================ sending


def send(chat_id: str, text: str, model: str = "") -> dict:
    text = text.strip()
    if not text:
        raise ValueError("type a message first")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ValueError("message is too long")
    exe = find_opencode()
    if not exe:
        raise ValueError("OpenCode is not installed on this PC")
    with _lock:
        chat = load(chat_id) if chat_id else None
        if chat is None:
            # A device that can't reach this PC directly (the GitHub relay) names new chats itself.
            new_id = chat_id if chat_id and _path(chat_id) else f"{time.strftime('%Y%m%d')}-{secrets.token_hex(4)}"
            chat = {
                "id": new_id,
                "title": text.splitlines()[0][:60],
                "model": model,
                "session": "",
                "status": "idle",
                "created": time.time(),
                "messages": [],
            }
        if chat["id"] in _running:
            raise ValueError("still answering the last message")
        if model:
            chat["model"] = model
        now = time.time()
        chat["messages"].append({"role": "user", "text": text, "at": now})
        chat["messages"].append({"role": "assistant", "text": "", "at": now, "model": chat["model"], "pending": True})
        chat["status"] = "running"
        chat["updated"] = now
        _save(chat)
        command = [exe, "run", "--format", "json"]
        if chat["model"]:
            command += ["-m", chat["model"]]
        if chat["session"]:
            command += ["-s", chat["session"]]
        else:
            command += ["--title", chat["title"]]
        command.append(text)
        process = subprocess.Popen(
            command, cwd=str(workspace()), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **_no_window(),
        )
        _running[chat["id"]] = process
    threading.Thread(target=_collect, args=(chat["id"], process), daemon=True).start()
    return chat


def _collect(chat_id: str, process: subprocess.Popen) -> None:
    """Read the JSON event stream, writing the reply into the chat file as it grows."""
    timer = threading.Timer(CHAT_TIMEOUT_S, process.kill)
    timer.start()
    parts: dict[str, str] = {}
    order: list[str] = []
    session = ""
    last_write = 0.0
    try:
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            session = session or event.get("sessionID", "")
            part = event.get("part") or {}
            if event.get("type") == "text" and part.get("text") is not None:
                key = part.get("id") or str(len(order))
                if key not in parts:
                    order.append(key)
                parts[key] = part["text"]
                if time.time() - last_write > 0.4:
                    _update(chat_id, "\n\n".join(parts[k].strip() for k in order), session, done=False)
                    last_write = time.time()
        process.wait()
    finally:
        timer.cancel()
        error = (process.stderr.read() or "").strip() if process.stderr else ""
        reply = "\n\n".join(parts[k].strip() for k in order).strip()
        if not reply and process.returncode not in (0, None):
            reply = f"_OpenCode stopped ({process.returncode})._" + (f"\n\n```\n{error[-1500:]}\n```" if error else "")
        with _lock:
            _running.pop(chat_id, None)
        _update(chat_id, reply or "_No reply._", session, done=True)


def _update(chat_id: str, reply: str, session: str, done: bool) -> None:
    with _lock:
        chat = load(chat_id)
        if not chat:
            return
        if session and not chat.get("session"):
            chat["session"] = session
        last = chat["messages"][-1] if chat["messages"] else None
        if last and last.get("role") == "assistant" and last.get("pending"):
            last["text"] = reply
            if done:
                last.pop("pending", None)
                last["at"] = time.time()
        if done:
            chat["status"] = "idle"
        chat["updated"] = time.time()
        _save(chat)


def stop(chat_id: str) -> None:
    with _lock:
        process = _running.get(chat_id)
    if process and process.poll() is None:
        process.kill()

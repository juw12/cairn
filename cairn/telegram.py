"""Cairn's status in Telegram, once a day.

A bot of your own tells you each evening whether Cairn is healthy (app build, brain sync, session
watcher, phone access, GitHub Actions minutes, PC load) and what the day held (AI sessions, time,
checks, what is still live, open tasks). `/status` in the chat gives the same at any moment.

The bot itself runs on a small Cloudflare Worker (worker/cairn-telegram.js), not on this PC, because
office networks often block Telegram and a PC that is off cannot report that it is off. This module
writes the status text and hands it to the Worker whenever it changed (at least once an hour); the
Worker sends it at the chosen hour and says so when the PC has gone quiet.

    cairn telegram setup <bot token>   connect a bot from @BotFather, then open the link it prints
    cairn telegram show                print the status text (sends nothing)
    cairn telegram send                hand the status over and have the bot send it now
    cairn telegram status              what is connected, and when the Worker last heard from this PC
    cairn telegram off                 forget the bot

The text holds session titles, counts and times: no note bodies, log lines, file names or paths.
The Worker's address and key are kept in ~/.cairn/telegram.json (the key encrypted for this Windows
user). The bot token goes straight to the Worker and is never written on this PC.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, chat, core, github_usage, relay, vibe, watch

DEFAULT_HOUR = 21
CHECK_SECONDS = 5 * 60  # how often the desktop app rebuilds the text
MIN_GAP_SECONDS = 10 * 60  # a changed status is handed over at most this often
HEARTBEAT_SECONDS = 60 * 60  # an unchanged one is repeated this often, so the Worker knows the PC is on
TITLE_CHARS = 60
MAX_LIVE = 3
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
WORKER_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?$")


class TelegramError(RuntimeError):
    pass


# ------------------------------------------------------------ settings on this PC


def config_path() -> Path:
    return chat.home() / "telegram.json"


def state_path() -> Path:
    return chat.home() / "telegram-state.json"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def load_config() -> dict:
    """{"worker", "key", "hour", "bot"} once a Worker is known, else {}."""
    config = _read_json(config_path())
    return config if WORKER_RE.match(str(config.get("worker", ""))) and config.get("key") else {}


def save_worker(worker: str, key: str) -> dict:
    """Remember where the Worker is and its key (the CAIRN_KEY secret it was deployed with)."""
    worker = str(worker or "").strip().rstrip("/")
    if not WORKER_RE.match(worker):
        raise TelegramError("The Worker address should look like https://cairn-telegram.<you>.workers.dev")
    if len(str(key or "").strip()) < 16:
        raise TelegramError("The key is the CAIRN_KEY secret the Worker was deployed with (16 characters or more).")
    config = _read_json(config_path())
    config.update({"worker": worker, "key": relay.seal_token(key.strip())})
    config.setdefault("hour", DEFAULT_HOUR)
    _write_json(config_path(), config)
    return config


def _hour(value) -> int:
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return DEFAULT_HOUR
    return hour if 0 <= hour <= 23 else DEFAULT_HOUR


# ------------------------------------------------------------ the status text


def _short(text: str, limit: int = TITLE_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _duration(minutes: int) -> str:
    hours, rest = divmod(max(0, int(minutes)), 60)
    return f"{hours}h {rest:02d}m" if hours else f"{rest}m"


def _folder_notes(brain, folder: str) -> list[dict]:
    """The notes of one folder of the brain, without reading the rest of a large vault."""
    notes = []
    for path in sorted((brain.dir / folder).glob("*.md")):
        try:
            notes.append(core.note_from_file(f"{folder}/{path.name}", path.read_text(encoding="utf-8-sig")))
        except (OSError, UnicodeDecodeError):
            continue
    return notes


def app_facts(hooks=None) -> dict:
    """What only the desktop app knows. `hooks` is its DesktopHooks; from a terminal the saved
    settings and a look at the app's port stand in."""
    from . import desktop

    config = hooks.config if hooks is not None else desktop.Config()
    phone = hooks.phone.status() if hooks is not None and hooks.phone is not None else None
    return {
        "version": __version__,
        "build": (hooks.loaded_head if hooks is not None else desktop.app_head()) or "",
        "running": True if hooks is not None else not desktop.port_free(desktop.PORT),
        "update": hooks.pending_update() if hooks is not None else "",
        "autoSync": bool(config.get("autoSync", True)),
        "vibeWatch": bool(config.get("vibeWatch", True)),
        "phoneAccess": bool(config.get("phoneAccess", False)),
        "phones": len(phone["devices"]) if phone else None,
        "phoneError": str(phone.get("error") or "") if phone else "",
    }


def _day_lines(brain, now: dt.datetime) -> tuple[list[str], list[str]]:
    day = now.date().isoformat()
    sessions = [note for note in vibe._notes(brain) if note["type"] == "session"]
    today = [note for note in sessions if core._session_day(note) == day]
    live = sorted(
        (note for note in sessions if note["status"] == "live"),
        key=lambda note: str(note["meta"].get("started", "")),
        reverse=True,
    )
    lines, problems = [], []
    if today:
        stats = core.vibe_stats(today, day, days=1)
        parts = [f"{stats[name]} {name}" for name in ("shipped", "partial", "dropped", "done") if stats[name]]
        parts += [f"{stats['live']} still live"] if stats["live"] else []
        lines.append(f"{_plural(len(today), 'AI session')}: " + ", ".join(parts))
        work = f"{_duration(stats['daily'][-1]['minutes'])} of session time"
        if stats["passed"] or stats["failed"]:
            work += f" · checks: {stats['passed']} passed, {stats['failed']} failed"
        lines.append(work)
        if stats["failed"]:
            problems.append(f"{_plural(stats['failed'], 'check')} failed in today's sessions")
    else:
        lines.append("No AI sessions today.")
    if live:
        names = "; ".join(_short(note["title"]) for note in live[:MAX_LIVE])
        more = f" (+{len(live) - MAX_LIVE} more)" if len(live) > MAX_LIVE else ""
        lines.append(f"Live now: {names}{more}")
    tasks = _folder_notes(brain, core.FOLDERS["task"])
    todo = sum(1 for note in tasks if note["status"] == "todo")
    doing = sum(1 for note in tasks if note["status"] == "doing")
    if todo or doing:
        lines.append(f"Tasks: {todo} to do, {doing} in progress")
    return lines, problems


def _brain_line(brain, problems: list[str]) -> str:
    name = f'Brain "{brain.root.name}"'
    try:
        status = brain.git_status()
    except Exception:  # a broken git install must not cost the whole report
        return f"{name}: git could not be asked"
    if not status["available"]:
        return f"{name}: not a git repo, so nothing is backed up"
    if not status["remote"]:
        return f"{name}: no GitHub remote, so it stays on this PC"
    waiting = len(status["changes"])
    if not waiting and not status["ahead"] and not status["behind"]:
        return f"{name}: in sync with GitHub"
    parts = [_plural(waiting, "change") + " not committed"] if waiting else []
    parts += [_plural(status["ahead"], "commit") + " not pushed"] if status["ahead"] else []
    parts += [_plural(status["behind"], "commit") + " to pull"] if status["behind"] else []
    if status["ahead"] or waiting > 50:
        problems.append("the brain has work that is not on GitHub yet")
    return f"{name}: " + ", ".join(parts)


def _watcher_line(brain, app: dict, now: dt.datetime, problems: list[str]) -> str:
    try:
        held = json.loads(watch._claim_path(brain).read_text(encoding="utf-8"))
        seen = float(held.get("at", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        seen = 0.0
    quiet = now.timestamp() - seen
    if seen and quiet < watch.CLAIM_STALE_SECONDS:
        return "Session watcher: running"
    if not app.get("vibeWatch", True):
        return "Session watcher: switched off in Settings"
    if not seen:
        return "Session watcher: has not run on this brain yet"
    problems.append("the session watcher stopped")
    return f"Session watcher: stopped, last pass {dt.datetime.fromtimestamp(seen):%d %b %H:%M}"


def _github_line(brain, now: dt.datetime, problems: list[str]) -> str:
    try:
        meta = github_usage._note_meta(brain, f"{now:%Y-%m}")
    except Exception:
        meta = None
    if not meta or not str(meta.get("gh_used", "")).isdigit():
        return ""
    used, limit = int(meta["gh_used"]), int(meta.get("gh_limit") or 0)
    line = f"GitHub Actions: {used:,} of {limit:,} free minutes ({meta.get('gh_pct', '?')}%)"
    if meta.get("gh_resets"):
        line += f", starts again {meta['gh_resets']}"
    level = str(meta.get("gh_level", ""))
    if level in ("warn", "critical", "over"):
        problems.append({
            "warn": "GitHub Actions minutes are on course to run out this month",
            "critical": "GitHub Actions minutes are nearly used up",
            "over": "GitHub Actions minutes are used up",
        }[level])
    return line


def _machine_line(brain) -> str:
    notes = [note for note in _folder_notes(brain, watch.MACHINE_FOLDER) if note["meta"].get("machine_at")]
    if not notes:
        return ""
    meta = max(notes, key=lambda note: str(note["meta"]["machine_at"]))["meta"]
    why = str(meta.get("load_why") or "")
    return f"PC load: {meta.get('load') or 'unknown'}" + (f" ({why})" if why else "")


def status_text(brain, app: dict | None = None, now: dt.datetime | None = None) -> str:
    """The whole message. Times of day are left out on purpose: the Worker adds how old it is, and
    a text that only changes when something happened does not need sending again."""
    now = now or dt.datetime.now()
    app = app_facts() if app is None else app
    day_lines, problems = _day_lines(brain, now)

    build = f" ({app['build'][:7]})" if app.get("build") else ""
    if not app.get("running", True):
        app_line = f"Desktop app {app['version']}{build}: not running"
        problems.append("the desktop app is not running")
    elif app.get("update") == "restart":
        app_line = f"Desktop app {app['version']}{build}: an update is waiting for a restart"
        problems.append("an update is waiting for Cairn to restart")
    else:
        app_line = f"Desktop app {app['version']}{build}: running"
    health = [app_line, _brain_line(brain, problems)]
    if not app.get("autoSync", True):
        health.append("Automatic sync: switched off in Settings")
    health.append(_watcher_line(brain, app, now, problems))
    if app.get("phoneAccess"):
        phones = app.get("phones")
        health.append("Phone access: on" + (f", {_plural(phones, 'phone')} paired" if phones is not None else ""))
        if app.get("phoneError"):
            problems.append("phone access could not start")
    else:
        health.append("Phone access: off")
    health += [line for line in (_github_line(brain, now, problems), _machine_line(brain)) if line]

    name = platform.node() or "this PC"
    verdict = "Needs a look: " + "; ".join(problems) + "." if problems else "All good."
    return "\n".join([f"Cairn on {name} · {now:%a %d %b}", verdict, "", "Today", *day_lines, "", "Cairn", *health])


# ------------------------------------------------------------ talking to the Worker


def _routes() -> list:
    """The office proxy first (as the relay is set up), then the usual variables, then no proxy at
    all: whichever is up when the laptop moves between networks."""
    proxies = [str((relay.load_config() or {}).get("proxy", "") or "")]
    proxies.append(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "")
    routes = []
    for proxy in dict.fromkeys(p for p in proxies if p):
        routes.append(urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy})))
    routes.append(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    return routes


def _call(method: str, path: str, body: dict | None = None, timeout: float = 20) -> tuple[int, dict]:
    config = load_config()
    if not config:
        raise TelegramError("No Worker is set up yet: see `cairn telegram --help`.")
    request = urllib.request.Request(
        config["worker"] + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method=method,
    )
    request.add_header("Authorization", "Bearer " + relay.open_token(config["key"]))
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "cairn-telegram")
    last_error: Exception | None = None
    for opener in _routes():
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, _json(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _json(error.read())
        except OSError as error:  # this route is down: try the next
            last_error = error
    raise TelegramError(f"Couldn't reach the Worker at {config['worker']}: {last_error}")


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _utc_offset_minutes(now: float) -> int:
    offset = dt.datetime.fromtimestamp(now).astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def push(brain, app: dict | None = None, force: bool = False, now: float | None = None, call=None) -> dict:
    """Hand the status to the Worker when it changed (or `force`). Never raises for network trouble."""
    config = load_config()
    if not config:
        return {"sent": False, "why": "not set up"}
    now = time.time() if now is None else now
    text = status_text(brain, app, dt.datetime.fromtimestamp(now))
    state = _read_json(state_path())
    since = now - float(state.get("at", 0) or 0)
    if not force:
        if since < MIN_GAP_SECONDS:
            return {"sent": False, "why": "waiting"}
        if text == state.get("body") and since < HEARTBEAT_SECONDS:
            return {"sent": False, "why": "unchanged"}
    body = {"text": text, "hour": _hour(config.get("hour")), "offset": _utc_offset_minutes(now)}
    try:
        status, answer = (call or _call)("PUT", "/status", body)
    except TelegramError as error:
        return {"sent": False, "why": str(error)}
    if status != 200:
        return {"sent": False, "why": f"the Worker answered {status} {answer.get('error', '')}".strip()}
    _write_json(state_path(), {"at": now, "body": text})
    return {"sent": True, "text": text}


def setup(token: str, brain, hour: int | None = None, call=None) -> dict:
    """Give the Worker a bot token from @BotFather. Returns the bot's name and the link that pairs
    your chat with it (the code in the link is single-use)."""
    token = str(token or "").strip()
    if not TOKEN_RE.match(token):
        raise TelegramError("That isn't a bot token. @BotFather gives one like 123456789:AAE... after /newbot.")
    config = _read_json(config_path())
    if hour is not None:
        config["hour"] = _hour(hour)
        _write_json(config_path(), config)
    code = secrets.token_urlsafe(12)
    status, answer = (call or _call)("POST", "/setup", {"token": token, "code": code})
    if status != 200:
        reasons = {
            "telegram_refused_token": "Telegram doesn't know that token. Copy it again from @BotFather.",
            "forbidden": "The Worker didn't accept this PC's key. Save it again with `cairn telegram setup --worker ... --key ...`.",
        }
        raise TelegramError(reasons.get(answer.get("error", ""), f"The Worker answered {status} {answer.get('error', '')}".strip()))
    config = _read_json(config_path())
    config["bot"] = str(answer.get("bot", ""))
    _write_json(config_path(), config)
    first = push(brain, force=True, call=call)
    return {"bot": config["bot"], "link": f"https://t.me/{config['bot']}?start={code}", "pushed": first.get("sent", False)}


def send_now(brain, app: dict | None = None, call=None) -> dict:
    """Hand over a fresh status and have the bot send it to the paired chat right away."""
    pushed = push(brain, app, force=True, call=call)
    if not pushed.get("sent"):
        return pushed
    status, answer = (call or _call)("POST", "/send")
    if status == 200 and answer.get("sent"):
        return {"sent": True, "text": pushed["text"]}
    why = answer.get("why") or answer.get("error") or status
    if why == "not_paired":
        why = "no chat is paired yet: run `cairn telegram setup <bot token>` and open the link it prints"
    return {"sent": False, "why": str(why)}


def off(call=None) -> bool:
    """Forget the bot on the Worker and stop handing statuses over. The Worker's address is kept."""
    config = _read_json(config_path())
    had = bool(config.get("bot"))
    if load_config():
        (call or _call)("POST", "/off")
    config.pop("bot", None)
    _write_json(config_path(), config)
    _write_json(state_path(), {})
    return had


def status(call=None) -> dict:
    config = load_config()
    info = {"worker": config.get("worker", ""), "bot": config.get("bot", ""), "hour": _hour(config.get("hour"))}
    if config:
        code, answer = (call or _call)("GET", "/state")
        if code != 200:
            raise TelegramError(f"The Worker answered {code} {answer.get('error', '')}".strip())
        info.update({"bot": answer.get("bot", ""), "paired": bool(answer.get("paired")), "at": answer.get("at"), "sent": answer.get("sent", "")})
    return info


# ------------------------------------------------------------ the desktop app's worker


def run_loop(stop: threading.Event, current_brain, log=print, hooks=None, every: float = CHECK_SECONDS) -> None:
    """Keeps the Worker's copy of the status fresh. Quiet until a bot was set up."""
    failures = 0
    wait = 30.0
    while not stop.wait(wait):
        wait = every
        if not load_config().get("bot"):
            continue
        try:
            result = push(current_brain(), app_facts(hooks))
            if result.get("sent"):
                failures = 0
            elif result.get("why", "").startswith("Couldn't reach"):
                failures += 1
                if failures in (1, 12):
                    log(f"telegram: {result['why']}")
        except Exception as error:  # never let the report take the app down
            log(f"telegram failed: {error}")


def start_in_background(stop: threading.Event, current_brain, log=print, hooks=None) -> threading.Thread:
    thread = threading.Thread(target=run_loop, args=(stop, current_brain, log, hooks), daemon=True, name="cairn-telegram")
    thread.start()
    return thread

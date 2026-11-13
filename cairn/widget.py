"""Cairn on the iPhone home-screen widget (the juw12 trio widget: PocketDex, Haru, Cozy Island, Cairn).

The widget reads small JSON snapshots from a private ntfy.sh topic named by a widget code
(`jw-` + 32 hex characters, made on https://juw12.github.io/widget/). This module sends Cairn's
snapshot to that topic: how many vibe sessions are live, the newest one and its last step, and
what shipped today. The desktop app checks every few seconds and sends only when something
changed, so the widget's live view follows within seconds.

    cairn widget pair jw-...   remember the code (in ~/.cairn/widget.json)
    cairn widget send          send the snapshot now
    cairn widget status        show the code, the last send and today's budget
    cairn widget unpair        stop sending

Only titles, counts, step kinds and times are sent: no log text, file names or paths. Anyone who
has the code can read the snapshot, and ntfy.sh keeps it for about 12 hours.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import vibe

RELAY = "https://ntfy.sh"
CODE_RE = re.compile(r"^jw-[0-9a-f]{32}$")
APP = "cairn"
CHECK_SECONDS = 3  # how often the desktop app looks for changes (reads only the session notes)
MIN_GAP_SECONDS = 8  # at most one message per 8 s, so a burst of log lines becomes one update
RESEND_SECONDS = 6 * 60 * 60  # the relay forgets after ~12 h: repeat an unchanged snapshot
DAILY_BUDGET = 200  # ntfy.sh allows 250 messages a day per IP; leave room for retries
SLOW_AFTER = 150  # past this many today, send at most every 5 minutes
SLOW_GAP_SECONDS = 5 * 60
BACKOFF_SECONDS = 15 * 60  # after "429 too many requests"
TITLE_CHARS = 60
MAX_LIVE = 3
_LOG_LINE = re.compile(r"^- (\d{1,2}:\d{2}) ([a-z]+): ", re.M)


class WidgetError(RuntimeError):
    pass


def cairn_dir() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")


def config_path() -> Path:
    return cairn_dir() / "widget.json"


def state_path() -> Path:
    return cairn_dir() / "widget-state.json"


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


def load_code() -> str:
    code = str(_read_json(config_path()).get("code", "")).strip().lower()
    return code if CODE_RE.match(code) else ""


def pair(code: str) -> str:
    code = str(code or "").strip().lower()
    if not CODE_RE.match(code):
        raise WidgetError("That isn't a widget code. Copy it from https://juw12.github.io/widget/ (it starts with jw-).")
    config = _read_json(config_path())
    config["code"] = code
    _write_json(config_path(), config)
    _write_json(state_path(), {})  # a new code starts with a clean slate
    return code


def unpair() -> bool:
    had = bool(load_code())
    config = _read_json(config_path())
    config.pop("code", None)
    _write_json(config_path(), config)
    return had


def proxy_url() -> str:
    """Same proxy as the relay (office network), else the usual environment variables."""
    configured = str(_read_json(config_path()).get("proxy", "") or "")
    if configured:
        return configured
    try:
        from . import relay

        relay_proxy = str((relay.load_config() or {}).get("proxy", "") or "")
    except Exception:
        relay_proxy = ""
    return relay_proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""


# ------------------------------------------------------------ snapshot


def _short(text: str, limit: int = TITLE_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _stamp_ms(value) -> int:
    """`2026-11-13T14:02` (local time, as vibe writes it) -> epoch milliseconds, 0 when unreadable."""
    try:
        return int(dt.datetime.strptime(str(value)[:16], "%Y-%m-%dT%H:%M").timestamp() * 1000)
    except ValueError:
        return 0


def _last_step(body: str, now: dt.datetime) -> tuple[str, int]:
    """Kind and time of the newest `- HH:MM kind: ...` log line. Lines carry only a clock time:
    take today, or yesterday when that would be in the future (a session running past midnight)."""
    found = _LOG_LINE.findall(body or "")
    if not found:
        return "", 0
    clock, kind = found[-1]
    hour, minute = (int(part) for part in clock.split(":"))
    moment = dt.datetime.combine(now.date(), dt.time(hour % 24, minute % 60))
    if moment > now + dt.timedelta(minutes=1):
        moment -= dt.timedelta(days=1)
    return kind, int(moment.timestamp() * 1000)


def snapshot(brain, now: dt.datetime | None = None) -> dict:
    """What the widget shows for Cairn. Plain counts, titles and step kinds."""
    now = now or dt.datetime.now()
    day = now.date().isoformat()
    sessions = [note for note in vibe._notes(brain) if note["type"] == "session"]
    live = [note for note in sessions if note["status"] == "live"]
    live.sort(key=lambda note: str(note["meta"].get("started", "")), reverse=True)
    ended = [note for note in sessions if note["status"] != "live" and str(note["meta"].get("ended", "")).startswith(day)]
    ended.sort(key=lambda note: str(note["meta"].get("ended", "")), reverse=True)

    live_items = []
    for note in live[:MAX_LIVE]:
        kind, at = _last_step(note.get("body", ""), now)
        live_items.append({
            "t": _short(note["title"]),
            "k": kind,
            "at": at,
            "since": _stamp_ms(note["meta"].get("started", "")),
        })
    result = {
        "day": day,
        "live": len(live),
        "sessions": live_items,
        "shipped": sum(1 for note in ended if note["status"] == "shipped"),
        "ended": len(ended),
    }
    if ended:
        last = ended[0]
        result["last"] = {"t": _short(last["title"]), "r": last["status"], "at": _stamp_ms(last["meta"].get("ended", ""))}
    return result


# ------------------------------------------------------------ sending


def _post(code: str, message: str, timeout: float = 15) -> int:
    routes = []
    proxy = proxy_url()
    if proxy:
        routes.append(urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy})))
    routes.append(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    request = urllib.request.Request(f"{RELAY}/{code}", data=message.encode("utf-8"), method="POST")
    request.add_header("User-Agent", "cairn-widget")
    last_error: Exception | None = None
    for opener in routes:
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code
        except OSError as error:  # this route is down (laptop away from the office proxy): try the next
            last_error = error
    raise WidgetError(f"Couldn't reach {RELAY}: {last_error}")


def message_for(snap: dict, now_ms: int) -> str:
    return json.dumps({"v": 1, "app": APP, "at": now_ms, "d": snap}, separators=(",", ":"), ensure_ascii=False)


def publish(brain, force: bool = False, now: float | None = None, post=None) -> dict:
    """Send the snapshot when it changed (or `force`). Returns what happened, never raises for network trouble."""
    code = load_code()
    if not code:
        return {"sent": False, "why": "not paired"}
    now = time.time() if now is None else now
    state = _read_json(state_path())
    today = dt.date.fromtimestamp(now).isoformat()
    if state.get("day") != today:
        state.update({"day": today, "count": 0})
    snap = snapshot(brain, dt.datetime.fromtimestamp(now))
    body = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    last_at = float(state.get("at", 0) or 0)
    if not force:
        if now < float(state.get("backoff", 0) or 0):
            return {"sent": False, "why": "relay asked us to slow down"}
        if body == state.get("body") and now - last_at < RESEND_SECONDS:
            return {"sent": False, "why": "unchanged"}
        count = int(state.get("count", 0) or 0)
        if count >= DAILY_BUDGET:
            return {"sent": False, "why": "daily budget used"}
        gap = SLOW_GAP_SECONDS if count >= SLOW_AFTER else MIN_GAP_SECONDS
        if now - last_at < gap:
            return {"sent": False, "why": "waiting", "due": last_at + gap}
    try:
        status = (post or _post)(code, message_for(snap, int(now * 1000)))
    except WidgetError as error:
        return {"sent": False, "why": str(error)}
    if status == 429:
        state["backoff"] = now + BACKOFF_SECONDS
        _write_json(state_path(), state)
        return {"sent": False, "why": "relay said too many requests", "status": status}
    if status >= 300:
        return {"sent": False, "why": f"relay answered {status}", "status": status}
    state.update({"at": now, "body": body, "count": int(state.get("count", 0) or 0) + 1})
    _write_json(state_path(), state)
    return {"sent": True, "snapshot": snap, "count": state["count"]}


def status() -> dict:
    state = _read_json(state_path())
    today = dt.date.today().isoformat()
    return {
        "code": load_code(),
        "last": state.get("at"),
        "today": int(state.get("count", 0) or 0) if state.get("day") == today else 0,
        "budget": DAILY_BUDGET,
    }


def run_loop(stop: threading.Event, current_brain, log=print, every: float = CHECK_SECONDS) -> None:
    """Desktop app worker: look for changes every few seconds and send them. Quiet until paired."""
    failures = 0
    while not stop.wait(every):
        if not load_code():
            continue
        try:
            result = publish(current_brain())
            if result.get("sent"):
                failures = 0
            elif result.get("why", "").startswith("Couldn't reach"):
                failures += 1
                if failures in (1, 20):
                    log(f"widget: {result['why']}")
        except Exception as error:  # never let the widget take the app down
            log(f"widget failed: {error}")


def start_in_background(stop: threading.Event, current_brain, log=print) -> threading.Thread:
    thread = threading.Thread(target=run_loop, args=(stop, current_brain, log), daemon=True, name="cairn-widget")
    thread.start()
    return thread

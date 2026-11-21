"""Cairn Pro: the paid part of Cairn is the Autopilot. Everything else stays free.

The first goal on a computer is free. After that the Autopilot takes new goals only with a licence
key, which comes with the Cairn Pro subscription on Polar. Goals already handed over keep running.

The key is checked with Polar's public licence endpoint, so the app holds no secret. A good answer
is kept for 14 days (a laptop away from the network keeps working) and asked again about once a
day. Only the key and Polar's yes or no are kept, in ~/.cairn/pro.json; nothing about the buyer.
The source is open, so this is a fair-play check, not copy protection. Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import precedent

ORGANIZATION_ID = "72517102-9db8-45d9-b955-eaacf4bca119"
CHECKOUT_URL = "https://buy.polar.sh/polar_cl_Lolm6R62rsxDP4DcmBvM9Uv3rPklvmS7p7ZlD4V2Uwf"
VALIDATE_URL = "https://api.polar.sh/v1/customer-portal/license-keys/validate"
PRICE = "USD 8 a month or USD 60 a year"
RECHECK_SECONDS = 24 * 3600
# After a check that got no answer, the next goal does not wait on the network again for an hour.
RETRY_SECONDS = 3600
GRACE_SECONDS = 14 * 24 * 3600
BLOCKED = (
    "Your free goal is used. New goals need Cairn Pro ("
    + PRICE
    + "): "
    + CHECKOUT_URL
    + " Then paste the key on the Autopilot page, or run: cairn pro activate <key>"
)

LOCK = threading.RLock()


class ProError(ValueError):
    """Shown to the person as is (the server answers 400 with this text)."""


class Unreachable(ProError):
    """Polar gave no answer; nothing is known about the key."""


def path() -> Path:
    return precedent.home_dir() / "pro.json"


def _read() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, target)


def _past(stamp: str, now: float) -> bool:
    """True when an ISO time from Polar (`expires_at`) is behind us. No time means no end."""
    if not stamp:
        return False
    try:
        moment = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.timestamp() <= now


def _routes() -> list:
    """The office proxy first when one is set (same as the widget and the relay), then straight out."""
    from . import widget

    routes = []
    proxy = widget.proxy_url()
    if proxy:
        routes.append(urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy})))
    routes.append(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    return routes


def ask_polar(key: str, timeout: float = 10) -> dict:
    """What Polar says about a key: `{"granted": bool, "expires_at": str}`.

    Only Polar's own answers count: 200 with a licence, or its 404 for a key it does not know (an
    ended subscription answers the same way). A proxy's error page or a timeout says nothing about
    the key, so that raises Unreachable and the last good answer stays in use.
    """
    body = json.dumps({"key": key, "organization_id": ORGANIZATION_ID}).encode("utf-8")
    request = urllib.request.Request(VALIDATE_URL, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "cairn-pro")
    problem = "no route"
    for opener in _routes():
        try:
            with opener.open(request, timeout=timeout) as response:
                answer = json.loads(response.read().decode("utf-8", "replace"))
            if isinstance(answer, dict) and "status" in answer:
                return {"granted": answer["status"] == "granted", "expires_at": str(answer.get("expires_at") or "")}
            problem = "an answer that is not a licence"
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", "replace")
            if error.code == 404 and "ResourceNotFound" in text:
                return {"granted": False, "expires_at": ""}
            problem = f"HTTP {error.code}"
        except (OSError, ValueError) as error:  # this route is down, or the page was not JSON
            problem = str(error)
    raise Unreachable(f"Couldn't reach Polar to check the key ({problem}). Try again when you are online.")


def is_active(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    data = _read()
    if not data.get("key") or not data.get("granted"):
        return False
    return now - float(data.get("checked_at", 0)) < GRACE_SECONDS and not _past(data.get("expires_at", ""), now)


def refresh(now: float | None = None, force: bool = False) -> None:
    """Ask Polar again when the last answer is a day old. Network trouble changes nothing."""
    now = time.time() if now is None else now
    with LOCK:
        data = _read()
        if not data.get("key"):
            return
        due = now - float(data.get("checked_at", 0)) >= RECHECK_SECONDS and now - float(data.get("tried_at", 0)) >= RETRY_SECONDS
        if not (force or due):
            return
        data["tried_at"] = now
        _write(data)
        try:
            answer = ask_polar(data["key"])
        except Unreachable:
            return
        data.update(granted=answer["granted"], expires_at=answer["expires_at"], checked_at=now)
        _write(data)


def allowed(now: float | None = None) -> bool:
    """May a new goal start? Yes with Cairn Pro, or while the free goal is unused."""
    refresh(now)
    return is_active(now) or not _read().get("trial_used")


def check(now: float | None = None) -> None:
    if not allowed(now):
        raise ProError(BLOCKED)


def goal_started(now: float | None = None) -> None:
    """A goal was accepted: without Cairn Pro that was the free one."""
    now = time.time() if now is None else now
    with LOCK:
        if is_active(now):
            return
        data = _read()
        if not data.get("trial_used"):
            data["trial_used"] = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
            _write(data)


def activate(key: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    key = "".join(str(key or "").split())
    if not key or len(key) > 200:
        raise ProError("Paste the licence key from your Cairn Pro e-mail.")
    answer = ask_polar(key)
    if not answer["granted"] or _past(answer["expires_at"], now):
        raise ProError("Polar doesn't know this key, or its subscription has ended.")
    with LOCK:
        data = _read()
        data.update(key=key, granted=True, expires_at=answer["expires_at"], checked_at=now, tried_at=now)
        _write(data)
    return status(now)


def remove() -> dict:
    """Take the key off this computer. The subscription itself is managed on Polar."""
    with LOCK:
        data = _read()
        for name in ("key", "granted", "expires_at", "checked_at", "tried_at"):
            data.pop(name, None)
        _write(data)
    return status()


def status(now: float | None = None) -> dict:
    """For the Autopilot page and `cairn pro`. The key is never sent back whole."""
    now = time.time() if now is None else now
    data = _read()
    key = str(data.get("key") or "")
    checked = float(data.get("checked_at", 0))
    return {
        "active": is_active(now),
        "trial_left": not data.get("trial_used"),
        "key": f"...{key[-4:]}" if key else "",
        "checked": dt.datetime.fromtimestamp(checked).strftime("%Y-%m-%d") if checked else "",
        "price": PRICE,
        "checkout": CHECKOUT_URL,
    }

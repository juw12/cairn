"""Read GitHub for the app's own pages as the account the GitHub CLI is signed in to on this computer.

A page that asks api.github.com without a sign-in gets 60 answers an hour for everyone behind the same
address, and then "Couldn't load the repos". On the computer itself there is usually a sign-in already:
the GitHub CLI's, the one picked under Pushes from this computer. `GET /api/github/read?path=...` asks
GitHub with it (5,000 answers an hour) and hands the answer back, so the page never holds the token.

What it will do is narrow on purpose:

- reads only: one GET, to api.github.com, for the paths in ALLOWED (users, orgs, repos, the signed-in
  user and their repos, repository search, the rate limit). Nothing that changes anything on GitHub.
- only where the server allows GitHub actions at all (never for a paired phone, see phone.py).
- no token is printed, stored on disk or sent to the page. It is kept in memory for TOKEN_TTL_SECONDS,
  so a switch of account made in a terminal is followed within a minute; a switch made in Cairn calls
  `forget()` and is followed at once.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import cloud_backup, publish

API = "https://api.github.com"
OFF_SWITCH = "CAIRN_NO_GITHUB_READ"  # set to anything: pages ask GitHub themselves, as before (the browser tests do)
TOKEN_TTL_SECONDS = 60
TIMEOUT_SECONDS = 30
MAX_BYTES = 25 * 1024 * 1024
AT_ONCE = 6  # the Projects page asks for many repos at once; GitHub and a small computer get them in turn

ALLOWED = re.compile(r"^/(?:user|user/repos|rate_limit|search/repositories|(?:users|orgs|repos)/[^\s?#]+)$")
ACCEPT = re.compile(r"^application/vnd\.github[A-Za-z0-9.+\-]*(?:\+json)?$")
# What the page reads from an answer besides its body (docs/github.js, docs/relay.js, docs/dash.js).
PASSED_ON = ("Content-Type", "ETag", "Link", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")

_token: dict = {"value": "", "at": float("-inf")}
_lock = threading.Lock()
_turn = threading.BoundedSemaphore(AT_ONCE)


class ReadError(Exception):
    """A read that was not made, with the HTTP status the page gets and a sentence that says why."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    """True where this computer has a GitHub CLI and the off switch is not set. Whether the CLI is
    signed in shows at the first read: asking here would hold up the page's first request."""
    return not os.environ.get(OFF_SWITCH) and bool(cloud_backup.gh())


def allowed(target: str) -> bool:
    """True for a path (with its query) this module will ask GitHub for."""
    path, _, query = str(target or "").partition("?")
    if not ALLOWED.fullmatch(path) or ".." in path.split("/"):
        return False
    return not re.search(r"[\s#\x00-\x1f]", query)


def _cli_token() -> str:
    """The token of the account the GitHub CLI uses now, or "" without a CLI or a sign-in."""
    gh = cloud_backup.gh()
    if not gh:
        return ""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([gh, "auth", "token", "--hostname", "github.com"], capture_output=True, text=True,
                                timeout=20, cwd=str(Path.home()), creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def token(find=None) -> str:
    """The CLI's token, asked for at most once in TOKEN_TTL_SECONDS (also when there is none)."""
    with _lock:
        if time.monotonic() - _token["at"] >= TOKEN_TTL_SECONDS:
            _token.update(value=(find or _cli_token)(), at=time.monotonic())
        return _token["value"]


def forget() -> None:
    """Ask the CLI again at the next read: the account was just switched."""
    with _lock:
        _token.update(value="", at=float("-inf"))


def read(target: str, accept: str = "", if_none_match: str = "", find=None, opener=None) -> tuple[int, dict, bytes]:
    """One GET of `target` (a path of the GitHub API with its query) as the CLI's account.

    Returns GitHub's (status, headers worth passing on, body), also for its own refusals (404, 403,
    304): the page turns those into sentences. Raises ReadError for what GitHub was never asked:
    400 a path outside ALLOWED, 503 no CLI sign-in on this computer, 502 GitHub not reached."""
    if os.environ.get(OFF_SWITCH):
        raise ReadError(503, "Reading GitHub through this computer's sign-in is switched off.")
    if not allowed(target):
        raise ReadError(400, "Only reads of users, orgs, repos and repository search go through this computer's GitHub sign-in.")
    secret = token(find)
    if not secret:
        raise ReadError(503, "The GitHub CLI is not signed in on this computer. Sign in once with: gh auth login")
    headers = {
        "Authorization": f"Bearer {secret}",
        "Accept": accept if ACCEPT.fullmatch(accept or "") else "application/vnd.github+json",
        "User-Agent": "cairn",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if if_none_match and not re.search(r"[\r\n]", if_none_match):
        headers["If-None-Match"] = if_none_match
    request = urllib.request.Request(API + target, method="GET", headers=headers)
    opener = opener or (lambda req: urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=publish.ssl_context()))
    with _turn:
        try:
            with opener(request) as response:
                status, found, body = response.status, response.headers, response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:  # GitHub answered, with a refusal
            status, found, body = error.code, error.headers, error.read(MAX_BYTES + 1)
            error.close()
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
            raise ReadError(502, f"Can't reach GitHub from this computer ({getattr(error, 'reason', error)}).") from None
    if len(body) > MAX_BYTES:
        raise ReadError(502, "GitHub's answer was too large to pass on.")
    if status == 401:
        forget()  # the sign-in was revoked or replaced: ask the CLI again next time
        raise ReadError(503, "GitHub no longer accepts the GitHub CLI's sign-in on this computer. Sign in again with: gh auth login")
    passed = {name: found.get(name) for name in PASSED_ON if found is not None and found.get(name)}
    return status, passed, body

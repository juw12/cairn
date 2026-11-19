"""Which of your GitHub accounts this computer pushes as, and switching between them.

A push is signed in, and the sign-in decides whose name GitHub puts on the push, on a pull request
opened from here and on a release. Who wrote a commit is a different thing (git's `user.name` and
`user.email`) and is never touched here.

The switch is the GitHub CLI's own. `gh auth switch` picks which of the accounts signed in on this
computer is the active one, and `gh auth setup-git` (once) makes git ask the CLI for its sign-in to
github.com. From then on `git push`, `gh` and every tool that uses them follow the one choice, so an
assistant working on this computer pushes as the account picked on the Vibe page without being told.

No token is shown or stored: who pushes is found on this computer, by comparing the sign-in git would
send to github.com with the CLI's. A repo whose address names a user (`https://name@github.com/...`),
an SSH address, or a helper set inside one repo still wins there.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import github_usage

HOST = "github.com"
ACCOUNTS_TTL_SECONDS = 600  # the list of signed-in accounts is the one part that asks GitHub
SOON_TTL_SECONDS = 20  # the live page shows a switch made elsewhere (a terminal, an assistant) this soon
UNDO = f"git config --global --unset-all credential.https://{HOST}.helper"

_accounts: dict = {"at": float("-inf"), "names": []}
_soon: dict = {"value": None, "at": float("-inf"), "thread": None}
_soon_lock = threading.Lock()


class PushError(Exception):
    """A switch that was not made, with a sentence a person can act on."""


def _run(args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    """One git or gh command, from the home folder (so no repo's own settings answer) and with every
    way of asking the person for a password turned off. (127, "", "") when the program is missing."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GH_PROMPT_DISABLED": "1"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=20, env=env, cwd=str(Path.home()), creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return 127, "", ""
    return result.returncode, result.stdout, result.stderr


def signed_in(run=None, fresh: bool = False) -> list[str]:
    """The accounts the GitHub CLI is signed in to on this computer. Kept for ten minutes (also when
    there are none, or no CLI), because listing them makes the CLI check each sign-in with GitHub."""
    if run is None and not fresh and time.monotonic() - _accounts["at"] < ACCOUNTS_TTL_SECONDS:
        return list(_accounts["names"])
    names = _list_accounts(_run if run is None else run)
    if run is None:
        _accounts.update(at=time.monotonic(), names=names)
    return list(names)


def _list_accounts(run) -> list[str]:
    code, out, err = run(["gh", "auth", "status", "--hostname", HOST, "--json", "hosts"])
    names: list[str] = []
    try:
        names = [str(entry.get("login", "")) for entry in json.loads(out)["hosts"].get(HOST, [])]
    except (ValueError, KeyError, TypeError, AttributeError):
        if code != 127:  # a GitHub CLI from before --json: read its sentences
            _, out, err = run(["gh", "auth", "status", "--hostname", HOST])
            names = re.findall(rf"Logged in to {re.escape(HOST)} (?:account|as) (\S+)", out + err)
    seen: list[str] = []
    for name in names:
        if github_usage.LOGIN_RE.fullmatch(name) and name.lower() not in (known.lower() for known in seen):
            seen.append(name)
    return sorted(seen, key=str.lower)  # the CLI lists the active one first; a chip must not move when pressed


def _git_sign_in(run) -> tuple[str, str]:
    """(user, password) git would send to github.com right now. The password never leaves this module."""
    _, out, _ = run(["git", "credential", "fill"], f"protocol=https\nhost={HOST}\n\n")
    found = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return found.get("username", ""), found.get("password", "")


def _asks_cli(run) -> bool:
    _, out, _ = run(["git", "config", "--get-urlmatch", "credential.helper", f"https://{HOST}"])
    return "auth git-credential" in out


def status(run=None, fresh: bool = False) -> dict:
    """Who a push from this computer goes out as, and who it could.

    `accounts` are the CLI's sign-ins and `active` the one it uses. `pusher` is the account git signs
    in to github.com as; `sure` says that sign-in is that account's CLI sign-in (compared here, not
    guessed from a name), and `viaCli` that git asks the CLI, so the two cannot drift apart. With
    fewer than two accounts there is nothing to switch and git is not asked anything."""
    names = signed_in(run, fresh)
    run = _run if run is None else run
    reading = {"accounts": names, "active": "", "pusher": "", "sure": False, "viaCli": False}
    if len(names) < 2:
        return reading
    _, active, _ = run(["gh", "config", "get", "user", "--host", HOST])
    reading["active"] = next((name for name in names if name.lower() == active.strip().lower()), "")
    reading["viaCli"] = _asks_cli(run)
    user, password = _git_sign_in(run)
    if password:
        for name in names:
            code, token, _ = run(["gh", "auth", "token", "--hostname", HOST, "--user", name])
            if code == 0 and token.strip() == password:
                reading.update(pusher=name, sure=True)
                break
    if not reading["pusher"] and github_usage.LOGIN_RE.fullmatch(user):
        reading["pusher"] = user  # a sign-in git saved by itself, under the name it was saved with
    return reading


def status_soon() -> dict | None:
    """`status` for the live page, which must never wait on it: the last reading at once, refreshed
    in the background one run at a time. None until the first reading is in."""
    with _soon_lock:
        if _soon["thread"] is None and time.monotonic() - _soon["at"] >= SOON_TTL_SECONDS:
            def refresh() -> None:
                try:
                    _soon["value"] = status()
                except Exception:  # a nicety: it must never take a request down
                    pass
                finally:
                    _soon["at"] = time.monotonic()
                    _soon["thread"] = None

            _soon["thread"] = threading.Thread(target=refresh, daemon=True)
            _soon["thread"].start()
    return _soon["value"]


def summary(reading: dict) -> str:
    if len(reading["accounts"]) < 2:
        only = f" ({reading['accounts'][0]})" if reading["accounts"] else ""
        return f"The GitHub CLI is signed in to {len(reading['accounts'])} account{only} on this computer, so there is nothing to switch. Add one with: gh auth login"
    if not reading["pusher"]:
        return "git has no sign-in for github.com on this computer yet. Pick an account: cairn vibe github push-as NAME"
    how = "git asks the GitHub CLI" if reading["viaCli"] and reading["sure"] else "a sign-in git saved by itself; the first switch makes git ask the GitHub CLI instead"
    others = ", ".join(name for name in reading["accounts"] if name.lower() != reading["pusher"].lower())
    return f"Pushes from this computer go to GitHub as {reading['pusher']} ({how}). Also signed in: {others}."


def switch(name: str, run=None) -> dict:
    """Make `name` the account this computer pushes as. Returns the new `status` with a `message`.
    Raises PushError when the CLI is not signed in as it, or when git still signs in as someone else."""
    try:
        wanted = github_usage.account_name(name)
    except ValueError as error:
        raise PushError(str(error)) from None
    names = signed_in(run, fresh=True)
    account = next((known for known in names if known.lower() == wanted.lower()), "")
    if not account:
        raise PushError(f"The GitHub CLI is not signed in as {wanted} on this computer. Sign in once with: gh auth login")
    call = _run if run is None else run
    code, _, err = call(["gh", "auth", "switch", "--hostname", HOST, "--user", account])
    if code != 0:
        raise PushError(f"The GitHub CLI could not switch to {account}{': ' + err.strip() if err.strip() else '.'}")
    set_up = not _asks_cli(call)
    if set_up:
        code, _, err = call(["gh", "auth", "setup-git", "--hostname", HOST])
        if code != 0:
            raise PushError(f"The GitHub CLI is on {account} now, but git could not be told to ask it{': ' + err.strip() if err.strip() else '.'}")
    reading = status(run)
    if not reading["sure"] or reading["pusher"].lower() != account.lower():
        who = reading["pusher"] or "nobody"
        raise PushError(f"The GitHub CLI is on {account} now, but git on this computer still signs in as {who}. Something set outside Cairn decides it.")
    reading["message"] = f"Pushes from this computer now go to GitHub as {account}." + (f" git asks the GitHub CLI for its sign-in from now on; to undo that: {UNDO}" if set_up else "")
    if run is None:
        with _soon_lock:
            _soon.update(value={key: value for key, value in reading.items() if key != "message"}, at=time.monotonic())
    return reading

"""A full copy of every repo of your GitHub accounts on this computer (`cairn vibe github copy FOLDER`).

GitHub is where the repos live, where other people find them and what your devices sync through.
Cairn runs on your own computers, which sleep and sit behind office networks, so it does not try to
stand in for that. What it can do is make sure GitHub is not the only place your work exists: one
locked account, one deleted repo or one force-push would otherwise be the end of it.

The copy is plain git. Each repo is a mirror (`git clone --mirror`: every branch, every tag, the
whole history) in `FOLDER/owner/name.git`, and a later run only fetches the repos pushed to since.
To work from a copy: `git clone FOLDER/owner/name.git`. To put one on another host:
`git --git-dir FOLDER/owner/name.git push --all NEW-URL` and the same with `--tags`.

Not in a copy, because they are not in git: issues, pull request talk, releases and their files,
wikis, Actions settings and secrets, and files kept in Git LFS.

The folder is remembered per computer in ~/.cairn/github-copy.json; with it set, the watcher brings
the copy up to date once a day. A repo that is gone from GitHub keeps its copy. The sign-in is the
GitHub CLI's, handed to git for one command at a time: never written into the copy, never printed.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import core, github_push, github_usage, machine, publish
from .brain import Brain, ConflictError

FOLDER = f"{github_usage.FOLDER}/copy"  # where each computer's note is kept in the brain
STATE_NAME = "copy.json"  # in the copy folder: what was copied and when
REFRESH_HOURS = 24  # the watcher brings the copy up to date this often
RETRY_SECONDS = 1800  # and waits this long after a try, whatever came of it
GIT_SECONDS = 1800  # the longest one clone or fetch may take

_watch: dict = {"thread": None, "after": 0.0}
_watch_lock = threading.Lock()


class CopyError(Exception):
    """A copy that was not made, with a sentence a person can act on."""


# ------------------------------------------------------------ the folder, remembered per computer


def settings_path() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn") / "github-copy.json"


def settings() -> dict:
    """{"folder": str, "accounts": [names]} as last asked for on this computer, or {} when never."""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("folder") else {}


def remember(folder: Path, accounts: list[str]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"folder": str(folder), "accounts": accounts}, indent=2) + "\n", encoding="utf-8")


def load_state(folder: Path) -> dict:
    try:
        state = json.loads((folder / STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or not isinstance(state.get("repos"), dict):
        state = {"repos": {}}
    return state


def _save_state(folder: Path, state: dict) -> None:
    temp = folder / (STATE_NAME + ".tmp")
    temp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, folder / STATE_NAME)


# ------------------------------------------------------------ asking GitHub, running git


def fetch_repos(http) -> list[dict]:
    """Every repo the signed-in account owns: public, private, forks and archived ones alike."""
    found: list[dict] = []
    for page in range(1, 51):
        status, repos = http("GET", f"/user/repos?per_page=100&affiliation=owner&page={page}")
        if status >= 300 or not isinstance(repos, list):
            message = repos.get("message", "") if isinstance(repos, dict) else ""
            raise CopyError(f"GitHub would not list the repos ({status}{': ' + message if message else ''}).")
        for repo in repos:
            found.append({
                "owner": str(repo["owner"]["login"]),
                "name": str(repo["name"]),
                "private": bool(repo.get("private")),
                "pushed_at": str(repo.get("pushed_at") or ""),
                "url": str(repo["clone_url"]),
            })
        if len(repos) < 100:
            break
    return found


def _run_git(args: list[str], token: str) -> tuple[int, str]:
    """One git command, signed in for github.com through its environment only, so the token is on no
    command line and in no config file. Every way of asking a person for a password is turned off."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, errors="replace", timeout=GIT_SECONDS, env=env, creationflags=flags)
    except OSError:
        return 127, "git is not installed on this computer"
    except subprocess.TimeoutExpired:
        return 124, f"git took longer than {GIT_SECONDS // 60} minutes"
    return result.returncode, (result.stderr or result.stdout).strip()


def _last_line(text: str, token: str) -> str:
    """What git said last, short enough for one line and never with the sign-in in it."""
    shown = text.replace(token, "***") if token else text
    lines = [line.strip() for line in shown.splitlines() if line.strip()]
    return (lines[-1] if lines else "git failed")[:200]


def copy_repo(folder: Path, repo: dict, known: dict, token: str, run=None) -> tuple[str, str]:
    """Bring one repo's copy up to date. ("new" | "updated" | "same" | "failed", why it failed)."""
    run = _run_git if run is None else run
    target = folder / repo["owner"] / (repo["name"] + ".git")
    if target.is_dir():
        if known.get("pushed_at") == repo["pushed_at"] and not known.get("error"):
            return "same", ""
        code, output = run(["--git-dir", str(target), "fetch", "--prune", "--quiet", "origin"], token)
        return ("updated", "") if code == 0 else ("failed", _last_line(output, token))
    part = target.with_name(target.name + ".part")  # a clone that stops half way is never taken for a copy
    shutil.rmtree(part, ignore_errors=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    code, output = run(["clone", "--mirror", "--quiet", repo["url"], str(part)], token)
    if code != 0:
        shutil.rmtree(part, ignore_errors=True)
        return "failed", _last_line(output, token)
    os.replace(part, target)
    return "new", ""


def copy(folder: Path, accounts: list[str], *, http_for=None, token_for=None, run=None, now: dt.datetime | None = None, say=None) -> dict:
    """Bring the copy of every repo of `accounts` up to date in `folder`. Returns the state, which is
    also kept in `folder/copy.json`. An account GitHub will not list is reported and leaves its copies
    as they are; `say(line)` hears about each repo that was copied or failed."""
    now = dt.datetime.now() if now is None else now
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    say = say or (lambda line: None)
    folder.mkdir(parents=True, exist_ok=True)
    state = load_state(folder)
    state["problems"] = []
    for account in accounts:
        try:
            token = token_for(account) if token_for else github_usage.account_token(account)
            listed = fetch_repos(http_for(account) if http_for else publish.make_http(token))
        except (CopyError, publish.PublishError) as error:
            state["problems"].append(f"{account}: {error}")
            say(f"{account}: {error}")
            continue
        seen = set()
        for repo in listed:
            key = f"{repo['owner']}/{repo['name']}"
            seen.add(key)
            known = state["repos"].get(key, {})
            result, why = copy_repo(folder, repo, known, token, run)
            entry = {"private": repo["private"], "pushed_at": repo["pushed_at"], "copied_at": known.get("copied_at", "")}
            if result == "failed":
                entry.update(error=why, pushed_at=known.get("pushed_at", ""))
            elif result != "same":
                entry["copied_at"] = stamp
            state["repos"][key] = entry
            if result != "same":
                say(f"{key}: {result}{' (' + why + ')' if why else ''}")
                _save_state(folder, state)  # a long first run that is stopped keeps what it has
        for key, entry in state["repos"].items():
            if key.lower().startswith(account.lower() + "/"):
                entry["gone"] = key not in seen  # no longer on GitHub (or moved away): the copy stays
    state["at"] = stamp
    _save_state(folder, state)
    return state


# ------------------------------------------------------------ saying it, and the note in the brain


def counts(state: dict) -> dict:
    repos = state.get("repos", {}).values()
    return {
        "repos": sum(1 for entry in repos if entry.get("copied_at")),
        "private": sum(1 for entry in repos if entry.get("copied_at") and entry.get("private")),
        "failed": sum(1 for entry in repos if entry.get("error")),
        "gone": sum(1 for entry in repos if entry.get("gone")),
    }


def summary(folder: Path, state: dict) -> str:
    if not state.get("at"):
        return f"No copy in {folder} yet. Make one with: cairn vibe github copy \"{folder}\""
    count = counts(state)
    line = f"{count['repos']} repos ({count['private']} private) have a full copy in {folder}, checked {state['at'].replace('T', ' ')}."
    if count["failed"]:
        line += f" {count['failed']} could not be copied; the next run tries again."
    if count["gone"]:
        line += f" {count['gone']} are no longer on GitHub; their copies are kept."
    for problem in state.get("problems", []):
        line += f" {problem}"
    return line


def note_path(device: str) -> str:
    return f"{FOLDER}/{core.slugify(device)}.md"


def copy_note(folder: Path, state: dict, device: str) -> tuple[str, str]:
    """(path, text) of the note that tells every device this computer holds a copy, and how fresh."""
    count = counts(state)
    meta = {
        "title": f"GitHub copy on {device}",
        "type": "note",
        "tags": ["github"],
        "gh_copy_device": device,
        "gh_copy_at": state.get("at", ""),
        "gh_copy_repos": count["repos"],
        "gh_copy_failed": count["failed"],
    }
    lines = [
        summary(folder, state),
        "",
        "Each copy is a git mirror: every branch, every tag, the whole history. Not in it: issues, releases,",
        "wikis, Actions settings and secrets, and files kept in Git LFS.",
        "",
        f"- Work from a copy: `git clone \"{folder}/OWNER/NAME.git\"`",
        f"- Put one on another host: `git --git-dir \"{folder}/OWNER/NAME.git\" push --all NEW-URL`, then the same with `--tags`",
        "- Bring the copy up to date now: `cairn vibe github copy` (the watcher does it once a day)",
    ]
    failed = sorted(key for key, entry in state.get("repos", {}).items() if entry.get("error"))
    if failed:
        lines += ["", "Could not be copied:", *[f"- `{key}`: {state['repos'][key]['error']}" for key in failed]]
    gone = sorted(key for key, entry in state.get("repos", {}).items() if entry.get("gone"))
    if gone:
        lines += ["", "No longer on GitHub, copy kept:", *[f"- `{key}`" for key in gone]]
    return note_path(device), core.serialize_note(meta, "\n".join(lines) + "\n")


def write_note(brain: Brain, folder: Path, state: dict, device: str | None = None) -> str:
    path, text = copy_note(folder, state, device or machine.device_name())
    if brain.resolve(path).exists():
        brain.write(path, text, brain.read(path)["mtime"])
    else:
        brain.write(path, text)
    return path


# ------------------------------------------------------------ the two ways in


def accounts_to_copy(asked: str = "") -> list[str]:
    """The one account asked for, or every account the GitHub CLI is signed in to on this computer."""
    if asked:
        return [github_usage.account_name(asked)]
    names = github_push.signed_in(fresh=True)
    if not names:
        raise CopyError("The GitHub CLI is not signed in on this computer. Sign in once with: gh auth login")
    return names


def is_due(state: dict, now: dt.datetime) -> bool:
    """The watcher only keeps up a copy that a person made first, and only once it is a day old."""
    try:
        return now - dt.datetime.fromisoformat(str(state.get("at") or "")) >= dt.timedelta(hours=REFRESH_HOURS)
    except ValueError:
        return False


def sync_soon(brain: Brain) -> bool:
    """For the watcher, which must never wait on a fetch: when this computer has a copy folder and the
    copy is a day old, bring it up to date in the background, one run at a time. True when one started."""
    with _watch_lock:
        if _watch["thread"] is not None or time.monotonic() < _watch["after"]:
            return False
        chosen = settings()
        folder = Path(chosen["folder"]) if chosen else None
        if folder is None or not is_due(load_state(folder), dt.datetime.now()):
            _watch["after"] = time.monotonic() + 60
            return False

        def refresh() -> None:
            try:
                names = [str(name) for name in chosen.get("accounts") or []] or accounts_to_copy()
                write_note(brain, folder, copy(folder, names))
            except Exception:  # a nicety: it must never take the watcher down
                pass
            finally:
                _watch.update(thread=None, after=time.monotonic() + RETRY_SECONDS)

        _watch["thread"] = threading.Thread(target=refresh, daemon=True)
        _watch["thread"].start()
        return True

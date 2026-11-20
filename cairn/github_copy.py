"""A full copy of every repo of your GitHub accounts on this computer (`cairn vibe github copy FOLDER`).

GitHub is where the repos live, where other people find them and what your devices sync through.
Cairn runs on your own computers, which sleep and sit behind office networks, so it does not try to
stand in for that. What it can do is make sure GitHub is not the only place your work exists: one
locked account, one deleted repo or one force-push would otherwise be the end of it.

The copy is plain git. Each repo is a mirror (`git clone --mirror`: every branch, every tag, the
whole history) in `FOLDER/owner/name.git`, and a later run only fetches the repos pushed to since.
To work from a copy: `git clone FOLDER/owner/name.git`. To put one on another host:
`git --git-dir FOLDER/owner/name.git push --all NEW-URL` and the same with `--tags`.

What is not in git is kept beside each mirror, in `FOLDER/owner/name.extras/`: issues and pull
requests with what was said under them, releases and their files, the wiki, the Actions variables
and the names of the Actions secrets (see github_extras). Only the value of a secret is left out,
because GitHub never gives one back.

The folder is remembered per computer in ~/.cairn/github-copy.json; with it set, the watcher brings
the copy up to date once a day. A repo that is gone from GitHub keeps its copy. The sign-in is the
GitHub CLI's, handed to git for one command at a time: never written into the copy, never printed.

One disk is one accident away from none, so `cairn vibe github copy everywhere` says it in the brain: every other
computer whose watcher reads that brain then starts its own copy, in `github-copy` in its home
folder, when it has the room. Each computer writes a note of what it holds, so any of them can say
how many copies there are.
"""

from __future__ import annotations

import base64
import concurrent.futures
import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import core, github_extras, github_push, github_usage, machine, publish
from .brain import Brain, ConflictError

FOLDER = f"{github_usage.FOLDER}/copy"  # where each computer's note is kept in the brain
EVERYWHERE = f"{github_usage.FOLDER}/copy-everywhere.md"  # the note that asks every computer for a copy
HOME_NAME = "github-copy"  # the folder, in its home folder, of a computer the brain asked
EXTRAS_AT_ONCE = 4  # repos whose issues, releases and so on are fetched side by side
ROOM_UNKNOWN = 30 * 1024**3  # free space a first copy wants when no computer has said how big one is
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
                "about": {name: repo[name] for name in github_extras.ABOUT if name in repo},
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


def copy(folder: Path, accounts: list[str], *, http_for=None, token_for=None, run=None, fetch=None, now: dt.datetime | None = None, say=None) -> dict:
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
            http = http_for(account) if http_for else publish.make_http(token)
            listed = fetch_repos(http)
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
            if known.get("extras"):
                entry["extras"] = known["extras"]
            if result == "failed":
                entry.update(error=why, pushed_at=known.get("pushed_at", ""))
            elif result != "same":
                entry["copied_at"] = stamp
            state["repos"][key] = entry
            if result != "same":
                say(f"{key}: {result}{' (' + why + ')' if why else ''}")
                _save_state(folder, state)  # a long first run that is stopped keeps what it has
        _copy_extras(folder, listed, state, http, token, run, fetch, now, say)
        for key, entry in state["repos"].items():
            if key.lower().startswith(account.lower() + "/"):
                entry["gone"] = key not in seen  # no longer on GitHub (or moved away): the copy stays
    state["at"] = stamp
    _save_state(folder, state)
    return state


def _copy_extras(folder: Path, listed: list[dict], state: dict, http, token: str, run, fetch, now: dt.datetime, say) -> None:
    """What is not in git (issues, releases, wiki, Actions names), for the repos where it changed."""
    run = _run_git if run is None else run
    stamps = github_extras.fetch_stamps(http)
    due = []
    for repo in listed:
        key = f"{repo['owner']}/{repo['name']}"
        entry = state["repos"][key]
        facts = stamps.get(key)
        if entry.get("copied_at") and github_extras.is_due(entry.get("extras", {}), github_extras.stamp_of(repo, facts), now):
            due.append((key, repo, facts))

    def one(item: tuple) -> tuple[str, dict]:
        key, repo, facts = item
        known = state["repos"][key].get("extras", {})
        try:
            return key, github_extras.copy_extras(folder, repo, facts, known, http, token, run, now, fetch)
        except OSError as error:  # a full disk, a file held open: this repo waits for the next run
            return key, {**known, "error": f"{type(error).__name__}: {error}"[:200]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=EXTRAS_AT_ONCE) as pool:
        for key, held in pool.map(one, due):
            state["repos"][key]["extras"] = held
            line = github_extras.said(held)
            if line:
                say(f"{key}: {line}")
            _save_state(folder, state)


# ------------------------------------------------------------ saying it, and the note in the brain


def counts(state: dict) -> dict:
    repos = state.get("repos", {}).values()
    return {
        "repos": sum(1 for entry in repos if entry.get("copied_at")),
        "private": sum(1 for entry in repos if entry.get("copied_at") and entry.get("private")),
        "failed": sum(1 for entry in repos if entry.get("error")),
        "gone": sum(1 for entry in repos if entry.get("gone")),
    }


def extras_counts(state: dict) -> dict:
    """What the copy holds beside the mirrors, added up over every repo."""
    held = [entry.get("extras", {}) for entry in state.get("repos", {}).values()]
    total = {name: sum(int(extras.get(name) or 0) for extras in held) for name in ("talk", "releases", "files", "bytes", "secrets")}
    total.update(wiki=sum(1 for extras in held if extras.get("wiki")), lfs=sum(1 for extras in held if extras.get("lfs")))
    total["failed"] = sum(1 for extras in held if extras.get("error"))
    return total


def folder_bytes(folder: Path) -> int:
    """How much disk the whole copy takes, so another computer knows the room a copy needs."""
    total = 0
    for parent, _, names in os.walk(folder):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(parent, name))
            except OSError:
                pass
    return total


def summary(folder: Path, state: dict, others: list[dict] | None = None, asked: bool = False) -> str:
    """One paragraph about this computer's copy. With `others` (the copies other computers say they
    hold, see other_copies), it also says whether this is the only one; `asked` when the brain
    already asks every computer for one."""
    if not state.get("at"):
        line = f"No copy in {folder} yet. Make one with: cairn vibe github copy \"{folder}\""
        return " ".join([line, *state.get("problems", [])])
    count = counts(state)
    line = f"{count['repos']} repos ({count['private']} private) have a full copy in {folder}, checked {state['at'].replace('T', ' ')}."
    extras = extras_counts(state)
    beside = github_extras.held_text({**extras, "wiki": 0, "lfs": 0})
    for name, words in (("wiki", "wikis"), ("lfs", "repos with Git LFS files")):
        if extras[name]:
            beside += f"{', ' if beside else ''}{extras[name]} {words}"
    if beside:
        line += f" Beside them: {beside}."
    if count["failed"]:
        line += f" {count['failed']} could not be copied; the next run tries again."
    if extras["failed"]:
        line += f" {extras['failed']} have parts that would not come (see copy.json); the next run tries again."
    if count["gone"]:
        line += f" {count['gone']} are no longer on GitHub; their copies are kept."
    for problem in state.get("problems", []):
        line += f" {problem}"
    if others:
        line += " Also on " + ", ".join(f"{other['device']} (checked {other['at'].replace('T', ' ')})" for other in others) + "."
    elif others is not None:
        line += " No other computer holds a copy: besides GitHub, this disk is the only place."
        line += (" Your other computers are asked for one; each starts when its Cairn next runs." if asked
                 else " `cairn vibe github copy everywhere` asks your other computers for one.")
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
        "gh_copy_bytes": folder_bytes(folder) if state.get("at") else 0,
    }
    lines = [
        summary(folder, state),
        "",
        "Each repo is a git mirror in `OWNER/NAME.git`: every branch, every tag, the whole history, and its",
        "Git LFS files. Beside it, `OWNER/NAME.extras/` holds what is not in git: issues and pull requests with",
        "their comments, releases and their files, the wiki, Actions variables and the names of the Actions",
        "secrets. Not copied: the value of a secret, which GitHub never gives back.",
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


def _put(brain: Brain, path: str, text: str) -> None:
    """Write a note over what is there; leave it alone when it already says this."""
    if brain.resolve(path).exists():
        current = brain.read(path)
        if current["text"] != text:
            brain.write(path, text, current["mtime"])
    else:
        brain.write(path, text)


def write_note(brain: Brain, folder: Path, state: dict, device: str | None = None) -> str:
    path, text = copy_note(folder, state, device or machine.device_name())
    _put(brain, path, text)
    return path


def other_copies(brain: Brain, device: str | None = None) -> list[dict]:
    """The copies other computers say they hold, from their notes: [{"device", "at", "repos", "bytes"}]."""
    mine = note_path(device or machine.device_name())
    found = []
    for file in sorted(brain.resolve(mine).parent.glob("*.md")):
        if f"{FOLDER}/{file.name}" == mine:
            continue
        try:
            meta = core.parse_note(file.read_text(encoding="utf-8"))["meta"]
            if meta.get("gh_copy_at"):
                found.append({"device": str(meta.get("gh_copy_device") or file.stem), "at": str(meta["gh_copy_at"]),
                              "repos": int(meta.get("gh_copy_repos") or 0), "bytes": int(meta.get("gh_copy_bytes") or 0)})
        except (OSError, ValueError):
            continue
    return found


def everywhere(brain: Brain) -> bool:
    """Whether the brain asks every computer that reads it to keep a copy."""
    try:
        if not brain.resolve(EVERYWHERE).exists():
            return False
        return str(core.parse_note(brain.read(EVERYWHERE)["text"])["meta"].get("gh_copy_everywhere", "")).lower() == "yes"
    except (OSError, ValueError):
        return False


def set_everywhere(brain: Brain, on: bool, device: str | None = None, now: dt.datetime | None = None) -> str:
    meta = {
        "title": "GitHub copy on every computer",
        "type": "note",
        "tags": ["github"],
        "gh_copy_everywhere": "yes" if on else "no",
        "gh_copy_asked_by": device or machine.device_name(),
        "gh_copy_asked_at": (now or dt.datetime.now()).strftime("%Y-%m-%dT%H:%M"),
    }
    body = (
        "Every computer whose Cairn watcher reads this brain keeps its own full copy of your GitHub repos, in\n"
        f"`{HOME_NAME}` in its home folder, and writes what it holds to `{FOLDER}/`. A computer starts once it has\n"
        "the room and the GitHub CLI is signed in there.\n"
        if on else
        "Asked off. Computers that already keep a copy go on keeping it; no other computer starts one.\n"
    )
    _put(brain, EVERYWHERE, core.serialize_note(meta, body))
    return EVERYWHERE


def home_folder() -> Path:
    return Path.home() / HOME_NAME


def room_needed(others: list[dict]) -> int:
    """Free space a first copy wants here: half again what the biggest copy elsewhere takes, and a margin."""
    biggest = max((other["bytes"] for other in others), default=0)
    return biggest * 3 // 2 + 5 * 1024**3 if biggest else ROOM_UNKNOWN


def _asked_by_the_brain(brain: Brain) -> dict:
    """On a computer nobody ran the command on, when the brain asks every computer for a copy: its
    folder, remembered from now on. {} when not asked, or when there is no room, which its note says."""
    if not everywhere(brain):
        return {}
    folder = home_folder()
    need = room_needed(other_copies(brain))
    free = shutil.disk_usage(folder.parent).free
    if free < need:
        why = f"Not started on this computer: a copy wants about {need // 1024**3} GB free in {folder.parent}, and {free // 1024**3} GB is."
        write_note(brain, folder, {"repos": {}, "problems": [why]})
        return {}
    remember(folder, [])
    return settings()


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
    """A copy is brought up to date once it is a day old. One never made is not due: see sync_soon."""
    try:
        return now - dt.datetime.fromisoformat(str(state.get("at") or "")) >= dt.timedelta(hours=REFRESH_HOURS)
    except ValueError:
        return False


def sync_soon(brain: Brain) -> bool:
    """For the watcher, which must never wait on a fetch: when this computer has a copy folder and the
    copy is a day old, bring it up to date in the background, one run at a time. A first copy is only
    started here when the brain asks every computer for one. True when a run started."""
    with _watch_lock:
        if _watch["thread"] is not None or time.monotonic() < _watch["after"]:
            return False
        try:
            chosen = settings() or _asked_by_the_brain(brain)
            folder = Path(chosen["folder"]) if chosen else None
            state = load_state(folder) if folder else {}
            due = folder is not None and (is_due(state, dt.datetime.now()) or (not state.get("at") and everywhere(brain)))
        except (ConflictError, OSError):
            due = False
        if not due:
            _watch["after"] = time.monotonic() + 60
            return False

        def refresh() -> None:
            try:
                try:
                    names = [str(name) for name in chosen.get("accounts") or []] or accounts_to_copy()
                    held = copy(folder, names)
                except (CopyError, publish.PublishError) as error:  # not signed in here: the note says so
                    held = {**load_state(folder), "problems": [str(error)]}
                write_note(brain, folder, held)
            except Exception:  # a nicety: it must never take the watcher down
                pass
            finally:
                _watch.update(thread=None, after=time.monotonic() + RETRY_SECONDS)

        _watch["thread"] = threading.Thread(target=refresh, daemon=True)
        _watch["thread"].start()
        return True

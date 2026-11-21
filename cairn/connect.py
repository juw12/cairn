"""Connect a brain to the AI apps on this computer, so a new chat there starts with the user's memory.

An AI app that reads skills (a folder with a SKILL.md) looks in one folder of the user's home. `cairn connect`
puts the `cairn-memory` skill there: how to search the brain before answering, how to save what must be
remembered, and two small scripts that do both through Cairn's own tools. No account, no server, no key.

Vector is connected by itself whenever Cairn opens (`auto_connect`), because it is the app people chat with.
The coding agents are connected when asked by name: they also run unattended jobs, where a skill that says
"search the brain when a chat starts" would cost every run.

Rules that keep this safe:
* An app that is not installed is left alone: its home folder is never created.
* A `cairn-memory` folder Cairn did not make is never touched (the marker file says who made it).
* Opening a second brain does not take the skill away from the first; `cairn connect` by hand does.

The cloud: a brain that is pushed to GitHub is also readable without its folder. The skill is told the repo, and
when the folder is missing on this computer (never synced, deleted, a new computer) its scripts read the notes
from GitHub and save new ones there. `cairn connect --cloud owner/repo` connects a computer that never had the
folder at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .brain import Brain

SKILL_NAME = "cairn-memory"
MARKER = ".cairn-connect.json"
SOURCE = Path(__file__).resolve().parent / "memory_skill"
COPIED = ("recall.py", "remember.py", "common.py", "cloud.py")
OFF_SWITCH = "CAIRN_NO_CONNECT"

# home: the folder that shows the app is installed. skills: where it reads personal skills.
APPS: dict[str, dict] = {
    "vector": {"title": "Vector", "home": (".vector",), "skills": (".vector", "skills"), "auto": True},
    "claude": {"title": "Claude Code", "home": (".claude",), "skills": (".claude", "skills"), "auto": False},
    "opencode": {"title": "OpenCode", "home": (".config", "opencode"), "skills": (".config", "opencode", "skills"), "auto": False},
    "codex": {"title": "Codex", "home": (".codex",), "skills": (".agents", "skills"), "auto": False},
}

SHARED_RULES = (
    "- Notes go in the brain's usual folders (`notes/`, `decisions/`), named the way Cairn names them. Change an\n"
    "  existing note only when the user asks for that note to change."
)
# A folder of its own keeps two computers from taking the same file name or decision number. It says where
# remember.py puts what it saves, not what this computer may work on: every computer has the same role.
FOLDER_RULES = (
    "- What `remember.py` saves from this computer goes in `{folder}/` (`notes/` and `decisions/` there), so two\n"
    "  computers never take the same file name or decision number. That is where its own notes land, not a\n"
    "  limit on the work: every computer has the same role.\n"
    "- When the work belongs in a shared note (a project's note, the Update Log, a task, the journal), create\n"
    "  or edit that note from here, the way any other computer would. Do not leave it for another computer\n"
    "  because that one wrote it first. Before editing a shared note run `remember.py check`, keep the edit\n"
    "  small, and say in the note or the Update Log which computer made it.\n"
    "- Hand something to another computer only when it is physically there: its files, its apps, a check that\n"
    "  needs that machine."
)


class ConnectError(ValueError):
    """The request cannot be done as asked; the message says why."""


class RemoteBrain:
    """A brain that is only on GitHub: this computer has no folder of it. Given as owner/repo, or
    owner/repo/folder when the brain is a folder inside the repo."""

    def __init__(self, spec: str):
        text = (spec or "").strip().strip("/")
        text = re.sub(r"^(https?://)?github\.com/", "", text).removesuffix(".git")
        parts = text.split("/")
        if len(parts) < 2 or not all(re.fullmatch(r"[\w.-]+", part) for part in parts):
            raise ConnectError(f"--cloud takes a GitHub repo as owner/name (got {spec!r}).")
        self.repo = "/".join(parts[:2])
        self.folder = "/".join(parts[2:])
        self.dir = self.root = "github:" + "/".join(parts)


def _git(folder, *arguments: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(folder), *arguments], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def cloud_of(brain) -> dict:
    """The GitHub repo this brain is pushed to, when it has one: what the skill reads when the folder is gone."""
    if isinstance(brain, RemoteBrain):
        return {"repo": brain.repo, "folder": brain.folder, "branch": ""}
    from .memory_skill.cloud import repo_of

    name = repo_of(_git(brain.dir, "remote", "get-url", "origin"))
    if not name:
        return {}
    return {"repo": name, "folder": _git(brain.dir, "rev-parse", "--show-prefix").strip("/"), "branch": ""}


# ---------------------------------------------------------------- settings (one small file per computer)


def cairn_home() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")


def read_settings(folder: Path | None = None) -> dict:
    try:
        data = json.loads(((folder or cairn_home()) / "connect.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_settings(settings: dict, folder: Path | None = None) -> None:
    folder = folder or cairn_home()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "connect.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def set_auto(on: bool, folder: Path | None = None) -> None:
    settings = read_settings(folder)
    settings["auto"] = bool(on)
    write_settings(settings, folder)


def clean_folder(brain: Brain, folder: str) -> str:
    """A folder inside the brain, as a relative path with forward slashes ("" for none)."""
    raw = (folder or "").replace("\\", "/").strip()
    text = raw.rstrip("/")
    if not text:
        return ""
    parts = text.split("/")
    if raw.startswith("/") or ":" in parts[0] or any(part in ("", ".", "..") for part in parts):
        raise ConnectError(f"--write-folder must be a folder inside the brain, like devices/laptop (got {folder!r}).")
    return text


def write_folder_for(brain: Brain, folder: Path | None = None) -> str:
    """The one folder this computer writes in for this brain, when the user set one."""
    return str((read_settings(folder).get("write_folders") or {}).get(str(brain.dir), ""))


def set_write_folder(brain: Brain, value: str, folder: Path | None = None) -> str:
    cleaned = clean_folder(brain, value)
    settings = read_settings(folder)
    folders = dict(settings.get("write_folders") or {})
    if cleaned:
        folders[str(brain.dir)] = cleaned
    else:
        folders.pop(str(brain.dir), None)
    settings["write_folders"] = folders
    write_settings(settings, folder)
    return cleaned


# ---------------------------------------------------------------- the skill's files


def script_python() -> str:
    """The Python the scripts run on: the one running Cairn, but one that has a console on Windows."""
    python = Path(sys.executable or "python")
    if python.name.lower().startswith("pythonw"):
        console = python.with_name(python.name.lower().replace("pythonw", "python", 1))
        if console.exists():
            return str(console)
    return str(python)


def skill_files(brain: Brain, target: Path, write_folder: str = "") -> dict[str, str]:
    """Every file of the skill as it should be in `target`, filled in for this brain and computer."""
    python = script_python()
    rules = FOLDER_RULES.format(folder=write_folder) if write_folder else SHARED_RULES
    remote = isinstance(brain, RemoteBrain)
    cloud = cloud_of(brain)
    if remote:
        place = f"on GitHub, `{cloud['repo']}`" + (f" (folder `{cloud['folder']}`)" if cloud["folder"] else "")
        place += ". This computer has no folder of it: the scripts read and save on GitHub"
    else:
        place = f"at `{brain.dir}`"
    guide = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
    for token, value in (("{{BRAIN}}", place), ("{{PYTHON}}", python), ("{{SKILL}}", target.as_posix()),
                         ("{{WRITE_RULES}}", rules)):
        guide = guide.replace(token, value)
    config = {
        "root": "" if remote else str(brain.root),
        "brain": "" if remote else str(brain.dir),
        "python": python,
        "package_parent": str(Path(__file__).resolve().parent.parent),
        "write_folder": write_folder,
        "cloud": cloud,
    }
    files = {"SKILL.md": guide, "config.json": json.dumps(config, indent=2) + "\n"}
    for name in COPIED:
        files[name] = (SOURCE / name).read_text(encoding="utf-8")
    return files


def _digest(files: dict[str, str]) -> str:
    whole = hashlib.sha256()
    for name in sorted(files):
        whole.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    return whole.hexdigest()


def read_marker(target: Path) -> dict | None:
    """What Cairn wrote there last time, or None when the folder is not Cairn's."""
    try:
        marker = json.loads((target / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) and marker.get("made_by") == "cairn" else None


def _on_disk(target: Path, names) -> dict[str, str]:
    found = {}
    for name in names:
        try:
            found[name] = (target / name).read_bytes().decode("utf-8")
        except (OSError, ValueError):
            pass
    return found


def _write(target: Path, files: dict[str, str], brain: Brain) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        temporary = target / (name + ".cairn-tmp")
        temporary.write_bytes(text.encode("utf-8"))
        os.replace(temporary, target / name)
    marker = {"made_by": "cairn", "brain": str(brain.dir), "digest": _digest(files)}
    (target / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- connect, disconnect, status


def _pick(apps, auto: bool) -> list[str]:
    if apps:
        unknown = [name for name in apps if name not in APPS]
        if unknown:
            raise ConnectError(f"Unknown app: {', '.join(unknown)}. Known: {', '.join(APPS)}.")
        return list(dict.fromkeys(apps))
    return [name for name, app in APPS.items() if app["auto"]] if auto else list(APPS)


def skill_folder(name: str, home: Path | None = None) -> Path:
    return (home or Path.home()).joinpath(*APPS[name]["skills"], SKILL_NAME)


def connect(brain: Brain, apps=None, auto: bool = False, home: Path | None = None,
            settings_folder: Path | None = None) -> list[dict]:
    """Put the skill into each app that is installed. Returns one row per app: what happened and where.

    state is one of: connected, updated, unchanged, absent (app not installed), kept (a cairn-memory folder
    someone else made), other-brain (auto only: connected to another brain, left as it is).
    """
    home = home or Path.home()
    write_folder = write_folder_for(brain, settings_folder)
    rows = []
    for name in _pick(apps, auto):
        app = APPS[name]
        target = skill_folder(name, home)
        row = {"app": name, "title": app["title"], "path": str(target), "state": "absent"}
        rows.append(row)
        if not home.joinpath(*app["home"]).is_dir():
            continue
        marker = read_marker(target)
        if target.exists() and marker is None:
            row["state"] = "kept"
            continue
        if auto and marker and marker.get("brain") != str(brain.dir):
            row["state"] = "other-brain"
            row["brain"] = marker.get("brain", "")
            continue
        files = skill_files(brain, target, write_folder)
        if marker and marker.get("brain") == str(brain.dir) and _on_disk(target, files) == files:
            row["state"] = "unchanged"
            continue
        _write(target, files, brain)
        row["state"] = "updated" if marker else "connected"
    return rows


def disconnect(apps=None, home: Path | None = None) -> list[dict]:
    """Take the skill out again. Only a folder Cairn made is removed."""
    rows = []
    for name in _pick(apps, auto=False):
        target = skill_folder(name, home)
        state = "absent"
        if target.exists():
            state = "kept"
            if read_marker(target) is not None:
                shutil.rmtree(target)
                state = "removed"
        rows.append({"app": name, "title": APPS[name]["title"], "path": str(target), "state": state})
    return rows


def status(brain: Brain, home: Path | None = None, settings_folder: Path | None = None) -> list[dict]:
    """What is there now, without changing anything."""
    home = home or Path.home()
    rows = []
    for name, app in APPS.items():
        target = skill_folder(name, home)
        marker = read_marker(target)
        if not home.joinpath(*app["home"]).is_dir():
            state = "absent"
        elif target.exists() and marker is None:
            state = "kept"
        elif marker is None:
            state = "not connected"
        elif marker.get("brain") != str(brain.dir):
            state = "other-brain"
        else:
            files = skill_files(brain, target, write_folder_for(brain, settings_folder))
            state = "connected" if _on_disk(target, files) == files else "out of date"
        rows.append({"app": name, "title": app["title"], "path": str(target), "state": state,
                     "brain": (marker or {}).get("brain", "")})
    return rows


def describe(row: dict) -> str:
    """One line a person can read for a row of connect(), disconnect() or status()."""
    title, path = row["title"], row["path"]
    return {
        "connected": f"{title}: connected. New chats there use this brain as memory ({path})",
        "updated": f"{title}: updated ({path})",
        "unchanged": f"{title}: already connected ({path})",
        "absent": f"{title}: not on this computer",
        "kept": f"{title}: has its own cairn-memory skill that Cairn did not make; left as it is ({path})",
        "other-brain": f"{title}: connected to another brain ({row.get('brain', '')}); `cairn connect {row['app']}` moves it to this one",
        "removed": f"{title}: disconnected ({path} removed)",
        "not connected": f"{title}: installed, not connected. `cairn connect {row['app']}` connects it",
        "out of date": f"{title}: connected, older files; `cairn connect {row['app']}` or opening Cairn updates them",
    }[row["state"]]


def auto_connect(brain: Brain, home: Path | None = None, settings_folder: Path | None = None) -> list[dict]:
    """Called when Cairn opens. Quiet, and never the reason Cairn fails to start."""
    if os.environ.get(OFF_SWITCH) or read_settings(settings_folder).get("auto") is False:
        return []
    try:
        if isinstance(brain, RemoteBrain) or not brain.dir.is_dir():
            return []
        rows = connect(brain, auto=True, home=home, settings_folder=settings_folder)
    except Exception as error:  # a full disk or a locked file must not stop the app
        print(f"cairn connect: {error}", file=sys.stderr)
        return []
    for row in rows:
        if row["state"] in ("connected", "updated"):
            print(describe(row))
    return rows

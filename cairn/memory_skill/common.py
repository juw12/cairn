"""Shared by recall.py and remember.py: this skill's settings, where the brain is, the right Python."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
NEEDS = (3, 13)  # the oldest Python Cairn's own code runs on


def plain_output() -> None:
    """Notes hold any language; a console in an old code page must not stop a search."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def cloud_settings() -> dict:
    settings = CONFIG.get("cloud") or {}
    return settings if settings.get("repo") else {}


def has_local_brain() -> bool:
    """The brain's folder and Cairn's own code are both on this computer."""
    brain, package = CONFIG.get("brain") or "", CONFIG.get("package_parent") or ""
    return bool(brain) and Path(brain).is_dir() and (Path(package) / "cairn" / "mcp.py").is_file()


def where(asked_for_cloud: bool = False) -> str:
    """"local": the folder on this computer. "cloud": straight from GitHub, because there is no folder
    here (never synced, deleted, moved) or because the newest copy was asked for."""
    if has_local_brain() and not asked_for_cloud:
        return "local"
    if cloud_settings():
        return "cloud"
    if asked_for_cloud:
        raise SystemExit("This brain has no GitHub repo to read from. Push it to GitHub and open Cairn once.")
    raise SystemExit(f"The brain is not at {CONFIG.get('brain')} any more, and it has no GitHub repo to read from "
                     "instead. Open Cairn once so it connects again.")


def open_cloud(max_age: int | None = None):
    """The brain on GitHub, brought up to date. Prints one line that says where the answer comes from."""
    import cloud

    settings = cloud_settings()
    try:
        brain = cloud.Cloud(settings["repo"], settings.get("folder", ""), settings.get("branch", ""))
        brain.refresh() if max_age is None else brain.refresh(max_age)
    except cloud.CloudError as error:
        raise SystemExit(str(error))
    print(brain.note + "\n")
    return brain


def use_cairns_python() -> None:
    """Started with an older Python than Cairn's code needs: run the same command with the one Cairn uses."""
    if sys.version_info >= NEEDS:
        return
    cairns = CONFIG.get("python") or ""
    if not cairns or not Path(cairns).exists() or Path(cairns).resolve() == Path(sys.executable).resolve():
        raise SystemExit(f"Cairn needs Python {NEEDS[0]}.{NEEDS[1]} or newer; this is {sys.version.split()[0]}.")
    sys.exit(subprocess.call([cairns, *sys.argv]))


def open_brain():
    use_cairns_python()
    sys.path.insert(0, CONFIG["package_parent"])
    from cairn.brain import Brain

    return Brain(CONFIG["root"], CONFIG["brain"])


def checks_in() -> bool:
    """This app's chats are put on Cairn's Vibe page by the skill (not OpenCode's: the watcher reads those)."""
    return bool(CONFIG.get("checkin")) and not os.environ.get("CAIRN_NO_CHECKIN")


def checkin(brain, task: str = "") -> dict | None:
    """Every command of the skill says the chat is at work: the first one opens its session on the Vibe
    page, the later ones are its signs of life. Which chat it is, is told by the folder the command runs in.
    Never the reason a command fails; None where it does not apply or an older Cairn cannot do it."""
    if not checks_in():
        return None
    try:
        from cairn import chatlog

        return chatlog.checkin(brain, Path.cwd(), agent=CONFIG.get("app") or "", task=task)
    except Exception:  # noqa: BLE001 - recalling and remembering come first
        return None


def saved_step(brain, text: str) -> None:
    """What the chat just saved is a step of its session, logged without the chat doing anything."""
    if not checks_in():
        return
    try:
        from cairn import chatlog

        if chatlog.checkin(brain, Path.cwd(), agent=CONFIG.get("app") or "")["state"] != "resting":
            chatlog.step(brain, Path.cwd(), text, agent=CONFIG.get("app") or "")
    except Exception:  # noqa: BLE001 - the note is saved; the step is a nicety
        pass


def write_folder(brain) -> Path | None:
    """The one folder this computer writes in, when the user set one (`cairn connect --write-folder`)."""
    relative = (CONFIG.get("write_folder") or "").strip("/")
    return brain.dir / relative if relative else None


def call(brain, tool: str, arguments: dict, read_only: bool = True) -> str:
    """One of Cairn's own tools (the ones `cairn mcp` serves), run here."""
    from cairn import mcp

    reply = mcp.Server(brain, read_only=read_only).call_tool(tool, arguments)
    text = "\n".join(part.get("text", "") for part in reply.get("content", []))
    if reply.get("isError"):
        raise SystemExit(text)
    return text

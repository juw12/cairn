"""Shared by recall.py and remember.py: this skill's settings, where the brain is, the right Python."""

from __future__ import annotations

import json
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

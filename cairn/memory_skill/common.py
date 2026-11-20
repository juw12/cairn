"""Shared by recall.py and remember.py: this skill's settings, the right Python, the brain."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
NEEDS = (3, 13)  # the oldest Python Cairn runs on


def use_cairns_python() -> None:
    """Started with an older Python than Cairn needs: run the same command with the one Cairn uses."""
    if sys.version_info >= NEEDS:
        return
    cairns = CONFIG.get("python") or ""
    if not cairns or not Path(cairns).exists() or Path(cairns).resolve() == Path(sys.executable).resolve():
        raise SystemExit(f"Cairn needs Python {NEEDS[0]}.{NEEDS[1]} or newer; this is {sys.version.split()[0]}.")
    sys.exit(subprocess.call([cairns, *sys.argv]))


def plain_output() -> None:
    """Notes hold any language; a console in an old code page must not stop a search."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def open_brain():
    sys.path.insert(0, CONFIG["package_parent"])
    from cairn.brain import Brain

    brain = Brain(CONFIG["root"], CONFIG["brain"])
    if not brain.dir.is_dir():
        raise SystemExit(f"The brain is not at {brain.dir} any more. Open Cairn once so it connects again.")
    return brain


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

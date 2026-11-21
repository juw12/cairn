#!/usr/bin/env python3
"""Recall from the Cairn brain. Read-only: nothing here can change a note.

    recall.py rules                              standing decisions (read these first)
    recall.py search "words" [--type T] [--limit 10]
    recall.py read "path, slug or title"
    recall.py recent [--days 7]
    recall.py brief                              one page: open tasks, decisions, ideas, latest log
    recall.py handoffs [--all]                   what the user's other computers left for this one
    recall.py handoffs "a few words"             one of those conversations in full

Add --cloud to read the newest copy on GitHub instead of the folder on this computer. Without a folder on
this computer that happens by itself.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ in the AI app's skills folder
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def rules(brain) -> str:
    """The decisions to follow: this computer's own when it has a folder, else every accepted one."""
    folder = common.write_folder(brain)
    decisions = folder / "decisions" if folder else None
    if decisions is None or not decisions.is_dir():
        return common.call(brain, "list_notes", {"type": "decision", "status": "accepted", "limit": 50})
    lines = [f"Decisions made on this computer ({decisions}):"]
    for note in sorted(decisions.glob("*.md")):
        title = note.stem
        for line in note.read_text(encoding="utf-8", errors="replace").splitlines()[:10]:
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip("\"'")
                break
        lines.append(f"- {note.relative_to(brain.dir).as_posix()}\n    {title}")
    lines.append("The shared decisions of every device: recall.py search \"...\" --type decision")
    return "\n".join(lines)


NEEDS_THE_FOLDER = ("The messages between computers are worked out by Cairn from the brain's folder, which is not on "
                    "this computer. Their files can be read: recall.py search \"handoff\", recall.py read \"handoffs/...\".")


def from_this_computer(options) -> str:
    brain = common.open_brain()
    if options.command == "rules":
        return rules(brain)
    if options.command == "search":
        arguments = {"query": options.query, "limit": options.limit}
        if options.type:
            arguments["type"] = options.type
        return common.call(brain, "search", arguments)
    if options.command == "read":
        return common.call(brain, "read_note", {"path": options.note})
    if options.command == "recent":
        return common.call(brain, "recent", {"days": options.days})
    if options.command == "handoffs":
        return common.call(brain, "handoffs", {"thread": options.thread, "all": options.all})
    return common.call(brain, "context", {})


def from_github(options) -> str:
    import cloud

    brain = common.open_cloud()
    try:
        if options.command == "rules":
            return brain.rules(common.CONFIG.get("write_folder") or "")
        if options.command == "search":
            return brain.search(options.query, options.type, options.limit)
        if options.command == "read":
            return brain.read(options.note)
        if options.command == "recent":
            return brain.recent(options.days)
        if options.command == "handoffs":
            return NEEDS_THE_FOLDER
        return brain.brief()
    except cloud.CloudError as error:
        raise SystemExit(str(error))


def main(argv: list[str] | None = None) -> int:
    common.plain_output()
    parser = argparse.ArgumentParser(prog="recall.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cloud", action="store_true", help="read the newest copy on GitHub")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rules")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--type", default="")
    search.add_argument("--limit", type=int, default=10)
    read = commands.add_parser("read")
    read.add_argument("note")
    recent = commands.add_parser("recent")
    recent.add_argument("--days", type=int, default=7)
    commands.add_parser("brief")
    handoffs = commands.add_parser("handoffs")
    handoffs.add_argument("thread", nargs="?", default="")
    handoffs.add_argument("--all", action="store_true")
    for command in commands.choices.values():
        command.add_argument("--cloud", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    options = parser.parse_args(argv)

    print(from_github(options) if common.where(options.cloud) == "cloud" else from_this_computer(options))
    return 0


if __name__ == "__main__":
    sys.exit(main())

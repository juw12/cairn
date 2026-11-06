"""cairn: command-line entry point."""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import __version__, core
from .brain import Brain


def _today() -> str:
    return dt.date.today().isoformat()


def cmd_init(brain: Brain, args) -> int:
    created = brain.init()
    print(f"Brain ready at {brain.dir}")
    for item in created:
        print(f"  + {item}")
    if not created:
        print("  (already set up, nothing changed)")
    return 0


def cmd_add(brain: Brain, args) -> int:
    text = " ".join(args.text).strip()
    if not text:
        print("Nothing to add. Example: cairn add \"todo: ship v1 #release\"", file=sys.stderr)
        return 2
    brain.init()
    captured = core.parse_capture(text)
    note = brain.create(captured["type"], captured["title"] or _today(), captured["tags"], captured["body"], _today())
    print(f"Added {captured['type']}: brain/{note['path']}")
    return 0


def cmd_list(brain: Brain, args) -> int:
    notes = brain.notes()
    if args.type:
        notes = [n for n in notes if n["type"] == args.type]
    if args.status:
        notes = [n for n in notes if n["status"] == args.status]
    if not notes:
        print("No notes yet. Try: cairn add \"idea: something worth remembering\"")
        return 0
    width = max(len(n["type"]) for n in notes)
    for note in sorted(notes, key=lambda n: (core.TYPES.index(n["type"]), n["status"], n["title"].lower())):
        status = f"[{note['status']}] " if note["status"] else ""
        tags = " " + " ".join("#" + t for t in note["tags"]) if note["tags"] else ""
        print(f"{note['type']:<{width}}  {status}{note['title']}{tags}")
    return 0


def cmd_todos(brain: Brain, args) -> int:
    items = brain.scan_code()
    for item in items:
        print(f"{item['path']}:{item['line']}  {item['tag']}  {item['text']}")
    print(f"{len(items)} item(s)")
    return 0


def cmd_context(brain: Brain, args) -> int:
    pack = core.context_pack(brain.notes(), brain.project_name, _today())
    if args.stdout:
        sys.stdout.write(pack)
        return 0
    brain.dir.mkdir(parents=True, exist_ok=True)
    (brain.dir / "CONTEXT.md").write_text(pack, encoding="utf-8", newline="\n")
    print(f"Wrote {brain.dir / 'CONTEXT.md'} ({len(pack.splitlines())} lines)")
    return 0


def cmd_serve(brain: Brain, args) -> int:
    from .server import serve

    if not brain.dir.exists():
        brain.init()
    serve(brain, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_desktop(brain: Brain, args) -> int:
    from . import desktop

    return desktop.main(["--hidden"] if args.hidden else [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cairn",
        description="A second brain that lives inside your repo.",
    )
    parser.add_argument("--version", action="version", version=f"cairn {__version__}")
    parser.add_argument("--root", default=".", help="project folder (default: current folder)")
    parser.add_argument("--brain", default=None, help="brain folder, relative to --root (default: brain/)")
    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", help="open the app (default)")
    serve_parser.add_argument("--port", type=int, default=4777)
    serve_parser.add_argument("--no-browser", action="store_true")
    desktop_parser = sub.add_parser("desktop", help="open Cairn as a desktop app (own window, lives in the tray)")
    desktop_parser.add_argument("--hidden", action="store_true", help="start in the tray")
    sub.add_parser("init", help="create the brain/ folder layout")
    add_parser = sub.add_parser("add", help='capture: cairn add "todo: ship v1 #release"')
    add_parser.add_argument("text", nargs="+")
    list_parser = sub.add_parser("list", help="list notes")
    list_parser.add_argument("--type", choices=core.TYPES)
    list_parser.add_argument("--status")
    sub.add_parser("todos", help="list TODO/FIXME comments in the code")
    context_parser = sub.add_parser("context", help="write brain/CONTEXT.md for teammates and AI assistants")
    context_parser.add_argument("--stdout", action="store_true", help="print instead of writing the file")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    brain = Brain(args.root, args.brain)
    commands = {
        "init": cmd_init,
        "add": cmd_add,
        "list": cmd_list,
        "todos": cmd_todos,
        "context": cmd_context,
        "serve": cmd_serve,
        "desktop": cmd_desktop,
        None: cmd_serve,
    }
    if args.command is None:
        args.port = 4777
        args.no_browser = False
    return commands[args.command](brain, args)

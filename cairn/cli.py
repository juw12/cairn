"""cairn: command-line entry point."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

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


def _print_report(report: dict) -> None:
    labels = [
        ("pulled", "from Obsidian"),
        ("pushed", "to Obsidian"),
        ("deletedInBrain", "moved to brain/.cairn/trash (deleted in Obsidian)"),
        ("deletedInVault", "moved to the vault's .trash (deleted in Cairn)"),
    ]
    for key, label in labels:
        for path in report.get(key, []):
            print(f"  {label}: {path}")
    for conflict in report.get("conflicts", []):
        print(f"  conflict: {conflict['path']} (kept the {conflict['kept']} version; other one in {conflict['copy']})")


def cmd_obsidian(brain: Brain, args) -> int:
    from .obsidian import ObsidianSync, SyncError

    sync = ObsidianSync(brain)
    try:
        if args.unlink:
            sync.unlink()
            print("Unlinked. Notes already synced stay where they are.")
            return 0
        if args.vault:
            brain.init()
            status = sync.link(args.vault, args.folder)
            print(f"Linked {status['vault']} <-> {brain.dir / status['folder']}")
        status = sync.status()
        if not status["linked"]:
            print("No vault linked. Example: cairn obsidian ~/Documents/MyVault", file=sys.stderr)
            return 2
        while True:
            report = sync.sync(_today(), dry_run=args.dry_run)
            if args.dry_run:
                for step in report["plan"]:
                    print(f"  would {step['op']}: {step['path']}")
                print(f"{len(report['plan'])} change(s) planned, nothing written")
                return 0
            if report["changes"] or not args.watch:
                stamp = dt.datetime.now().strftime("%H:%M:%S")
                print(f"[{stamp}] synced: {report['changes']} change(s)")
                _print_report(report)
            if not args.watch:
                return 0
            time.sleep(max(args.watch, 2))
    except SyncError as error:
        print(f"Sync stopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def cmd_publish(brain: Brain, args) -> int:
    from pathlib import Path

    from . import publish

    source = Path(args.source).expanduser() if args.source else brain.dir
    if not source.is_dir():
        print(f"No folder at {source}", file=sys.stderr)
        return 2
    try:
        if args.folder:
            report = publish.publish_to_folder(source, Path(args.folder).expanduser())
        else:
            if not args.to:
                print("Where to? Example: cairn publish --to YOUR-NAME/brain", file=sys.stderr)
                return 2
            http = publish.make_http(publish.find_token())
            report = publish.publish_to_github(source, args.to, http, _today(), dry_run=args.dry_run)
    except publish.PublishError as error:
        print(f"Publish stopped: {error}", file=sys.stderr)
        return 1
    verb = "would" if args.dry_run else "did"
    for path in report["write"]:
        print(f"  {verb} write: {path}")
    for path in report["delete"]:
        print(f"  {verb} remove: {path}")
    if report.get("created"):
        print(f"  {verb} create the public repo {report['repo']}")
    if not report["write"] and not report["delete"]:
        print("Nothing to publish: the public brain is up to date.")
    elif report.get("commit"):
        print(f"Published to https://github.com/{report['repo']} ({report['commit'][:7]})")
    return 0


def cmd_vibe(brain: Brain, args) -> int:
    from . import vibe

    brain.init()
    try:
        if args.action == "start":
            others = vibe.live_sessions(brain)
            note = vibe.start(brain, " ".join(args.text), task=args.task or "", agent=args.agent or "")
            slug = note["path"].rsplit("/", 1)[-1][:-3]
            print(f"Live: brain/{note['path']}  (open Cairn > Vibe to watch it)")
            if others:
                print(f"{len(others)} other session(s) are live too. In this terminal, point every command at this one:")
                print(f"  {'$env:CAIRN_SESSION = ' + repr(slug) if os.name == 'nt' else 'export CAIRN_SESSION=' + slug}"
                      f"   (or add --session {slug})")
        elif args.action in ("log", "skill"):
            text = " ".join(args.text)
            kind = "skill" if args.action == "skill" else args.kind
            vibe.log(brain, text, kind=kind, session=args.session or "")
            print("Logged.")
        elif args.action == "check":
            code, _ = vibe.check(brain, args.text, session_slug=args.session or "")
            print(f"{'PASS' if code == 0 else 'FAIL'} logged to the live session.")
            return code
        elif args.action == "end":
            note = vibe.end(brain, args.result, " ".join(args.text), session_slug=args.session or "")
            print(f"Ended as {args.result}: brain/{note['path']}")
        else:
            print(vibe.stats_text(brain))
    except vibe.VibeError as error:
        print(error, file=sys.stderr)
        return 2
    return 0


def cmd_feedback(brain: Brain, args) -> int:
    from . import vibe

    brain.init()
    try:
        note = vibe.add_feedback(brain, " ".join(args.text), rating=args.rating, source=args.source or "", about=args.about or "")
    except vibe.VibeError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Feedback saved: brain/{note['path']}")
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
    obsidian_parser = sub.add_parser(
        "obsidian",
        help="import an Obsidian vault and keep it in sync both ways",
        description="Mirror an Obsidian vault into brain/<folder>/ and keep both in sync. The first run imports "
        "everything; later runs copy whatever changed on either side. Deletes go to a trash folder, never away.",
    )
    obsidian_parser.add_argument("vault", nargs="?", help="vault folder (only needed the first time)")
    obsidian_parser.add_argument("--folder", default="obsidian", help="folder inside the brain (default: obsidian; '' = whole brain)")
    obsidian_parser.add_argument("--watch", type=float, default=0, metavar="SECONDS", help="keep syncing every N seconds")
    obsidian_parser.add_argument("--dry-run", action="store_true", help="show what would change")
    obsidian_parser.add_argument("--unlink", action="store_true", help="stop syncing (keeps the notes)")
    publish_parser = sub.add_parser(
        "publish",
        help="copy notes marked publish: true to your public brain repo",
        description="Copy every note whose frontmatter says publish: true to brain/ in a public GitHub repo, in one "
        "commit, and tag it cairn-brain. Nothing else leaves your brain. Links to private notes become plain text.",
    )
    publish_parser.add_argument("--to", metavar="OWNER/REPO", help="public repo (created if missing), e.g. you/brain")
    publish_parser.add_argument("--source", help="folder to publish from (default: the brain; can be an Obsidian vault)")
    publish_parser.add_argument("--folder", help="write into this local clone instead of using the GitHub API")
    publish_parser.add_argument("--dry-run", action="store_true", help="show what would change")
    vibe_parser = sub.add_parser(
        "vibe",
        help="track a coding session live: start, log, check, end, status",
        description="Live progress for AI-assisted coding, kept in brain/sessions/. Tell your assistant to run "
        "`cairn vibe log` after each step and `cairn vibe check -- <tests>` to record results; the app shows it as it happens.",
    )
    vibe_parser.add_argument("action", choices=["start", "log", "skill", "check", "end", "status"])
    vibe_parser.add_argument("text", nargs="*", help="goal / log text / end summary; for check, the command after --")
    vibe_parser.add_argument("--kind", default="note", choices=core.LOG_KINDS, help="log: note, prompt, pass, fail, commit, skill (a text starting with /refine, /council... is a skill)")
    vibe_parser.add_argument("--task", help="start: link a task, e.g. ship-v1")
    vibe_parser.add_argument("--agent", help="start: who is coding, e.g. claude, copilot, cursor")
    vibe_parser.add_argument("--session", help="which live session to write to (default: $CAIRN_SESSION, else the newest)")
    vibe_parser.add_argument("--result", default="shipped", choices=["shipped", "partial", "dropped"], help="end: the outcome")
    feedback_parser = sub.add_parser("feedback", help='record feedback: cairn feedback "toggle is hard to find" --rating 3')
    feedback_parser.add_argument("text", nargs="+")
    feedback_parser.add_argument("--rating", type=int, choices=range(1, 6), metavar="1-5")
    feedback_parser.add_argument("--from", dest="source", help="who said it")
    feedback_parser.add_argument("--about", help="session or note it is about (default: the latest session)")
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
        "obsidian": cmd_obsidian,
        "publish": cmd_publish,
        "vibe": cmd_vibe,
        "feedback": cmd_feedback,
        "serve": cmd_serve,
        "desktop": cmd_desktop,
        None: cmd_serve,
    }
    if args.command is None:
        args.port = 4777
        args.no_browser = False
    return commands[args.command](brain, args)

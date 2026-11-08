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
    if captured["type"] == "journal":
        match = core._CAPTURE_RE.match(text)
        page = brain.log(match.group(2).strip() if match else text, _today(), _now_time())
        print(f"Logged to brain/{page['path']}")
        return 0
    note = brain.create(captured["type"], captured["title"] or _today(), captured["tags"], captured["body"], _today())
    print(f"Added {captured['type']}: brain/{note['path']}")
    return 0


def _now_time() -> str:
    return dt.datetime.now().strftime("%H:%M")


def _resolve_day(value: str | None) -> str:
    if not value or value == "today":
        return _today()
    if value == "yesterday":
        return (dt.date.today() - dt.timedelta(days=1)).isoformat()
    dt.date.fromisoformat(value)  # raises ValueError on a bad date
    return value


_LOG_KINDS = {"task": "Task", "idea": "Idea", "decision": "Decision", "note": "Note"}


def cmd_log(brain: Brain, args) -> int:
    try:
        day = _resolve_day(args.day)
    except ValueError:
        print(f"Not a date: {args.day} (use YYYY-MM-DD, today or yesterday)", file=sys.stderr)
        return 2
    text = " ".join(args.text).strip()
    if not text:
        return _show_log(brain, day, max(1, args.days))
    brain.init()
    match = core._CAPTURE_RE.match(text)
    prefix = match.group(1).lower() if match else ""
    kind = core.CAPTURE_PREFIXES.get(prefix)
    if kind in _LOG_KINDS:
        captured = core.parse_capture(text)
        note = brain.create(kind, captured["title"] or day, captured["tags"], captured["body"], _today())
        slug = note["path"].rsplit("/", 1)[-1][:-3]
        line = f"{_LOG_KINDS[kind]}: [[{slug}|{captured['title']}]]"
        print(f"Added {kind}: brain/{note['path']}")
    else:
        line = match.group(2).strip() if kind == "journal" else text
    page = brain.log(line, day, _now_time())
    print(f"Logged to brain/{page['path']}")
    return 0


def _show_log(brain: Brain, day: str, days: int) -> int:
    pages = {n["slug"]: n for n in brain.notes() if n["type"] == "journal"}
    start = dt.date.fromisoformat(day)
    shown = 0
    for offset in range(days):
        current = (start - dt.timedelta(days=offset)).isoformat()
        note = pages.get(current)
        entries = core.log_entries(note["body"]) if note else []
        if days > 1 and not entries:
            continue
        label = dt.date.fromisoformat(current).strftime("%A")
        print(f"{current} ({label})")
        if not entries:
            print('  Nothing logged. Add a line: cairn log "what happened"')
        for entry in entries:
            print(f"  {entry['time'] or '  ·  ':>5}  {core.plain_log_text(entry['text'])}")
        shown += 1
    if not shown:
        print("Nothing logged in that range.")
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



def _vibe_watch(brain: Brain, args) -> int:
    from . import watch

    if args.uninstall:
        print("Auto-start removed." if watch.uninstall_startup() else "Auto-start was not set up.")
        return 0
    options = {
        "opencode": not args.no_opencode,
        "vector": not args.no_vector,
        "everywhere": args.everywhere,
        "days": args.days,
        "idle_minutes": args.idle,
    }
    if args.vector_process:
        options["vector_names"] = tuple(args.vector_process)
    if args.install:
        extra = [f"--every={args.every}", f"--idle={args.idle}", f"--days={args.days}"]
        extra += ["--everywhere"] if args.everywhere else []
        extra += ["--no-opencode"] if args.no_opencode else []
        extra += ["--no-vector"] if args.no_vector else []
        extra += [f"--vector-process={name}" for name in args.vector_process or []]
        try:
            target = watch.install_startup(brain, extra)
        except watch.WatchError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"The watcher now starts, hidden, when you sign in: {target}")
        print("Start it now without signing out:  cairn vibe watch")
        return 0
    try:
        if args.once:
            messages = watch.watch_once(brain, **options)
            print("\n".join(messages) if messages else "No new or changed sessions.")
            return 0
        watch.watch(brain, every=args.every, **options)
    except watch.WatchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped watching.")
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
        elif args.action == "watch":
            return _vibe_watch(brain, args)
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
    log_parser = sub.add_parser(
        "log",
        help='daily log: cairn log "fixed the GPX bug" (no text: show today)',
        description="Add a timestamped line to today's journal page, or show it. Prefixes work like the app: "
        "todo:, idea:, decision: and note: create that note and log a link to it.",
    )
    log_parser.add_argument("text", nargs="*")
    log_parser.add_argument("--day", help="YYYY-MM-DD, today or yesterday (default: today)")
    log_parser.add_argument("--days", type=int, default=1, help="show the last N days")
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
    vibe_parser.add_argument("action", choices=["start", "log", "skill", "check", "end", "status", "watch"])
    vibe_parser.add_argument("text", nargs="*", help="goal / log text / end summary; for check, the command after --")
    vibe_parser.add_argument("--kind", default="note", choices=core.LOG_KINDS, help="log: note, prompt, pass, fail, commit, skill (a text starting with /refine, /council... is a skill)")
    vibe_parser.add_argument("--task", help="start: link a task, e.g. ship-v1")
    vibe_parser.add_argument("--agent", help="start: who is coding, e.g. claude, copilot, cursor")
    vibe_parser.add_argument("--session", help="which live session to write to (default: $CAIRN_SESSION, else the newest)")
    vibe_parser.add_argument("--result", default="shipped", choices=["shipped", "partial", "dropped"], help="end: the outcome")
    vibe_parser.add_argument("--once", action="store_true", help="watch: one pass, then exit")
    vibe_parser.add_argument("--every", type=int, default=60, help="watch: seconds between passes (default 60)")
    vibe_parser.add_argument("--idle", type=int, default=30, help="watch: minutes without activity before a session ends (default 30)")
    vibe_parser.add_argument("--days", type=int, default=14, help="watch: how far back to pick up OpenCode sessions (default 14)")
    vibe_parser.add_argument("--everywhere", action="store_true", help="watch: OpenCode sessions from every folder, not just this repo")
    vibe_parser.add_argument("--no-opencode", action="store_true", help="watch: skip OpenCode")
    vibe_parser.add_argument("--no-vector", action="store_true", help="watch: skip Vector detection")
    vibe_parser.add_argument("--vector-process", action="append", help="watch: process name(s) that mean Vector is open (default Vector.exe)")
    vibe_parser.add_argument("--install", action="store_true", help="watch: start the watcher when you sign in to Windows")
    vibe_parser.add_argument("--uninstall", action="store_true", help="watch: stop starting it at sign-in")
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
        "log": cmd_log,
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

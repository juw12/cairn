"""cairn: command-line entry point."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

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
            if args.progress is not None:
                if not 0 <= args.progress <= 100:
                    print("--progress takes a percentage from 0 to 100", file=sys.stderr)
                    return 2
                # The app reads "[40%] ..." as the agent's own progress and paces the time left from it.
                text = f"[{args.progress}%] {text}".strip()
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


def cmd_pair(brain: Brain, args) -> int:
    """Like `opencode pair`: ask the running desktop app for a QR code and print it here."""
    import http.client

    from .desktop import PORT

    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
        conn.request("POST", "/api/desktop", body=json.dumps({"action": "phone-pair"}),
                     headers={"X-Cairn": "1", "Content-Type": "application/json", "Host": f"127.0.0.1:{PORT}"})
        response = conn.getresponse()
        result = json.loads(response.read() or b"{}")
        conn.close()
    except (OSError, ValueError):
        print("Cairn isn't running. Start the desktop app (cairn desktop) and try again.", file=sys.stderr)
        return 1
    if response.status != 200:
        print(f"Couldn't make a pairing code: {result.get('error', response.status)}", file=sys.stderr)
        return 1
    try:
        import segno

        segno.make(result["url"], error="m").terminal(compact=True)
    except ImportError:
        pass  # no QR library: the link below works too
    print(f"Scan with your phone, or open: {result['url']}")
    for other in result.get("others", []):
        print(f"  other address: {other}")
    print("The code works once and expires in 5 minutes. Phone and PC need the same Wi-Fi (or Tailscale).")
    return 0


def cmd_relay(brain: Brain, args) -> int:
    """Chat from anywhere through a private GitHub repo (see cairn/relay.py)."""
    from . import relay

    try:
        if args.relay_action == "setup":
            config = relay.setup(args.repo or "", args.proxy or "")
            print(f"Relay ready: https://github.com/{config['repo']} (private).")
            print("The desktop app answers from it on its next start; or run `cairn relay run` now.")
            print(f"On your phone: open Cairn, sign in with a token that can read and write {config['repo']}, open Chat.")
            return 0
        if args.relay_action == "run":
            relay.Relay(relay.connect()).run()
            return 0
        config = relay.load_config()
        print(f"Relay: {config['repo']}" if config else "Relay isn't set up. Run `cairn relay setup`.")
        return 0
    except relay.RelayError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


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
    sub.add_parser("pair", help="pair a phone: show a QR code to watch Cairn from your phone (desktop app must run)")
    relay_parser = sub.add_parser(
        "relay",
        help="chat from your phone anywhere, through a private GitHub repo",
        description="setup: create the private relay repo and remember it | run: answer chats in this terminal | status",
    )
    relay_parser.add_argument("relay_action", nargs="?", choices=["setup", "run", "status"], default="status")
    relay_parser.add_argument("--repo", help="owner/name of the relay repo (default: <you>/cairn-relay)")
    relay_parser.add_argument("--proxy", help="HTTP proxy for GitHub, e.g. http://proxy:80")
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
    vibe_parser.add_argument("--progress", type=int, metavar="PCT", help="log: how far along the work is (0-100); the Vibe page shows it as the live bar and paces the time left from it")
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
    mcp_parser = sub.add_parser(
        "mcp",
        help="let AI assistants (Claude, Cursor, Copilot, OpenCode) read and write the brain over MCP",
        description="Model Context Protocol server on stdio. Point your assistant at it with the snippet from --print-config.",
    )
    mcp_parser.add_argument("--read-only", action="store_true", help="leave out the tools that write")
    mcp_parser.add_argument("--print-config", action="store_true", help="print the config to paste into MCP clients, then exit")
    precedent_parser = sub.add_parser(
        "precedent",
        help="give a goal once; get what you also asked for after similar goals (your own history)",
        description="brief GOAL | learn | log GOAL DELIVERED [CORRECTION] | queue TEXT [--auto] | next | init | install",
    )
    precedent_parser.add_argument("action", choices=["brief", "learn", "log", "queue", "next", "init", "install"])
    precedent_parser.add_argument("text", nargs="*", help="the goal / the queue item / for log: goal, delivered, correction")
    precedent_parser.add_argument("--auto", action="store_true", help="queue: safe to run unattended")
    precedent_parser.add_argument("--needs-me", action="store_true", help="queue: waiting on you (credential, payment, sending)")
    precedent_parser.add_argument("--k", type=int, default=5, help="brief: how many precedents")
    precedent_parser.add_argument("--import-skill", default="", help="init: a standalone precedent skill folder to move in")
    precedent_parser.add_argument("--queue-file", default="", help="init: an old Queue.md whose open items become #queue tasks")
    precedent_parser.add_argument("--agent-app", default="", help="init: the old Precedent Agent app folder (job history)")
    autopilot_parser = sub.add_parser(
        "autopilot",
        help="run your coding agent round after round on a goal, then on #queue #auto tasks",
        description="status | on | off | add GOAL | run | set KEY=VALUE ...",
    )
    autopilot_parser.add_argument("action", nargs="?", default="status", choices=["status", "on", "off", "add", "run", "set"])
    autopilot_parser.add_argument("text", nargs="*")
    autopilot_parser.add_argument("--workdir", default="", help="add: where the agent works (default: a new folder per goal)")
    feedback_parser = sub.add_parser("feedback", help='record feedback: cairn feedback "toggle is hard to find" --rating 3')
    feedback_parser.add_argument("text", nargs="+")
    feedback_parser.add_argument("--rating", type=int, choices=range(1, 6), metavar="1-5")
    feedback_parser.add_argument("--from", dest="source", help="who said it")
    feedback_parser.add_argument("--about", help="session or note it is about (default: the latest session)")
    return parser


def cmd_mcp(brain: Brain, args) -> int:
    from . import mcp

    if args.print_config:
        print(mcp.client_config(brain, args.read_only))
        return 0
    return mcp.serve(brain, args.read_only)


def cmd_precedent(brain: Brain, args) -> int:
    from . import precedent

    text = " ".join(args.text).strip()
    if args.action == "brief":
        if precedent.library_age_days() is None:
            print("(No precedent library yet: `cairn precedent learn` builds it from your history.)\n")
        print(precedent.brief(brain, text, args.k))
        return 0
    if args.action == "learn":
        report = precedent.learn(brain)
        print(f"Learned {report['precedents']} past goals from {report['prompts']} prompts, "
              f"{report['updates']} shipped notes, {report['lessons']} lessons -> {report['folder']}")
        return 0
    if args.action == "log":
        if not args.text:
            print('Usage: cairn precedent log "goal" "what you delivered" ["correction"]', file=sys.stderr)
            return 2
        parts = list(args.text) + ["", ""]
        precedent.log_lesson(parts[0], parts[1], parts[2])
        print("Logged.")
        return 0
    if args.action == "queue":
        if not text:
            for item in precedent.queue_items(brain):
                flags = (" #auto" if item["auto"] else "") + (" #needs-me" if item["needs_me"] else "")
                print(f"[{item['status']}] {item['title']}{flags}  (brain/{item['path']})")
            return 0
        note = precedent.queue_add(brain, text, auto=args.auto, needs_me=args.needs_me)
        print(f"Queued: brain/{note['path']}")
        return 0
    if args.action == "next":
        item = precedent.queue_next(brain, auto_only=args.auto)
        if not item:
            print("The queue is empty.")
            return 0
        print(f"{item['text']}\n(brain/{item['path']})")
        return 0
    if args.action == "init":
        report = {}
        # Import first, so a profile moved in from the old skill wins over the blank template.
        if args.import_skill or args.queue_file:
            report = precedent.import_legacy(
                brain, Path(args.import_skill) if args.import_skill else None,
                Path(args.queue_file) if args.queue_file else None)
        path, created = precedent.ensure_profile(brain)
        if args.agent_app:
            from . import autopilot

            report["jobs"] = autopilot.import_legacy_jobs(Path(args.agent_app))
        print(f"Profile: brain/{path}" + (" (moved in)" if report.get("profile") else " (created; fill it in)" if created else ""))
        if report:
            print("Moved in: " + ", ".join(f"{key} {value}" for key, value in report.items()))
        return 0
    if args.action == "install":
        written = precedent.install_commands(brain)
        print("\n".join(f"Wrote {path}" for path in written) or "No agent command folders found (Claude Code, OpenCode, Codex).")
        return 0
    return 2


def cmd_autopilot(brain: Brain, args) -> int:
    from . import autopilot

    text = " ".join(args.text).strip()
    if args.action == "on":
        autopilot.update_settings({"enabled": True})
        print("Autopilot on. It runs inside `cairn serve` / the desktop app, or here with `cairn autopilot run`.")
        return 0
    if args.action == "off":
        autopilot.update_settings({"enabled": False})
        print("Autopilot off. A round already running finishes first.")
        return 0
    if args.action == "add":
        if not text:
            print('Usage: cairn autopilot add "goal"', file=sys.stderr)
            return 2
        job = autopilot.create_job(text, args.workdir)
        print(f"Queued job {job['id']}: {job['goal']}\nWorking folder: {job['workdir']}")
        return 0
    if args.action == "set":
        changes = dict(pair.split("=", 1) for pair in args.text if "=" in pair)
        for key, value in list(changes.items()):
            if value.lower() in ("true", "false", "on", "off"):
                changes[key] = value.lower() in ("true", "on")
        print(json.dumps(autopilot.update_settings(changes), indent=2))
        return 0
    if args.action == "run":
        autopilot.update_settings({"enabled": True})
        worker = autopilot.Autopilot(brain)
        worker.start()
        print("Autopilot running (Ctrl+C to stop).")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            worker.shutdown()
        return 0
    state = autopilot.state()
    config = state["settings"]
    print(f"Autopilot: {'on' if config['enabled'] else 'off'}, engine {config['engine']}"
          + ("" if state["engine_found"] else " (not found on PATH)"))
    for job in state["jobs"][:10]:
        print(f"  [{job['status']}] {job['goal'][:90]}  ({job['rounds']} rounds)")
    queued = precedent_queue_count(brain)
    print(f"Queue: {queued} #auto task(s) waiting." if queued else "Queue: no #auto tasks waiting.")
    return 0


def precedent_queue_count(brain: Brain) -> int:
    from . import precedent

    return sum(1 for item in precedent.queue_items(brain) if item["auto"] and not item["needs_me"])


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
        "mcp": cmd_mcp,
        "precedent": cmd_precedent,
        "autopilot": cmd_autopilot,
        "serve": cmd_serve,
        "desktop": cmd_desktop,
        "pair": cmd_pair,
        "relay": cmd_relay,
        None: cmd_serve,
    }
    if args.command is None:
        args.port = 4777
        args.no_browser = False
    return commands[args.command](brain, args)

"""cairn: command-line entry point."""

from __future__ import annotations

import argparse
import datetime as dt
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


def cmd_serve(brain: Brain, args) -> int:
    from .server import serve

    if not brain.dir.exists():
        brain.init()
    serve(brain, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_desktop(brain: Brain, args) -> int:
    from . import desktop

    return desktop.main(["--hidden"] if args.hidden else [])


def cmd_pro(brain: Brain, args) -> int:
    """Cairn Pro for sellers (keygen, issue) and buyers (status, verify)."""
    import json
    import re
    from pathlib import Path

    from . import pro

    action = args.pro_command
    if action == "keygen":
        path = pro.seller_key_path()
        if path.exists() and not args.force:
            print(f"A seller key already exists at {path}. Keys issued with it stop working if you replace it.")
            print("Use --force only if you really want a new one.")
            return 1
        print("Making a 2048-bit seller key (a few seconds)...")
        pair = pro.generate_keypair()
        pro._save(path, {"n": pair["n"], "e": pair["e"], "d": pair["d"], "created": _today()})
        print(f"Private seller key saved to {path}")
        print("Back it up somewhere safe and never commit it. Anyone with it can make Pro keys.")
        if args.install:
            source_path = Path(pro.__file__)
            source = source_path.read_text(encoding="utf-8")
            source, count = re.subn(r'"n": \d+,[^\n]*', f'"n": {pair["n"]},', source, count=1)
            if not count:
                print("Could not find SELLER_PUBLIC_KEY in pro.py; paste the public key in by hand.")
                return 1
            source_path.write_text(source, encoding="utf-8")
            print(f"Public key installed in {source_path}. Commit that file so every copy of Cairn can check your keys.")
        else:
            print(json.dumps({"n": pair["n"], "e": pair["e"]}))
        return 0
    if action == "sell":
        client_id = args.paypal_client_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", client_id):
            print("That does not look like a PayPal client id (PayPal developer dashboard -> Apps -> Live).")
            return 1
        if not pro.seller_key_path().exists():
            print("No seller key yet, making one first.")
            args.force = False
            args.install = True
            args.pro_command = "keygen"
            if cmd_pro(brain, args):
                return 1
        source_path = Path(pro.__file__)
        source = source_path.read_text(encoding="utf-8")
        source, count = re.subn(
            r'PAYPAL_CLIENT_ID = os\.environ\.get\("CAIRN_PRO_PAYPAL_CLIENT", "[^"]*"\)',
            f'PAYPAL_CLIENT_ID = os.environ.get("CAIRN_PRO_PAYPAL_CLIENT", "{client_id}")',
            source,
            count=1,
        )
        if not count:
            print("Could not find PAYPAL_CLIENT_ID in pro.py.")
            return 1
        source_path.write_text(source, encoding="utf-8")
        print(f"In-app checkout is on: {source_path} now carries your PayPal client id (public, safe to commit).")
        print("Commit and release; every copy of Cairn then sells Pro inside the app.")
        return 0
    if action == "issue":
        path = pro.seller_key_path()
        private = pro._load(path, {})
        if not private.get("d"):
            print(f"No seller key at {path}. Run: cairn pro keygen --install")
            return 1
        if private["n"] != pro.SELLER_PUBLIC_KEY.get("n"):
            print("Warning: this seller key does not match the public key built into this copy of Cairn.")
        key = pro.issue_key(args.email, args.plan, private, days=args.days)
        print(key)
        return 0
    if action == "verify":
        try:
            payload = pro.verify_key(args.key)
        except ValueError as error:
            print(f"Not valid: {error}")
            return 1
        print(json.dumps(payload, indent=2))
        return 0
    status = pro.License().status()
    if status["pro"]:
        until = f" until {status['expires']}" if status["expires"] else ""
        print(f"Cairn Pro: {status['planName']}{until} ({status['email'] or 'no e-mail'}, from {status['source']})")
    else:
        print(f"Free plan. Trial goals left: {status['trialLeft']}. {status['reason']}".strip())
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
    pro_parser = sub.add_parser("pro", help="Cairn Pro: plan status, and key tools for the seller")
    pro_sub = pro_parser.add_subparsers(dest="pro_command")
    pro_sub.add_parser("status", help="show this computer's plan (default)")
    keygen_parser = pro_sub.add_parser("keygen", help="seller: make the signing key pair (once)")
    keygen_parser.add_argument("--install", action="store_true", help="write the public key into cairn/pro.py")
    keygen_parser.add_argument("--force", action="store_true", help="replace an existing seller key")
    issue_parser = pro_sub.add_parser("issue", help="seller: print a signed Pro key for a buyer")
    issue_parser.add_argument("--email", required=True)
    issue_parser.add_argument("--plan", default="lifetime", help="month or lifetime (default: lifetime)")
    issue_parser.add_argument("--days", type=int, default=None, help="override the plan length (0 = never ends)")
    sell_parser = pro_sub.add_parser("sell", help="seller: switch the in-app checkout on (one command)")
    sell_parser.add_argument("--paypal-client-id", required=True, help="public client id of your PayPal REST app")
    sell_parser.add_argument("--install", action="store_true", help=argparse.SUPPRESS)
    sell_parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    verify_parser = pro_sub.add_parser("verify", help="check a Pro key")
    verify_parser.add_argument("key")
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
        "serve": cmd_serve,
        "desktop": cmd_desktop,
        "pro": cmd_pro,
        None: cmd_serve,
    }
    if args.command is None:
        args.port = 4777
        args.no_browser = False
    return commands[args.command](brain, args)

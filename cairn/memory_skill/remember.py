#!/usr/bin/env python3
"""Remember something in the Cairn brain, then check it is really there.

    remember.py note "Short title" --body "What to remember." [--tags a,b]
    remember.py decision "Short title" --decision "..." [--context "..."] [--options "..."] [--consequences "..."]
    remember.py check                            is the brain's copy on its git remote up to date?

It never overwrites a note, and it refuses text that looks like a password, key or token.
When this computer has its own folder in the brain, it writes only there.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ in the AI app's skills folder
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SECRET_SIGNS = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:password|passwd|api[_-]?key|secret|token)\s*[:=]\s*\S{6,})"
)
NUMBERED = re.compile(r"^((?:[a-z]+-)?)(\d{4})-")


def refuse_secrets(*texts: str) -> None:
    for text in texts:
        if text and SECRET_SIGNS.search(text):
            raise SystemExit("Not saved: this looks like a password, key or token. Memory holds facts only.")


def decision_body(options) -> str:
    parts = [("Context", options.context), ("Options", options.options),
             ("Decision", options.decision), ("Consequences", options.consequences)]
    return "\n\n".join(f"## {heading}\n\n{text.strip()}" for heading, text in parts) + "\n"


def next_decision_name(folder: Path, slug: str) -> str:
    """Continue the numbering the folder already uses (0007-..., mac-0007-...)."""
    prefixes, highest = Counter(), {}
    for note in folder.glob("*.md"):
        match = NUMBERED.match(note.name)
        if match:
            prefixes[match.group(1)] += 1
            highest[match.group(1)] = max(highest.get(match.group(1), 0), int(match.group(2)))
    prefix = prefixes.most_common(1)[0][0] if prefixes else ""
    return f"{prefix}{highest.get(prefix, 0) + 1:04d}-{slug}.md"


def save_in_folder(brain, folder: Path, kind: str, title: str, tags: list[str], body: str, today: str) -> Path:
    """This computer's own folder: notes/ and decisions/ inside it, nothing anywhere else."""
    from cairn import core

    slug = core.slugify(title)
    if kind == "decision":
        (folder / "decisions").mkdir(parents=True, exist_ok=True)
        path = folder / "decisions" / next_decision_name(folder / "decisions", slug)
    else:
        path = folder / "notes" / f"{today}-{slug}.md"
    if brain.dir.resolve() not in path.resolve().parents:
        raise SystemExit(f"Not saved: {path} is outside the brain.")
    meta = {"title": title, "type": kind}
    if kind == "decision":
        meta["status"] = "accepted"
    meta["tags"] = [folder.name, *[tag for tag in tags if tag != folder.name]]
    meta["created"] = today
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(core.serialize_note(meta, body))
    except FileExistsError:
        raise SystemExit(f"Not saved: {path} already exists. Add to that note instead.")
    return path


def save_shared(brain, kind: str, title: str, tags: list[str], body: str, today: str) -> Path:
    """No folder of its own: the brain's usual notes/ and decisions/, named the way Cairn names them."""
    brain.init()
    note = brain.create(kind, title, tags, body, today)
    if kind == "decision":
        common.call(brain, "update_note", {"path": note["path"], "status": "accepted"}, read_only=False)
    return brain.dir / note["path"]


def git_in(folder: Path, *arguments: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(folder), *arguments], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def git(brain, *arguments: str) -> str:
    return git_in(brain.dir, *arguments)


def check(brain, path: Path | None = None) -> bool:
    """Where the memory stands: on this computer, and against the copy on the git remote as last seen."""
    fine = True
    if path is not None:
        fine = path.is_file() and path.stat().st_size > 0
        print(f"{'ok ' if fine else 'NO '} on this computer: {path}")
    remote = git(brain, "remote", "get-url", "origin")
    if not remote:
        print("... no git remote: this brain has no copy off this computer.")
        return fine
    if path is not None:
        # asked from the note's own folder, so git and the system need not agree on how the path is spelt
        waiting = bool(git_in(path.parent, "status", "--porcelain", "--", path.name))
        print(f"{'...' if waiting else 'ok '} {'not committed yet: Cairn sends it at its next sync' if waiting else 'committed'}")
    unsent = git(brain, "rev-list", "--count", "@{u}..HEAD") or "?"
    changed = len([line for line in git(brain, "status", "--porcelain").splitlines() if line])
    print(f"Copy on the git remote: {remote}")
    print(f"  last sent {git(brain, 'log', '-1', '--format=%ci', '@{u}') or 'never'}; {unsent} commit(s) not sent; {changed} changed file(s) waiting")
    return fine


def main(argv: list[str] | None = None) -> int:
    common.use_cairns_python()
    common.plain_output()
    parser = argparse.ArgumentParser(prog="remember.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    note = commands.add_parser("note")
    note.add_argument("title")
    note.add_argument("--body", required=True)
    note.add_argument("--tags", default="")
    decision = commands.add_parser("decision")
    decision.add_argument("title")
    decision.add_argument("--decision", required=True)
    decision.add_argument("--context", default="")
    decision.add_argument("--options", default="")
    decision.add_argument("--consequences", default="")
    decision.add_argument("--tags", default="")
    commands.add_parser("check")
    options = parser.parse_args(argv)

    brain = common.open_brain()
    if options.command == "check":
        check(brain)
        return 0

    tags = [tag.strip().lstrip("#") for tag in options.tags.split(",") if tag.strip().lstrip("#")]
    if options.command == "decision":
        refuse_secrets(options.title, options.context, options.options, options.decision, options.consequences)
        body = decision_body(options)
    else:
        refuse_secrets(options.title, options.body)
        body = options.body.strip() + "\n"
    today = dt.date.today().isoformat()
    folder = common.write_folder(brain)
    if folder is not None:
        path = save_in_folder(brain, folder, options.command, options.title, tags, body, today)
    else:
        path = save_shared(brain, options.command, options.title, tags, body, today)
    return 0 if check(brain, path) else 1


if __name__ == "__main__":
    sys.exit(main())

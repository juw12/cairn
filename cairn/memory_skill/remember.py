#!/usr/bin/env python3
"""Remember something in the Cairn brain, then check it is really there.

    remember.py note "Short title" --body "What to remember." [--tags a,b]
    remember.py decision "Short title" --decision "..." [--context "..."] [--options "..."] [--consequences "..."]
    remember.py check                            is the brain's copy on its git remote up to date?
    remember.py handoff "Short title" --to "computer" [--to "another"] --body "What it must know or do."
    remember.py answer "a few words of the conversation" --says picked-up|done|note|closed [--text "..."]

    remember.py begin "what the user asked, in one line"       this chat's session on Cairn's Vibe page
    remember.py step "what was just done" [--progress 40] [--kind note|prompt|pass|fail|commit]
    remember.py finish shipped|partial|dropped|done "what came out"

It never overwrites a note, and it refuses text that looks like a password, key or token.
When this computer has its own folder in the brain, what this script saves goes there.

Add --cloud to save straight on GitHub instead of in the folder on this computer. Without a folder on this
computer that happens by itself.
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


def save_on_github(options, tags: list[str], body: str, today: str) -> int:
    """No folder on this computer: the note is made on GitHub, where every device's Cairn picks it up."""
    import cloud

    brain = common.open_cloud(max_age=0)  # the newest list of notes, so a decision gets the next free number
    folder = (common.CONFIG.get("write_folder") or "").strip("/")
    kind = options.command
    try:
        relative = brain.new_path(kind, options.title, today, folder)
        if folder:
            tags = [Path(folder).name, *[tag for tag in tags if tag != Path(folder).name]]
        text = cloud.note_text(options.title, kind, tags, body, today, "accepted" if kind == "decision" else "")
        saved = brain.save(relative, text, f"brain: remember {options.title[:60]} [skip ci]")
        size = brain.on_github(relative)
    except cloud.CloudError as error:
        raise SystemExit(str(error))
    fine = size > 0
    print(f"{'ok ' if fine else 'NO '} on GitHub: {brain.repo}/{relative} (commit {saved['commit']})")
    print("     No copy was written to a brain folder on this computer; each device's Cairn gets it at its next sync.")
    return 0 if fine else 1


def check_github() -> int:
    brain = common.open_cloud(max_age=0)
    state = brain._state()
    print(f"The brain is on GitHub: {brain.repo}, last change {state.get('committed') or 'unknown'} "
          f"(commit {str(state.get('commit', ''))[:7]}).")
    print(f"  {len(state.get('shas', {}))} notes are readable from this computer without a synced copy.")
    return 0


REPORTS = ("begin", "step", "finish")


def report(options) -> int:
    """The chat's own session on Cairn's Vibe page: what it is doing, each step, how it ended."""
    if common.where(options.cloud) == "cloud":
        raise SystemExit("A chat's session is kept in the brain's folder, which is not on this computer. "
                         "Nothing was written; go on with the work.")
    if not common.checks_in():
        raise SystemExit("This app's chats are not reported through the skill (Cairn's watcher reads them). "
                         "Nothing was written; go on with the work.")
    brain = common.open_brain()
    from cairn import chatlog

    here, app = Path.cwd(), common.CONFIG.get("app") or ""
    text = " ".join(options.text).strip()
    refuse_secrets(text)
    try:
        if options.command == "begin":
            if not text:
                raise SystemExit('Say what the chat is doing: remember.py begin "the request in one line"')
            entry = chatlog.checkin(brain, here, agent=app, task=text)
            print(f"On Cairn: {entry['path']} ({entry['state']})")
        elif options.command == "step":
            entry = chatlog.step(brain, here, text, kind=options.kind, progress=options.progress, agent=app)
            print(f"Logged in {entry['path']}")
        else:
            entry = chatlog.finish(brain, here, options.result, text, agent=app)
            print(f"Ended as {options.result}: {entry['path']}")
    except chatlog.ChatError as error:
        raise SystemExit(str(error))
    return 0


def main(argv: list[str] | None = None) -> int:
    common.plain_output()
    parser = argparse.ArgumentParser(prog="remember.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("begin")
    begin.add_argument("text", nargs="+")
    step = commands.add_parser("step")
    step.add_argument("text", nargs="+")
    step.add_argument("--progress", type=int, default=None, metavar="PCT")
    step.add_argument("--kind", default="note", choices=["note", "prompt", "pass", "fail", "commit", "skill"])
    finish = commands.add_parser("finish")
    finish.add_argument("result", choices=["shipped", "partial", "dropped", "done"])
    finish.add_argument("text", nargs="*")
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
    handoff = commands.add_parser("handoff")
    handoff.add_argument("title")
    handoff.add_argument("--to", action="append", default=[], required=True)
    handoff.add_argument("--body", default="")
    answer = commands.add_parser("answer")
    answer.add_argument("thread")
    answer.add_argument("--says", default="note", choices=["note", "picked-up", "done", "closed"])
    answer.add_argument("--text", default="")
    parser.add_argument("--cloud", action="store_true", help="save on GitHub, not in the folder on this computer")
    for command in commands.choices.values():
        command.add_argument("--cloud", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    options = parser.parse_args(argv)

    if options.command in REPORTS:
        return report(options)
    in_cloud = common.where(options.cloud) == "cloud"
    if options.command == "check":
        if in_cloud:
            return check_github()
        brain = common.open_brain()
        common.checkin(brain)
        check(brain)
        return 0
    if options.command in ("handoff", "answer"):
        if in_cloud:
            raise SystemExit("A message between computers needs the brain's folder on this computer. Save a note "
                             "instead (remember.py note ...) and tell the user it is for the other computer.")
        brain = common.open_brain()
        if options.command == "handoff":
            # A message for another computer is its own file in handoffs/, never an edit of someone else's note.
            refuse_secrets(options.title, options.body)
            print(common.call(brain, "handoff_send", {"title": options.title, "to": options.to, "body": options.body}, read_only=False))
            common.saved_step(brain, f"left for {', '.join(options.to)}: {options.title}")
        else:
            refuse_secrets(options.text)
            print(common.call(brain, "handoff_answer", {"thread": options.thread, "says": options.says, "text": options.text}, read_only=False))
            common.saved_step(brain, f"answered another computer ({options.says}): {options.thread}")
        return 0

    tags = [tag.strip().lstrip("#") for tag in options.tags.split(",") if tag.strip().lstrip("#")]
    if options.command == "decision":
        refuse_secrets(options.title, options.context, options.options, options.decision, options.consequences)
        body = decision_body(options)
    else:
        refuse_secrets(options.title, options.body)
        body = options.body.strip() + "\n"
    today = dt.date.today().isoformat()
    if in_cloud:
        return save_on_github(options, tags, body, today)
    brain = common.open_brain()
    folder = common.write_folder(brain)
    if folder is not None:
        path = save_in_folder(brain, folder, options.command, options.title, tags, body, today)
    else:
        path = save_shared(brain, options.command, options.title, tags, body, today)
    common.saved_step(brain, f"saved {options.command}: {options.title}")
    return 0 if check(brain, path) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Hand a private repo to another of your GitHub accounts, so its Actions minutes count there.

GitHub counts a private repo's Actions minutes against the account that owns the repo, whoever
pushes to it. When one account's free minutes run out and another of yours has room, the way to
spread the load is to move a repo: GitHub mails the new owner, and the move happens when the link
in that mail is opened (it lasts a day). GitHub keeps the old owner as a collaborator, so it can
still push; issues, secrets and history go along; the old address redirects.

Three limits keep a press from doing harm. It only moves between accounts the GitHub CLI is signed
in to on this computer, so a typo cannot hand a repo to a stranger. It only moves a private repo: a
public one's minutes are free already. And it only asks: nothing changes hands until the new owner
confirms. Each request leaves a note in github-usage/moves/, which is how the Vibe page knows a
move is waiting.
"""

from __future__ import annotations

import datetime as dt
import re

from . import core, github_usage, publish
from .brain import Brain, ConflictError

FOLDER = f"{github_usage.FOLDER}/moves"
REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
CONFIRM_HOURS = 24  # how long GitHub keeps the link it mails to the new owner


class MoveError(Exception):
    """A move that was not asked for, with a sentence a person can act on."""


def note_path(source: str, repo: str) -> str:
    return f"{FOLDER}/{source}-{repo}.md".lower()


def move_note(repo: str, source: str, target: str, stamp: str) -> tuple[str, str]:
    """(path, text) of the note a request leaves. Frontmatter is what the Vibe page reads."""
    meta = {
        "title": f"Move {repo} to {target}",
        "type": "note",
        "tags": ["github"],
        "gh_move_repo": repo,
        "gh_move_from": source,
        "gh_move_to": target,
        "gh_move_at": stamp,
    }
    lines = [
        f"Cairn asked GitHub to move the private repo `{source}/{repo}` to `{target}` at {stamp.replace('T', ' ')},",
        f"so that its Actions minutes count against {target}'s free minutes from then on.",
        "",
        f"- GitHub mailed {target} a link. The move happens when that link is opened, within {CONFIRM_HOURS} hours.",
        f"- {source} stays on the repo as a collaborator and can still push.",
        f"- The old address redirects. To point a clone at the new one: `git remote set-url origin https://github.com/{target}/{repo}.git`",
        f"- To take it back, {target} moves it again from the repo's Settings on GitHub.",
    ]
    return note_path(source, repo), core.serialize_note(meta, "\n".join(lines) + "\n")


def _refusal(status: int, data) -> str:
    message = data.get("message", "") if isinstance(data, dict) else ""
    errors = data.get("errors") if isinstance(data, dict) else None
    detail = ""
    if isinstance(errors, list) and errors:
        first = errors[0]
        detail = str(first.get("message", "") if isinstance(first, dict) else first)
    parts = [part for part in (message, detail) if part]
    return f"{status}{': ' + '. '.join(parts) if parts else ''}"


def check(repo: str, source: str, target: str, http) -> dict:
    """The repo as GitHub describes it, or MoveError when it is not one to move."""
    status, data = http("GET", f"/repos/{source}/{repo}")
    if status == 404:
        raise MoveError(f"{source} has no repo named {repo}.")
    if status >= 300 or not isinstance(data, dict):
        raise MoveError(f"GitHub would not show {source}/{repo} ({_refusal(status, data)}).")
    owner = str((data.get("owner") or {}).get("login", ""))
    if owner.lower() == target.lower():
        raise MoveError(f"{repo} is already with {target}.")
    if owner.lower() != source.lower():
        raise MoveError(f"{repo} belongs to {owner or 'someone else'} now, not {source}.")
    if not data.get("private"):
        raise MoveError(f"{repo} is public, and Actions minutes in a public repo are free. Moving it would change nothing.")
    if not (data.get("permissions") or {}).get("admin"):
        raise MoveError(f"This sign-in may not move {repo}; only its owner can.")
    return data


def move(brain: Brain | None, repo: str, source: str, target: str, http=None, token_of=None, now: dt.datetime | None = None) -> dict:
    """Ask GitHub to move the private repo `source/repo` to `target`. Returns what was asked and what
    happens next. Raises MoveError for anything that should not or could not be asked. `http` and
    `token_of(name)` stand in for GitHub and the GitHub CLI in tests."""
    repo = str(repo or "").strip()
    try:
        source, target = github_usage.account_name(source), github_usage.account_name(target)
    except ValueError as error:
        raise MoveError(str(error)) from None
    if not REPO_RE.fullmatch(repo) or repo in (".", ".."):
        raise MoveError(f"{repo!r} is not a repo name.")
    if source.lower() == target.lower():
        raise MoveError(f"{repo} is already with {target}.")
    token_of = github_usage.account_token if token_of is None else token_of
    try:
        token_of(target)  # proof that the new owner is an account of yours, nothing is done with it
        token = token_of(source)
    except publish.PublishError as error:
        raise MoveError(str(error)) from None
    http = publish.make_http(token) if http is None else http
    check(repo, source, target, http)
    status, data = http("POST", f"/repos/{source}/{repo}/transfer", {"new_owner": target})
    if status != 202:
        raise MoveError(f"GitHub refused to move {repo} to {target} ({_refusal(status, data)}).")
    stamp = (dt.datetime.now() if now is None else now).strftime("%Y-%m-%dT%H:%M")
    result = {
        "repo": repo,
        "from": source,
        "to": target,
        "at": stamp,
        "note": "",
        "message": f"GitHub mailed {target} a link. {repo} moves when it is opened, within {CONFIRM_HOURS} hours; {source} keeps push access.",
    }
    if brain is not None:
        path, text = move_note(repo, source, target, stamp)
        try:
            if brain.resolve(path).exists():
                brain.write(path, text, brain.read(path)["mtime"])
            else:
                brain.write(path, text)
            result["note"] = path
        except (ConflictError, OSError, ValueError):
            pass  # the request stands; only the reminder on the page is missing
    return result

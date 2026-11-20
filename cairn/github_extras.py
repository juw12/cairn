"""What a git mirror leaves out, kept beside it by `cairn vibe github copy`.

A mirror is the code and its history. The rest of a repo lives only on GitHub, so it goes in
`FOLDER/owner/name.extras/`:

- `repo.json`: what the repo page says (description, site, topics, private or public, main branch).
- `issues.json`, `comments.json`, `review-comments.json`: every issue and pull request and what was
  said under them, as GitHub's API gives them.
- `releases.json` and `releases/TAG/FILE`: every release and the files attached to it.
- `actions.json`: the Actions variables with their values, and the names of the Actions secrets.
- `wiki.git`: a mirror of the wiki, when the repo has one.

Files kept in Git LFS are fetched into the mirror itself (`git lfs fetch --all`).

One thing cannot be copied by anyone: the value of an Actions secret. GitHub never gives it back, so
the names are kept and the values stay wherever you made them.

A repo's extras are fetched again when it was pushed to, when an issue, pull request or release
changed, and once a week whatever happened.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from . import publish

FULL_DAYS = 7  # a repo's extras are fetched again at least this often
PAGES = 200  # the most pages of one list that are read (100 rows each)
QUIET_SECONDS = 900  # how long a download may say nothing: an office proxy reads a big file whole before passing it on
CHUNK = 1 << 20
ABOUT = ("description", "homepage", "topics", "private", "visibility", "default_branch", "archived", "fork", "has_issues", "has_wiki", "has_pages", "created_at")

STAMPS = """
query($cursor: String) {
  viewer {
    repositories(first: 50, after: $cursor, ownerAffiliations: OWNER) {
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        issues(first: 1, orderBy: {field: UPDATED_AT, direction: DESC}) { totalCount nodes { updatedAt } }
        pullRequests(first: 1, orderBy: {field: UPDATED_AT, direction: DESC}) { totalCount nodes { updatedAt } }
        releases(first: 1, orderBy: {field: CREATED_AT, direction: DESC}) { totalCount nodes { updatedAt } }
      }
    }
  }
}"""


class ExtrasError(Exception):
    """One part of a repo's extras that was not copied, with a sentence a person can act on."""


def extras_folder(folder: Path, key: str) -> Path:
    owner, name = key.split("/", 1)
    return folder / owner / (name + ".extras")


def fetch_stamps(http) -> dict:
    """{"owner/name": {"stamp", "talk", "releases"}} for every repo of the signed-in account, from three
    cheap calls, so a run knows which repos changed without asking each one. {} when GitHub will not
    say: the repos are then looked at once a week."""
    found: dict = {}
    cursor = None
    for _ in range(PAGES):
        try:
            status, data = http("POST", "/graphql", {"query": STAMPS, "variables": {"cursor": cursor}})
            page = data["data"]["viewer"]["repositories"]
        except (publish.PublishError, KeyError, TypeError):
            return {}
        if status >= 300:
            return {}
        for node in page["nodes"]:
            parts = [node[kind] for kind in ("issues", "pullRequests", "releases")]
            newest = ["".join(str(row.get("updatedAt", "")) for row in part["nodes"]) for part in parts]
            found[node["nameWithOwner"]] = {
                "stamp": "|".join(f"{part['totalCount']}:{when}" for part, when in zip(parts, newest)),
                "talk": parts[0]["totalCount"] + parts[1]["totalCount"],
                "releases": parts[2]["totalCount"],
            }
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return found


def stamp_of(repo: dict, facts: dict | None) -> str:
    return f"{repo['pushed_at']}|{facts['stamp']}" if facts else ""


def is_due(known: dict, stamp: str, now: dt.datetime) -> bool:
    """Never fetched, failed last time, changed on GitHub since, or a week old."""
    if not known.get("at") or known.get("error"):
        return True
    if stamp and known.get("stamp") != stamp:
        return True
    try:
        return now - dt.datetime.fromisoformat(known["at"]) >= dt.timedelta(days=FULL_DAYS)
    except ValueError:
        return True


def size_text(count: int) -> str:
    return f"{count / 1024**3:.1f} GB" if count >= 1024**3 else f"{count / 1024**2:.0f} MB" if count >= 1024**2 else f"{count / 1024:.0f} KB"


def held_text(held: dict) -> str:
    """"61 issues and pull requests, 95 releases with 289 files (13.5 GB), 3 secret names, the wiki"."""
    parts = []
    if held.get("talk"):
        parts.append(f"{held['talk']} issues and pull requests")
    if held.get("releases"):
        files = f" with {held['files']} files ({size_text(held['bytes'])})" if held.get("files") else ""
        parts.append(f"{held['releases']} releases{files}")
    if held.get("secrets"):
        parts.append(f"{held['secrets']} secret names")
    for name, words in (("wiki", "the wiki"), ("lfs", "Git LFS files")):
        if held.get(name):
            parts.append(words)
    return ", ".join(parts)


def said(held: dict) -> str:
    """One line about a repo whose extras were just fetched; nothing for a repo that has none."""
    text = held_text(held)
    if held.get("error"):
        return f"{text + '; ' if text else ''}not complete ({held['error']})"
    return f"with {text}" if text else ""


# ------------------------------------------------------------ the parts


def _pages(http, path: str, gone_is_empty: bool = False) -> list:
    """Every row of one of GitHub's lists. 404 and 410 mean the repo has that part switched off."""
    rows: list = []
    for page in range(1, PAGES + 1):
        try:
            status, data = http("GET", f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        except publish.PublishError as error:
            raise ExtrasError(str(error)) from None
        if gone_is_empty and status in (404, 410):
            return rows
        if status >= 300 or not isinstance(data, list):
            raise ExtrasError(f"GitHub answered {status} for {path.split('?')[0]}")
        rows += data
        if len(data) < 100:
            break
    return rows


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def safe_name(text: str) -> str:
    """A tag or file name as one folder or file name on any computer."""
    cleaned = "".join("_" if char in '<>:"/\\|?*' or ord(char) < 32 else char for char in str(text)).strip(". ")
    return cleaned or "_"


def download(url: str, path: Path, token: str) -> None:
    """One release file. The sign-in goes to GitHub only: it is not passed on to the file store GitHub
    sends the request to."""
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream", "User-Agent": "cairn"})
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    part = path.with_name(path.name + ".part")  # a download that stops half way is never taken for the file
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=QUIET_SECONDS, context=publish.ssl_context()) as response, open(part, "wb") as out:
            shutil.copyfileobj(response, out, CHUNK)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
        part.unlink(missing_ok=True)
        reason = getattr(error, "reason", "") or getattr(error, "code", "") or type(error).__name__
        raise ExtrasError(f"could not download {path.name} ({reason})") from None
    os.replace(part, path)


def copy_talk(target: Path, repo: dict, http) -> int:
    """Issues and pull requests, and what was said under them. How many there are."""
    key = f"{repo['owner']}/{repo['name']}"
    # GitHub lists pull requests among the issues; with issues switched off, they are asked for alone
    listed = "issues" if repo.get("about", {}).get("has_issues", True) else "pulls"
    threads = _pages(http, f"/repos/{key}/{listed}?state=all")
    _write_json(target / "issues.json", threads)
    _write_json(target / "comments.json", _pages(http, f"/repos/{key}/issues/comments", gone_is_empty=True))
    _write_json(target / "review-comments.json", _pages(http, f"/repos/{key}/pulls/comments", gone_is_empty=True))
    return len(threads)


def copy_releases(target: Path, key: str, http, token: str, fetch=None) -> tuple[int, int, int, list[str]]:
    """Releases and their files. (releases, files held, bytes held, files that would not come)."""
    fetch = download if fetch is None else fetch
    releases = _pages(http, f"/repos/{key}/releases")
    _write_json(target / "releases.json", releases)
    files = size = 0
    missing: list[str] = []
    for release in releases:
        where = target / "releases" / safe_name(release.get("tag_name") or release.get("id"))
        for asset in release.get("assets") or []:
            path = where / safe_name(asset["name"])
            wanted = int(asset.get("size") or 0)
            if not (path.is_file() and path.stat().st_size == wanted):
                try:
                    fetch(asset["url"], path, token)
                    if path.stat().st_size != wanted:  # cut short on the way: a second try mostly brings it whole
                        fetch(asset["url"], path, token)
                except ExtrasError as error:
                    missing.append(str(error))
                    continue
                if path.stat().st_size != wanted:
                    path.unlink(missing_ok=True)
                    missing.append(f"{path.name} came back the wrong size")
                    continue
            files += 1
            size += wanted
    return len(releases), files, size, missing


def copy_actions(target: Path, key: str, http) -> int:
    """Actions variables with their values, and the names of the secrets. How many secrets are named."""
    kept: dict = {}
    for kind in ("variables", "secrets"):
        try:
            status, data = http("GET", f"/repos/{key}/actions/{kind}?per_page=100")
        except publish.PublishError as error:
            raise ExtrasError(str(error)) from None
        if status in (403, 404):  # Actions switched off, or a sign-in that may not read them
            kept[kind] = []
            continue
        if status >= 300 or not isinstance(data, dict):
            raise ExtrasError(f"GitHub answered {status} for the Actions {kind}")
        rows = data.get(kind) or []
        kept[kind] = [{"name": row.get("name"), "value": row.get("value")} for row in rows] if kind == "variables" else [row.get("name") for row in rows]
    kept["note"] = "GitHub never gives the value of a secret back. These are the names to set again on another host."
    _write_json(target / "actions.json", kept)
    return len(kept["secrets"])


def copy_wiki(target: Path, repo: dict, token: str, run) -> bool:
    """A mirror of the wiki, when the repo has one. A repo with the wiki switched on and no page in it
    has nothing to clone, which is not a failure."""
    url = repo["url"][:-4] if repo["url"].endswith(".git") else repo["url"]
    wiki = target / "wiki.git"
    if wiki.is_dir():
        code, output = run(["--git-dir", str(wiki), "fetch", "--prune", "--quiet", "origin"], token)
        if code != 0:
            raise ExtrasError("the wiki could not be fetched")
        return True
    part = target / "wiki.git.part"
    shutil.rmtree(part, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    code, output = run(["clone", "--mirror", "--quiet", url + ".wiki.git", str(part)], token)
    if code != 0:
        shutil.rmtree(part, ignore_errors=True)
        return False
    os.replace(part, wiki)
    return True


def copy_lfs(mirror: Path, token: str, run) -> bool:
    """Files kept in Git LFS, fetched into the mirror. False when the repo keeps none there."""
    code, output = run(["--git-dir", str(mirror), "grep", "-l", "filter=lfs", "HEAD", "--", ":(glob)**/.gitattributes"], token)
    if code != 0:
        return False
    code, output = run(["--git-dir", str(mirror), "lfs", "fetch", "--all", "origin"], token)
    if code != 0:
        raise ExtrasError("its Git LFS files could not be fetched (is git-lfs installed?)")
    return True


def copy_extras(folder: Path, repo: dict, facts: dict | None, known: dict, http, token: str, run, now: dt.datetime, fetch=None) -> dict:
    """Bring one repo's extras up to date. Returns what is held now; a part that failed is named in
    "error" and tried again by the next run, and the parts that worked are kept."""
    key = f"{repo['owner']}/{repo['name']}"
    target = extras_folder(folder, key)
    mirror = folder / repo["owner"] / (repo["name"] + ".git")
    held = {name: known.get(name, 0) for name in ("talk", "releases", "files", "bytes", "secrets")}
    held.update(wiki=bool(known.get("wiki")), lfs=bool(known.get("lfs")))
    problems: list[str] = []
    _write_json(target / "repo.json", {"name": key, **repo.get("about", {})})

    def part(work) -> None:
        try:
            work()
        except ExtrasError as error:
            problems.append(str(error))

    def talk() -> None:
        held["talk"] = copy_talk(target, repo, http)

    def releases() -> None:
        held["releases"], held["files"], held["bytes"], missing = copy_releases(target, key, http, token, fetch)
        problems.extend(missing[:3])
        if len(missing) > 3:
            problems.append(f"and {len(missing) - 3} more release files")

    def actions() -> None:
        held["secrets"] = copy_actions(target, key, http)

    def wiki() -> None:
        held["wiki"] = copy_wiki(target, repo, token, run)

    def lfs() -> None:
        held["lfs"] = copy_lfs(mirror, token, run)

    if facts is None or facts["talk"] or held["talk"]:
        part(talk)
    if facts is None or facts["releases"] or held["releases"]:
        part(releases)
    code, listed = run(["--git-dir", str(mirror), "ls-tree", "-d", "--name-only", "HEAD", ".github/workflows"], token)
    if code == 0 and ".github/workflows" in listed:
        part(actions)  # a repo with no workflow has no use for Actions secrets
    if repo.get("about", {}).get("has_wiki") or held["wiki"]:
        part(wiki)
    part(lfs)
    held.update(stamp=stamp_of(repo, facts), at=now.strftime("%Y-%m-%dT%H:%M"))
    if problems:
        held["error"] = "; ".join(problems)[:400]
    return held

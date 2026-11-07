"""Publish the public part of a private brain (or Obsidian vault) to a public GitHub brain repo.

Only notes whose frontmatter says ``publish: true`` (and not ``private: true``) ever leave the
source folder. They are copied to ``brain/`` in the target repo in one commit, and the repo gets
the ``cairn-brain`` topic so People and Proof can find it. A manifest
(``brain/.cairn-published.json``) remembers what was published, so a note you un-publish is
removed again, and files you added to the public repo by hand are never touched.

Links to notes that stay private are turned into plain text, so the public brain has no dangling
links and no private file names in link targets. The format is described in PROOF.md.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import core

API = "https://api.github.com"
BRAIN_TOPIC = "cairn-brain"
ROOT = "brain/"
MANIFEST = ROOT + ".cairn-published.json"
SKIP_DIRS = {".git", ".obsidian", ".trash", ".cairn", "node_modules"}
_WIKI_RE = re.compile(r"(!?)\[\[([^\]\n]+)\]\]")


class PublishError(RuntimeError):
    pass


def is_public(meta: dict) -> bool:
    """Same rule as isPublic() in docs/proof.js."""

    def yes(value) -> bool:
        return value is True or str(value).strip().lower() == "true"

    return yes(meta.get("publish")) and not yes(meta.get("private"))


def select_public(source: Path) -> dict[str, str]:
    """Every Markdown note under `source` that asks to be published: {relative path: text}."""
    chosen: dict[str, str] = {}
    for path in sorted(source.rglob("*.md")):
        relative = path.relative_to(source)
        if any(part in SKIP_DIRS or part.startswith(".") for part in relative.parts[:-1]):
            continue
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        if is_public(core.parse_note(text)["meta"]):
            chosen[relative.as_posix()] = text
    return chosen


def unlink_private(files: dict[str, str]) -> dict[str, str]:
    """Turn [[links]] to notes that aren't published into plain text; drop embeds of unpublished files."""
    notes = [core.note_from_file(path, text) for path, text in files.items()]
    index = core.link_index(notes)
    result: dict[str, str] = {}
    for path, text in files.items():

        def replace(match: re.Match) -> str:
            embed, inner = match.group(1), match.group(2)
            target, _, alias = inner.partition("|")
            if core.resolve_target(index, target.strip(), path):
                return match.group(0)
            if embed:
                return ""
            label = alias.strip() or target.split("#")[0].strip().split("/")[-1]
            return label.removesuffix(".md")

        result[path] = _WIKI_RE.sub(replace, text)
    return result


def git_blob_sha(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def plan(public: dict[str, str], remote_shas: dict[str, str], previous: list[str]) -> dict:
    """What to change in the public repo. `public` paths are relative to the brain folder;
    `remote_shas` maps repo paths to blob shas; `previous` lists repo paths we published last time."""
    writes = {}
    for path, text in public.items():
        repo_path = ROOT + path
        if remote_shas.get(repo_path) != git_blob_sha(text):
            writes[repo_path] = text
    wanted = {ROOT + path for path in public}
    deletes = sorted(path for path in previous if path not in wanted and path in remote_shas)
    return {"write": writes, "delete": deletes, "manifest": sorted(wanted)}


def manifest_text(paths: list[str]) -> str:
    return json.dumps({"publishedBy": "cairn", "files": paths}, indent=2) + "\n"


# ------------------------------------------------------------ HTTP (urllib, with a Windows fallback)

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$headers = @{ Authorization = "Bearer $env:CAIRN_GH_TOKEN"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'cairn' }
$params = @{ Uri = $env:CAIRN_URL; Method = $env:CAIRN_METHOD; Headers = $headers; UseBasicParsing = $true }
if ($env:CAIRN_BODY_FILE) { $params.Body = [IO.File]::ReadAllBytes($env:CAIRN_BODY_FILE); $params.ContentType = 'application/json' }
try {
  $response = Invoke-WebRequest @params
  $status = [int]$response.StatusCode
  $content = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
} catch {
  $failed = $_.Exception.Response
  if (-not $failed) { throw }
  $status = [int]$failed.StatusCode
  $content = (New-Object IO.StreamReader($failed.GetResponseStream())).ReadToEnd()
}
$out = @{ status = $status; body = $content } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($env:CAIRN_OUT_FILE, $out, (New-Object Text.UTF8Encoding($false)))
"""


def _powershell_request(method: str, url: str, token: str, payload: bytes | None) -> tuple[int, bytes]:
    """Same request through Windows' own TLS stack. Some corporate networks reset Python's TLS
    handshakes to GitHub but let Windows (SChannel) through."""
    with tempfile.TemporaryDirectory() as folder:
        out_file = Path(folder) / "out.json"
        env = dict(os.environ, CAIRN_GH_TOKEN=token, CAIRN_URL=url, CAIRN_METHOD=method, CAIRN_OUT_FILE=str(out_file))
        if payload is not None:
            body_file = Path(folder) / "body.json"
            body_file.write_bytes(payload)
            env["CAIRN_BODY_FILE"] = str(body_file)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            env=env, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0 or not out_file.exists():
            raise PublishError(f"Can't reach GitHub: {(result.stderr or result.stdout).strip()[:300]}")
        answer = json.loads(out_file.read_text(encoding="utf-8"))
        return int(answer["status"]), (answer.get("body") or "").encode("utf-8")


def make_http(token: str, use_powershell: bool | None = None):
    """A function (method, path, body) -> (status, json) for the GitHub API."""
    state = {"powershell": bool(use_powershell)}

    def http(method: str, path: str, body: dict | None = None):
        url = path if path.startswith("http") else API + path
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        if not state["powershell"]:
            request = urllib.request.Request(url, data=payload, method=method, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "cairn",
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    status, raw = response.status, response.read()
            except urllib.error.HTTPError as error:
                status, raw = error.code, error.read()
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                if os.name != "nt" or use_powershell is False:
                    raise PublishError("Can't reach GitHub. Check your connection and try again.")
                state["powershell"] = True
        if state["powershell"]:
            status, raw = _powershell_request(method, url, token, payload)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            data = None
        return status, data

    return http


def find_token() -> str:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    raise PublishError("No GitHub token. Set GITHUB_TOKEN, or sign in once with: gh auth login")


# ------------------------------------------------------------ publishing

def _check(status: int, data, what: str) -> None:
    if status >= 300:
        message = (data or {}).get("message", "") if isinstance(data, dict) else ""
        raise PublishError(f"GitHub refused to {what} ({status}{': ' + message if message else ''}).")


def _readme(owner: str) -> str:
    return (
        f"# @{owner}'s public brain\n\n"
        "Published from a private [Cairn](https://github.com/juw12/cairn) brain. Only notes marked "
        "`publish: true` appear here; everything else stays private.\n\n"
        f"Open it in Cairn: https://juw12.github.io/cairn/?u={owner}\n"
    )


def publish_to_github(source: Path, repo: str, http, today: str, dry_run: bool = False) -> dict:
    """Publish `source` to the GitHub repo `owner/name` (created if missing) in one commit."""
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise PublishError("Use --to OWNER/REPO, for example --to juw12/brain")
    public = unlink_private(select_public(source))
    status, info = http("GET", f"/repos/{owner}/{name}")
    created = False
    if status == 404:
        if dry_run:
            return {"repo": repo, "created": True, "write": sorted(ROOT + p for p in public), "delete": [], "commit": None}
        status, info = http("POST", "/user/repos", {
            "name": name,
            "description": "My public brain: work I can show, published from a private Cairn brain.",
            "private": False,
            "auto_init": True,
            "has_wiki": False,
        })
        _check(status, info, f"create {repo}")
        created = True
    else:
        _check(status, info, f"read {repo}")
    if info.get("private"):
        raise PublishError(f"{repo} is private. Publishing only goes to a public repo.")
    branch = info.get("default_branch") or "main"
    topics = list(info.get("topics") or [])
    if BRAIN_TOPIC not in topics and not dry_run:
        status, data = http("PUT", f"/repos/{owner}/{name}/topics", {"names": topics + [BRAIN_TOPIC]})
        _check(status, data, "add the cairn-brain topic")

    status, ref = http("GET", f"/repos/{owner}/{name}/git/ref/heads/{branch}")
    _check(status, ref, f"read branch {branch}")
    head = ref["object"]["sha"]
    status, tree = http("GET", f"/repos/{owner}/{name}/git/trees/{head}?recursive=1")
    _check(status, tree, "read the repo's files")
    remote = {item["path"]: item["sha"] for item in tree.get("tree", []) if item.get("type") == "blob"}
    previous: list[str] = []
    if MANIFEST in remote:
        status, blob = http("GET", f"/repos/{owner}/{name}/git/blobs/{remote[MANIFEST]}")
        if status < 300:
            try:
                previous = json.loads(base64.b64decode(blob["content"]).decode("utf-8")).get("files", [])
            except (ValueError, KeyError):
                previous = []
    changes = plan(public, remote, previous)
    manifest = manifest_text(changes["manifest"])
    if remote.get(MANIFEST) != git_blob_sha(manifest):
        changes["write"][MANIFEST] = manifest
    if created or "README.md" not in remote:
        changes["write"]["README.md"] = _readme(owner)
    report = {"repo": repo, "created": created, "write": sorted(changes["write"]), "delete": changes["delete"], "commit": None}
    if dry_run or not (changes["write"] or changes["delete"]):
        return report

    entries = [{"path": path, "mode": "100644", "type": "blob", "content": text} for path, text in changes["write"].items()]
    entries += [{"path": path, "mode": "100644", "type": "blob", "sha": None} for path in changes["delete"]]
    status, new_tree = http("POST", f"/repos/{owner}/{name}/git/trees", {"base_tree": tree["sha"], "tree": entries})
    _check(status, new_tree, "write the files")
    count = len(changes["manifest"])
    message = f"cairn: publish {count} public note{'s' if count != 1 else ''} ({today})"
    status, commit = http("POST", f"/repos/{owner}/{name}/git/commits", {"message": message, "tree": new_tree["sha"], "parents": [head]})
    _check(status, commit, "commit")
    status, data = http("PATCH", f"/repos/{owner}/{name}/git/refs/heads/{branch}", {"sha": commit["sha"]})
    _check(status, data, f"move {branch}")
    report["commit"] = commit["sha"]
    return report


def publish_to_folder(source: Path, destination: Path) -> dict:
    """Publish into a local clone of the public repo (you commit and push it yourself)."""
    public = unlink_private(select_public(source))
    manifest_path = destination / MANIFEST
    previous: list[str] = []
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])
        except ValueError:
            previous = []
    remote = {}
    for path in previous + [ROOT + p for p in public]:
        target = destination / path
        if target.exists():
            remote[path] = git_blob_sha(target.read_text(encoding="utf-8"))
    changes = plan(public, remote, previous)
    for path, text in changes["write"].items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    for path in changes["delete"]:
        (destination / path).unlink(missing_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest_text(changes["manifest"]), encoding="utf-8", newline="\n")
    return {"repo": str(destination), "created": False, "write": sorted(changes["write"]), "delete": changes["delete"], "commit": None}

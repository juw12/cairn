"""Publish the public part of a private brain (or Obsidian vault) to a public GitHub brain repo.

Only notes whose frontmatter says ``publish: true`` (and not ``private: true``) ever leave the
source folder. They are copied to ``brain/`` in the target repo in one commit, and the repo gets
the ``cairn-brain`` topic so People and Proof can find it. A manifest
(``brain/.cairn-published.json``) remembers what was published, so a note you un-publish is
removed again, and files you added to the public repo by hand are never touched.

Links to notes that stay private are turned into plain text, so the public brain has no dangling
links and no private file names in link targets. The format is described in PROOF.md.

The same commit carries a web page of those notes in ``docs/`` (site.py: one page per note, a search
box, a sitemap), and GitHub Pages is switched on for that folder. So the public brain can be read and
searched by anyone at ``https://OWNER.github.io/REPO/`` and found through Google or Bing, with no
sign-in and no GitHub API limit. A repo that already keeps something else in ``docs/`` is left alone.
After a commit the report names the pages that changed, so announce.py can tell the search engines.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import core

API = "https://api.github.com"
BRAIN_TOPIC = "cairn-brain"
ROOT = "brain/"
MANIFEST = ROOT + ".cairn-published.json"
SITE_ROOT = "docs/"  # the public brain as a web page; GitHub Pages can serve this folder
SITE_MANIFEST = SITE_ROOT + ".cairn-site.json"
PAGES_HINT = "To put it online, turn on GitHub Pages once: the repo's Settings > Pages > Branch {branch}, folder /docs."
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
function Send($direct) {
  if ($direct) { [Net.WebRequest]::DefaultWebProxy = $null }
  elseif ([Net.WebRequest]::DefaultWebProxy) { [Net.WebRequest]::DefaultWebProxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials }
  try {
    $response = Invoke-WebRequest @params
    return @([int]$response.StatusCode, [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()))
  } catch {
    $failed = $_.Exception.Response
    if (-not $failed) { return @(0, $_.Exception.Message) }
    return @([int]$failed.StatusCode, (New-Object IO.StreamReader($failed.GetResponseStream())).ReadToEnd())
  }
}
# The system proxy first (with your Windows sign-in), then straight to GitHub if the proxy is down or refuses.
$status, $content = Send $false
if ($status -eq 0 -or $status -eq 407) { $status, $content = Send $true }
if ($status -eq 0) { throw $content }
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


MAC_CERTS = "/etc/ssl/cert.pem"  # the roots macOS itself trusts


def ssl_context() -> ssl.SSLContext | None:
    """None for Python's own certificates, which is right nearly everywhere. The Python from
    python.org on a Mac has none until its "Install Certificates" script is run, and every call
    then fails as if GitHub could not be reached; there, check against macOS's own bundle."""
    if sys.platform != "darwin":
        return None
    paths = ssl.get_default_verify_paths()
    try:
        has_folder = bool(paths.capath) and bool(os.listdir(paths.capath))
    except OSError:
        has_folder = False
    if paths.cafile or has_folder or not os.path.isfile(MAC_CERTS):
        return None
    return ssl.create_default_context(cafile=MAC_CERTS)


def make_http(token: str, use_powershell: bool | None = None):
    """A function (method, path, body) -> (status, json) for the GitHub API."""
    state = {"powershell": bool(use_powershell)}
    context = ssl_context()

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
                with urllib.request.urlopen(request, timeout=30, context=context) as response:
                    status, raw = response.status, response.read()
            except urllib.error.HTTPError as error:
                status, raw = error.code, error.read()
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                status, raw = 0, b""
            # 0 = no connection; 407 = a proxy wants credentials Python doesn't have. Windows' own
            # stack usually gets through both, so retry (and keep using) it there.
            if status in (0, 407):
                if os.name != "nt" or use_powershell is False:
                    raise PublishError("Can't reach GitHub. Check your connection or proxy and try again.")
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


def _readme(owner: str, web: str = "") -> str:
    return (
        f"# @{owner}'s public brain\n\n"
        "Published from a private [Cairn](https://github.com/juw12/cairn) brain. Only notes marked "
        "`publish: true` appear here; everything else stays private.\n\n"
        + (f"Read and search it on the web: {web}\n\n" if web else "")
        + f"Open it in Cairn: https://juw12.github.io/cairn/?u={owner}\n"
    )


def pages_address(owner: str, name: str) -> str:
    """Where GitHub Pages serves a repo that has no domain of its own."""
    host = f"{owner.lower()}.github.io"
    return f"https://{host}/" if name.lower() == host else f"https://{host}/{name}/"


def _pages(http, owner: str, name: str, branch: str) -> dict:
    """What GitHub Pages does with this repo. `url` is where docs/ is served, or would be once Pages is on.

    state: "docs" (Pages serves docs/), "root" (Pages serves the whole branch, so docs/ is a sub-folder),
    "off" (no Pages yet) or "other" (built by a workflow or from another branch: docs/ is not what it shows).
    """
    default = pages_address(owner, name)
    status, data = http("GET", f"/repos/{owner}/{name}/pages")
    if status != 200 or not isinstance(data, dict):
        return {"url": default, "state": "off"}
    address = (data.get("html_url") or default).rstrip("/") + "/"
    served = data.get("source") or {}
    if data.get("build_type") == "workflow" or served.get("branch") != branch:
        return {"url": address + SITE_ROOT, "state": "other"}
    if served.get("path") == "/docs":
        return {"url": address, "state": "docs"}
    return {"url": address + SITE_ROOT, "state": "root"}


def _site_title(http, owner: str) -> str:
    """The name on the owner's GitHub profile, or @owner."""
    status, user = http("GET", f"/users/{owner}")
    name = user.get("name") if status == 200 and isinstance(user, dict) else ""
    return name.strip() if isinstance(name, str) and name.strip() else f"@{owner}"


def _blob_json(http, owner: str, name: str, sha: str) -> dict:
    status, blob = http("GET", f"/repos/{owner}/{name}/git/blobs/{sha}")
    if status >= 300:
        return {}
    try:
        data = json.loads(base64.b64decode(blob["content"]).decode("utf-8"))
    except (ValueError, KeyError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _plan_site(public: dict[str, str], remote: dict[str, str], http, owner: str, name: str, branch: str) -> dict:
    """The web page of the public notes as changes under docs/: {"write", "delete", "url", "state"}.

    docs/ is only ever written when it is empty or already ours (it has our manifest), so a repo whose
    docs/ holds something else, a web app for instance, keeps it.
    """
    from . import site

    if SITE_MANIFEST not in remote and any(path.startswith(SITE_ROOT) for path in remote):
        return {"write": {}, "delete": [], "url": "", "state": "taken"}
    pages = _pages(http, owner, name, branch)
    wanted = {SITE_ROOT + path: text for path, text in site.files(public, _site_title(http, owner), pages["url"]).items()}
    wanted[SITE_MANIFEST] = site.manifest_text(path.removeprefix(SITE_ROOT) for path in wanted)
    previous = _blob_json(http, owner, name, remote[SITE_MANIFEST]).get("files", []) if SITE_MANIFEST in remote else []
    writes = {path: text for path, text in wanted.items() if remote.get(path) != git_blob_sha(text)}
    deletes = sorted(
        SITE_ROOT + path for path in previous
        if isinstance(path, str) and SITE_ROOT + path not in wanted and SITE_ROOT + path in remote
    )
    return {"write": writes, "delete": deletes, "url": pages["url"], "state": pages["state"]}


def _open_site(http, owner: str, name: str, branch: str, web: dict, info: dict) -> dict:
    """Make sure the web page is being served and say where: {"url", "live", "note"}."""
    repo = f"{owner}/{name}"
    if web["state"] == "taken":
        return {"url": "", "live": False, "note": f"No web page: {repo} keeps other files in docs/, and Cairn leaves them alone."}
    if web["state"] in ("docs", "root"):
        return {"url": web["url"], "live": True, "note": ""}
    if web["state"] == "other":
        return {"url": web["url"], "live": False, "note": f"The web page is in docs/, but GitHub Pages in {repo} is set up to show something else."}
    # Pages is off. Only the owner (or an admin) may switch it on; a collaborator's sign-in is refused.
    status, _ = http("POST", f"/repos/{owner}/{name}/pages", {"source": {"branch": branch, "path": "/docs"}})
    if status >= 300:
        return {"url": web["url"], "live": False, "note": "The web page is in docs/. " + PAGES_HINT.format(branch=branch)}
    if not info.get("homepage"):
        http("PATCH", f"/repos/{owner}/{name}", {"homepage": web["url"]})  # the link beside the repo's description
    return {"url": web["url"], "live": True, "note": "GitHub Pages was switched on; the first build takes about a minute."}


def _changes_online(web: dict) -> dict:
    """What announce.py needs after a commit: the addresses of the pages that changed, the feed when it
    changed, and one changed file to watch for (GitHub Pages puts a commit online about a minute later)."""
    from . import site

    def address(path: str) -> str:
        relative = path.removeprefix(SITE_ROOT)
        return web["url"] + ("" if relative == "index.html" else urllib.parse.quote(relative))

    pages = sorted(path for path in web["write"] if path.endswith(".html"))
    feed = SITE_ROOT + site.FEED
    watched = feed if feed in web["write"] else pages[0] if pages else ""
    return {
        "changed": [address(path) for path in pages],
        "feed": address(feed) if feed in web["write"] else "",
        "check": {"url": address(watched), "sha": git_blob_sha(web["write"][watched])} if watched else None,
    }


def publish_to_github(source: Path, repo: str, http, today: str, dry_run: bool = False, site: bool = True) -> dict:
    """Publish `source` to the GitHub repo `owner/name` (created if missing) in one commit.

    With `site` (the default) the commit also holds the notes as a web page in docs/, and GitHub Pages is
    switched on for it when this sign-in may do that. report["site"] says where the page is and if it is live.
    """
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
    web = _plan_site(public, remote, http, owner, name, branch) if site else None
    link = ""
    if web:
        changes["write"].update(web["write"])
        changes["delete"] = sorted(changes["delete"] + web["delete"])
        # The README names the page when it is online, or about to be: this sign-in may switch Pages on.
        may_open = created or bool((info.get("permissions") or {}).get("admin"))
        link = web["url"] if web["state"] in ("docs", "root") or (web["state"] == "off" and may_open) else ""
    if created or "README.md" not in remote:
        changes["write"]["README.md"] = _readme(owner, link)
    elif link and remote["README.md"] == git_blob_sha(_readme(owner)):
        changes["write"]["README.md"] = _readme(owner, link)  # still the README Cairn wrote: add the link
    report = {"repo": repo, "created": created, "write": sorted(changes["write"]), "delete": changes["delete"], "commit": None}
    if dry_run:
        if web:
            report["site"] = {"url": web["url"], "live": web["state"] in ("docs", "root"), "note": ""}
        return report
    if not (changes["write"] or changes["delete"]):
        if web:
            report["site"] = _open_site(http, owner, name, branch, web, info)
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
    if web:
        report["site"] = _open_site(http, owner, name, branch, web, info)
        if report["site"]["live"]:
            report["site"].update(_changes_online(web))
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

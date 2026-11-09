"""The brain on disk: safe file access, the code inbox scan and git."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import core

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__",
    "dist", "build", "target", ".next", ".idea", ".vscode", ".obsidian", "vendor",
    ".mypy_cache", ".pytest_cache", ".tox", "coverage", "site-packages",
}
CODE_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".m", ".scala", ".sh",
    ".ps1", ".bat", ".lua", ".sql", ".html", ".css", ".scss", ".vue", ".svelte", ".yml",
    ".yaml", ".toml", ".ini", ".f90", ".f", ".r", ".dart", ".ex", ".exs", ".hs", ".clj",
}
MAX_FILE_BYTES = 1_000_000
MAX_ATTACHMENT_BYTES = 50_000_000
# Attachments notes can embed (![[photo.png]]); served by /api/raw with their type.
ATTACHMENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".avif": "image/avif",
    ".pdf": "application/pdf", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".csv": "text/csv", ".txt": "text/plain",
}
MAX_TODOS = 500
# Files the app reads and writes: Markdown notes, and Obsidian canvases (JSON Canvas, jsoncanvas.org).
TEXT_SUFFIXES = (".md", ".canvas")
LOCAL_WRITE_TIMEOUT = 120  # add/commit of a big vault on a machine with busy antivirus
NETWORK_TIMEOUT = 180  # one clone, or a fetch/push that may wait for a GitHub sign-in window
ROUTE_TIMEOUT = 75  # one fetch/push attempt over one network route before trying the next
# No console window flashing up for every git call when Cairn runs as a windowed app on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Signs that git couldn't get through (as opposed to "GitHub said no"): worth trying another route.
NETWORK_ERRORS = (
    "unable to access", "could not resolve", "timed out", "connection was reset", "connection reset",
    "recv failure", "failed to connect", "proxy", "407", "early eof", "rpc failed", "unexpected disconnect",
    "the remote end hung up", "schannel", "ssl", "operation too slow",
)
PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")

BRAIN_README = """# brain/

This folder is the project's second brain: ideas, tasks, decisions, notes and a journal,
kept as plain Markdown next to the code so they are versioned, reviewed and searchable
like everything else.

| Folder | What goes in it |
|---|---|
| `ideas/` | Half-formed thoughts. Status: raw, exploring, ready, parked |
| `tasks/` | Things to do. Status: todo, doing, done |
| `decisions/` | Lightweight decision records (context, options, decision, consequences) |
| `notes/` | Reference material, research, architecture |
| `journal/` | One page per day |
| `CONTEXT.md` | A generated brief for teammates and AI assistants |

Every file starts with a small header (`title`, `type`, `status`, `tags`). Link notes
with `[[file-name]]`. You can edit these files in any editor; [Cairn](https://github.com/juw12/cairn)
just makes capturing, brainstorming and moving work along faster.
"""


class PathError(ValueError):
    """A requested path is outside the brain folder or not a Markdown file."""


class ConflictError(RuntimeError):
    """The file changed on disk since the client last read it."""

    def __init__(self, current_text: str | None, current_mtime: int):
        super().__init__("file changed on disk")
        self.current_text = current_text
        self.current_mtime = current_mtime


class SyncError(RuntimeError):
    """Sync stopped safely (offline, sign-in needed, or edits that clash with the remote)."""


def find_brain_dir(root: Path, explicit: str | None = None) -> Path:
    if explicit:
        path = Path(explicit)
        return (path if path.is_absolute() else root / path).resolve()
    for name in ("brain", ".brain"):
        candidate = root / name
        if candidate.is_dir():
            return candidate.resolve()
    if is_vault(root):
        return root.resolve()
    return (root / "brain").resolve()


def is_vault(root: Path) -> bool:
    """An Obsidian vault is opened as it is: the whole folder is the brain."""
    return (root / ".obsidian").is_dir()


def _read_text(path: Path) -> str:
    # Keep line endings exactly as they are on disk (CRLF notes from Windows editors stay CRLF).
    return path.read_bytes().decode("utf-8")


def _mtime_ms(path: Path) -> int:
    return path.stat().st_mtime_ns // 1_000_000


class Brain:
    def __init__(self, root: str | Path = ".", brain_dir: str | None = None):
        self.root = Path(root).resolve()
        self.dir = find_brain_dir(self.root, brain_dir)

    # ------------------------------------------------------------ setup

    @property
    def project_name(self) -> str:
        return self.root.name or "project"

    def init(self) -> list[str]:
        """Create the folder layout. Never overwrites existing files."""
        created = []
        for folder in core.FOLDERS.values():
            target = self.dir / folder
            if not target.exists():
                target.mkdir(parents=True)
                created.append(f"{folder}/")
        readme = self.dir / "README.md"
        if not readme.exists() and not is_vault(self.dir):
            readme.write_text(BRAIN_README, encoding="utf-8")
            created.append("README.md")
        return created

    # ------------------------------------------------------------ paths

    def resolve(self, relative: str) -> Path:
        if not relative or "\x00" in relative:
            raise PathError("empty path")
        relative = relative.replace("\\", "/")
        if relative.startswith("/") or (len(relative) > 1 and relative[1] == ":"):
            raise PathError("absolute paths are not allowed")
        if not relative.endswith(TEXT_SUFFIXES):
            raise PathError("only .md and .canvas files can be read or written")
        target = (self.dir / relative).resolve()
        if target != self.dir and self.dir not in target.parents:
            raise PathError("path escapes the brain folder")
        return target

    def relative(self, path: Path) -> str:
        return path.relative_to(self.dir).as_posix()

    def _inside(self, path: Path) -> bool:
        return path == self.dir or self.dir in path.parents

    def find_attachment(self, target: str, from_note: str = "") -> Path | None:
        """An image or other attachment a note embeds, found the way Obsidian finds it.

        Tries the path next to the note, then from the brain root, then any file with that name
        (shallowest first). Only files inside the brain with an allowed extension are returned.
        """
        name = (target or "").strip().replace("\\", "/")
        if not name or "\x00" in name or name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            return None
        while name.startswith("./"):
            name = name[2:]
        if Path(name).suffix.lower() not in ATTACHMENT_TYPES:
            return None
        folder = from_note.replace("\\", "/").rsplit("/", 1)[0] if "/" in (from_note or "") else ""
        for candidate in ([f"{folder}/{name}"] if folder else []) + [name]:
            path = (self.dir / candidate).resolve()
            if self._inside(path) and path.is_file():
                return path
        wanted = name.rsplit("/", 1)[-1].lower()
        best = None
        for current, dirs, names in os.walk(self.dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
            depth = len(Path(current).relative_to(self.dir).parts)
            if best is not None and depth >= best[0]:
                continue
            for entry in names:
                if entry.lower() == wanted:
                    best = (depth, Path(current) / entry)
                    break
        if best is None:
            return None
        path = best[1].resolve()
        return path if self._inside(path) else None

    # ------------------------------------------------------------ files

    def files(self) -> list[dict]:
        """Every note (.md) and canvas (.canvas) in the brain as {path, text, mtime}, sorted by path."""
        if not self.dir.exists():
            return []
        out = []
        # Re-reading every file on each call is what makes a big vault slow (the MCP server and the
        # local API call this per request), so unchanged files come from a cache keyed on mtime + size.
        previous = getattr(self, "_text_cache", {})
        cache = {}
        for current, dirs, names in os.walk(self.dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
            for name in sorted(names):
                if not name.endswith(TEXT_SUFFIXES):
                    continue
                path = Path(current) / name
                try:
                    stat = path.stat()
                    stamp = (stat.st_mtime_ns, stat.st_size)
                    hit = previous.get(str(path))
                    text = hit[1] if hit and hit[0] == stamp else _read_text(path)
                except (OSError, UnicodeDecodeError):
                    continue
                cache[str(path)] = (stamp, text)
                out.append({"path": self.relative(path), "text": text, "mtime": stamp[0] // 1_000_000})
        self._text_cache = cache
        return sorted(out, key=lambda item: item["path"])

    def notes(self) -> list[dict]:
        return [
            core.note_from_file(f["path"], f["text"], f["mtime"])
            for f in self.files()
            if f["path"].endswith(".md") and f["path"] not in core.SPECIAL_FILES
        ]

    def read(self, relative: str) -> dict:
        path = self.resolve(relative)
        return {"path": relative, "text": _read_text(path), "mtime": _mtime_ms(path)}

    def write(self, relative: str, text: str, expected_mtime: int | None = None) -> int:
        """Write a note. `expected_mtime=None` means "create": it fails if the file exists."""
        path = self.resolve(relative)
        if path.exists():
            current = _mtime_ms(path)
            if expected_mtime is None or int(expected_mtime) != current:
                raise ConflictError(_read_text(path), current)
        elif expected_mtime is not None:
            raise ConflictError(None, 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".cairn-tmp")
        # Written byte-for-byte: the client already chose the note's line endings.
        temp.write_bytes(text.encode("utf-8"))
        os.replace(temp, path)
        return _mtime_ms(path)

    def delete(self, relative: str) -> None:
        path = self.resolve(relative)
        if path.exists():
            path.unlink()

    def create(self, note_type: str, title: str, tags: list[str] | None = None,
               body: str = "", today: str = "", source: str = "") -> dict:
        existing = [f["path"] for f in self.files()]
        relative = core.note_path(note_type, title, existing, today)
        meta = {"title": title or today, "type": note_type}
        status = core.default_status(note_type)
        if status:
            meta["status"] = status
        meta["tags"] = list(tags or [])
        meta["created"] = today
        if source:
            meta["source"] = source
        text = core.serialize_note(meta, body if body else core.TEMPLATES.get(note_type, ""))
        mtime = self.write(relative, text, None)
        return {"path": relative, "text": text, "mtime": mtime}

    # ------------------------------------------------------------ journal

    def log(self, text: str, day: str, time: str) -> dict:
        """Append "- HH:MM text" to journal/<day>.md under "## Log", creating the page if needed."""
        relative = f"{core.FOLDERS['journal']}/{day}.md"
        if self.resolve(relative).exists():
            current = self.read(relative)
            body = core.append_log(core.parse_note(current["text"])["body"], time, text)
            updated = core.update_note_text(current["text"], None, body)
            mtime = self.write(relative, updated, current["mtime"])
            return {"path": relative, "text": updated, "mtime": mtime}
        return self.create("journal", day, [], core.append_log("", time, text), day)

    # ------------------------------------------------------------ code inbox

    def scan_code(self, limit: int = MAX_TODOS) -> list[dict]:
        items: list[dict] = []
        for current, dirs, names in os.walk(self.root):
            current_path = Path(current)
            dirs[:] = sorted(
                d for d in dirs
                if d not in SKIP_DIRS and not d.startswith(".") and (current_path / d).resolve() != self.dir
            )
            for name in sorted(names):
                path = current_path / name
                if path.suffix.lower() not in CODE_EXTENSIONS:
                    continue
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        continue
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                items.extend(core.scan_todos(text, path.relative_to(self.root).as_posix()))
                if len(items) >= limit:
                    return items[:limit]
        return items

    # ------------------------------------------------------------ git

    def _git(self, *args: str, timeout: int = 20) -> subprocess.CompletedProcess:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env, creationflags=NO_WINDOW,
        )

    def _network(self, *args: str) -> subprocess.CompletedProcess:
        """git fetch / pull / push: tries every way out of this network (see git_over_network)."""
        remote = self._remote_name() or "origin"
        url = self._git("remote", "get-url", remote).stdout.strip()
        return git_over_network(list(args), self.root, url)

    def git_available(self) -> bool:
        if not shutil.which("git"):
            return False
        result = self._git("rev-parse", "--is-inside-work-tree")
        return result.returncode == 0 and result.stdout.strip() == "true"

    def git_status(self) -> dict:
        if not self.git_available():
            return {"available": False, "branch": "", "changes": [], "remote": False, "ahead": 0, "behind": 0}
        brain_spec = self._brain_pathspec()
        status = self._git("status", "--porcelain", "-uall", "--", brain_spec)
        branch = self._git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
        if not branch:
            branch = self._git("rev-parse", "--short", "HEAD").stdout.strip() or "detached"
        top = Path(self._git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
        changes = []
        for line in status.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip().strip('"')
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            try:
                path = (top / path).resolve().relative_to(self.dir).as_posix()
            except ValueError:
                pass
            changes.append({"status": line[:2].strip() or "?", "path": path})
        ahead, behind = self._ahead_behind()
        return {
            "available": True,
            "branch": branch,
            "changes": changes,
            "remote": self._remote_name() is not None,
            "ahead": ahead,
            "behind": behind,
        }

    def _remote_name(self) -> str | None:
        remotes = self._git("remote").stdout.split()
        if not remotes:
            return None
        return "origin" if "origin" in remotes else remotes[0]

    def _upstream(self) -> str | None:
        result = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    def _link_upstream(self, remote: str, branch: str, has_commits: bool) -> str | None:
        """First sync on this device: connect the branch to the same branch on the remote."""
        fetch = self._network("fetch", remote)
        if fetch.returncode != 0:
            raise SyncError(_explain_git_failure(fetch, "fetch"))
        remote_ref = f"refs/remotes/{remote}/{branch}"
        if self._git("rev-parse", "--verify", "-q", remote_ref).returncode != 0:
            return None  # nothing there yet: the first push creates it
        if not has_commits:
            pull = self._network("pull", remote, branch)
            if pull.returncode != 0:
                raise SyncError(_explain_git_failure(pull, "pull"))
        self._git("branch", f"--set-upstream-to={remote}/{branch}", branch)
        return self._upstream()

    def _ahead_behind(self) -> tuple[int, int]:
        """Commits not yet pushed / not yet pulled, as of the last fetch (no network)."""
        if not self._upstream():
            return 0, 0
        result = self._git("rev-list", "--left-right", "--count", "HEAD...@{u}")
        try:
            ahead, behind = (int(n) for n in result.stdout.split())
            return ahead, behind
        except ValueError:
            return 0, 0

    def _brain_pathspec(self) -> str:
        try:
            return self.dir.relative_to(self.root).as_posix() or "."
        except ValueError:
            return str(self.dir)

    def git_commit(self, message: str) -> dict:
        if not self.git_available():
            raise RuntimeError("this folder is not a git repository")
        spec = self._brain_pathspec()
        added = self._git("add", "-A", "--", spec, timeout=LOCAL_WRITE_TIMEOUT)
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or "git add failed")
        if not self._git("diff", "--cached", "--name-only", "--", spec).stdout.strip():
            raise RuntimeError("nothing to commit in the brain folder")
        result = self._git("commit", "-m", message or "brain: update notes", "--", spec, timeout=LOCAL_WRITE_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git commit failed")
        sha = self._git("rev-parse", "--short", "HEAD").stdout.strip()
        return {"commit": sha, "message": message}

    def git_sync(self, message: str = "") -> dict:
        """One step to be up to date everywhere: commit brain edits, pull others' edits, push.

        Only the brain folder is committed. Anything else in the working tree is left alone
        (it is stashed around the pull and put back). On a clash the pull is undone and the
        repo is left exactly as it was, with the local commit kept.
        """
        if not self.git_available():
            raise SyncError("This folder is not a git repository yet.")
        committed = None
        pending = self.git_status()["changes"]
        if pending:
            count = len(pending)
            text = message or f"brain: update {count} note{'s' if count != 1 else ''}"
            committed = self.git_commit(text)["commit"]
        remote = self._remote_name()
        if remote is None:
            return {"committed": committed, "pulled": 0, "pushed": 0, "remote": False}
        branch = self._git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
        if not branch:
            raise SyncError("Not on a branch (detached HEAD); switch to a branch to sync.")
        before = self._git("rev-parse", "--verify", "-q", "HEAD").stdout.strip()
        upstream = self._upstream() or self._link_upstream(remote, branch, has_commits=bool(before))
        if upstream:
            # Fetch over whichever network route works, then replay local commits on top (no network).
            fetch = self._network("fetch", remote)
            if fetch.returncode != 0:
                raise SyncError(_explain_git_failure(fetch, "pull"))
            rebase = self._git("rebase", "--autostash", "@{u}", timeout=LOCAL_WRITE_TIMEOUT)
            if rebase.returncode != 0:
                self._git("rebase", "--abort")
                raise SyncError(_explain_git_failure(rebase, "pull"))
        after_pull = self._git("rev-parse", "--verify", "-q", "HEAD").stdout.strip()
        pulled = 0
        if after_pull and before != after_pull:
            span = f"{before}..{after_pull}" if before else after_pull
            pulled = int(self._git("rev-list", "--count", span).stdout.strip() or 0)
        has_commits = bool(self._git("rev-parse", "--verify", "-q", "HEAD").stdout.strip())
        ahead, _ = self._ahead_behind() if upstream else ((1, 0) if has_commits else (0, 0))
        pushed = 0
        if ahead:
            push_args = ["push"] if upstream else ["push", "-u", remote, branch]
            push = self._network(*push_args)
            if push.returncode != 0:
                raise SyncError(_explain_git_failure(push, "push"))
            pushed = ahead
        return {"committed": committed, "pulled": pulled, "pushed": pushed, "remote": True}


def _explain_git_failure(result: subprocess.CompletedProcess, step: str) -> str:
    text = (result.stderr or result.stdout or "").strip()
    lowered = text.lower()
    if "conflict" in lowered or "could not apply" in lowered:
        return ("Your edits and the remote's edits touch the same lines. Nothing was lost: your "
                "changes are committed locally. Resolve it in your git tool, then sync again.")
    if "authentication" in lowered or "terminal prompts disabled" in lowered or "403" in lowered:
        return "GitHub needs you to sign in. Sync again and approve the GitHub sign-in window."
    if is_network_failure(result):
        return "Couldn't reach the remote (offline?). Your edits are safe and will sync next time."
    last_line = text.splitlines()[-1] if text else f"git {step} failed"
    return f"git {step} failed: {last_line}"



# ------------------------------------------------------------ getting through office proxies

_last_route: dict[str, str] = {}  # remote host -> name of the route that worked last time


def _windows_system_proxy() -> list[str]:
    """Proxies from Windows' Internet settings (what Edge uses), e.g. ['proxy.corp:80']."""
    if os.name != "nt":
        return []
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
    except OSError:
        return []
    if not enabled or not server:
        return []
    found = []
    for part in str(server).split(";"):  # "proxy:80" or "http=proxy:80;https=proxy:443"
        part = part.strip()
        if "=" in part:
            scheme, _, part = part.partition("=")
            if scheme.strip().lower() not in ("http", "https"):
                continue
        if part:
            found.append(part)
    return found


def _proxy_url(proxy: str) -> str:
    """http://:@host:port: the empty user:password lets git sign in to the proxy as the Windows user."""
    proxy = proxy.strip()
    scheme, sep, rest = proxy.partition("://")
    if not sep:
        scheme, rest = "http", proxy
    rest = rest.split("@")[-1].rstrip("/")
    return f"{scheme}://:@{rest}"


def _remote_host(remote_url: str) -> str:
    if "://" not in remote_url:
        return ""
    return remote_url.split("://", 1)[1].split("/", 1)[0].split("@")[-1]


def network_routes(remote_url: str = "") -> list[tuple[str, tuple[str, ...]]]:
    """Ways to reach the remote: git as configured, every proxy this computer knows about
    (Windows settings, HTTPS_PROXY...), then no proxy. The one that worked last time goes first."""
    host = _remote_host(remote_url)
    scoped = [f"http.https://{host}/.proxy"] if host else []  # beats any per-host proxy in .gitconfig
    routes: list[tuple[str, tuple[str, ...]]] = [("as configured", ())]
    candidates = _windows_system_proxy() + [os.environ[name] for name in PROXY_VARIABLES[:4] if os.environ.get(name)]
    seen = set()
    for proxy in candidates:
        url = _proxy_url(proxy)
        if url in seen:
            continue
        seen.add(url)
        flags = ["-c", f"http.proxy={url}", "-c", "http.proxyAuthMethod=anyauth"]
        for key in scoped:
            flags += ["-c", f"{key}={url}"]
        routes.append((f"via {url.split('@')[-1]}", tuple(flags)))
    direct = ["-c", "http.proxy="]
    for key in scoped:
        direct += ["-c", f"{key}="]
    routes.append(("direct", tuple(direct)))
    remembered = _last_route.get(host)
    if remembered:
        routes.sort(key=lambda route: route[0] != remembered)
    return routes


def is_network_failure(result: subprocess.CompletedProcess) -> bool:
    text = (result.stderr or result.stdout or "").lower()
    if "authentication" in text or "terminal prompts disabled" in text or "403" in text or "repository not found" in text:
        return False
    return any(sign in text for sign in NETWORK_ERRORS)


def _run_git(args: list[str], cwd: Path | None, timeout: int, env: dict | None = None) -> subprocess.CompletedProcess:
    command = ["git", "-C", str(cwd), *args] if cwd is not None else ["git", *args]
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env, creationflags=NO_WINDOW,
    )


def git_over_network(args: list[str], cwd: Path | None, remote_url: str, timeout: int = ROUTE_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a git command that talks to the remote, trying each network route until one gets through.

    Office networks often have more than one way out (the Windows proxy, a sign-in proxy, none),
    and any of them can stall for a while. The route that worked is remembered per host and tried
    first next time. A real answer from the server (bad password, conflict) stops the search.
    """
    host = _remote_host(remote_url)
    last: subprocess.CompletedProcess | None = None
    for name, flags in network_routes(remote_url):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        if name == "direct":
            for variable in PROXY_VARIABLES:
                env.pop(variable, None)
        try:
            result = _run_git([*flags, *args], cwd, timeout, env)
        except subprocess.TimeoutExpired:
            result = subprocess.CompletedProcess(args, 124, "", f"git {args[0]} timed out after {timeout}s ({name})")
        if result.returncode == 0:
            _last_route[host] = name
            return result
        last = result
        if not is_network_failure(result):
            return result
    return last if last is not None else subprocess.CompletedProcess(args, 1, "", "no network route")


def clone_repo(url: str, destination: Path) -> Path:
    """Clone a repo (e.g. a GitHub vault) into destination; returns the folder."""
    url = url.strip()
    if not url:
        raise SyncError("Paste the address of a repo, e.g. https://github.com/you/notes")
    if "://" not in url and not url.startswith("git@") and url.count("/") == 1:
        url = f"https://github.com/{url}.git"  # owner/repo shorthand
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise SyncError(f"{destination} already exists and isn't empty.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = git_over_network(["clone", url, str(destination)], None, url, timeout=NETWORK_TIMEOUT)
    if result.returncode != 0:
        raise SyncError(_explain_git_failure(result, "clone"))
    return destination


def repo_folder_name(url: str) -> str:
    """notes from https://github.com/you/notes.git, you/notes or git@github.com:you/notes.git."""
    tail = url.strip().rstrip("/").replace(":", "/").split("/")[-1]
    name = tail[:-4] if tail.endswith(".git") else tail
    return "".join(ch for ch in name if ch.isalnum() or ch in "-_. ").strip() or "brain"

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
MAX_TODOS = 500
LOCAL_WRITE_TIMEOUT = 120  # add/commit of a big vault on a machine with busy antivirus
NETWORK_TIMEOUT = 180  # pull / push / clone; sign-in prompts from Git Credential Manager count too
# No console window flashing up for every git call when Cairn runs as a windowed app on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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
        if not relative.endswith(".md"):
            raise PathError("only .md files can be read or written")
        target = (self.dir / relative).resolve()
        if target != self.dir and self.dir not in target.parents:
            raise PathError("path escapes the brain folder")
        return target

    def relative(self, path: Path) -> str:
        return path.relative_to(self.dir).as_posix()

    # ------------------------------------------------------------ files

    def files(self) -> list[dict]:
        """Every Markdown file in the brain as {path, text, mtime}, sorted by path."""
        if not self.dir.exists():
            return []
        out = []
        for current, dirs, names in os.walk(self.dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
            for name in sorted(names):
                if not name.endswith(".md"):
                    continue
                path = Path(current) / name
                try:
                    text = _read_text(path)
                except (OSError, UnicodeDecodeError):
                    continue
                out.append({"path": self.relative(path), "text": text, "mtime": _mtime_ms(path)})
        return sorted(out, key=lambda item: item["path"])

    def notes(self) -> list[dict]:
        return [
            core.note_from_file(f["path"], f["text"], f["mtime"])
            for f in self.files()
            if f["path"] not in core.SPECIAL_FILES
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
        fetch = self._git("fetch", remote, timeout=NETWORK_TIMEOUT)
        if fetch.returncode != 0:
            raise SyncError(_explain_git_failure(fetch, "fetch"))
        remote_ref = f"refs/remotes/{remote}/{branch}"
        if self._git("rev-parse", "--verify", "-q", remote_ref).returncode != 0:
            return None  # nothing there yet: the first push creates it
        if not has_commits:
            pull = self._git("pull", remote, branch, timeout=NETWORK_TIMEOUT)
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
            pull = self._git("pull", "--rebase", "--autostash", timeout=NETWORK_TIMEOUT)
            if pull.returncode != 0:
                self._git("rebase", "--abort")
                raise SyncError(_explain_git_failure(pull, "pull"))
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
            push = self._git(*push_args, timeout=NETWORK_TIMEOUT)
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
    if "could not resolve host" in lowered or "unable to access" in lowered or "timed out" in lowered:
        return "Couldn't reach the remote (offline?). Your edits are safe and will sync next time."
    last_line = text.splitlines()[-1] if text else f"git {step} failed"
    return f"git {step} failed: {last_line}"


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
    result = subprocess.run(
        ["git", "clone", url, str(destination)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=NETWORK_TIMEOUT, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise SyncError(_explain_git_failure(result, "clone"))
    return destination


def repo_folder_name(url: str) -> str:
    """notes from https://github.com/you/notes.git, you/notes or git@github.com:you/notes.git."""
    tail = url.strip().rstrip("/").replace(":", "/").split("/")[-1]
    name = tail[:-4] if tail.endswith(".git") else tail
    return "".join(ch for ch in name if ch.isalnum() or ch in "-_. ").strip() or "brain"

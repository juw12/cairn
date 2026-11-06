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

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )

    def git_available(self) -> bool:
        if not shutil.which("git"):
            return False
        result = self._git("rev-parse", "--is-inside-work-tree")
        return result.returncode == 0 and result.stdout.strip() == "true"

    def git_status(self) -> dict:
        if not self.git_available():
            return {"available": False, "branch": "", "changes": []}
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
        return {"available": True, "branch": branch, "changes": changes}

    def _brain_pathspec(self) -> str:
        try:
            return self.dir.relative_to(self.root).as_posix() or "."
        except ValueError:
            return str(self.dir)

    def git_commit(self, message: str) -> dict:
        if not self.git_available():
            raise RuntimeError("this folder is not a git repository")
        spec = self._brain_pathspec()
        added = self._git("add", "-A", "--", spec)
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or "git add failed")
        if not self._git("diff", "--cached", "--name-only", "--", spec).stdout.strip():
            raise RuntimeError("nothing to commit in the brain folder")
        result = self._git("commit", "-m", message or "brain: update notes", "--", spec)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git commit failed")
        sha = self._git("rev-parse", "--short", "HEAD").stdout.strip()
        return {"commit": sha, "message": message}

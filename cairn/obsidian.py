"""Two-way sync between an Obsidian vault (any folder of Markdown) and a folder inside the brain.

Files are mirrored byte for byte; nothing is converted, so both apps keep reading the same Markdown.
The rules live in core.plan_sync. This module only does the file work and keeps the small state file
`brain/.cairn/obsidian.json` (vault path, target folder and the manifest of the last sync). That folder
ignores itself in git, because the vault path is specific to this machine.

Safety:
* Deletes are never permanent. A note removed in Cairn goes to the vault's `.trash/` folder (Obsidian's own
  "move to Obsidian trash" location); a note removed in Obsidian goes to `brain/.cairn/trash/`.
* If both sides changed a note, the newer one wins and the other is kept as "<name> (conflict <date>).md".
* A vault or brain folder that suddenly looks empty (unplugged drive, wrong path) stops the sync instead of
  deleting everything on the other side.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from . import core
from .brain import SKIP_DIRS, Brain

STATE_DIR = ".cairn"
STATE_FILE = "obsidian.json"
DEFAULT_FOLDER = "obsidian"
MAX_FILE_BYTES = 5_000_000


class SyncError(RuntimeError):
    """The sync can't run safely; the message says why and what to do."""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def clean_folder(folder: str | None) -> str:
    """'obsidian', '/notes/vault/' -> 'notes/vault'. Empty means the whole brain."""
    parts = [p for p in (folder or "").replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." or p.startswith(".") for p in parts):
        raise SyncError("The brain folder must be a plain folder name, for example 'obsidian'.")
    return "/".join(parts)


def scan(root: Path) -> dict[str, dict]:
    """Every Markdown file under root as {relative path: {"hash", "mtime", "text"}}, skipping dot folders."""
    files: dict[str, dict] = {}
    if not root.is_dir():
        return files
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(names):
            if not name.lower().endswith(".md") or name.endswith(".cairn-tmp"):
                continue
            path = Path(current) / name
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            except (OSError, UnicodeDecodeError):
                continue
            files[path.relative_to(root).as_posix()] = {
                "hash": text_hash(text),
                "mtime": path.stat().st_mtime_ns // 1_000_000,
                "text": text,
            }
    return files


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _write(root: Path, relative: str, text: str) -> None:
    target = (root / relative).resolve()
    if not _inside(target, root.resolve()) or not target.name.lower().endswith(".md"):
        raise SyncError(f"Refusing to write outside the synced folder: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".cairn-tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, target)


def _move_to_trash(root: Path, relative: str, trash: Path) -> None:
    source = (root / relative).resolve()
    if not _inside(source, root.resolve()) or not source.exists():
        return
    target = trash / relative
    if target.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = target.with_name(f"{target.stem} ({stamp}){target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


def conflict_name(relative: str, taken: set[str], today: str) -> str:
    stem = relative[:-3]
    candidate = f"{stem} (conflict {today}).md"
    number = 2
    while candidate in taken:
        candidate = f"{stem} (conflict {today} {number}).md"
        number += 1
    return candidate


class ObsidianSync:
    def __init__(self, brain: Brain):
        self.brain = brain
        self.state_path = brain.dir / STATE_DIR / STATE_FILE

    # ------------------------------------------------------------ state

    def load(self) -> dict:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                state.setdefault("manifest", {})
                return state
        except (OSError, ValueError):
            pass
        return {"vault": "", "folder": DEFAULT_FOLDER, "manifest": {}}

    def save(self, state: dict) -> None:
        folder = self.state_path.parent
        folder.mkdir(parents=True, exist_ok=True)
        ignore = folder / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# Machine-specific Cairn state (vault path, sync manifest, trash).\n*\n", encoding="utf-8")
        temp = self.state_path.with_name(STATE_FILE + ".tmp")
        temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.state_path)

    def status(self) -> dict:
        state = self.load()
        vault = state.get("vault") or ""
        return {
            "linked": bool(vault),
            "vault": vault,
            "vaultName": Path(vault).name if vault else "",
            "vaultFound": bool(vault) and Path(vault).is_dir(),
            "folder": state.get("folder", DEFAULT_FOLDER),
            "synced": len(state.get("manifest") or {}),
            "lastSync": state.get("lastSync", ""),
            "last": state.get("last"),
        }

    # ------------------------------------------------------------ link / unlink

    def link(self, vault: str, folder: str | None = DEFAULT_FOLDER) -> dict:
        raw = (vault or "").strip().strip('"')
        if not raw:
            raise SyncError("Give the path of your Obsidian vault folder.")
        vault_path = Path(raw).expanduser().resolve()
        if not vault_path.is_dir():
            raise SyncError(f"No folder at {vault_path}. Use the vault folder (the one that contains .obsidian).")
        folder = clean_folder(DEFAULT_FOLDER if folder is None else folder)
        target = (self.brain.dir / folder).resolve() if folder else self.brain.dir
        if _inside(vault_path, target) or _inside(target, vault_path):
            raise SyncError("The vault and the brain folder can't be inside each other. Pick another folder.")
        state = self.load()
        if state.get("vault") != str(vault_path) or state.get("folder") != folder:
            state = {"vault": str(vault_path), "folder": folder, "manifest": {}}
        self.save(state)
        return self.status()

    def unlink(self) -> dict:
        if self.state_path.exists():
            self.state_path.unlink()
        return self.status()

    # ------------------------------------------------------------ sync

    def sync(self, today: str | None = None, dry_run: bool = False) -> dict:
        state = self.load()
        if not state.get("vault"):
            raise SyncError("No vault linked yet. Run: cairn obsidian path/to/your/vault")
        vault = Path(state["vault"])
        if not vault.is_dir():
            raise SyncError(f"The vault folder is missing: {vault}. Is the drive connected?")
        folder = clean_folder(state.get("folder", DEFAULT_FOLDER))
        target = (self.brain.dir / folder) if folder else self.brain.dir
        today = today or dt.date.today().isoformat()
        manifest: dict = state.get("manifest") or {}

        vault_files = scan(vault)
        brain_files = scan(target)
        if not folder:
            # Whole-brain mode: brain/README.md and the generated CONTEXT.md belong to Cairn, not the vault.
            for name in core.SPECIAL_FILES:
                vault_files.pop(name, None)
                brain_files.pop(name, None)
        if manifest and not vault_files:
            raise SyncError("The vault looks empty, so nothing was changed. Check the vault path.")
        if manifest and not brain_files:
            raise SyncError(f"brain/{folder or ''} looks empty, so nothing was changed. Unlink and link again to re-import.")

        meta = lambda files: {p: {"hash": f["hash"], "mtime": f["mtime"]} for p, f in files.items()}  # noqa: E731
        plan = core.plan_sync(meta(vault_files), meta(brain_files), manifest)
        prefix = f"{folder}/" if folder else ""
        report = {"pulled": [], "pushed": [], "deletedInBrain": [], "deletedInVault": [], "conflicts": []}
        if dry_run:
            return {**report, "plan": plan, "dryRun": True}

        final_vault = {p: f["hash"] for p, f in vault_files.items()}
        final_brain = {p: f["hash"] for p, f in brain_files.items()}
        taken = set(final_vault) | set(final_brain)
        brain_trash = self.brain.dir / STATE_DIR / "trash" / (folder or "")
        for step in plan:
            path, op = step["path"], step["op"]
            if op == "pull":
                _write(target, path, vault_files[path]["text"])
                final_brain[path] = final_vault[path]
                report["pulled"].append(prefix + path)
            elif op == "push":
                _write(vault, path, brain_files[path]["text"])
                final_vault[path] = final_brain[path]
                report["pushed"].append(path)
            elif op == "delete-brain":
                _move_to_trash(target, path, brain_trash)
                final_brain.pop(path, None)
                report["deletedInBrain"].append(prefix + path)
            elif op == "delete-vault":
                _move_to_trash(vault, path, vault / ".trash")
                final_vault.pop(path, None)
                report["deletedInVault"].append(path)
            elif op == "conflict":
                winner = vault_files[path] if step["keep"] == "vault" else brain_files[path]
                loser = brain_files[path] if step["keep"] == "vault" else vault_files[path]
                copy = conflict_name(path, taken, today)
                taken.add(copy)
                for root, final in ((vault, final_vault), (target, final_brain)):
                    _write(root, path, winner["text"])
                    _write(root, copy, loser["text"])
                    final[path] = winner["hash"]
                    final[copy] = loser["hash"]
                report["conflicts"].append({"path": prefix + path, "copy": prefix + copy, "kept": step["keep"]})

        state["manifest"] = {p: h for p, h in final_vault.items() if final_brain.get(p) == h}
        state["lastSync"] = dt.datetime.now().isoformat(timespec="seconds")
        changes = sum(len(v) for v in report.values())
        report.update({
            "at": state["lastSync"],
            "changes": changes,
            "brainChanged": bool(report["pulled"] or report["deletedInBrain"] or report["conflicts"]),
        })
        if changes or "last" not in state:
            state["last"] = report
        self.save(state)
        return report

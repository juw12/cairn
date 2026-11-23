"""A second copy of the brain, and of your GitHub repos, in a cloud drive: locked on this computer first.

GitHub is where a brain usually lives. This keeps a copy somewhere else, for the day GitHub (or the account)
is the thing that fails. What goes up is a 7-Zip archive locked with AES-256 (file names inside hidden) that
holds one git bundle: the complete repository, every commit, in one file. To get it back: 7z x NAME.7z, then
git clone NAME.bundle NAME. The key never leaves this computer.

On the drive, in "Cairn backup": a folder "brain <date time>" per run (the newest two are kept), and in
"repos" a folder per run with the repos that were pushed since the run before (forks are left out). Nothing
is ever replaced under its own name; the newest folder's CONTENTS.json says in which folder each repo's newest
archive is. TeraBox now and then puts the time in a file's name (NAME_20261122_004953.7z): same file.

Off until it is set up on a computer (`cairn cloud setup`): it needs git, 7-Zip, Google Chrome, a private
Python environment for the helper that steers the drive's web page, a key, and one sign-in to the drive done
by the person. After that: the button on the Vibe page, `cairn cloud sync`, and once a day by itself while
Cairn is open.

The drive is TeraBox (1 TB free, 30 GB of it for good). It has no upload tool for a personal account, so the
files go through its own web page (cairn/cloud_terabox.py). That part breaks when TeraBox changes its page;
it then says what it could not find and changes nothing.

Everything of this on the computer is in CAIRN_HOME/cloud-backup/: settings.json, sent.json (what the drive
holds), the Chrome profile, the helper's environment, and work/ while a run is going.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import connect
from .brain import Brain

DRIVE_FOLDER = "/Cairn backup"
KEY_NAME = "cairn-cloud-backup"  # the entry in the macOS login keychain
PIECE = "3900m"  # the drive's free plan takes files up to 4 GB; bigger archives are cut in pieces
KEEP_FREE_GB = 2.0  # never fill the disk: a repo that would leave less than this waits
BATCH_BYTES = 300_000_000  # repo archives go up, and off this disk, in batches of about this size
BATCH_FILES = 15
KEEP_BRAINS = 2  # copies of the brain kept on the drive: the new one and the one before
BRAIN_EVERY_HOURS = 24
REPOS_EVERY_HOURS = 24 * 7
RETRY_HOURS = 6  # after a run that failed
CLONE_FROM = "https://github.com/{owner}/{name}.git"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

HOW_TO_RESTORE = """How to get these back
=====================

Every .7z file here is a 7-Zip archive locked with a key (AES-256). Inside each is one git bundle:
a complete repository with all of its history in a single file.

1. Install 7-Zip (7-zip.org) and git.
2. 7z x NAME.7z          (for NAME.7z.001, .002 ...: 7z x NAME.7z.001)   It asks for the key.
3. git clone NAME.bundle NAME

The key is on the computer that made these: {key_place}
It is not in this folder and not on GitHub. Without it nothing here opens.

CONTENTS.json lists each archive with its size, SHA-256 and the commit it was made at.
It names the repositories, so it is the one file here that is readable without the key.

In "repos" there is a folder per run. An archive is in the folder its line in the newest CONTENTS.json names
("folder"); a repo that was pushed again has a newer archive in a newer folder, and the older one stays.
"""


class CloudError(Exception):
    """The backup cannot go on; the message says why and what to do."""


# ---------------------------------------------------------------- where things are


def home() -> Path:
    return connect.cairn_home() / "cloud-backup"


def _read(name: str) -> dict:
    try:
        data = json.loads((home() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(name: str, data: dict) -> None:
    home().mkdir(parents=True, exist_ok=True)
    temporary = home() / (name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, home() / name)


def settings() -> dict:
    return _read("settings.json")


def sent() -> dict:
    data = _read("sent.json")
    data.setdefault("repos", {})
    return data


def _find(name: str, *places: str) -> str:
    return shutil.which(name) or next((place for place in places if place and Path(place).is_file()), "")


def git() -> str:
    return _find("git", "/usr/bin/git", r"C:\Program Files\Git\cmd\git.exe")


def seven_zip() -> str:
    return _find("7z", "/usr/local/bin/7z", "/opt/homebrew/bin/7z", r"C:\Program Files\7-Zip\7z.exe") or _find("7zz") or _find("7za")


def gh() -> str:
    return _find("gh", "/usr/local/bin/gh", "/opt/homebrew/bin/gh",
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "GitHubCLI" / "bin" / "gh.exe"))


def sender_python() -> str:
    """The Python of the helper's own environment (it has playwright; Cairn's own Python needs nothing)."""
    folder = home() / "venv"
    found = next((path for path in (folder / "bin" / "python", folder / "Scripts" / "python.exe") if path.is_file()), None)
    return str(found) if found else ""


def chrome() -> str:
    return _find("google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"))


def missing(repos: bool = False) -> list[str]:
    """What this computer still lacks, each with what to do about it."""
    lacks = []
    if not git():
        lacks.append("git (git-scm.com)")
    if not seven_zip():
        lacks.append("7-Zip (7-zip.org; on a Mac: brew install sevenzip)")
    if not chrome():
        lacks.append("Google Chrome (google.com/chrome)")
    if repos and not gh():
        lacks.append("the GitHub CLI, signed in (cli.github.com), to list your repos")
    return lacks


# ---------------------------------------------------------------- the key


def _keychain(*args: str) -> str:
    done = subprocess.run(["security", *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


def read_key() -> str:
    if os.environ.get("CAIRN_BACKUP_KEY", "").strip():
        return os.environ["CAIRN_BACKUP_KEY"].strip()
    if sys.platform == "darwin":
        found = _keychain("find-generic-password", "-s", KEY_NAME, "-w")
        if found:
            return found
    try:
        return (home() / "key.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def key_place() -> str:
    if os.environ.get("CAIRN_BACKUP_KEY", "").strip():
        return "the CAIRN_BACKUP_KEY setting of this computer"
    if sys.platform == "darwin" and _keychain("find-generic-password", "-s", KEY_NAME, "-w"):
        return f'the macOS login keychain, under "{KEY_NAME}" (print it with: cairn cloud key --show)'
    return f"the file {home() / 'key.txt'} (print it with: cairn cloud key --show)"


def make_key() -> str:
    """The key that locks every archive. Made once. In the login keychain on a Mac, else in a file only this
    user can read."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    made = "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(6))
    if sys.platform == "darwin":
        done = subprocess.run(["security", "add-generic-password", "-s", KEY_NAME, "-a", os.environ.get("USER", "cairn"),
                               "-w", made, "-j", "Opens the encrypted backups Cairn sends to the cloud. Keep a copy off this Mac."],
                              capture_output=True, text=True)
        if done.returncode == 0:
            return made
    home().mkdir(parents=True, exist_ok=True)
    path = home() / "key.txt"
    path.write_text(made + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return made


# ---------------------------------------------------------------- packing


def _run(*command: str) -> str:
    done = subprocess.run(list(command), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=NO_WINDOW if os.name == "nt" else 0)
    if done.returncode != 0:
        raise CloudError(f"{Path(command[0]).name} {command[1] if len(command) > 1 else ''} failed: {(done.stderr or done.stdout).strip()[-300:]}")
    return done.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def remove_tree(path) -> None:
    """Delete a folder, also on Windows: git writes its objects read-only there, and shutil.rmtree with
    ignore_errors left a whole mirror behind (found on the ASUS, 2026-11-23). Never raises."""
    import stat

    def clear_and_retry(function, failed_path, *_):
        try:
            os.chmod(failed_path, stat.S_IWRITE)
            function(failed_path)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=clear_and_retry)
    else:
        shutil.rmtree(path, onerror=clear_and_retry)


def free_gb(folder: Path) -> float:
    return shutil.disk_usage(folder).free / 1e9


def pack(source: Path, name: str, folder: Path, key: str, drop_source: bool = False) -> dict:
    """One repository (or, without git in it, one folder) -> folder/NAME.7z, or NAME.7z.001, .002 ... when big.
    With drop_source (a mirror fetched only for this) the repository is deleted as soon as its bundle is made
    and checked, so the disk never holds the mirror, the bundle and the archive at once."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{name}.7z*"):
        old.unlink()
    archive = folder / f"{name}.7z"
    lock = [seven_zip(), "a", "-t7z", "-mx=1", "-mhe=on", f"-p{key}", f"-v{PIECE}", "-bso0", "-bsp0", str(archive)]
    entry: dict = {"name": name, "made": dt.datetime.now().isoformat(timespec="seconds")}
    is_repo = subprocess.run([git(), "-C", str(source), "rev-parse", "--git-dir"], capture_output=True).returncode == 0
    if is_repo:
        if _run(git(), "-C", str(source), "rev-parse", "--is-shallow-repository") == "true":
            raise CloudError(f"{source} holds only part of its history; a backup from it would be incomplete.")
        with tempfile.TemporaryDirectory(prefix="cairn-backup-") as scratch:
            bundle = Path(scratch) / f"{name}.bundle"
            _run(git(), "-C", str(source), "bundle", "create", str(bundle), "--all")
            _run(git(), "-C", str(source), "bundle", "verify", str(bundle))
            heads = _run(git(), "-C", str(source), "bundle", "list-heads", str(bundle)).splitlines()
            entry.update({"kind": "git bundle", "refs": len(heads),
                          "head": next((line.split()[0] for line in heads if line.endswith(" HEAD")), heads[0].split()[0] if heads else "")})
            if drop_source:
                remove_tree(source)
            _run(*lock, str(bundle))
    else:
        entry["kind"] = "folder"
        _run(*lock, str(source))
    pieces = sorted(folder.glob(f"{name}.7z.*"))
    if len(pieces) == 1:  # one piece: plain NAME.7z is easier to recognise
        pieces[0].rename(archive)
        pieces = [archive]
    entry["files"] = [{"file": piece.name, "bytes": piece.stat().st_size, "sha256": _sha256(piece)} for piece in pieces]
    return entry


def write_contents(folder: Path, entries: dict) -> list[Path]:
    """The two readable files that go with the archives: what is here, and how to get it back."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "CONTENTS.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    (folder / "HOW-TO-RESTORE.txt").write_text(HOW_TO_RESTORE.format(key_place=key_place()), encoding="utf-8")
    return [folder / "CONTENTS.json", folder / "HOW-TO-RESTORE.txt"]


def repo_list(owner: str) -> list[dict]:
    """The repos of `owner` that the GitHub CLI's sign-in can see: name, size on GitHub in KB, last push.
    Forks are left out: they are copies of someone else's public repo, and can be very large."""
    rows = _run(gh(), "api", "--paginate", "user/repos?affiliation=owner,collaborator&per_page=100", "--jq",
                f'.[] | select(.owner.login=="{owner}" and .fork==false) | [.name, .size, .pushed_at] | @tsv')
    return [{"name": name, "kb": int(kb), "pushed": pushed} for name, kb, pushed in (line.split("\t") for line in rows.splitlines() if line.count("\t") == 2)]


# ---------------------------------------------------------------- the drive


def send(*args: str, log=print) -> dict:
    """Run the helper that steers the drive's web page. Its last line is the result; the others are progress."""
    python = sender_python()
    if not python:
        raise CloudError("The upload helper is not set up on this computer. Run: cairn cloud setup")
    process = subprocess.Popen([python, "-u", str(Path(__file__).with_name("cloud_terabox.py")), *args],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                               env={**os.environ, "CAIRN_HOME": str(connect.cairn_home())},
                               creationflags=NO_WINDOW if os.name == "nt" else 0)
    last = ""
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            last = line
        else:
            log(line)
    process.wait()
    try:
        result = json.loads(last)
    except ValueError:
        raise CloudError("The upload helper stopped without an answer.")
    if not result.get("ok"):
        wrong = result.get("wrong") or []
        raise CloudError(result.get("error") or f"{len(wrong)} file(s) are missing or have another size on the drive: {', '.join(wrong[:6])}")
    return result


# ---------------------------------------------------------------- a run

_lock = threading.Lock()
_progress = {"running": False, "step": ""}


def _step(text: str, log) -> None:
    _progress["step"] = text
    log(text)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="minutes")


_last_stamp = ""


def _stamp() -> str:
    """The date and time a folder on the drive is named by. Never the same twice: a folder name is never reused."""
    global _last_stamp
    while True:
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H%M%S")
        if stamp != _last_stamp:
            _last_stamp = stamp
            return stamp
        time.sleep(0.2)


def _sync_brain(brain: Brain, key: str, log) -> dict:
    remove_tree(home() / "work")
    name = f"brain {_stamp()}"
    work = home() / "work" / name
    _step("Making the brain's archive", log)
    try:
        root = _run(git(), "-C", str(brain.dir), "rev-parse", "--show-toplevel")
    except CloudError:
        root = str(brain.dir)
    entry = pack(Path(root), "brain", work, key)
    entry["notes"] = len(brain.files())
    write_contents(work, {"brain": entry})
    size = sum(part["bytes"] for part in entry["files"])
    _step(f"Sending the brain ({size / 1e6:.0f} MB)", log)
    send("upload-folder", str(work), DRIVE_FOLDER, log=log)
    remove_tree(work)
    # Only now, with the new copy checked on the drive, do older copies go (to the drive's recycle bin).
    there = send("list", DRIVE_FOLDER, log=log).get("files", {})
    older = sorted(folder for folder in there if folder == "brain" or folder.startswith("brain ") and folder != name)
    extra = older[: max(0, len(older) - (KEEP_BRAINS - 1))]
    if extra:
        _step(f"Removing {len(extra)} older copy(ies) of the brain from the drive", log)
        send("remove", DRIVE_FOLDER, *extra, log=log)
    return {"at": _now(), "folder": name, "bytes": size, "files": len(entry["files"]) + 2, "head": entry.get("head", ""), "notes": entry["notes"]}


def _sync_repos(owner: str, key: str, log) -> dict:
    record = sent()
    held: dict = record["repos"]
    repos = repo_list(owner)
    changed = sorted((repo for repo in repos if held.get(repo["name"], {}).get("pushed") != repo["pushed"]), key=lambda item: item["kb"])
    remote = f"{DRIVE_FOLDER}/repos"
    run_name = _stamp()
    done = waiting = batches = 0
    batch: list[tuple[dict, dict]] = []
    work = home() / "work" / run_name

    def flush() -> None:
        """Send what is packed as one new folder on the drive, with the list of where everything is."""
        nonlocal done, batches, work
        if not batch:
            return
        for repo, entry in batch:
            held[repo["name"]] = {"pushed": repo["pushed"], "at": _now(), "folder": f"repos/{work.name}", "head": entry.get("head", ""), "files": entry["files"]}
        contents = {name: {"from": f"https://github.com/{owner}/{name}", **entry} for name, entry in sorted(held.items()) if not entry.get("empty")}
        write_contents(work, contents)
        _step(f"Sending {len(batch)} repo(s), {done + len(batch)} of {len(changed)}", log)
        send("upload-folder", str(work), remote, log=log)
        done += len(batch)
        _write("sent.json", record)  # what is up is recorded at once: a run that stops later starts from here
        remove_tree(work)
        batch.clear()
        batches += 1
        work = home() / "work" / f"{run_name} part {batches + 1}"

    for repo in changed:
        name = repo["name"]
        need = repo["kb"] / 1e6 * 2  # two of the three at a time: the mirror and its bundle, then the bundle and the archive
        if free_gb(home()) - need < KEEP_FREE_GB:
            waiting += 1
            log(f"Not enough room on this disk for {name} ({repo['kb'] / 1024:.0f} MB on GitHub): it waits")
            continue
        _step(f"Making the archive of {name}", log)
        with tempfile.TemporaryDirectory(prefix="cairn-backup-") as scratch:
            mirror = Path(scratch) / f"{name}.git"
            _run(git(), "clone", "--mirror", "--quiet", CLONE_FROM.format(owner=owner, name=name), str(mirror))
            if not _run(git(), "-C", str(mirror), "for-each-ref", "--count=1"):
                held[name] = {"pushed": repo["pushed"], "at": _now(), "empty": True, "files": []}
                continue
            entry = pack(mirror, name, work, key, drop_source=True)
        batch.append((repo, entry))
        if len(batch) >= BATCH_FILES or sum(part["bytes"] for _, item in batch for part in item["files"]) >= BATCH_BYTES:
            flush()
    flush()
    remove_tree(home() / "work")
    record["repos_at"] = _now()
    _write("sent.json", record)
    return {"at": record["repos_at"], "sent": done, "waiting": waiting, "held": sum(1 for entry in held.values() if not entry.get("empty")), "on_github": len(repos)}


def sync(brain: Brain, repos: bool | None = None, log=print) -> dict:
    """Make the archives and send them. One run at a time. The outcome is kept (sent.json) and returned."""
    chosen = settings()
    if not chosen.get("set_up"):
        raise CloudError("Cloud backup is not set up on this computer. Run: cairn cloud setup")
    with_repos = chosen.get("repos", False) if repos is None else repos
    if with_repos and not chosen.get("owner"):
        raise CloudError("Whose repos? Run: cairn cloud setup --repos")
    lacks = missing(with_repos)
    if lacks:
        raise CloudError("This computer lacks: " + "; ".join(lacks))
    key = read_key()
    if not key:
        raise CloudError("The key that locks the backup is not on this computer any more. See: cairn cloud key")
    if not _lock.acquire(blocking=False):
        raise CloudError("A backup is already running.")
    _progress.update({"running": True, "step": "Starting"})
    outcome: dict = {"at": _now(), "ok": False, "with_repos": with_repos}
    try:
        result = {"brain": _sync_brain(brain, key, log)}
        record = sent()
        record["brain"] = result["brain"]
        _write("sent.json", record)
        if with_repos:
            result["repos"] = _sync_repos(chosen.get("owner") or "", key, log)
        told = [f"brain {result['brain']['bytes'] / 1e6:.0f} MB"]
        if with_repos:
            told.append(f"{result['repos']['sent']} repo(s) sent, {result['repos']['held']} of {result['repos']['on_github']} held"
                        + (f", {result['repos']['waiting']} waiting for room" if result['repos']['waiting'] else ""))
        outcome.update({"ok": True, "message": "Sent: " + "; ".join(told), **result})
        return outcome
    except CloudError as error:
        outcome["message"] = str(error)
        raise
    except Exception as error:  # a bug here must be seen on the page, not lost in a thread
        outcome["message"] = f"{type(error).__name__}: {error}"
        raise CloudError(outcome["message"]) from error
    finally:
        remove_tree(home() / "work")
        record = sent()
        record["last"] = {key_: outcome[key_] for key_ in ("at", "ok", "message", "with_repos") if key_ in outcome}
        _write("sent.json", record)
        _progress.update({"running": False, "step": ""})
        _lock.release()


def start(brain: Brain, repos: bool | None = None) -> None:
    """The same, in the background, for the page's button. Raises at once when it cannot start."""
    if not settings().get("set_up"):
        raise CloudError("Cloud backup is not set up on this computer. Run: cairn cloud setup")
    if _progress["running"]:
        raise CloudError("A backup is already running.")

    def run() -> None:
        try:
            sync(brain, repos, log=lambda line: None)
        except CloudError:
            pass  # kept in sent.json and shown by status()

    _progress.update({"running": True, "step": "Starting"})
    threading.Thread(target=run, daemon=True, name="cairn-cloud-backup").start()


def status() -> dict:
    """What the page and `cairn cloud` show. Reads two small files; asks nobody."""
    chosen, record = settings(), sent()
    held = sum(1 for entry in record["repos"].values() if not entry.get("empty"))
    return {
        "setUp": bool(chosen.get("set_up")),
        "drive": "TeraBox",
        "running": _progress["running"],
        "step": _progress["step"],
        "auto": chosen.get("auto", True),
        "repos": bool(chosen.get("repos")),
        "owner": chosen.get("owner", ""),
        "last": record.get("last"),
        "brain": record.get("brain"),
        "reposHeld": held,
        "reposAt": record.get("repos_at", ""),
    }


def summary(reading: dict) -> str:
    if not reading["setUp"]:
        return "Cloud backup is not set up on this computer. Run: cairn cloud setup"
    lines = [f"Cloud backup to {reading['drive']}: " + ("running: " + reading["step"] if reading["running"] else "ready")]
    brain = reading.get("brain")
    lines.append(f"  brain: sent {brain['at']} ({brain['bytes'] / 1e6:.0f} MB)" if brain else "  brain: not sent yet")
    if reading["repos"]:
        lines.append(f"  repos of {reading['owner']}: {reading['reposHeld']} held" + (f", last run {reading['reposAt']}" if reading["reposAt"] else ""))
    last = reading.get("last")
    if last and not last.get("ok"):
        lines.append(f"  the last run ({last['at']}) did not finish: {last.get('message', '')}")
    lines.append("  by itself once a day while Cairn is open: " + ("on" if reading["auto"] else "off"))
    return "\n".join(lines)


def change(**values) -> dict:
    """Turn a setting on or off (auto, repos, set_up) and say what holds now."""
    chosen = settings()
    chosen.update(values)
    _write("settings.json", chosen)
    return status()


# ---------------------------------------------------------------- once a day by itself


def _hours_since(stamp: str, now: dt.datetime) -> float:
    try:
        return (now - dt.datetime.fromisoformat(stamp)).total_seconds() / 3600
    except (TypeError, ValueError):
        return float("inf")


def due(now: dt.datetime | None = None) -> str:
    """"" (nothing to do), "brain", or "all" (the repos' weekly turn too)."""
    chosen, record = settings(), sent()
    now = now or dt.datetime.now()
    if not chosen.get("set_up") or not chosen.get("auto", True) or _progress["running"]:
        return ""
    last = record.get("last") or {}
    if not last.get("ok") and _hours_since(last.get("at", ""), now) < RETRY_HOURS:
        return ""
    if _hours_since((record.get("brain") or {}).get("at", ""), now) < BRAIN_EVERY_HOURS:
        return ""
    return "all" if chosen.get("repos") and _hours_since(record.get("repos_at", ""), now) >= REPOS_EVERY_HOURS else "brain"


def auto_loop(get_brain, stop: threading.Event, every_seconds: int = 1200) -> None:
    """Run by `cairn serve` and the desktop app in a thread: look every 20 minutes, back up when a day has gone."""
    while not stop.wait(every_seconds):
        try:
            what = due()
            if what:
                sync(get_brain(), repos=what == "all", log=lambda line: None)
        except Exception:  # shown by status(); the app goes on
            pass


def start_auto(get_brain) -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=auto_loop, args=(get_brain, stop), daemon=True, name="cairn-cloud-auto").start()
    return stop


# ---------------------------------------------------------------- setting it up


def setup(repos: bool = False, owner: str = "", say=print) -> bool:
    """Everything this computer needs, in order. Stops at the first thing only the person can do, says what,
    and carries on from there when run again. True when the backup is ready to run."""
    lacks = missing(repos)
    if lacks:
        say("This computer still needs:")
        for item in lacks:
            say(f"  - {item}")
        say("Install them and run `cairn cloud setup` again.")
        return False
    if not sender_python():
        say("Making a private Python environment for the upload helper (one download, about 50 MB) ...")
        _run(sys.executable, "-m", "venv", str(home() / "venv"))
        _run(sender_python(), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "playwright")
    if not read_key():
        make_key()
        say("A key was made. It locks every archive before it leaves this computer.")
    say(f"The key is in {key_place()}.")
    say("  Copy it somewhere that is not this computer. Without it a backup cannot be opened.")
    if repos and not owner:
        try:
            owner = _run(gh(), "api", "user", "--jq", ".login")
        except CloudError:
            say("The GitHub CLI is not signed in (gh auth login), so the repos cannot be listed. Run this again after.")
            return False
    if not send("check", log=lambda line: None).get("signedIn"):
        send("window", log=lambda line: None)
        say("A Chrome window opened on TeraBox. Sign in there (or make an account), close the window,")
        say("then run `cairn cloud setup` again.")
        return False
    chosen = settings()
    chosen.update({"set_up": True, "drive": "terabox", "repos": bool(repos or chosen.get("repos")),
                   "owner": owner or chosen.get("owner", ""), "auto": chosen.get("auto", True)})
    _write("settings.json", chosen)
    say("Cloud backup is set up. `cairn cloud sync` sends now; the Vibe page has a button; and while Cairn is open")
    say("it sends once a day by itself (cairn cloud auto off stops that).")
    return True

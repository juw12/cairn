"""Cairn as a desktop app: its own window, always running in the tray, updating itself.

    pythonw Cairn.pyw            open the window
    pythonw Cairn.pyw --hidden   start in the tray (how it starts with Windows)
    python -m cairn desktop      the same, from a terminal

Everything a person needs is inside the app, no terminal and no git commands:
  * Open a brain: the last Obsidian vault you used is picked up automatically on first run;
    "Switch brain" opens any folder, clones a GitHub repo or starts a new brain.
  * Sync: one button commits your edits, pulls everyone else's and pushes (and it happens on
    its own every few minutes). GitHub sign-in is handled by Git Credential Manager's window.
  * Settings: start with Windows, sync automatically, update automatically.
  * The Vibe page fills itself in: while Cairn runs it records OpenCode sessions (the same pass as
    `cairn vibe watch`), so no separate watcher needs installing. Only one watcher writes at a time.
  * Updates arrive on their own: a new web app is swapped in with a page reload the moment you
    stop typing; a new Python part restarts Cairn quietly (in the tray, or when you're idle) and
    reopens it where you were. Nobody has to restart anything by hand.

The window is pywebview (Edge WebView2 on Windows) and the tray icon is pystray; both are
imported only when the app actually starts, so this module stays importable (and testable)
anywhere.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__, watch
from .brain import NO_WINDOW, Brain, SyncError, clone_repo, repo_folder_name
from .server import attach_autopilot, make_server, switch_brain
from .winshell import AUMID, stamp_shortcut, start_menu_shortcut

APP_ROOT = Path(__file__).resolve().parent.parent  # the git checkout the installer made
PORT = 47777  # own fixed port: saved settings survive restarts; never clashes with `cairn serve` (4777)
SYNC_FIRST_SECONDS = 60
SYNC_EVERY_SECONDS = 10 * 60
UPDATE_FIRST_SECONDS = 60
UPDATE_EVERY_SECONDS = 20 * 60
VIBE_WATCH_FIRST_SECONDS = 20
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Cairn"
MAX_RECENT = 8
DEPENDENCIES = ["pywebview", "pystray", "pillow"]


# ============================================================ settings on disk

def config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Cairn"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cairn"


def log(message: str) -> None:
    try:
        folder = config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with (folder / "cairn.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {message}\n")
    except OSError:
        pass


class Config:
    """A tiny JSON file: folder, recent folders, autostart, autoSync, vibeWatch."""

    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "config.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def remember_folder(self, folder: Path) -> None:
        folder_text = str(folder)
        key = folder_key(folder)
        recent = [p for p in self.data.get("recent", []) if folder_key(p) != key]
        self.data["folder"] = folder_text
        self.data["recent"] = [folder_text, *recent][:MAX_RECENT]
        self.save()


# ============================================================ which folder to open

def obsidian_vaults(config_file: Path | None = None) -> list[Path]:
    """Vaults Obsidian knows about on this machine, most recently used first."""
    if config_file is None:
        base = Path(os.environ.get("APPDATA", "")) if sys.platform == "win32" else Path.home() / ".config"
        config_file = base / "obsidian" / "obsidian.json"
    try:
        vaults = json.loads(Path(config_file).read_text(encoding="utf-8")).get("vaults", {})
    except (OSError, ValueError, AttributeError):
        return []
    entries = [v for v in vaults.values() if isinstance(v, dict) and v.get("path")]
    entries.sort(key=lambda v: (bool(v.get("open")), v.get("ts", 0)), reverse=True)
    return [Path(v["path"]) for v in entries if Path(v["path"]).is_dir()]


def documents_dir() -> Path:
    return Path.home() / "Documents"


def default_folder(config: Config, vaults: list[Path] | None = None) -> Path:
    """Last folder used → the Obsidian vault used most recently → a fresh brain in Documents."""
    saved = config.get("folder")
    if saved and Path(saved).is_dir():
        return Path(saved)
    vaults = obsidian_vaults() if vaults is None else vaults
    if vaults:
        return vaults[0]
    fresh = documents_dir() / "Cairn"
    fresh.mkdir(parents=True, exist_ok=True)
    return fresh


def folder_key(folder) -> str:
    """One identity per folder, however Windows spells it (8.3 short names, letter case)."""
    return os.path.normcase(os.path.realpath(str(folder)))


def unique_folder(parent: Path, name: str) -> Path:
    candidate = parent / name
    number = 2
    while candidate.exists() and any(candidate.iterdir()):
        candidate = parent / f"{name} {number}"
        number += 1
    return candidate


# ============================================================ start with Windows

def pythonw_executable() -> str:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def autostart_command(launcher: Path | None = None) -> str:
    launcher = launcher or APP_ROOT / "Cairn.pyw"
    return f'"{pythonw_executable()}" "{launcher}" --hidden'


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        raise OSError("Start with Windows is only available on Windows.")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


# ============================================================ updating Cairn itself

def ensure_start_menu_shortcut(config: "Config") -> None:
    """Keep the Start-menu shortcut tagged with Cairn's AppUserModelID.

    Without the tag a pinned taskbar button can't find Cairn's icon or relaunch command (it shows a
    blank icon). Written once per install location; rewritten if the shortcut goes missing.
    """
    if sys.platform != "win32":
        return
    link = start_menu_shortcut()
    stamp = f"{AUMID}|{APP_ROOT}"
    if link.exists() and config.get("shortcutStamp") == stamp:
        return
    try:
        stamp_shortcut(
            link,
            pythonw_executable(),
            f'"{APP_ROOT / "Cairn.pyw"}"',
            APP_ROOT / "docs" / "icons" / "cairn.ico",
            APP_ROOT,
        )
        config.set("shortcutStamp", stamp)
        log("start menu shortcut tagged for taskbar pinning")
    except (OSError, subprocess.SubprocessError) as error:
        log(f"could not tag the start menu shortcut: {error}")


def app_git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess | None:
    if not (APP_ROOT / ".git").exists() or not shutil.which("git"):
        return None
    try:
        return subprocess.run(
            ["git", "-C", str(APP_ROOT), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def app_head() -> str:
    result = app_git("rev-parse", "--short", "HEAD")
    return result.stdout.strip() if result and result.returncode == 0 else ""


def changed_files(old: str, new: str) -> list[str] | None:
    """Paths that differ between two builds of the app, or None when git can't tell."""
    result = app_git("diff", "--name-only", f"{old}..{new}")
    if result is None or result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def needs_restart(paths: list[str]) -> bool:
    """True when an update touches code the running process has loaded.

    The web app (docs/) is read from disk on every request, so changes there only need the
    page to reload. Python code, the launcher and the package metadata need a fresh process.
    """
    for path in paths:
        if path in ("Cairn.pyw", "pyproject.toml") or (path.startswith("cairn/") and path.endswith(".py")):
            return True
    return False



# ============================================================ publishing the public brain

def public_digest(source: Path) -> str:
    """Fingerprint of what would be published (notes marked publish: true, private links removed).

    Lets the app skip GitHub entirely when nothing public changed since the last publish.
    """
    from . import publish

    files = publish.unlink_private(publish.select_public(source))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8") + b"\0" + files[path].encode("utf-8") + b"\0")
    return digest.hexdigest()


def publish_now(source: Path, repo: str, config: "Config", force: bool = False) -> dict:
    """Publish public notes to `repo` if they changed (or always with force). Records the outcome."""
    from . import publish

    digest = public_digest(source)
    if not force and digest == config.get("publishedDigest"):
        return {"published": False, "reason": "up to date"}
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        http = publish.make_http(publish.find_token())
        report = publish.publish_to_github(source, repo, http, dt.date.today().isoformat())
    except publish.PublishError as error:
        config.set("lastPublish", {"at": stamp, "error": str(error), "repo": repo})
        raise SyncError(str(error)) from error
    config.data["publishedDigest"] = digest
    config.set("lastPublish", {
        "at": stamp, "repo": repo, "commit": (report.get("commit") or "")[:7],
        "changed": len(report["write"]) + len(report["delete"]),
    })
    return {"published": bool(report.get("commit")), "commit": report.get("commit"), "changed": len(report["write"]) + len(report["delete"])}


def valid_repo(text: str) -> str:
    text = text.strip().removeprefix("https://github.com/").strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    parts = text.split("/")
    if len(parts) != 2 or not all(parts) or any(not ch.isalnum() and ch not in "-_." for ch in "".join(parts)):
        raise ValueError("Use OWNER/REPO, for example juw12/brain.")
    return text

# ============================================================ vibe sessions

def vibe_watch_options(config: Config, root: Path) -> dict:
    """How the app watches for AI coding sessions in the open brain.

    An Obsidian vault is a whole personal brain, so sessions from every folder belong in it; a brain
    inside one project only takes that project's sessions (the CLI default). Vector sessions are left
    to the assistant's own reports unless turned on: process detection can only guess."""
    everywhere = config.get("vibeWatchEverywhere")
    if everywhere is None:
        everywhere = (Path(root) / ".obsidian").is_dir()
    return {
        "opencode": True,
        "vector": bool(config.get("vibeWatchVector", False)),
        "everywhere": bool(everywhere),
    }


def vibe_watch_pass(brain: Brain, config: Config, owner: str, **overrides) -> list[str] | None:
    """One watcher pass from the app. None when switched off or another watcher holds the brain."""
    if not config.get("vibeWatch", True):
        watch.release_watch(brain, owner)
        return None
    if not watch.claim_watch(brain, owner):
        return None
    options = {**vibe_watch_options(config, brain.root), **overrides}
    return watch.watch_once(brain, **options)


# ============================================================ what the web app can ask for

class DesktopHooks:
    """Backs GET/POST /api/desktop. The GUI parts (window, restart) are injected by the app."""

    def __init__(self, config: Config, server=None):
        self.config = config
        self.server = server
        self.pick_folder = None  # set by the app: () -> str | None (native folder dialog)
        self.request_restart = None  # set by the app: () -> None
        self.loaded_head = app_head()  # the build whose Python code is running
        self.page_head = self.loaded_head  # the build whose web app the window has loaded

    # -------------------------------------------------- state
    def state(self) -> dict:
        current = self.server.RequestHandlerClass.brain.root if self.server is not None else None
        seen = {folder_key(current)} if current is not None else set()
        places = []
        candidates = [Path(p) for p in self.config.get("recent", [])] + obsidian_vaults()[:MAX_RECENT]
        for folder in candidates:
            key = folder_key(folder)
            if key in seen or not folder.is_dir():
                continue
            seen.add(key)
            places.append({"path": str(folder), "name": folder.name})
        return {
            "version": __version__,
            "build": self.loaded_head,
            "updates": (APP_ROOT / ".git").exists(),
            "autoUpdate": self.config.get("autoUpdate", True),
            "publishTo": self.config.get("publishTo", ""),
            "autoPublish": bool(self.config.get("autoPublish", False)),
            "lastPublish": self.config.get("lastPublish"),
            "autostart": autostart_enabled(),
            "autostartSupported": sys.platform == "win32",
            "autoSync": self.config.get("autoSync", True),
            "vibeWatch": bool(self.config.get("vibeWatch", True)),
            "folder": str(current) if current is not None else self.config.get("folder"),
            "places": places[:MAX_RECENT],
        }

    # -------------------------------------------------- actions
    def act(self, action: str, payload: dict) -> dict:
        handlers = {
            "open": lambda: self.open(Path(str(payload.get("path", "")))),
            "pick": self.pick,
            "clone": lambda: self.clone(str(payload.get("url", ""))),
            "new": lambda: self.new_brain(str(payload.get("name", "")) or "My brain"),
            "autostart": lambda: self.toggle_autostart(bool(payload.get("on"))),
            "auto-sync": lambda: self.toggle_auto_sync(bool(payload.get("on"))),
            "vibe-watch": lambda: self.toggle_vibe_watch(bool(payload.get("on"))),
            "auto-update": lambda: self.toggle_auto_update(bool(payload.get("on"))),
            "check-update": self.check_update,
            "update": self.update,
            "restart": self.restart_now,
            "publish-settings": lambda: self.publish_settings(str(payload.get("to", "")), bool(payload.get("auto"))),
            "publish": self.publish,
        }
        if action not in handlers:
            raise ValueError(f"unknown desktop action: {action}")
        return handlers[action]()

    def open(self, folder: Path) -> dict:
        folder = folder.expanduser()
        if not folder.is_dir():
            raise ValueError(f"{folder} isn't a folder.")
        brain = Brain(folder)
        if not brain.dir.exists():
            brain.init()
        if self.server is not None:
            switch_brain(self.server, brain)
        self.config.remember_folder(folder.resolve())
        log(f"opened {folder}")
        return {"reload": True, "folder": str(folder)}

    def pick(self) -> dict:
        chosen = self.pick_folder() if self.pick_folder else None
        if not chosen:
            return {"cancelled": True}
        return self.open(Path(chosen))

    def clone(self, url: str) -> dict:
        target = unique_folder(documents_dir() / "Cairn", repo_folder_name(url))
        clone_repo(url, target)
        return self.open(target)

    def new_brain(self, name: str) -> dict:
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_. ").strip() or "My brain"
        target = unique_folder(documents_dir() / "Cairn", safe)
        target.mkdir(parents=True, exist_ok=True)
        if shutil.which("git"):
            subprocess.run(["git", "init", "-q", str(target)], capture_output=True, creationflags=NO_WINDOW)
        return self.open(target)

    def toggle_autostart(self, on: bool) -> dict:
        set_autostart(on)
        self.config.set("autostart", on)
        return {"autostart": autostart_enabled()}

    def toggle_auto_sync(self, on: bool) -> dict:
        self.config.set("autoSync", on)
        return {"autoSync": on}

    def toggle_vibe_watch(self, on: bool) -> dict:
        self.config.set("vibeWatch", on)
        return {"vibeWatch": on}

    def publish_settings(self, to: str, auto: bool) -> dict:
        repo = valid_repo(to) if to.strip() else ""
        if repo != self.config.get("publishTo"):
            self.config.data.pop("publishedDigest", None)  # a new target gets everything
        self.config.data["publishTo"] = repo
        self.config.set("autoPublish", bool(auto and repo))
        return {"publishTo": repo, "autoPublish": bool(auto and repo)}

    def publish(self) -> dict:
        repo = self.config.get("publishTo")
        if not repo:
            raise ValueError("Choose where to publish first, e.g. you/brain.")
        brain = self.server.RequestHandlerClass.brain
        return publish_now(brain.dir, repo, self.config, force=True)

    def maybe_publish(self) -> dict | None:
        """After a sync: publish public notes if auto-publish is on and something public changed."""
        repo = self.config.get("publishTo")
        if not repo or not self.config.get("autoPublish") or self.server is None:
            return None
        brain = self.server.RequestHandlerClass.brain
        return publish_now(brain.dir, repo, self.config)

    def toggle_auto_update(self, on: bool) -> dict:
        self.config.set("autoUpdate", on)
        return {"autoUpdate": on}

    def check_update(self) -> dict:
        if app_git("fetch", "--quiet") is None:
            return {"available": False, "reason": "This copy of Cairn wasn't installed with the installer."}
        behind = app_git("rev-list", "--count", "HEAD..@{u}")
        count = int(behind.stdout.strip() or 0) if behind and behind.returncode == 0 else 0
        return {"available": count > 0, "behind": count, "build": self.loaded_head}

    def update(self) -> dict:
        pulled = app_git("pull", "--ff-only", "--quiet")
        if pulled is None or pulled.returncode != 0:
            raise SyncError("Couldn't download the update (offline?). Try again later.")
        kind = self.pending_update()
        if kind == "restart" and self.request_restart:
            threading.Timer(0.5, self.request_restart).start()
            return {"updated": True, "restarting": True}
        if kind == "reload":
            self.page_head = app_head()
            return {"updated": True, "reload": True}
        return {"updated": False}

    def restart_now(self) -> dict:
        """The page asks for this once it's idle and has saved everything."""
        if self.pending_update() != "restart" or not self.request_restart:
            return {"restarting": False}
        threading.Timer(0.3, self.request_restart).start()
        return {"restarting": True}

    def pending_update(self) -> str:
        """What it takes to run the build on disk: "" (nothing), "reload" (page only) or "restart"."""
        head = app_head()
        if not head or not self.loaded_head or head == self.loaded_head == self.page_head:
            return ""
        files = changed_files(self.loaded_head, head)
        if files is None or needs_restart(files):
            return "restart"  # when in doubt, a restart is always correct
        return "reload" if head != self.page_head else ""

    def update_waiting(self) -> bool:
        return self.pending_update() == "restart"


# ============================================================ the native app (Windows-first)

def port_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def wait_for_port(port: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not port_free(port):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    return True


def ensure_dependencies() -> None:
    """The window and tray libraries; installed on the fly if this machine lacks them."""
    try:
        import pystray  # noqa: F401
        import webview  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        log("installing " + " ".join(DEPENDENCIES))
        subprocess.run(
            [str(Path(sys.executable).with_name("python.exe")) if sys.platform == "win32" else sys.executable,
             "-m", "pip", "install", "--user", "--quiet", *DEPENDENCIES],
            creationflags=NO_WINDOW,
        )


def ensure_git_on_path() -> None:
    """Apps started from the Start menu can get a PATH from before Git was installed."""
    if shutil.which("git"):
        return
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    for folder in (local / "Programs" / "Git" / "cmd", program_files / "Git" / "cmd", APP_ROOT.parent / "git" / "cmd"):
        if (folder / "git.exe").exists():
            os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"
            return


class SingleInstance:
    """One Cairn per user. A second launch just asks the running one to show its window."""

    MUTEX = "Local\\juw12.Cairn.single-instance"
    SHOW_EVENT = "Local\\juw12.Cairn.show"

    def __init__(self):
        self.enabled = sys.platform == "win32"
        self.mutex = None
        self.event = None
        if not self.enabled:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32 = kernel32
        self.ctypes = ctypes

    def claim(self) -> bool:
        if not self.enabled:
            return True
        self.event = self.kernel32.CreateEventW(None, False, False, self.SHOW_EVENT)
        self.mutex = self.kernel32.CreateMutexW(None, False, self.MUTEX)
        if self.ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            self.release()
            return False
        return True

    def ask_running_copy_to_show(self) -> None:
        if not self.enabled:
            return
        event = self.kernel32.OpenEventW(0x0002, False, self.SHOW_EVENT)
        if event:
            self.kernel32.SetEvent(event)
            self.kernel32.CloseHandle(event)

    def wait_for_show(self, timeout_ms: int = 1000) -> bool:
        if not self.enabled or not self.event:
            time.sleep(timeout_ms / 1000)
            return False
        return self.kernel32.WaitForSingleObject(self.event, timeout_ms) == 0

    def release(self) -> None:
        if not self.enabled:
            return
        for handle in (self.event, self.mutex):
            if handle:
                self.kernel32.CloseHandle(handle)
        self.event = None
        self.mutex = None


class DesktopApp:
    def __init__(self, start_hidden: bool):
        self.start_hidden = start_hidden
        self.visible = not start_hidden
        self.quitting = False
        self.restart_requested = False
        self.restart_visible = False
        self.stopping = threading.Event()
        self.config = Config()
        self.hooks = DesktopHooks(self.config)
        self.window = None
        self.tray = None
        self.server = None
        self.instance = SingleInstance()

    # -------------------------------------------------- window
    def native_call(self, function) -> None:
        """Run on the window's UI thread (works even before the window was ever shown)."""
        try:
            from System import Action

            self.window.native.Invoke(Action(function))
        except Exception as error:  # non-Windows backends: fall back to pywebview's own calls
            log(f"native call failed: {error}")

    def set_icon(self) -> None:
        icon = APP_ROOT / "docs" / "icons" / "cairn.ico"
        if sys.platform != "win32" or not icon.exists():
            return

        def apply():
            from System.Drawing import Icon

            self.window.native.Icon = Icon(str(icon))

        self.native_call(apply)

    def show_window(self, *_):
        self.visible = True
        if sys.platform != "win32":
            self.window.show()
            return

        def bring_to_front():
            from System.Windows.Forms import FormWindowState

            form = self.window.native
            form.Show()
            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Normal
            form.Activate()

        self.native_call(bring_to_front)
        self.set_icon()

    def hide_window(self):
        self.visible = False
        if sys.platform == "win32":
            self.native_call(lambda: self.window.native.Hide())
        else:
            self.window.hide()
        if self.hooks.update_waiting():
            self.restart()

    def on_closing(self):
        if self.quitting:
            return True
        threading.Thread(target=self.hide_window, daemon=True).start()
        if not self.config.get("trayHintShown"):
            self.config.set("trayHintShown", True)
            try:
                self.tray.notify("Cairn keeps running in the tray. Right-click its icon to quit.", "Cairn")
            except Exception:
                pass
        return False

    def pick_folder(self):
        import webview

        dialog = getattr(webview, "FileDialog", None)
        kind = dialog.FOLDER if dialog else webview.FOLDER_DIALOG
        chosen = self.window.create_file_dialog(kind)
        return chosen[0] if chosen else None

    def reload_page(self):
        try:
            self.window.evaluate_js("window.cairn && window.cairn.reload && window.cairn.reload()")
        except Exception:
            pass

    # -------------------------------------------------- tray
    def build_tray(self):
        import pystray
        from PIL import Image

        icon_file = APP_ROOT / "docs" / "icons" / "icon-192.png"
        image = Image.open(icon_file) if icon_file.exists() else Image.new("RGB", (64, 64), "#d4572a")
        menu = pystray.Menu(
            pystray.MenuItem("Open Cairn", self.show_window, default=True),
            pystray.MenuItem("Sync now", lambda *_: threading.Thread(target=self.sync_once, args=(True,), daemon=True).start()),
            pystray.MenuItem(
                "Start with Windows",
                lambda *_: self.hooks.toggle_autostart(not autostart_enabled()),
                checked=lambda item: autostart_enabled(),
                visible=sys.platform == "win32",
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Cairn", self.quit),
        )
        return pystray.Icon("Cairn", image, "Cairn", menu)

    def quit(self, *_):
        self.quitting = True
        self.stopping.set()
        try:
            self.tray.stop()
        except Exception:
            pass
        self.window.destroy()

    def restart(self, show: bool | None = None):
        """Quit and start again on the new build; the window comes back if it was open."""
        log("restarting to apply an update")
        self.restart_requested = True
        self.restart_visible = self.visible if show is None else show
        self.quit()

    # -------------------------------------------------- background work
    def watch_show_requests(self):
        while not self.stopping.is_set():
            if self.instance.wait_for_show(1000):
                self.show_window()

    def sync_once(self, manual: bool = False) -> None:
        if not manual and not self.config.get("autoSync", True):
            return
        brain = self.server.RequestHandlerClass.brain
        try:
            status = brain.git_status()
            if not status["available"] or (not status["remote"] and not status["changes"]):
                return
            if not status["remote"]:
                return  # nothing to sync with; commits stay a manual choice in the app
            result = brain.git_sync()
            if result["pulled"] or result["committed"] or result["pushed"]:
                log(f"synced: committed={result['committed']} pulled={result['pulled']} pushed={result['pushed']}")
                self.reload_page()
        except SyncError as error:
            log(f"sync skipped: {error}")
            if manual and self.tray:
                try:
                    self.tray.notify(str(error), "Cairn couldn't sync")
                except Exception:
                    pass
        except Exception as error:  # never let a background sync take the app down
            log(f"sync failed: {error}")

    def sync_loop(self):
        wait = SYNC_FIRST_SECONDS
        while not self.stopping.wait(wait):
            wait = SYNC_EVERY_SECONDS
            self.sync_once()
            self.publish_once()

    def vibe_watch_loop(self):
        owner = watch.watch_owner("app")
        held = None
        wait = VIBE_WATCH_FIRST_SECONDS
        while not self.stopping.wait(wait):
            wait = watch.EVERY_SECONDS
            brain = self.server.RequestHandlerClass.brain
            if held is not None and held.root != brain.root:
                watch.release_watch(held, owner)  # switched brains: hand the old one back
            held = brain
            try:
                messages = vibe_watch_pass(brain, self.config, owner)
                for message in messages or []:
                    log(f"vibe: {message}")
            except watch.WatchError as error:
                log(f"vibe watch skipped: {error}")
            except Exception as error:  # never let a background pass take the app down
                log(f"vibe watch failed: {error}")
        if held is not None:
            watch.release_watch(held, owner)

    def publish_once(self) -> None:
        try:
            result = self.hooks.maybe_publish()
            if result and result.get("published"):
                log(f"published {result['changed']} change(s) to {self.config.get('publishTo')}")
        except SyncError as error:
            log(f"publish skipped: {error}")
        except Exception as error:  # never let a background publish take the app down
            log(f"publish failed: {error}")

    def update_loop(self):
        wait = UPDATE_FIRST_SECONDS
        while not self.stopping.wait(wait):
            wait = UPDATE_EVERY_SECONDS
            if not self.config.get("autoUpdate", True):
                continue
            if self.apply_update():
                return

    def apply_update(self) -> bool:
        """Download the latest build and put it to use without bothering anyone.

        Web-only changes: the page reloads itself as soon as you pause (your note stays open).
        Python changes: restart right away when Cairn is in the tray, otherwise the page asks
        for the restart once you've been idle for a while. Returns True when restarting now.
        """
        before = app_head()
        app_git("pull", "--ff-only", "--quiet")
        after = app_head()
        if before and after and before != after:
            log(f"downloaded update {before} -> {after}")
        kind = self.hooks.pending_update()
        if kind == "restart":
            if not self.visible:
                self.restart()
                return True
            self.ask_page_to_update(restart=True)
        elif kind == "reload":
            self.hooks.page_head = after
            log(f"reloading the page for update {after}")
            self.ask_page_to_update(restart=False)
        return False

    def ask_page_to_update(self, restart: bool) -> None:
        flag = "true" if restart else "false"
        # A page from before auto-update existed has no applyUpdate(): a plain reload brings in the
        # new web app, which handles every later update itself.
        fallback = "" if restart else " else location.reload();"
        script = f"if (window.cairn && window.cairn.applyUpdate) window.cairn.applyUpdate({{restart: {flag}}});{fallback}"
        try:
            self.window.evaluate_js(script)
        except Exception as error:
            log(f"could not ask the page to update: {error}")

    # -------------------------------------------------- run
    def run(self) -> None:
        if not self.instance.claim():
            if not self.start_hidden:
                self.instance.ask_running_copy_to_show()
            return
        try:
            self._run()
        finally:
            self.instance.release()
        if self.restart_requested:
            args = [pythonw_executable(), str(APP_ROOT / "Cairn.pyw")]
            if not self.restart_visible:
                args.append("--hidden")
            subprocess.Popen(
                args,
                cwd=str(APP_ROOT),
                creationflags=NO_WINDOW,
            )

    def _run(self) -> None:
        ensure_git_on_path()
        ensure_dependencies()
        import webview

        if sys.platform == "win32":
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
            threading.Thread(target=ensure_start_menu_shortcut, args=(self.config,), daemon=True).start()
            if self.config.get("autostart") is None:  # first run: always live, can be turned off
                try:
                    set_autostart(True)
                    self.config.set("autostart", True)
                except OSError:
                    pass

        folder = default_folder(self.config)
        brain = Brain(folder)
        if not brain.dir.exists():
            brain.init()
        self.config.remember_folder(folder.resolve())

        port = PORT if wait_for_port(PORT, 15) else 0
        self.server = make_server(brain, port=port, desktop=self.hooks)
        self.autopilot = attach_autopilot(self.server, brain)
        self.hooks.server = self.server
        self.hooks.pick_folder = self.pick_folder
        self.hooks.request_restart = self.restart
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.server.server_address[1]}/"

        self.window = webview.create_window(
            "Cairn", url, width=1440, height=920, min_size=(380, 560),
            text_select=True, hidden=self.start_hidden,
        )
        self.window.events.closing += self.on_closing
        self.window.events.shown += self.set_icon
        self.tray = self.build_tray()
        self.tray.run_detached()
        for loop in (self.watch_show_requests, self.sync_loop, self.update_loop, self.vibe_watch_loop):
            threading.Thread(target=loop, daemon=True).start()
        log(f"running {self.hooks.loaded_head or __version__} on {folder} ({'tray' if self.start_hidden else 'window'})")

        webview.start(private_mode=False, storage_path=str(config_dir() / "webview"))

        self.stopping.set()
        try:
            self.tray.stop()
        except Exception:
            pass
        self.autopilot.shutdown()
        self.server.shutdown()
        self.server.server_close()
        log("restarting" if self.restart_requested else "quit")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    DesktopApp(start_hidden="--hidden" in args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

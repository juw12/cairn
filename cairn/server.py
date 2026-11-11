"""Local JSON API + static web app. Standard library only.

Security model: binds to 127.0.0.1, checks the Host header (DNS rebinding) and requires an
`X-Cairn: 1` header on every API call, which browsers can't send cross-site without a CORS
preflight that this server never approves.

Phones reach the same app through a second, opt-in listener (cairn.phone) that swaps the Host
check for a paired-device cookie and has no /api/desktop.

The desktop app (cairn.desktop) plugs in a `desktop` object for the things only a native app
can do: open another folder, clone a repo, start with Windows, self-update. Plain `cairn serve`
has none of that and answers 404 for /api/desktop.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, autopilot, mcp, precedent, vibe
from .brain import ATTACHMENT_TYPES, MAX_ATTACHMENT_BYTES, Brain, ConflictError, PathError, SyncError
from .obsidian import ObsidianSync
from .obsidian import SyncError as ObsidianSyncError

PACKAGE_DIR = Path(__file__).resolve().parent
WEB_CANDIDATES = [PACKAGE_DIR / "web", PACKAGE_DIR.parent / "docs"]
MAX_BODY = 5_000_000


class ChangeFeed:
    """Which notes changed through this server, so every open page (PC window, paired phones) can pick up
    the others' edits within seconds. Each page sends a random X-Cairn-Client id and is not told about
    its own edits. In memory only: a restart or a brain switch tells pages to reload everything."""

    KEEP = 500

    def __init__(self):
        self.lock = threading.Lock()
        self.epoch = secrets.token_hex(4)  # changes when the server restarts or switches brain
        self.revision = 0
        self.entries: list[tuple[int, str, str]] = []  # (revision, path, client)

    def record(self, path: str, client: str) -> None:
        with self.lock:
            self.revision += 1
            self.entries.append((self.revision, path, client))
            del self.entries[:-self.KEEP]

    def reset(self) -> None:
        with self.lock:
            self.epoch = secrets.token_hex(4)
            self.revision = 0
            self.entries.clear()

    def since(self, revision: int, epoch: str, client: str) -> dict:
        with self.lock:
            oldest = self.entries[0][0] if self.entries else self.revision + 1
            stale = epoch != self.epoch or revision > self.revision or revision < oldest - 1
            paths = sorted({p for r, p, c in self.entries if r > revision and c != client}) if not stale else []
            return {"epoch": self.epoch, "revision": self.revision, "paths": paths, "reset": bool(stale and epoch)}


def find_web_dir() -> Path:
    for candidate in WEB_CANDIDATES:
        if (candidate / "index.html").exists():
            return candidate
    raise FileNotFoundError("web app files not found (expected docs/ or cairn/web/)")


class Handler(BaseHTTPRequestHandler):
    brain: Brain
    web_dir: Path
    allowed_hosts: set[str]
    desktop = None  # optional DesktopHooks: state() -> dict, act(action, payload) -> dict
    server_version = f"Cairn/{__version__}"

    def log_message(self, *args):  # keep the terminal quiet
        pass

    # ------------------------------------------------------------ helpers

    def _send_json(self, payload, status: int = 200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str, **extra):
        self._send_json({"error": message, **extra}, status)

    def _drain(self) -> None:
        """Read an unused request body so Windows does not reset the connection before the reply."""
        length = int(self.headers.get("Content-Length") or 0)
        if 0 < length <= MAX_BODY:
            self.rfile.read(length)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").lower() in self.allowed_hosts

    def _api_guard(self) -> bool:
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return False
        if self.headers.get("X-Cairn") != "1":
            self._error(HTTPStatus.FORBIDDEN, "missing X-Cairn header")
            return False
        return True

    def _client(self) -> str:
        return (self.headers.get("X-Cairn-Client") or "")[:64]

    def _query_path(self) -> str:
        return (parse_qs(urlparse(self.path).query).get("path") or [""])[0]

    # ------------------------------------------------------------ routes

    def _serve_attachment(self):
        """GET /api/raw?path=&from=: an image or file a note embeds.

        Plain <img> tags can't send the X-Cairn header, so this one route only checks the Host
        header, and it can only return files inside the brain with an attachment extension.
        Responses are sandboxed (an SVG can't run script) and can't be embedded cross-site.
        """
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
        query = parse_qs(urlparse(self.path).query)
        target = (query.get("path") or [""])[0]
        from_note = (query.get("from") or [""])[0]
        path = self.brain.find_attachment(target, from_note)
        if path is None:
            return self._error(HTTPStatus.NOT_FOUND, "no such attachment")
        size = path.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "attachment too large")
        data = path.read_bytes()
        content_type = ATTACHMENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if path.suffix.lower() == ".svg":
            # Opened on its own, an SVG is a page on this origin; sandbox it so it can't run script.
            self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/raw":
            return self._serve_attachment()
        if route.startswith("/api/"):
            if not self._api_guard():
                return
            brain = self.brain
            if route == "/api/info":
                self._send_json({
                    "mode": "server",
                    "version": __version__,
                    "project": brain.project_name,
                    "brainDir": brain._brain_pathspec(),
                    "exists": brain.dir.exists(),
                    "git": brain.git_available(),
                    "desktop": self.desktop is not None,
                    "path": str(brain.root) if self.desktop is not None else None,
                    "mcp": mcp.server_entry(brain),
                })
            elif route == "/api/files":
                self._send_json({"files": brain.files()})
            elif route == "/api/changes":
                query = parse_qs(urlparse(self.path).query)
                try:
                    since = int(query.get("since", ["0"])[0] or 0)
                except ValueError:
                    since = 0
                self._send_json(self.changes.since(since, query.get("epoch", [""])[0], self._client()))
            elif route == "/api/todos":
                self._send_json({"items": brain.scan_code()})
            elif route == "/api/git":
                self._send_json(brain.git_status())
            elif route == "/api/vibe":
                query = parse_qs(urlparse(self.path).query)
                since = query.get("since", [""])[0]
                base = query.get("base", [""])[0]
                self._send_json({"files": vibe.vibe_files(brain), "git": vibe.git_activity(brain, since, base)})
            elif route == "/api/autopilot":
                self._send_json(autopilot.state(self.autopilot))
            elif route == "/api/autopilot/log":
                query = parse_qs(urlparse(self.path).query)
                self._send_json(autopilot.read_log(query.get("id", [""])[0], int(query.get("since", ["0"])[0] or 0)))
            elif route == "/api/precedent/brief":
                query = parse_qs(urlparse(self.path).query)
                goal = query.get("goal", [""])[0]
                self._send_json({
                    "brief": precedent.brief(brain, goal),
                    "queue": precedent.queue_items(brain),
                    "libraryAgeDays": precedent.library_age_days(),
                })
            elif route == "/api/obsidian":
                self._send_json(self.obsidian.status())
            elif route == "/api/desktop" and self.desktop is not None:
                self._send_json(self.desktop.state())
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        self._serve_static(route)

    def do_PUT(self):
        if urlparse(self.path).path != "/api/files":
            return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        if not self._api_guard():
            return
        try:
            body = self._body()
            mtime = self.brain.write(self._query_path(), str(body.get("text", "")), body.get("mtime"))
            self.changes.record(self._query_path(), self._client())
            self._send_json({"path": self._query_path(), "mtime": mtime})
        except PathError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except ConflictError as error:
            self._error(HTTPStatus.CONFLICT, "conflict", text=error.current_text, mtime=error.current_mtime)
        except (ValueError, TypeError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))

    def do_DELETE(self):
        if urlparse(self.path).path != "/api/files":
            return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        if not self._api_guard():
            return
        try:
            self.brain.delete(self._query_path())
            self.changes.record(self._query_path(), self._client())
            self._send_json({"ok": True})
        except PathError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))

    def do_POST(self):
        route = urlparse(self.path).path
        if not self._api_guard():
            return
        if route == "/api/init":
            return self._send_json({"created": self.brain.init()})
        if route.startswith("/api/obsidian/"):
            return self._obsidian(route.rsplit("/", 1)[-1])
        if route.startswith("/api/autopilot/"):
            return self._autopilot(route.rsplit("/", 1)[-1])
        if route == "/api/git/commit":
            try:
                body = self._body()
                return self._send_json(self.brain.git_commit(str(body.get("message", "")).strip()))
            except (RuntimeError, ValueError) as error:
                return self._error(HTTPStatus.BAD_REQUEST, str(error))
        if route == "/api/git/sync":
            try:
                body = self._body()
                return self._send_json(self.brain.git_sync(str(body.get("message", "")).strip()))
            except (RuntimeError, ValueError) as error:
                return self._error(HTTPStatus.BAD_REQUEST, str(error))
        if route == "/api/desktop" and self.desktop is not None:
            try:
                body = self._body()
                return self._send_json(self.desktop.act(str(body.get("action", "")), body))
            except (SyncError, ValueError, OSError) as error:
                return self._error(HTTPStatus.BAD_REQUEST, str(error))
        self._drain()
        self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def _autopilot(self, action: str):
        """Goals, comments, stop/resume/delete and settings for the autopilot (state lives in ~/.cairn)."""
        try:
            body = self._body()
            job_id = str(body.get("id", ""))
            if action == "goal":
                return self._send_json(autopilot.create_job(str(body.get("goal", "")), str(body.get("workdir", ""))))
            if action == "comment":
                job = autopilot.add_comment(job_id, str(body.get("text", "")))
                return self._send_json(job) if job else self._error(HTTPStatus.NOT_FOUND, "no such job")
            if action == "stop":
                if self.autopilot:
                    self.autopilot.stop_job(job_id)
                else:
                    autopilot.update_job(job_id, status="stopped")
                return self._send_json({"ok": True})
            if action == "resume":
                return self._send_json(autopilot.update_job(job_id, status="queued", blocked_reason="") or {})
            if action == "delete":
                autopilot.delete_job(job_id)
                return self._send_json({"ok": True})
            if action == "settings":
                return self._send_json(autopilot.update_settings(body))
            if action == "queue":
                note = precedent.queue_add(self.brain, str(body.get("text", "")), bool(body.get("auto")), bool(body.get("needsMe")))
                return self._send_json(note)
            return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except (ValueError, TypeError, OSError) as error:
            return self._error(HTTPStatus.BAD_REQUEST, str(error))

    def _obsidian(self, action: str):
        sync = self.obsidian
        try:
            with self.sync_lock:
                if action == "link":
                    body = self._body()
                    sync.link(str(body.get("vault", "")), body.get("folder"))
                    report = sync.sync()
                elif action == "sync":
                    report = sync.sync()
                elif action == "unlink":
                    return self._send_json({"status": sync.unlink(), "report": None})
                else:
                    return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            self._send_json({"status": sync.status(), "report": report})
        except ObsidianSyncError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except (ValueError, TypeError, OSError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))

    def _serve_static(self, route: str):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
        relative = route.lstrip("/") or "index.html"
        target = (self.web_dir / relative).resolve()
        if self.web_dir not in target.parents or not target.is_file():
            target = self.web_dir / "index.html"
        data = target.read_bytes()
        if target.name == "index.html":
            data = data.replace(b"</head>", b'<meta name="cairn-server" content="1">\n</head>', 1)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".js", ".mjs"):
            content_type = "text/javascript"
        elif target.suffix == ".webmanifest":
            content_type = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._static_headers(target)
        self.end_headers()
        self.wfile.write(data)

    def _static_headers(self, target: Path) -> None:
        """Extra headers for a static file (the phone listener renews its cookie here)."""


def make_server(brain: Brain, port: int = 4777, host: str = "127.0.0.1", desktop=None) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Cairn only listens on localhost")
    handler = type("CairnHandler", (Handler,), {})
    handler.brain = brain
    handler.obsidian = ObsidianSync(brain)
    handler.sync_lock = threading.Lock()
    handler.desktop = desktop
    handler.autopilot = None
    handler.changes = ChangeFeed()
    handler.web_dir = find_web_dir().resolve()
    server = ThreadingHTTPServer((host, port), handler)
    actual_port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}", f"[::1]:{actual_port}"}
    return server


def attach_autopilot(server: ThreadingHTTPServer, brain: Brain) -> "autopilot.Autopilot":
    """Run the autopilot worker in this process (`cairn serve`, the desktop app). It idles while off."""
    worker = autopilot.Autopilot(brain)
    server.RequestHandlerClass.autopilot = worker
    worker.start()
    return worker


def switch_brain(server: ThreadingHTTPServer, brain: Brain) -> None:
    """Serve a different folder from now on (the desktop app's "Open a brain")."""
    server.RequestHandlerClass.brain = brain
    server.RequestHandlerClass.obsidian = ObsidianSync(brain)
    server.RequestHandlerClass.changes.reset()
    if server.RequestHandlerClass.autopilot is not None:
        server.RequestHandlerClass.autopilot.brain = brain


def serve(brain: Brain, port: int = 4777, open_browser: bool = True) -> None:
    server = make_server(brain, port)
    worker = attach_autopilot(server, brain)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Cairn {__version__} · {brain.project_name} · brain: {brain.dir}")
    print(f"Open {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        worker.shutdown()
        server.server_close()

"""Local JSON API + static web app. Standard library only.

Security model: binds to 127.0.0.1, checks the Host header (DNS rebinding) and requires an
`X-Cairn: 1` header on every API call, which browsers can't send cross-site without a CORS
preflight that this server never approves.

The desktop app (cairn.desktop) plugs in a `desktop` object for the things only a native app
can do: open another folder, clone a repo, start with Windows, self-update. Plain `cairn serve`
has none of that and answers 404 for /api/desktop.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, vibe
from .brain import ATTACHMENT_TYPES, MAX_ATTACHMENT_BYTES, Brain, ConflictError, PathError, SyncError
from .obsidian import ObsidianSync
from .obsidian import SyncError as ObsidianSyncError

PACKAGE_DIR = Path(__file__).resolve().parent
WEB_CANDIDATES = [PACKAGE_DIR / "web", PACKAGE_DIR.parent / "docs"]
MAX_BODY = 5_000_000


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
                })
            elif route == "/api/files":
                self._send_json({"files": brain.files()})
            elif route == "/api/todos":
                self._send_json({"items": brain.scan_code()})
            elif route == "/api/git":
                self._send_json(brain.git_status())
            elif route == "/api/vibe":
                query = parse_qs(urlparse(self.path).query)
                since = query.get("since", [""])[0]
                base = query.get("base", [""])[0]
                self._send_json({"files": vibe.vibe_files(brain), "git": vibe.git_activity(brain, since, base)})
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
        self.end_headers()
        self.wfile.write(data)


def make_server(brain: Brain, port: int = 4777, host: str = "127.0.0.1", desktop=None) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Cairn only listens on localhost")
    handler = type("CairnHandler", (Handler,), {})
    handler.brain = brain
    handler.obsidian = ObsidianSync(brain)
    handler.sync_lock = threading.Lock()
    handler.desktop = desktop
    handler.web_dir = find_web_dir().resolve()
    server = ThreadingHTTPServer((host, port), handler)
    actual_port = server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}", f"[::1]:{actual_port}"}
    return server


def switch_brain(server: ThreadingHTTPServer, brain: Brain) -> None:
    """Serve a different folder from now on (the desktop app's "Open a brain")."""
    server.RequestHandlerClass.brain = brain
    server.RequestHandlerClass.obsidian = ObsidianSync(brain)


def serve(brain: Brain, port: int = 4777, open_browser: bool = True) -> None:
    server = make_server(brain, port)
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
        server.server_close()

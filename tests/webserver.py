"""Serve docs/ as plain static files (the GitHub Pages runtime) for browser tests."""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

DOCS = Path(__file__).resolve().parents[1] / "docs"


class QuietHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".webmanifest": "application/manifest+json",
    }

    def log_message(self, *args):
        pass


def start_static_server() -> tuple[ThreadingHTTPServer, str]:
    handler = functools.partial(QuietHandler, directory=str(DOCS))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def launch_browser(playwright):
    """Bundled Chromium if installed, else the system's Edge or Chrome."""
    for options in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return playwright.chromium.launch(**options)
        except Exception:  # noqa: BLE001 - try the next browser
            continue
    raise RuntimeError("no Chromium-based browser available for Playwright")


BRAIN = Path(__file__).resolve().parents[1] / "brain"
MOCK_USER = {
    "login": "juw12",
    "name": "Juwen Lie",
    "avatar_url": "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
    "bio": "R&D mechanical engineer who ships software.",
    "location": "Batam, Indonesia",
    "blog": "https://juw12.github.io/",
    "html_url": "https://github.com/juw12",
    "created_at": "2019-03-01T00:00:00Z",
    "type": "User",
}
MOCK_REPO = {
    "name": "cairn",
    "full_name": "juw12/cairn",
    "owner": {"login": "juw12", "avatar_url": MOCK_USER["avatar_url"]},
    "description": "A second brain that lives inside your repo.",
    "html_url": "https://github.com/juw12/cairn",
    "default_branch": "main",
    "stargazers_count": 3,
    "pushed_at": "2026-11-05T10:00:00Z",
}


def install_github_mock(context) -> list[str]:
    """Serve GitHub API + raw file requests from the local brain/ folder. Returns the request log."""
    log: list[str] = []
    brain_files = sorted(p.relative_to(BRAIN.parent).as_posix() for p in BRAIN.rglob("*.md"))

    def fulfill_json(route, payload, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload),
                      headers={"Access-Control-Allow-Origin": "*"})

    def api(route):
        url = urlparse(route.request.url)
        path = url.path
        log.append(route.request.url)
        if path == "/users/juw12":
            return fulfill_json(route, MOCK_USER)
        if path.startswith("/users/"):
            return fulfill_json(route, {"message": "Not Found"}, 404)
        if path == "/search/repositories":
            query = unquote(url.query)
            items = [MOCK_REPO] if ("topic:cairn-brain" in query and ("user:juw12" in query or "user:" not in query)) else []
            return fulfill_json(route, {"total_count": len(items), "items": items})
        if path == "/repos/juw12/cairn":
            return fulfill_json(route, MOCK_REPO)
        if path.startswith("/repos/juw12/cairn/git/trees/"):
            tree = [{"path": p, "type": "blob"} for p in brain_files + ["README.md", "cairn/core.py"]]
            return fulfill_json(route, {"tree": tree})
        return fulfill_json(route, {"message": "Not Found"}, 404)

    def raw(route):
        path = unquote(urlparse(route.request.url).path)
        prefix = "/juw12/cairn/main/"
        if not path.startswith(prefix):
            return route.fulfill(status=404, body="")
        target = BRAIN.parent / path[len(prefix):]
        if not target.is_file():
            return route.fulfill(status=404, body="")
        route.fulfill(status=200, content_type="text/plain; charset=utf-8", body=target.read_text(encoding="utf-8"),
                      headers={"Access-Control-Allow-Origin": "*"})

    context.route("https://api.github.com/**", api)
    context.route("https://raw.githubusercontent.com/**", raw)
    return log

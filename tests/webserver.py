"""Serve docs/ as plain static files (the GitHub Pages runtime) for browser tests."""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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

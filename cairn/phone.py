"""Pair a phone: watch Cairn from your phone after scanning a QR code (like `opencode pair`).

The desktop app's own server stays on 127.0.0.1. Turning on phone access adds a second listener
on the local network (fixed port 47778, so a paired phone's bookmark keeps working) that serves
the same web app and API, with three differences:

* every request needs a paired device's cookie (no cookie: a "scan the QR code" page);
* there is no Host check (the phone uses this PC's IP) - the cookie is what proves who's asking,
  and a DNS-rebinding page never has it because cookies belong to the address the phone paired with;
* /api/desktop (open folders, clone, update, pair more phones) does not exist there.

Pairing: the desktop shows a QR code for http://<this PC>:47778/pair#<code>. The code is single-use
and lasts 5 minutes; it sits in the URL fragment, so it never reaches a log or proxy. The /pair page
posts it back and gets a random device cookie (400 days, renewed while used). Only a hash of each
device token is stored, in devices.json next to the app's settings. "Unpair all" deletes them.

Plain HTTP: on your own Wi-Fi or a Tailscale network that is fine, on a public network it is not.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PHONE_PORT = 47778
CODE_SECONDS = 5 * 60
COOKIE = "cairn_device"
COOKIE_MAX_AGE = 400 * 24 * 3600  # the longest a browser keeps a cookie; renewed on every page load
SEEN_WRITE_SECONDS = 3600  # write "last seen" at most hourly


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ============================================================ paired devices on disk

class DeviceStore:
    """devices.json: {"devices": [{"id", "hash", "name", "paired", "seen"}]}. Tokens never stored."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._written: dict[str, float] = {}

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [d for d in data.get("devices", []) if isinstance(d, dict) and d.get("hash")]
        except (OSError, ValueError):
            return []

    def _save(self, devices: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"devices": devices}, indent=2), encoding="utf-8")

    def devices(self) -> list[dict]:
        with self.lock:
            return [{k: d.get(k) for k in ("id", "name", "paired", "seen")} for d in self._load()]

    def add(self, name: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            devices = self._load()
            devices.append({
                "id": secrets.token_hex(4),
                "hash": _hash(token),
                "name": (name or "Phone")[:60],
                "paired": _now(),
                "seen": _now(),
            })
            self._save(devices)
        return token

    def check(self, token: str) -> bool:
        if not token:
            return False
        wanted = _hash(token)
        with self.lock:
            devices = self._load()
            match = next((d for d in devices if hmac.compare_digest(d["hash"], wanted)), None)
            if match is None:
                return False
            if time.monotonic() - self._written.get(match["id"], -SEEN_WRITE_SECONDS) >= SEEN_WRITE_SECONDS:
                match["seen"] = _now()
                self._written[match["id"]] = time.monotonic()
                try:
                    self._save(devices)
                except OSError:
                    pass
            return True

    def clear(self) -> None:
        with self.lock:
            self._save([])


# ============================================================ where the phone can reach this PC

def lan_addresses() -> list[str]:
    """This PC's IPv4 addresses a phone could use, best first: Tailscale, then home-style LANs."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:  # the address of the default route (no packet is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            found.add(probe.getsockname()[0])
    except OSError:
        pass

    def rank(address: str) -> tuple:
        ip = ipaddress.ip_address(address)
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return (0, address)  # Tailscale: works away from home too
        if ip in ipaddress.ip_network("192.168.0.0/16"):
            return (1, address)
        if ip in ipaddress.ip_network("172.16.0.0/12"):
            return (2, address)
        if ip.is_private:
            return (3, address)
        return (4, address)

    usable = [a for a in found if not ipaddress.ip_address(a).is_loopback and not ipaddress.ip_address(a).is_link_local]
    return sorted(usable, key=rank)


def qr_svg(text: str) -> str | None:
    """An SVG QR code, or None when the `segno` library isn't installed."""
    try:
        import segno
    except ImportError:
        return None
    return segno.make(text, error="m").svg_inline(scale=6, border=2, dark="#1b1a17", light="#ffffff")


# ============================================================ the phone listener

PAIR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pair with Cairn</title>
<style>body{font:16px/1.5 system-ui,sans-serif;background:#1b1a17;color:#f2efe8;display:grid;place-items:center;
min-height:100vh;margin:0;padding:24px;box-sizing:border-box;text-align:center}p{max-width:22rem}b{color:#e8784a}</style>
</head><body><main><h1>Cairn</h1><p id="msg">__MESSAGE__</p></main>
<script>
(function () {
  var code = location.hash.slice(1);
  var msg = document.getElementById("msg");
  if (!code) return;
  msg.textContent = "Pairing\u2026";
  fetch("/api/pair", {method: "POST", headers: {"X-Cairn": "1", "Content-Type": "application/json"},
    body: JSON.stringify({code: code, name: navigator.userAgent})})
    .then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); })
    .then(function (res) {
      if (res[0]) { history.replaceState(null, "", "/pair"); location.replace("/#/vibe"); }
      else msg.textContent = res[1].error || "Pairing failed.";
    })
    .catch(function () { msg.textContent = "Couldn't reach Cairn on your PC."; });
})();
</script></body></html>"""

NOT_PAIRED = ("This phone isn't paired yet. On your PC open Cairn, then <b>Settings &gt; Pair a phone</b> "
              "(or the tray icon) and scan the QR code.")


class PhoneAccess:
    """Owns the phone listener, the pending pairing codes and the paired devices."""

    def __init__(self, server: ThreadingHTTPServer, devices_file: Path, port: int = PHONE_PORT, host: str = "0.0.0.0"):
        self.main = server
        self.store = DeviceStore(devices_file)
        self.port = port
        self.host = host
        self.listener: ThreadingHTTPServer | None = None
        self.error = ""
        self._codes: dict[str, float] = {}  # hash of a pairing code -> expiry (monotonic)
        self._lock = threading.Lock()

    # -------------------------------------------------- listener
    @property
    def running(self) -> bool:
        return self.listener is not None

    def start(self) -> None:
        if self.listener is not None:
            return
        handler = make_phone_handler(self.main.RequestHandlerClass, self)
        try:
            self.listener = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as error:
            self.error = f"Port {self.port} is busy or blocked: {error}"
            raise
        self.error = ""
        self.port = self.listener.server_address[1]
        threading.Thread(target=self.listener.serve_forever, daemon=True).start()

    def stop(self) -> None:
        listener, self.listener = self.listener, None
        if listener is not None:
            listener.shutdown()
            listener.server_close()

    # -------------------------------------------------- pairing
    def addresses(self) -> list[str]:
        return [f"http://{ip}:{self.port}" for ip in lan_addresses()]

    def new_pairing(self) -> dict:
        """A fresh single-use code; returns the URL to put in the QR code and the code's lifetime."""
        self.start()
        code = secrets.token_urlsafe(16)
        with self._lock:
            now = time.monotonic()
            self._codes = {h: t for h, t in self._codes.items() if t > now}
            self._codes[_hash(code)] = now + CODE_SECONDS
        bases = self.addresses() or [f"http://127.0.0.1:{self.port}"]
        url = f"{bases[0]}/pair#{code}"
        return {
            "url": url,
            "qr": qr_svg(url),
            "others": [f"{base}/pair#{code}" for base in bases[1:]],
            "expiresIn": CODE_SECONDS,
        }

    def redeem(self, code: str, name: str) -> str | None:
        """Trade a pairing code for a device token (once)."""
        with self._lock:
            expiry = self._codes.pop(_hash(code or ""), None)
        if expiry is None or expiry < time.monotonic():
            return None
        return self.store.add(_device_name(name))

    def unpair_all(self) -> None:
        with self._lock:
            self._codes.clear()
        self.store.clear()

    def status(self) -> dict:
        return {
            "running": self.running,
            "port": self.port,
            "addresses": self.addresses() if self.running else [],
            "devices": self.store.devices(),
            "error": self.error,
        }


def _device_name(user_agent: str) -> str:
    ua = user_agent or ""
    for needle, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android phone")):
        if needle in ua:
            return name
    return "Browser"


def make_phone_handler(base: type, phone: PhoneAccess) -> type:
    """The app's request handler, behind a device cookie. Subclassing `base` means switching
    brains in the desktop app (which sets attributes on `base`) switches the phone too."""

    class PhoneHandler(base):
        desktop = None  # no /api/desktop from a phone

        def _token(self) -> str:
            for part in (self.headers.get("Cookie") or "").split(";"):
                key, _, value = part.strip().partition("=")
                if key == COOKIE:
                    return value
            return ""

        def _host_ok(self) -> bool:  # every route goes through this: here it means "a paired device"
            return phone.store.check(self._token())

        def _page(self, status: int, message: str, cookie: str = "") -> None:
            data = PAIR_PAGE.replace("__MESSAGE__", message).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/pair":
                return self._page(200, NOT_PAIRED)
            if not route.startswith("/api/") and not self._host_ok():
                return self._page(HTTPStatus.UNAUTHORIZED, NOT_PAIRED)
            return super().do_GET()

        def do_POST(self):
            if urlparse(self.path).path == "/api/pair":
                return self._pair()
            return super().do_POST()

        def _pair(self):
            if self.headers.get("X-Cairn") != "1":
                self._drain()
                return self._error(HTTPStatus.FORBIDDEN, "missing X-Cairn header")
            try:
                body = self._body()
            except ValueError as error:
                return self._error(HTTPStatus.BAD_REQUEST, str(error))
            token = phone.redeem(str(body.get("code", "")), str(body.get("name", "")))
            if token is None:
                return self._error(HTTPStatus.FORBIDDEN, "This QR code has expired or was already used. Make a new one in Cairn on your PC.")
            data = json.dumps({"paired": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", self._cookie(token))
            self.end_headers()
            self.wfile.write(data)

        @staticmethod
        def _cookie(token: str) -> str:
            return f"{COOKIE}={token}; Max-Age={COOKIE_MAX_AGE}; Path=/; HttpOnly; SameSite=Lax"

        def _static_headers(self, target: Path) -> None:
            # Renew the cookie whenever the app itself loads, so a phone in use never gets logged out.
            if target.name == "index.html":
                self.send_header("Set-Cookie", self._cookie(self._token()))

    PhoneHandler.__name__ = "CairnPhoneHandler"
    return PhoneHandler

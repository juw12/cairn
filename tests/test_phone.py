"""Pair a phone: QR code -> single-use code -> device cookie; everything else stays locked."""

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cairn import desktop, phone
from cairn.brain import Brain
from cairn.server import make_server, switch_brain


class PhoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.folder = self.tmp / "brain one"
        self.folder.mkdir()
        brain = Brain(self.folder)
        brain.init()
        self.server = make_server(brain, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.devices_file = self.tmp / "devices.json"
        self.phone = phone.PhoneAccess(self.server, self.devices_file, port=0, host="127.0.0.1")
        self.addCleanup(self.phone.stop)

    # -------------------------------------------------- helpers
    def call(self, method, path, body=None, cookie="", port=None, host=None, x_cairn=True):
        port = port or self.phone.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        headers = {"Content-Type": "application/json"}
        if x_cairn:
            headers["X-Cairn"] = "1"
        if cookie:
            headers["Cookie"] = f"{phone.COOKIE}={cookie}"
        if host:
            headers["Host"] = host
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response, raw

    def pair(self):
        pairing = self.phone.new_pairing()
        code = pairing["url"].split("#", 1)[1]
        response, raw = self.call("POST", "/api/pair", {"code": code, "name": "Mozilla/5.0 (iPhone)"})
        self.assertEqual(response.status, 200, raw)
        cookie = response.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        return cookie.split(";", 1)[0].split("=", 1)[1], code

    # -------------------------------------------------- tests
    def test_unpaired_phone_sees_only_the_pair_page(self):
        self.phone.start()
        response, raw = self.call("GET", "/")
        self.assertEqual(response.status, 401)
        self.assertIn(b"isn't paired yet", raw)
        response, _ = self.call("GET", "/api/files")
        self.assertEqual(response.status, 403)
        response, _ = self.call("GET", "/api/raw?path=x.png")
        self.assertEqual(response.status, 403)
        response, _ = self.call("PUT", "/api/files?path=ideas/x.md", {"text": "hi"})
        self.assertEqual(response.status, 403)
        response, raw = self.call("GET", "/pair")
        self.assertEqual(response.status, 200)

    def test_pairing_gives_a_working_cookie_once(self):
        token, code = self.pair()
        response, raw = self.call("GET", "/api/info", cookie=token, host="192.168.1.20:47778")
        self.assertEqual(response.status, 200, "any Host works on the phone listener once paired")
        info = json.loads(raw)
        self.assertFalse(info["desktop"], "a phone never gets the desktop controls")
        response, raw = self.call("GET", "/api/files", cookie=token)
        self.assertEqual(response.status, 200)
        response, raw = self.call("GET", "/", cookie=token)
        self.assertEqual(response.status, 200)
        self.assertIn(b'name="cairn-server"', raw)
        self.assertIn(token, response.getheader("Set-Cookie"), "loading the app renews the cookie")
        # The code is single-use.
        response, raw = self.call("POST", "/api/pair", {"code": code})
        self.assertEqual(response.status, 403)
        self.assertIn(b"expired or was already used", raw)

    def test_pair_needs_the_custom_header(self):
        pairing = self.phone.new_pairing()
        code = pairing["url"].split("#", 1)[1]
        response, _ = self.call("POST", "/api/pair", {"code": code}, x_cairn=False)
        self.assertEqual(response.status, 403)

    def test_desktop_api_is_not_on_the_phone_listener(self):
        token, _ = self.pair()
        response, _ = self.call("POST", "/api/desktop", {"action": "phone-pair"}, cookie=token)
        self.assertEqual(response.status, 404)
        response, _ = self.call("GET", "/api/desktop", cookie=token)
        self.assertEqual(response.status, 404)

    def test_expired_code_is_refused(self):
        with mock.patch.object(phone, "CODE_SECONDS", -1):
            pairing = self.phone.new_pairing()
        code = pairing["url"].split("#", 1)[1]
        response, _ = self.call("POST", "/api/pair", {"code": code})
        self.assertEqual(response.status, 403)

    def test_wrong_cookie_and_unpair_all(self):
        token, _ = self.pair()
        response, _ = self.call("GET", "/api/files", cookie="not-a-real-token")
        self.assertEqual(response.status, 403)
        self.phone.unpair_all()
        response, _ = self.call("GET", "/api/files", cookie=token)
        self.assertEqual(response.status, 403)
        self.assertEqual(self.phone.status()["devices"], [])

    def test_only_a_hash_of_the_token_is_stored(self):
        token, _ = self.pair()
        stored = self.devices_file.read_text(encoding="utf-8")
        self.assertNotIn(token, stored)
        devices = self.phone.status()["devices"]
        self.assertEqual([d["name"] for d in devices], ["iPhone"])
        self.assertNotIn("hash", devices[0])

    def test_switching_brains_switches_the_phone_too(self):
        token, _ = self.pair()
        other = self.tmp / "brain two"
        other.mkdir()
        switch_brain(self.server, Brain(other))
        response, raw = self.call("GET", "/api/info", cookie=token)
        self.assertEqual(json.loads(raw)["project"], "brain two")

    def test_local_server_still_checks_the_host(self):
        self.phone.start()
        response, _ = self.call("GET", "/api/info", port=self.server.server_address[1], host="192.168.1.20:47777")
        self.assertEqual(response.status, 403)

    def test_addresses_skip_loopback(self):
        for address in phone.lan_addresses():
            self.assertFalse(address.startswith("127."))


class DesktopPhoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        folder = self.tmp / "vault"
        folder.mkdir()
        brain = Brain(folder)
        brain.init()
        self.config = desktop.Config(self.tmp / "config.json")
        self.hooks = desktop.DesktopHooks(self.config)
        self.server = make_server(brain, port=0, desktop=self.hooks)
        self.hooks.server = self.server
        self.hooks.phone = phone.PhoneAccess(self.server, self.tmp / "devices.json", port=0, host="127.0.0.1")
        self.addCleanup(self.hooks.phone.stop)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def act(self, action, **payload):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=15)
        conn.request("POST", "/api/desktop", body=json.dumps({"action": action, **payload}),
                     headers={"X-Cairn": "1", "Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def test_pair_turns_phone_access_on_and_returns_a_qr_code(self):
        self.assertFalse(self.hooks.state()["phoneAccess"])
        status, result = self.act("phone-pair")
        self.assertEqual(status, 200, result)
        self.assertIn("/pair#", result["url"])
        self.assertEqual(result["expiresIn"], phone.CODE_SECONDS)
        try:
            import segno  # noqa: F401
            self.assertTrue(result["qr"].startswith("<svg"))
        except ImportError:
            self.assertIsNone(result["qr"])
        self.assertTrue(result["phone"]["running"])
        self.assertTrue(self.config.get("phoneAccess"))
        self.assertTrue(self.hooks.state()["phone"]["running"])

    def test_access_off_stops_the_listener_but_keeps_devices(self):
        self.act("phone-pair")
        status, result = self.act("phone-access", on=False)
        self.assertEqual(status, 200)
        self.assertFalse(result["phone"]["running"])
        self.assertFalse(self.config.get("phoneAccess"))
        status, result = self.act("phone-unpair")
        self.assertEqual((status, result["phone"]["devices"]), (200, []))


if __name__ == "__main__":
    unittest.main()

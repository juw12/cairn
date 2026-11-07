"""Cairn Pro license server: the Worker (run in Node against a fake PayPal) and the app's
claim / restore path (against a fake local license server)."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cairn import pro
from cairn.pro import License, generate_keypair, sign_key, verify_key

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "worker" / "pro" / "test" / "run_claim.mjs"
SELLER = generate_keypair(1024)
PUBLIC = {"n": SELLER["n"], "e": SELLER["e"]}
NODE = os.environ.get("CAIRN_NODE") or shutil.which("node")


def paypal_order(plan="month", value="9.00", status="COMPLETED", email="Buyer@Example.com"):
    return {
        "id": "5O190127TN364715T",
        "status": status,
        "payer": {"email_address": email},
        "purchase_units": [{
            "custom_id": plan,
            "payments": {"captures": [{
                "status": status,
                "amount": {"currency_code": "USD", "value": value},
                "create_time": "2026-11-07T08:00:00Z",
            }]},
        }],
    }


@unittest.skipUnless(NODE, "Node is not installed (set CAIRN_NODE to its path)")
class WorkerTests(unittest.TestCase):
    def run_worker(self, **payload):
        payload.setdefault("signingKey", {name: str(value) for name, value in SELLER.items()})
        done = subprocess.run(
            [NODE, str(HARNESS)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def claim(self, order, order_id="5o190127tn364715t", email="buyer@example.com", **extra):
        return self.run_worker(order=order, request={"orderId": order_id, "email": email}, **extra)

    def test_plans_match_the_app(self):
        plans = self.run_worker(plans=True)
        self.assertEqual(
            plans,
            {plan["id"]: {"price": plan["price"], "days": plan["days"]} for plan in pro.PLANS},
        )

    def test_key_signed_in_javascript_verifies_in_python(self):
        result = self.claim(paypal_order())
        self.assertEqual(result["status"], 200, result)
        payload = verify_key(result["body"]["key"], PUBLIC, today=date(2026, 11, 8))
        self.assertEqual(payload["id"], "5O190127TN364715T")
        self.assertEqual(payload["email"], "buyer@example.com")
        self.assertEqual(payload["plan"], "month")
        self.assertEqual(payload["issued"], "2026-11-07")
        self.assertEqual(payload["expires"], "2026-12-07")
        self.assertTrue(any("sandbox" in url for url in result["calls"]))

    def test_lifetime_key_never_ends(self):
        result = self.claim(paypal_order("lifetime", "49.00"))
        payload = verify_key(result["body"]["key"], PUBLIC, today=date(2040, 1, 1))
        self.assertEqual(payload["expires"], "")

    def test_refusals(self):
        cases = [
            (dict(order=paypal_order(), email="someone@else.com"), 403),
            (dict(order=paypal_order(), email=""), 400),
            (dict(order=paypal_order(status="PENDING")), 402),
            (dict(order=paypal_order(value="1.00")), 400),
            (dict(order=paypal_order(plan="mug")), 400),
            (dict(order=None), 404),
            (dict(order=paypal_order(), order_id="x"), 400),
            (dict(order=paypal_order(), env={"PAYPAL_SECRET": ""}), 503),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments, expected=expected):
                result = self.claim(**arguments)
                self.assertEqual(result["status"], expected, result)
                self.assertTrue(result["body"]["error"])
                self.assertNotIn("key", result["body"])


class FakeLicenseServer:
    """Stands in for the Worker: signs a key for order ORDER / e-mail EMAIL, refuses the rest."""

    ORDER = "5O190127TN364715T"
    EMAIL = "buyer@example.com"

    def __init__(self, plan="lifetime", fail=None):
        owner = self
        self.plan = plan
        self.fail = fail
        self.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                if owner.fail:
                    return self.reply(owner.fail, {"error": "The Cairn Pro license server is not set up yet."})
                if body.get("orderId") != owner.ORDER:
                    return self.reply(404, {"error": "PayPal does not know that order number."})
                if body.get("email", "").lower() != owner.EMAIL:
                    return self.reply(403, {"error": "That e-mail does not match the PayPal order."})
                expires = "" if owner.plan == "lifetime" else "2026-12-07"
                key = sign_key({"product": "cairn-pro", "id": owner.ORDER, "email": owner.EMAIL, "plan": owner.plan,
                                "issued": "2026-11-07", "expires": expires}, SELLER)
                self.reply(200, {"key": key, "plan": owner.plan, "expires": expires})

            def reply(self, status, data):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.server = FakeLicenseServer()
        self.today = lambda: date(2026, 11, 8)

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.home, ignore_errors=True)

    def license(self, home=None, server=None):
        return License(home or self.home, public_key=PUBLIC, today=self.today,
                       server=self.server.url if server is None else server)

    def purchase(self, license, plan="lifetime", price="49.00"):
        return license.record_purchase({
            "orderId": FakeLicenseServer.ORDER,
            "status": "COMPLETED",
            "plan": plan,
            "amount": {"currency_code": "USD", "value": price},
            "email": "Buyer@Example.com",
        })

    def test_purchase_fetches_a_portable_key(self):
        license = self.license()
        status = self.purchase(license)
        self.assertTrue(status["pro"])
        license.claim_thread.join(30)
        status = license.status()
        self.assertTrue(status["key"].startswith("CAIRN1."))
        self.assertFalse(status["keyPending"])
        self.assertEqual(self.server.requests[-1]["email"], "Buyer@Example.com")
        # That key alone turns Pro on on another computer.
        other = Path(tempfile.mkdtemp())
        try:
            moved = self.license(home=other, server="").activate(status["key"])
            self.assertTrue(moved["pro"])
            self.assertEqual(moved["planName"], "Lifetime")
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_restore_on_a_new_computer(self):
        status = self.license().restore(" 5o19 0127tn364715t ", "Buyer@example.com")
        self.assertTrue(status["pro"])
        self.assertEqual(status["source"], "key")
        self.assertEqual(status["order"], FakeLicenseServer.ORDER)
        self.assertTrue(status["key"])

    def test_restore_refusals_are_plain(self):
        license = self.license()
        with self.assertRaisesRegex(ValueError, "does not match"):
            license.restore(FakeLicenseServer.ORDER, "thief@example.com")
        with self.assertRaisesRegex(ValueError, "does not know"):
            license.restore("ABCDEFGH1234", FakeLicenseServer.EMAIL)
        with self.assertRaisesRegex(ValueError, "order number"):
            license.restore("12", FakeLicenseServer.EMAIL)
        with self.assertRaisesRegex(ValueError, "e-mail"):
            license.restore(FakeLicenseServer.ORDER, "nobody")
        self.assertFalse(license.status()["pro"])

    def test_restore_needs_a_server(self):
        with self.assertRaisesRegex(ValueError, "Paste a Pro key"):
            self.license(server="").restore(FakeLicenseServer.ORDER, FakeLicenseServer.EMAIL)

    def test_server_down_keeps_pro_and_retries_hourly(self):
        self.server.fail = 503
        license = self.license()
        self.purchase(license)
        license.claim_thread.join(30)
        status = license.status()
        self.assertTrue(status["pro"])
        self.assertTrue(status["keyPending"])
        self.assertIn("not set up", status["keyError"])
        asked = len(self.server.requests)
        license.status()
        self.assertEqual(len(self.server.requests), asked, "must not retry within the hour")
        # An hour later the server is up again: the next status() fetches the key in the background.
        self.server.fail = None
        record = json.loads(license.path.read_text(encoding="utf-8"))
        record["claim"]["tried"] = time.time() - pro.CLAIM_RETRY_SECONDS - 1
        license.path.write_text(json.dumps(record), encoding="utf-8")
        license.status()
        license.claim_thread.join(30)
        self.assertTrue(license.status()["key"])

    def test_unreachable_server_is_a_connection_error(self):
        with self.assertRaises(ConnectionError):
            pro.claim_key(FakeLicenseServer.ORDER, FakeLicenseServer.EMAIL, server="http://127.0.0.1:9", timeout=2)

    def test_longer_pass_wins_over_a_shorter_key(self):
        # Two 30-day passes bought here stack to 60 days; the server key for one order says 30.
        self.server.plan = "month"
        license = self.license()
        for _ in range(2):
            self.purchase(license, "month", "9.00")
            license.claim_thread.join(30)
        status = license.status()
        self.assertEqual(status["expires"], "2027-01-07")
        self.assertTrue(status["key"])

    def test_custom_public_key_means_no_server_by_default(self):
        self.assertEqual(License(self.home, public_key=PUBLIC).server, "")
        self.assertEqual(License(self.home).server, pro.LICENSE_SERVER)


if __name__ == "__main__":
    unittest.main()

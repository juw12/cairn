"""Cairn Pro: signed keys checked offline, in-app purchase receipts, plans, seller CLI."""

import contextlib
import io
import json
import os
import shutil
import socket
import tempfile
import unittest
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from cairn import cli, pro
from cairn.pro import License, generate_keypair, issue_key, sign_key, verify_key

REPO = Path(__file__).resolve().parents[1]
SELLER = generate_keypair(1024)
PUBLIC = {"n": SELLER["n"], "e": SELLER["e"]}
OTHER = generate_keypair(1024)


def no_network(*args, **kwargs):
    raise AssertionError("Pro must never touch the network")


class KeyTests(unittest.TestCase):
    def test_round_trip_and_payload(self):
        key = issue_key("ana@example.com", "lifetime", SELLER)
        payload = verify_key(key, PUBLIC)
        self.assertEqual(payload["email"], "ana@example.com")
        self.assertEqual(payload["plan"], "lifetime")
        self.assertEqual(payload["expires"], "")

    def test_works_with_the_network_blocked(self):
        key = issue_key("ana@example.com", "month", SELLER)
        with mock.patch.object(urllib.request, "urlopen", no_network), \
                mock.patch.object(socket, "create_connection", no_network):
            home = Path(tempfile.mkdtemp())
            try:
                status = License(home, public_key=PUBLIC).activate(key)
            finally:
                shutil.rmtree(home, ignore_errors=True)
        self.assertTrue(status["pro"])

    def test_tampered_key_is_rejected(self):
        key = issue_key("ana@example.com", "month", SELLER)
        prefix, body, signature = key.split(".")
        payload = json.loads(pro._b64d(body))
        payload["plan"] = "lifetime"
        payload["expires"] = ""
        forged = ".".join([prefix, pro._b64e(json.dumps(payload).encode()), signature])
        with self.assertRaisesRegex(ValueError, "not genuine"):
            verify_key(forged, PUBLIC)

    def test_key_from_another_signer_is_rejected(self):
        key = issue_key("ana@example.com", "lifetime", OTHER)
        with self.assertRaisesRegex(ValueError, "not genuine"):
            verify_key(key, PUBLIC)

    def test_expired_key_is_rejected(self):
        key = issue_key("ana@example.com", "month", SELLER, today=date(2026, 1, 1))
        self.assertEqual(verify_key(key, PUBLIC, today=date(2026, 1, 31))["plan"], "month")
        with self.assertRaisesRegex(ValueError, "ended on 2026-01-31"):
            verify_key(key, PUBLIC, today=date(2026, 2, 1))

    def test_garbage_and_other_products(self):
        for junk in ("", "hello", "CAIRN1.abc", "CAIRN1.!!!.???"):
            with self.assertRaises(ValueError):
                verify_key(junk, PUBLIC)
        other_product = sign_key({"product": "something-else", "plan": "lifetime"}, SELLER)
        with self.assertRaisesRegex(ValueError, "different product"):
            verify_key(other_product, PUBLIC)

    def test_whitespace_from_copy_paste_is_ignored(self):
        key = issue_key("ana@example.com", "lifetime", SELLER)
        wrapped = "\n".join(key[i:i + 60] for i in range(0, len(key), 60))
        self.assertEqual(verify_key(wrapped, PUBLIC)["email"], "ana@example.com")


class PurchaseTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.day = date(2026, 11, 7)
        self.license = License(self.home, public_key=PUBLIC, paypal_client_id="client-123", today=lambda: self.day)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def order(self, plan="lifetime", value="49.00", status="COMPLETED", order_id="5O190127TN364715T"):
        return {"orderId": order_id, "status": status, "plan": plan,
                "amount": {"currency_code": "USD", "value": value}, "email": "buyer@example.com"}

    def test_status_lists_plans_and_checkout(self):
        status = self.license.status()
        self.assertFalse(status["pro"])
        self.assertEqual([plan["id"] for plan in status["plans"]], ["month", "lifetime"])
        self.assertTrue(status["checkout"]["ready"])
        self.assertEqual(status["checkout"]["clientId"], "client-123")

    def test_completed_payment_turns_pro_on(self):
        status = self.license.record_purchase(self.order())
        self.assertTrue(status["pro"])
        self.assertEqual(status["source"], "purchase")
        self.assertEqual(status["planName"], "Lifetime")
        self.assertEqual(status["order"], "5O190127TN364715T")
        self.assertEqual(self.license.can_start(), "pro")

    def test_wrong_amount_or_unfinished_payment_is_refused(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.license.record_purchase(self.order(value="1.00"))
        with self.assertRaisesRegex(ValueError, "not complete"):
            self.license.record_purchase(self.order(status="PENDING"))
        with self.assertRaisesRegex(ValueError, "order number"):
            self.license.record_purchase(self.order(order_id="x"))
        with self.assertRaisesRegex(ValueError, "Unknown plan"):
            self.license.record_purchase(self.order(plan="forever"))
        self.assertFalse(self.license.is_pro())

    def test_month_pass_ends_and_buying_again_extends_it(self):
        self.license.record_purchase(self.order(plan="month", value="9", order_id="ORDER0000001"))
        self.assertEqual(self.license.status()["expires"], (self.day + timedelta(days=30)).isoformat())
        self.license.record_purchase(self.order(plan="month", value="9.00", order_id="ORDER0000002"))
        self.assertEqual(self.license.status()["expires"], (self.day + timedelta(days=60)).isoformat())
        self.day = self.day + timedelta(days=61)
        status = self.license.status()
        self.assertFalse(status["pro"])
        self.assertIn("ended on", status["reason"])

    def test_remove_from_this_computer(self):
        self.license.record_purchase(self.order())
        self.assertFalse(self.license.deactivate()["pro"])
        self.assertTrue(json.loads(self.license.path.read_text())["history"])  # receipts are kept

    def test_no_checkout_without_client_id(self):
        status = License(self.home, public_key=PUBLIC, paypal_client_id="").status()
        self.assertFalse(status["checkout"]["ready"])


class SellerCliTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"CAIRN_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--root", str(self.home), "pro", *args])
        return code, out.getvalue()

    def test_keygen_then_issue(self):
        with mock.patch.object(pro, "generate_keypair", lambda bits=2048: dict(SELLER)):
            code, out = self.run_cli("keygen")
        self.assertEqual(code, 0)
        self.assertTrue((self.home / "seller" / "signing-key.json").exists())
        self.assertNotIn('"d"', out)  # only the public half is printed
        code, _ = self.run_cli("keygen")
        self.assertEqual(code, 1)  # refuses to overwrite
        with mock.patch.dict(pro.SELLER_PUBLIC_KEY, PUBLIC):
            code, out = self.run_cli("issue", "--email", "ana@example.com", "--plan", "month")
            self.assertEqual(code, 0)
            key = out.strip().splitlines()[-1]
            self.assertEqual(verify_key(key)["plan"], "month")
            code, out = self.run_cli("verify", key)
            self.assertEqual(code, 0)
            self.assertIn("ana@example.com", out)


    def test_sell_switches_checkout_on_in_one_command(self):
        copy = self.home / "pro_copy.py"
        copy.write_text(Path(pro.__file__).read_text(encoding="utf-8"), encoding="utf-8")
        with mock.patch.object(pro, "__file__", str(copy)), \
                mock.patch.object(pro, "generate_keypair", lambda bits=2048: dict(SELLER)):
            code, out = self.run_cli("sell", "--paypal-client-id", "AbCdEfGhIjKlMnOpQrStUvWx-123")
            self.assertEqual(code, 0, out)
            self.assertIn('"CAIRN_PRO_PAYPAL_CLIENT", "AbCdEfGhIjKlMnOpQrStUvWx-123"', copy.read_text(encoding="utf-8"))
            self.assertTrue((self.home / "seller" / "signing-key.json").exists())  # made the key on the way
            code, _ = self.run_cli("sell", "--paypal-client-id", "bad id!")
            self.assertEqual(code, 1)


class ShippingTests(unittest.TestCase):
    """What ships in the repo: a public key only, and no outside storefront."""

    def shipped_files(self):
        for path in REPO.rglob("*"):
            if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts \
                    and path.suffix in {".py", ".js", ".css", ".html", ".md", ".json", ".toml"}:
                yield path

    def test_public_key_is_installed_and_private_half_is_not(self):
        self.assertGreater(pro.SELLER_PUBLIC_KEY["n"].bit_length(), 2000)
        self.assertEqual(set(pro.SELLER_PUBLIC_KEY), {"n", "e"})
        self.assertFalse(list(REPO.rglob("signing-key.json")))

    def test_no_outside_storefront(self):
        for path in self.shipped_files():
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for storefront in ("gum" + "road", "lemon" + "squeezy"):
                self.assertNotIn(storefront, text, f"{path.relative_to(REPO)} mentions {storefront}")


if __name__ == "__main__":
    unittest.main()

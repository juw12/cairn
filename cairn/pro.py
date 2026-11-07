"""Cairn Pro: plans, in-app purchases and signed Pro keys. Standard library only.

Everything happens inside Cairn; there is no storefront website.

- Buying: the Autopilot screen shows the plans and PayPal's embedded checkout (card or PayPal
  balance, inside the Cairn window). When the payment completes the app stores a receipt and Pro
  is active at once.
- Pro keys: a key is a small signed note, ``CAIRN1.<payload>.<signature>``. The app checks the
  signature offline against ``SELLER_PUBLIC_KEY`` (RSA-2048, PKCS#1 v1.5, SHA-256), so a key works
  with no network and cannot be forged without the seller's private key. Keys move Pro to a new
  computer, or sell it any other way (bank transfer, QRIS, a gift).
- Selling: the seller makes a key pair once with ``cairn pro keygen`` (the private half stays in
  ``~/.cairn/seller/`` on the seller's computer, never in a repo) and issues keys with
  ``cairn pro issue``.

Cairn is open source, so this is an honest lock, not DRM: it keeps paying simple and fair.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
from datetime import date, timedelta
from pathlib import Path

PRODUCT = "cairn-pro"
KEY_PREFIX = "CAIRN1"
CURRENCY = "USD"
TRIAL_GOALS = 1

# Prices live here, in one place: the app, the checkout and the receipt check all read them.
PLANS = (
    {"id": "month", "name": "30 days", "price": "9.00", "days": 30, "blurb": "Then it simply ends, no renewal. Buy again any time to add 30 days."},
    {"id": "lifetime", "name": "Lifetime", "price": "49.00", "days": 0, "blurb": "Pay once. Pro on this and future versions."},
)

# Public half of the seller's key (made with `cairn pro keygen`). Safe to publish.
SELLER_PUBLIC_KEY = {
    "n": 23633786664660389935374512353110572279964842911131004927830787730230056089811188817336077172579095103628265527606195302850649206924648257021579586656655169870825594809452601343784418551899206221154199422760854608619894541347781257086983214040021066796317968855967639091756201182533036463452788886575828290264908990740346635942284806350982446672452934329432578209969486101568655323883187014325336024819967506398168641200823855538819544877564339993268159516155006116995238959098442325211597064735627435293962101091338125907236630279839838304641718348679291948179968088890341946008031809460349645925410962538960371006323,
    "e": 65537,
}

# PayPal REST app client id (public, made once in PayPal's developer dashboard). Empty = the
# in-app checkout stays hidden and only Pro keys work.
PAYPAL_CLIENT_ID = os.environ.get("CAIRN_PRO_PAYPAL_CLIENT", "")

SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class PlanError(Exception):
    """Autopilot needs Cairn Pro (or a free trial goal)."""


def config_dir() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or (Path.home() / ".cairn"))


def seller_key_path() -> Path:
    return config_dir() / "seller" / "signing-key.json"


def plan_by_id(plan_id: str) -> dict:
    for plan in PLANS:
        if plan["id"] == plan_id:
            return plan
    raise ValueError(f"Unknown plan '{plan_id}'. Plans: {', '.join(p['id'] for p in PLANS)}.")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


# ------------------------------------------------------------------ signatures (RSA, stdlib)

def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _encoded_digest(message: bytes, size: int) -> int:
    """EMSA-PKCS1-v1_5 encoding of SHA-256(message) as an integer."""
    digest = SHA256_PREFIX + hashlib.sha256(message).digest()
    padding = b"\xff" * (size - len(digest) - 3)
    return int.from_bytes(b"\x00\x01" + padding + b"\x00" + digest, "big")


def _is_probable_prime(number: int, rounds: int = 40) -> bool:
    if number < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if number % small == 0:
            return number == small
    d, s = number - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        witness = secrets.randbelow(number - 3) + 2
        x = pow(witness, d, number)
        if x in (1, number - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, number)
            if x == number - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int, e: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if (candidate - 1) % e and _is_probable_prime(candidate):
            return candidate


def generate_keypair(bits: int = 2048) -> dict:
    """A new RSA key pair: {"n", "e", "d"}. Keep "d" secret."""
    e = 65537
    while True:
        p = _random_prime(bits // 2, e)
        q = _random_prime(bits // 2, e)
        n = p * q
        if p != q and n.bit_length() == bits:
            break
    d = pow(e, -1, (p - 1) * (q - 1))
    return {"n": n, "e": e, "d": d}


def sign_key(payload: dict, private: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    size = (private["n"].bit_length() + 7) // 8
    signature = pow(_encoded_digest(body, size), private["d"], private["n"])
    return f"{KEY_PREFIX}.{_b64e(body)}.{_b64e(signature.to_bytes(size, 'big'))}"


def verify_key(key: str, public: dict | None = None, today: date | None = None) -> dict:
    """Check a Pro key offline. Returns its payload or raises ValueError with a plain reason."""
    public = public or SELLER_PUBLIC_KEY
    if not public.get("n"):
        raise ValueError("This copy of Cairn has no seller key installed, so it cannot check Pro keys.")
    parts = "".join(key.split()).split(".")
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        raise ValueError("That does not look like a Cairn Pro key. It starts with CAIRN1.")
    try:
        body = _b64d(parts[1])
        signature = int.from_bytes(_b64d(parts[2]), "big")
    except (ValueError, TypeError):
        raise ValueError("That key is damaged. Copy it again, all of it.") from None
    size = (public["n"].bit_length() + 7) // 8
    if signature >= public["n"] or pow(signature, public["e"], public["n"]) != _encoded_digest(body, size):
        raise ValueError("That key is not genuine (its signature does not match).")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("That key is damaged. Copy it again, all of it.") from None
    if payload.get("product") != PRODUCT:
        raise ValueError("That key is for a different product.")
    expires = payload.get("expires") or ""
    if expires and date.fromisoformat(expires) < (today or date.today()):
        raise ValueError(f"That key ended on {expires}.")
    return payload


def issue_key(email: str, plan_id: str, private: dict, days: int | None = None, today: date | None = None) -> str:
    plan = plan_by_id(plan_id)
    start = today or date.today()
    length = plan["days"] if days is None else days
    payload = {
        "product": PRODUCT,
        "id": secrets.token_hex(4),
        "email": email.strip(),
        "plan": plan["id"],
        "issued": start.isoformat(),
        "expires": (start + timedelta(days=length)).isoformat() if length else "",
    }
    return sign_key(payload, private)


# ------------------------------------------------------------------ this machine's plan

ORDER_ID = re.compile(r"^[A-Z0-9-]{8,40}$")


class License:
    """Cairn Pro on this computer, kept in ~/.cairn/license.json (never inside a brain or repo)."""

    def __init__(self, home: Path | None = None, public_key: dict | None = None, paypal_client_id: str | None = None,
                 today=date.today):
        self.home = home or config_dir()
        self.public_key = public_key or SELLER_PUBLIC_KEY
        self.paypal_client_id = PAYPAL_CLIENT_ID if paypal_client_id is None else paypal_client_id
        self.today = today
        self.lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.home / "license.json"

    @property
    def trial_path(self) -> Path:
        return self.home / "trial.json"

    # ---------------------------------------------------------- trial

    def trial_left(self) -> int:
        used = int(_load(self.trial_path, {}).get("goals_used", 0))
        return max(0, TRIAL_GOALS - used)

    def use_trial(self) -> None:
        data = _load(self.trial_path, {})
        data["goals_used"] = int(data.get("goals_used", 0)) + 1
        _save(self.trial_path, data)

    # ---------------------------------------------------------- entitlement

    def _entitlement(self) -> tuple[dict | None, str]:
        """(active plan or None, reason it is not active)."""
        record = _load(self.path, {})
        today = self.today()
        if record.get("key"):
            try:
                payload = verify_key(record["key"], self.public_key, today)
            except ValueError as error:
                return None, str(error)
            return {"source": "key", "email": payload.get("email", ""), "plan": payload.get("plan", ""),
                    "expires": payload.get("expires", "")}, ""
        receipt = record.get("receipt")
        if receipt:
            expires = receipt.get("expires", "")
            if expires and date.fromisoformat(expires) < today:
                return None, f"Your {plan_name(receipt.get('plan'))} pass ended on {expires}."
            return {"source": "purchase", "email": receipt.get("email", ""), "plan": receipt.get("plan", ""),
                    "expires": expires, "order": receipt.get("order", "")}, ""
        return None, ""

    def is_pro(self) -> bool:
        return self._entitlement()[0] is not None

    def status(self) -> dict:
        active, reason = self._entitlement()
        return {
            "plan": "pro" if active else ("trial" if self.trial_left() else "free"),
            "pro": bool(active),
            "email": (active or {}).get("email", ""),
            "planName": plan_name((active or {}).get("plan")),
            "expires": (active or {}).get("expires", ""),
            "source": (active or {}).get("source", ""),
            "order": (active or {}).get("order", ""),
            "trialLeft": self.trial_left(),
            "reason": reason,
            "plans": [dict(plan) for plan in PLANS],
            "currency": CURRENCY,
            "checkout": {"provider": "paypal", "clientId": self.paypal_client_id, "ready": bool(self.paypal_client_id)},
            "keysReady": bool(self.public_key.get("n")),
        }

    def activate(self, key: str) -> dict:
        key = "".join(key.split())
        if not key:
            raise ValueError("Paste your Cairn Pro key (it starts with CAIRN1).")
        verify_key(key, self.public_key, self.today())
        with self.lock:
            _save(self.path, {"key": key})
        return self.status()

    def record_purchase(self, order: dict) -> dict:
        """Store a completed in-app PayPal payment and turn Pro on."""
        order_id = str(order.get("orderId", "")).strip()
        plan = plan_by_id(str(order.get("plan", "")))
        amount = order.get("amount") or {}
        if not ORDER_ID.match(order_id):
            raise ValueError("The payment did not return an order number.")
        if str(order.get("status", "")).upper() != "COMPLETED":
            raise ValueError("The payment is not complete yet, so Pro is not on. Nothing was charged twice.")
        if str(amount.get("currency_code", "")).upper() != CURRENCY or _money(amount.get("value")) != _money(plan["price"]):
            raise ValueError("The amount paid does not match the plan price. Contact the seller with your order number.")
        with self.lock:
            record = _load(self.path, {})
            previous = record.get("receipt") or {}
            start = self.today()
            if previous.get("expires") and plan["days"]:
                start = max(start, date.fromisoformat(previous["expires"]))  # buying again extends the pass
            receipt = {
                "order": order_id,
                "plan": plan["id"],
                "email": str(order.get("email", "")).strip(),
                "paid": f"{plan['price']} {CURRENCY}",
                "date": self.today().isoformat(),
                "expires": (start + timedelta(days=plan["days"])).isoformat() if plan["days"] else "",
            }
            record = {"receipt": receipt, "history": (record.get("history") or []) + [receipt]}
            _save(self.path, record)
        return self.status()

    def deactivate(self) -> dict:
        with self.lock:
            record = _load(self.path, {})
            record.pop("key", None)
            record.pop("receipt", None)
            if record:
                _save(self.path, record)
            elif self.path.exists():
                self.path.unlink()
        return self.status()

    def can_start(self) -> str:
        """'pro', 'trial', or raise PlanError."""
        if self.is_pro():
            return "pro"
        if self.trial_left() > 0:
            return "trial"
        raise PlanError("Autopilot is part of Cairn Pro. Your free trial goal is used up.")


def plan_name(plan_id) -> str:
    for plan in PLANS:
        if plan["id"] == plan_id:
            return plan["name"]
    return ""


def _money(value) -> int:
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return -1

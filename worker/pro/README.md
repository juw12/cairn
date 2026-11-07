# cairn-pro worker (license server)

Turns a PayPal order into a signed Cairn Pro key, so buyers can restore Pro on any computer by themselves.

**Live:** `https://cairn-pro.juwenmondial.workers.dev` (`GET /health` says `ready: true` once all secrets are set).

| Request | Does |
|---|---|
| `POST /claim` `{orderId, email}` | Looks the order up at PayPal (`/v2/checkout/orders/{id}`), checks it is COMPLETED, is a Cairn Pro plan at the exact price, and that the e-mail is the payer's. Returns `{key, plan, expires}`. |
| `GET /health` | `{ok, ready, paypal}` |

It stores nothing and logs nothing: the PayPal order is the record, so the same order number and e-mail give the
same key again on any computer. The app calls `/claim` right after a purchase and when someone uses *Restore*.

Keys use the same format as `cairn/pro.py` (`CAIRN1.<payload>.<RSA-2048 PKCS#1 v1.5 SHA-256 signature>`), and the
plans in `src/license.js` must match `PLANS` in `cairn/pro.py`. `tests/test_pro_server.py` runs this Worker in Node
against a fake PayPal, checks the key verifies in Python and that the plans match.

## Setup

1. Deploy: `npx wrangler deploy` in this folder (`wrangler login` once).
2. Secrets: `./set-secrets.ps1 -Signing` uploads `~/.cairn/seller/signing-key.json` as `SIGNING_KEY` and asks for
   the PayPal REST app's **Client ID** and **Secret** (developer.paypal.com -> Apps & Credentials -> Live).
3. `PAYPAL_ENV` in `wrangler.toml` is `live`; set it to `sandbox` to test with sandbox accounts.

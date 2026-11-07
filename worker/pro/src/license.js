// Cairn Pro license server logic: PayPal order -> signed Pro key (same format cairn/pro.py verifies).
// Pure functions + fetch, so it runs on Cloudflare Workers and in Node for tests.

// Must match PLANS in cairn/pro.py (tests/test_pro_server.py checks this).
export const PLANS = {
  month: { price: "9.00", days: 30 },
  lifetime: { price: "49.00", days: 0 },
};
export const CURRENCY = "USD";
export const PRODUCT = "cairn-pro";

const SHA256_PREFIX = "3031300d060960864801650304020105000420";

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function bytesToBigInt(bytes) {
  let hex = "";
  for (const byte of bytes) hex += byte.toString(16).padStart(2, "0");
  return BigInt(`0x${hex || "0"}`);
}

function bigIntToBytes(value, size) {
  const hex = value.toString(16).padStart(size * 2, "0");
  return hexToBytes(hex);
}

function modPow(base, exponent, modulus) {
  let result = 1n;
  let b = base % modulus;
  let e = exponent;
  while (e > 0n) {
    if (e & 1n) result = (result * b) % modulus;
    b = (b * b) % modulus;
    e >>= 1n;
  }
  return result;
}

function b64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Same bytes as Python json.dumps(payload, separators=(",", ":"), sort_keys=True) for flat ASCII-safe payloads.
export function canonicalJson(payload) {
  const keys = Object.keys(payload).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${JSON.stringify(payload[key])}`).join(",")}}`;
}

export async function signKey(payload, privateKey) {
  const n = BigInt(privateKey.n);
  const d = BigInt(privateKey.d);
  const size = Math.ceil(n.toString(16).length / 2);
  const body = new TextEncoder().encode(canonicalJson(payload));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", body));
  const info = new Uint8Array([...hexToBytes(SHA256_PREFIX), ...digest]);
  const em = new Uint8Array(size);
  em[0] = 0x00;
  em[1] = 0x01;
  em.fill(0xff, 2, size - info.length - 1);
  em[size - info.length - 1] = 0x00;
  em.set(info, size - info.length);
  const signature = modPow(bytesToBigInt(em), d, n);
  return `CAIRN1.${b64url(body)}.${b64url(bigIntToBytes(signature, size))}`;
}

function addDays(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function money(value) {
  return Math.round(Number(value) * 100);
}

export class ClaimError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

async function paypalToken(env, fetchImpl) {
  const base = env.PAYPAL_ENV === "sandbox" ? "https://api-m.sandbox.paypal.com" : "https://api-m.paypal.com";
  const response = await fetchImpl(`${base}/v1/oauth2/token`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${btoa(`${env.PAYPAL_CLIENT_ID}:${env.PAYPAL_SECRET}`)}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: "grant_type=client_credentials",
  });
  if (!response.ok) throw new ClaimError("The license server could not sign in to PayPal.", 502);
  const data = await response.json();
  return { base, token: data.access_token };
}

// Look the order up at PayPal and turn it into a signed key. Restores use the same path.
export async function claim({ orderId, email }, env, fetchImpl = fetch) {
  if (!env.SIGNING_KEY || !env.PAYPAL_CLIENT_ID || !env.PAYPAL_SECRET) {
    throw new ClaimError("The Cairn Pro license server is not set up yet.", 503);
  }
  const id = String(orderId || "").trim().toUpperCase();
  if (!/^[A-Z0-9-]{8,40}$/.test(id)) throw new ClaimError("That is not a PayPal order number.");
  if (!/^[^@\s]+@[^@\s]+$/.test(String(email || "").trim())) {
    throw new ClaimError("Enter the e-mail of the PayPal account that paid.");
  }
  const { base, token } = await paypalToken(env, fetchImpl);
  const response = await fetchImpl(`${base}/v2/checkout/orders/${encodeURIComponent(id)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 404) throw new ClaimError("PayPal does not know that order number.", 404);
  if (!response.ok) throw new ClaimError("PayPal did not answer. Try again in a minute.", 502);
  const order = await response.json();
  const unit = (order.purchase_units || [])[0] || {};
  const capture = ((unit.payments || {}).captures || [])[0] || {};
  const plan = PLANS[unit.custom_id];
  if (order.status !== "COMPLETED" || capture.status !== "COMPLETED") {
    throw new ClaimError("That payment is not complete.", 402);
  }
  if (!plan) throw new ClaimError("That order is not a Cairn Pro purchase.");
  const amount = capture.amount || {};
  if (amount.currency_code !== CURRENCY || money(amount.value) !== money(plan.price)) {
    throw new ClaimError("The amount paid does not match the plan price.");
  }
  const payer = String((order.payer || {}).email_address || "").toLowerCase();
  if (!payer || String(email).trim().toLowerCase() !== payer) {
    throw new ClaimError("That e-mail does not match the PayPal order.", 403);
  }
  const issued = String(capture.create_time || order.create_time || new Date().toISOString()).slice(0, 10);
  const payload = {
    product: PRODUCT,
    id: id,
    email: payer,
    plan: unit.custom_id,
    issued,
    expires: plan.days ? addDays(issued, plan.days) : "",
  };
  return { key: await signKey(payload, JSON.parse(env.SIGNING_KEY)), plan: unit.custom_id, expires: payload.expires };
}

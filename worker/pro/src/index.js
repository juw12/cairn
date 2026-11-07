// Cairn Pro license server (Cloudflare Worker).
//   POST /claim   {orderId, email} -> {key, plan, expires}   (the app calls it after a purchase;
//                                                             people use it to restore on a new computer)
//   GET  /health  -> {ok, ready}
// Secrets: SIGNING_KEY (JSON {n, e, d}), PAYPAL_CLIENT_ID, PAYPAL_SECRET. Var: PAYPAL_ENV (live|sandbox).
// It stores nothing: the PayPal order itself is the record, so a key can always be claimed again.
import { ClaimError, claim } from "./license.js";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...CORS },
  });
}

export async function handle(request, env, fetchImpl = fetch) {
  const url = new URL(request.url);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (url.pathname === "/health" && request.method === "GET") {
    const ready = Boolean(env.SIGNING_KEY && env.PAYPAL_CLIENT_ID && env.PAYPAL_SECRET);
    return json({ ok: true, product: "cairn-pro", ready, paypal: env.PAYPAL_ENV || "live" });
  }
  if ((url.pathname === "/claim" || url.pathname === "/restore") && request.method === "POST") {
    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "Send JSON: {orderId, email}." }, 400);
    }
    try {
      return json(await claim({ orderId: body.orderId, email: body.email }, env, fetchImpl));
    } catch (error) {
      if (error instanceof ClaimError) return json({ error: error.message }, error.status);
      return json({ error: "The license server had a problem. Try again in a minute." }, 500);
    }
  }
  return json({ error: "Not found." }, 404);
}

export default {
  fetch(request, env) {
    return handle(request, env);
  },
};

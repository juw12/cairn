// cairn-auth: the smallest possible helper for "Continue with GitHub" on a static site.
//
// GitHub's OAuth device flow needs no client secret, but its two endpoints don't send CORS
// headers, so a browser can't call them directly. This Worker relays exactly those two requests:
//
//   POST /device/code         -> https://github.com/login/device/code
//   POST /oauth/access_token  -> https://github.com/login/oauth/access_token
//
// What it will NOT do: store anything, log tokens, accept another client_id or scope, relay any
// other URL, or answer origins that aren't listed in ALLOWED_ORIGINS.
//
// Settings (Cloudflare dashboard -> Worker -> Settings -> Variables):
//   GITHUB_CLIENT_ID  the Client ID of your GitHub OAuth App (with "Enable Device Flow" ticked)
//   ALLOWED_ORIGINS   comma-separated, e.g. "https://juw12.github.io,http://127.0.0.1:4777"

const GITHUB = "https://github.com";
const SCOPE = "public_repo";
const ROUTES = {
  "/device/code": "/login/device/code",
  "/oauth/access_token": "/login/oauth/access_token",
};

function allowedOrigin(request, env) {
  const origin = request.headers.get("Origin") || "";
  const allowed = String(env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  return allowed.includes(origin) ? origin : null;
}

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Accept",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...headers, "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

export async function handle(request, env, fetchImpl = fetch) {
  const origin = allowedOrigin(request, env);
  if (!origin) return json({ error: "origin_not_allowed" }, 403, { Vary: "Origin" });
  const cors = corsHeaders(origin);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

  const target = ROUTES[new URL(request.url).pathname];
  if (!target || request.method !== "POST") return json({ error: "not_found" }, 404, cors);
  if (!env.GITHUB_CLIENT_ID) return json({ error: "not_configured" }, 500, cors);

  // The browser's body is only trusted for the device code; client_id, scope and grant are fixed here.
  const input = await request.json().catch(() => ({}));
  const form = new URLSearchParams({ client_id: env.GITHUB_CLIENT_ID });
  if (target === ROUTES["/device/code"]) {
    form.set("scope", SCOPE);
  } else {
    const deviceCode = typeof input.device_code === "string" ? input.device_code : "";
    if (!/^[A-Za-z0-9_-]{8,100}$/.test(deviceCode)) return json({ error: "bad_device_code" }, 400, cors);
    form.set("device_code", deviceCode);
    form.set("grant_type", "urn:ietf:params:oauth:grant-type:device_code");
  }

  const upstream = await fetchImpl(GITHUB + target, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": "cairn-auth",
    },
    body: form.toString(),
  });
  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

export default {
  fetch: (request, env) => handle(request, env),
};

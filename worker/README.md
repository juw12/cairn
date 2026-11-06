# cairn-auth worker

Turns on **Continue with GitHub** for a Cairn site on GitHub Pages. Without it, people can still
sign in by pasting a personal access token.

It relays two GitHub requests that browsers aren't allowed to make directly (GitHub's OAuth
device flow has no CORS). It holds **no secret**: the device flow only needs a public Client ID.
It stores nothing and logs nothing. It does see the token in the one response it relays back to
the browser, so only deploy it on an account you control.

**Live for juw12.github.io:** `https://cairn-auth.juwenmondial.workers.dev`, using the "Cairn" OAuth App
(Client ID `Ov23liZGOkKszTJypcel`, set in `wrangler.toml`). Redeploy with `npx wrangler deploy` from this folder.

## Setup (about 10 minutes, free)

1. **GitHub OAuth App**: github.com → Settings → Developer settings → OAuth Apps → New OAuth App.
   - Application name: `Cairn`
   - Homepage URL: your site, e.g. `https://juw12.github.io/cairn/`
   - Authorization callback URL: the same URL (unused by the device flow, but required)
   - After creating it, tick **Enable Device Flow** and save. Copy the **Client ID**.
   - Do **not** generate a client secret; it isn't needed.
2. **Cloudflare Worker**: dash.cloudflare.com → Workers & Pages → Create → Worker → name it
   `cairn-auth` → Deploy → Edit code. Replace the code with `worker/cairn-auth.js` and deploy.
   (Or, with Node installed: `npx wrangler deploy` from this folder.)
3. **Variables** (Worker → Settings → Variables and secrets):
   - `GITHUB_CLIENT_ID` = the Client ID from step 1
   - `ALLOWED_ORIGINS` = `https://juw12.github.io` (comma-separate more, e.g. `http://127.0.0.1:4777`)
4. **Point Cairn at it**: in `docs/index.html` set
   `<meta name="cairn-auth" content="https://cairn-auth.<your-subdomain>.workers.dev">` and push.

## What it accepts

| Request | Relayed to | Notes |
|---|---|---|
| `POST /device/code` | `github.com/login/device/code` | `client_id` and `scope=public_repo` are set by the Worker |
| `POST /oauth/access_token` | `github.com/login/oauth/access_token` | only `device_code` comes from the browser |
| anything else | nothing | 404; unknown origins get 403 |

Tested by `tests/test_worker.py` (runs the module in a real browser against a fake GitHub).

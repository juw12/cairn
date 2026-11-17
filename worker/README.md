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

---

# cairn-telegram worker

A Telegram bot of your own that reports Cairn's status once a day, and on `/status` in between.

The bot lives on a Worker, not on the PC, for two reasons: office networks often block Telegram, and
a PC that is off cannot report that it is off. The desktop app hands its latest status text to the
Worker (when it changed, and at least once an hour); the Worker sends it at the hour you chose. When
the PC has been quiet for 2.5 hours, the message starts by saying since when.

What leaves the PC: the status text only. That is session titles, counts, the brain's sync state and
the app's health. No note bodies, log lines, file names or paths. It is kept in the Worker's KV store
(the latest one only) and sent to the one chat you paired. Nothing is sent until you set a bot up.

**Live for juw12:** `https://cairn-telegram.juwenmondial.workers.dev` (config in `wrangler.telegram.toml`).
Redeploy with `npx wrangler deploy -c wrangler.telegram.toml` from this folder.

## Setup (about 10 minutes, free)

1. **Worker**, from this folder with Node installed:

   ```
   npx wrangler kv namespace create cairn-telegram      # put the id it prints in wrangler.telegram.toml
   npx wrangler deploy -c wrangler.telegram.toml
   npx wrangler secret put CAIRN_KEY -c wrangler.telegram.toml    # any long random text; the PC needs the same
   ```

2. **Tell this PC where it is** (the key is stored encrypted for your Windows user in `~/.cairn/telegram.json`):

   ```
   cairn telegram setup --worker https://cairn-telegram.<your-subdomain>.workers.dev --key <CAIRN_KEY>
   ```

3. **Bot**: in Telegram, message `@BotFather`, send `/newbot` and copy the token it gives you. Then:

   ```
   cairn telegram setup <bot token>            # add --hour 8 for a morning report; the default is 21
   ```

   Open the `t.me` link it prints and press **Start**. The link pairs that one chat with the bot and
   works once. The token goes straight to the Worker and is never written on the PC.

`cairn telegram show` prints the text without sending anything, `cairn telegram send` sends it now,
`cairn telegram status` says what is connected, `cairn telegram off` forgets the bot.

## What it accepts

| Request | Who | Notes |
|---|---|---|
| `PUT /status` | the PC (`Authorization: Bearer CAIRN_KEY`) | `{text, hour, offset}`: the latest status, replacing the one before |
| `POST /setup` | the PC | `{token, code}`: checks the token with Telegram, points the bot's webhook at `/hook` |
| `POST /send` | the PC | send the stored status now |
| `GET /state` | the PC | bot name, paired or not, when the PC last reported. Never the token |
| `POST /off` | the PC | removes the webhook and deletes the token, the chat and the status |
| `POST /hook` | Telegram (secret header) | `/start <code>` pairs a chat; `/status` answers the paired chat only |
| cron, hourly | Cloudflare | sends the day's report once the chosen hour has come |
| anything else | nobody | 404; a wrong or missing key gets 403 |

Strangers who find the bot get no status: only the paired chat is answered.

Tested by `tests/test_telegram.py` (runs the module in a real browser against a fake Telegram and a fake KV store).

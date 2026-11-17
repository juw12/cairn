// cairn-telegram: a Telegram bot that reports Cairn's status once a day.
//
// The desktop app cannot always reach Telegram (office networks block it) and it is off whenever the
// PC is, so the bot lives here. The app pushes its latest status text; this Worker keeps it, sends it
// to one chat at the hour you chose and answers /status in between. When the app stopped reporting,
// the message says since when, which is the point of a status report.
//
//   PUT  /status  (key)       {text, hour, offset}   the app's latest status
//   POST /setup   (key)       {token, code}          keep the bot token, point Telegram at /hook
//   POST /send    (key)                              send the stored status now
//   POST /off     (key)                              forget the bot, the chat and the status
//   GET  /state   (key)                              what is held here (never the token)
//   POST /hook    (Telegram)                         /start <code>, /status
//   cron, hourly                                     the daily message
//
// What it will NOT do: answer anyone but the one paired chat, relay anything else to Telegram, or
// show the bot token again once it is saved.
//
// Settings: a KV namespace bound as STORE, and the secret CAIRN_KEY (`npx wrangler secret put CAIRN_KEY`).

const TELEGRAM = "https://api.telegram.org";
const TOKEN_RE = /^\d{5,}:[A-Za-z0-9_-]{30,}$/;
const CODE_RE = /^[A-Za-z0-9_-]{8,64}$/;
const DEFAULT_HOUR = 21;
const STALE_MS = 150 * 60 * 1000; // the app reports at least hourly: quiet for 2.5 h means off or asleep
const MAX_TEXT = 3500; // Telegram allows 4096 characters a message

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

async function digest(text) {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

// Compared as digests, so the time it takes says nothing about how much of the key matched.
async function same(left, right) {
  return Boolean(left) && Boolean(right) && (await digest(left)) === (await digest(right));
}

const hookSecret = (env) => digest(env.CAIRN_KEY + ":hook");

async function load(env, key) {
  const raw = await env.STORE.get(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

const save = (env, key, value) => env.STORE.put(key, JSON.stringify(value));

async function telegram(fetchImpl, token, method, body) {
  const response = await fetchImpl(`${TELEGRAM}/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return response.json().catch(() => ({ ok: false }));
}

function clock(ms, offset) {
  const local = new Date(ms + offset * 60000);
  const pad = (value) => String(value).padStart(2, "0");
  return {
    day: local.toISOString().slice(0, 10),
    hour: local.getUTCHours(),
    time: `${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`,
  };
}

function ago(ms) {
  const minutes = Math.max(1, Math.round(ms / 60000));
  if (minutes < 90) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
}

// The message for one chat: the app's own text, with a first line saying how old it is when it is old.
export function report(status, now) {
  if (!status || !status.text) return "Cairn has not reported yet. Open the Cairn desktop app on your PC.";
  const offset = Number(status.offset) || 0;
  const age = now - status.at;
  const then = clock(status.at, offset);
  const today = clock(now, offset).day;
  const when = then.day === today ? then.time : `${then.day} ${then.time}`;
  const head =
    age > STALE_MS
      ? `Cairn has been quiet since ${when} (${ago(age)}): the PC is off or asleep, or the desktop app is closed. Last status:\n\n`
      : "";
  const foot = age > STALE_MS ? "" : `\n\nAs of ${when}.`;
  return head + String(status.text).slice(0, MAX_TEXT) + foot;
}

async function sendReport(env, fetchImpl, now) {
  const [bot, chat, status] = await Promise.all([load(env, "bot"), load(env, "chat"), load(env, "status")]);
  if (!bot || !chat) return { sent: false, why: "not_paired" };
  const answer = await telegram(fetchImpl, bot.token, "sendMessage", {
    chat_id: chat.id,
    text: report(status, now),
    disable_web_page_preview: true,
  });
  return answer.ok ? { sent: true } : { sent: false, why: String(answer.description || "telegram_refused") };
}

async function setup(request, env, fetchImpl) {
  const input = await request.json().catch(() => ({}));
  const token = typeof input.token === "string" ? input.token.trim() : "";
  const code = typeof input.code === "string" ? input.code : "";
  if (!TOKEN_RE.test(token)) return json({ error: "bad_token" }, 400);
  if (!CODE_RE.test(code)) return json({ error: "bad_code" }, 400);
  const me = await telegram(fetchImpl, token, "getMe", {});
  if (!me.ok) return json({ error: "telegram_refused_token" }, 400);
  const hooked = await telegram(fetchImpl, token, "setWebhook", {
    url: new URL("/hook", request.url).toString(),
    secret_token: await hookSecret(env),
    allowed_updates: ["message"],
    drop_pending_updates: true,
  });
  if (!hooked.ok) return json({ error: "telegram_refused_webhook" }, 502);
  await telegram(fetchImpl, token, "setMyCommands", {
    commands: [{ command: "status", description: "Cairn's status right now" }],
  });
  const name = String(me.result.username || "");
  await Promise.all([save(env, "bot", { token, name }), save(env, "pair", { code }), env.STORE.delete("chat")]);
  return json({ bot: name });
}

async function off(env, fetchImpl) {
  const bot = await load(env, "bot");
  if (bot) await telegram(fetchImpl, bot.token, "deleteWebhook", {});
  await Promise.all(["bot", "pair", "chat", "status", "sent"].map((key) => env.STORE.delete(key)));
  return json({ off: true });
}

async function putStatus(request, env, now) {
  const input = await request.json().catch(() => ({}));
  const text = typeof input.text === "string" ? input.text.slice(0, MAX_TEXT) : "";
  if (!text.trim()) return json({ error: "empty_status" }, 400);
  const hour = Number.isInteger(input.hour) && input.hour >= 0 && input.hour <= 23 ? input.hour : DEFAULT_HOUR;
  const offset = Number.isInteger(input.offset) && Math.abs(input.offset) <= 14 * 60 ? input.offset : 0;
  await save(env, "status", { text, at: now, hour, offset });
  return json({ kept: true });
}

async function state(env) {
  const [bot, chat, status, sent, pair] = await Promise.all(
    ["bot", "chat", "status", "sent", "pair"].map((key) => load(env, key)),
  );
  return json({
    bot: bot ? bot.name : "",
    paired: Boolean(chat),
    waiting: Boolean(pair),
    at: status ? status.at : null,
    hour: status ? status.hour : DEFAULT_HOUR,
    sent: sent ? sent.day : "",
  });
}

// Telegram calls this for every message to the bot. Always answers 200, or Telegram keeps retrying.
async function hook(request, env, fetchImpl, now) {
  const update = await request.json().catch(() => ({}));
  const message = update && update.message;
  const from = message && message.chat ? message.chat.id : null;
  const text = message && typeof message.text === "string" ? message.text.trim() : "";
  const bot = await load(env, "bot");
  if (!bot || from === null || !text) return json({ ok: true });
  const say = (reply) =>
    telegram(fetchImpl, bot.token, "sendMessage", { chat_id: from, text: reply, disable_web_page_preview: true });
  const chat = await load(env, "chat");

  const start = /^\/start(?:@\w+)?(?:\s+(\S+))?$/.exec(text);
  if (start) {
    const pair = await load(env, "pair");
    if (pair && start[1] && (await same(start[1], pair.code))) {
      await Promise.all([save(env, "chat", { id: from }), env.STORE.delete("pair")]);
      const status = await load(env, "status");
      const hour = String(status ? status.hour : DEFAULT_HOUR).padStart(2, "0");
      await say(`Paired. Cairn reports here every day at ${hour}:00. Send /status whenever you want it sooner.`);
      await sendReport(env, fetchImpl, now);
      return json({ ok: true });
    }
    if (!chat || chat.id !== from) {
      await say("This bot reports one person's Cairn. To pair it, run `cairn telegram setup` on that PC and open the link it prints.");
      return json({ ok: true });
    }
  }
  if (!chat || chat.id !== from) return json({ ok: true }); // strangers get no answer
  if (/^\/status(?:@\w+)?$/.test(text) || start) await sendReport(env, fetchImpl, now);
  else await say("Send /status for Cairn's status right now. The daily report comes on its own.");
  return json({ ok: true });
}

export async function handle(request, env, fetchImpl = fetch, now = Date.now()) {
  const path = new URL(request.url).pathname;
  const route = `${request.method} ${path}`;
  if (route === "GET /") return json({ service: "cairn-telegram" });
  if (!env.CAIRN_KEY || !env.STORE) return json({ error: "not_configured" }, 500);
  if (route === "POST /hook") {
    const given = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
    if (!(await same(given, await hookSecret(env)))) return json({ error: "forbidden" }, 403);
    return hook(request, env, fetchImpl, now);
  }
  const given = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!(await same(given, env.CAIRN_KEY))) return json({ error: "forbidden" }, 403);
  if (route === "PUT /status") return putStatus(request, env, now);
  if (route === "POST /setup") return setup(request, env, fetchImpl);
  if (route === "POST /off") return off(env, fetchImpl);
  if (route === "GET /state") return state(env);
  if (route === "POST /send") {
    const result = await sendReport(env, fetchImpl, now);
    return json(result, result.sent ? 200 : 409);
  }
  return json({ error: "not_found" }, 404);
}

// Hourly: send today's report once the chosen hour has come (in the PC's own time zone).
export async function daily(env, fetchImpl = fetch, now = Date.now()) {
  if (!env.STORE) return { sent: false, why: "not_configured" };
  const status = await load(env, "status");
  const local = clock(now, status ? Number(status.offset) || 0 : 0);
  if (local.hour < (status ? status.hour : DEFAULT_HOUR)) return { sent: false, why: "too_early" };
  const sent = await load(env, "sent");
  if (sent && sent.day === local.day) return { sent: false, why: "already_sent" };
  const result = await sendReport(env, fetchImpl, now);
  if (result.sent) await save(env, "sent", { day: local.day });
  return result;
}

export default {
  fetch: (request, env) => handle(request, env),
  scheduled: (event, env, context) => context.waitUntil(daily(env)),
};

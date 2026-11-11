// Chat from anywhere. When this device can't reach your PC directly (you're outside the office
// network), chat goes through a private GitHub repo, <you>/cairn-relay by default:
//   requests/<id>.json   this device writes one per message, stop or delete
//   threads/<chat>.json  your PC answers here as the reply streams in ("request" = last one it handled)
//   index.json           your PC's chat list, models and when it last checked in ("pcSeen")
// Your PC runs the other half (cairn/relay.py) inside the desktop app after `cairn relay setup`.
// Reads use ETags, so polling while you wait for an answer costs almost nothing against GitHub's limits.

import { authFetch, session } from "./auth.js";
import { decodeBase64, encodeBase64 } from "./github.js";

const API = "https://api.github.com";
const REPO_KEY = "cairn.relayRepo";
const REPO_RE = /^[A-Za-z0-9-]{1,39}\/[A-Za-z0-9._-]{1,100}$/;
export const TOKEN_URL = "https://github.com/settings/personal-access-tokens/new";

const fetched = new Map(); // url -> { etag, data }
const waiting = new Map(); // chat id -> { request, thread } until the PC has picked the message up

export class RelayError extends Error {}

export function relayRepo() {
  const saved = localStorage.getItem(REPO_KEY) || "";
  if (REPO_RE.test(saved)) return saved;
  const login = session()?.login;
  return login ? `${login}/cairn-relay` : "";
}

export function setRelayRepo(value) {
  const repo = String(value || "").trim().replace(/^https?:\/\/github\.com\//i, "").replace(/\/+$/, "");
  if (!repo) {
    localStorage.removeItem(REPO_KEY);
  } else if (REPO_RE.test(repo)) {
    localStorage.setItem(REPO_KEY, repo);
  } else {
    throw new RelayError("Write the relay repo as owner/name, for example juw12/cairn-relay.");
  }
  fetched.clear();
  return relayRepo();
}

const accessHelp = () =>
  `Can't open ${relayRepo() || "your relay repo"}. On your PC run "cairn relay setup", then sign in here with a token that can read and write that private repo.`;

const randomHex = (bytes) =>
  Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (b) => b.toString(16).padStart(2, "0")).join("");

export function newChatId(date = new Date()) {
  const day = `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
  return `${day}-${randomHex(4)}`;
}

const contentsUrl = (path) => `${API}/repos/${relayRepo()}/contents/${path}`;

// One JSON file from the relay repo, or null if it isn't there. Unchanged files cost a free 304.
async function readJson(path) {
  if (!relayRepo()) throw new RelayError("Sign in with GitHub to chat through your relay repo.");
  const url = contentsUrl(path);
  const known = fetched.get(url);
  let response;
  try {
    response = await authFetch(url, { cache: "no-store", headers: known?.etag ? { "If-None-Match": known.etag } : {} });
  } catch (error) {
    throw new RelayError(error.message || "Can't reach GitHub. Check your connection.");
  }
  if (response.status === 304 && known) return known.data;
  if (response.status === 404) return null;
  if (response.status === 403 || response.status === 401) throw new RelayError(accessHelp());
  if (!response.ok) throw new RelayError(`GitHub answered ${response.status}. Try again in a moment.`);
  const body = await response.json();
  let data = null;
  try {
    data = JSON.parse(decodeBase64(body.content));
  } catch {
    data = null;
  }
  fetched.set(url, { etag: response.headers.get("etag") || "", data });
  return data;
}

async function postRequest(request) {
  let response;
  try {
    response = await authFetch(contentsUrl(`requests/${request.id}.json`), {
      method: "PUT",
      body: JSON.stringify({ message: `cairn: ${request.action} ${request.thread}`, content: encodeBase64(JSON.stringify(request)) }),
    });
  } catch (error) {
    throw new RelayError(error.message || "Can't reach GitHub. Check your connection.");
  }
  if (response.status === 404 || response.status === 403) throw new RelayError(accessHelp());
  if (!response.ok) throw new RelayError(`GitHub answered ${response.status}. Your message wasn't sent.`);
}

function makeRequest(action, thread, extra = {}) {
  return { id: `${Date.now()}-${randomHex(3)}`, action, thread, at: Date.now(), ...extra };
}

// Same shape as the local server's chat API, so chat.js can use either.
export const relayStore = {
  repo: relayRepo,

  async chat() {
    const index = await readJson("index.json");
    if (!index) throw new RelayError(accessHelp());
    return {
      available: index.available !== false,
      models: index.models || [],
      chats: index.chats || [],
      pcSeen: index.pcSeen || 0,
    };
  },

  async chatThread(id) {
    const remote = await readJson(`threads/${id}.json`);
    const pending = waiting.get(id);
    if (pending) {
      if (remote && remote.request === pending.request) {
        waiting.delete(id);
        return remote;
      }
      return pending.thread;
    }
    if (!remote) throw new RelayError("That chat isn't on GitHub yet. Your PC uploads it within a few seconds.");
    return remote;
  },

  async chatAction(action, body = {}) {
    if (action === "send") {
      const id = body.id || newChatId();
      const text = String(body.text || "").trim();
      const request = makeRequest("send", id, { text, model: body.model || "" });
      await postRequest(request);
      const base = waiting.get(id)?.thread || fetched.get(contentsUrl(`threads/${id}.json`))?.data || {
        id, title: text.split("\n")[0].slice(0, 60), model: body.model || "", messages: [],
      };
      const now = Date.now() / 1000;
      const thread = {
        ...base,
        status: "running",
        messages: [
          ...(base.messages || []),
          { role: "user", text, at: now },
          { role: "assistant", text: "", at: now, model: body.model || base.model || "", pending: true, waiting: true },
        ],
      };
      waiting.set(id, { request: request.id, thread });
      return thread;
    }
    if (action === "stop" || action === "delete") {
      await postRequest(makeRequest(action, body.id));
      if (action === "delete") waiting.delete(body.id);
      return { ok: true };
    }
    throw new RelayError(`Unknown chat action ${action}.`);
  },
};

// Chat from anywhere. When this device can't reach your PC directly (you're outside the office
// network), chat goes through a private GitHub repo, <you>/cairn-relay by default. Every PC signed in
// to the same GitHub account has its own folder there, and you pick which one you talk to:
//   devices/<pc>/requests/<id>.json   this device writes one per message, stop or delete
//   devices/<pc>/threads/<chat>.json  that PC answers here as the reply streams in ("request" = last one it handled)
//   devices/<pc>/index.json           that PC's name, chat list, models and when it last checked in ("pcSeen")
// A relay made before PCs had folders keeps requests/, threads/ and index.json at the top; that still works.
// Your PC runs the other half (cairn/relay.py) inside the desktop app after `cairn relay setup`.
// Reads use ETags, so polling while you wait for an answer costs almost nothing against GitHub's limits.

import { authFetch, session } from "./auth.js";
import { decodeBase64, encodeBase64 } from "./github.js";

const API = "https://api.github.com";
const REPO_KEY = "cairn.relayRepo";
const DEVICE_KEY = "cairn.relayDevice";
const REPO_RE = /^[A-Za-z0-9-]{1,39}\/[A-Za-z0-9._-]{1,100}$/;
export const TOKEN_URL = "https://github.com/settings/personal-access-tokens/new";

const fetched = new Map(); // url -> { etag, data }
const waiting = new Map(); // chat id -> { request, thread } until the PC has picked the message up

export class RelayError extends Error {}

// The PC this phone is talking to ("" = a relay from before PCs had their own folders).
let activeDevice = localStorage.getItem(DEVICE_KEY) || "";
const base = () => (activeDevice ? `devices/${activeDevice}/` : "");

export function setRelayDevice(id) {
  activeDevice = String(id || "");
  if (activeDevice) localStorage.setItem(DEVICE_KEY, activeDevice);
  else localStorage.removeItem(DEVICE_KEY);
  waiting.clear();
}

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

// One GitHub contents answer (a file or a folder listing), or null if it isn't there.
// Unchanged answers cost a free 304, so listing every PC on each refresh stays cheap.
async function readContents(path, parse) {
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
    data = parse(body);
  } catch {
    data = null;
  }
  fetched.set(url, { etag: response.headers.get("etag") || "", data });
  return data;
}

const readJson = (path) => readContents(path, (body) => JSON.parse(decodeBase64(body.content)));
const readFolder = (path) => readContents(path, (body) => (Array.isArray(body) ? body : null));

// Every PC that has joined the relay, most recently seen first.
async function listDevices() {
  const items = await readFolder("devices");
  const folders = (items || []).filter((item) => item.type === "dir");
  const found = await Promise.all(folders.map(async (folder) => {
    const index = await readJson(`devices/${folder.name}/index.json`).catch(() => null);
    return index ? { ...index, id: folder.name, name: index.device?.name || folder.name } : null;
  }));
  return found.filter(Boolean).sort((a, b) => (b.pcSeen || 0) - (a.pcSeen || 0));
}

async function postRequest(request) {
  let response;
  try {
    response = await authFetch(contentsUrl(`${base()}requests/${request.id}.json`), {
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
    const devices = await listDevices();
    let index;
    if (devices.length) {
      const chosen = devices.find((d) => d.id === activeDevice) || devices[0];
      if (chosen.id !== activeDevice) setRelayDevice(chosen.id);
      index = chosen;
    } else {
      if (activeDevice) setRelayDevice("");
      index = await readJson("index.json");
    }
    if (!index) throw new RelayError(accessHelp());
    return {
      available: index.available !== false,
      models: index.models || [],
      chats: index.chats || [],
      pcSeen: index.pcSeen || 0,
      device: activeDevice,
      devices: devices.map(({ id, name, pcSeen }) => ({ id, name, pcSeen: pcSeen || 0 })),
    };
  },

  async chatThread(id) {
    const remote = await readJson(`${base()}threads/${id}.json`);
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
      const known = waiting.get(id)?.thread || fetched.get(contentsUrl(`${base()}threads/${id}.json`))?.data || {
        id, title: text.split("\n")[0].slice(0, 60), model: body.model || "", messages: [],
      };
      const now = Date.now() / 1000;
      const thread = {
        ...known,
        status: "running",
        messages: [
          ...(known.messages || []),
          { role: "user", text, at: now },
          { role: "assistant", text: "", at: now, model: body.model || known.model || "", pending: true, waiting: true },
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

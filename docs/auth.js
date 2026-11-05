// Sign in with GitHub, without a Cairn server.
//
// Two ways in, same result (a GitHub token held in this browser):
//   1. Device flow: "Continue with GitHub" shows a short code you confirm on github.com.
//      GitHub's device endpoints don't allow browser calls (no CORS), so a tiny Worker
//      (worker/cairn-auth.js) relays exactly those two requests. It needs no client secret.
//      It is enabled by <meta name="cairn-auth" content="https://...workers.dev">.
//   2. Personal access token: works with zero setup, on any device.
//
// Rules this module enforces (and tests check):
//   - The token lives in sessionStorage (this tab) unless the user ticks "Remember on this device".
//   - Signing out removes it from both storages.
//   - authFetch() only ever attaches the token to https://api.github.com/ URLs.
//   - A 401 from GitHub ends the session and tells the app why.

export const API = "https://api.github.com";
export const SCOPE = "public_repo";
const KEY = "cairn.session";

export class AuthError extends Error {}

// ------------------------------------------------------------ session storage

function readStored(storage) {
  try {
    const value = JSON.parse(storage.getItem(KEY) || "null");
    return value && typeof value.token === "string" && value.token ? value : null;
  } catch {
    return null;
  }
}

export function session() {
  return readStored(sessionStorage) || readStored(localStorage);
}

export function signedIn() {
  return Boolean(session());
}

function store(value, remember) {
  sessionStorage.removeItem(KEY);
  localStorage.removeItem(KEY);
  (remember ? localStorage : sessionStorage).setItem(KEY, JSON.stringify({ ...value, remember: Boolean(remember) }));
}

export function signOut(reason = "") {
  const was = signedIn();
  sessionStorage.removeItem(KEY);
  localStorage.removeItem(KEY);
  // Cached API answers may include private permission data; drop them with the session.
  for (const storage of [sessionStorage]) {
    for (let i = storage.length - 1; i >= 0; i -= 1) {
      const key = storage.key(i);
      if (key && key.startsWith("cairn.gh:")) storage.removeItem(key);
    }
  }
  if (was) window.dispatchEvent(new CustomEvent("cairn:signed-out", { detail: { reason } }));
}

// ------------------------------------------------------------ authenticated fetch

export function isGitHubApi(url) {
  return String(url).startsWith(`${API}/`);
}

export async function authFetch(url, options = {}) {
  const headers = { Accept: "application/vnd.github+json", ...(options.headers || {}) };
  const current = session();
  if (current && isGitHubApi(url)) headers.Authorization = `Bearer ${current.token}`;
  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch {
    throw new AuthError("Can't reach GitHub. Check your connection and try again.");
  }
  if (response.status === 401 && current && isGitHubApi(url)) {
    signOut("Your GitHub sign-in expired or was revoked. Sign in again to keep editing.");
    throw new AuthError("Your GitHub sign-in expired or was revoked. Sign in again to keep editing.");
  }
  return response;
}

// ------------------------------------------------------------ sign in with a token

const TOKEN_RE = /^(gh[pousr]_[A-Za-z0-9_]{20,255}|github_pat_[A-Za-z0-9_]{20,255}|[a-f0-9]{40})$/;

export function looksLikeToken(text) {
  return TOKEN_RE.test(String(text || "").trim());
}

async function profileFor(token) {
  let response;
  try {
    response = await fetch(`${API}/user`, {
      headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new AuthError("Can't reach GitHub. Check your connection and try again.");
  }
  if (response.status === 401) throw new AuthError("GitHub didn't accept that token. Check it was copied in full and hasn't expired.");
  if (!response.ok) throw new AuthError(`GitHub answered ${response.status}. Try again in a moment.`);
  const user = await response.json();
  const scopes = response.headers.get("x-oauth-scopes");
  return {
    login: user.login,
    name: user.name || user.login,
    avatar: user.avatar_url || "",
    // Classic tokens and OAuth report scopes; fine-grained tokens don't (their access is per repo).
    scopes: scopes === null ? null : scopes.split(",").map((s) => s.trim()).filter(Boolean),
  };
}

export async function signInWithToken(raw, { remember = false } = {}) {
  const token = String(raw || "").trim();
  if (!looksLikeToken(token)) throw new AuthError("That doesn't look like a GitHub token. It starts with ghp_, gho_ or github_pat_.");
  const profile = await profileFor(token);
  store({ token, method: "token", ...profile }, remember);
  return session();
}

// ------------------------------------------------------------ sign in with the device flow

export function authUrl() {
  const value = document.querySelector('meta[name="cairn-auth"]')?.content?.trim() || "";
  return /^https:\/\//.test(value) || /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(value) ? value.replace(/\/+$/, "") : "";
}

async function relay(path, body) {
  const base = authUrl();
  if (!base) throw new AuthError("Sign-in with GitHub isn't set up on this site yet. Use a token instead.");
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new AuthError("Can't reach the sign-in service. Check your connection, or use a token instead.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok && !data.error) throw new AuthError(`Sign-in service answered ${response.status}.`);
  return data;
}

// Step 1: ask GitHub for a code the user types at github.com/login/device.
export async function startDeviceFlow() {
  const data = await relay("/device/code", {});
  if (!data.device_code || !data.user_code) throw new AuthError(data.error_description || "GitHub didn't return a sign-in code.");
  return {
    deviceCode: data.device_code,
    userCode: data.user_code,
    verificationUri: data.verification_uri || "https://github.com/login/device",
    interval: Math.max(5, Number(data.interval) || 5),
    expiresAt: Date.now() + (Number(data.expires_in) || 900) * 1000,
  };
}

const sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new AuthError("Sign-in cancelled."));
    });
  });

// Step 2: wait until the user approves, following GitHub's pacing rules (interval, slow_down).
export async function finishDeviceFlow(flow, { remember = false, signal, pollMs } = {}) {
  let interval = flow.interval * 1000;
  while (Date.now() < flow.expiresAt) {
    await sleep(pollMs ?? interval, signal);
    const data = await relay("/oauth/access_token", { device_code: flow.deviceCode });
    if (data.access_token) {
      const profile = await profileFor(data.access_token);
      store({ token: data.access_token, method: "device", ...profile }, remember);
      return session();
    }
    if (data.error === "authorization_pending") continue;
    if (data.error === "slow_down") {
      interval = (Number(data.interval) || flow.interval + 5) * 1000;
      continue;
    }
    if (data.error === "access_denied") throw new AuthError("Sign-in was cancelled on GitHub.");
    if (data.error === "expired_token") break;
    throw new AuthError(data.error_description || "GitHub sign-in failed. Try again.");
  }
  throw new AuthError("The sign-in code expired. Start again to get a new one.");
}

// Links that open GitHub's token page with sensible defaults.
export const TOKEN_LINKS = {
  classic: `https://github.com/settings/tokens/new?scopes=${SCOPE}&description=${encodeURIComponent("Cairn")}`,
  fineGrained: "https://github.com/settings/personal-access-tokens/new",
};

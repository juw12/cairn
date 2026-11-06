// The "Sign in with GitHub" dialog: device flow when the relay is configured, a pasted token otherwise.
// Talks only to auth.js; the app decides what happens after sign-in through onSignedIn.

import * as auth from "./auth.js";
import { escapeHtml as esc } from "./md.js";

const $ = (selector, root = document) => root.querySelector(selector);

export function openSignIn({ icon, onSignedIn }) {
  if ($("#signin-dialog")) return;
  const deviceReady = Boolean(auth.authUrl());
  const wrap = document.createElement("div");
  wrap.className = "modal-wrap";
  wrap.id = "signin-dialog";
  wrap.innerHTML = `
    <div class="modal signin" role="dialog" aria-modal="true" aria-labelledby="signin-title">
      <h2 id="signin-title">Sign in with GitHub</h2>
      <p>Edit your brain from any device. Your notes stay in your own GitHub repo; Cairn has no server and no database.</p>
      <ul class="signin-facts">
        <li><strong>Access:</strong> read and write your <em>public</em> repos (GitHub calls this <code>public_repo</code>). Cairn only touches <code>brain/</code> folders.</li>
        <li><strong>Where it lives:</strong> in this browser only, sent only to <code>api.github.com</code>.</li>
        <li><strong>Undo:</strong> sign out any time, or revoke access on GitHub.</li>
      </ul>
      ${deviceReady ? `
        <button type="button" class="btn primary wide" data-signin="device">${icon("github", "sm")} Continue with GitHub</button>
        <p class="hint">You approve on github.com. The sign-in passes once through Cairn's tiny relay, which keeps nothing; after that Cairn talks only to GitHub.</p>
        <div class="device-step" id="device-step" hidden></div>
        <div class="or"><span>or paste a token</span></div>` : ""}
      <form id="token-form" class="token-form" autocomplete="off">
        <label for="token-input">Personal access token</label>
        <input id="token-input" class="input" type="password" spellcheck="false" placeholder="github_pat_… or ghp_…">
        <p class="hint"><a href="${auth.TOKEN_LINKS.fineGrained}" target="_blank" rel="noopener">Make a fine-grained token</a> (opens GitHub with the name, 90-day expiry and <em>Contents: Read and write</em> filled in; pick <em>Only select repositories</em> and choose your brain). It's the narrowest access. To create new brains from here, use a <a href="${auth.TOKEN_LINKS.classic}" target="_blank" rel="noopener">classic token with public_repo</a> instead.</p>
        <label class="check"><input type="checkbox" id="signin-remember"> Remember on this device</label>
        <p class="hint" id="remember-hint">Off: forgotten when you close this tab. Turn it on only on your own device.</p>
        <p class="form-error" id="signin-error" role="alert" hidden></p>
        <div class="actions">
          <button type="button" class="btn ghost" data-cancel>Cancel</button>
          <button type="submit" class="btn ${deviceReady ? "" : "primary"}" id="token-submit">Sign in with token</button>
        </div>
      </form>
    </div>`;
  document.body.appendChild(wrap);
  const controller = new AbortController();
  const close = () => {
    controller.abort();
    wrap.remove();
  };
  const showError = (message) => {
    const el = $("#signin-error", wrap);
    el.textContent = message;
    el.hidden = !message;
  };
  const remember = () => $("#signin-remember", wrap).checked;
  $("#signin-remember", wrap).addEventListener("change", (e) => {
    $("#remember-hint", wrap).textContent = e.target.checked
      ? "On: kept in this browser until you sign out. Use only on your own device."
      : "Off: forgotten when you close this tab. Turn it on only on your own device.";
  });
  wrap.addEventListener("click", (e) => {
    if (e.target === wrap || e.target.closest("[data-cancel]")) close();
  });
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  });
  $("#token-form", wrap).addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("#token-submit", wrap);
    button.disabled = true;
    button.textContent = "Checking…";
    showError("");
    try {
      const me = await auth.signInWithToken($("#token-input", wrap).value, { remember: remember() });
      close();
      onSignedIn(me);
    } catch (error) {
      showError(error.message);
      button.disabled = false;
      button.textContent = "Sign in with token";
    }
  });
  $("[data-signin=device]", wrap)?.addEventListener("click", async (e) => {
    const step = $("#device-step", wrap);
    e.currentTarget.disabled = true;
    showError("");
    try {
      const flow = await auth.startDeviceFlow();
      step.hidden = false;
      step.innerHTML = `
        <p>Enter this code on GitHub:</p>
        <div class="device-code" id="device-code">${esc(flow.userCode)}</div>
        <a class="btn primary wide" id="device-open" href="${esc(flow.verificationUri)}" target="_blank" rel="noopener">${icon("copy", "sm")} Copy code and open GitHub</a>
        <p class="muted waiting">Waiting for you to approve on GitHub…</p>`;
      $("#device-open", wrap).addEventListener("click", () => navigator.clipboard?.writeText(flow.userCode).catch(() => {}));
      const me = await auth.finishDeviceFlow(flow, { remember: remember(), signal: controller.signal, pollMs: window.CAIRN_TEST_POLL_MS });
      close();
      onSignedIn(me);
    } catch (error) {
      if (controller.signal.aborted) return;
      showError(error.message);
      step.hidden = true;
      e.currentTarget.disabled = false;
    }
  });
  setTimeout(() => (deviceReady ? $("[data-signin=device]", wrap) : $("#token-input", wrap))?.focus(), 0);
}

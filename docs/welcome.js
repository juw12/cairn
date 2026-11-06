// The welcome screen shown before a brain is open.

import { escapeHtml as esc } from "./md.js";
import { FolderStore, DemoStore } from "./store.js";
import * as gh from "./github.js";
import * as auth from "./auth.js";
import { $, LOGO, icon, toast } from "./ui.js";
import { LAST_BRAIN_KEY, REPO_URL } from "./state.js";
import { copyText } from "./actions.js";
import { start } from "./app.js";

function exploreTarget(raw) {
  const parsed = gh.parseGithubInput(raw);
  if (!parsed) return null;
  return parsed.repo ? `?brain=${encodeURIComponent(parsed.owner + "/" + parsed.repo)}` : `?u=${encodeURIComponent(parsed.owner)}`;
}


function welcomeAccount() {
  const me = auth.session();
  const last = localStorage.getItem(LAST_BRAIN_KEY);
  if (me) {
    return `
      <div class="welcome-account">
        <img src="${esc(me.avatar)}" alt="" width="28" height="28">
        <span>Signed in as <strong>@${esc(me.login)}</strong></span>
        ${last ? `<button class="btn primary" data-action="open-brain" data-repo="${esc(last)}">${icon("arrow", "sm")} Continue in ${esc(last)}</button>` : ""}
        <a class="btn" href="?u=${encodeURIComponent(me.login)}">Your brains</a>
      </div>`;
  }
  return `
    <div class="welcome-account">
      <span>Keep your brain on GitHub and edit it from your phone, laptop or any browser.</span>
      <button class="btn" data-action="sign-in">${icon("github", "sm")} Sign in with GitHub</button>
    </div>`;
}

export function showWelcome(remembered) {
  const welcome = $("#welcome");
  const folderSupported = FolderStore.supported();
  welcome.innerHTML = `
    <div class="welcome-inner">
      <div class="brand">${LOGO}<span>Cairn</span></div>
      <h1>A second brain that lives <em>inside your repo</em></h1>
      <p class="lede">Capture ideas, brainstorm, record decisions and move tasks, all as plain Markdown in a <code>brain/</code> folder next to your code. Works alongside Obsidian, VS Code and any AI assistant.</p>
      <div class="choices">
        <section class="card choice">
          <span class="badge-icon">${icon("play")}</span>
          <h2>Try the demo</h2>
          <p>A sample project (an offline hiking app) with ideas, tasks, decisions and notes. Saved only in this browser.</p>
          <button class="btn primary" data-start="demo">${icon("arrow")} Open the demo</button>
          <button class="sub-link" data-start="empty">or start with an empty brain</button>
        </section>
        <section class="card choice">
          <span class="badge-icon">${icon("folder")}</span>
          <h2>Open a repo folder</h2>
          <p>${folderSupported ? "Pick your project folder. Cairn reads and writes its <code>brain/</code> folder directly. Nothing is uploaded." : "Needs Chrome or Edge on a computer. You can still use the demo or the local app."}</p>
          ${remembered ? `<button class="btn primary" data-start="reopen">${icon("folder")} Reopen “${esc(remembered.root.name)}”</button>` : ""}
          <button class="btn ${remembered ? "" : "primary"}" data-start="folder" ${folderSupported ? "" : "disabled"}>${icon("folder")} Choose a folder</button>
        </section>
        <section class="card choice">
          <span class="badge-icon">${icon("terminal")}</span>
          <h2>Get the desktop app</h2>
          <p>Its own window, opens your Obsidian vault by itself, syncs with GitHub in one click and updates itself. Windows, no admin needed.</p>
          <a class="btn primary" href="Cairn-Setup.cmd" download="Cairn-Setup.cmd">${icon("arrow")} Download for Windows</a>
          <button class="sub-link" data-copy-cmd>Mac or Linux? Copy the two terminal commands</button>
        </section>
      </div>
      ${welcomeAccount()}
      <form class="explore-box" id="explore-form" autocomplete="off">
        <label for="explore-input">Or look someone up</label>
        <div class="explore-row">
          ${icon("search")}
          <input id="explore-input" name="who" placeholder="github.com/username, username/repo or just a username">
          <button class="btn" type="submit">Explore</button>
        </div>
      </form>
      <div class="welcome-points">
        <div><strong>Plain files</strong>Markdown with a tiny header. Delete Cairn and your brain still reads fine on GitHub.</div>
        <div><strong>Knows your code</strong>Turns TODO comments into tasks and keeps the file and line.</div>
        <div><strong>Made for AI too</strong>One click writes a context brief any assistant can read.</div>
        <div><strong>Private</strong>No account, no server, no tracking. MIT licensed.</div>
      </div>
      <p class="welcome-foot"><a href="${REPO_URL}" target="_blank" rel="noopener">Source on GitHub</a> · <a href="https://juw12.github.io/p/cairn/" target="_blank" rel="noopener">About Cairn</a></p>
    </div>`;
  welcome.hidden = false;
  $("#explore-form", welcome).onsubmit = (event) => {
    event.preventDefault();
    const target = exploreTarget(event.target.who.value);
    if (!target) return toast("That doesn't look like a GitHub username or link.", { error: true });
    location.href = location.pathname + target;
  };
  welcome.onclick = async (event) => {
    const copyButton = event.target.closest("[data-copy-cmd]");
    if (copyButton) return copyText(`git clone ${REPO_URL}\npython -m cairn --root ~/your-repo`, "Commands copied");
    const button = event.target.closest("[data-start]");
    if (!button) return;
    try {
      const kind = button.dataset.start;
      if (kind === "demo" || kind === "empty") {
        const store = new DemoStore();
        if (kind === "empty") store.reset(DemoStore.empty());
        localStorage.setItem("cairn.mode", "demo");
        return start(store);
      }
      const store = kind === "reopen" ? remembered : await FolderStore.pick();
      if (!(await store.ensurePermission())) return toast("Cairn needs permission to read and write that folder.", { error: true });
      localStorage.setItem("cairn.mode", "folder");
      return start(store);
    } catch (error) {
      if (error.name !== "AbortError") toast(error.message || "Couldn’t open that folder", { error: true });
    }
  };
}

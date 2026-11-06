// The network: profiles, discover, read-only brains, sign-in and your own brains.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { ServerStore, FolderStore, GitHubStore } from "./store.js";
import * as gh from "./github.js";
import * as auth from "./auth.js";
import { openSignIn as showSignInDialog } from "./signin.js";
import { $, icon, pageHead, relativeTime, toast, today } from "./ui.js";
import { LAST_BRAIN_KEY, readOnly, state } from "./state.js";
import { flushPending, renderDrawer } from "./drawer.js";
import { applyInfo, render, renderSidebarFoot, renderView, setView } from "./app.js";

// ============================================================ network: profiles, discover, read-only brains

export function readOnlyBanner() {
  if (!readOnly()) return "";
  const { owner, repoName, url, avatar } = state.info;
  return `
    <div class="ro-banner" role="note">
      ${avatar ? `<img class="ro-avatar" src="${esc(avatar)}" alt="" width="28" height="28">` : ""}
      <div class="ro-text"><strong>@${esc(owner)}'s brain</strong><span>${esc(owner)}/${esc(repoName)} · ${icon("lock", "sm")} read-only</span></div>
      <div class="ro-actions">
        <button class="btn sm ghost" data-action="open-profile" data-login="${esc(owner)}">Profile</button>
        <a class="btn sm ghost" href="${esc(url)}" target="_blank" rel="noopener">${icon("github", "sm")} Repo</a>
        <a class="btn sm primary" href="${esc(gh.joinRequestUrl(owner, repoName))}" target="_blank" rel="noopener">Ask to join</a>
        <button class="btn sm" data-action="leave-brain">Back to my brain</button>
      </div>
    </div>`;
}

function brainNotes(files) {
  return files.filter((f) => !core.SPECIAL_FILES.includes(f.path)).map((f) => core.noteFromFile(f.path, f.text, f.mtime));
}

function proofOfWork(notes) {
  const pick = (type, statuses) => notes.filter((n) => n.type === type && statuses.includes(n.status));
  return {
    now: pick("task", ["doing"]),
    shipped: pick("task", ["done"]),
    decisions: pick("decision", ["accepted"]),
    open: notes.filter((n) => n.type === "idea" && (n.status === "ready" || n.tags.includes("help-wanted"))),
    ideas: pick("idea", ["raw", "exploring", "ready"]).length,
  };
}

export async function lookupProfile(raw) {
  const parsed = gh.parseGithubInput(raw);
  state.peopleQuery = String(raw || "").trim();
  if (!parsed) {
    state.profile = { error: "That doesn't look like a GitHub username or link." };
    return setView("people");
  }
  if (parsed.repo) return openBrain(`${parsed.owner}/${parsed.repo}`);
  state.profile = { loading: true, login: parsed.owner };
  setView("people");
  try {
    const [user, brains] = await Promise.all([gh.fetchUser(parsed.owner), gh.findBrains(parsed.owner)]);
    state.profile = { user, brains, proof: null };
    renderView();
    if (brains.length) {
      const loaded = await gh.loadBrainFiles(brains[0].owner, brains[0].name, brains[0].branch);
      state.profile.proof = proofOfWork(brainNotes(loaded.files));
      state.profile.proofRepo = brains[0];
    }
  } catch (error) {
    state.profile = { ...(state.profile || {}), loading: false, error: error.message };
  }
  if (state.view === "people") renderView();
}

async function loadDiscover() {
  if (state.discover) return;
  state.discover = { loading: true, items: [] };
  try {
    state.discover = { items: await gh.discoverBrains() };
  } catch (error) {
    state.discover = { items: [], error: error.message };
  }
  if (state.view === "people") renderView();
}

export function openBrain(fullName) {
  location.href = `${location.pathname}?brain=${encodeURIComponent(fullName)}`;
}

function proofList(title, items, empty, meta = () => "") {
  return `<section class="proof-col"><h3>${title} <span class="muted">${items.length}</span></h3>${
    items.length
      ? `<ul>${items.slice(0, 5).map((n) => `<li><span class="dot t-${n.type}"></span>${esc(n.title)}${meta(n)}</li>`).join("")}</ul>`
      : `<p class="muted">${empty}</p>`
  }</section>`;
}

function profileBlock() {
  const p = state.profile;
  if (!p) return "";
  if (p.loading && !p.user) return `<div class="profile-card skeleton"><div class="avatar-lg"></div><div><h2>Looking up @${esc(p.login)}…</h2></div></div>`;
  if (p.error && !p.user) return `<div class="notice">${icon("search")}<div>${esc(p.error)}</div></div>`;
  const { user, brains, proof, proofRepo } = p;
  const facts = [user.location, user.since ? `on GitHub since ${user.since}` : ""].filter(Boolean).map(esc).join(" · ");
  const first = brains[0];
  return `
    <article class="profile-card">
      <img class="avatar-lg" src="${esc(user.avatar)}" alt="" width="96" height="96">
      <div class="profile-main">
        <h2>${esc(user.name)}</h2>
        <p class="handle">@${esc(user.login)}${facts ? ` · ${facts}` : ""}</p>
        ${user.bio ? `<p class="bio">${esc(user.bio)}</p>` : ""}
        <div class="actions">
          ${first ? `<button class="btn primary" data-action="open-brain" data-repo="${esc(first.fullName)}">Open brain</button>` : ""}
          ${first ? `<a class="btn" href="${esc(gh.joinRequestUrl(first.owner, first.name))}" target="_blank" rel="noopener">Ask to join</a>` : ""}
          <a class="btn ghost" href="${esc(user.url)}" target="_blank" rel="noopener">${icon("github", "sm")} GitHub</a>
          ${user.blog ? `<a class="btn ghost" href="${esc(user.blog.startsWith("http") ? user.blog : "https://" + user.blog)}" target="_blank" rel="noopener">${icon("external", "sm")} Website</a>` : ""}
        </div>
      </div>
    </article>
    ${
      proof
        ? `<div class="proof">
            <p class="proof-note">Proof of work from <strong>${esc(proofRepo.fullName)}</strong>. Read from their files, not self-reported.</p>
            <div class="proof-grid">
              ${proofList("Working on", proof.now, "Nothing in progress.")}
              ${proofList("Shipped", proof.shipped, "Nothing shipped yet.")}
              ${proofList("Decisions", proof.decisions, "No decisions recorded.")}
              ${proofList("Open to collaborate", proof.open, "No ideas flagged ready or #help-wanted.")}
            </div>
          </div>`
        : brains.length
          ? `<p class="muted">Reading their brain…</p>`
          : `<div class="notice">${icon("lock")}<div><strong>@${esc(user.login)} hasn't published a brain yet.</strong><br>A brain becomes visible here when its repo has the <code>${gh.BRAIN_TOPIC}</code> topic. Nothing private is ever shown.</div></div>`
    }
    ${
      brains.length
        ? `<h3 class="section-title">Published brains</h3><div class="repo-list">${brains.map(repoRow).join("")}</div>`
        : ""
    }`;
}

function repoRow(repo) {
  const updated = repo.pushedAt ? relativeTime(Date.parse(repo.pushedAt)) : "";
  return `
    <div class="repo-row">
      ${repo.avatar ? `<img src="${esc(repo.avatar)}" alt="" width="36" height="36" loading="lazy">` : ""}
      <div class="repo-main">
        <button class="link-button" data-action="open-brain" data-repo="${esc(repo.fullName)}">${esc(repo.fullName)}</button>
        <p>${esc(repo.description || "No description.")}</p>
      </div>
      <span class="muted repo-meta">${updated}</span>
      <div class="repo-actions">
        <button class="btn sm ghost" data-action="open-profile" data-login="${esc(repo.owner)}">Profile</button>
        <button class="btn sm" data-action="open-brain" data-repo="${esc(repo.fullName)}">Open</button>
      </div>
    </div>`;
}

// ------------------------------------------------------------ sign in

export function openSignIn() {
  showSignInDialog({ icon, onSignedIn: afterSignIn });
}

async function afterSignIn(me) {
  state.mine = null;
  // A GitHub brain you were reading may now be editable: reload it with your access.
  if (state.store instanceof GitHubStore) return location.reload();
  // Working in a local folder: stay there; your GitHub brains are one click away under People.
  if (state.store instanceof ServerStore || state.store instanceof FolderStore) {
    toast(`Signed in as @${me.login}. Your GitHub brains are under People.`);
    renderSidebarFoot();
    if (state.view === "people") renderView();
    return;
  }
  toast(`Signed in as @${me.login}. Opening your brain…`);
  const brain = await brainToOpen().catch(() => null);
  location.href = brain
    ? `${location.pathname}?brain=${encodeURIComponent(brain)}`
    : `${location.pathname}?u=${encodeURIComponent(me.login)}`;
}

// The brain you last edited if it is still yours, else your only brain; null means "let me choose".
async function brainToOpen() {
  const brains = await gh.myBrains();
  const last = (localStorage.getItem(LAST_BRAIN_KEY) || "").toLowerCase();
  const lastOne = brains.find((repo) => repo.fullName.toLowerCase() === last);
  if (lastOne) return lastOne.fullName;
  return brains.length === 1 ? brains[0].fullName : null;
}

export async function afterSignOut() {
  state.mine = null;
  if (state.store instanceof GitHubStore && state.store.writable) {
    // Keep what's on screen, but stop offering edits that can't be saved.
    await flushPending().catch(() => {});
    state.store.writable = false;
    state.info = await state.store.info();
    applyInfo();
    if (state.open) renderDrawer();
    render();
    return;
  }
  renderSidebarFoot();
  if (state.view === "people") renderView();
}

async function loadMine() {
  if (state.mine || !auth.signedIn()) return;
  state.mine = { loading: true, items: [] };
  try {
    state.mine = { items: await gh.myBrains() };
  } catch (error) {
    state.mine = { items: [], error: error.message };
  }
  if (state.view === "people") renderView();
}

function myBrainsBlock() {
  const me = auth.session();
  if (!me) {
    return `
      <section class="signin-cta">
        <div><h3>Edit your own brain from any device</h3><p>Sign in with GitHub. Your notes stay in your repo; each save is a commit.</p></div>
        <button class="btn primary" data-action="sign-in">${icon("github", "sm")} Sign in</button>
      </section>`;
  }
  loadMine();
  const mine = state.mine;
  const list = !mine || mine.loading
    ? `<p class="muted">Looking for your brains…</p>`
    : mine.error
      ? `<div class="notice">${icon("search")}<div>${esc(mine.error)}</div></div>`
      : mine.items.length
        ? `<div class="repo-list">${mine.items.map(repoRow).join("")}</div>`
        : `<p class="muted">No brains yet. A brain is a repo with the <code>${gh.BRAIN_TOPIC}</code> topic. Create one below.</p>`;
  return `
    <section class="my-brains">
      <h3 class="section-title">Your brains</h3>
      ${list}
      <form class="create-brain" id="create-brain-form" autocomplete="off">
        <label for="new-brain-name">New brain</label>
        <span class="prefix">github.com/${esc(me.login)}/</span>
        <input id="new-brain-name" class="input" value="${mine?.items?.length ? "" : "brain"}" placeholder="repo-name" spellcheck="false">
        <button class="btn" type="submit" data-action="create-brain">${icon("plus", "sm")} Create</button>
      </form>
      <p class="hint">Creates a public repo with the <code>${gh.BRAIN_TOPIC}</code> topic and a starter idea and task. Anyone can read it; only you can edit it.</p>
    </section>`;
}

export async function createMyBrain() {
  const input = $("#new-brain-name");
  const name = (input?.value || "").trim();
  if (!/^[A-Za-z0-9._-]{1,100}$/.test(name)) {
    toast("Use letters, numbers, dots, dashes or underscores for the repo name.", { error: true });
    input?.focus();
    return;
  }
  const button = $("#create-brain-form button");
  if (button) {
    button.disabled = true;
    button.textContent = "Creating…";
  }
  try {
    const repo = await gh.createBrain(name, "My Cairn brain: ideas, tasks and decisions in plain Markdown.", today());
    toast(`Created ${repo.fullName}`);
    openBrain(repo.fullName);
  } catch (error) {
    const message = error.conflict
      ? `You already have a repo called "${name}". Pick another name.`
      : /refused/.test(error.message || "")
        ? "Your token can't create repos. Use a classic token with public_repo, or create the repo on GitHub and add the cairn-brain topic."
        : error.message;
    toast(message || "Couldn't create the brain.", { error: true });
    if (button) {
      button.disabled = false;
      button.innerHTML = `${icon("plus", "sm")} Create`;
    }
  }
}

export function peopleView() {
  loadDiscover();
  const d = state.discover;
  // Don't repeat the looked-up person's own brains in the "others" list.
  const profileLogin = state.profile?.user?.login?.toLowerCase();
  const others = d?.items?.filter((repo) => repo.owner.toLowerCase() !== profileLogin) || [];
  const discoverHtml = !d || d.loading
    ? `<p class="muted">Loading published brains…</p>`
    : d.error
      ? `<div class="notice">${icon("search")}<div>${esc(d.error)}</div></div>`
      : others.length
        ? `<div class="repo-list">${others.map(repoRow).join("")}</div>`
        : `<p class="muted">${profileLogin ? "No one else has published a brain yet." : "No published brains yet. Be the first."}</p>`;
  const publishHint = state.info.mode === "server" || state.info.mode === "folder"
    ? `<section class="publish"><h3>Publish your brain</h3><p>Push your repo to GitHub as public and add one topic. Only repos you mark this way ever appear here.</p><code>gh repo edit OWNER/${esc(state.info.project)} --add-topic ${gh.BRAIN_TOPIC}</code></section>`
    : `<section class="publish"><h3>Publish your brain</h3><p>Keep your <code>brain/</code> folder in a public GitHub repo and add the <code>${gh.BRAIN_TOPIC}</code> topic. Only repos you mark this way ever appear here.</p></section>`;
  return `
    ${pageHead("People", "Look up anyone on GitHub and see what they're thinking about, building and deciding. Their brain is plain files in their repo, shown read-only.")}
    <form class="people-search" id="people-form" autocomplete="off">
      <label for="people-query" class="sr-only">GitHub username or link</label>
      ${icon("search")}
      <input id="people-query" name="query" placeholder="Paste a GitHub profile or repo link, or type a username" value="${esc(state.peopleQuery)}">
      <button class="btn primary" type="submit">Look up</button>
    </form>
    ${myBrainsBlock()}
    ${profileBlock()}
    <h3 class="section-title">Recently published by others</h3>
    ${discoverHtml}
    ${publishHint}`;
}

// Proof view and claim cards: what you claim, what GitHub confirms, and vouching for others.

import * as proof from "./proof.js";
import * as gh from "./github.js";
import * as auth from "./auth.js";
import { escapeHtml as esc } from "./md.js";
import { ask, icon, pageHead, plural, toast, today } from "./ui.js";
import { createNote, readOnly, state } from "./state.js";
import { openNote } from "./drawer.js";
import { copyText } from "./actions.js";
import { renderView } from "./app.js";

const LOGIN_KEY = "cairn.proofLogin";
const LOGIN_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const io = {
  api: (path) => gh.apiGet(path),
  raw: (owner, repo, branch, path) => gh.rawText(owner, repo, branch, path),
};

// ------------------------------------------------------------ checking

// Check `claims` for `subject` into `holder.checked`, re-rendering as answers arrive.
// `isCurrent()` guards against a newer lookup having replaced this one.
export async function runCheck(holder, subject, claims, isCurrent) {
  holder.running = true;
  holder.checked = null;
  try {
    await proof.checkClaims(subject, claims, io, (checked) => {
      if (!isCurrent()) return;
      holder.checked = checked;
      if (["people", "proof"].includes(state.view)) renderView();
    });
  } finally {
    holder.running = false;
    if (isCurrent() && ["people", "proof"].includes(state.view)) renderView();
  }
}

// Whose claims the Proof view shows: the brain's owner when reading someone else's brain, else you.
function proofSubject() {
  if (readOnly()) return state.info.owner || "";
  return auth.session()?.login || state.info?.owner || localStorage.getItem(LOGIN_KEY) || "";
}

export async function checkMyClaims() {
  const subject = proofSubject();
  if (!subject) {
    toast("Enter your GitHub username first.", { error: true });
    return;
  }
  const holder = (state.proofCheck = { subject, checked: null, running: true });
  await runCheck(holder, subject, proof.claimsFromNotes(state.notes), () => state.proofCheck === holder);
}

export function setProofLogin(form) {
  const login = form.login.value.trim().replace(/^@/, "");
  if (!LOGIN_RE.test(login)) {
    toast("That doesn't look like a GitHub username.", { error: true });
    return;
  }
  localStorage.setItem(LOGIN_KEY, login);
  checkMyClaims();
}

// ------------------------------------------------------------ cards

function evidenceLabel(evidence) {
  const repo = evidence.owner ? `${evidence.owner}/${evidence.repo}` : "";
  switch (evidence.kind) {
    case "pr":
      return `${repo}#${evidence.number}`;
    case "commit":
      return `${repo}@${evidence.sha.slice(0, 7)}`;
    case "release":
      return `${repo} ${evidence.tag}`;
    case "file":
      return `${repo}/${evidence.path}`;
    case "repo":
      return repo;
    default:
      return evidence.url.replace(/^https?:\/\//, "").slice(0, 60);
  }
}

function levelBadge(level, pending = false) {
  if (pending) return `<span class="lvl-badge lvl-0" title="Checking on GitHub">Checking…</span>`;
  if (!level) return `<span class="lvl-badge lvl-0" title="Not checked yet">Not checked</span>`;
  const info = proof.levelInfo(level);
  return `<span class="lvl-badge lvl-${level}" title="${esc(info.hint)}">L${level} · ${esc(info.label)}</span>`;
}

function evidenceRow(evidence, result, pending) {
  const state = result ? (result.ok ? "ok" : "fail") : pending ? "pending" : "unchecked";
  const mark = result ? icon(result.ok ? "check" : "x", "sm") : `<span class="ev-dot"></span>`;
  const kind = evidence.vouch || result?.vouch ? "vouch" : evidence.kind;
  return `
    <li class="ev ev-${state}">
      ${mark}
      <span class="ev-kind">${esc(kind)}</span>
      <a href="${esc(evidence.url)}" target="_blank" rel="noopener">${esc(evidenceLabel(evidence))}</a>
      ${result?.note ? `<span class="ev-note">${esc(result.note)}</span>` : ""}
    </li>`;
}

// One claim. `checked` is the matching result from proof.checkClaims (or undefined before the check).
export function claimCard(claim, { checked, pending = false, subject = "", local = false, canVouch = false } = {}) {
  const results = checked?.results || [];
  const links = [...claim.evidence, ...claim.attestations.map((link) => ({ ...link, vouch: true }))];
  const meta = [claim.role, claim.when, claim.outcome].filter(Boolean).map(esc).join(" · ");
  const rows = links.length
    ? `<ul class="evidence">${links.map((link, index) => evidenceRow(link, results[index], pending && !checked)).join("")}</ul>`
    : `<p class="muted claim-empty">No evidence links yet${local ? ". Add repo, pull request, commit or release links under <code>evidence:</code>." : "."}</p>`;
  return `
    <article class="claim" data-claim="${esc(claim.slug)}">
      <div class="claim-head">
        ${levelBadge(checked?.level, pending && !checked)}
        <h3>${esc(claim.title)}</h3>
        ${local ? `<span class="pill ${claim.isPublic ? "status-done" : "status-parked"}">${claim.isPublic ? "Public" : "Private"}</span>` : ""}
      </div>
      ${meta ? `<p class="claim-meta">${meta}</p>` : ""}
      ${rows}
      <div class="claim-actions">
        ${local ? `<button class="btn sm ghost" data-open="${esc(claim.path)}">Edit note</button>` : ""}
        ${canVouch ? `<button class="btn sm" data-action="vouch" data-subject="${esc(subject)}" data-claim="${esc(claim.slug)}">${icon("check", "sm")} Vouch</button>` : ""}
      </div>
    </article>`;
}

export function levelSummary(checked) {
  const counts = proof.levelCounts(checked || []);
  return `<div class="lvl-summary">${proof.LEVELS.map(
    (info) => `<div class="lvl-cell"><span class="lvl-num lvl-${info.level}">${counts[info.level]}</span><span><strong>L${info.level} ${esc(info.label)}</strong><small>${esc(info.hint)}</small></span></div>`,
  ).join("")}</div>`;
}

// The claims block on someone's profile (People view).
export function profileClaims(profile) {
  const holder = profile.claims;
  if (!holder) return "";
  const subject = profile.user.login;
  const me = auth.session()?.login || "";
  const canVouch = me.toLowerCase() !== subject.toLowerCase();
  if (!holder.items.length) {
    return `<section class="claims"><h3 class="section-title">Claims</h3><p class="muted">@${esc(subject)} hasn't written any claims yet. A claim is a note with a <code>claim:</code> line and links to the work.</p></section>`;
  }
  const byPath = new Map((holder.checked || []).map((item) => [item.path, item]));
  return `
    <section class="claims">
      <h3 class="section-title">Claims, checked against GitHub</h3>
      ${holder.checked && !holder.running ? levelSummary(holder.checked) : ""}
      <div class="claim-list">
        ${holder.items.map((claim) => claimCard(claim, { checked: byPath.get(claim.path), pending: holder.running, subject, canVouch })).join("")}
      </div>
      <p class="hint">Levels come from GitHub, not from the note: L2 needs work this person owns or authored, L3 needs others to have merged, forked or starred it, L4 needs someone to vouch in their own repo. <a href="https://github.com/juw12/cairn/blob/main/PROOF.md" target="_blank" rel="noopener">How proof works</a></p>
    </section>`;
}

// ------------------------------------------------------------ Proof view

const CLAIM_EXAMPLE = `---
title: Built Cairn
claim: Built Cairn, a second brain that lives in your repo
role: author
evidence:
  - https://github.com/you/cairn
  - https://github.com/someone/project/pull/42
publish: true
---
What you did, what changed because of it.`;

export function proofView() {
  const claims = proof.claimsFromNotes(state.notes);
  const subject = proofSubject();
  const check = state.proofCheck && state.proofCheck.subject === subject ? state.proofCheck : null;
  // Check once on first visit (answers are cached for 10 minutes); "Check again" refreshes.
  if (subject && claims.length && !check) setTimeout(() => state.view === "proof" && !state.proofCheck && checkMyClaims(), 0);
  const byPath = new Map((check?.checked || []).map((item) => [item.path, item]));
  const local = !readOnly();
  const publicCount = claims.filter((claim) => claim.isPublic).length;
  const actions = `
    ${subject && claims.length ? `<button class="btn" data-action="proof-check" ${check?.running ? "disabled" : ""}>${icon("refresh", "sm")} ${check ? "Check again" : "Check on GitHub"}</button>` : ""}
    <button class="btn primary" data-action="new-claim">${icon("plus", "sm")} New claim</button>`;
  const who = subject
    ? `<p class="proof-who">Checked as <strong>@${esc(subject)}</strong>${local && !auth.session() ? ` · <button class="link-btn" data-action="proof-forget">change</button>` : ""}</p>`
    : `<form class="proof-login" id="proof-login-form" autocomplete="off">
        <label for="proof-login">Your GitHub username</label>
        <input id="proof-login" name="login" class="input" placeholder="octocat" spellcheck="false">
        <button class="btn" type="submit">Check my claims</button>
      </form>`;
  const list = claims.length
    ? `<div class="claim-list">${claims.map((claim) => claimCard(claim, { checked: byPath.get(claim.path), pending: check?.running, subject, local })).join("")}</div>`
    : `<div class="claim-empty-state">
        <h3>No claims yet</h3>
        <p>A claim is any note with a <code>claim:</code> line. Point <code>evidence:</code> at your repos, merged pull requests, commits or releases and Cairn checks each one on GitHub.</p>
        <pre class="claim-example">${esc(CLAIM_EXAMPLE)}</pre>
      </div>`;
  return `
    ${pageHead(
      "Proof",
      readOnly()
        ? `What @${esc(subject)} claims, and what GitHub confirms.`
        : "What you claim, and what GitHub confirms. Levels are earned from evidence, never typed in.",
      local ? actions : "",
      "Network",
    )}
    ${who}
    ${check?.checked && !check.running ? levelSummary(check.checked) : ""}
    ${list}
    ${
      local
        ? `<section class="publish">
            <h3>Publish</h3>
            <p>${plural(publicCount, "claim")} of ${claims.length} marked <code>publish: true</code>. Only notes marked that way ever leave this brain; everything else stays private.</p>
            <code>python -m cairn publish --to ${esc(subject || "OWNER")}/brain</code>
          </section>`
        : ""
    }`;
}

export async function newClaim() {
  const text = await ask({ title: "New claim", text: "One line: what did you do? You'll add the links to the work next.", placeholder: "Shipped offline maps for Trailmix", confirm: "Create" });
  if (!text) return;
  const note = await createNote({
    type: "note",
    title: text,
    tags: ["proof"],
    extraMeta: { claim: text, role: "author", evidence: [], publish: false },
    body: "What I did, and what changed because of it.\n\nAdd links under `evidence:` above: a repo, a merged pull request, a commit or a release.\n",
  });
  if (note) openNote(note.path);
}

export function forgetProofLogin() {
  localStorage.removeItem(LOGIN_KEY);
  state.proofCheck = { subject: "", checked: null, running: false };
  renderView();
}

// ------------------------------------------------------------ vouching

function claimBySlug(subject, slug) {
  const fromProfile = state.profile?.user?.login?.toLowerCase() === subject.toLowerCase() ? state.profile.claims?.items : null;
  const pool = fromProfile || proof.claimsFromNotes(state.notes);
  return pool.find((claim) => claim.slug === slug) || null;
}

// Open GitHub's "new file" page in the voucher's own repo with the vouch prefilled.
export async function vouchFor(subject, slug) {
  const claim = claimBySlug(subject, slug);
  if (!claim) return toast("That claim isn't loaded any more. Look the person up again.", { error: true });
  let voucher = auth.session()?.login || "";
  if (!voucher) {
    voucher = (await ask({ title: "Vouch as…", text: "Your vouch is a file you commit to your own public repo, so it's signed by your GitHub account.", placeholder: "your GitHub username", confirm: "Next" })) || "";
    voucher = voucher.replace(/^@/, "");
    if (!voucher) return;
  }
  if (!LOGIN_RE.test(voucher)) return toast("That doesn't look like a GitHub username.", { error: true });
  if (voucher.toLowerCase() === subject.toLowerCase()) return toast("You can't vouch for yourself.", { error: true });
  const brains = await gh.findBrains(voucher).catch(() => []);
  let repo = brains[0]?.name || "";
  let branch = brains[0]?.branch || "main";
  if (!repo) {
    repo = (await ask({ title: "Which of your public repos?", text: "The vouch goes in a vouches/ folder there.", value: "brain", confirm: "Open GitHub" })) || "";
    if (!repo) return;
    branch = "main";
  }
  const args = { subject, claim, voucher, repo, branch, today: today() };
  window.open(proof.vouchUrl(args), "_blank", "noopener");
  const link = proof.vouchLink(args);
  toast(`After you commit, send @${subject} the link so they can add it under attested_by.`, {
    action: { label: "Copy link", run: () => copyText(link, "Vouch link copied") },
  });
}

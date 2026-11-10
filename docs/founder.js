// Founder Studio inside Cairn: test a business idea on a panel of simulated buyers in a 3D market square,
// then keep what you learned in the brain. The studio itself lives in ./founder/ (its own page, styles and
// 3D scene) and runs fully in the browser; this module hosts it and turns finished panels into notes.
//
// The studio sits in one persistent element next to #view, not inside it: Cairn re-renders #view whenever
// data loads, and an iframe re-created there would reload and stop a running panel.

import { escapeHtml as esc } from "./md.js";
import { $, icon, pageHead, toast, today } from "./ui.js";
import { createNote, readOnly, state } from "./state.js";

const STUDIO_URL = "founder/index.html?project=demo-matcha";
const RESULT_TYPE = "founder-studio:result";

let stage = null;
let latest = null;
let savedKey = null;
let business = "";
let running = false;

const pct = (value) => `${Math.round((Number(value) || 0) * 100)}%`;

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin) return;
  const type = event.data?.type;
  if (type === RESULT_TYPE) {
    latest = { ...event.data, at: new Date().toISOString() };
    running = false;
  } else if (type === "founder-studio:project") {
    business = event.data.business || event.data.project || "";
  } else if (type === "founder-studio:run-start") {
    running = true;
  } else if (type === "founder-studio:run-stopped") {
    running = false;
  } else {
    return;
  }
  if (state.view === "founder") paintResult();
});

// Opens the studio's first setup step so a visitor can swap the example for their own idea.
function editIdea(section = "productSection") {
  const frame = stage?.querySelector("iframe");
  frame?.contentWindow?.postMessage({ type: "founder-studio:edit", section }, location.origin);
  frame?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function shortName(name) {
  return String(name || "").split(/[(\-\u2013\u2014,]/)[0].trim().slice(0, 32);
}

function stepItem(number, status, title, detail) {
  const mark = status === "done" ? icon("check", "sm") : number;
  return `<li class="founder-step ${status}"${status === "current" ? ' aria-current="step"' : ""}>
      <span class="founder-step-mark">${mark}</span>
      <span class="founder-step-text"><b>${title}</b><span>${detail}</span></span>
    </li>`;
}

// Three steps that follow the studio: idea loaded, panel run, result saved.
function stepsHtml() {
  const saved = Boolean(latest) && savedKey === resultKey(latest);
  const idea = business
    ? `${esc(shortName(business))} · <button class="founder-link" type="button" data-action="founder-edit">write your own</button>`
    : "Loading the example idea";
  let runStatus = business ? "current" : "todo";
  let runDetail = "Simulated buyers walk to the stall and decide";
  if (running) {
    runStatus = "current";
    runDetail = "Buyers are deciding now";
  } else if (latest) {
    runStatus = "done";
    runDetail = `${latest.buys} of ${latest.n} would buy`;
  }
  let saveStatus = "todo";
  let saveDetail = "Keep the result with your notes";
  if (latest && !running) {
    saveStatus = saved ? "done" : "current";
    if (saved) {
      saveDetail = "Saved as a founder-studio note";
    } else if (readOnly()) {
      saveDetail = "Open your own brain to save";
    } else {
      saveDetail = "Use <b>Save to brain</b> below";
    }
  }
  return `<ol class="founder-steps" aria-label="Founder Studio steps">
      ${stepItem(1, business ? "done" : "current", "Describe the idea", idea)}
      ${stepItem(2, runStatus, "Run the panel", runDetail)}
      ${stepItem(3, saveStatus, "Save to brain", saveDetail)}
    </ol>`;
}

export function founderView() {
  return `${pageHead(
    "Founder Studio",
    "Watch simulated buyers decide on your idea in a 3D market square, then keep the result with your notes.",
  )}
    <div class="founder-result" id="founder-result" aria-live="polite"></div>
    <section class="founder-saved" id="founder-saved" aria-label="Saved panels"></section>`;
}

// One concrete next move for each reason the founder skill's buyers can give for passing.
const NEXT_STEP = {
  price: "Try a lower price or say what the price buys, then run again.",
  habit: "Make switching easy (a first visit, a trial), then run again.",
  need: "Name the problem it solves in the first line of the pitch, then run again.",
  trust: "Add proof (reviews, a guarantee, who is behind it), then run again.",
  quality: "Say what makes it better than what they use now, then run again.",
  convenience: "Say where and how fast they get it, then run again.",
  timing: "Give a reason to try it now, then run again.",
  values: "Speak to what they care about (sourcing, impact), then run again.",
};

function nextStep(result) {
  if (result.rate >= result.target) {
    return "It meets your target. Next: a real launch test (pre-orders or a landing page), since simulated buyers are only a first filter.";
  }
  return NEXT_STEP[result.topReason] || "Read why buyers passed in the studio, then sharpen the pitch and run again.";
}

const tagsOf = (note) => note.tags || note.meta?.tags || [];

// Past panels saved from this page, newest first, so the brain shows the idea improving run by run.
function savedPanels() {
  return (state.notes || [])
    .filter((note) => tagsOf(note).includes("founder-studio"))
    .sort((a, b) => (b.mtime || 0) - (a.mtime || 0))
    .slice(0, 6);
}

function savedHtml() {
  const notes = savedPanels();
  if (!notes.length) {
    return `<span class="founder-saved-head">Saved panels</span>
      <span class="founder-saved-empty muted">None yet. Saved panels show up here so you can compare runs as the pitch improves.</span>`;
  }
  const rows = notes.map((note) => {
    const meta = note.meta || {};
    const rate = Number(meta.buy_rate);
    const target = Number(meta.target);
    const known = Number.isFinite(rate) && Number.isFinite(target);
    const name = String(note.title || "").replace(/^Founder Studio · /, "").replace(/ · \d{4}-\d{2}-\d{2}$/, "");
    const date = meta.created || (note.mtime ? new Date(note.mtime).toISOString().slice(0, 10) : "");
    return `<li><a class="founder-saved-item" href="#" data-open="${esc(note.path)}">
        <span class="founder-saved-date">${esc(String(date))}</span>
        <span class="founder-saved-name">${esc(shortName(name) || name)}</span>
        ${known ? `<span class="founder-saved-rate ${rate >= target ? "good" : "bad"}">${pct(rate)} <span class="muted">vs ${pct(target)}</span></span>` : ""}
      </a></li>`;
  }).join("");
  return `<span class="founder-saved-head">Saved panels</span>
    <ul class="founder-saved-list">${rows}</ul>`;
}

function paintSaved() {
  const box = $("#founder-saved");
  if (box) box.innerHTML = savedHtml();
}

function resultKey(result) {
  return result ? `${result.project}|${result.heading}|${result.at}` : null;
}

function paintResult() {
  const box = $("#founder-result");
  if (!box) return;
  paintSaved();
  if (!latest) {
    box.innerHTML = stepsHtml();
    box.querySelector("[data-action=founder-edit]")?.addEventListener("click", () => editIdea());
    return;
  }
  const good = latest.rate >= latest.target;
  const saved = savedKey === resultKey(latest);
  box.innerHTML = `${stepsHtml()}<div class="founder-strip ${good ? "good" : "bad"}">
      <div class="founder-figure"><b>${latest.buys} of ${latest.n}</b> would buy <span class="muted">(${pct(latest.rate)} vs ${pct(latest.target)} target)</span></div>
      <div class="founder-meta">${esc(latest.business)} · ${esc(latest.heading)}${latest.topReason ? ` · top reason to pass: <b>${esc(latest.topReason)}</b>` : ""}</div>
      <div class="founder-next"><b>Next:</b> ${esc(nextStep(latest))} <button class="founder-link" type="button" data-action="founder-pitch">Edit pitch</button></div>
      ${readOnly() ? "" : `<button class="btn${saved ? "" : " primary"}" data-action="founder-save" ${saved ? "disabled" : ""}>${icon(saved ? "check" : "save", "sm")} ${saved ? "Saved to brain" : "Save to brain"}</button>`}
    </div>`;
  box.querySelector("[data-action=founder-edit]")?.addEventListener("click", () => editIdea());
  box.querySelector("[data-action=founder-pitch]")?.addEventListener("click", () => editIdea("pitchSection"));
  box.querySelector("[data-action=founder-save]")?.addEventListener("click", () => {
    saveResult().catch((error) => toast(error.message, { error: true }));
  });
}

function noteBody(result) {
  const reasons = Object.entries(result.reasons || {})
    .sort((a, b) => b[1] - a[1])
    .map(([reason, count]) => `- ${reason}: ${count}`)
    .join("\n");
  return [
    `**${result.buys} of ${result.n} simulated buyers would buy** (${pct(result.rate)}), ${result.rate >= result.target ? "meeting" : "below"} the ${pct(result.target)} target.`,
    "",
    `- Business: ${result.business}`,
    `- Run: ${result.heading} (${result.mode === "ai" ? "AI buyers" : result.mode === "replay" ? "replay" : "quick sim, rule model"})`,
    result.price ? `- Price: ${result.price}` : null,
    "",
    "## Why buyers passed",
    "",
    reasons || "- nobody passed",
    "",
    "## Pitch they read",
    "",
    result.pitch ? result.pitch.split("\n").map((line) => `> ${line}`).join("\n") : "> (empty)",
    "",
    "_Simulated buyers, not customers: a cheap first filter before a real launch test._",
  ].filter((line) => line !== null).join("\n");
}

async function saveResult() {
  if (!latest) return;
  const note = await createNote({
    type: "note",
    title: `Founder Studio · ${latest.business} · ${today()}`,
    tags: ["founder-studio"],
    body: noteBody(latest),
    extraMeta: { buy_rate: Number(latest.rate.toFixed(3)), target: latest.target, project: latest.project },
  });
  savedKey = resultKey(latest);
  paintResult();
  toast(`Saved to ${note?.path || "the brain"}`);
}

function ensureStage() {
  if (stage) return stage;
  stage = document.createElement("section");
  stage.id = "founder-stage";
  stage.className = "founder-stage";
  stage.hidden = true;
  stage.innerHTML = `<iframe title="Founder Studio" src="${STUDIO_URL}" allow="fullscreen" loading="eager"></iframe>`;
  $("#view").after(stage);
  return stage;
}

// Called on every render: shows the studio only on its own page and keeps it alive everywhere else.
export function syncFounderStage() {
  const onPage = state.view === "founder";
  if (!onPage && !stage) return;
  ensureStage().hidden = !onPage;
  document.body.classList.toggle("founder-open", onPage);
}

export function wireFounder() {
  syncFounderStage();
  paintResult();
}

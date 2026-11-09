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
function editIdea() {
  const frame = stage?.querySelector("iframe");
  frame?.contentWindow?.postMessage({ type: "founder-studio:edit" }, location.origin);
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
  let runDetail = "Press <b>Run panel</b> in the studio";
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
    "Write a pitch, then watch simulated buyers walk up to your stall in a 3D market square and decide to buy or pass. Every number comes from the founder skill's own scripts. Save a finished panel to keep it with your notes.",
  )}
    <div class="founder-result" id="founder-result" aria-live="polite"></div>`;
}

function resultKey(result) {
  return result ? `${result.project}|${result.heading}|${result.at}` : null;
}

function paintResult() {
  const box = $("#founder-result");
  if (!box) return;
  if (!latest) {
    box.innerHTML = stepsHtml();
    box.querySelector("[data-action=founder-edit]")?.addEventListener("click", editIdea);
    return;
  }
  const good = latest.rate >= latest.target;
  const saved = savedKey === resultKey(latest);
  box.innerHTML = `${stepsHtml()}<div class="founder-strip ${good ? "good" : "bad"}">
      <div class="founder-figure"><b>${latest.buys} of ${latest.n}</b> would buy <span class="muted">(${pct(latest.rate)} vs ${pct(latest.target)} target)</span></div>
      <div class="founder-meta">${esc(latest.business)} · ${esc(latest.heading)}${latest.topReason ? ` · top reason to pass: <b>${esc(latest.topReason)}</b>` : ""}</div>
      ${readOnly() ? "" : `<button class="btn${saved ? "" : " primary"}" data-action="founder-save" ${saved ? "disabled" : ""}>${icon(saved ? "check" : "save", "sm")} ${saved ? "Saved to brain" : "Save to brain"}</button>`}
    </div>`;
  box.querySelector("[data-action=founder-edit]")?.addEventListener("click", editIdea);
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

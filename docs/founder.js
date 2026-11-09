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

const pct = (value) => `${Math.round((Number(value) || 0) * 100)}%`;

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin || event.data?.type !== RESULT_TYPE) return;
  latest = { ...event.data, at: new Date().toISOString() };
  if (state.view === "founder") paintResult();
});

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
    box.innerHTML = `<p class="muted founder-hint">${icon("play", "sm")}<span>Press <b>Run panel</b> below. When the buyers are done, the result shows up here so you can save it to the brain.</span></p>`;
    return;
  }
  const good = latest.rate >= latest.target;
  const saved = savedKey === resultKey(latest);
  box.innerHTML = `<div class="founder-strip ${good ? "good" : "bad"}">
      <div class="founder-figure"><b>${latest.buys} of ${latest.n}</b> would buy <span class="muted">(${pct(latest.rate)} vs ${pct(latest.target)} target)</span></div>
      <div class="founder-meta">${esc(latest.business)} · ${esc(latest.heading)}${latest.topReason ? ` · top reason to pass: <b>${esc(latest.topReason)}</b>` : ""}</div>
      ${readOnly() ? "" : `<button class="btn${saved ? "" : " primary"}" data-action="founder-save" ${saved ? "disabled" : ""}>${icon(saved ? "check" : "save", "sm")} ${saved ? "Saved to brain" : "Save to brain"}</button>`}
    </div>`;
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

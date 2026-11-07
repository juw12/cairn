// Autopilot (Cairn Pro): hand a goal over once; a local AI coding agent carries it to done.
// Runs only with the local app (desktop or `cairn serve`); the web version explains how to get it.

import { escapeHtml as esc } from "./md.js";
import { icon, pageHead, toast } from "./ui.js";
import { loadAll, state } from "./state.js";

const pilot = { data: null, selected: null, logFor: null, logSize: 0, timer: null, busy: false, wired: false, signature: null };

async function call(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: { "X-Cairn": "1", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function isLocal() {
  return state.info?.mode === "server";
}

const STATUS_LABEL = {
  queued: "waiting",
  running: "working",
  blocked: "needs you",
  done: "done",
  stopped: "stopped",
};

// ------------------------------------------------------------ rendering

function planPanel(license) {
  if (license.pro) {
    return `<div class="callout">${icon("star")}<div><strong>Cairn Pro</strong> is active${license.email ? ` for ${esc(license.email)}` : ""}. Thank you for supporting Cairn.
      <button class="btn ghost" data-pilot="unlicense" style="margin-left:8px">Remove key</button></div></div>`;
  }
  const trial = license.trialLeft
    ? `You have <strong>${license.trialLeft} free trial goal${license.trialLeft === 1 ? "" : "s"}</strong>. After that Autopilot needs Cairn Pro.`
    : `Your free trial goal is used. Autopilot needs <strong>Cairn Pro</strong>; the rest of Cairn stays free forever.`;
  const reason = license.reason ? `<div class="muted" style="margin-top:6px">${esc(license.reason)}</div>` : "";
  return `<div class="callout pilot-plan">${icon("lock")}<div style="flex:1">${trial}${reason}
    <div class="pilot-row" style="margin-top:10px">
      <a class="btn primary" href="${esc(license.buyUrl)}" target="_blank" rel="noopener">${icon("star")} Get Cairn Pro</a>
      <input class="input" id="pilot-key" placeholder="Paste your license key" autocomplete="off" style="flex:1;min-width:200px">
      <button class="btn" data-pilot="license">Activate</button>
    </div></div></div>`;
}

function jobsHtml(jobs) {
  if (!jobs.length) return `<p class="empty">No goals yet. Your first one appears here and as a task on the Board.</p>`;
  return jobs.map((job) => {
    const label = STATUS_LABEL[job.status] || job.status;
    const selected = job.id === pilot.selected ? " sel" : "";
    return `<button class="pilot-job${selected}" data-pilot-job="${esc(job.id)}">
      <span class="pilot-goal">${esc(job.goal)}</span>
      <span class="pilot-meta"><span class="pill pilot-${esc(job.status)}">${esc(label)}</span>
      <span>round ${job.rounds}</span>${job.plan === "trial" ? "<span>trial</span>" : ""}</span>
    </button>`;
  }).join("");
}

function detailHtml(job) {
  if (!job) return `<div class="empty-state">${icon("zap")}<h3>Tell it what you want</h3><p>One message. Autopilot plans it from your brain, does it, checks it and keeps going on its own.</p></div>`;
  const needs = job.status === "blocked"
    ? `<div class="callout" style="border-color:var(--warn,#c48a1a)">${icon("help")}<div><strong>Needs you:</strong> ${esc(job.reason || "comment to continue")}. Reply below and it carries on.</div></div>`
    : "";
  const controls = [
    job.status === "running" || job.status === "queued" ? `<button class="btn" data-pilot="stop">${icon("pause")} Stop</button>` : "",
    job.status === "stopped" || job.status === "blocked" || job.status === "done" ? `<button class="btn" data-pilot="resume">${icon("play")} Resume</button>` : "",
    `<button class="btn ghost" data-open="${esc(job.note)}">${icon("task")} Open task note</button>`,
    `<button class="btn ghost" data-pilot="remove">${icon("trash")} Remove</button>`,
  ].join("");
  return `<div class="pilot-head"><h3>${esc(job.goal)}</h3>
      <div class="muted">${esc(STATUS_LABEL[job.status] || job.status)} · round ${job.rounds}${job.next ? ` · next: ${esc(job.next)}` : ""}</div>
      <div class="pilot-row" style="margin-top:10px">${controls}</div></div>
    ${needs}
    <pre class="pre-box pilot-log" id="pilot-log"></pre>
    <form class="pilot-row" id="pilot-comment-form" autocomplete="off">
      <input class="input" id="pilot-comment" placeholder="Optional: steer it (used next round)" style="flex:1">
      <button class="btn">Send</button>
    </form>`;
}

export function autopilotView() {
  const head = pageHead(
    "Autopilot",
    "Hand a goal over once. A coding agent on your computer reads the brain for precedents, does the work round after round, checks it and logs progress into a task, without you typing go or continue.",
    "",
    "Cairn Pro",
  );
  if (!isLocal()) {
    return `${head}<div class="callout">${icon("terminal")}<div>Autopilot runs an AI agent on your own computer, so it lives in the Cairn desktop app (or <code>cairn serve</code>). Install it from the README, open this brain there, and Autopilot appears here.</div></div>`;
  }
  return `${head}
    <div id="pilot-plan"></div>
    <div class="pilot-grid">
      <div>
        <form id="pilot-goal-form" autocomplete="off">
          <textarea class="input" id="pilot-goal" rows="4" placeholder="What do you want? e.g. add CSV export to the reports page, test it and update the README"></textarea>
          <button class="btn primary" style="margin-top:8px;width:100%">${icon("zap")} Delegate</button>
        </form>
        <p class="muted" style="font-size:12.5px">Hard stops it never crosses: paying, sending to people, final submit, deleting your data, passwords. It asks first.</p>
        <div class="nav-label" style="margin-top:14px">Goals</div>
        <div id="pilot-jobs"></div>
      </div>
      <div id="pilot-detail"></div>
    </div>`;
}

function paint() {
  if (!pilot.data) return;
  const plan = document.getElementById("pilot-plan");
  if (plan && !plan.contains(document.activeElement)) plan.innerHTML = planPanel(pilot.data.license);
  const engine = pilot.data.engine;
  if (plan && !engine.available) {
    plan.insertAdjacentHTML("beforeend", `<div class="callout" style="margin-top:10px">${icon("terminal")}<div>Autopilot uses <strong>${esc(engine.name)}</strong> as its engine and it is not installed yet. Get it free at <a href="https://opencode.ai" target="_blank" rel="noopener">opencode.ai</a>, then reopen Cairn.</div></div>`);
  }
  const jobs = pilot.data.jobs;
  if (!pilot.selected && jobs.length) pilot.selected = jobs[0].id;
  const list = document.getElementById("pilot-jobs");
  if (list) list.innerHTML = jobsHtml(jobs);
  const detail = document.getElementById("pilot-detail");
  const job = jobs.find((item) => item.id === pilot.selected);
  if (detail && !detail.contains(document.activeElement)) {
    const key = job ? `${job.id}|${job.status}|${job.rounds}|${job.next}|${job.reason}` : "none";
    if (detail.dataset.key !== key) {
      detail.dataset.key = key;
      detail.innerHTML = detailHtml(job);
      pilot.logFor = null;
    }
  }
}

async function pollLog() {
  const box = document.getElementById("pilot-log");
  if (!box || !pilot.selected) return;
  if (pilot.logFor !== pilot.selected) {
    pilot.logFor = pilot.selected;
    pilot.logSize = 0;
    box.textContent = "";
  }
  const data = await call("GET", `api/autopilot/log?id=${encodeURIComponent(pilot.selected)}&since=${pilot.logSize}`);
  if (data.text) {
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    box.textContent += data.text;
    if (atBottom) box.scrollTop = box.scrollHeight;
  }
  pilot.logSize = data.size;
}

async function refresh() {
  if (state.view !== "autopilot" || !document.getElementById("pilot-jobs")) {
    clearInterval(pilot.timer);
    pilot.timer = null;
    return;
  }
  try {
    pilot.data = await call("GET", "api/autopilot");
    // The worker writes task notes on the server; reload notes when a job appears or changes
    // status so the Board, Today and counts show them without a page reload.
    const signature = (pilot.data.jobs || []).map((job) => `${job.id}:${job.status}`).join("|");
    if (pilot.signature !== null && signature !== pilot.signature) await loadAll().catch(() => {});
    pilot.signature = signature;
    paint();
    await pollLog();
  } catch (error) {
    toast(error.message, { error: true });
  }
}

// ------------------------------------------------------------ actions

async function act(action, body = {}) {
  if (pilot.busy) return null;
  pilot.busy = true;
  try {
    const result = await call("POST", `api/autopilot/${action}`, body);
    await refresh();
    return result;
  } catch (error) {
    if (error.status === 402) toast("Autopilot is part of Cairn Pro. Get a key to keep going.", { error: true });
    else toast(error.message, { error: true });
    return null;
  } finally {
    pilot.busy = false;
  }
}

export function wireAutopilot() {
  if (!isLocal()) return;
  const view = document.getElementById("view");
  clearInterval(pilot.timer);
  pilot.timer = setInterval(refresh, 2000);
  refresh();
  view.querySelector("#pilot-goal-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("pilot-goal");
    const goal = input.value.trim();
    if (!goal) return;
    const job = await act("start", { goal });
    if (job) {
      input.value = "";
      pilot.selected = job.id;
      toast("Delegated. It is on the Board too.");
      refresh();
    }
  });
  view.querySelector("#pilot-goal")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) view.querySelector("#pilot-goal-form").requestSubmit();
  });
  if (pilot.wired) return;
  pilot.wired = true;
  view.addEventListener("submit", async (event) => {
    if (event.target.id !== "pilot-comment-form") return;
    event.preventDefault();
    const input = document.getElementById("pilot-comment");
    const text = input.value.trim();
    if (!text || !pilot.selected) return;
    input.value = "";
    input.blur();
    await act("comment", { id: pilot.selected, text });
  });
  view.addEventListener("click", async (event) => {
    if (state.view !== "autopilot") return;
    const jobButton = event.target.closest("[data-pilot-job]");
    if (jobButton) {
      pilot.selected = jobButton.dataset.pilotJob;
      paint();
      return pollLog();
    }
    const button = event.target.closest("[data-pilot]");
    if (!button) return;
    const action = button.dataset.pilot;
    if (action === "license") {
      const key = document.getElementById("pilot-key").value.trim();
      if (!key) return toast("Paste the key from your Gumroad receipt.", { error: true });
      document.getElementById("pilot-key").blur();
      if (await act("license", { key })) toast("Cairn Pro activated. Thank you!");
    } else if (action === "unlicense") {
      await act("unlicense");
    } else if (action === "remove") {
      if (!confirm("Remove this goal from Autopilot? The task note stays in the brain.")) return;
      await act("remove", { id: pilot.selected });
      pilot.selected = null;
      refresh();
    } else if (pilot.selected) {
      await act(action, { id: pilot.selected });
    }
  });
}

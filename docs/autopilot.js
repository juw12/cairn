// Autopilot (Cairn Pro): hand a goal over once; a local AI coding agent carries it to done.
// Runs only with the local app (desktop or `cairn serve`); the web version explains how to get it.

import { escapeHtml as esc } from "./md.js";
import { icon, pageHead, toast } from "./ui.js";
import { loadAll, state } from "./state.js";

const pilot = { data: null, selected: null, logFor: null, logSize: 0, timer: null, busy: false, wired: false, signature: null, plan: "lifetime", sdk: null };

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

function money(plan, currency) {
  const value = Number(plan.price);
  const amount = Number.isInteger(value) ? String(value) : value.toFixed(2);
  return currency === "USD" ? `$${amount}` : `${amount} ${esc(currency)}`;
}

function planPanel(license) {
  if (license.pro) {
    const until = license.expires ? ` until ${esc(license.expires)}` : "";
    const who = license.email ? ` · ${esc(license.email)}` : "";
    const order = license.order ? ` · order ${esc(license.order)}` : "";
    return `<div class="callout pilot-pro">${icon("star")}<div style="flex:1"><strong>Cairn Pro</strong> · ${esc(license.planName || "active")}${until}${who}${order}
      <div class="muted" style="margin-top:4px">Thank you for supporting Cairn. Autopilot has no goal limit on this computer.</div>
      ${moveNote(license)}</div>
      <div class="pilot-pro-actions">
        ${license.key ? `<button class="btn" data-pilot="copykey">${icon("copy")} Copy your Pro key</button>` : ""}
        <button class="btn ghost" data-pilot="unlicense">Remove from this computer</button>
      </div></div>`;
  }
  const lead = license.trialLeft
    ? `You have <strong>${license.trialLeft} free goal</strong> to try Autopilot. After that it is part of Cairn Pro.`
    : `Your free goal is used. Autopilot is part of <strong>Cairn Pro</strong>; everything else in Cairn stays free.`;
  const reason = license.reason ? `<p class="pilot-reason">${esc(license.reason)}</p>` : "";
  const cards = license.plans.map((plan) => {
    const selected = plan.id === pilot.plan;
    return `<button type="button" class="pilot-plancard${selected ? " sel" : ""}" data-pilot-plan="${esc(plan.id)}" aria-pressed="${selected}">
      <span class="pilot-planname">${esc(plan.name)}</span>
      <span class="pilot-price">${money(plan, license.currency)}</span>
      <span class="pilot-blurb">${esc(plan.blurb)}</span>
    </button>`;
  }).join("");
  const checkout = license.checkout.ready
    ? `<div id="pilot-paypal" class="pilot-paypal" aria-live="polite"><span class="muted">Loading checkout…</span></div>
       <p class="pilot-fine">Pay by card or PayPal without leaving Cairn. Pro turns on the moment the payment completes.</p>`
    : `<p class="pilot-fine">${icon("help")} Checkout is not switched on in this copy of Cairn yet. If you have a Pro key, paste it below.</p>`;
  return `<section class="pilot-upgrade" aria-label="Cairn Pro">
    <div class="pilot-upgrade-head">${icon("lock")}<div><h3>Cairn Pro</h3><p>${lead}</p>${reason}</div></div>
    <div class="pilot-plans" role="group" aria-label="Choose a plan">${cards}</div>
    <ul class="pilot-perks">
      <li>${icon("zap")} Unlimited Autopilot goals</li>
      <li>${icon("folder")} Works in every brain you open on this computer</li>
      <li>${icon("terminal")} Uses the OpenCode you already have; Cairn adds no AI fees</li>
    </ul>
    ${checkout}
    <details class="pilot-keybox"${license.checkout.ready ? "" : " open"}>
      <summary>Already bought Pro, or moving to a new computer?</summary>
      ${license.restoreReady ? `<form class="pilot-restore" id="pilot-restore-form" autocomplete="off">
        <div class="pilot-fine-head">Restore a purchase</div>
        <div class="pilot-row">
          <input class="input" id="pilot-order" placeholder="PayPal order number" spellcheck="false" aria-label="PayPal order number" style="flex:1;min-width:160px">
          <input class="input" id="pilot-email" type="email" placeholder="E-mail that paid" aria-label="E-mail of the PayPal account that paid" style="flex:1;min-width:160px">
          <button class="btn">Restore</button>
        </div>
        <p class="pilot-fine">The order number is the Transaction ID on your PayPal receipt e-mail. Works on any computer, any time.</p>
      </form>` : ""}
      <div class="pilot-fine-head">Or paste a Pro key</div>
      <div class="pilot-row">
        <input class="input" id="pilot-key" placeholder="CAIRN1…" autocomplete="off" spellcheck="false" aria-label="Pro key" style="flex:1;min-width:200px">
        <button class="btn" data-pilot="license">Activate</button>
      </div>
      <p class="pilot-fine">A key turns Pro on even offline. Copy it from Cairn on a computer that already has Pro.</p>
    </details>
  </section>`;
}

function moveNote(license) {
  if (license.key) return `<div class="pilot-fine">Moving to another computer? Copy your Pro key and paste it there, or restore with your PayPal order number.</div>`;
  if (license.keyPending) {
    const why = license.keyError ? ` (${esc(license.keyError)})` : "";
    return `<div class="pilot-fine">Getting your portable Pro key from the license server${why}. Pro already works here; your PayPal order number also restores it anywhere.</div>`;
  }
  return "";
}

async function copyKey(key) {
  try {
    await navigator.clipboard.writeText(key);
    toast("Pro key copied. Paste it in Cairn on your other computer.");
  } catch {
    window.prompt("Copy your Pro key:", key);
  }
}

function engineNote(engine) {
  if (engine.available) return "";
  return `<div class="callout" style="margin-top:10px">${icon("terminal")}<div>Autopilot uses <strong>${esc(engine.name)}</strong> as its engine and it is not installed yet. Get it free at <a href="https://opencode.ai" target="_blank" rel="noopener">opencode.ai</a>, then reopen Cairn.</div></div>`;
}

// ------------------------------------------------------------ in-app checkout (PayPal buttons)

function loadPayPal(clientId, currency) {
  if (window.paypal) return Promise.resolve(window.paypal);
  if (!pilot.sdk) {
    pilot.sdk = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&currency=${encodeURIComponent(currency)}&intent=capture&components=buttons`;
      script.onload = () => (window.paypal ? resolve(window.paypal) : reject(new Error("Checkout did not load. Try again.")));
      script.onerror = () => {
        pilot.sdk = null;
        reject(new Error("Could not reach PayPal. Check the internet connection and try again."));
      };
      document.head.appendChild(script);
    });
  }
  return pilot.sdk;
}

async function mountCheckout(license) {
  const box = document.getElementById("pilot-paypal");
  if (!box || license.pro || !license.checkout.ready) return;
  const plan = license.plans.find((item) => item.id === pilot.plan) || license.plans[0];
  try {
    const paypal = await loadPayPal(license.checkout.clientId, license.currency);
    if (!box.isConnected) return;
    box.innerHTML = "";
    await paypal.Buttons({
      style: { layout: "vertical", shape: "rect", label: "pay", height: 40 },
      createOrder: (data, actions) => actions.order.create({
        intent: "CAPTURE",
        purchase_units: [{
          description: `Cairn Pro - ${plan.name}`,
          custom_id: plan.id,
          amount: { currency_code: license.currency, value: plan.price },
        }],
        application_context: { shipping_preference: "NO_SHIPPING", brand_name: "Cairn" },
      }),
      onApprove: async (data, actions) => {
        const order = await actions.order.capture();
        const unit = (order.purchase_units || [])[0] || {};
        const capture = ((unit.payments || {}).captures || [])[0] || {};
        const result = await act("purchase", {
          orderId: order.id,
          status: capture.status || order.status,
          plan: plan.id,
          amount: capture.amount || unit.amount,
          email: (order.payer || {}).email_address || "",
        });
        if (result) toast("Cairn Pro is on. Thank you!");
      },
      onError: (error) => toast(`The payment did not go through: ${(error && error.message) || error}`, { error: true }),
    }).render(box);
  } catch (error) {
    if (box.isConnected) box.innerHTML = `<span class="muted">${esc(error.message)}</span>`;
  }
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
  const { license, engine } = pilot.data;
  const planKey = JSON.stringify([license, engine.available, pilot.plan]);
  // Don't redraw under the user's cursor (typing a key, inside the checkout) unless Pro just turned on or off.
  const flipped = plan && plan.dataset.pro !== String(license.pro);
  if (plan && plan.dataset.key !== planKey && (flipped || !plan.contains(document.activeElement))) {
    plan.dataset.key = planKey;
    plan.dataset.pro = String(license.pro);
    plan.innerHTML = planPanel(license) + engineNote(engine);
    mountCheckout(license);
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
    if (event.target.id === "pilot-restore-form") {
      event.preventDefault();
      const orderId = document.getElementById("pilot-order").value.trim();
      const email = document.getElementById("pilot-email").value.trim();
      if (!orderId || !email) return toast("Enter the PayPal order number and the e-mail that paid.", { error: true });
      document.activeElement?.blur();
      if (await act("restore", { orderId, email })) toast("Cairn Pro is back on. Welcome back!");
      return;
    }
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
    const planCard = event.target.closest("[data-pilot-plan]");
    if (planCard) {
      pilot.plan = planCard.dataset.pilotPlan;
      planCard.blur();
      paint();
      return;
    }
    const button = event.target.closest("[data-pilot]");
    if (!button) return;
    const action = button.dataset.pilot;
    if (action === "license") {
      const key = document.getElementById("pilot-key").value.trim();
      if (!key) return toast("Paste your Cairn Pro key (it starts with CAIRN1).", { error: true });
      document.getElementById("pilot-key").blur();
      if (await act("license", { key })) toast("Cairn Pro is on. Thank you!");
    } else if (action === "copykey") {
      if (pilot.data?.license?.key) await copyKey(pilot.data.license.key);
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

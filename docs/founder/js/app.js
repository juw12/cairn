// Founder Studio - app controller.
import { Arena } from "./arena.js";
import {
  heuristicAnswer, suggestAddressed, vanWestendorp, groupRates, reasonCounts, median,
} from "./engine.js";
import {
  REVIEWERS, ruleReview, applyFixes, keepBest, placeholderCount, reviewerBrief, editorBrief, answeredMap,
} from "./review.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const params = new URLSearchParams(location.search);
// Segment colours come from the CSS design tokens so the legend, feed dots and 3D bots always match.
const tone = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const SEGMENT_HEX = [1, 2, 3, 4, 5, 6].map((i) => getComputedStyle(document.documentElement).getPropertyValue(`--seg-${i}`).trim() || "#475569");
const REASONS = ["price", "habit", "need", "trust", "quality", "convenience", "timing", "values", "other"];

const state = {
  projects: [],
  project: null,
  mode: "heuristic",
  customer: null,
  numbers: null,
  competitors: "",
  idea: "",
  addressed: {},
  need: {},
  runs: { A: null, B: null },
  running: false,
  skip: false,
  selected: null,
};
// Waits that "Skip to results" can cut short.
// A zero-delay timer (not a resolved promise) so an instant run still lets the browser breathe.
const pause = (ms) => (state.skip ? sleep(0) : sleep(ms));

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const speed = () => Number($("#speed").value) || 1;
const fmt = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  const abs = Math.abs(value);
  return abs >= 1000 ? Math.round(value).toLocaleString() : (Math.round(value * 100) / 100).toLocaleString();
};
const pct = (value) => (value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`);
// Prices and money: always 2 decimals below 1,000 (6.5 -> 6.50), whole numbers above.
const money = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString();
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

// Never show raw OS errors or local file paths to the user.
function friendlyError(message = "") {
  if (/WinError|being used by another process|Permission denied|EACCES|EBUSY/i.test(message)) {
    return "Project files were busy (another run was saving). Run again in a moment.";
  }
  return message.replace(/[A-Za-z]:\\[^\s'"]+|\/(?:Users|home)\/[^\s'"]+/g, "a project file");
}

function toast(message, isError = false, ms = 4200) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (node.hidden = true), ms);
}

// ---------------- 3D arena ----------------
const arena = new Arena($("#arena"), { onSelect: (id) => selectBot(id) });
// Read-only handle for automated checks (style gates); the app never relies on it.
window.__fsArena = arena;
for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => {
    arena.setView(button.dataset.view);
    for (const other of document.querySelectorAll("[data-view]")) other.classList.toggle("active", other === button);
  });
}

// ---------------- projects ----------------
function blankCustomer(name) {
  return {
    business: name,
    target: "",
    where: "",
    age: [22, 45],
    segments: [
      { name: "Early adopter", share: 0.4, income: [30000, 90000] },
      { name: "Mainstream buyer", share: 0.6, income: [25000, 70000] },
    ],
    behaviours: [
      { name: "Tries new things", share: 0.3, how: "Signs up for new tools quickly." },
      { name: "Uses a free alternative", share: 0.4, how: "Gets by with a free tool today." },
      { name: "Needs proof", share: 0.3, how: "Waits for reviews before paying." },
    ],
    objections: ["A free alternative is good enough", "Who are you? No track record", "Too expensive for what it does"],
    habits: ["you scroll social media daily"],
  };
}

// Refreshes the project picker (buy rate / verdict) without touching the live view.
async function refreshProjectList() {
  const data = await api("/api/projects");
  state.projects = data.projects;
  const select = $("#projectSelect");
  select.innerHTML = data.projects
    .map((p) => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}${p.buy_rate != null ? ` · ${pct(p.buy_rate)}` : ""}${p.verdict ? ` · ${escapeHtml(p.verdict)}` : ""}</option>`)
    .join("");
  select.value = state.project;
  return data;
}

async function loadProjects(selectName) {
  const data = await api("/api/projects");
  state.projects = data.projects;
  const select = $("#projectSelect");
  select.innerHTML = data.projects
    .map((p) => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}${p.buy_rate != null ? ` · ${pct(p.buy_rate)}` : ""}${p.verdict ? ` · ${escapeHtml(p.verdict)}` : ""}</option>`)
    .join("");
  const target = selectName || params.get("project") || data.projects[0]?.name;
  if (target) {
    select.value = target;
    await loadProject(target);
  }
}

async function loadProject(name) {
  const data = await api(`/api/project?name=${encodeURIComponent(name)}`);
  state.project = name;
  state.customer = data.customer || blankCustomer(name);
  state.numbers = data.numbers;
  state.competitors = data.competitors || "";
  state.idea = data.idea || "";
  state.official = data;
  const saved = JSON.parse(localStorage.getItem(`studio:${name}`) || "{}");
  state.need = saved.need || {};
  state.addressed = saved.addressed || {};
  $("#pitch").value = saved.pitch || data.pitch || "";
  updatePitchSummary(true);
  $("#pitchB").value = saved.pitchB || "";
  $("#idea").value = state.idea;
  $("#price").value = saved.price ?? data.numbers?.price ?? "";
  $("#anchor").value = saved.anchor ?? data.numbers?.price ?? "";
  $("#seed").value = data.seed || "";
  state.runs = { A: null, B: null };
  renderSetup();
  renderNumbers();
  renderCompetitors();
  resetLive();
  arena.setStall(state.customer.business || name, priceLabel());
  if (data.answers?.length) {
    renderResults(data.answers, data.personas, data.results, "Official panel (saved)");
    renderPricing(data.answers, null);
  }
  notifyHost("founder-studio:project", { project: name, business: state.customer.business || name });
}

function persistLocal() {
  if (!state.project) return;
  localStorage.setItem(`studio:${state.project}`, JSON.stringify({
    need: state.need,
    addressed: state.addressed,
    pitch: $("#pitch").value,
    pitchB: $("#pitchB").value,
    price: $("#price").value,
    anchor: $("#anchor").value,
  }));
}

function priceLabel() {
  const price = $("#price").value;
  const unit = state.numbers?.unit ? ` / ${state.numbers.unit}` : "";
  const amount = Number(price);
  const text = Number.isInteger(amount)
    ? amount.toLocaleString()
    : amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return price ? `${text}${unit}` : "set a price";
}

// ---------------- setup editor ----------------
// Step 1 shows a one-line summary and starts collapsed once business and price are filled.
function updateProductSummary(setOpenState = false) {
  const business = $("#business").value.trim();
  const price = $("#price").value;
  const shortName = business.split(/[(\-–—,]/)[0].trim().slice(0, 28);
  $("#productSummary").textContent = business && price ? `${shortName} · ${priceLabel()} · edit` : "";
  if (setOpenState) $("#productSection").open = !(business && price);
}

// Step 3 shows the pitch's first line when collapsed, so a filled pitch is not a wall of text.
function updatePitchSummary(setOpenState = false) {
  const pitch = $("#pitch").value.trim();
  const lineCount = pitch ? pitch.split(/\n+/).length : 0;
  const firstLine = pitch.split("\n")[0].slice(0, 44);
  $("#pitchSummary").textContent = pitch ? `${firstLine}${firstLine.length >= 44 ? "…" : ""} · ${lineCount} line${lineCount === 1 ? "" : "s"} · edit` : "";
  if (setOpenState) $("#pitchSection").open = !pitch;
}

function renderSetup() {
  const c = state.customer;
  $("#business").value = c.business || "";
  $("#target").value = c.target || "";
  $("#habits").value = (c.habits || []).join("\n");
  const segmentCount = (c.segments || []).length;
  $("#buyersSummary").textContent = `${segmentCount} segment${segmentCount === 1 ? "" : "s"} · ${$("#panelSize").value || 0} buyers · edit`;
  if (!segmentCount) $("#buyersSection").open = true;
  updateProductSummary(true);
  $("#segments").innerHTML = (c.segments || []).map((s, i) => `
    <div class="item" data-i="${i}">
      <button class="del" data-del="segments" data-i="${i}" title="Remove">×</button>
      <div class="namerow"><span class="swatch" style="background:${SEGMENT_HEX[i % SEGMENT_HEX.length]}"></span><input data-f="name" value="${escapeHtml(s.name)}" placeholder="segment"></div>
      <div class="grid3">
        <input data-f="share" type="number" step="0.05" min="0" max="1" value="${s.share}" title="share of buyers">
        <input data-f="income0" type="number" value="${s.income?.[0] ?? ""}" title="income from">
        <input data-f="income1" type="number" value="${s.income?.[1] ?? ""}" title="income to">
      </div>
    </div>`).join("");
  $("#behaviours").innerHTML = (c.behaviours || []).map((b, i) => `
    <div class="item" data-i="${i}">
      <button class="del" data-del="behaviours" data-i="${i}" title="Remove">×</button>
      <div class="grid3" style="grid-template-columns:2fr 1fr"><input data-f="name" value="${escapeHtml(b.name)}" placeholder="behaviour"><input data-f="share" type="number" step="0.05" min="0" max="1" value="${b.share}" title="share"></div>
      <input data-f="how" value="${escapeHtml(b.how || "")}" placeholder="how they buy">
      <div class="need">need <input type="range" min="0" max="1" step="0.05" data-need="${escapeHtml(b.name)}" value="${state.need[b.name] ?? 0.5}"><span>${Math.round((state.need[b.name] ?? 0.5) * 100)}</span></div>
    </div>`).join("");
  renderObjections();
  renderLegend();
}

function renderObjections() {
  const objections = state.customer.objections || [];
  const suggested = suggestAddressed(objections, $("#pitch").value);
  $("#objections").innerHTML = objections.map((o, i) => {
    const on = state.addressed[o] ?? suggested[o];
    return `<span class="chip ${on ? "on" : ""}" data-obj="${i}" title="${on ? "Pitch answers this" : "Pitch does not answer this"}">
      <span class="tick${on ? " on" : ""}" aria-hidden="true"></span> <input data-objtext="${i}" value="${escapeHtml(o)}"><span class="x" data-delobj="${i}">×</span></span>`;
  }).join("");
}

function renderLegend() {
  if (!state.current && !state.running) {
    arena.preview(Number($("#panelSize").value) || 0, (state.customer.segments || []).map((s) => Number(s.share) || 0));
    renderRoster(null);
  }
  $("#legend").innerHTML = (state.customer.segments || [])
    .map((s, i) => `<span><span class="dot" style="background:${SEGMENT_HEX[i % SEGMENT_HEX.length]}"></span>${escapeHtml(s.name)}</span>`)
    .join("");
}

function readSetup() {
  const c = state.customer;
  c.business = $("#business").value.trim();
  c.target = $("#target").value.trim();
  c.habits = $("#habits").value.split("\n").map((s) => s.trim()).filter(Boolean);
  c.segments = $$("#segments .item").map((row) => ({
    name: row.querySelector('[data-f="name"]').value.trim() || "Segment",
    share: Number(row.querySelector('[data-f="share"]').value) || 0,
    income: [Number(row.querySelector('[data-f="income0"]').value) || 0, Number(row.querySelector('[data-f="income1"]').value) || 0],
  }));
  c.behaviours = $$("#behaviours .item").map((row) => ({
    name: row.querySelector('[data-f="name"]').value.trim() || "Behaviour",
    share: Number(row.querySelector('[data-f="share"]').value) || 0,
    how: row.querySelector('[data-f="how"]').value.trim(),
  }));
  c.objections = $$("#objections [data-objtext]").map((input) => input.value.trim()).filter(Boolean);
  const problems = [];
  if (!c.business) problems.push("business name");
  if (c.segments.length < 1) problems.push("at least one segment");
  if (c.behaviours.length < 1) problems.push("at least one behaviour");
  if (c.objections.length < 2) problems.push("at least two objections");
  if (!$("#pitch").value.trim()) problems.push("a pitch");
  return problems;
}

document.addEventListener("click", (event) => {
  const add = event.target.closest("[data-add]");
  if (add) {
    readSetup();
    const kind = add.dataset.add;
    if (kind === "segments") state.customer.segments.push({ name: "New segment", share: 0.2, income: [20000, 60000] });
    if (kind === "behaviours") state.customer.behaviours.push({ name: "New behaviour", share: 0.2, how: "" });
    if (kind === "objections") state.customer.objections.push("New objection");
    renderSetup();
    return;
  }
  const del = event.target.closest("[data-del]");
  if (del) {
    readSetup();
    state.customer[del.dataset.del].splice(Number(del.dataset.i), 1);
    renderSetup();
    return;
  }
  const delObj = event.target.closest("[data-delobj]");
  if (delObj) {
    readSetup();
    state.customer.objections.splice(Number(delObj.dataset.delobj), 1);
    renderObjections();
    return;
  }
  const chip = event.target.closest(".chip");
  if (chip && event.target.tagName !== "INPUT") {
    readSetup();
    const objection = state.customer.objections[Number(chip.dataset.obj)];
    const suggested = suggestAddressed([objection], $("#pitch").value)[objection];
    state.addressed[objection] = !(state.addressed[objection] ?? suggested);
    persistLocal();
    renderObjections();
  }
});
document.addEventListener("input", (event) => {
  if (event.target.dataset.need) {
    state.need[event.target.dataset.need] = Number(event.target.value);
    event.target.nextElementSibling.textContent = Math.round(event.target.value * 100);
    persistLocal();
  }
  if (event.target.id === "pitch") renderObjections();
  if (["pitch", "pitchB", "price", "anchor"].includes(event.target.id)) persistLocal();
  if (event.target.id === "price" || event.target.id === "business") arena.setStall($("#business").value || "Your product", priceLabel());
});

// ---------------- live HUD & feed ----------------
function resetLive() {
  arena.reset();
  $("#feedList").innerHTML = "";
  $("#feedCount").textContent = "0";
  $("#botCard").hidden = true;
  $("#botInspector").hidden = true;
  renderRoster(null);
  updateHud([], 0, "ready");
}

function updateHud(answers, total, status) {
  const hint = document.getElementById("arenaHint");
  if (hint) hint.hidden = total > 0 || localStorage.getItem("studio:hintSeen") === "1";
  const buys = answers.filter((a) => a.buys).length;
  const done = answers.length;
  const planned = total || Number($("#panelSize").value) || 0;
  $("#progressText").textContent = `${done}/${planned}`;
  $("#stateText").textContent = total ? status : "not started";
  $("#hudSource").textContent = {
    heuristic: "source: Quick sim",
    ai: "source: AI model",
    replay: "source: saved panel",
  }[state.mode] || "";
  $("#progressArc").style.strokeDasharray = `${total ? (done / total) * 100 : 0} 100`;
  $("#buyCount").textContent = buys;
  $("#passCount").textContent = done - buys;
  const rate = done ? buys / done : null;
  $("#rateText").textContent = pct(rate);
  $("#rateFill").style.width = `${rate ? rate * 100 : 0}%`;
  const target = Number($("#minBuyRate").value) || 0.25;
  $("#barMark").style.left = `${target * 100}%`;
  $("#barLabel").textContent = `target ${pct(target)}`;
}

// One line under the HUD that ties the arena to the feed and, at the end, to the results.
function setTicker(html, tone = "") {
  const ticker = $("#ticker");
  ticker.hidden = !html;
  ticker.className = `ticker ${tone}`;
  ticker.innerHTML = html;
  // Phones: the full roster and decision list sit below the square; one tap jumps there.
  ticker.querySelector("[data-jump=feed]")?.addEventListener("click", () => openFeed());
  $("#tickerResults")?.addEventListener("click", () => {
    document.querySelector('.tab[data-tab="results"]')?.click();
    $(".tabs").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function segmentHex(segment) {
  const index = (state.customer?.segments || []).findIndex((s) => s.name === segment);
  return SEGMENT_HEX[(index < 0 ? 0 : index) % SEGMENT_HEX.length];
}

function feedItem(persona, answer, status) {
  let item = $(`#feedList li[data-id="${persona.id}"]`);
  if (!item) {
    item = document.createElement("li");
    item.dataset.id = persona.id;
    item.addEventListener("click", () => selectBot(persona.id));
    item.addEventListener("mouseenter", () => arena.highlight(persona.id));
    item.addEventListener("mouseleave", () => arena.highlight(state.selected));
    $("#feedList").prepend(item);
  }
  if (status !== "thinking" && answer) {
    for (const row of $$("#feedList li.latest")) row.classList.remove("latest");
    item.classList.add("latest");
  }
  setRosterState(persona, status === "thinking" ? "think" : answer?.buys ? "buy" : "pass", answer?.reason || "");
  const tag = status === "thinking" ? '<span class="tag think">thinking</span>'
    : answer?.buys ? '<span class="tag buy">BUY</span>' : '<span class="tag pass">PASS</span>';
  if (status !== "thinking" && answer) {
    const verb = answer.buys ? '<b class="buy-text">BUY</b>' : '<b class="pass-text">PASS</b>';
    setTicker(`<span class="dot" style="background:${segmentHex(persona.segment)}"></span><span><b>${escapeHtml(persona.name || persona.id)}</b> chose ${verb}: ${escapeHtml(answer.reason || answer.error || "")}</span>`
      + `<button class="ghost ticker-jump" type="button" data-jump="feed">All buyers</button>`);
  }
  item.innerHTML = `<div class="who"><span><span class="dot" style="background:${segmentHex(persona.segment)}"></span><b>${escapeHtml(persona.name || persona.id)}</b> · ${escapeHtml(persona.segment)}</span>${tag}</div>
    <div class="why">${status === "thinking" ? escapeHtml(`"${persona.objection}"…`) : escapeHtml(answer?.reason || answer?.error || "")}</div>`;
  $("#feedCount").textContent = $$("#feedList li").length;
}

// ---------------- roster: one chip per buyer, always showing their state ----------------
const ROSTER_STATES = ["wait", "think", "buy", "pass"];

function renderRoster(personas) {
  const roster = $("#roster");
  if (!personas?.length) {
    const planned = Number($("#panelSize").value) || 0;
    const segments = state.customer?.segments || [];
    const shares = segments.map((s) => Number(s.share) || 0);
    const sum = shares.reduce((a, b) => a + b, 0) || 1;
    let html = "";
    let segment = 0;
    let used = 0;
    for (let i = 0; i < Math.min(planned, 100); i++) {
      while (segment < shares.length - 1 && i >= Math.round(((used + shares[segment]) / sum) * planned)) {
        used += shares[segment];
        segment++;
      }
      html += `<span class="rchip" data-state="wait" style="--seg:${SEGMENT_HEX[segment % SEGMENT_HEX.length]}"></span>`;
    }
    roster.innerHTML = html;
    roster.classList.toggle("dense", planned > 40);
    $("#rosterSummary").textContent = `${planned} waiting in the queue`;
    return;
  }
  roster.classList.toggle("dense", personas.length > 40);
  roster.innerHTML = "";
  for (const persona of personas) {
    const chip = document.createElement("span");
    const name = persona.name || persona.id;
    chip.className = "rchip";
    chip.dataset.id = persona.id;
    chip.dataset.state = "wait";
    chip.style.setProperty("--seg", segmentHex(persona.segment));
    chip.textContent = name.split(/\s+/)[0].slice(0, 8);
    chip.title = `${name} · ${persona.segment} · waiting`;
    chip.addEventListener("mouseenter", () => {
      arena.highlight(persona.id);
      $(`#feedList li[data-id="${persona.id}"]`)?.classList.add("hover");
    });
    chip.addEventListener("mouseleave", () => {
      arena.highlight(state.selected);
      $(`#feedList li[data-id="${persona.id}"]`)?.classList.remove("hover");
    });
    chip.addEventListener("click", () => selectBot(persona.id));
    roster.appendChild(chip);
  }
  updateRosterSummary();
}

function setRosterState(persona, rosterState, reason = "") {
  const chip = $(`#roster .rchip[data-id="${persona.id}"]`);
  if (!chip || !ROSTER_STATES.includes(rosterState)) return;
  chip.dataset.state = rosterState;
  const label = { wait: "waiting", think: "thinking", buy: "BUY", pass: "PASS" }[rosterState];
  chip.title = `${persona.name || persona.id} · ${persona.segment} · ${label}${reason ? `: ${reason}` : ""}`;
  updateRosterSummary();
}

function updateRosterSummary() {
  const chips = $$("#roster .rchip");
  const count = (s) => chips.filter((c) => c.dataset.state === s).length;
  const parts = [];
  if (count("buy")) parts.push(`${count("buy")} buy`);
  if (count("pass")) parts.push(`${count("pass")} pass`);
  if (count("think")) parts.push(`${count("think")} thinking`);
  if (count("wait")) parts.push(`${count("wait")} waiting`);
  $("#rosterSummary").textContent = parts.join(" · ");
  renderMiniRoster(chips, parts);
}

// Phones: one compact strip under the square shows every buyer's state at a glance.
function renderMiniRoster(chips, parts) {
  const strip = $("#miniRoster");
  if (!strip) return;
  strip.hidden = !chips.length;
  const dots = chips.map((chip) => `<i data-state="${chip.dataset.state}" style="--seg:${chip.style.getPropertyValue("--seg")}"></i>`).join("");
  strip.innerHTML = `<span class="mini-dots">${dots}</span><span class="mini-text">${escapeHtml(parts.join(" · "))}</span>`;
}

const STATE_WORDS = { queued: "In the queue", walking: "Walking to the stall", thinking: "Reading the pitch", buy: "Buys", pass: "Passes" };

function renderInspector(persona, answer, kicker = "") {
  const box = $("#botInspector");
  const segmentIndex = (state.customer.segments || []).findIndex((s) => s.name === persona.segment);
  const color = SEGMENT_HEX[Math.max(0, segmentIndex) % SEGMENT_HEX.length];
  const botState = answer ? (answer.buys ? "buy" : "pass") : (arena.bots.get(persona.id)?.state || "queued");
  box.hidden = false;
  box.innerHTML = `
    ${kicker ? `<p class="ins-kicker">${escapeHtml(kicker)}</p>` : ""}
    <div class="ins-head">
      <span class="ins-swatch" style="background:${color}"></span>
      <h4>${escapeHtml(persona.name)}</h4>
      <button type="button" class="ins-close" aria-label="Close buyer details">Close</button>
    </div>
    <p class="ins-sub">${escapeHtml(persona.segment)} · ${escapeHtml(persona.behaviour)} · ${money(persona.income)}</p>
    <span class="ins-state ${botState}">${STATE_WORDS[botState] || botState}</span>
    <p class="ins-why">${answer ? escapeHtml(answer.reason) : `First doubt: ${escapeHtml(persona.objection)}`}</p>
    ${answer && !answer.buys && answer.would_change_my_mind ? `<p class="ins-flip">Would flip if: ${escapeHtml(answer.would_change_my_mind)}</p>` : ""}`;
  box.querySelector(".ins-close").addEventListener("click", () => {
    box.hidden = true;
    state.selected = null;
    arena.highlight(null);
  });
}

// After a run, open one concrete buyer in the inspector so the result has a face: a buyer who
// passed but says what would change their mind (the most useful next lead), else the first answer.
// Desktop only; on a phone the card would cover most of the square.
function spotlightBuyer() {
  if (window.matchMedia("(max-width: 760px)").matches) return;
  const answers = state.current?.answers || [];
  const pick = answers.find((a) => !a.buys && a.would_change_my_mind) || answers.find((a) => !a.buys) || answers[0];
  if (pick) selectBot(pick.id, "One buyer, up close");
}

function selectBot(id, kicker = "") {
  if (String(id).startsWith("R")) return selectReviewer(id);
  const run = state.current;
  if (!run) return;
  const persona = run.personas.find((p) => p.id === id);
  if (!persona) return;
  const answer = run.answers.find((a) => a.id === id);
  state.selected = id;
  arena.highlight(id);
  // Link the 3D bot, its roster chip and its feed entry.
  for (const chip of $$("#roster .rchip")) chip.classList.toggle("selected", chip.dataset.id === id);
  for (const entry of $$("#feedList li")) {
    const isSelected = entry.dataset.id === id;
    entry.classList.toggle("selected", isSelected);
    if (isSelected) entry.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  renderInspector(persona, answer, kicker);
  const card = $("#botCard");
  card.hidden = false;
  card.innerHTML = `<h4>${escapeHtml(persona.name)} · ${escapeHtml(persona.age)}</h4>
    <div class="sub">${escapeHtml(persona.segment)} · ${escapeHtml(persona.behaviour)}</div>
    <dl>
      <dt>Income</dt><dd>${money(persona.income)}</dd>
      <dt>First doubt</dt><dd>${escapeHtml(persona.objection)}</dd>
      <dt>Habit</dt><dd>${escapeHtml(persona.habit || "")}</dd>
      ${answer ? `
      <dt>Decision</dt><dd>${answer.buys ? '<span class="tag buy">BUY</span>' : '<span class="tag pass">PASS</span>'} · ${escapeHtml(answer.reason_code)}</dd>
      <dt>Why</dt><dd>${escapeHtml(answer.reason)}</dd>
      <dt>Would flip</dt><dd>${escapeHtml(answer.would_change_my_mind || "–")}</dd>
      <dt>Prices</dt><dd>too cheap ${money(answer.price_too_cheap)} · cheap ${money(answer.price_cheap)} · expensive ${money(answer.price_expensive)} · too expensive ${money(answer.price_too_expensive)}</dd>
      <dt>Engine</dt><dd>${escapeHtml(answer._engine || run.engine)}</dd>` : "<dt>Status</dt><dd>deciding…</dd>"}
    </dl>`;
}

// ---------------- running a panel ----------------
function setRunButtons(disabled) {
  for (const id of ["#runBtn", "#runAbBtn", "#loopBtn"]) {
    const button = $(id);
    if (button) button.disabled = disabled;
  }
}

function variantName(letter) {
  const base = state.mode === "ai" ? "panel-ai" : "panel-quick";
  return letter === "B" ? `${base}-b` : base;
}

async function runPanel(letter = "A") {
  if (state.running) return;
  const problems = readSetup();
  if (problems.length) return toast(`Add ${problems.join(", ")} first.`, true);
  if (state.mode === "replay") return replay();
  const pitch = letter === "B" ? $("#pitchB").value.trim() : $("#pitch").value.trim();
  if (!pitch) {
    const field = letter === "B" ? $("#pitchB") : $("#pitch");
    field.focus();
    return toast(letter === "B" ? "Write offer B first." : "Write the pitch the buyers will read first.", true);
  }
  if (letter === "B" && !state.runs.A) return toast("Run panel A first so B can use the same buyers.", true);
  const price = Number($("#price").value) || 0;
  if (!price) return toast("Set a price first.", true);
  if (state.mode === "ai" && !$("#model").value.trim() && !$("#baseUrl").value.trim() && !$("#apiKey").value.trim()) {
    toast("No model set: using server defaults (OPENAI_API_KEY / gpt-4o-mini).", false);
  }
  state.running = true;
  document.documentElement.dataset.run = "running";
  notifyHost("founder-studio:run-start", { project: state.project });
  state.skip = params.get("skip") === "1";
  setRunButtons(true);
  $("#skipBtn").hidden = state.skip;
  try {
    const seed = letter === "B" ? state.runs.A.seed : (Number($("#seed").value) || undefined);
    await executeRun({ letter, pitch, seed, variant: variantName(letter), price });
    document.documentElement.dataset.run = "done";
    spotlightBuyer();
  } catch (error) {
    document.documentElement.dataset.run = "stopped";
    notifyHost("founder-studio:run-stopped", { project: state.project });
    toast(friendlyError(error.message), true, 8000);
    updateHud(state.current?.answers || [], state.current?.personas.length || 0, "stopped");
  } finally {
    state.running = false;
    state.skip = false;
    $("#skipBtn").hidden = true;
    setRunButtons(false);
    refreshProjectList().catch(() => {});
  }
}

// One panel run: deal the buyers, animate their decisions, verify with panel.py, render results.
// Used by Run panel, offer B and every round of the review loop.
async function executeRun({ letter = null, pitch, seed, variant, price, title, addressed }) {
  const dealt = await api("/api/deal", {
    name: state.project,
    variant,
    customer: state.customer,
    pitch,
    n: Number($("#panelSize").value),
    seed,
    idea: $("#idea").value,
  });
  $("#seed").value = dealt.seed;
  const run = {
    letter, variant: dealt.variant, seed: dealt.seed, personas: dealt.personas, briefs: dealt.briefs,
    answers: [], engine: state.mode === "ai" ? `AI · ${$("#model").value || "default model"}` : "heuristic", pitch, addressed,
  };
  state.current = run;
  if (letter) state.runs[letter] = run;
  await animateRun(run, price);
  arena.settle();
  updateHud(run.answers, run.personas.length, "verifying with skill scripts…");
  const saved = await api("/api/answers", { name: state.project, variant: run.variant, answers: run.answers, price });
  run.serverResults = saved.results;
  run.serverPricing = saved.pricing;
  run.buys = run.answers.filter((a) => a.buys).length;
  run.rate = run.answers.length ? run.buys / run.answers.length : 0;
  updateHud(run.answers, run.personas.length, "done");
  const heading = title || (letter === "B" ? "Offer B" : `Panel A · ${run.engine}`);
  renderResults(run.answers, run.personas, saved.results, heading);
  const summary = state.summary;
  setTicker(
    `<span><b>${summary.buys} of ${summary.n} would buy</b> (${pct(summary.rate)}) - ${summary.rate >= summary.bar ? "meets" : "below"} your ${pct(summary.bar)} target.${summary.topReason ? ` Top reason to pass: <b>${escapeHtml(summary.topReason[0])}</b> (${summary.topReason[1]}).` : ""}</span>`
    + `<button class="ghost" id="tickerResults">See results</button>`,
    summary.rate >= summary.bar ? "good" : "bad"
  );
  renderPricing(run.answers, saved.pricing);
  if (letter === "B") renderAb();
  shareWithHost(run, summary, heading);
  return run;
}

async function animateRun(run, price) {
  resetLive();
  arena.setStall(state.customer.business, priceLabel());
  const segmentIndex = Object.fromEntries((state.customer.segments || []).map((s, i) => [s.name, i]));
  run.personas.forEach((persona, i) => arena.spawn(persona, segmentIndex[persona.segment] ?? 0, i, run.personas.length));
  renderRoster(run.personas);
  arena.setSpeed(speed());
  updateHud([], run.personas.length, state.mode === "ai" ? "AI buyers reading the pitch" : "buyers arriving");
  const incomes = run.personas.map((p) => p.income);
  const context = {
    price,
    anchor: Number($("#anchor").value) || price,
    addressed: run.addressed || { ...suggestAddressed(state.customer.objections, run.pitch), ...state.addressed },
    need: state.need,
    seed: run.seed,
    medianIncome: median(incomes),
  };
  const queue = [...run.personas];
  const concurrency = state.mode === "ai" ? Math.max(1, Math.min(10, Number($("#concurrency").value) || 4)) : 3;
  let livePricingTick = 0;
  const worker = async (workerIndex) => {
    await pause(workerIndex * 350 / speed());
    while (queue.length) {
      const persona = queue.shift();
      arena.setSpeed(speed());
      await pause(arena.approach(persona.id));
      arena.think(persona.id);
      feedItem(persona, null, "thinking");
      let answer;
      try {
        if (state.mode === "ai") {
          const response = await api("/api/llm", {
            provider: $("#provider").value,
            base_url: $("#baseUrl").value.trim() || undefined,
            model: $("#model").value.trim() || undefined,
            api_key: $("#apiKey").value.trim() || undefined,
            brief: run.briefs[persona.id],
          });
          answer = normaliseAnswer(response.answer, persona.id);
        } else {
          await pause((700 + Math.random() * 900) / speed());
          answer = heuristicAnswer(persona, context);
        }
      } catch (error) {
        answer = { id: persona.id, buys: false, reason_code: "other", reason: `No answer: ${error.message}`, error: true };
        toast(`${persona.name}: ${error.message}`, true);
      }
      if (!answer.error) run.answers.push(answer);
      arena.decide(persona.id, answer.buys, answer.reason);
      feedItem(persona, answer, "done");
      updateHud(run.answers, run.personas.length, "decided so far");
      if (++livePricingTick % 3 === 0) renderPricing(run.answers, null);
      if (state.selected === persona.id) selectBot(persona.id);
      await pause(250 / speed());
    }
  };
  await Promise.all(Array.from({ length: concurrency }, (_, i) => worker(i)));
  await pause(600 / speed());
}

function normaliseAnswer(raw, id) {
  const answer = { ...raw, id };
  answer.buys = answer.buys === true || answer.buys === "true";
  if (!REASONS.includes(answer.reason_code)) answer.reason_code = "other";
  answer.reason = String(answer.reason || "");
  answer.purchases_first_month = Number(answer.purchases_first_month) || (answer.buys ? 1 : 0);
  for (const key of ["price_too_cheap", "price_cheap", "price_expensive", "price_too_expensive"]) {
    answer[key] = Number(answer[key]) || null;
  }
  answer._engine = `AI · ${$("#model").value || "default"}`;
  return answer;
}

async function replay() {
  const data = await api(`/api/project?name=${encodeURIComponent(state.project)}&variant=${encodeURIComponent(params.get("variant") || "panel")}`);
  if (!data.answers?.length) return toast("This project has no saved panel to replay.", true);
  state.running = true;
  $("#runBtn").disabled = true;
  const run = { letter: "A", variant: data.variant, seed: data.seed, personas: data.personas, answers: [], engine: "replay of saved panel", pitch: data.pitch };
  state.current = run;
  const byId = Object.fromEntries(data.answers.map((a) => [a.id, a]));
  resetLive();
  arena.setStall(data.customer?.business || state.project, priceLabel());
  const segmentIndex = Object.fromEntries((data.customer?.segments || []).map((s, i) => [s.name, i]));
  run.personas.forEach((p, i) => arena.spawn(p, segmentIndex[p.segment] ?? 0, i, run.personas.length));
  renderRoster(run.personas);
  arena.setSpeed(speed());
  const queue = run.personas.filter((p) => byId[p.id]);
  const worker = async (w) => {
    await pause(w * 300 / speed());
    while (queue.length) {
      const persona = queue.shift();
      await pause(arena.approach(persona.id));
      arena.think(persona.id);
      feedItem(persona, null, "thinking");
      await pause((600 + Math.random() * 700) / speed());
      const answer = byId[persona.id];
      run.answers.push(answer);
      arena.decide(persona.id, answer.buys, answer.reason);
      feedItem(persona, answer, "done");
      updateHud(run.answers, queue.length + run.answers.length, "replaying");
    }
  };
  await Promise.all([0, 1, 2].map(worker));
  arena.settle();
  updateHud(run.answers, run.answers.length, "replay done");
  renderResults(run.answers, run.personas, data.results, "Replay of saved panel");
  renderPricing(run.answers, null);
  state.running = false;
  $("#runBtn").disabled = false;
}

// ---------------- results ----------------
function barRows(groups) {
  return `<div class="bars">${groups.map((g) => `
    <div class="barrow"><span class="lab" title="${escapeHtml(g.label)}">${escapeHtml(g.label)}</span>
    <div class="track"><div class="val" style="width:${(g.buys / g.n) * 100}%"></div></div>
    <span>${g.buys}/${g.n}</span></div>`).join("")}</div>`;
}

function renderResults(answers, personas, server, title) {
  const buys = answers.filter((a) => a.buys).length;
  const rate = answers.length ? buys / answers.length : 0;
  const bar = Number($("#minBuyRate").value) || 0.25;
  const passReasons = reasonCounts(answers, false);
  const flips = answers.filter((a) => !a.buys && a.would_change_my_mind).slice(0, 8);
  const quoteFor = (code) => answers.find((a) => !a.buys && a.reason_code === code);
  const canPromote = state.current?.engine?.startsWith("AI") && state.current?.variant?.startsWith("panel-ai");
  const source = server
    ? `${state.current?.engine || "saved panel"} · counted by the skill's panel.py`
    : `${state.current?.engine || "live run"} · live preview, not yet checked`;
  const topCode = passReasons[0]?.[0];
  const passDoubts = {};
  answers.filter((a) => !a.buys && a.objection).forEach((a) => { passDoubts[a.objection] = (passDoubts[a.objection] || 0) + 1; });
  const topDoubt = Object.entries(passDoubts).sort((x, y) => y[1] - x[1])[0];
  const nextTest = !answers.length ? ""
    : topCode === "price"
      ? `Price is the top reason to pass. Open <b>Pricing</b> for the acceptable range, then test a cheaper or better-justified offer as <b>offer B</b> with the same buyers.`
      : topCode === "need" && topDoubt
        ? `Most buyers pass on <b>need</b>: they don't see a reason to change their habit. In <b>offer B</b>, open with the one moment your product beats what they do today, and answer their top doubt <b>"${escapeHtml(topDoubt[0])}"</b> (${topDoubt[1]} buyers). Rerun with the same buyers.`
      : topDoubt
        ? `The most common unanswered doubt is <b>"${escapeHtml(topDoubt[0])}"</b> (${topDoubt[1]} buyers). Answer it in <b>offer B</b> and rerun with the same buyers.`
        : `Write a sharper <b>offer B</b> and rerun with the same buyers to see who you win or lose.`;
  const priceCount = passReasons.find(([code]) => code === "price")?.[1] || 0;
  const localCurve = vanWestendorp(answers);
  const priceRange = state.current?.serverPricing?.range || (localCurve ? [localCurve.points.PMC, localCurve.points.PME] : null);
  const yourPrice = Number($("#price").value) || 0;
  // Price lever: only suggest a price change when the price sits outside the simulated acceptable range.
  const rangeText = priceRange?.[0] && priceRange?.[1] ? `${money(priceRange[0])} to ${money(priceRange[1])}` : "";
  const stopsText = `Price also stops <b>${priceCount}</b> buyer${priceCount === 1 ? "" : "s"}`;
  let priceLever = "";
  if (topCode !== "price" && priceCount && rangeText) {
    if (yourPrice > priceRange[1]) {
      priceLever = ` ${stopsText}, and ${money(yourPrice)} is above the simulated acceptable range (${rangeText}): also try <b>${money(priceRange[1])}</b>.`;
    } else if (yourPrice < priceRange[0]) {
      priceLever = ` ${stopsText}; ${money(yourPrice)} is below the simulated acceptable range (${rangeText}), so a low price may read as low quality.`;
    } else {
      priceLever = ` ${stopsText}, but ${money(yourPrice)} sits inside the simulated acceptable range (${rangeText}), so fix the message before the price.`;
    }
  }
  state.summary = { buys, n: answers.length, rate, bar, topReason: passReasons[0] };
  const meets = rate >= bar;
  $("#tab-results").innerHTML = `
    <div class="headline ${meets ? "good" : "bad"}"><strong>${buys} of ${answers.length} would buy</strong> <span>${pct(rate)} · ${meets ? "meets" : "below"} your ${pct(bar)} target${passReasons[0] ? ` · top reason to pass: <b>${escapeHtml(passReasons[0][0])}</b>` : ""}</span></div>
    <p class="source-line">Source: ${escapeHtml(source)}. Simulated buyers are an upper bound, not customers.</p>
    ${nextTest ? `<div class="next-test"><strong>Next test</strong><span>${nextTest}${priceLever}</span><span class="next-actions"><button class="ghost" id="goReviewBtn">Auto-fix with reviewers</button><button class="ghost" id="goAbBtn">Write offer B</button></span></div>` : ""}
    <div class="cards">
      <div class="card"><small>${escapeHtml(title)}</small><strong>${buys}/${answers.length} buy</strong><span class="sub">${pct(rate)} · target ${pct(bar)} ${rate >= bar ? "met" : "not met"}</span></div>
      <div class="card"><small>Check</small><strong style="font-size:15px">${server ? '<span class="verified">Verified by panel.py</span>' : '<span class="preview">live preview</span>'}</strong><span class="sub">${escapeHtml(state.current?.engine || "")}</span></div>
      <div class="card"><small>Top pass reason</small><strong style="font-size:17px">${escapeHtml(passReasons[0]?.[0] || "–")}</strong><span class="sub">${passReasons[0]?.[1] || 0} buyers</span></div>
    </div>
    ${canPromote ? '<p><button id="promoteBtn" class="ghost">Make this AI run the official panel (used by the verdict)</button></p>' : ""}
    <div class="two">
      <div><h3>By segment</h3>${barRows(groupRates(answers, personas, "segment"))}</div>
      <div><h3>By buying behaviour</h3>${barRows(groupRates(answers, personas, "behaviour"))}</div>
    </div>
    <div class="two">
      <div><h3>Why they pass</h3><ul class="quotes">${passReasons.map(([code, n]) => `<li><b>${escapeHtml(code)} (${n})</b> - "${escapeHtml(quoteFor(code)?.reason || "")}"</li>`).join("") || "<li>Nobody passed.</li>"}</ul></div>
      <div><h3>What would flip a no</h3><ul class="quotes">${flips.map((a) => `<li>${escapeHtml(a.would_change_my_mind)}</li>`).join("") || "<li>–</li>"}</ul></div>
    </div>`;
  $("#promoteBtn")?.addEventListener("click", promoteRun);
  $("#goAbBtn")?.addEventListener("click", () => document.querySelector('.tab[data-tab="ab"]')?.click());
  $("#goReviewBtn")?.addEventListener("click", () => {
    document.querySelector('.tab[data-tab="review"]')?.click();
    if (!state.running) runReviewLoop();
  });
}

async function promoteRun() {
  const run = state.current;
  if (!confirm("Replace this project's official panel with this AI run? The previous panel answers in panel/ will be overwritten.")) return;
  try {
    await api("/api/deal", { name: state.project, variant: "panel", customer: state.customer, pitch: run.pitch, n: run.personas.length, seed: run.seed });
    await api("/api/answers", { name: state.project, variant: "panel", answers: run.answers, price: Number($("#price").value) });
    toast("Official panel updated. Recompute the verdict.");
  } catch (error) {
    toast(error.message, true);
  }
}

// ---------------- pricing chart ----------------
function renderPricing(answers, serverPricing) {
  const curve = vanWestendorp(answers);
  const price = Number($("#price").value) || null;
  if (!curve) {
    $("#tab-pricing").innerHTML = '<p class="empty">Need at least 3 price answers.</p>';
    return;
  }
  const W = 760, H = 260, P = 36;
  const min = curve.grid[0], max = curve.grid[curve.grid.length - 1];
  const x = (v) => P + ((v - min) / (max - min || 1)) * (W - 2 * P);
  const y = (v) => H - P + 6 - v * (H - 2 * P);
  const line = (values, color, dash = "") => `<polyline fill="none" stroke="${color}" stroke-width="2.2" ${dash ? `stroke-dasharray="${dash}"` : ""}
    points="${values.map((v, i) => `${x(curve.grid[i]).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}"/>`;
  const points = serverPricing?.points || curve.points;
  const range = serverPricing?.range || [points.PMC, points.PME];
  const marker = (label, value, color) => value == null ? "" : `<line x1="${x(value)}" x2="${x(value)}" y1="${P - 6}" y2="${H - P + 6}" stroke="${color}" stroke-dasharray="3 4"/>
    <text x="${x(value) + 4}" y="${P + 4}" fill="${color}" font-size="11">${label} ${money(value)}</text>`;
  $("#tab-pricing").innerHTML = `
    <div class="cards">
      <div class="card"><small>Acceptable range</small><strong>${money(range[0])} – ${money(range[1])}</strong><span class="sub">${serverPricing && !serverPricing.error ? '<span class="verified">Verified by van_westendorp.py</span>' : '<span class="preview">live preview</span>'}</span></div>
      <div class="card"><small>Optimal (OPP)</small><strong>${money(points.OPP)}</strong></div>
      <div class="card"><small>Indifference (IPP)</small><strong>${money(points.IPP)}</strong></div>
      <div class="card"><small>Your price</small><strong>${money(price)}</strong><span class="sub">${price && range[1] && price > range[1] ? "above the range" : price && range[0] && price < range[0] ? "below the range" : "inside the range"}</span></div>
    </div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px;background:var(--panel);border-radius:10px;border:1px solid var(--line)">
      <rect x="${x(range[0] ?? min)}" y="${P - 6}" width="${Math.max(0, x(range[1] ?? min) - x(range[0] ?? min))}" height="${H - 2 * P + 12}" fill="${tone("--buy-soft")}" fill-opacity="0.7"/>
      ${line(curve.curves.tooCheap, tone("--pass"), "6 4")}${line(curve.curves.cheap, tone("--think"))}
      ${line(curve.curves.expensive, tone("--seg-1"))}${line(curve.curves.tooExpensive, tone("--seg-3"), "6 4")}
      ${marker("OPP", points.OPP, tone("--text"))}${marker("IPP", points.IPP, tone("--muted"))}
      ${price ? `<line x1="${x(Math.min(max, Math.max(min, price)))}" x2="${x(Math.min(max, Math.max(min, price)))}" y1="${P - 6}" y2="${H - P + 6}" stroke="${tone("--accent")}" stroke-width="2"/><text x="${x(Math.min(max, Math.max(min, price))) + 4}" y="${H - P - 4}" fill="${tone("--accent-text")}" font-size="11">your price</text>` : ""}
      <text x="${P}" y="${H - 8}" fill="${tone("--muted")}" font-size="11">${money(min)}</text><text x="${W - P - 40}" y="${H - 8}" fill="${tone("--muted")}" font-size="11">${money(max)}</text>
    </svg>
    <p class="hint"><span style="color:var(--pass)">too cheap</span> · <span style="color:var(--think)">cheap</span> · <span style="color:var(--seg-1)">expensive</span> · <span style="color:var(--seg-3)">too expensive</span>. Green band = acceptable range. Simulated answers; real surveys beat this.</p>`;
}

// ---------------- numbers ----------------
function renderNumbers() {
  const n = state.numbers || {
    business: state.customer?.business || "", unit: "unit", price: Number($("#price").value) || 0,
    variable: [{ label: "Variable cost per unit", cost: 0 }], fixed_monthly: [{ label: "Fixed monthly", cost: 0 }],
    startup: [{ label: "Startup", cost: 0 }], days_per_month: 30, capacity_per_day: 100, plan_per_day: 1,
    ramp_per_day: Array(12).fill(1),
  };
  state.numbers = n;
  const list = (key) => (n[key] || []).map((item, i) => `<tr><td><input data-k="${key}" data-i="${i}" data-f="label" value="${escapeHtml(item.label)}"></td>
    <td style="width:130px"><input data-k="${key}" data-i="${i}" data-f="cost" type="number" step="any" value="${item.cost}"></td></tr>`).join("");
  $("#tab-numbers").innerHTML = `
    <div class="form-grid">
      <label>Unit<input id="nUnit" value="${escapeHtml(n.unit)}"></label>
      <label>Price<input id="nPrice" type="number" step="any" value="${n.price}"></label>
      <label>Capacity / day<input id="nCapacity" type="number" step="any" value="${n.capacity_per_day}"></label>
      <label>Plan / day<input id="nPlan" type="number" step="any" value="${n.plan_per_day ?? ""}"></label>
    </div>
    <div class="two">
      <div><h3>Variable cost per unit</h3><table>${list("variable")}</table></div>
      <div><h3>Fixed per month</h3><table>${list("fixed_monthly")}</table><h3>Startup</h3><table>${list("startup")}</table></div>
    </div>
    <h3>Units per day, months 1-12</h3>
    <div class="ramp">${(n.ramp_per_day || Array(12).fill(0)).map((v, i) => `<input data-ramp="${i}" type="number" step="any" value="${v}">`).join("")}</div>
    <p><button id="cfoBtn" class="primary">Run unit_economics.py</button> <span class="hint">Mark estimates in the labels; real quotes beat guesses.</span></p>
    <div id="cfoOut"></div>`;
  $("#cfoBtn").addEventListener("click", runCfo);
}

async function runCfo() {
  const n = state.numbers;
  n.unit = $("#nUnit").value;
  n.price = Number($("#nPrice").value);
  n.capacity_per_day = Number($("#nCapacity").value);
  n.plan_per_day = Number($("#nPlan").value) || undefined;
  $$("#tab-numbers [data-k]").forEach((input) => {
    const item = n[input.dataset.k][Number(input.dataset.i)];
    item[input.dataset.f] = input.dataset.f === "cost" ? Number(input.value) : input.value;
  });
  n.ramp_per_day = $$("#tab-numbers [data-ramp]").map((input) => Number(input.value) || 0);
  try {
    const out = await api("/api/numbers", {
      name: state.project, numbers: n,
      what_ifs: { "price −20%": ["--price", n.price * 0.8], "price +20%": ["--price", n.price * 1.2], "half the volume": ["--volume", 0.5] },
    });
    const c = out.cfo;
    $("#cfoOut").innerHTML = `
      <div class="cards">
        <div class="card"><small>Contribution / unit</small><strong>${money(c.contribution)}</strong><span class="sub">${pct(c.contribution_pct)} margin</span></div>
        <div class="card"><small>Break-even</small><strong>${fmt(c.breakeven_per_day)}/day</strong><span class="sub">capacity ${fmt(c.capacity_per_day)}</span></div>
        <div class="card"><small>Year-1 profit</small><strong>${money(c.year1_profit)}</strong><span class="sub">after startup ${money(c.year1_after_startup)}</span></div>
        <div class="card"><small>Payback</small><strong>${c.payback_month ? `month ${c.payback_month}` : "not in year 1"}</strong><span class="sub">cash needed ${money(c.cash_needed)}</span></div>
      </div>
      ${c.flags.length ? `<h3>Red flags</h3><ul class="quotes">${c.flags.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>` : '<p class="verified">No red flags from unit_economics.py (only as good as the inputs).</p>'}
      <h3>What-ifs</h3><table><tr><th>Scenario</th><th>Year-1 profit</th><th>Payback</th></tr>
      ${Object.entries(out.what_ifs).map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${money(v.year1_profit)}</td><td>${v.payback_month ? `month ${v.payback_month}` : "–"}</td></tr>`).join("")}</table>`;
  } catch (error) {
    toast(error.message, true);
  }
}

// ---------------- competitors ----------------
function markdown(text) {
  const lines = escapeHtml(text || "").split("\n");
  let html = "";
  let inTable = false;
  let inList = false;
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  for (const line of lines) {
    if (/^\s*\|/.test(line)) {
      if (/^\s*\|[\s|:-]+\|\s*$/.test(line)) continue;
      const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      html += (inTable ? "" : "<table>") + `<tr>${cells.map((c) => `<${inTable ? "td" : "th"}>${c}</${inTable ? "td" : "th"}>`).join("")}</tr>`;
      inTable = true;
      continue;
    }
    if (inTable) { html += "</table>"; inTable = false; }
    if (/^\s*[-*] /.test(line)) {
      html += (inList ? "" : "<ul>") + `<li>${inline(line.replace(/^\s*[-*] /, ""))}</li>`;
      inList = true;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    const heading = line.match(/^(#{1,3}) (.*)/);
    if (heading) html += `<h${heading[1].length + 1}>${inline(heading[2])}</h${heading[1].length + 1}>`;
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (inTable ? "</table>" : "") + (inList ? "</ul>" : "");
}

function renderCompetitors() {
  const unanswered = (state.customer?.objections || []).filter((o) => !(state.addressed[o] ?? suggestAddressed([o], $("#pitch").value)[o]));
  $("#tab-competitors").innerHTML = `
    <div class="two">
      <div>
        <h3>Competitor notes <span class="hint">(competitors.md - public sources only, link every fact)</span></h3>
        <textarea id="competitorsText" rows="14">${escapeHtml(state.competitors)}</textarea>
        <button id="saveCompetitors" class="ghost">Save competitors.md</button>
      </div>
      <div>
        <h3>Beat-them board</h3>
        <p class="hint">Objections your pitch doesn't answer yet. Each is a gap a competitor can use, or a feature/offer you can add.</p>
        <ul class="quotes">${unanswered.map((o) => `<li>${escapeHtml(o)}</li>`).join("") || "<li>Every listed objection is answered by the pitch.</li>"}</ul>
        <div class="md">${markdown(state.competitors)}</div>
      </div>
    </div>`;
  $("#saveCompetitors").addEventListener("click", async () => {
    state.competitors = $("#competitorsText").value;
    try {
      await api("/api/save", { name: state.project, file: "competitors.md", content: state.competitors });
      renderCompetitors();
      toast("competitors.md saved");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

// ---------------- A/B ----------------
function renderAb() {
  const { A, B } = state.runs;
  if (!A || !B) return;
  const rate = (run) => run.answers.filter((a) => a.buys).length / (run.answers.length || 1);
  const byIdA = Object.fromEntries(A.answers.map((a) => [a.id, a]));
  const flippedUp = B.answers.filter((b) => b.buys && !byIdA[b.id]?.buys);
  const flippedDown = B.answers.filter((b) => !b.buys && byIdA[b.id]?.buys);
  const name = (id) => B.personas.find((p) => p.id === id)?.name || id;
  $("#abResult").innerHTML = `
    <div class="cards">
      <div class="card"><small>Offer A</small><strong>${pct(rate(A))}</strong><span class="sub">${A.answers.filter((a) => a.buys).length}/${A.answers.length}</span></div>
      <div class="card"><small>Offer B</small><strong>${pct(rate(B))}</strong><span class="sub">${B.answers.filter((a) => a.buys).length}/${B.answers.length} · same seed ${B.seed}</span></div>
    </div>
    <h3>Won over by B (${flippedUp.length})</h3><ul class="quotes">${flippedUp.map((a) => `<li><b>${escapeHtml(name(a.id))}</b>: ${escapeHtml(a.reason)}</li>`).join("") || "<li>–</li>"}</ul>
    <h3>Lost by B (${flippedDown.length})</h3><ul class="quotes">${flippedDown.map((a) => `<li><b>${escapeHtml(name(a.id))}</b>: ${escapeHtml(a.reason)}</li>`).join("") || "<li>–</li>"}</ul>
    <p class="hint">Same simulator answered both rounds, so the lift is an upper bound. Confirm with a real test.</p>`;
}

// ---------------- review loop ----------------
// Reviewer bots (the board lenses) read the pitch and the last run and post feedback; an editor
// applies the fixes; the same buyers (same seed) judge the new version; the loop keeps the best.
// Reviewers suggest, buyers judge: no bot grades its own work.
const VERDICT_TAG = { fix: "FIX", ok: "OK", you: "YOUR CALL" };

function reviewSourceLabel() {
  return state.mode === "ai" ? "AI reviewers + AI buyers" : "rule reviewers + Quick sim buyers";
}

function reviewerFeedItem(reviewer, note, status) {
  let item = $(`#feedList li[data-id="${reviewer.id}"]`);
  if (!item) {
    item = document.createElement("li");
    item.dataset.id = reviewer.id;
    item.className = "reviewer";
    item.addEventListener("click", () => selectReviewer(reviewer.id));
    item.addEventListener("mouseenter", () => arena.highlight(reviewer.id));
    item.addEventListener("mouseleave", () => arena.highlight(state.selected));
    $("#feedList").prepend(item);
  }
  const tag = status === "thinking"
    ? '<span class="tag think">reviewing</span>'
    : `<span class="tag review ${note.verdict}">${VERDICT_TAG[note.verdict] || "OK"}</span>`;
  item.innerHTML = `<div class="who"><span><span class="dot review-dot"></span><b>${escapeHtml(reviewer.name)}</b> · reviewer</span>${tag}</div>
    <div class="why">${status === "thinking" ? escapeHtml(reviewer.focus) : escapeHtml(note.note)}</div>`;
  $("#feedCount").textContent = $$("#feedList li").length;
}

function selectReviewer(id) {
  const reviewer = REVIEWERS.find((r) => r.id === id);
  if (!reviewer) return;
  const note = state.loop?.notes?.[id];
  state.selected = id;
  arena.highlight(id);
  const card = $("#botCard");
  card.hidden = false;
  card.innerHTML = `<h4>${escapeHtml(reviewer.name)} · reviewer</h4>
    <div class="sub">${escapeHtml(reviewer.focus)}</div>
    <dl>
      <dt>Lens</dt><dd>${escapeHtml(reviewer.lens)}</dd>
      <dt>Verdict</dt><dd>${note ? `<span class="tag review ${note.verdict}">${VERDICT_TAG[note.verdict]}</span>` : "not reviewed yet"}</dd>
      <dt>Feedback</dt><dd>${escapeHtml(note?.note || "–")}</dd>
      ${note?.change ? `<dt>Change</dt><dd>${escapeHtml(note.change)}</dd>` : ""}
      <dt>Engine</dt><dd>${escapeHtml(state.mode === "ai" ? "AI, one isolated call" : "rule check")}</dd>
    </dl>`;
}

function highlightPitch(text) {
  return escapeHtml(text)
    .replace(/\[[^\]]+\]/g, (m) => `<mark class="slot">${m}</mark>`)
    .replace(/\(confirm\)/g, '<mark class="slot">(confirm)</mark>');
}

function renderReview() {
  const loop = state.loop;
  const out = $("#reviewOut");
  if (!loop) return;
  const bar = Number($("#minBuyRate").value) || 0.25;
  const best = loop.best;
  const rows = loop.rounds.map((r) => `
    <tr class="${r.kept ? "kept" : "dropped"}${best && r.version === best.version ? " best" : ""}">
      <td>v${r.version}</td>
      <td><b>${r.buys}/${r.n}</b> · ${pct(r.rate)}</td>
      <td>${r.version === 0 ? "baseline" : `${r.delta > 0 ? "+" : ""}${Math.round(r.delta * 100)} pts`}</td>
      <td>${r.version === 0 ? "your pitch" : r.kept ? "kept" : "reverted"}</td>
      <td class="changes">${r.changes.length ? r.changes.map((c) => escapeHtml(c)).join("<br>") : "–"}</td>
    </tr>`).join("");
  const board = REVIEWERS.map((reviewer) => {
    const note = loop.notes[reviewer.id];
    const live = loop.activeReviewer === reviewer.id;
    return `<li class="${live ? "live" : ""}">
      <div class="rv-head"><b>${escapeHtml(reviewer.name)}</b>${live ? '<span class="tag think">reviewing</span>' : note ? `<span class="tag review ${note.verdict}">${VERDICT_TAG[note.verdict]}</span>` : '<span class="tag idle">waiting</span>'}</div>
      <div class="rv-note">${escapeHtml(note?.note || reviewer.focus)}</div>
    </li>`;
  }).join("");
  const yourCalls = Object.values(loop.yourCalls);
  const slots = best ? placeholderCount(best.pitch) : 0;
  const done = loop.status === "done";
  out.innerHTML = `
    <div class="loop-status ${done ? (best?.rate >= bar ? "good" : "bad") : ""}">
      <strong>${escapeHtml(loop.headline)}</strong>
      <span>${escapeHtml(loop.detail || "")}</span>
    </div>
    <p class="source-line">Source: ${escapeHtml(reviewSourceLabel())}. Reviewers suggest, the same ${loop.n || ""} buyers (seed ${loop.seed ?? "–"}) judge each version, and only a version that wins more buyers is kept.</p>
    <div class="loop-grid">
      <div>
        <h3>Reviewers</h3>
        <ul class="reviewer-board">${board}</ul>
      </div>
      <div>
        <h3>Rounds</h3>
        <table class="loop-table"><thead><tr><th>Version</th><th>Buy</th><th>vs best</th><th>Result</th><th>Changes</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Baseline running…</td></tr>'}</tbody></table>
      </div>
    </div>
    ${best ? `<div class="best-pitch">
      <div class="best-head"><h3>Best version: v${best.version} · ${best.buys}/${best.n} buy (${pct(best.rate)})</h3>
      ${done ? '<span class="next-actions"><button class="primary" id="useBestBtn">Use as my pitch</button><button class="ghost" id="bestToBBtn">Send to offer B</button></span>' : ""}</div>
      <pre>${highlightPitch(best.pitch)}</pre>
      ${slots ? `<p class="hint">${slots} highlighted slot${slots === 1 ? "" : "s"} need${slots === 1 ? "s" : ""} your true answer before you use this pitch. The loop names the doubt; it never invents the answer.</p>` : ""}
    </div>` : ""}
    ${yourCalls.length ? `<div class="your-calls"><h3>Needs your facts or decision</h3><ul>${yourCalls.map((c) => `<li><b>${escapeHtml(c.reviewer)}</b>: ${escapeHtml(c.note)}</li>`).join("")}</ul></div>` : ""}
    <p class="hint">${state.mode === "ai"
      ? "The same simulator judged every version, so the lift is an upper bound. Check lines marked (confirm), then test with real buyers."
      : "Quick sim counts a doubt as answered when the pitch uses its words, so the lift shows which doubts matter, not that your wording works. Fill each slot with a true answer, then re-check with AI buyers or a real test."}</p>`;
  $("#useBestBtn")?.addEventListener("click", () => {
    $("#pitch").value = best.pitch;
    updatePitchSummary();
    persistLocal();
    renderObjections();
    toast(`Pitch replaced with v${best.version}. Your previous pitch is v0 in the rounds table.`);
  });
  $("#bestToBBtn")?.addEventListener("click", () => {
    $("#pitchB").value = best.pitch;
    persistLocal();
    document.querySelector('.tab[data-tab="ab"]')?.click();
  });
}

async function aiCall(role, brief) {
  const response = await api("/api/llm", {
    role,
    provider: $("#provider").value,
    base_url: $("#baseUrl").value.trim() || undefined,
    model: $("#model").value.trim() || undefined,
    api_key: $("#apiKey").value.trim() || undefined,
    brief,
  });
  return response.answer || {};
}

async function aiReview(reviewer, context) {
  try {
    const answer = await aiCall("reviewer", reviewerBrief(reviewer, context));
    const verdict = ["fix", "ok", "you"].includes(answer.verdict) ? answer.verdict : "ok";
    const change = String(answer.change || "").trim();
    return { verdict: verdict === "fix" && !change ? "ok" : verdict, note: String(answer.note || "").trim() || "No comment.", change };
  } catch (error) {
    return { verdict: "ok", note: `No answer: ${friendlyError(error.message)}`, error: true };
  }
}

async function aiEdit(context, notes) {
  const answer = await aiCall("editor", editorBrief(context, notes));
  const pitch = String(answer.pitch || "").trim();
  if (!pitch) throw new Error("the editor returned no pitch");
  const changes = Array.isArray(answer.changes) ? answer.changes.map(String).filter(Boolean) : [];
  return { pitch, changes: changes.length ? changes : ["Rewrote the pitch"], answered: [] };
}

function reviewReport(loop) {
  const lines = [
    `# Review loop - ${state.customer.business || state.project}`,
    "",
    `Source: ${reviewSourceLabel()}. Same ${loop.n} buyers (seed ${loop.seed}) judged every version. Simulated buyers are an upper bound, not customers.`,
    `Stopped: ${loop.detail}`,
    "",
    "| Version | Buy | vs best | Result | Changes |",
    "|---|---|---|---|---|",
    ...loop.rounds.map((r) => `| v${r.version} | ${r.buys}/${r.n} (${pct(r.rate)}) | ${r.version === 0 ? "baseline" : `${r.delta > 0 ? "+" : ""}${Math.round(r.delta * 100)} pts`} | ${r.version === 0 ? "your pitch" : r.kept ? "kept" : "reverted"} | ${r.changes.join("; ") || "-"} |`),
    "",
    `## Best version (v${loop.best.version})`,
    "",
    "```",
    loop.best.pitch,
    "```",
    "",
    "## Last feedback",
    "",
    ...REVIEWERS.map((r) => `- **${r.name}** (${VERDICT_TAG[loop.notes[r.id]?.verdict] || "-"}): ${loop.notes[r.id]?.note || "-"}`),
  ];
  return lines.join("\n") + "\n";
}

async function runReviewLoop() {
  if (state.running) return;
  if (state.mode === "replay") return toast("The review loop needs Quick sim or AI buyers. Pick one at the top.", true);
  const problems = readSetup();
  if (problems.length) return toast(`Add ${problems.join(", ")} first.`, true);
  const startPitch = $("#pitch").value.trim();
  const price = Number($("#price").value) || 0;
  if (!price) return toast("Set a price first.", true);
  const maxRounds = Math.max(1, Math.min(6, Number($("#loopRounds").value) || 3));
  const bar = Number($("#minBuyRate").value) || 0.25;
  const objections = state.customer.objections;
  const overrides = { ...state.addressed };
  const addressedFor = (pitch, answered) => ({
    ...answeredMap(objections, pitch, overrides),
    ...Object.fromEntries(answered.map((o) => [o, true])),
  });
  const loop = {
    status: "running", rounds: [], notes: {}, yourCalls: {}, best: null, stop: false,
    headline: "Baseline: your pitch", detail: "The buyers read your current pitch first.", seed: null, n: 0,
  };
  state.loop = loop;
  state.running = true;
  document.documentElement.dataset.run = "running";
  state.skip = params.get("skip") === "1";
  setRunButtons(true);
  $("#skipBtn").hidden = state.skip;
  $("#loopStopBtn").hidden = false;
  document.querySelector('.tab[data-tab="review"]')?.click();
  const variant = `${variantName("A")}-review`;
  const stallTitle = () => state.customer.business || state.project;
  try {
    arena.spawnReviewers(REVIEWERS);
    arena.setSpeed(speed());
    renderReview();
    arena.setStall(stallTitle(), `${priceLabel()} · v0`);
    let run = await executeRun({
      pitch: startPitch, seed: Number($("#seed").value) || undefined, variant, price,
      title: "Review loop · v0 (your pitch)", addressed: addressedFor(startPitch, []),
    });
    arena.setStall(stallTitle(), `${priceLabel()} · v0`);
    loop.seed = run.seed;
    loop.n = run.personas.length;
    loop.best = { version: 0, pitch: startPitch, rate: run.rate, buys: run.buys, n: run.answers.length, run, answered: [] };
    loop.rounds.push({ version: 0, rate: run.rate, buys: run.buys, n: run.answers.length, delta: 0, kept: true, changes: [] });
    const tried = new Set();
    let stale = 0;
    let stopReason = "";
    for (let round = 1; round <= maxRounds; round++) {
      const best = loop.best;
      if (best.rate >= bar) {
        stopReason = `Target met: ${best.buys}/${best.n} buy (${pct(best.rate)}), your bar is ${pct(bar)}.`;
        break;
      }
      if (loop.stop) {
        stopReason = "Stopped by you.";
        break;
      }
      loop.headline = `Round ${round} of ${maxRounds}: reviewers read v${best.version}`;
      loop.detail = `Best so far ${best.buys}/${best.n} (${pct(best.rate)}). Target ${pct(bar)}.`;
      updateHud(best.run.answers, best.run.personas.length, `round ${round}: reviewing`);
      const localCurve = vanWestendorp(best.run.answers);
      const context = {
        business: state.customer.business,
        pitch: best.pitch,
        answers: best.run.answers,
        personas: best.run.personas,
        objections,
        overrides: { ...overrides, ...Object.fromEntries(best.answered.map((o) => [o, true])) },
        tried,
        claimed: new Set(),
        price,
        priceLabel: priceLabel(),
        priceRange: best.run.serverPricing?.range || (localCurve ? [localCurve.points.PMC, localCurve.points.PME] : null),
      };
      const notes = [];
      for (const reviewer of REVIEWERS) {
        if (loop.stop) break;
        loop.activeReviewer = reviewer.id;
        renderReview();
        reviewerFeedItem(reviewer, null, "thinking");
        await pause(arena.reviewerInspect(reviewer.id));
        arena.reviewerThink(reviewer.id);
        setTicker(`<span class="dot review-dot"></span><span><b>${escapeHtml(reviewer.name)}</b> is reading v${best.version}: ${escapeHtml(reviewer.focus.toLowerCase())}…</span>`);
        let note;
        if (state.mode === "ai") {
          note = await aiReview(reviewer, context);
        } else {
          await pause(900 / speed());
          note = ruleReview(reviewer, context);
        }
        if (note.fix?.objection) context.claimed.add(note.fix.objection);
        notes.push({ reviewer: reviewer.name, id: reviewer.id, ...note });
        loop.notes[reviewer.id] = note;
        if (note.verdict === "you") loop.yourCalls[reviewer.id] = { reviewer: reviewer.name, note: note.note };
        else delete loop.yourCalls[reviewer.id];
        loop.activeReviewer = null;
        reviewerFeedItem(reviewer, note, "done");
        setTicker(`<span class="dot review-dot"></span><span><b>${escapeHtml(reviewer.name)}</b> says <b>${VERDICT_TAG[note.verdict]}</b>: ${escapeHtml(note.note)}</span>`);
        if (state.selected === reviewer.id) selectReviewer(reviewer.id);
        renderReview();
        await pause(arena.reviewerVerdict(reviewer.id, note.verdict) * 0.6);
      }
      if (loop.stop) {
        stopReason = "Stopped by you.";
        break;
      }
      const fixes = notes.filter((n) => n.verdict === "fix");
      if (!fixes.length) {
        stopReason = "No reviewer has a fix the loop can make on its own. What is left needs your facts or decisions.";
        break;
      }
      loop.headline = `Round ${round} of ${maxRounds}: editor applies ${fixes.length} fix${fixes.length === 1 ? "" : "es"}`;
      renderReview();
      setTicker(`<span class="dot review-dot"></span><span><b>Editor</b> is writing v${round} from ${fixes.length} reviewer fix${fixes.length === 1 ? "" : "es"}…</span>`);
      let next;
      if (state.mode === "ai") {
        next = await aiEdit(context, notes);
      } else {
        await pause(700 / speed());
        next = applyFixes(best.pitch, fixes.map((n) => n.fix), { priceLabel: priceLabel() });
      }
      for (const n of fixes) tried.add(n.fix?.objection || n.change);
      if (next.pitch.trim() === best.pitch.trim()) {
        stopReason = "The fixes did not change the pitch.";
        break;
      }
      loop.headline = `Round ${round} of ${maxRounds}: the same buyers read v${round}`;
      renderReview();
      arena.setStall(stallTitle(), `${priceLabel()} · v${round}`);
      const answered = [...best.answered, ...next.answered];
      run = await executeRun({
        pitch: next.pitch, seed: loop.seed, variant, price,
        title: `Review loop · v${round}`, addressed: addressedFor(next.pitch, answered),
      });
      arena.setStall(stallTitle(), `${priceLabel()} · v${round}`);
      const candidate = { version: round, pitch: next.pitch, rate: run.rate, buys: run.buys, n: run.answers.length, run, answered };
      const kept = keepBest(best, candidate);
      loop.rounds.push({ version: round, rate: run.rate, buys: run.buys, n: run.answers.length, delta: run.rate - best.rate, kept, changes: next.changes });
      if (kept) {
        loop.best = candidate;
        stale = 0;
      } else {
        stale += 1;
      }
      renderReview();
      if (stale >= 2) {
        stopReason = "Two rounds in a row without a gain.";
        break;
      }
    }
    const best = loop.best;
    if (!stopReason) {
      stopReason = best.rate >= bar
        ? `Target met: ${best.buys}/${best.n} buy (${pct(best.rate)}).`
        : `${maxRounds} round${maxRounds === 1 ? "" : "s"} done. Best ${best.buys}/${best.n} (${pct(best.rate)}), below your ${pct(bar)} target.`;
    }
    // Leave the best version (not the last try) on disk and on screen.
    if (state.current !== best.run) {
      await api("/api/deal", { name: state.project, variant, customer: state.customer, pitch: best.pitch, n: loop.n, seed: loop.seed });
      const saved = await api("/api/answers", { name: state.project, variant, answers: best.run.answers, price });
      best.run.serverResults = saved.results;
      state.current = best.run;
      renderResults(best.run.answers, best.run.personas, saved.results, `Review loop · best v${best.version}`);
      updateHud(best.run.answers, best.run.personas.length, `best: v${best.version}`);
    }
    const start = loop.rounds[0];
    loop.status = "done";
    loop.headline = best.version === 0
      ? `No version beat your pitch (${start.buys}/${start.n})`
      : `v${best.version} wins ${best.buys}/${best.n} (${pct(best.rate)}), up from ${start.buys}/${start.n} (${pct(start.rate)})`;
    loop.detail = stopReason;
    renderReview();
    setTicker(`<span><b>Review loop done.</b> ${escapeHtml(loop.headline)}. ${escapeHtml(stopReason)}</span><button class="ghost" id="tickerReview">See review</button>`, best.rate >= bar ? "good" : "bad");
    $("#tickerReview")?.addEventListener("click", () => document.querySelector('.tab[data-tab="review"]')?.click());
    api("/api/save", { name: state.project, file: "review-loop.md", content: reviewReport(loop) }).catch(() => {});
    document.documentElement.dataset.run = "done";
  } catch (error) {
    document.documentElement.dataset.run = "stopped";
    loop.status = "done";
    loop.headline = "The loop stopped";
    loop.detail = friendlyError(error.message);
    renderReview();
    toast(friendlyError(error.message), true, 8000);
  } finally {
    state.running = false;
    state.skip = false;
    loop.activeReviewer = null;
    $("#skipBtn").hidden = true;
    $("#loopStopBtn").hidden = true;
    setRunButtons(false);
    refreshProjectList().catch(() => {});
  }
}

// ---------------- verdict ----------------
// One recommended next step, derived from compile.py's first failing check.
function nextTestFor(verdict, checkLines) {
  if (/profitable/i.test(verdict)) {
    return "Run the real launch test: a landing page with a waitlist or pre-order, then compare its conversion with the simulated buy rate.";
  }
  if (/panel/i.test(checkLines.join(" ")) && /incomplete/i.test(verdict)) {
    return "Run AI buyers and make that run the official panel, then recompute.";
  }
  const failing = checkLines.find((line) => !line.includes("\u2713"));
  if (failing) {
    return `Fix the first failing check (${failing.replace(/^[^A-Za-z0-9]+/, "").trim()}), then re-run the same buyers with offer B.`;
  }
  return "Fill in numbers.json and run an official panel, then recompute.";
}
async function computeVerdict() {
  try {
    const out = await api("/api/verdict", { name: state.project, min_buy_rate: Number($("#minBuyRate").value) || 0.25 });
    const lines = out.check.split("\n").filter(Boolean);
    const verdict = (lines[0] || "").replace("Verdict:", "").trim();
    $("#verdictOut").innerHTML = `
      <div class="verdict-big ${/profitable/i.test(verdict) ? "good" : "bad"}">${escapeHtml(verdict || "?")}</div>
      ${lines.slice(1).map((l) => `<div class="check-line ${l.includes("\u2713") ? "ok" : "no"}">${escapeHtml(l.trim().replace(/^[\u2713\u2717\u00d7]\s*/, ""))}</div>`).join("")}
      <div class="next-test"><strong>Next test</strong><span>${escapeHtml(nextTestFor(verdict, lines.slice(1)))}</span></div>
      <p class="hint">Computed by compile.py from numbers.json and the official panel (panel/results.json). Quick-sim runs never count.</p>
      <details><summary>business-plan.md</summary><div class="md">${markdown(out.plan || "")}</div></details>`;
  } catch (error) {
    toast(error.message, true);
  }
}

// ---------------- wiring ----------------
$$(".seg").forEach((button) => button.addEventListener("click", () => {
  $$(".seg").forEach((b) => b.classList.toggle("active", b === button));
  state.mode = button.dataset.mode;
  $("#modeBadge").textContent = { heuristic: "Quick sim · rules, not AI", ai: "AI buyers · isolated calls", replay: "Replay · saved panel" }[state.mode];
  updateHud(state.current?.answers || [], state.current?.personas.length || 0, $("#stateText").textContent);
  $("#runBtn").textContent = state.mode === "replay" ? "Replay panel" : "Run panel";
  // The AI model settings only matter in AI mode; keep first-run setup short otherwise.
  const aiSection = $("#aiSection");
  aiSection.hidden = state.mode !== "ai";
  if (state.mode === "ai") aiSection.open = true;
}));
$$(".tab").forEach((tab) => tab.addEventListener("click", () => {
  $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tab.dataset.tab}`));
  if (tab.dataset.tab === "competitors") renderCompetitors();
}));
$("#runBtn").addEventListener("click", () => runPanel("A"));
$("#skipBtn").addEventListener("click", () => {
  state.skip = true;
  $("#skipBtn").hidden = true;
});
$("#runAbBtn").addEventListener("click", () => runPanel("B"));
$("#loopBtn").addEventListener("click", runReviewLoop);
$("#loopStopBtn").addEventListener("click", () => {
  if (state.loop) state.loop.stop = true;
  $("#loopStopBtn").hidden = true;
  toast("The loop stops after the current step.");
});
$("#verdictBtn").addEventListener("click", computeVerdict);
const showSpeed = () => {
  $("#speedVal").textContent = `${speed()}×`;
};
$("#speed").addEventListener("input", () => {
  arena.setSpeed(speed());
  showSpeed();
});
$("#minBuyRate").addEventListener("input", () => updateHud(state.current?.answers || [], state.current?.personas.length || 0, $("#stateText").textContent));
$("#projectSelect").addEventListener("change", (event) => loadProject(event.target.value));
$("#newProjectBtn").addEventListener("click", async () => {
  const raw = prompt("Project name (lowercase, dashes):", "my-app");
  if (!raw) return;
  const name = raw.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
  state.project = name;
  state.customer = blankCustomer(name);
  state.numbers = null;
  state.competitors = "";
  state.need = {};
  state.addressed = {};
  $("#pitch").value = "";
  updatePitchSummary(true);
  $("#pitchB").value = "";
  $("#idea").value = "";
  $("#price").value = "";
  $("#anchor").value = "";
  $("#seed").value = "";
  const select = $("#projectSelect");
  select.insertAdjacentHTML("afterbegin", `<option value="${escapeHtml(name)}">${escapeHtml(name)} (new)</option>`);
  select.value = name;
  renderSetup();
  renderNumbers();
  renderCompetitors();
  resetLive();
  toast("New project: fill buyers + pitch, then Run panel. Files are created on first run.");
});

loadProjects().then(async () => {
  if (params.get("speed")) $("#speed").value = params.get("speed");
  showSpeed();
  const autoMode = params.get("mode");
  if (autoMode) $(`.seg[data-mode="${autoMode}"]`)?.click();
  if (params.get("autorun") === "review") await runReviewLoop();
  else if (params.get("autorun")) await runPanel("A");
}).catch((error) => toast(`Could not load projects: ${error.message}`, true, 10000));

document.getElementById("hintOpen")?.addEventListener("click", () => {
  localStorage.removeItem("studio:hintSeen");
  document.getElementById("arenaHint").hidden = false;
});
document.getElementById("hintClose")?.addEventListener("click", () => {
  localStorage.setItem("studio:hintSeen", "1");
  document.getElementById("arenaHint").hidden = true;
});

for (const fieldId of ["business", "price"]) {
  document.getElementById(fieldId)?.addEventListener("input", () => updateProductSummary());
}

// Guided setup: the three steps behave as one accordion when the user moves between them,
// and each step ends with a "Next" button that opens the following one.
const SETUP_STEPS = ["productSection", "buyersSection", "pitchSection"];
function openSetupStep(id) {
  for (const stepId of SETUP_STEPS) {
    const section = document.getElementById(stepId);
    if (section) section.open = stepId === id;
  }
  const target = document.getElementById(id);
  target?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  target?.querySelector("input, textarea, select")?.focus({ preventScroll: true });
}
for (const stepId of SETUP_STEPS) {
  const section = document.getElementById(stepId);
  section?.querySelector(":scope > summary")?.addEventListener("click", (event) => {
    if (section.open) return;
    event.preventDefault();
    openSetupStep(stepId);
  });
}
for (const button of document.querySelectorAll(".step-next")) {
  button.addEventListener("click", () => openSetupStep(button.dataset.next));
}
// Phones: the full buyer list opens as a bottom sheet so the square stays in view.
const phoneQuery = window.matchMedia("(max-width: 760px)");
function openFeed() {
  const feed = $(".feed");
  if (!phoneQuery.matches) {
    feed.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  // Bring the square to the top so it stays visible above the sheet.
  $(".arena").scrollIntoView({ block: "start" });
  feed.classList.add("sheet-open");
  document.body.classList.add("sheet-lock");
  $("#sheetClose")?.focus();
}
function closeFeed() {
  $(".feed").classList.remove("sheet-open");
  document.body.classList.remove("sheet-lock");
}
$("#miniRoster")?.addEventListener("click", openFeed);
$("#sheetClose")?.addEventListener("click", closeFeed);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && $(".feed").classList.contains("sheet-open")) closeFeed();
});
phoneQuery.addEventListener("change", closeFeed);
$("#pitch")?.addEventListener("input", () => updatePitchSummary());

// Inside Cairn: small same-origin events let the host page track progress and open setup.
function notifyHost(type, detail = {}) {
  if (window.parent === window) return;
  window.parent.postMessage({ type, ...detail }, location.origin);
}

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin || event.source !== window.parent) return;
  if (event.data?.type !== "founder-studio:edit") return;
  const section = ["productSection", "buyersSection", "pitchSection"].includes(event.data.section) ? event.data.section : "productSection";
  openSetupStep(section);
});

// Inside Cairn the host page already names the studio, so drop the brand block and keep the honesty note short.
if (window.parent !== window) {
  document.documentElement.classList.add("embedded");
  const banner = document.querySelector(".demo-banner");
  if (banner) {
    banner.innerHTML = 'Runs in your browser with the founder skill\'s own scripts. Quick sim is a rule model, not evidence; AI buyers need the <a href="https://github.com/juw12/founder-studio#run" target="_blank" rel="noopener">local app</a>.';
  }
}

// Inside Cairn: hand each finished panel to the host page so it can be saved as a note in the brain.
function shareWithHost(run, summary, heading) {
  if (window.parent === window) return;
  const reasons = {};
  for (const answer of run.answers) {
    if (!answer.buys && answer.reason_code) reasons[answer.reason_code] = (reasons[answer.reason_code] || 0) + 1;
  }
  window.parent.postMessage({
    type: "founder-studio:result",
    project: state.project,
    business: state.customer?.business || state.project,
    heading,
    mode: state.mode,
    buys: summary.buys,
    n: summary.n,
    rate: summary.rate,
    target: summary.bar,
    topReason: summary.topReason ? summary.topReason[0] : null,
    reasons,
    pitch: ($("#pitch")?.value || "").trim(),
    price: typeof priceLabel === "function" ? priceLabel() : "",
  }, location.origin);
}

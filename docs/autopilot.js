// Autopilot: hand over a goal once and your coding agent carries it to done, round after round.
// The work queue is plain brain tasks tagged #queue (#auto = safe to run unattended, #needs-me =
// waiting on you), so it lives in brain/ next to everything else and works in every mode. Running
// goals needs the local app: the server's worker starts your agent CLI (OpenCode or any command)
// with a brief built from your own history, reads its STATUS line and keeps going until it's done.

import { escapeHtml as esc } from "./md.js";
import { $, ask, icon, pageHead, plural, toast, today } from "./ui.js";
import { createNote, readOnly, saveNote, state } from "./state.js";
import { renderNav, renderView } from "./app.js";

const POLL_MS = 2000;
const QUEUE_TAG = "queue";
const STATUS_LABEL = { queued: "Queued", running: "Running", done: "Done", blocked: "Needs you", stopped: "Stopped", error: "Error" };
const CLI_LINES = [
  'cairn autopilot add "<goal>"        # hand over a goal',
  "cairn autopilot on                  # let the worker run goals and #auto queue items",
  'cairn precedent queue "<task> #auto" # queue a follow-up',
  'cairn precedent brief "<goal>"      # the brief your agent gets',
  "cairn precedent learn               # rebuild the library from your past prompts",
].join("\n");

let pollTimer = null;
let polling = false;

const canRun = () => typeof state.store?.autopilot === "function";
const ap = () => state.autopilot || (state.autopilot = { data: null, selected: "", log: "", logSize: 0, logFor: "", brief: "" });

// ============================================================ queue (brain tasks tagged #queue)

export function queueItems(includeDone = false) {
  const order = { doing: 0, todo: 1, done: 2 };
  return state.notes
    .filter((n) => n.type === "task" && n.tags.map((t) => String(t).toLowerCase()).includes(QUEUE_TAG))
    .filter((n) => includeDone || n.status !== "done")
    .map((n) => {
      const tags = n.tags.map((t) => String(t).toLowerCase());
      return { note: n, auto: tags.includes("auto"), needsMe: tags.includes("needs-me") };
    })
    .sort((a, b) => (order[a.note.status] ?? 1) - (order[b.note.status] ?? 1)
      || Number(a.needsMe) - Number(b.needsMe)
      || String(a.note.meta.created || "").localeCompare(String(b.note.meta.created || ""))
      || a.note.path.localeCompare(b.note.path));
}

function shortTitle(text, limit = 90) {
  if (text.length <= limit) return text;
  const cut = text.slice(0, limit).replace(/\s+\S*$/, "").replace(/[,;:-]+$/, "");
  return `${cut}…`;
}

export async function addQueueItem(form) {
  let text = form.elements.text.value.replace(/\s+/g, " ").trim();
  if (!text) return;
  const found = new Set([...text.matchAll(/#([\w-]+)/g)].map((m) => m[1].toLowerCase()));
  const auto = form.elements.auto.checked || found.has("auto");
  const needsMe = form.elements.needsMe.checked || found.has("needs-me") || found.has("needs-juwen");
  text = ` ${text}`.replace(/\s#(auto|needs-me|needs-juwen|queue)\b/gi, "").trim();
  const title = shortTitle(text);
  const tags = [QUEUE_TAG, ...(auto ? ["auto"] : []), ...(needsMe ? ["needs-me"] : [])];
  await createNote({ type: "task", title, tags, body: title !== text ? `${text}\n` : "" });
  form.reset();
  toast(auto && !needsMe ? "Queued. The autopilot can pick it up on its own." : "Queued.");
  renderNav();
  renderView();
  $("#ap-queue-form input[name=text]")?.focus();
}

export async function queueDone(path) {
  await saveNote(path, { status: "done" });
  renderNav();
  renderView();
}

export async function queueToggle(path, flag) {
  const item = queueItems().find((i) => i.note.path === path);
  if (!item) return;
  const tags = item.note.tags.filter((t) => String(t).toLowerCase() !== flag);
  if (tags.length === item.note.tags.length) tags.push(flag);
  await saveNote(path, { tags });
  renderView();
}

// ============================================================ server actions

async function refresh() {
  const data = await state.store.autopilot();
  const key = JSON.stringify(data);
  const changed = key !== ap().dataKey;
  ap().data = data;
  ap().dataKey = key;
  if (!ap().selected && data.jobs.length) {
    // Open on what matters most: a goal that needs you, then one that is running.
    const pick = data.jobs.find((j) => j.status === "blocked") || data.jobs.find((j) => j.status === "running") || data.jobs[0];
    ap().selected = pick.id;
  }
  return changed;
}

async function act(action, body = {}) {
  const result = await state.store.autopilotAction(action, body);
  await refresh();
  renderNav();
  renderView();
  return result;
}

export async function startGoal(form) {
  const goal = form.elements.goal.value.replace(/\s+/g, " ").trim();
  if (!goal) return;
  const job = await act("goal", { goal, workdir: form.elements.workdir.value.trim() });
  ap().selected = job.id;
  ap().log = "";
  ap().logSize = 0;
  ap().logFor = job.id;
  ap().brief = "";
  const enabled = ap().data?.settings?.enabled;
  toast(enabled ? "Goal handed over. The first round starts within a few seconds." : "Goal queued. Turn the autopilot on to start it.");
  renderView();
}

export async function previewBrief(form) {
  const goal = form.elements.goal.value.replace(/\s+/g, " ").trim();
  const data = await state.store.precedentBrief(goal);
  ap().brief = data.brief;
  renderView();
}

export const toggleEnabled = () => act("settings", { enabled: !ap().data?.settings?.enabled });
export async function jobAction(action, id) {
  if (action === "delete") {
    const sure = await ask({ title: "Delete this goal?", text: "Its log goes too. Work already done in its folder stays.", confirm: "Delete", noInput: true, danger: true });
    if (!sure) return;
    if (ap().selected === id) ap().selected = "";
  }
  await act(action, { id });
}

export function selectJob(id) {
  ap().selected = id;
  ap().log = "";
  ap().logSize = 0;
  ap().logFor = "";
  renderView();
}

export async function sendComment(form) {
  const text = form.elements.text.value.trim();
  if (!text) return;
  await act("comment", { id: form.dataset.job, text });
  toast("Sent. The next round reads it first.");
}

export async function saveSettings(form) {
  const f = form.elements;
  await act("settings", {
    engine: f.engine.value,
    command: f.command.value,
    opencode_flags: f.opencode_flags.value,
    workdir_root: f.workdir_root.value,
    round_timeout_min: Number(f.round_timeout_min.value) || 45,
    max_rounds_per_goal: Number(f.max_rounds_per_goal.value) || 40,
    improve_rounds_after_done: Number(f.improve_rounds_after_done.value),
    auto_pull_queue: f.auto_pull_queue.checked,
  });
  toast("Settings saved.");
}

// ============================================================ live updates

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

async function pullLog() {
  const id = ap().selected;
  if (!id) return false;
  if (ap().logFor !== id) {
    ap().log = "";
    ap().logSize = 0;
    ap().logFor = id;
  }
  const chunk = await state.store.autopilotLog(id, ap().logSize);
  if (chunk.size < ap().logSize) {
    ap().log = "";
    ap().logSize = 0;
    return pullLog();
  }
  if (!chunk.text) return false;
  ap().log = (ap().log + chunk.text).slice(-200000);
  ap().logSize = chunk.size;
  return true;
}

function paintLog() {
  const pre = $("#ap-log");
  if (!pre) return;
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  pre.textContent = ap().log || "No output yet.";
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

async function poll() {
  if (state.view !== "autopilot" || !canRun()) {
    stopPolling();
    return;
  }
  if (polling) return;
  polling = true;
  try {
    const first = !ap().data;
    const changed = await refresh();
    const grew = await pullLog();
    if (state.view !== "autopilot") return;
    if (first) {
      // The first answer brings the on/off switch and settings, which live outside the live part.
      renderNav();
      renderView();
      return;
    }
    if (changed) {
      // Only the live parts are redrawn, so whatever is typed in the forms stays put.
      const live = $("#ap-live");
      if (live) live.innerHTML = liveParts();
      renderNav();
    }
    if (grew || changed) paintLog();
  } catch {
    /* the local server is restarting; try again on the next tick */
  } finally {
    polling = false;
  }
}

export function wireAutopilot() {
  if (canRun() && !pollTimer) {
    pollTimer = setInterval(poll, window.CAIRN_TEST_POLL_MS || POLL_MS);
    poll();
  }
  paintLog();
  const forms = {
    "#ap-goal-form": startGoal,
    "#ap-queue-form": addQueueItem,
    "#ap-settings-form": saveSettings,
  };
  for (const [selector, handler] of Object.entries(forms)) {
    $(selector)?.addEventListener("submit", (event) => {
      event.preventDefault();
      handler(event.target).catch((error) => toast(error.message, { error: true }));
    });
  }
  // The comment form is redrawn with the job detail, so it is wired by delegation.
  $("#ap-live")?.addEventListener("submit", (event) => {
    if (!event.target.matches(".ap-comment-form")) return;
    event.preventDefault();
    sendComment(event.target).catch((error) => toast(error.message, { error: true }));
  });
}

export function autopilotCount() {
  const jobs = ap().data?.jobs;
  if (jobs) {
    const active = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    const blocked = jobs.filter((j) => j.status === "blocked").length;
    if (blocked) return `${blocked} waiting`;
    if (active) return String(active);
  }
  return "";
}

// ============================================================ view

function pillFor(status) {
  return `<span class="pill ap-st ap-st-${esc(status)}">${esc(STATUS_LABEL[status] || status)}</span>`;
}

function when(stamp) {
  const text = String(stamp || "");
  return text.startsWith(today()) ? text.slice(11, 16) : text.slice(0, 10);
}

function jobRow(job) {
  const selected = job.id === ap().selected;
  const rounds = job.rounds ? plural(job.rounds, "round") : "not started";
  return `<button class="ap-job${selected ? " selected" : ""}" data-action="ap-select" data-id="${esc(job.id)}" aria-pressed="${selected}">
    ${pillFor(job.status)}
    <span class="ap-goal">${esc(job.goal)}</span>
    <span class="muted small">${esc(rounds)} · ${esc(when(job.updated))}</span>
  </button>`;
}

function jobDetail(job) {
  if (!job) return `<p class="empty">Hand over a goal above and its live log appears here.</p>`;
  const facts = [
    job.blocked_reason ? `<p class="ap-blocked">${icon("help", "sm")} <span><strong>Needs you:</strong> ${esc(job.blocked_reason)}</span></p>` : "",
    job.summary ? `<p><strong>Result:</strong> ${esc(job.summary)}</p>` : "",
    job.next_hint && job.status !== "done" ? `<p class="muted"><strong>Next:</strong> ${esc(job.next_hint)}</p>` : "",
  ].join("");
  const canStop = job.status === "running" || job.status === "queued";
  const canResume = ["blocked", "stopped", "error", "done"].includes(job.status);
  return `<div class="ap-detail">
    <header class="ap-detail-head">
      <div><h3>${esc(job.goal)}</h3>
      <p class="muted small mono">${esc(job.workdir || "")}</p></div>
      <div class="ap-buttons">
        ${canStop ? `<button class="btn sm" data-action="ap-job" data-op="stop" data-id="${esc(job.id)}">${icon("pause", "sm")} Stop</button>` : ""}
        ${canResume ? `<button class="btn sm" data-action="ap-job" data-op="resume" data-id="${esc(job.id)}">${icon("play", "sm")} ${job.status === "done" ? "Run again" : "Resume"}</button>` : ""}
        <button class="btn sm ghost danger" data-action="ap-job" data-op="delete" data-id="${esc(job.id)}" aria-label="Delete goal">${icon("trash", "sm")}</button>
      </div>
    </header>
    ${facts}
    <form class="ap-comment-form vibe-log-form" data-job="${esc(job.id)}">
      <input class="input" name="text" id="ap-comment-${esc(job.id)}" placeholder="${job.status === "blocked" ? "Answer it here and the goal resumes" : "Steer it: a correction or extra requirement"}" aria-label="Comment on this goal" autocomplete="off">
      <button class="btn sm" type="submit">${icon("chat", "sm")} Send</button>
    </form>
  </div>`;
}

function figures(data) {
  const s = data.settings;
  const w = data.worker || {};
  const active = data.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const done = data.jobs.filter((j) => j.status === "done").length;
  const workerText = !s.enabled ? "Off" : w.worker === "here" ? (w.current ? "Working" : "On, idle") : w.worker === "elsewhere" ? "Another window" : "Starting";
  const engine = s.engine === "opencode" ? (data.engine_found ? "OpenCode" : "OpenCode missing") : "Custom command";
  return `<div class="figures ap-figures">
    <div class="figure"><span class="value">${esc(workerText)}</span><span class="label">worker</span></div>
    <div class="figure"><span class="value">${active}</span><span class="label">in progress</span></div>
    <div class="figure"><span class="value">${done}</span><span class="label">done</span></div>
    <div class="figure" title="${esc(data.engine_found || s.engine !== "opencode" ? "" : "opencode is not on PATH: install it or pick another command in Settings")}"><span class="value${data.engine_found || s.engine !== "opencode" ? "" : " warn"}">${esc(engine)}</span><span class="label">agent</span></div>
  </div>`;
}

function liveParts() {
  const data = ap().data;
  if (!data) return `<p class="muted small">Connecting…</p>`;
  const job = data.jobs.find((j) => j.id === ap().selected) || null;
  return `${figures(data)}
    <div class="ap-cols">
      <section class="column-block">
        <header><h2>Goals</h2><span class="muted small">${plural(data.jobs.length, "goal")}</span></header>
        ${data.jobs.length ? `<div class="ap-jobs">${data.jobs.slice(0, 60).map(jobRow).join("")}</div>` : `<p class="empty">No goals yet.</p>`}
      </section>
      <section class="column-block">
        <header><h2>Selected goal</h2></header>
        ${jobDetail(job)}
      </section>
    </div>`;
}

function queueBlock() {
  const items = queueItems();
  const rows = items.slice(0, 50).map(({ note, auto, needsMe }) => `<li class="ap-q${needsMe ? " needs-me" : ""}">
    <a href="#" data-open="${esc(note.path)}" class="ap-q-title">${esc(note.title)}</a>
    <span class="ap-q-flags">
      <button class="chip${auto ? " on" : ""}" data-action="ap-flag" data-flag="auto" data-path="${esc(note.path)}" aria-pressed="${auto}" title="Safe for the autopilot to run unattended">auto</button>
      <button class="chip${needsMe ? " on" : ""}" data-action="ap-flag" data-flag="needs-me" data-path="${esc(note.path)}" aria-pressed="${needsMe}" title="Waiting on you">needs me</button>
      <button class="btn sm ghost icon-only" data-action="ap-q-done" data-path="${esc(note.path)}" aria-label="Mark done" title="Mark done">${icon("check", "sm")}</button>
    </span>
  </li>`).join("");
  const autoCount = items.filter((i) => i.auto && !i.needsMe).length;
  return `<section class="column-block ap-queue">
    <header><h2>Queue</h2><span class="muted small">${items.length} open · ${autoCount} auto</span></header>
    ${readOnly() ? "" : `<form id="ap-queue-form" class="ap-queue-form">
      <input class="input" name="text" id="ap-queue-text" placeholder="A follow-up, e.g. Add CSV export to the report page" aria-label="Queue item" autocomplete="off">
      <div class="ap-queue-opts">
        <label class="ap-check" title="Safe for the autopilot to run unattended"><input type="checkbox" name="auto"> auto</label>
        <label class="ap-check" title="Waiting on you: a login, a purchase, a decision"><input type="checkbox" name="needsMe"> needs me</label>
        <button class="btn sm" type="submit">${icon("plus", "sm")} Queue</button>
      </div>
    </form>`}
    ${items.length ? `<ul class="ap-queue-list">${rows}</ul>` : `<p class="empty">Nothing queued. Agents add follow-ups here instead of asking you; each one is a task tagged <code>#queue</code> in <code>brain/tasks/</code>.</p>`}
  </section>`;
}

function settingsBlock(s) {
  const field = (name, label, value, type = "text", hint = "") => `<label class="field"><span>${label}</span>
    <input class="input" type="${type}" name="${name}" value="${esc(String(value ?? ""))}" ${type === "number" ? 'min="0"' : ""}>${hint ? `<span class="muted small">${hint}</span>` : ""}</label>`;
  return `<details class="ap-settings column-block">
    <summary><h2>Settings</h2><span class="muted small">agent, work folders, limits</span></summary>
    <form id="ap-settings-form">
      <label class="field"><span>Agent</span>
        <select class="select" name="engine">
          <option value="opencode"${s.engine === "opencode" ? " selected" : ""}>OpenCode (opencode run)</option>
          <option value="command"${s.engine === "command" ? " selected" : ""}>Another command</option>
        </select></label>
      ${field("command", "Command", s.command, "text", "Used when Agent is another command. The prompt is added last, or wherever you write {prompt}. Example: claude -p")}
      ${field("opencode_flags", "OpenCode flags", s.opencode_flags)}
      ${field("workdir_root", "Work folders go in", s.workdir_root, "text", "Empty: Documents/Cairn Autopilot. Each goal gets its own folder unless you pick one.")}
      <div class="ap-nums">
        ${field("round_timeout_min", "Minutes per round", s.round_timeout_min, "number")}
        ${field("max_rounds_per_goal", "Max rounds", s.max_rounds_per_goal, "number")}
        ${field("improve_rounds_after_done", "Polish rounds after done", s.improve_rounds_after_done, "number")}
      </div>
      <label class="ap-check"><input type="checkbox" name="auto_pull_queue"${s.auto_pull_queue ? " checked" : ""}> When idle, take the next <code>#auto</code> queue item</label>
      <div><button class="btn sm primary" type="submit">${icon("save", "sm")} Save settings</button></div>
    </form>
    <p class="muted small">State and logs: <code>${esc(ap().data?.state_dir || "~/.cairn/autopilot")}</code></p>
  </details>`;
}

function offlineCard() {
  return `<section class="callout">${icon("terminal")}<div>
    <p><strong>Running goals needs Cairn on this computer.</strong> Start the desktop app or <code>python -m cairn</code> in your project; its worker launches your coding agent (OpenCode, or any command such as <code>claude -p</code>) with a brief built from your past prompts, lessons and this brain, and keeps it going until the goal is done. Cairn itself never calls a model.</p>
    <pre class="pre-box">${esc(CLI_LINES)}</pre>
  </div></section>`;
}

export function autopilotView() {
  if (!canRun()) {
    return `${pageHead("Autopilot", "Hand over a goal once; your coding agent carries it to done while you do something else. The queue below is plain tasks tagged <code>#queue</code>.")}
      ${offlineCard()}
      ${queueBlock()}`;
  }
  const data = ap().data;
  const enabled = Boolean(data?.settings?.enabled);
  const head = pageHead(
    "Autopilot",
    "Hand over a goal once. Cairn briefs your coding agent from your own history, runs it round after round until the work is done and verified, then takes the next <code>#auto</code> item from the queue.",
    data ? `<button class="btn${enabled ? "" : " primary"}" data-action="ap-toggle" aria-pressed="${enabled}">${icon(enabled ? "pause" : "play")} ${enabled ? "Turn off" : "Turn on"}</button>` : "",
  );
  const brief = ap().brief ? `<details class="ap-brief" open><summary>The brief your agent gets</summary><pre class="pre-box">${esc(ap().brief)}</pre></details>` : "";
  return `${head}
    ${readOnly() ? "" : `<form id="ap-goal-form" class="ap-goal-form">
      <textarea class="input" name="goal" id="ap-goal" rows="2" placeholder="What should get done? e.g. Add dark mode to the settings page, tests passing" aria-label="Goal"></textarea>
      <div class="ap-goal-row">
        <input class="input" name="workdir" id="ap-workdir" placeholder="Folder to work in (optional, e.g. the repo)" aria-label="Working folder" autocomplete="off">
        <button class="btn" type="button" data-action="ap-brief">${icon("list", "sm")} Preview brief</button>
        <button class="btn primary" type="submit">${icon("play", "sm")} Hand it over</button>
      </div>
    </form>`}
    ${brief}
    <div id="ap-live">${liveParts()}</div>
    <section class="column-block ap-log-block"><header><h2>Log</h2><span class="muted small">your agent's output, live</span></header>
      <pre id="ap-log" class="pre-box ap-log" tabindex="0" aria-label="Log of the selected goal"></pre></section>
    <div class="ap-cols ap-cols-bottom">
      ${queueBlock()}
      ${data ? settingsBlock(data.settings) : ""}
    </div>`;
}

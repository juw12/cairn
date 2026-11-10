// Projects: every GitHub repo of one person, ranked by how solid it is and how much potential it has.
//
// The ranking re-arranges itself: it refreshes when you open the page and every 15 minutes while
// Cairn is open, remembers yesterday's order to show who moved, and can keep a plain Markdown
// "Project ranking" note in the brain up to date (so Obsidian, the Map and AI assistants see it too).
//
// Scores only use facts GitHub returns plus how often your own notes mention a repo. Each score is
// normalised over the signals that are known, so a repo is never punished for a check we couldn't run.

import * as gh from "./github.js";
import * as auth from "./auth.js";
import { escapeHtml as esc } from "./md.js";
import { $, icon, pageHead, plural, relativeTime, toast } from "./ui.js";
import { REPO_URL, createNote, readOnly, saveNote, state } from "./state.js";
import { renderView } from "./app.js";

const DAY = 86400000;
const STALE_MS = 15 * 60 * 1000;
const OWNER_KEY = "cairn.projects.owner";
const SORT_KEY = "cairn.projects.sort";
const AUTO_NOTE_KEY = "cairn.projects.autoNote";
const HISTORY_KEY = (owner) => `cairn.projects.ranks:${owner.toLowerCase()}`;
// Cairn itself is always in the ranking, whoever's repos you look at.
const CAIRN = REPO_URL.replace(/^https:\/\/github\.com\//, "");
export const RANKING_MARK = "project-ranking";

const PACKAGE_FILES = /^(package\.json|pyproject\.toml|setup\.py|cargo\.toml|go\.mod|pom\.xml|build\.gradle(\.kts)?|gemfile|composer\.json|.*\.csproj|.*\.sln|pubspec\.yaml)$/i;
const TEST_NAMES = /^(tests?|__tests__|specs?|e2e|cypress|playwright\.config\.\w+|jest\.config\.\w+|vitest\.config\.\w+|pytest\.ini|tox\.ini|conftest\.py)$/i;
const CI_NAMES = /^(\.github|\.gitlab-ci\.yml|\.travis\.yml|\.circleci|azure-pipelines\.yml|jenkinsfile)$/i;

// ============================================================ scoring (pure, tested)

const clamp01 = (value) => Math.max(0, Math.min(1, value));
// 0 at zero, 1 at `full`, logarithmic in between (the 10th star matters more than the 90th).
const logScale = (value, full) => clamp01(Math.log10(1 + Math.max(0, value)) / Math.log10(1 + full));
const days = (from, to) => Math.max(0, (to - from) / DAY);

// What the repo root tells us about how real the project is: { readme, tests, ci, packaged } or null.
export function rootFacts(names) {
  if (!Array.isArray(names)) return null;
  return {
    readme: names.some((name) => /^readme(\.|$)/i.test(name)),
    tests: names.some((name) => TEST_NAMES.test(name)),
    ci: names.some((name) => CI_NAMES.test(name)),
    packaged: names.some((name) => PACKAGE_FILES.test(name)),
  };
}

// How many notes mention a repo by name (title, tags, file name or text). Hyphens and underscores count
// as spaces, so "trail-mix" also matches "Trail mix".
export function brainMentions(notes, repoName) {
  const words = String(repoName).toLowerCase().split(/[-_.\s]+/).filter(Boolean);
  if (!words.length) return 0;
  const pattern = new RegExp(`(^|[^a-z0-9])${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[-_\\s]?")}($|[^a-z0-9])`, "i");
  let count = 0;
  for (const note of notes) {
    const text = `${note.title}\n${note.slug || ""}\n${(note.tags || []).join(" ")}\n${note.body || ""}`;
    if (pattern.test(text)) count += 1;
  }
  return count;
}

function weigh(signals) {
  const known = signals.filter((s) => s.value !== null && s.value !== undefined);
  const max = known.reduce((sum, s) => sum + s.weight, 0);
  const got = known.reduce((sum, s) => sum + s.weight * s.value, 0);
  return max ? Math.round((got / max) * 100) : 0;
}

// One repo -> its scores, tier, the reasons it ranks where it does and the next steps that would lift it.
//   repo:    a GitHub REST repo object (as returned by /users/:owner/repos)
//   facts:   rootFacts(...) or null when the root wasn't checked
//   mentions: notes in this brain that mention it, or null when there is no brain to ask
export function scoreProject(repo, { now = Date.now(), facts = null, mentions = null } = {}) {
  const created = Date.parse(repo.created_at || "") || now;
  const pushed = Date.parse(repo.pushed_at || repo.updated_at || "") || created;
  const age = days(created, now);
  const idle = days(pushed, now);
  const span = days(created, pushed);
  const description = String(repo.description || "").trim();
  const topics = repo.topics || [];
  const hasDemo = Boolean(String(repo.homepage || "").trim()) || Boolean(repo.has_pages);
  const known = (value) => (facts ? (value ? 1 : 0) : null);

  const solidSignals = [
    { key: "demo", label: "live demo or site", weight: 14, value: hasDemo ? 1 : 0, fix: "Ship a live demo (GitHub Pages is free)" },
    { key: "tests", label: "has tests", weight: 16, value: known(facts?.tests), fix: "Add tests" },
    { key: "size", label: "real codebase", weight: 14, value: logScale(repo.size || 0, 5000), fix: "Build out the core feature" },
    { key: "longevity", label: "kept up over time", weight: 12, value: clamp01(span / 120), fix: "Keep shipping small updates" },
    { key: "ci", label: "CI checks", weight: 10, value: known(facts?.ci), fix: "Add a CI workflow" },
    { key: "describe", label: "clear description", weight: 8, value: description.length >= 20 ? 1 : description ? 0.5 : 0, fix: "Write a one-line description" },
    { key: "readme", label: "README", weight: 6, value: known(facts?.readme), fix: "Write a README" },
    { key: "packaged", label: "installable package", weight: 6, value: known(facts?.packaged), fix: "Make it installable (package manifest)" },
    { key: "license", label: "license", weight: 6, value: repo.license ? 1 : 0, fix: "Add a license" },
    { key: "topics", label: "tagged topics", weight: 4, value: clamp01(topics.length / 3), fix: "Add 3+ topics" },
  ];
  const potentialSignals = [
    { key: "stars", label: `${repo.stargazers_count || 0} stars`, weight: 26, value: logScale(repo.stargazers_count || 0, 100), fix: null },
    { key: "momentum", label: idle < 1 ? "pushed today" : `pushed ${Math.round(idle)}d ago`, weight: 24, value: Math.exp(-idle / 30), fix: `Push an update (idle ${Math.round(idle)} days)` },
    { key: "focus", label: mentions ? `in ${plural(mentions, "note")} of yours` : "in your notes", weight: 14, value: mentions === null ? null : logScale(mentions, 10), fix: "Write down what's next for it in a note" },
    { key: "usable", label: "usable by others today", weight: 12, value: hasDemo ? 1 : 0, fix: "Give people a link to try it" },
    { key: "rising", label: "young and moving fast", weight: 10, value: age <= 180 && idle <= 14 ? 1 : age <= 365 && idle <= 30 ? 0.5 : 0, fix: null },
    { key: "forks", label: `${repo.forks_count || 0} forks`, weight: 8, value: logScale(repo.forks_count || 0, 20), fix: null },
    { key: "discoverable", label: "easy to discover", weight: 6, value: topics.length >= 3 && description ? 1 : topics.length || description ? 0.4 : 0, fix: "Add topics so people can find it" },
  ];

  const solid = weigh(solidSignals);
  const potential = weigh(potentialSignals);
  const overall = Math.round(0.6 * Math.max(solid, potential) + 0.4 * Math.min(solid, potential));

  let tier = "Seedling";
  if (repo.archived) tier = "Archived";
  else if (!repo.size) tier = "Empty";
  else if (idle > 180) tier = "Dormant";
  else if (overall >= 70) tier = "Flagship";
  else if (potential >= 55 && potential >= solid + 10) tier = "Rising";
  else if (solid >= 60) tier = "Solid";

  const all = [...solidSignals, ...potentialSignals].filter((s) => s.value !== null);
  const strengths = [...new Map(
    all
      .filter((s) => s.value >= 0.5)
      .sort((a, b) => b.weight * b.value - a.weight * a.value)
      .map((s) => [s.label, s]),
  ).values()].slice(0, 3).map((s) => s.label);
  const nextSteps = [...new Map(
    all
      .filter((s) => s.fix && s.value < 0.5)
      .sort((a, b) => b.weight * (1 - b.value) - a.weight * (1 - a.value))
      .map((s) => [s.fix, s]),
  ).values()].slice(0, 2).map((s) => s.fix);

  return { solid, potential, overall, tier, strengths, nextSteps, idleDays: Math.round(idle) };
}

const SORTS = {
  overall: (p) => p.score.overall,
  solid: (p) => p.score.solid,
  potential: (p) => p.score.potential,
};

// Score and order a list of repos. Forks and archived repos are left out (they aren't yours to grow),
// except Cairn itself, which is always kept. Ties go to stars, then the most recent push.
export function rankProjects(repos, { sort = "overall", now = Date.now(), facts = {}, mentions = null, keep = CAIRN } = {}) {
  const by = SORTS[sort] || SORTS.overall;
  const seen = new Set();
  return repos
    .filter((repo) => {
      const name = String(repo.full_name || "").toLowerCase();
      if (seen.has(name)) return false;
      seen.add(name);
      if (name === keep.toLowerCase()) return true;
      return !repo.fork && !repo.archived;
    })
    .map((repo) => ({
      repo,
      self: String(repo.full_name || "").toLowerCase() === keep.toLowerCase(),
      score: scoreProject(repo, { now, facts: facts[repo.full_name] || null, mentions: mentions ? mentions(repo.name) : null }),
    }))
    .sort((a, b) =>
      by(b) - by(a) ||
      (b.repo.stargazers_count || 0) - (a.repo.stargazers_count || 0) ||
      String(b.repo.pushed_at || "").localeCompare(String(a.repo.pushed_at || "")))
    .map((project, index) => ({ ...project, rank: index + 1 }));
}

// Rank changes since the last different day: { name: +2 } means it climbed two places.
export function rankMoves(previous, ranked) {
  const moves = {};
  if (!previous) return moves;
  for (const project of ranked) {
    const before = previous[project.repo.full_name];
    moves[project.repo.full_name] = before ? before - project.rank : null;
  }
  return moves;
}

// The ranking as a Markdown note body (a table Obsidian, GitHub and every editor can read).
export function rankingMarkdown(owner, ranked, when) {
  const rows = ranked.map((p) => {
    const name = `[${p.repo.name}](${p.repo.html_url})${p.self ? " (Cairn)" : ""}`;
    const next = p.score.nextSteps[0] || "";
    return `| ${p.rank} | ${name} | ${p.score.tier} | ${p.score.overall} | ${p.score.solid} | ${p.score.potential} | ${next.replace(/\|/g, "/")} |`;
  });
  return [
    `Every repo of @${owner}, ranked by Cairn on ${when}. **Solid** = how finished and trustworthy it is; **Potential** = traction, momentum and how much it's on your mind. Cairn rewrites this note when the ranking changes.`,
    "",
    "| # | Project | Tier | Overall | Solid | Potential | Next step |",
    "|---|---|---|---|---|---|---|",
    ...rows,
    "",
  ].join("\n");
}

// ============================================================ loading

const local = {
  get(key, fallback = null) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage full or blocked: the ranking still works for this visit */
    }
  },
};

const view = { owner: null, loading: false, error: "", repos: null, facts: {}, loadedAt: 0, moves: {}, timer: null };

export function defaultOwner() {
  const saved = local.get(OWNER_KEY);
  if (saved) return saved;
  const login = auth.session()?.login;
  if (login) return login;
  if (state.info?.owner) return state.info.owner;
  return CAIRN.split("/")[0];
}

async function listRepos(owner) {
  const mine = auth.session()?.login?.toLowerCase() === owner.toLowerCase();
  // Signed in as this owner: include private repos too.
  const path = mine
    ? "/user/repos?affiliation=owner&sort=pushed&per_page=100"
    : `/users/${encodeURIComponent(owner)}/repos?type=owner&sort=pushed&per_page=100`;
  const repos = await gh.apiGet(path);
  if (!Array.isArray(repos)) throw new gh.GitHubError("GitHub didn't return a list of repos.");
  if (!repos.some((r) => String(r.full_name).toLowerCase() === CAIRN.toLowerCase())) {
    try {
      repos.push(await gh.apiGet(`/repos/${CAIRN}`));
    } catch {
      /* offline or rate-limited: rank the rest */
    }
  }
  return repos;
}

// Root listings tell us about tests, CI, README and packaging. Anonymous visitors get 60 GitHub requests an
// hour, so only the leading repos are checked then; signed in, every repo is (answers are cached 10 minutes).
async function loadFacts(repos) {
  const budget = auth.session() ? 60 : 12;
  const preliminary = rankProjects(repos, { mentions: mentionCounter() }).slice(0, budget);
  const facts = {};
  await Promise.all(preliminary.map(async ({ repo }) => {
    try {
      const listing = await gh.apiGet(`/repos/${repo.full_name}/contents/`);
      facts[repo.full_name] = rootFacts(Array.isArray(listing) ? listing.map((item) => item.name) : null);
    } catch {
      /* an empty repo or the hourly limit: score from what we have */
    }
  }));
  return facts;
}

function mentionCounter() {
  if (!state.notes?.length) return null;
  const cache = new Map();
  return (name) => {
    if (!cache.has(name)) cache.set(name, brainMentions(state.notes, name));
    return cache.get(name);
  };
}

function ranked() {
  if (!view.repos) return [];
  return rankProjects(view.repos, { sort: local.get(SORT_KEY, "overall"), facts: view.facts, mentions: mentionCounter() });
}

function rememberRanks(owner, list) {
  const history = local.get(HISTORY_KEY(owner), {});
  const today = new Date().toISOString().slice(0, 10);
  const ranks = Object.fromEntries(list.map((p) => [p.repo.full_name, p.rank]));
  if (history.current && history.current.date !== today) history.previous = history.current;
  history.current = { date: today, ranks };
  local.set(HISTORY_KEY(owner), history);
  return history.previous?.ranks || null;
}

export async function loadProjects({ owner = view.owner || defaultOwner(), quiet = false } = {}) {
  if (view.loading) return;
  view.owner = owner;
  view.loading = true;
  view.error = "";
  if (state.view === "projects") renderView();
  try {
    const repos = await listRepos(owner);
    view.repos = repos;
    view.facts = await loadFacts(repos);
    view.loadedAt = Date.now();
    const list = rankProjects(repos, { facts: view.facts, mentions: mentionCounter() });
    view.moves = rankMoves(rememberRanks(owner, list), list);
    if (local.get(AUTO_NOTE_KEY, false)) await saveRankingNote({ quiet: true });
  } catch (error) {
    view.error = error.message || "Couldn't reach GitHub.";
    if (!quiet) toast(view.error, { error: true });
  } finally {
    view.loading = false;
    if (state.view === "projects") renderView();
  }
}

// Re-rank on its own: when the page opens with old data, and every few minutes while Cairn stays open.
function ensureFresh() {
  if (!view.loading && Date.now() - view.loadedAt > STALE_MS) loadProjects({ quiet: Boolean(view.repos) });
  if (!view.timer) {
    view.timer = setInterval(() => {
      if (state.view === "projects" && !document.hidden && Date.now() - view.loadedAt > STALE_MS) loadProjects({ quiet: true });
    }, 60 * 1000);
  }
}

// ============================================================ the ranking note

function rankingNote() {
  return state.notes.find((note) => note.meta?.cairn === RANKING_MARK) || null;
}

export async function saveRankingNote({ quiet = false } = {}) {
  if (readOnly() || !state.store) return;
  const list = rankProjects(view.repos || [], { facts: view.facts, mentions: mentionCounter() });
  if (!list.length) return;
  const body = rankingMarkdown(view.owner, list, new Date().toISOString().slice(0, 10));
  const existing = rankingNote();
  if (existing) {
    // Only the table changes; the rest of the file (and any properties you added) stays as it was.
    if (existing.body.trim() !== body.trim()) await saveNote(existing.path, { body });
  } else {
    await createNote({ type: "note", title: "Project ranking", tags: ["projects"], body, extraMeta: { cairn: RANKING_MARK } });
  }
  if (!quiet) toast(existing ? "Project ranking note updated" : "Saved the ranking as a note");
}

// ============================================================ view

const TIER_HINT = {
  Flagship: "Strong on both: finished and getting attention.",
  Rising: "More potential than polish: worth finishing.",
  Solid: "Well built; needs reach.",
  Seedling: "Early. Pick one next step.",
  Dormant: "No push in six months.",
  Empty: "Nothing pushed yet.",
  Archived: "Archived on GitHub.",
};

function bar(label, value, cls) {
  return `<div class="proj-bar ${cls}" title="${label} ${value} / 100"><span class="proj-bar-label">${label}</span><span class="proj-bar-track"><span style="width:${value}%"></span></span><strong>${value}</strong></div>`;
}

function move(delta) {
  if (delta === null || delta === undefined) return `<span class="proj-move new" title="New since yesterday">new</span>`;
  if (!delta) return "";
  return delta > 0
    ? `<span class="proj-move up" title="Up ${delta} since yesterday">▲${delta}</span>`
    : `<span class="proj-move down" title="Down ${-delta} since yesterday">▼${-delta}</span>`;
}

function card(project) {
  const { repo, score, rank, self } = project;
  const demo = String(repo.homepage || "").trim() || (repo.has_pages ? `https://${repo.owner?.login}.github.io/${repo.name}/` : "");
  const chips = [
    `<span>${icon("star", "sm")} ${repo.stargazers_count || 0}</span>`,
    repo.language ? `<span>${esc(repo.language)}</span>` : "",
    `<span>pushed ${esc(relativeTime(Date.parse(repo.pushed_at || "")))}</span>`,
    repo.private ? `<span>${icon("lock", "sm")} private</span>` : "",
    demo ? `<a href="${esc(demo)}" target="_blank" rel="noopener">${icon("external", "sm")} demo</a>` : "",
  ].filter(Boolean).join("");
  return `
    <li class="proj-card${self ? " self" : ""}${rank <= 3 ? " podium" : ""}" data-project="${esc(repo.full_name)}">
      <div class="proj-rank"><span>${rank}</span>${move(view.moves[repo.full_name])}</div>
      <div class="proj-main">
        <div class="proj-title">
          <a href="${esc(repo.html_url)}" target="_blank" rel="noopener">${esc(repo.name)}</a>
          <span class="pill proj-tier tier-${score.tier.toLowerCase()}" title="${esc(TIER_HINT[score.tier] || "")}">${score.tier}</span>
          ${self ? `<span class="pill proj-self">this app</span>` : ""}
        </div>
        ${repo.description ? `<p class="proj-desc">${esc(repo.description)}</p>` : ""}
        <div class="proj-meta">${chips}</div>
        ${score.strengths.length ? `<p class="proj-why"><strong>Why:</strong> ${score.strengths.map(esc).join(" · ")}</p>` : ""}
        ${score.nextSteps.length ? `<p class="proj-next"><strong>Next:</strong> ${score.nextSteps.map(esc).join(" · ")}</p>` : ""}
      </div>
      <div class="proj-scores">
        <div class="proj-overall" title="Overall: the stronger of Solid and Potential counts most"><strong>${score.overall}</strong><span>overall</span></div>
        ${bar("Solid", score.solid, "solid")}
        ${bar("Potential", score.potential, "potential")}
      </div>
    </li>`;
}

export function projectsView() {
  if (!view.owner) view.owner = defaultOwner();
  ensureFresh();
  const sort = local.get(SORT_KEY, "overall");
  const list = ranked();
  const autoNote = local.get(AUTO_NOTE_KEY, false);
  const sortButton = (key, label) =>
    `<button type="button" class="${sort === key ? "on" : ""}" data-action="projects-sort" data-sort="${key}" aria-pressed="${sort === key}">${label}</button>`;
  const status = view.loading
    ? "Ranking…"
    : view.loadedAt
      ? `Re-ranked ${relativeTime(view.loadedAt)} · refreshes every 15 min`
      : "";
  const top = list[0];
  const lead = top
    ? `<p class="proj-lead">${icon("target", "sm")} Focus pick: <strong>${esc(top.repo.name)}</strong>${top.score.nextSteps[0] ? ` · next: ${esc(top.score.nextSteps[0])}` : ""}</p>`
    : "";
  const body = view.error && !list.length
    ? `<div class="card"><div class="empty-state">${icon("github")}<h3>Couldn't load the repos</h3><p>${esc(view.error)}</p></div></div>`
    : !list.length
      ? `<div class="card"><div class="empty-state">${icon("github")}<h3>${view.loading ? "Fetching repos from GitHub…" : "No repos yet"}</h3><p>Every public repo of @${esc(view.owner)} shows up here, ranked.</p></div></div>`
      : `<ol class="proj-list">${list.map(card).join("")}</ol>`;
  return `
    ${pageHead(
      "Projects",
      `Every repo of <strong>@${esc(view.owner)}</strong>, ranked by how <strong>solid</strong> it is and how much <strong>potential</strong> it has. Cairn re-ranks on its own.`,
      `<button class="btn" data-action="projects-refresh" ${view.loading ? "disabled" : ""}>${icon("refresh", "sm")} Re-rank</button>
       <button class="btn" data-action="projects-note" ${list.length ? "" : "disabled"}>${icon("note", "sm")} Save as note</button>`,
      "Project",
    )}
    <form class="proj-toolbar" id="projects-form" autocomplete="off">
      <label class="sr-only" for="projects-owner">GitHub user or organisation</label>
      <input class="input" id="projects-owner" name="owner" value="${esc(view.owner)}" placeholder="GitHub user, e.g. juw12">
      <button class="btn sm" type="submit">Rank</button>
      <div class="segmented" role="group" aria-label="Order by">${sortButton("overall", "Overall")}${sortButton("solid", "Most solid")}${sortButton("potential", "Most potential")}</div>
      <label class="proj-auto"><input type="checkbox" data-projects-auto ${autoNote ? "checked" : ""} ${readOnly() ? "disabled" : ""}> Keep a ranking note in the brain</label>
      <span class="muted proj-status">${status}</span>
    </form>
    ${lead}
    ${body}
    <p class="muted proj-foot">Solid: live demo, tests, CI, codebase size, months of upkeep, README, license, packaging. Potential: stars, recent pushes, how often your notes mention it, whether people can try it, forks. Overall weighs the stronger side more, so a project can top the list by being either very solid or very promising. Forks and archived repos are left out.</p>`;
}

// Clicks and the owner form (called from the app's global handlers).
export async function projectsAction(action, target) {
  if (action === "projects-refresh") {
    view.loadedAt = 0;
    return loadProjects();
  }
  if (action === "projects-sort") {
    local.set(SORT_KEY, target.dataset.sort);
    return renderView();
  }
  if (action === "projects-note") {
    if (readOnly()) return toast("This brain is read-only.");
    await saveRankingNote();
    return renderView();
  }
  if (action === "projects-auto-note") {
    local.set(AUTO_NOTE_KEY, target.checked);
    if (target.checked) await saveRankingNote();
  }
}

export function submitProjectsOwner(form) {
  const parsed = gh.parseGithubInput(form.owner.value);
  if (!parsed) return toast("Type a GitHub user or organisation, like juw12.");
  local.set(OWNER_KEY, parsed.owner);
  view.repos = null;
  view.moves = {};
  view.loadedAt = 0;
  return loadProjects({ owner: parsed.owner });
}

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
import { $, $$, icon, pageHead, plural, relativeTime, toast } from "./ui.js";
import { REPO_URL, createNote, readOnly, saveNote, state } from "./state.js";
import { renderView } from "./app.js";
import { boardHtml, dashboardHtml, recapHtml } from "./vibelayout.js";
import { openRepo, parseRepoHash, repoHash, repoView } from "./repo.js";

const DAY = 86400000;
const STALE_MS = 15 * 60 * 1000;
const OWNER_KEY = "cairn.projects.owner";
const SORT_KEY = "cairn.projects.sort";
const AUTO_NOTE_KEY = "cairn.projects.autoNote";
const GROUP_KEY = "cairn.projects.group";
const FILTER_KEY = "cairn.projects.filter";
const CLOSED_KEY = "cairn.projects.closedGroups";
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

// Raw numbers that are compared within your own set of repos (a 3 MB repo is big for one person, small for
// a company). Each entry: key -> repo -> number.
const RELATIVE = {
  size: (repo) => repo.size || 0,
  longevity: (repo, now) => days(Date.parse(repo.created_at || "") || now, Date.parse(repo.pushed_at || repo.updated_at || "") || now),
  stars: (repo) => repo.stargazers_count || 0,
  forks: (repo) => repo.forks_count || 0,
};

// Where `value` sits among `sorted` numbers: 0 = lowest, 1 = highest (ties share the middle). Zero is never a
// strength, so it always scores 0.
function percentile(sorted, value) {
  if (!value || sorted.length < 2) return 0;
  let below = 0;
  let equal = 0;
  for (const other of sorted) {
    if (other < value) below += 1;
    else if (other === value) equal += 1;
  }
  return clamp01((below + Math.max(0, equal - 1) / 2) / (sorted.length - 1));
}

function signalsFor(repo, { now, facts, mentions, cohort }) {
  const created = Date.parse(repo.created_at || "") || now;
  const pushed = Date.parse(repo.pushed_at || repo.updated_at || "") || created;
  const age = days(created, now);
  const idle = days(pushed, now);
  const span = days(created, pushed);
  const description = String(repo.description || "").trim();
  const topics = repo.topics || [];
  const hasDemo = Boolean(String(repo.homepage || "").trim()) || Boolean(repo.has_pages);
  const known = (value) => (facts ? (value ? 1 : 0) : null);
  // Half absolute (how good it is in general), half relative (how it compares with your other repos), so a
  // set of similar repos still spreads out instead of tying.
  const blend = (key, absolute) => (cohort?.sorted?.[key] ? 0.5 * absolute + 0.5 * percentile(cohort.sorted[key], RELATIVE[key](repo, now)) : absolute);

  const solid = [
    { key: "demo", label: "live demo or site", weight: 14, value: hasDemo ? 1 : 0, fix: "Ship a live demo (GitHub Pages is free)" },
    { key: "tests", label: "has tests", weight: 16, value: known(facts?.tests), fix: "Add tests" },
    { key: "size", label: "substantial codebase", weight: 14, value: blend("size", logScale(repo.size || 0, 5000)), fix: null },
    { key: "longevity", label: "kept up for months", weight: 12, value: blend("longevity", clamp01(span / 120)), fix: null },
    { key: "ci", label: "CI checks", weight: 10, value: known(facts?.ci), fix: "Add a CI workflow that runs the tests" },
    { key: "describe", label: "clear description", weight: 8, value: description.length >= 20 ? 1 : description ? 0.5 : 0, fix: "Write a one-line description on GitHub" },
    { key: "readme", label: "README", weight: 6, value: known(facts?.readme), fix: "Write a README with a screenshot" },
    // A package manifest counts in its favour, but its absence doesn't: a web app or a game needn't be a package.
    { key: "packaged", label: "installable package", weight: 6, value: facts?.packaged ? 1 : null, fix: null },
    { key: "license", label: "licensed", weight: 6, value: repo.license ? 1 : 0, fix: "Add a license (MIT takes a minute)" },
    { key: "topics", label: "tagged topics", weight: 4, value: clamp01(topics.length / 3), fix: "Add 3+ topics on GitHub" },
  ];
  const potential = [
    { key: "stars", label: plural(repo.stargazers_count || 0, "star"), weight: 26, value: blend("stars", logScale(repo.stargazers_count || 0, 100)), fix: hasDemo && (repo.stargazers_count || 0) < 5 ? "Share the demo where your users are to get first stars" : null },
    { key: "momentum", label: idle < 1 ? "pushed today" : `pushed ${Math.round(idle)}d ago`, weight: 24, value: Math.exp(-idle / 30), fix: `Push an update (idle ${Math.round(idle)} days)` },
    { key: "focus", label: mentions ? `in ${plural(mentions, "note")} of yours` : "in your notes", weight: 14, value: mentions === null ? null : logScale(mentions, 10), fix: "Write down what's next for it in a note", brain: true },
    { key: "usable", label: "usable by others today", weight: 12, value: hasDemo ? 1 : 0, fix: null },
    { key: "rising", label: "young and moving fast", weight: 10, value: age <= 180 && idle <= 14 ? 1 : age <= 365 && idle <= 30 ? 0.5 : 0, fix: null },
    { key: "forks", label: plural(repo.forks_count || 0, "fork"), weight: 8, value: blend("forks", logScale(repo.forks_count || 0, 20)), fix: null },
    { key: "discoverable", label: "easy to discover", weight: 6, value: topics.length >= 3 && description ? 1 : topics.length || description ? 0.4 : 0, fix: null },
  ];
  return { solid, potential, idle };
}

// Facts about the whole set of repos that make each score relative: sorted raw numbers, and how common each
// strength is (a strength every repo has, like "pushed today", says little about any one of them).
export function buildCohort(repos, { now = Date.now(), facts = {}, mentions = null } = {}) {
  const sorted = {};
  for (const [key, read] of Object.entries(RELATIVE)) sorted[key] = repos.map((repo) => read(repo, now)).sort((a, b) => a - b);
  const counts = {};
  for (const repo of repos) {
    const { solid, potential } = signalsFor(repo, { now, facts: facts[repo.full_name] || null, mentions: mentions ? mentions(repo.name) : null, cohort: { sorted } });
    for (const signal of [...solid, ...potential]) {
      if (signal.value !== null && signal.value >= 0.5) counts[signal.key] = (counts[signal.key] || 0) + 1;
    }
  }
  const common = Object.fromEntries(Object.entries(counts).map(([key, count]) => [key, repos.length ? count / repos.length : 0]));
  return { sorted, common };
}

// One repo -> its scores, tier, the reasons it ranks where it does and the next steps that would lift it.
//   repo:     a GitHub REST repo object (as returned by /users/:owner/repos)
//   facts:    rootFacts(...) or null when the root wasn't checked
//   mentions: notes in this brain that mention it, or null when there is no brain to ask
//   cohort:   buildCohort(...) of all the owner's repos, or null to score the repo on its own
export function scoreProject(repo, { now = Date.now(), facts = null, mentions = null, cohort = null } = {}) {
  const { solid: solidSignals, potential: potentialSignals, idle } = signalsFor(repo, { now, facts, mentions, cohort });
  const solid = weigh(solidSignals);
  const potential = weigh(potentialSignals);
  const overall = Math.round(0.6 * Math.max(solid, potential) + 0.4 * Math.min(solid, potential));

  let tier = "Seedling";
  if (repo.archived) tier = "Archived";
  else if (!repo.size) tier = "Empty";
  else if (idle > 180) tier = "Dormant";
  // Tiers follow the overall score, so a higher-ranked repo never gets a lower tier than one below it.
  else if (overall >= 70) tier = "Flagship";
  else if (overall >= 55) tier = potential >= solid + 10 ? "Rising" : "Solid";

  const all = [...solidSignals, ...potentialSignals].filter((s) => s.value !== null);
  // Strengths: what this repo has that most of your others don't come first.
  const rarity = (s) => 1.2 - (cohort?.common?.[s.key] ?? 0);
  const strengths = [...new Map(
    all
      .filter((s) => s.value >= 0.5)
      .sort((a, b) => b.weight * b.value * rarity(b) - a.weight * a.value * rarity(a))
      .map((s) => [s.label, s]),
  ).values()].slice(0, 3).map((s) => s.label);
  // Next steps: concrete changes to the repo first, then "write it down" for the brain.
  const gaps = all
    .filter((s) => s.fix && s.value < 0.5)
    .sort((a, b) => Number(Boolean(a.brain)) - Number(Boolean(b.brain)) || b.weight * (1 - b.value) - a.weight * (1 - a.value));
  const fixes = [...new Set(gaps.map((s) => s.fix))];

  return { solid, potential, overall, tier, strengths, nextSteps: fixes.slice(0, 2), fixes, idleDays: Math.round(idle) };
}

// ============================================================ categories (pure, tested)

// What a repo is about, worked out from what GitHub already says about it: its topics, its name and its
// description. No list to keep by hand: a new repo sorts itself, and changing a repo's topics on GitHub moves it.
// A word found in a topic counts 3, in the name 2, in the description 1 (at most 3 words of it), and the
// category with the most wins; a tie goes to the one listed first here. Phone and web apps says how a project is
// built, not what it is about, so it only takes a repo no other category claims.
const CATEGORIES = [
  { id: "ai", label: "AI and agents", words: "ai agent agents ai-agents llm llms mcp model-context-protocol claude claude-code codex opencode copilot openai ollama gpt chatbot prompt prompts prompt-injection guardrails llm-guardrails ai-safety ai-security llm-security local-llm machine-learning deep-learning embeddings interpretability activation-steering skills agent-skills ai-assistant ai-detection rag" },
  { id: "games", label: "Games", words: "game games gaming gamedev cozy-game browser-game indie-game html5-game cooking-game driving-game life-sim open-world unity godot unreal game-analytics first-person action-adventure low-poly mobile-legends aquarium" },
  { id: "science", label: "Engineering and science", words: "cad cadquery opencascade mechanical mechanical-engineering gd-t tolerance cfd computational-fluid-dynamics numerical-methods fortran physics mathematics computational-mathematics number-theory combinatorics erdos-problems arxiv research research-tools science simulation simulations simulator simulators 3d-printing additive-manufacturing topology-optimization 5-axis firmware embedded robotics motor battery bess energy-storage" },
  { id: "money", label: "Money and work", words: "money finance personal-finance payroll payslip salary budget budgets expense expenses spending ledger stocks portfolio-tracker trading algorithmic-trading crypto pricing invoice saas startup waitlist referral job jobs outreach linkedin" },
  { id: "media", label: "Media and content", words: "podcast rss news newsletter video videos short-video shorts youtube remotion transcripts photography stock-photography music content content-marketing marketing social-media instagram tiktok downloader design-system design-tokens color-palette themes theme" },
  { id: "notes", label: "Notes and learning", words: "notes note markdown obsidian brain second-brain knowledge-management kanban adr journal diary pomodoro productivity todo spaced-repetition fsrs flashcards language-learning chinese-learning mandarin hsk" },
  { id: "life", label: "Health and daily life", words: "health wellbeing digital-wellbeing fitness meditation habit-tracker habits accountability commitment food-diary meal-tracker cooking recipes family caregiving friendship wedding invitation interfaith" },
  { id: "travel", label: "Travel and places", words: "travel trip trips trip-planner maps map geolocation meeting-point leaflet openstreetmap ferry" },
  { id: "devtools", label: "Developer tools", words: "developer-tools developer-tool devtools cli git github github-action github-actions github-trending code-review merge-queue monorepo ci testing linter sdk library api vscode benchmark model-selection interpreter compiler programming-language" },
  { id: "system", label: "Desktop and system", words: "windows linux macos system-tray tray desktop desktop-widget desktop-assistant desktop-pet powershell performance process-priority old-hardware cpu pywebview electron tauri screen-reader kernel microkernel operating-system" },
  { id: "sites", label: "Sites and profile", words: "github-pages website site portfolio personal-website homepage landing-page blog profile awesome awesome-list documentation docs" },
  { id: "keep", label: "Backups and settings", words: "backup backups signing keystore config github-config dotfiles settings mirror sync" },
  // How it is built rather than what it is about: only for a repo that none of the lines above claims.
  { id: "apps", label: "Phone and web apps", fallback: true, words: "pwa offline-first web-app webapp android android-app ios iphone phone capacitor mobile react-native flutter indexeddb webrtc peer-to-peer" },
].map((category) => ({ ...category, words: new Set(category.words.split(" ")) }));
const OTHER = { id: "other", label: "Other" };

export const CATEGORY_LABELS = Object.fromEntries([...CATEGORIES, OTHER].map((category) => [category.id, category.label]));

const wordsOf = (value) => String(value || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

// A repo -> { id, label, why }. `why` lists the words that decided it, for the tooltip on the card.
export function categoryOf(repo) {
  const topics = (repo.topics || []).map((topic) => String(topic).toLowerCase());
  const name = wordsOf(repo.name);
  const described = new Set(wordsOf(repo.description));
  let best = null;
  for (const category of CATEGORIES) {
    const why = [];
    let points = 0;
    for (const topic of topics) {
      // The whole topic ("cozy-game"), or one word of it ("game"). "no-ai" says what a repo is not.
      const hit = category.words.has(topic) ? topic : /^(no|non|not|anti)-/.test(topic) ? null : topic.split("-").find((part) => category.words.has(part));
      if (hit) {
        points += 3;
        why.push(topic);
      }
    }
    for (const word of name) {
      if (category.words.has(word)) {
        points += 2;
        why.push(word);
      }
    }
    let fromText = 0;
    for (const word of described) {
      if (fromText < 3 && category.words.has(word)) {
        fromText += 1;
        points += 1;
        why.push(word);
      }
    }
    if (!points || (category.fallback && best)) continue;
    if (!best || points > best.points) best = { id: category.id, label: category.label, points, why: [...new Set(why)].slice(0, 4) };
  }
  return best ? { id: best.id, label: best.label, why: best.why } : { ...OTHER, why: [] };
}

// The list as it is shown: narrowed by the filter, in the chosen order, in one list or one group per category.
//   ranked: rankProjects(...) (each project keeps the place it has in the full ranking)
//   sort:   "name" for A to Z; anything else keeps the order of `ranked`
//   group:  true for one group per category, A to Z, with Other last
//   filter: { text, categories, tiers, shown } - a choice left empty lets everything through
// Returns { groups: [{ id, label, projects }], shown, total }.
export function arrangeProjects(ranked, { sort = "overall", group = false, filter = {} } = {}) {
  const text = wordsOf(filter.text);
  const picked = (list, value) => !list?.length || list.includes(value);
  const kept = ranked.filter((project) => {
    const { repo, score } = project;
    if (!picked(filter.categories, categoryOf(repo).id)) return false;
    if (!picked(filter.tiers, score.tier)) return false;
    if (!picked(filter.shown, repo.private ? "private" : "public")) return false;
    if (!text.length) return true;
    const hay = `${repo.name} ${repo.description || ""} ${(repo.topics || []).join(" ")} ${repo.language || ""}`.toLowerCase();
    return text.every((word) => hay.includes(word));
  });
  const byName = (a, b) => String(a.repo.name).localeCompare(String(b.repo.name), undefined, { sensitivity: "base", numeric: true });
  const ordered = sort === "name" ? [...kept].sort(byName) : kept;
  if (!group) return { groups: [{ id: "all", label: "", projects: ordered }], shown: kept.length, total: ranked.length };
  const groups = new Map();
  for (const project of ordered) {
    const category = categoryOf(project.repo);
    if (!groups.has(category.id)) groups.set(category.id, { id: category.id, label: category.label, projects: [] });
    groups.get(category.id).projects.push(project);
  }
  const sorted = [...groups.values()].sort((a, b) => Number(a.id === OTHER.id) - Number(b.id === OTHER.id) || a.label.localeCompare(b.label));
  return { groups: sorted, shown: kept.length, total: ranked.length };
}

// How many projects each filter choice would show, for the chips: { categories, tiers, shown } of [value, count].
export function filterCounts(ranked) {
  const tally = (read) => {
    const counts = new Map();
    for (const project of ranked) counts.set(read(project), (counts.get(read(project)) || 0) + 1);
    return counts;
  };
  const categories = tally((p) => categoryOf(p.repo).id);
  const tiers = tally((p) => p.score.tier);
  const shown = tally((p) => (p.repo.private ? "private" : "public"));
  const order = Object.keys(CATEGORY_LABELS);
  return {
    categories: [...categories.entries()].sort((a, b) => Number(a[0] === OTHER.id) - Number(b[0] === OTHER.id) || CATEGORY_LABELS[a[0]].localeCompare(CATEGORY_LABELS[b[0]]) || order.indexOf(a[0]) - order.indexOf(b[0])),
    tiers: ["Flagship", "Rising", "Solid", "Seedling", "Dormant", "Empty", "Archived"].filter((tier) => tiers.has(tier)).map((tier) => [tier, tiers.get(tier)]),
    shown: ["public", "private"].filter((kind) => shown.has(kind)).map((kind) => [kind, shown.get(kind)]),
  };
}

const SORTS = {
  overall: (p) => p.score.overall,
  solid: (p) => p.score.solid,
  potential: (p) => p.score.potential,
};

// Repos that hold settings rather than a project: the profile README (owner/owner) and owner/.github.
const isMeta = (repo) => {
  const name = String(repo.name || "").toLowerCase();
  return name === ".github" || name === String(repo.owner?.login || "").toLowerCase();
};

// Score and order a list of repos. Forks, archived and profile/settings repos are left out (they aren't
// projects to grow), except Cairn itself, which is always kept. Ties go to stars, then the most recent push.
export function rankProjects(repos, { sort = "overall", now = Date.now(), facts = {}, mentions = null, keep = CAIRN } = {}) {
  const by = SORTS[sort] || SORTS.overall;
  const seen = new Set();
  const kept = repos.filter((repo) => {
    const name = String(repo.full_name || "").toLowerCase();
    if (seen.has(name)) return false;
    seen.add(name);
    if (name === keep.toLowerCase()) return true;
    return !repo.fork && !repo.archived && !isMeta(repo);
  });
  const cohort = buildCohort(kept, { now, facts, mentions });
  const scored = kept
    .map((repo) => ({
      repo,
      self: String(repo.full_name || "").toLowerCase() === keep.toLowerCase(),
      score: scoreProject(repo, { now, facts: facts[repo.full_name] || null, mentions: mentions ? mentions(repo.name) : null, cohort }),
    }))
    .sort((a, b) =>
      by(b) - by(a) ||
      (b.repo.stargazers_count || 0) - (a.repo.stargazers_count || 0) ||
      String(b.repo.pushed_at || "").localeCompare(String(a.repo.pushed_at || "")))
    .map((project, index) => ({ ...project, rank: index + 1 }));
  // A step most of your repos need is advice about you, not about one repo: it is shown once above the list,
  // and each card keeps the steps that are specific to it.
  const shared = new Set(sharedSteps(scored));
  for (const project of scored) {
    const specific = project.score.fixes.filter((fix) => !shared.has(fix));
    // Nothing specific left (usually your best repos): still show the one step that would lift it most.
    project.score.nextSteps = specific.length ? specific.slice(0, 2) : project.score.fixes.slice(0, 1);
  }
  return scored;
}

// Next steps that at least `share` of the repos have in common (only for sets of 6+ repos).
export function sharedSteps(ranked, share = 0.6) {
  if (ranked.length < 6) return [];
  const counts = new Map();
  for (const project of ranked) {
    for (const fix of project.score.fixes || []) counts.set(fix, (counts.get(fix) || 0) + 1);
  }
  return [...counts.entries()]
    .filter(([, count]) => count / ranked.length >= share)
    .sort((a, b) => b[1] - a[1])
    .map(([fix]) => fix);
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

const view = { owner: null, loading: false, error: "", repos: null, facts: {}, loadedAt: 0, failedAt: 0, moves: {}, timer: null, query: "" };

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
  const repos = [];
  // 100 per page; people with more repos get up to 300 (newest pushes first).
  for (let page = 1; page <= 3; page += 1) {
    const batch = await gh.apiGet(`${path}&page=${page}`);
    if (!Array.isArray(batch)) throw new gh.GitHubError("GitHub didn't return a list of repos.");
    repos.push(...batch);
    if (batch.length < 100) break;
  }
  if (!repos.some((r) => String(r.full_name).toLowerCase() === CAIRN.toLowerCase())) {
    try {
      repos.push(await gh.apiGet(`/repos/${CAIRN}`));
    } catch {
      /* offline or rate-limited: rank the rest */
    }
  }
  return repos;
}

// Root listings tell us about tests, CI, README and packaging. A root only changes when the repo is pushed, so
// answers are kept per repo and push time, and only new or pushed repos are asked again. Anonymous visitors get
// 60 GitHub requests an hour, so they check at most 12 repos per refresh (the leading ones first); every
// refresh fills in more until all are known. Signed in, up to 60 per refresh.
const FACTS_KEY = (owner) => `cairn.projects.facts:${owner.toLowerCase()}`;

async function loadFacts(owner, repos) {
  const budget = auth.session() ? 60 : 12;
  const saved = local.get(FACTS_KEY(owner), {});
  const facts = {};
  const missing = [];
  for (const { repo } of rankProjects(repos, { mentions: mentionCounter() })) {
    const cached = saved[repo.full_name];
    if (cached && cached.pushed === repo.pushed_at) facts[repo.full_name] = cached.facts;
    else missing.push(repo);
  }
  await Promise.all(missing.slice(0, budget).map(async (repo) => {
    try {
      const listing = await gh.apiGet(`/repos/${repo.full_name}/contents/`);
      const found = rootFacts(Array.isArray(listing) ? listing.map((item) => item.name) : null);
      if (found) {
        facts[repo.full_name] = found;
        saved[repo.full_name] = { pushed: repo.pushed_at, facts: found };
      }
    } catch {
      /* an empty repo or the hourly limit: score from what we have */
    }
  }));
  local.set(FACTS_KEY(owner), saved);
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

// A to Z is an order to read the list in, not a ranking: the places shown then are those of the overall ranking.
function ranked() {
  if (!view.repos) return [];
  return rankProjects(view.repos, { sort: local.get(SORT_KEY, "overall"), facts: view.facts, mentions: mentionCounter() });
}

// The filter as chosen on this device. The words typed in the box are kept for this visit only.
function chosenFilter() {
  const saved = local.get(FILTER_KEY, {}) || {};
  const list = (value) => (Array.isArray(value) ? value : []);
  return { text: view.query || "", categories: list(saved.categories), tiers: list(saved.tiers), shown: list(saved.shown) };
}

// A choice that names nothing in the list any more (a category whose last repo moved) is dropped, so an old
// choice can never leave the page empty.
function liveFilter(list) {
  const chosen = chosenFilter();
  const counts = filterCounts(list);
  const present = (key) => new Set(counts[key].map(([value]) => value));
  return {
    text: chosen.text,
    categories: chosen.categories.filter((value) => present("categories").has(value)),
    tiers: chosen.tiers.filter((value) => present("tiers").has(value)),
    shown: chosen.shown.filter((value) => present("shown").has(value)),
  };
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
    view.facts = await loadFacts(owner, repos);
    view.loadedAt = Date.now();
    const list = rankProjects(repos, { facts: view.facts, mentions: mentionCounter() });
    view.moves = rankMoves(rememberRanks(owner, list), list);
    if (local.get(AUTO_NOTE_KEY, false)) await saveRankingNote({ quiet: true });
  } catch (error) {
    view.error = error.message || "Couldn't reach GitHub.";
    view.failedAt = Date.now();
    if (!quiet) toast(view.error, { error: true });
  } finally {
    view.loading = false;
    if (state.view === "projects") renderView();
  }
}

// Re-rank on its own: when the page opens with old data, and every few minutes while Cairn stays open.
// A failed fetch waits as long as a stale ranking before trying again (Refresh retries at once),
// so a GitHub outage or rate limit never turns into a request loop.
function isStale() {
  const now = Date.now();
  return now - view.loadedAt > STALE_MS && now - view.failedAt > STALE_MS;
}

function ensureFresh() {
  if (!view.loading && isStale()) loadProjects({ quiet: Boolean(view.repos) });
  if (!view.timer) {
    view.timer = setInterval(() => {
      if (state.view === "projects" && !document.hidden && isStale()) loadProjects({ quiet: true });
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

// delta: a number of places (from rankMoves), null for a repo that wasn't ranked yesterday, undefined when
// there is no earlier ranking to compare with (the first day): then nothing is shown.
function move(delta) {
  if (delta === undefined) return "";
  if (delta === null) return `<span class="proj-move new" title="New since yesterday">new</span>`;
  if (!delta) return "";
  return delta > 0
    ? `<span class="proj-move up" title="Up ${delta} since yesterday">▲${delta}</span>`
    : `<span class="proj-move down" title="Down ${-delta} since yesterday">▼${-delta}</span>`;
}

// A repo's own page inside Cairn (repo.js): its pictures, videos, README, pushes and files.
const repoPage = (repo) => repoHash({ owner: repo.owner?.login || repo.full_name.split("/")[0], name: repo.name });

function card(project, { grouped = false } = {}) {
  const { repo, score, rank, self } = project;
  const category = categoryOf(repo);
  const sortedBy = category.why.length
    ? `Sorted into ${category.label} by Cairn, from: ${category.why.join(", ")}. Change the repo's topics on GitHub to move it.`
    : "Nothing in its topics, name or description says what it is about. Add topics on GitHub and Cairn sorts it.";
  const demo = String(repo.homepage || "").trim() || (repo.has_pages ? `https://${repo.owner?.login}.github.io/${repo.name}/` : "");
  const chips = [
    `<span>${icon("star", "sm")} ${repo.stargazers_count || 0}</span>`,
    repo.language ? `<span>${esc(repo.language)}</span>` : "",
    `<span>pushed ${esc(relativeTime(Date.parse(repo.pushed_at || "")))}</span>`,
    repo.private ? `<span>${icon("lock", "sm")} private</span>` : "",
    demo ? `<a href="${esc(demo)}" target="_blank" rel="noopener">${icon("external", "sm")} demo</a>` : "",
    `<a href="${esc(repo.html_url)}" data-github target="_blank" rel="noopener" title="Open on github.com">${icon("github", "sm")} GitHub</a>`,
  ].filter(Boolean).join("");
  return `
    <li class="proj-card${self ? " self" : ""}${rank <= 3 ? " podium" : ""}" data-project="${esc(repo.full_name)}">
      <div class="proj-rank"><span>${rank}</span>${move(view.moves[repo.full_name])}</div>
      <div class="proj-main">
        <div class="proj-title">
          <a href="${esc(repoPage(repo))}" data-repo-go title="Look inside: pictures, videos, README, pushes and files">${esc(repo.name)}</a>
          <span class="pill proj-tier tier-${score.tier.toLowerCase()}" title="${esc(TIER_HINT[score.tier] || "")}">${score.tier}</span>
          ${grouped ? "" : `<span class="pill proj-cat" data-category="${category.id}" title="${esc(sortedBy)}">${esc(category.label)}</span>`}
          ${self ? `<span class="pill proj-self">this app</span>` : ""}
        </div>
        ${repo.description ? `<p class="proj-desc" title="${esc(repo.description)}">${esc(repo.description)}</p>` : ""}
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

// Anonymous GitHub limits mean tests/CI/README are only known for some repos at first; say so honestly.
function coverage(list) {
  const checked = list.filter((p) => view.facts[p.repo.full_name]).length;
  if (!list.length || checked >= list.length) return "";
  return ` · tests & CI checked for ${checked} of ${list.length} (more each refresh${auth.session() ? "" : "; sign in to check all"})`;
}

const WEEK = 7 * DAY;

// What changed in the ranking: the pick, what most repos share, who moved since yesterday and what was pushed lately.
function projectsRecap(list) {
  const top = list[0];
  const shared = sharedSteps(list).slice(0, 2);
  const lead = top
    ? `<p class="proj-lead">${icon("target", "sm")} Focus pick: <strong>${esc(top.repo.name)}</strong>${top.score.nextSteps[0] ? ` · next: ${esc(top.score.nextSteps[0])}` : ""}</p>
       ${shared.length ? `<p class="proj-lead proj-shared">${icon("sparkle", "sm")} Across most of your repos: ${shared.map(esc).join(" · ")}</p>` : ""}`
    : "";
  const moved = list
    .filter((p) => view.moves[p.repo.full_name] || view.moves[p.repo.full_name] === null)
    .sort((a, b) => Math.abs(view.moves[b.repo.full_name] || 0) - Math.abs(view.moves[a.repo.full_name] || 0))
    .slice(0, 4)
    .map((p) => ({ when: "rank", html: `${move(view.moves[p.repo.full_name])} <strong>${esc(p.repo.name)}</strong> is now #${p.rank}` }));
  const pushed = [...list]
    .filter((p) => Date.now() - Date.parse(p.repo.pushed_at || "") < WEEK)
    .sort((a, b) => Date.parse(b.repo.pushed_at) - Date.parse(a.repo.pushed_at))
    .slice(0, 4)
    .map((p) => ({ when: relativeTime(Date.parse(p.repo.pushed_at)), html: `pushed to <a href="${esc(repoPage(p.repo))}" data-repo-go><strong>${esc(p.repo.name)}</strong></a> (#${p.rank}, ${p.score.tier})` }));
  return `${lead}${recapHtml([...moved, ...pushed], list.length ? "No rank moves since yesterday and nothing pushed this week." : "Nothing ranked yet.")}`;
}

function projectsDashboard(list) {
  const tier = (name) => list.filter((p) => p.score.tier === name).length;
  const top = list[0];
  const average = list.length ? Math.round(list.reduce((sum, p) => sum + p.score.overall, 0) / list.length) : 0;
  const pushed = list.filter((p) => Date.now() - Date.parse(p.repo.pushed_at || "") < WEEK).length;
  return dashboardHtml([
    { value: list.length, label: "repos ranked" },
    { value: tier("Flagship"), label: "flagship", title: TIER_HINT.Flagship },
    { value: tier("Rising"), label: "rising", title: TIER_HINT.Rising },
    { value: top ? top.score.overall : "–", label: "top score", sub: top ? top.repo.name : "" },
    { value: list.length ? average : "–", label: "average score" },
    { value: pushed, label: "pushed this week" },
  ]);
}

const SORT_WORDS = { overall: "by overall", solid: "by most solid", potential: "by most potential", name: "A to Z" };

function rankingSummary(list, arranged, sort, grouped) {
  const count = arranged.shown === arranged.total ? plural(list.length, "repo") : `${arranged.shown} of ${plural(arranged.total, "repo")}`;
  return `${count} ${SORT_WORDS[sort] || SORT_WORDS.overall}${grouped ? ", by category" : ""}`;
}

// The filter: a box for words, then one row of chips per question. Chips of one row add up (Games or Notes);
// rows narrow each other (Games that are also Flagship). A row with a single choice is left out.
function filterBar(list, filter, arranged, grouped) {
  const counts = filterCounts(list);
  const row = (key, label, name) => {
    if (counts[key].length < 2) return "";
    const chips = counts[key].map(([value, count]) => {
      const on = filter[key].includes(value);
      return `<button type="button" class="chip${on ? " on" : ""}" data-action="projects-filter" data-kind="${key}" data-value="${esc(value)}" aria-pressed="${on}">${esc(name(value))} <b>${count}</b></button>`;
    }).join("");
    return `<div class="proj-filter-row" role="group" aria-label="${label}"><span class="proj-filter-label">${label}</span>${chips}</div>`;
  };
  const active = Boolean(filter.text || filter.categories.length || filter.tiers.length || filter.shown.length);
  const tally = active
    ? `<span class="proj-filter-count" role="status">Showing ${arranged.shown} of ${arranged.total}</span><button type="button" class="btn sm ghost" data-action="projects-filter-clear">Show all</button>`
    : "";
  return `
    <div class="proj-filters" data-grouped="${grouped ? "1" : ""}">
      <div class="proj-filter-row">
        <label class="sr-only" for="projects-filter">Filter the projects</label>
        <input class="input" id="projects-filter" type="search" value="${esc(filter.text)}" placeholder="Filter: a name, a word, a language" autocomplete="off">
        ${tally}
      </div>
      ${row("categories", "Category", (id) => CATEGORY_LABELS[id] || id)}
      ${row("tiers", "Tier", (tier) => tier)}
      ${row("shown", "Kind", (kind) => (kind === "private" ? "Private" : "Public"))}
    </div>`;
}

function listHtml(arranged, grouped) {
  if (!arranged.shown) {
    return `<div class="card"><div class="empty-state">${icon("search")}<h3>Nothing matches the filter</h3><p>${arranged.total} ${arranged.total === 1 ? "project is" : "projects are"} ranked. <button type="button" class="link-button" data-action="projects-filter-clear">Show all</button></p></div></div>`;
  }
  if (!grouped) return `<ol class="proj-list">${arranged.groups[0].projects.map((project) => card(project)).join("")}</ol>`;
  const closed = new Set(local.get(CLOSED_KEY, []) || []);
  return arranged.groups.map((group) => `
    <details class="proj-group" data-proj-group="${group.id}"${closed.has(group.id) ? "" : " open"}>
      <summary title="Sorted by Cairn from each repo's topics, name and description"><span class="proj-group-name">${esc(group.label)}</span><span class="count">${group.projects.length}</span></summary>
      <ol class="proj-list">${group.projects.map((project) => card(project, { grouped: true })).join("")}</ol>
    </details>`).join("");
}

export function projectsView() {
  // An address that names a repo (#/projects/juw12/cairn/…) shows that repo's page instead of the ranking.
  const repo = parseRepoHash(location.hash);
  if (repo) return repoView(repo);
  if (!view.owner) view.owner = defaultOwner();
  ensureFresh();
  const sort = local.get(SORT_KEY, "overall");
  const grouped = Boolean(local.get(GROUP_KEY, false));
  const list = ranked();
  const filter = liveFilter(list);
  const arranged = arrangeProjects(list, { sort, group: grouped, filter });
  const autoNote = local.get(AUTO_NOTE_KEY, false);
  const sortButton = (key, label, title = "") =>
    `<button type="button" class="${sort === key ? "on" : ""}" data-action="projects-sort" data-sort="${key}" aria-pressed="${sort === key}"${title ? ` title="${title}"` : ""}>${label}</button>`;
  const groupButton = (on, label) =>
    `<button type="button" class="${grouped === on ? "on" : ""}" data-action="projects-group" data-group="${on ? "1" : ""}" aria-pressed="${grouped === on}">${label}</button>`;
  const status = view.loading
    ? "Ranking…"
    : view.loadedAt
      ? `Re-ranked ${relativeTime(view.loadedAt)} · refreshes every 15 min${coverage(list)}`
      : "";
  const top = list[0];
  const body = view.error && !list.length
    ? `<div class="card"><div class="empty-state">${icon("github")}<h3>Couldn't load the repos</h3><p>${esc(view.error)}</p></div></div>`
    : !list.length
      ? `<div class="card"><div class="empty-state">${icon("github")}<h3>${view.loading ? "Fetching repos from GitHub…" : "No repos yet"}</h3><p>Every public repo of @${esc(view.owner)} shows up here, ranked.</p></div></div>`
      : `${filterBar(list, filter, arranged, grouped)}${listHtml(arranged, grouped)}`;
  const toolbar = `
    <form class="proj-toolbar" id="projects-form" autocomplete="off">
      <label class="sr-only" for="projects-owner">GitHub user or organisation</label>
      <input class="input" id="projects-owner" name="owner" value="${esc(view.owner)}" placeholder="GitHub user, e.g. juw12" title="A GitHub user ranks their repos. user/repo opens that one repo.">
      <button class="btn sm" type="submit">Rank</button>
      <div class="segmented" role="group" aria-label="Order by">${sortButton("overall", "Overall")}${sortButton("solid", "Most solid")}${sortButton("potential", "Most potential")}${sortButton("name", "A to Z", "By name. The number on each project stays its place in the overall ranking")}</div>
      <div class="segmented" role="group" aria-label="Show as">${groupButton(false, "One list")}${groupButton(true, "By category")}</div>
      <label class="proj-auto"><input type="checkbox" data-projects-auto ${autoNote ? "checked" : ""} ${readOnly() ? "disabled" : ""}> Keep a ranking note in the brain</label>
      <span class="muted proj-status">${status}</span>
    </form>`;
  const blocks = {
    dashboard: list.length ? { html: projectsDashboard(list), summary: `${plural(list.length, "repo")} · top ${top.repo.name} ${top.score.overall}` } : null,
    recap: list.length ? { html: projectsRecap(list), summary: `focus: ${top.repo.name}` } : null,
    ranking: { html: `${toolbar}${body}`, summary: list.length ? rankingSummary(list, arranged, sort, grouped) : view.loading ? "ranking…" : "" },
    scoring: {
      html: `<p class="muted proj-foot">Solid: live demo, tests, CI, codebase size, months of upkeep, README, license, packaging. Potential: stars, recent pushes, how often your notes mention it, whether people can try it, forks. Overall weighs the stronger side more, so a project can top the list by being either very solid or very promising. Forks, archived repos and profile/settings repos are left out.</p>
        <p class="muted proj-foot">Categories: Cairn sorts each repo by itself from its topics on GitHub, its name and its description (a topic counts most). Point at the category on a project to see which words decided it. To move a project, change its topics on GitHub. A to Z orders by name and keeps each project's place in the overall ranking as its number.</p>`,
      summary: "what Solid, Potential and Overall count, and how the categories are chosen",
    },
  };
  return `
    ${pageHead(
      "Projects",
      `Every repo of <strong>@${esc(view.owner)}</strong>, ranked by how <strong>solid</strong> it is and how much <strong>potential</strong> it has. Cairn re-ranks on its own.`,
      `<button class="btn" data-action="projects-refresh" ${view.loading ? "disabled" : ""}>${icon("refresh", "sm")} Re-rank</button>
       <button class="btn" data-action="projects-note" ${list.length ? "" : "disabled"}>${icon("note", "sm")} Save as note</button>`,
      "Project",
    )}
    ${boardHtml("projects", blocks)}`;
}

// On a phone a row of chips scrolls sideways. Drawing the page again must not throw a row back to its start,
// or the chip just pressed would jump out of sight.
function redrawKeepingRows() {
  const places = $$(".proj-filter-row[aria-label]").map((row) => [row.getAttribute("aria-label"), row.scrollLeft]);
  renderView();
  for (const [label, left] of places) {
    const row = $(`.proj-filter-row[aria-label="${label}"]`);
    if (row && left) row.scrollLeft = left;
  }
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
  if (action === "projects-group") {
    local.set(GROUP_KEY, Boolean(target.dataset.group));
    return renderView();
  }
  if (action === "projects-filter") {
    const kind = target.dataset.kind;
    if (!["categories", "tiers", "shown"].includes(kind)) return;
    const chosen = liveFilter(ranked());
    const value = target.dataset.value;
    const next = chosen[kind].includes(value) ? chosen[kind].filter((other) => other !== value) : [...chosen[kind], value];
    local.set(FILTER_KEY, { categories: chosen.categories, tiers: chosen.tiers, shown: chosen.shown, [kind]: next });
    redrawKeepingRows();
    return $(`[data-action="projects-filter"][data-kind="${kind}"][data-value="${CSS.escape(value)}"]`)?.focus({ preventScroll: true });
  }
  if (action === "projects-filter-clear") {
    view.query = "";
    local.set(FILTER_KEY, {});
    return renderView();
  }
  if (action === "projects-filter-text") {
    // Typing redraws the list, so the box gets its focus and caret back.
    view.query = target.value;
    const caret = target.selectionStart;
    redrawKeepingRows();
    const input = $("#projects-filter");
    input?.focus({ preventScroll: true });
    if (input && caret !== null) input.setSelectionRange(caret, caret);
    return;
  }
  if (action === "projects-group-toggle") {
    const closed = new Set(local.get(CLOSED_KEY, []) || []);
    if (target.open) closed.delete(target.dataset.projGroup);
    else closed.add(target.dataset.projGroup);
    return local.set(CLOSED_KEY, [...closed]);
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
  // "juw12/cairn" (or a pasted github.com link to a repo) opens that repo's page.
  if (parsed.repo) return openRepo(parsed.owner, parsed.repo);
  local.set(OWNER_KEY, parsed.owner);
  view.repos = null;
  view.moves = {};
  view.loadedAt = 0;
  return loadProjects({ owner: parsed.owner });
}

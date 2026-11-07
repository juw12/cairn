// Proof of work: claims written in notes, checked against GitHub. The format is specified in PROOF.md.
//
// A claim is any note with a `claim:` property. Its `evidence:` links point at work on GitHub
// (repos, pull requests, commits, releases), and `attested_by:` links point at vouch files that
// other people committed to their own repos. Nothing here trusts what the note says about itself:
// every level above 1 comes from facts GitHub returns.

import { parseNote } from "./core.js";

export const LEVELS = [
  { level: 1, key: "claimed", label: "Claimed", hint: "Self-reported. No checkable evidence yet." },
  { level: 2, key: "artifact", label: "Backed by work", hint: "Points at work on GitHub this person owns or authored." },
  { level: 3, key: "used", label: "Used by others", hint: "Merged into someone else's repo, or forked or starred by others." },
  { level: 4, key: "vouched", label: "Vouched", hint: "Another GitHub user confirms it in a file in their own repo." },
];

// A repo counts as "used by others" at this many stars (or any fork). Owners can star their own repo once.
export const STAR_THRESHOLD = 3;
// Most GitHub checks one profile may spend (anonymous visitors get 60 requests an hour).
export const CHECK_BUDGET = 40;

const GH_RE = /^https?:\/\/(?:www\.)?github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]{1,100})(?:\/(.*))?$/i;

const same = (a, b) => String(a || "").toLowerCase() === String(b || "").toLowerCase();

export function levelInfo(level) {
  return LEVELS[Math.min(Math.max(level, 1), 4) - 1];
}

// A frontmatter value as a list of strings: [a, b], "- a" lists, or one value.
export function asList(value) {
  if (Array.isArray(value)) return value.map(String).map((v) => v.trim()).filter(Boolean);
  if (typeof value === "string" && value.trim()) return value.trim().split(/\s+/);
  return [];
}

// True only when the note asks to be published (`publish: true`) and isn't marked private.
export function isPublic(meta = {}) {
  const yes = (value) => value === true || String(value).trim().toLowerCase() === "true";
  return yes(meta.publish) && !yes(meta.private);
}

// What a link points at. Unknown links are kept as kind "link" and never raise the level.
export function parseEvidence(url) {
  const text = String(url || "").trim().replace(/[?#].*$/, "").replace(/\/+$/, "");
  const match = GH_RE.exec(text);
  if (!match) return { kind: "link", url: text };
  const [, owner, rawRepo, rest = ""] = match;
  const repo = rawRepo.replace(/\.git$/i, "");
  const base = { owner, repo, url: text };
  const parts = rest.split("/").filter(Boolean);
  if (parts[0] === "pull" && /^\d+$/.test(parts[1] || "")) return { kind: "pr", number: Number(parts[1]), ...base };
  if (parts[0] === "commit" && /^[0-9a-f]{7,40}$/i.test(parts[1] || "")) return { kind: "commit", sha: parts[1], ...base };
  if (parts[0] === "releases" && parts[1] === "tag" && parts[2]) return { kind: "release", tag: decodeURIComponent(parts.slice(2).join("/")), ...base };
  if (parts[0] === "blob" && parts.length >= 3) return { kind: "file", branch: parts[1], path: parts.slice(2).map(decodeURIComponent).join("/"), ...base };
  if (!parts.length || parts[0] === "tree") return { kind: "repo", ...base };
  return { kind: "link", url: text };
}

// All claims in a list of notes (from core.noteFromFile), in a stable order.
export function claimsFromNotes(notes) {
  return notes
    .filter((note) => typeof note.meta?.claim === "string" && note.meta.claim.trim())
    .map((note) => ({
      path: note.path,
      slug: note.slug,
      title: note.meta.claim.trim(),
      noteTitle: note.title,
      role: typeof note.meta.role === "string" ? note.meta.role : "",
      outcome: typeof note.meta.outcome === "string" ? note.meta.outcome : "",
      when: String(note.meta.date || note.meta.created || ""),
      isPublic: isPublic(note.meta),
      evidence: asList(note.meta.evidence).map(parseEvidence),
      attestations: asList(note.meta.attested_by).map(parseEvidence),
    }))
    .sort((a, b) => (b.when || "").localeCompare(a.when || "") || a.title.localeCompare(b.title));
}

// ------------------------------------------------------------ judging facts (pure)

// One evidence link + what GitHub said about it -> { ok, level, note }.
// `fact` is null when the lookup failed; `fact.error` carries the reason.
export function judgeEvidence(subject, evidence, fact) {
  if (evidence.kind === "link") return { ok: false, level: 1, note: "Not a GitHub link, so it can't be checked." };
  if (!fact || fact.error) return { ok: false, level: 1, note: fact?.error || "Couldn't check this on GitHub." };
  const ownsRepo = same(fact.owner, subject);
  switch (evidence.kind) {
    case "repo":
    case "file": {
      if (!ownsRepo) return { ok: false, level: 1, note: `Repo belongs to @${fact.owner}. Link a merged pull request or commit instead.` };
      if (fact.fork) return { ok: false, level: 1, note: "This repo is a fork. Link the original or your merged pull request." };
      const used = (fact.forks || 0) >= 1 || (fact.stars || 0) >= STAR_THRESHOLD;
      return used
        ? { ok: true, level: 3, note: `Owned by @${fact.owner}; ${fact.stars} stars, ${fact.forks} forks.` }
        : { ok: true, level: 2, note: `Owned by @${fact.owner}${fact.createdAt ? `, created ${fact.createdAt.slice(0, 10)}` : ""}.` };
    }
    case "pr": {
      if (!same(fact.author, subject)) return { ok: false, level: 1, note: `Opened by @${fact.author || "someone else"}, not @${subject}.` };
      if (!fact.merged) return { ok: false, level: 1, note: "Pull request isn't merged." };
      return ownsRepo
        ? { ok: true, level: 2, note: `Merged ${String(fact.mergedAt || "").slice(0, 10)} into their own repo.` }
        : { ok: true, level: 3, note: `Merged by ${evidence.owner}/${evidence.repo} on ${String(fact.mergedAt || "").slice(0, 10)}.` };
    }
    case "commit":
      return same(fact.author, subject)
        ? { ok: true, level: 2, note: `Authored by @${fact.author}${fact.date ? ` on ${fact.date.slice(0, 10)}` : ""}.` }
        : { ok: false, level: 1, note: `Commit author is ${fact.author ? `@${fact.author}` : "not linked to a GitHub account"}.` };
    case "release":
      return ownsRepo || same(fact.author, subject)
        ? { ok: true, level: 2, note: `Released ${String(fact.date || "").slice(0, 10)}.` }
        : { ok: false, level: 1, note: `Release belongs to @${fact.owner}.` };
    default:
      return { ok: false, level: 1, note: "Unknown evidence type." };
  }
}

// One attested_by link + the vouch file's text -> { ok, level, note, voucher }.
export function judgeVouch(subject, claim, link, text) {
  if (link.kind !== "file") return { ok: false, level: 1, note: "Vouch links must point at a file on GitHub (…/blob/…)." };
  const voucher = link.owner;
  if (same(voucher, subject)) return { ok: false, level: 1, note: "You can't vouch for yourself.", voucher };
  if (text == null) return { ok: false, level: 1, note: "Vouch file not found.", voucher };
  const { meta, body } = parseNote(text);
  if (!same(meta.vouch_for, subject)) return { ok: false, level: 1, note: `The file vouches for @${meta.vouch_for || "nobody"}.`, voucher };
  if (String(meta.claim || "").trim() !== claim.slug) return { ok: false, level: 1, note: `The file vouches for claim "${meta.claim || ""}", not "${claim.slug}".`, voucher };
  const relation = typeof meta.relation === "string" && meta.relation ? meta.relation : "";
  const words = body.trim().split(/\n/)[0].slice(0, 160);
  return { ok: true, level: 4, note: `@${voucher}${relation ? ` (${relation})` : ""}: ${words || "vouched"}`, voucher };
}

// The level a claim earns from its judged links: the best passing one, never below 1.
export function claimLevel(results) {
  return results.reduce((best, result) => (result.ok ? Math.max(best, result.level) : best), 1);
}

// Count of claims per level, for the profile summary.
export function levelCounts(checked) {
  const counts = { 1: 0, 2: 0, 3: 0, 4: 0 };
  for (const item of checked) counts[item.level] += 1;
  return counts;
}

// ------------------------------------------------------------ checking on GitHub

// GitHub facts for one evidence link. `api(path)` returns parsed JSON for https://api.github.com + path.
async function factFor(evidence, api) {
  const repoPath = `/repos/${evidence.owner}/${evidence.repo}`;
  try {
    if (evidence.kind === "repo" || evidence.kind === "file") {
      const data = await api(repoPath);
      return {
        owner: data.owner?.login,
        fork: Boolean(data.fork),
        stars: data.stargazers_count || 0,
        forks: data.forks_count || 0,
        createdAt: data.created_at || "",
      };
    }
    if (evidence.kind === "pr") {
      const data = await api(`${repoPath}/pulls/${evidence.number}`);
      return { owner: evidence.owner, author: data.user?.login, merged: Boolean(data.merged_at), mergedAt: data.merged_at || "" };
    }
    if (evidence.kind === "commit") {
      const data = await api(`${repoPath}/commits/${evidence.sha}`);
      return { owner: evidence.owner, author: data.author?.login || "", date: data.commit?.author?.date || "" };
    }
    if (evidence.kind === "release") {
      const data = await api(`${repoPath}/releases/tags/${encodeURIComponent(evidence.tag)}`);
      return { owner: evidence.owner, author: data.author?.login || "", date: data.published_at || "" };
    }
  } catch (error) {
    return { error: error.message || "Couldn't check this on GitHub." };
  }
  return null;
}

// Check every claim of `subject`. `io` = { api(path) -> json, raw(owner, repo, branch, path) -> text | null }.
// `onProgress(checked)` is called after each claim so the page can fill in as answers arrive.
export async function checkClaims(subject, claims, io, onProgress = () => {}) {
  let budget = CHECK_BUDGET;
  const checked = [];
  for (const claim of claims) {
    const results = [];
    for (const evidence of claim.evidence) {
      if (evidence.kind !== "link" && budget <= 0) {
        results.push({ evidence, ok: false, level: 1, note: "Not checked: this profile has more links than one visit may check." });
        continue;
      }
      if (evidence.kind !== "link") budget -= 1;
      const fact = evidence.kind === "link" ? null : await factFor(evidence, io.api);
      results.push({ evidence, ...judgeEvidence(subject, evidence, fact) });
    }
    for (const link of claim.attestations) {
      let text = null;
      if (link.kind === "file" && budget > 0) {
        budget -= 1;
        text = await io.raw(link.owner, link.repo, link.branch, link.path).catch(() => null);
      }
      results.push({ evidence: link, vouch: true, ...judgeVouch(subject, claim, link, text) });
    }
    checked.push({ ...claim, results, level: claimLevel(results) });
    onProgress([...checked]);
  }
  return checked;
}

// ------------------------------------------------------------ vouching

// The vouch file someone commits to their own repo, at vouches/<subject>/<claim>.md.
export function vouchFileText({ subject, claim, voucher, today }) {
  return [
    "---",
    `vouch_for: ${subject}`,
    `claim: ${claim.slug}`,
    `claim_text: "${claim.title.replaceAll('"', "'")}"`,
    "relation: colleague",
    `by: ${voucher}`,
    `date: ${today}`,
    "---",
    `I worked with @${subject} on this. What I saw first-hand: `,
    "",
  ].join("\n");
}

export function vouchPath(subject, claim) {
  return `vouches/${subject.toLowerCase()}/${claim.slug}.md`;
}

// GitHub's "create new file" page in the voucher's own repo, prefilled. No token needed: the voucher
// commits it with their own account, which is what makes the vouch theirs.
export function vouchUrl({ subject, claim, voucher, repo, branch = "main", today }) {
  const filename = encodeURIComponent(vouchPath(subject, claim));
  const value = encodeURIComponent(vouchFileText({ subject, claim, voucher, today }));
  const message = encodeURIComponent(`Vouch for @${subject}: ${claim.title}`.slice(0, 120));
  return `https://github.com/${voucher}/${repo}/new/${branch}?filename=${filename}&value=${value}&message=${message}`;
}

// Where the vouch file will live once committed: the link the subject adds to `attested_by`.
export function vouchLink({ subject, claim, voucher, repo, branch = "main" }) {
  return `https://github.com/${voucher}/${repo}/blob/${branch}/${vouchPath(subject, claim)}`;
}

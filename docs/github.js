// Read-only GitHub access for Explore and Profiles. No token needed: public data only.
// API calls are cached in sessionStorage (10 min) to stay well inside the 60 requests/hour
// anonymous limit; file contents come from raw.githubusercontent.com, which has no API limit.

const API = "https://api.github.com";
const RAW = "https://raw.githubusercontent.com";
const CACHE_MS = 10 * 60 * 1000;
export const BRAIN_TOPIC = "cairn-brain";
const MAX_FILES = 300;

export class GitHubError extends Error {}

const LOGIN_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const REPO_RE = /^[A-Za-z0-9._-]{1,100}$/;

// "juw12", "@juw12", "github.com/juw12", "https://github.com/juw12/cairn/tree/main", "juw12/cairn"
export function parseGithubInput(text) {
  let value = String(text || "").trim();
  if (!value) return null;
  value = value.replace(/^https?:\/\//i, "").replace(/^(www\.)?github\.com\//i, "").replace(/^@/, "");
  value = value.split(/[?#]/)[0].replace(/\.git$/i, "").replace(/\/+$/, "");
  const [owner, repo] = value.split("/");
  if (!owner || !LOGIN_RE.test(owner)) return null;
  if (repo !== undefined && !REPO_RE.test(repo)) return null;
  return { owner, repo: repo || null };
}

async function getJson(url) {
  const key = `cairn.gh:${url}`;
  try {
    const cached = JSON.parse(sessionStorage.getItem(key) || "null");
    if (cached && Date.now() - cached.at < CACHE_MS) return cached.data;
  } catch {
    /* ignore a broken cache entry */
  }
  let response;
  try {
    response = await fetch(url, { headers: { Accept: "application/vnd.github+json" } });
  } catch {
    throw new GitHubError("Can't reach GitHub. Check your connection and try again.");
  }
  if (response.status === 404) throw new GitHubError("Not found on GitHub.");
  if (response.status === 403 || response.status === 429) {
    throw new GitHubError("GitHub's hourly limit for anonymous lookups was reached. Try again in a few minutes.");
  }
  if (!response.ok) throw new GitHubError(`GitHub answered ${response.status}.`);
  const data = await response.json();
  try {
    sessionStorage.setItem(key, JSON.stringify({ at: Date.now(), data }));
  } catch {
    /* storage full: skip caching */
  }
  return data;
}

function slimRepo(repo) {
  return {
    owner: repo.owner?.login || repo.full_name.split("/")[0],
    avatar: repo.owner?.avatar_url || "",
    name: repo.name,
    fullName: repo.full_name,
    description: repo.description || "",
    url: repo.html_url,
    branch: repo.default_branch || "main",
    stars: repo.stargazers_count || 0,
    pushedAt: repo.pushed_at || repo.updated_at || "",
  };
}

export async function fetchUser(login) {
  const user = await getJson(`${API}/users/${encodeURIComponent(login)}`);
  return {
    login: user.login,
    name: user.name || user.login,
    avatar: user.avatar_url,
    bio: user.bio || "",
    location: user.location || "",
    blog: user.blog || "",
    url: user.html_url,
    since: (user.created_at || "").slice(0, 4),
    type: user.type,
  };
}

export async function findBrains(login) {
  const q = encodeURIComponent(`user:${login} topic:${BRAIN_TOPIC}`);
  const data = await getJson(`${API}/search/repositories?q=${q}&sort=updated&per_page=20`);
  return (data.items || []).map(slimRepo);
}

export async function discoverBrains() {
  const q = encodeURIComponent(`topic:${BRAIN_TOPIC}`);
  const data = await getJson(`${API}/search/repositories?q=${q}&sort=updated&per_page=30`);
  return (data.items || []).map(slimRepo);
}

export async function fetchRepo(owner, repo) {
  return slimRepo(await getJson(`${API}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`));
}

// All Markdown files of a repo's brain folder: [{ path (relative to the brain), text, mtime }]
export async function loadBrainFiles(owner, repo, branch) {
  const tree = await getJson(`${API}/repos/${owner}/${repo}/git/trees/${encodeURIComponent(branch)}?recursive=1`);
  const paths = (tree.tree || []).filter((item) => item.type === "blob").map((item) => item.path);
  const root = ["brain/", ".brain/"].find((prefix) => paths.some((p) => p.startsWith(prefix)));
  if (!root) throw new GitHubError(`${owner}/${repo} has no brain/ folder yet.`);
  const wanted = paths.filter((p) => p.startsWith(root) && p.endsWith(".md")).slice(0, MAX_FILES);
  const files = await Promise.all(
    wanted.map(async (path) => {
      const response = await fetch(`${RAW}/${owner}/${repo}/${encodeURIComponent(branch)}/${path.split("/").map(encodeURIComponent).join("/")}`);
      if (!response.ok) return null;
      return { path: path.slice(root.length), text: await response.text(), mtime: 0 };
    }),
  );
  return files.filter(Boolean);
}

export function joinRequestUrl(owner, repo, from = "") {
  const title = "[Cairn] Request to join this brain";
  const body = [
    `Hi @${owner}! I'd like to join this brain on Cairn${from ? ` (I'm @${from})` : ""}.`,
    "",
    "**What I'd like to do together:** ",
    "",
    "**Why I'm a good fit:** ",
    "",
    "---",
    "_Sent from [Cairn](https://juw12.github.io/cairn/). To accept, add me as a collaborator on this repo._",
  ].join("\n");
  return `https://github.com/${owner}/${repo}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
}

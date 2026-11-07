// GitHub access for Explore, Profiles and signed-in editing.
// Signed out: public data only. API reads are cached in sessionStorage (10 min) to stay inside the
// 60 requests/hour anonymous limit, and file contents come from raw.githubusercontent.com.
// Signed in: the same reads go through authFetch (5,000 requests/hour), file contents come from the
// Git blobs API (content is addressed by its sha, so it is never stale), and writes use the
// Contents API with the file's current sha, so GitHub refuses to overwrite a newer version.

import { authFetch, session } from "./auth.js";

const API = "https://api.github.com";
const RAW = "https://raw.githubusercontent.com";
const CACHE_MS = 10 * 60 * 1000;
export const BRAIN_TOPIC = "cairn-brain";
const MAX_FILES = 2000;

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
  // Cache per identity: a signed-in answer can include your permissions on a repo.
  const key = `cairn.gh:${session()?.login || ""}:${url}`;
  try {
    const cached = JSON.parse(sessionStorage.getItem(key) || "null");
    if (cached && Date.now() - cached.at < CACHE_MS) return cached.data;
  } catch {
    /* ignore a broken cache entry */
  }
  const response = await request(url);
  const data = await response.json();
  try {
    sessionStorage.setItem(key, JSON.stringify({ at: Date.now(), data }));
  } catch {
    /* storage full: skip caching */
  }
  return data;
}

// One place that turns HTTP failures into sentences a person can act on.
async function request(url, options = {}) {
  let response;
  try {
    response = await authFetch(url, options);
  } catch (error) {
    throw new GitHubError(error.message || "Can't reach GitHub. Check your connection and try again.");
  }
  if (response.ok) return response;
  if (response.status === 404) throw new GitHubError("Not found on GitHub.");
  if (response.status === 403 || response.status === 429) {
    const limited = response.headers.get("x-ratelimit-remaining") === "0" || response.status === 429;
    if (limited) {
      throw new GitHubError(
        session()
          ? "GitHub's hourly limit was reached. Wait a few minutes; nothing you typed is lost."
          : "GitHub's hourly limit for anonymous lookups was reached. Sign in or try again in a few minutes.",
      );
    }
    throw new GitHubError("GitHub refused that. Your sign-in may not have permission to change this repo.");
  }
  if (response.status === 409 || response.status === 422) {
    const error = new GitHubError("This changed on GitHub since you opened it.");
    error.conflict = true;
    throw error;
  }
  throw new GitHubError(`GitHub answered ${response.status}. Try again in a moment.`);
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
    topics: repo.topics || [],
    isPrivate: Boolean(repo.private),
    // Only present on signed-in requests; true when you may commit to this repo.
    canPush: repo.permissions?.push === true,
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

// Any public API read, cached like the rest: apiGet("/repos/juw12/cairn/pulls/3").
export function apiGet(path) {
  return getJson(`${API}${path}`);
}

// One public file's text, or null if it doesn't exist (used for vouch files in other people's repos).
export async function rawText(owner, repo, branch, path) {
  const response = await fetch(`${RAW}/${owner}/${repo}/${encodeURIComponent(branch)}/${encodePath(path)}`);
  return response.ok ? response.text() : null;
}

export async function fetchRepo(owner, repo) {
  return slimRepo(await getJson(`${API}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`));
}

// ------------------------------------------------------------ text <-> base64 (UTF-8 safe)

export function encodeBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export function decodeBase64(data) {
  const binary = atob(String(data || "").replace(/\s/g, ""));
  return new TextDecoder().decode(Uint8Array.from(binary, (c) => c.charCodeAt(0)));
}

const encodePath = (path) => path.split("/").map(encodeURIComponent).join("/");

// All Markdown files of a repo's brain folder:
//   { root: "brain/", files: [{ path (relative to the brain), text, mtime, sha }] }
export async function loadBrainFiles(owner, repo, branch) {
  // Never cached: after an edit the tree must show the new shas.
  const treeUrl = `${API}/repos/${owner}/${repo}/git/trees/${encodeURIComponent(branch)}?recursive=1`;
  const tree = await (await request(treeUrl, { cache: "no-store" })).json();
  const blobs = (tree.tree || []).filter((item) => item.type === "blob");
  const paths = blobs.map((item) => item.path);
  let root = ["brain/", ".brain/"].find((prefix) => paths.some((p) => p.startsWith(prefix)));
  // An Obsidian vault (or any repo of notes) is opened as it is: the repo root is the brain.
  if (!root && paths.some((p) => p.startsWith(".obsidian/") || p.endsWith(".md"))) root = "";
  if (root === undefined) throw new GitHubError(`${owner}/${repo} has no brain/ folder or notes yet.`);
  const hidden = (path) => path.split("/").some((part) => part.startsWith(".") && part !== ".brain");
  const wanted = blobs
    .filter((item) => item.path.startsWith(root) && item.path.endsWith(".md") && !hidden(item.path))
    .slice(0, MAX_FILES);
  const signedIn = Boolean(session());
  const files = await mapLimited(wanted, 8, async (item) => {
    let text;
    if (signedIn) {
      const blob = await (await request(`${API}/repos/${owner}/${repo}/git/blobs/${item.sha}`)).json();
      text = decodeBase64(blob.content);
    } else {
      const response = await fetch(`${RAW}/${owner}/${repo}/${encodeURIComponent(branch)}/${encodePath(item.path)}`);
      if (!response.ok) return null;
      text = await response.text();
    }
    return { path: item.path.slice(root.length), text, mtime: 0, sha: item.sha || null };
  });
  return { root, files: files.filter(Boolean) };
}

// Run `task` over `items` with at most `limit` requests in flight (a vault has hundreds of notes).
async function mapLimited(items, limit, task) {
  const results = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next++;
      results[index] = await task(items[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

// ------------------------------------------------------------ writes (signed in only)

// Latest text and sha of one file, or null if it doesn't exist.
export async function readFile(repo, fullPath) {
  try {
    const url = `${API}/repos/${repo.owner}/${repo.name}/contents/${encodePath(fullPath)}?ref=${encodeURIComponent(repo.branch)}`;
    const data = await (await request(url, { cache: "no-store" })).json();
    return { text: decodeBase64(data.content), sha: data.sha };
  } catch (error) {
    if (error.message === "Not found on GitHub.") return null;
    throw error;
  }
}

// Create (sha = null) or update (sha = the version you edited). Returns the new sha.
export async function writeFile(repo, fullPath, text, sha, message) {
  const body = { message, content: encodeBase64(text), branch: repo.branch };
  if (sha) body.sha = sha;
  const url = `${API}/repos/${repo.owner}/${repo.name}/contents/${encodePath(fullPath)}`;
  const data = await (await request(url, { method: "PUT", body: JSON.stringify(body) })).json();
  return data.content?.sha || null;
}

export async function deleteFile(repo, fullPath, sha, message) {
  const url = `${API}/repos/${repo.owner}/${repo.name}/contents/${encodePath(fullPath)}`;
  await request(url, { method: "DELETE", body: JSON.stringify({ message, sha, branch: repo.branch }) });
}

// Brains you can edit: your repos (and ones you collaborate on) with the cairn-brain topic.
export async function myBrains() {
  const url = `${API}/user/repos?affiliation=owner,collaborator&sort=pushed&per_page=100`;
  const repos = await (await request(url, { cache: "no-store" })).json();
  return repos.filter((repo) => (repo.topics || []).includes(BRAIN_TOPIC)).map(slimRepo);
}

const STARTER = (owner, today) => [
  [
    "brain/README.md",
    `# ${owner}'s brain\n\nPlain Markdown kept by [Cairn](https://juw12.github.io/cairn/). Ideas, tasks, decisions and notes live in the folders next to this file.\n`,
  ],
  [
    "brain/ideas/what-should-this-brain-be-for.md",
    `---\ntitle: What should this brain be for?\ntype: idea\nstatus: raw\ntags: [start]\ncreated: ${today}\n---\nWrite one sentence about the project or question this brain is for.\n`,
  ],
  [
    "brain/tasks/capture-three-ideas.md",
    `---\ntitle: Capture three ideas\ntype: task\nstatus: todo\ntags: [start]\ncreated: ${today}\n---\n- [ ] One from today\n- [ ] One you keep coming back to\n- [ ] One that scares you a little\n`,
  ],
];

// A new public repo with the cairn-brain topic and a small starter brain. Returns the slim repo.
export async function createBrain(name, description, today) {
  const created = await (
    await request(`${API}/user/repos`, {
      method: "POST",
      body: JSON.stringify({ name, description, private: false, auto_init: true, has_wiki: false }),
    })
  ).json();
  const repo = slimRepo(created);
  await request(`${API}/repos/${repo.owner}/${repo.name}/topics`, {
    method: "PUT",
    body: JSON.stringify({ names: [BRAIN_TOPIC] }),
  });
  for (const [path, text] of STARTER(repo.owner, today)) {
    await writeFile(repo, path, text, null, `cairn: start ${path}`);
  }
  return { ...repo, canPush: true, topics: [BRAIN_TOPIC] };
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

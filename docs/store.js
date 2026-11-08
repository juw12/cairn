// Storage adapters. Every adapter speaks the same small interface:
//   info()                       -> { mode, project, label, canGit, canScan, readOnly?, saveDelay? }
//   list()                       -> [{ path, text, mtime }]
//   write(path, text, mtime)     -> { mtime }   (mtime null = create; throws ConflictError)
//   remove(path)
//   todos()                      -> [{ path, line, tag, text }]
//   gitStatus() / gitCommit(msg) / gitSync() -> server only
//   desktop() / desktopAction(action, payload) -> the desktop app only (info.desktop)
//   vibe(since, base)            -> server only: session/feedback files + live git activity

import { addDays, addMonths, dayOf } from "./daylog.js";
import { isBrainFile, scanTodos } from "./core.js";
import { DEMO_FILES, DEMO_TODOS } from "./demo.js";
import * as gh from "./github.js";
import * as auth from "./auth.js";

export class ConflictError extends Error {
  constructor(text, mtime) {
    super("This note changed somewhere else since you opened it.");
    this.text = text;
    this.mtime = mtime;
  }
}

// ------------------------------------------------------------ local Python server

export class ServerStore {
  constructor(info) {
    this.meta = info;
  }

  static async detect() {
    // The local Python server marks the page it serves; static hosting (GitHub Pages) never probes.
    if (!document.querySelector('meta[name="cairn-server"]')) return null;
    try {
      const response = await fetch("api/info", { headers: { "X-Cairn": "1" }, cache: "no-store" });
      if (!response.ok) return null;
      const info = await response.json();
      return info.mode === "server" ? new ServerStore(info) : null;
    } catch {
      return null;
    }
  }

  async request(method, url, body) {
    const response = await fetch(url, {
      method,
      headers: { "X-Cairn": "1", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 409) throw new ConflictError(data.text ?? null, data.mtime ?? 0);
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  async info() {
    return {
      mode: "server",
      project: this.meta.project,
      label: `Local · ${this.meta.brainDir}/`,
      canGit: Boolean(this.meta.git),
      canScan: true,
      desktop: Boolean(this.meta.desktop),
      path: this.meta.path || null,
    };
  }

  async list() {
    if (!this.meta.exists) {
      await this.request("POST", "api/init", {});
      this.meta.exists = true;
    }
    return (await this.request("GET", "api/files")).files;
  }

  async write(path, text, mtime) {
    return this.request("PUT", `api/files?path=${encodeURIComponent(path)}`, { text, mtime });
  }

  async remove(path) {
    await this.request("DELETE", `api/files?path=${encodeURIComponent(path)}`);
  }

  async todos() {
    return (await this.request("GET", "api/todos")).items;
  }

  async gitStatus() {
    return this.request("GET", "api/git");
  }

  async gitCommit(message) {
    return this.request("POST", "api/git/commit", { message });
  }

  async gitSync(message = "") {
    return this.request("POST", "api/git/sync", { message });
  }

  async desktop() {
    return this.request("GET", "api/desktop");
  }

  // Session and feedback files plus live git activity since `since`, polled by the Vibe view.
  async vibe(since = "", base = "") {
    return this.request("GET", `api/vibe?since=${encodeURIComponent(since)}&base=${encodeURIComponent(base)}`);
  }

  // Images and other attachments are served by the local server, which finds them the way
  // Obsidian does (next to the note, from the brain root, or anywhere by file name).
  async assetUrls(target, from = "") {
    return [`api/raw?path=${encodeURIComponent(target)}&from=${encodeURIComponent(from)}`];
  }

  async desktopAction(action, payload = {}) {
    return this.request("POST", "api/desktop", { ...payload, action });
  }
}

// ------------------------------------------------------------ browser-only: a real folder on disk

const SKIP_DIRS = new Set([
  ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target",
  ".next", ".idea", ".vscode", ".obsidian", "vendor", "coverage",
]);
const CODE_EXT = /\.(py|m?js|cjs|tsx?|jsx|java|kt|go|rs|c|h|cpp|hpp|cs|rb|php|swift|sh|ps1|lua|sql|html|css|scss|vue|svelte|ya?ml|toml|f90|dart)$/i;

export function idb(action) {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("cairn", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("handles");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const tx = open.result.transaction("handles", "readwrite");
      const request = action(tx.objectStore("handles"));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    };
  });
}

export class FolderStore {
  constructor(root) {
    this.root = root;
    this.brain = null;
  }

  static supported() {
    return typeof window.showDirectoryPicker === "function";
  }

  static async pick() {
    const root = await window.showDirectoryPicker({ id: "cairn-repo", mode: "readwrite" });
    await idb((store) => store.put(root, "root"));
    return new FolderStore(root);
  }

  static async remembered() {
    if (!FolderStore.supported()) return null;
    try {
      const root = await idb((store) => store.get("root"));
      return root ? new FolderStore(root) : null;
    } catch {
      return null;
    }
  }

  async ensurePermission() {
    const options = { mode: "readwrite" };
    if ((await this.root.queryPermission(options)) === "granted") return true;
    return (await this.root.requestPermission(options)) === "granted";
  }

  async brainDir() {
    if (!this.brain) {
      // Opening the brain folder itself also works.
      const names = [];
      for await (const [name] of this.root.entries()) names.push(name);
      // So does an Obsidian vault: the whole vault is the brain.
      const isVault = names.includes(".obsidian") && !names.includes("brain");
      const isBrain = isVault || (["ideas", "tasks", "decisions"].some((n) => names.includes(n)) && !names.includes("brain"));
      this.brain = isBrain ? this.root : await this.root.getDirectoryHandle("brain", { create: true });
    }
    return this.brain;
  }

  async info() {
    const brain = await this.brainDir();
    return {
      mode: "folder",
      project: this.root.name,
      label: `Folder · ${brain === this.root ? this.root.name : "brain"}/`,
      canGit: false,
      canScan: true,
      // Opening brain/ itself leaves no code around it to scan; say so instead of "inbox zero".
      emptyScanHint: brain === this.root ? "You opened the brain folder itself. Open the repo root instead to scan its code." : "",
    };
  }

  async fileHandle(path, create = false) {
    let dir = await this.brainDir();
    const parts = path.split("/");
    const name = parts.pop();
    for (const part of parts) dir = await dir.getDirectoryHandle(part, { create });
    return dir.getFileHandle(name, { create });
  }

  async list() {
    const out = [];
    const walk = async (dir, prefix) => {
      for await (const [name, handle] of dir.entries()) {
        if (handle.kind === "directory") {
          if (!name.startsWith(".") && !SKIP_DIRS.has(name)) await walk(handle, `${prefix}${name}/`);
        } else if (isBrainFile(name)) {
          const file = await handle.getFile();
          out.push({ path: prefix + name, text: await file.text(), mtime: file.lastModified });
        }
      }
    };
    await walk(await this.brainDir(), "");
    return out.sort((a, b) => (a.path < b.path ? -1 : 1));
  }

  // Session and feedback files, re-read every few seconds by the Vibe view. Enumerating two small folders is
  // the fallback Chrome recommends when a FileSystemObserver isn't available. No git from a browser.
  async vibe() {
    const files = [];
    const brain = await this.brainDir();
    for (const folder of ["sessions", "feedback"]) {
      let dir;
      try {
        dir = await brain.getDirectoryHandle(folder);
      } catch {
        continue;
      }
      for await (const [name, handle] of dir.entries()) {
        if (handle.kind !== "file" || !name.endsWith(".md")) continue;
        const file = await handle.getFile();
        files.push({ path: `${folder}/${name}`, text: await file.text(), mtime: file.lastModified });
      }
    }
    return { files, git: { available: false, browser: true } };
  }

  async write(path, text, mtime) {
    let existing = null;
    try {
      existing = await (await this.fileHandle(path)).getFile();
    } catch {
      existing = null;
    }
    if (existing && (mtime === null || mtime === undefined || existing.lastModified !== mtime)) {
      throw new ConflictError(await existing.text(), existing.lastModified);
    }
    const handle = await this.fileHandle(path, true);
    const writable = await handle.createWritable();
    await writable.write(text);
    await writable.close();
    return { mtime: (await handle.getFile()).lastModified };
  }

  async assetUrls(target, from = "") {
    const name = target.trim().replaceAll("\\", "/").replace(/^\.\//, "");
    const folder = from.includes("/") ? from.slice(0, from.lastIndexOf("/")) : "";
    const tryPath = async (path) => {
      try {
        const file = await (await this.fileHandle(path)).getFile();
        return URL.createObjectURL(file);
      } catch {
        return null;
      }
    };
    for (const option of [folder ? `${folder}/${name}` : null, name]) {
      if (!option || option.split("/").includes("..")) continue;
      const url = await tryPath(option);
      if (url) return [url];
    }
    // Anywhere in the brain by file name, shallowest first.
    const base = name.split("/").pop().toLowerCase();
    let best = null;
    const walk = async (dir, prefix, depth) => {
      for await (const [entry, handle] of dir.entries()) {
        if (handle.kind === "directory") {
          if (!entry.startsWith(".") && !SKIP_DIRS.has(entry) && depth < 8) await walk(handle, `${prefix}${entry}/`, depth + 1);
        } else if (entry.toLowerCase() === base && (!best || best.depth > depth)) {
          best = { handle, depth };
        }
      }
    };
    await walk(await this.brainDir(), "", 0);
    return best ? [URL.createObjectURL(await best.handle.getFile())] : [];
  }

  async remove(path) {
    let dir = await this.brainDir();
    const parts = path.split("/");
    const name = parts.pop();
    for (const part of parts) dir = await dir.getDirectoryHandle(part);
    await dir.removeEntry(name);
  }

  async todos() {
    const items = [];
    const brain = await this.brainDir();
    const walk = async (dir, prefix) => {
      for await (const [name, handle] of dir.entries()) {
        if (items.length >= 500) return;
        if (handle.kind === "directory") {
          if (!name.startsWith(".") && !SKIP_DIRS.has(name) && !(await handle.isSameEntry(brain))) {
            await walk(handle, `${prefix}${name}/`);
          }
        } else if (CODE_EXT.test(name)) {
          const file = await handle.getFile();
          if (file.size <= 1_000_000) items.push(...scanTodos(await file.text(), prefix + name));
        }
      }
    };
    if (brain !== this.root) await walk(this.root, "");
    return items.slice(0, 500);
  }
}

// ------------------------------------------------------------ demo: sample brain in localStorage

const DEMO_KEY = "cairn.demo.v1";

// Recent journal days for the demo, dated relative to today so the streak, heatmap, mood chart and
// look-backs (a week / month / year ago) have something to show. Plain "## Log" pages like any other.
const DEMO_DAYS = [
  [-1, ["09:12 Standup: GPX import is the last beta blocker #beta", "13:40 Paired with @sam on the segment bug, found the timezone issue", "Mood: good (4/5)", "18:05 Task: [[gpx-import|GPX import]] is close, tests pass locally"]],
  [-2, ["10:02 Five beta hikers confirmed for the weekend test, thanks @ana #beta", "15:30 Weather overlay looks great on the elevation profile", "Mood: great (5/5)"]],
  [-3, ["08:50 Read the interview notes again, offline is the #1 ask #research", "16:20 Decided to keep the map corridor at 2 km", "Mood: okay (3/5)"]],
  [-5, ["11:15 Tile sizes are fine: 40 km route is 26 MB #maps", "Mood: good (4/5)"]],
  [-7, ["09:30 Kicked off the beta plan with @sam and @ana #beta", "14:00 Idea: share a live trip link with family", "Mood: good (4/5)"]],
  [-10, ["17:45 Long day, the PMTiles build kept failing #maps", "Mood: low (2/5)"]],
  [-14, ["10:10 First hike test on the Ridge trail, map held up offline", "Mood: great (5/5)"]],
  ["month", ["12:00 Wrote the first [[user-interviews]] summary #research", "Mood: good (4/5)"]],
  ["year", ["19:30 Sketched the first idea for an offline hiking app", "Mood: great (5/5)"]],
];

// The sample brain's one dated journal page moves to a day the generated pages never use, so the
// demo has the same notes (and the same count) whatever today's date is.
const FIXED_JOURNAL_OFFSET = -4;

function redateJournal(path, text) {
  const match = /^journal\/(\d{4}-\d{2}-\d{2})\.md$/.exec(path);
  if (!match) return [path, text];
  const day = addDays(dayOf(), FIXED_JOURNAL_OFFSET);
  return [`journal/${day}.md`, text.split(match[1]).join(day)];
}

function demoJournalDays() {
  const files = {};
  const base = dayOf();
  for (const [offset, lines] of DEMO_DAYS) {
    const day = offset === "month" ? addMonths(base, -1) : offset === "year" ? addMonths(base, -12) : addDays(base, offset);
    const body = lines.map((line) => (/^\d{2}:\d{2} /.test(line) ? `- ${line}` : `- 20:00 ${line}`)).join("\n");
    files[`journal/${day}.md`] = `---\ntitle: ${day}\ntype: journal\ncreated: ${day}\n---\n## Log\n\n${body}\n`;
  }
  return files;
}

export class DemoStore {
  constructor() {
    this.files = this.load();
  }

  load() {
    try {
      const saved = JSON.parse(localStorage.getItem(DEMO_KEY) || "null");
      if (saved && typeof saved === "object") return saved;
    } catch {
      /* fall through to the sample */
    }
    return DemoStore.sample();
  }

  static sample() {
    const files = {};
    let stamp = Date.now() - DEMO_FILES.length * 60_000;
    for (const file of DEMO_FILES) {
      stamp += 60_000;
      const [path, text] = redateJournal(file.path, file.text);
      files[path] = { text, mtime: stamp };
    }
    for (const [path, text] of Object.entries(demoJournalDays())) {
      if (!files[path]) files[path] = { text, mtime: stamp - 3_600_000 };
    }
    return files;
  }

  static empty() {
    return {};
  }

  reset(files) {
    this.files = files;
    this.save();
  }

  save() {
    localStorage.setItem(DEMO_KEY, JSON.stringify(this.files));
  }

  async info() {
    return { mode: "demo", project: "cairn-demo", label: "Demo · saved in this browser", canGit: false, canScan: true, canLoadSample: true };
  }

  async list() {
    return Object.entries(this.files)
      .map(([path, file]) => ({ path, text: file.text, mtime: file.mtime }))
      .sort((a, b) => (a.path < b.path ? -1 : 1));
  }

  async write(path, text, mtime) {
    const existing = this.files[path];
    if (existing && existing.mtime !== mtime) throw new ConflictError(existing.text, existing.mtime);
    const next = Math.max(Date.now(), (existing?.mtime || 0) + 1);
    this.files[path] = { text, mtime: next };
    this.save();
    return { mtime: next };
  }

  async remove(path) {
    delete this.files[path];
    this.save();
  }

  async todos() {
    return DEMO_TODOS;
  }

  async assetUrls() {
    return [];
  }
}

// ------------------------------------------------------------ someone's public brain on GitHub (read-only)

export class GitHubStore {
  // repo: slim repo from github.js. loaded: { root: "brain/", files: [{ path, text, mtime, sha }] }.
  // Writable only when you are signed in AND GitHub says you may push to the repo.
  constructor(repo, loaded, { writable = false } = {}) {
    this.repo = repo;
    this.root = loaded.root ?? "brain/";
    this.files = loaded.files || [];
    this.writable = Boolean(writable);
    // The sha of the version we last saw for each file: GitHub refuses a save based on an older one.
    this.shas = new Map(this.files.map((file) => [file.path, file.sha || null]));
  }

  async info() {
    const { owner, name, url, avatar, branch } = this.repo;
    return {
      mode: "github",
      project: name,
      label: this.writable ? `${owner}/${name} · saves to GitHub` : `@${owner} · read-only`,
      canGit: false,
      canScan: false,
      readOnly: !this.writable,
      owner,
      repoName: name,
      url,
      avatar,
      branch,
      // Every save is a commit, so wait for a real pause in typing before saving.
      saveDelay: this.writable ? 1500 : 450,
      savedLabel: "Saved to GitHub",
    };
  }

  async list() {
    return this.files.map(({ path, text, mtime }) => ({ path, text, mtime }));
  }

  // Signed in, the Vibe view polls the brain folder with a conditional request (304s cost nothing against the
  // rate limit). Only when the sha of sessions/ or feedback/ changes are they listed, and only files whose
  // sha changed are fetched, the way git itself compares trees. Signed out there's no live polling.
  get vibePollMs() {
    return 20000;
  }

  async vibe() {
    if (!auth.session()) return { files: [], git: { available: false, github: true, signedOut: true } };
    this.folderShas ||= new Map();
    const files = [];
    const top = await gh.listFolderIfChanged(this.repo, this.root.replace(/\/$/, ""), this.rootEtag || "");
    if (!top) return { files, git: { available: false, github: true } };
    this.rootEtag = top.etag;
    for (const folder of ["sessions", "feedback"]) {
      const dir = top.items.find((item) => item.type === "dir" && item.name === folder);
      if (!dir || this.folderShas.get(folder) === dir.sha) continue;
      const listing = await gh.listFolderIfChanged(this.repo, dir.path);
      if (!listing) continue;
      this.folderShas.set(folder, dir.sha);
      for (const item of listing.items.filter((entry) => entry.type === "file" && entry.name.endsWith(".md"))) {
        const path = item.path.slice(this.root.length);
        if (this.shas.get(path) === item.sha) continue;
        const latest = await gh.readFile(this.repo, item.path);
        if (!latest) continue;
        this.shas.set(path, latest.sha);
        files.push({ path, text: latest.text, mtime: Date.now() });
      }
    }
    return { files, git: { available: false, github: true } };
  }

  // Public repos serve files from raw.githubusercontent.com; private ones need a token, so those
  // attachments show as a chip instead.
  async assetUrls(target, from = "") {
    const { owner, name, branch } = this.repo;
    const clean = target.trim().replaceAll("\\", "/").replace(/^\.\//, "");
    const folder = from.includes("/") ? from.slice(0, from.lastIndexOf("/")) : "";
    const options = [...new Set([folder ? `${folder}/${clean}` : null, clean].filter(Boolean))];
    return options
      .filter((path) => !path.split("/").includes(".."))
      .map((path) => `https://raw.githubusercontent.com/${owner}/${name}/${encodeURIComponent(branch || "main")}/${this.root}${path}`.replace(/ /g, "%20"));
  }

  assertWritable() {
    if (!this.writable) throw new Error("This brain is read-only. Ask to join to contribute.");
  }

  async write(path, text) {
    this.assertWritable();
    const fullPath = this.root + path;
    const sha = this.shas.get(path) || null;
    try {
      const next = await gh.writeFile(this.repo, fullPath, text, sha, `cairn: ${sha ? "update" : "add"} ${path}`);
      this.shas.set(path, next);
      return { mtime: Date.now() };
    } catch (error) {
      if (!error.conflict) throw error;
      // Someone (or another device) changed it first. Show their version; the app keeps ours.
      const latest = await gh.readFile(this.repo, fullPath);
      if (latest) {
        this.shas.set(path, latest.sha);
        throw new ConflictError(latest.text, Date.now());
      }
      // It was deleted on GitHub meanwhile: saving recreates it.
      const next = await gh.writeFile(this.repo, fullPath, text, null, `cairn: add ${path}`);
      this.shas.set(path, next);
      return { mtime: Date.now() };
    }
  }

  async remove(path) {
    this.assertWritable();
    const sha = this.shas.get(path);
    if (!sha) return;
    try {
      await gh.deleteFile(this.repo, this.root + path, sha, `cairn: delete ${path}`);
    } catch (error) {
      if (!error.conflict) throw error;
      throw new Error("This note changed on GitHub since you opened it, so it wasn't deleted. Reload to see the newer version.");
    }
    this.shas.delete(path);
  }

  async todos() {
    return [];
  }
}

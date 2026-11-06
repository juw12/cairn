// Storage adapters. Every adapter speaks the same small interface:
//   info()                       -> { mode, project, label, canGit, canScan, readOnly?, saveDelay? }
//   list()                       -> [{ path, text, mtime }]
//   write(path, text, mtime)     -> { mtime }   (mtime null = create; throws ConflictError)
//   remove(path)
//   todos()                      -> [{ path, line, tag, text }]
//   gitStatus() / gitCommit(msg) -> server only

import { scanTodos } from "./core.js";
import { DEMO_FILES, DEMO_TODOS } from "./demo.js";
import * as gh from "./github.js";

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
}

// ------------------------------------------------------------ browser-only: a real folder on disk

const SKIP_DIRS = new Set([
  ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target",
  ".next", ".idea", ".vscode", ".obsidian", "vendor", "coverage",
]);
const CODE_EXT = /\.(py|m?js|cjs|tsx?|jsx|java|kt|go|rs|c|h|cpp|hpp|cs|rb|php|swift|sh|ps1|lua|sql|html|css|scss|vue|svelte|ya?ml|toml|f90|dart)$/i;

function idb(action) {
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
      const isBrain = ["ideas", "tasks", "decisions"].some((n) => names.includes(n)) && !names.includes("brain");
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
        } else if (name.endsWith(".md")) {
          const file = await handle.getFile();
          out.push({ path: prefix + name, text: await file.text(), mtime: file.lastModified });
        }
      }
    };
    await walk(await this.brainDir(), "");
    return out.sort((a, b) => (a.path < b.path ? -1 : 1));
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
      files[file.path] = { text: file.text, mtime: stamp };
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
}

// ------------------------------------------------------------ someone's public brain on GitHub (read-only)

export class GitHubStore {
  // repo: slim repo from github.js. loaded: { root: "brain/", files: [{ path, text, mtime, sha }] }.
  // Writable only when you are signed in AND GitHub says you may push to the repo.
  constructor(repo, loaded, { writable = false } = {}) {
    this.repo = repo;
    this.root = loaded.root || "brain/";
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

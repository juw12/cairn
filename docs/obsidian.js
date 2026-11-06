// Obsidian: import a vault and keep it in sync both ways.
//
// The same Markdown files live in both places; nothing is converted. Three ways in, picked by what the
// runtime can do:
//   server  - the local Python app syncs a vault path on disk (cairn/obsidian.py). Works in every browser.
//   handle  - Chrome/Edge: the File System Access API gives read/write access to the vault folder.
//   import  - any other browser: pick the vault folder once; notes are copied in (one way). Import again
//             later to bring in what changed.
// The rules for who wins are core.planSync, the same as core.plan_sync in Python.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { $, ask, icon, pageHead, plural, relativeTime, toast } from "./ui.js";
import { fileByPath, loadAll, noteByPath, readOnly, state, upsertFile } from "./state.js";
import { idb } from "./store.js";
import { flushPending, renderDrawer } from "./drawer.js";
import { render, renderNav, renderView } from "./app.js";

const AUTO_SYNC_MS = 20_000;
const DEFAULT_FOLDER = "obsidian";
const SKIP_DIRS = new Set(["node_modules", ".git", ".obsidian", ".trash"]);

let timer = null;
let lastAuto = 0;
let vaultHandle = null;
const readCache = new Map(); // vault path -> { stamp, hash, text }: unchanged files are not re-read

// ============================================================ modes and saved state

function mode() {
  if (!state.info || readOnly() || state.info.mode === "visitor") return "none";
  if (state.info.mode === "server") return "server";
  return typeof window.showDirectoryPicker === "function" ? "handle" : "import";
}

function localKey() {
  return `cairn.obsidian.${state.info.mode}.${state.info.owner || ""}/${state.info.project}`;
}

function loadLocal() {
  try {
    const saved = JSON.parse(localStorage.getItem(localKey()) || "null");
    return saved && typeof saved === "object" ? { manifest: {}, ...saved } : null;
  } catch {
    return null;
  }
}

function saveLocal(saved) {
  if (saved) localStorage.setItem(localKey(), JSON.stringify(saved));
  else localStorage.removeItem(localKey());
}

function autoOn() {
  return localStorage.getItem("cairn.obsidian.auto") !== "off";
}

function cleanFolder(raw) {
  const parts = String(raw ?? DEFAULT_FOLDER).replaceAll("\\", "/").split("/").filter((p) => p && p !== ".");
  if (parts.some((p) => p === ".." || p.startsWith("."))) throw new Error("Use a plain folder name for the brain side, for example “obsidian”.");
  return parts.join("/");
}

function setStatus(patch) {
  state.obsidian = { ...(state.obsidian || {}), ...patch };
}

export async function refreshObsidian() {
  const kind = mode();
  if (kind === "none") return setStatus({ status: null });
  if (kind === "server") {
    try {
      const status = await state.store.request("GET", "api/obsidian");
      setStatus({ status: { ...status, kind: "server" }, error: "" });
    } catch (error) {
      setStatus({ status: null, error: error.message });
    }
    return;
  }
  const saved = loadLocal();
  if (saved?.kind === "handle" && !vaultHandle) {
    try {
      vaultHandle = (await idb((store) => store.get(localKey()))) || null;
    } catch {
      vaultHandle = null;
    }
  }
  setStatus({
    status: saved
      ? {
          linked: true,
          kind: saved.kind,
          vault: saved.vaultName,
          vaultName: saved.vaultName,
          folder: saved.folder,
          synced: Object.keys(saved.manifest || {}).length,
          lastSync: saved.lastSync || "",
          last: saved.last || null,
          vaultFound: saved.kind === "import" || Boolean(vaultHandle),
        }
      : { linked: false, kind },
  });
}

// ============================================================ hashing and the vault side (browser)

function hashText(text) {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return `${(h2 >>> 0).toString(16)}${(h1 >>> 0).toString(16)}:${text.length}`;
}

const normalize = (text) => text.replaceAll("\r\n", "\n");
const skipName = (name) => name.startsWith(".") || SKIP_DIRS.has(name);

function handleVault(root) {
  const dirFor = async (path, create) => {
    let dir = root;
    const parts = path.split("/");
    const name = parts.pop();
    for (const part of parts) dir = await dir.getDirectoryHandle(part, { create });
    return { dir, name };
  };
  const vault = {
    name: root.name,
    async list() {
      const files = {};
      const walk = async (dir, prefix) => {
        for await (const [name, handle] of dir.entries()) {
          if (handle.kind === "directory") {
            if (!skipName(name)) await walk(handle, `${prefix}${name}/`);
          } else if (name.toLowerCase().endsWith(".md")) {
            const path = prefix + name;
            const file = await handle.getFile();
            const stamp = `${file.lastModified}:${file.size}`;
            let cached = readCache.get(path);
            if (!cached || cached.stamp !== stamp) {
              const text = normalize(await file.text());
              cached = { stamp, text, hash: hashText(text) };
              readCache.set(path, cached);
            }
            files[path] = { hash: cached.hash, mtime: file.lastModified, text: cached.text };
          }
        }
      };
      await walk(root, "");
      return files;
    },
    async write(path, text) {
      const { dir, name } = await dirFor(path, true);
      const writable = await (await dir.getFileHandle(name, { create: true })).createWritable();
      await writable.write(text);
      await writable.close();
      readCache.delete(path);
    },
    async trash(path) {
      // Obsidian's own trash folder, so a delete made in Cairn can be undone from Obsidian.
      const { dir, name } = await dirFor(path, false);
      const text = await (await (await dir.getFileHandle(name)).getFile()).text();
      await vault.write(`.trash/${path}`, text);
      await dir.removeEntry(name);
      readCache.delete(path);
    },
  };
  return vault;
}

async function snapshotVault(fileList) {
  const files = {};
  let name = "vault";
  for (const file of fileList) {
    const parts = (file.webkitRelativePath || file.name).split("/");
    if (parts.length > 1) name = parts.shift();
    if (!file.name.toLowerCase().endsWith(".md") || parts.slice(0, -1).some(skipName)) continue;
    const text = normalize(await file.text());
    files[parts.join("/")] = { hash: hashText(text), mtime: file.lastModified, text };
  }
  return { name, list: async () => files };
}

function conflictName(path, taken, day) {
  const stem = path.slice(0, -3);
  let candidate = `${stem} (conflict ${day}).md`;
  for (let n = 2; taken.has(candidate); n++) candidate = `${stem} (conflict ${day} ${n}).md`;
  return candidate;
}

// ============================================================ the browser sync

async function browserSync(vault, saved, { pullOnly = false, auto = false } = {}) {
  await flushPending();
  const prefix = saved.folder ? `${saved.folder}/` : "";
  const manifest = saved.manifest || {};
  const vaultFiles = await vault.list();
  const brainFiles = {};
  for (const file of state.files) {
    if (!file.path.startsWith(prefix) || !file.path.toLowerCase().endsWith(".md")) continue;
    const text = normalize(file.text);
    brainFiles[file.path.slice(prefix.length)] = { hash: hashText(text), mtime: file.mtime || 0, text };
  }
  if (!prefix) {
    // Whole-brain mode: brain/README.md and the generated CONTEXT.md belong to Cairn, not the vault.
    for (const name of core.SPECIAL_FILES) {
      delete vaultFiles[name];
      delete brainFiles[name];
    }
  }
  const hasManifest = Object.keys(manifest).length > 0;
  if (hasManifest && !Object.keys(vaultFiles).length) throw new Error("The vault looks empty, so nothing was changed. Check you picked the right folder.");
  if (hasManifest && !Object.keys(brainFiles).length && !pullOnly) throw new Error(`brain/${prefix} looks empty, so nothing was changed. Unlink and link again to re-import.`);

  const meta = (files) => Object.fromEntries(Object.entries(files).map(([p, f]) => [p, { hash: f.hash, mtime: f.mtime }]));
  let plan = core.planSync(meta(vaultFiles), meta(brainFiles), manifest);
  const skipped = new Set();
  if (pullOnly) {
    // One-way import: never touch the vault, never delete notes in the brain.
    plan = plan
      .filter((step) => {
        const keep = step.op === "pull" || step.op === "conflict";
        if (!keep) skipped.add(step.path);
        return keep;
      })
      .map((step) => (step.op === "conflict" ? { ...step, keep: "brain" } : step));
  }
  const deletes = plan.filter((s) => s.op.startsWith("delete")).length;
  if (!auto && (deletes || plan.length > 40)) {
    const counts = ["pull", "push", "delete-brain", "delete-vault", "conflict"].map((op) => [op, plan.filter((s) => s.op === op).length]);
    const words = { pull: "from Obsidian", push: "to Obsidian", "delete-brain": "deleted here (trash)", "delete-vault": "deleted in the vault (.trash)", conflict: "conflicts (both kept)" };
    const ok = await ask({
      title: `Sync ${plural(plan.length, "change")}?`,
      text: state.info.mode === "github" ? "Each note written to GitHub is one commit." : "Nothing is deleted for good: deletes go to a trash folder.",
      list: counts.filter(([, n]) => n).map(([op, n]) => `${n} ${words[op]}`),
      confirm: "Sync",
      noInput: true,
    });
    if (!ok) return null;
  } else if (auto && deletes > 10) {
    throw new Error(`${deletes} notes would be deleted. Open Obsidian sync and press Sync now to confirm.`);
  }

  const finalVault = Object.fromEntries(Object.entries(vaultFiles).map(([p, f]) => [p, f.hash]));
  const finalBrain = Object.fromEntries(Object.entries(brainFiles).map(([p, f]) => [p, f.hash]));
  const taken = new Set([...Object.keys(finalVault), ...Object.keys(finalBrain)]);
  const report = { pulled: [], pushed: [], deletedInBrain: [], deletedInVault: [], conflicts: [] };
  const writeBrain = async (rel, text) => {
    const path = prefix + rel;
    const existing = fileByPath(path);
    const { mtime } = await state.store.write(path, text, existing ? existing.mtime : null);
    upsertFile(path, text, mtime);
  };
  let done = 0;
  for (const step of plan) {
    const { path, op } = step;
    const progress = $("#obsidian-progress");
    if (progress) progress.textContent = `Syncing ${++done} of ${plan.length}…`;
    if (op === "pull") {
      await writeBrain(path, vaultFiles[path].text);
      finalBrain[path] = finalVault[path];
      report.pulled.push(prefix + path);
    } else if (op === "push") {
      await vault.write(path, brainFiles[path].text);
      finalVault[path] = finalBrain[path];
      report.pushed.push(path);
    } else if (op === "delete-brain") {
      await state.store.remove(prefix + path);
      upsertFile(prefix + path, null);
      delete finalBrain[path];
      report.deletedInBrain.push(prefix + path);
    } else if (op === "delete-vault") {
      await vault.trash(path);
      delete finalVault[path];
      report.deletedInVault.push(path);
    } else if (op === "conflict") {
      const [winner, loser] = step.keep === "vault" ? [vaultFiles[path], brainFiles[path]] : [brainFiles[path], vaultFiles[path]];
      const copy = conflictName(path, taken, new Date().toISOString().slice(0, 10));
      taken.add(copy);
      if (!pullOnly) {
        await vault.write(path, winner.text);
        await vault.write(copy, loser.text);
        finalVault[path] = winner.hash;
        finalVault[copy] = loser.hash;
      }
      await writeBrain(path, winner.text);
      await writeBrain(copy, loser.text);
      finalBrain[path] = winner.hash;
      finalBrain[copy] = loser.hash;
      report.conflicts.push({ path: prefix + path, copy: prefix + copy, kept: step.keep });
    }
  }
  const nextManifest = {};
  for (const path of new Set([...Object.keys(finalVault), ...Object.keys(finalBrain), ...Object.keys(manifest)])) {
    if (finalVault[path] && finalVault[path] === finalBrain[path]) nextManifest[path] = finalVault[path];
    else if (skipped.has(path) && manifest[path]) nextManifest[path] = manifest[path];
  }
  const changes = Object.values(report).reduce((sum, list) => sum + list.length, 0);
  Object.assign(report, { at: new Date().toISOString(), changes, brainChanged: Boolean(report.pulled.length || report.deletedInBrain.length || report.conflicts.length) });
  saveLocal({ ...saved, manifest: nextManifest, lastSync: report.at, last: changes || !saved.last ? report : saved.last });
  return report;
}

// ============================================================ actions

async function afterSync(report, { auto }) {
  await refreshObsidian();
  if (report?.brainChanged) {
    if (state.info.mode === "server") await loadAll();
    // Re-open the note only if nobody is typing in it.
    if (state.open && noteByPath(state.open) && !$("#drawer-layer")?.contains(document.activeElement)) renderDrawer();
    render();
  } else if (state.view === "obsidian") {
    renderView();
  }
  if (!auto && report) {
    toast(report.changes ? `Synced with Obsidian: ${plural(report.changes, "change")}` : "Already in sync with Obsidian");
  } else if (auto && report?.changes) {
    toast(`Obsidian sync: ${plural(report.changes, "change")}`);
  }
}

async function withBusy(run) {
  if (state.obsidian?.busy) return null;
  setStatus({ busy: true, error: "" });
  if (state.view === "obsidian") renderView();
  try {
    return await run();
  } finally {
    setStatus({ busy: false });
    if (state.view === "obsidian") renderView();
  }
}

export async function syncObsidian({ auto = false } = {}) {
  const kind = mode();
  const status = state.obsidian?.status;
  if (kind === "none" || !status?.linked) return;
  try {
    await withBusy(async () => {
      let report = null;
      if (kind === "server") {
        await flushPending();
        report = (await state.store.request("POST", "api/obsidian/sync", {})).report;
      } else if (status.kind === "handle") {
        if (!vaultHandle) throw new Error("This browser forgot the vault folder. Unlink and choose it again.");
        const options = { mode: "readwrite" };
        let permission = await vaultHandle.queryPermission(options);
        if (permission !== "granted" && !auto) permission = await vaultHandle.requestPermission(options);
        if (permission !== "granted") {
          setStatus({ needsPermission: true });
          return;
        }
        setStatus({ needsPermission: false });
        report = await browserSync(handleVault(vaultHandle), loadLocal(), { auto });
      } else {
        if (!auto) pickImport(loadLocal()?.folder ?? DEFAULT_FOLDER);
        return;
      }
      await afterSync(report, { auto });
    });
  } catch (error) {
    setStatus({ error: error.message || "Sync failed" });
    if (state.view === "obsidian") renderView();
    if (!auto) toast(error.message || "Sync failed", { error: true });
  }
}

function pickImport(folder) {
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.webkitdirectory = true;
  input.addEventListener("change", async () => {
    if (!input.files?.length) return;
    try {
      await withBusy(async () => {
        const vault = await snapshotVault(input.files);
        const previous = loadLocal();
        const saved = previous && previous.vaultName === vault.name && previous.folder === folder
          ? previous
          : { kind: "import", vaultName: vault.name, folder, manifest: {} };
        const report = await browserSync(vault, saved, { pullOnly: true });
        await afterSync(report, { auto: false });
      });
    } catch (error) {
      toast(error.message || "Import failed", { error: true });
    }
  });
  input.click();
}

export async function linkObsidian(form) {
  const kind = mode();
  let folder;
  try {
    folder = cleanFolder(form.folder.value.trim());
  } catch (error) {
    return toast(error.message, { error: true });
  }
  if (kind === "server") {
    const vault = form.vault.value.trim();
    if (!vault) return toast("Paste the path of your vault folder first.", { error: true });
    try {
      await withBusy(async () => {
        await flushPending();
        const result = await state.store.request("POST", "api/obsidian/link", { vault, folder });
        await afterSync(result.report, { auto: false });
      });
      startAutoSync();
    } catch (error) {
      toast(error.message || "Couldn’t link that vault", { error: true });
    }
    return;
  }
  if (kind === "import") return pickImport(folder);
  try {
    const handle = await window.showDirectoryPicker({ id: "cairn-obsidian", mode: "readwrite" });
    await idb((store) => store.put(handle, localKey())).catch(() => {});
    vaultHandle = handle;
    readCache.clear();
    saveLocal({ kind: "handle", vaultName: handle.name, folder, manifest: {} });
    await refreshObsidian();
    await syncObsidian();
    startAutoSync();
  } catch (error) {
    if (error.name !== "AbortError") toast(error.message || "Couldn’t open that folder", { error: true });
  }
}

export async function unlinkObsidian() {
  const ok = await ask({
    title: "Stop syncing with Obsidian?",
    text: "Notes already synced stay in both places. You can link the vault again any time.",
    confirm: "Unlink",
    noInput: true,
  });
  if (!ok) return;
  if (mode() === "server") {
    await state.store.request("POST", "api/obsidian/unlink", {});
  } else {
    saveLocal(null);
    vaultHandle = null;
    readCache.clear();
    await idb((store) => store.delete(localKey())).catch(() => {});
  }
  await refreshObsidian();
  renderView();
  toast("Unlinked from Obsidian");
}

export function toggleAutoSync(on) {
  localStorage.setItem("cairn.obsidian.auto", on ? "on" : "off");
  startAutoSync();
  renderView();
}

function autoTick() {
  const status = state.obsidian?.status;
  if (!status?.linked || status.kind === "import" || !autoOn() || document.visibilityState !== "visible") return;
  if (Date.now() - lastAuto < 3000) return;
  lastAuto = Date.now();
  syncObsidian({ auto: true });
}

export function startAutoSync() {
  clearInterval(timer);
  timer = null;
  const status = state.obsidian?.status;
  if (!status?.linked || status.kind === "import" || !autoOn()) return;
  timer = setInterval(autoTick, AUTO_SYNC_MS);
}

// Called whenever a brain opens.
export async function initObsidian() {
  clearInterval(timer);
  vaultHandle = null;
  readCache.clear();
  state.obsidian = { status: null, busy: false, error: "" };
  await refreshObsidian();
  if (state.view === "obsidian") renderView();
  renderNav();
  startAutoSync();
  if (state.obsidian.status?.linked && state.obsidian.status.kind !== "import" && autoOn()) autoTick();
}

window.addEventListener("focus", autoTick);
document.addEventListener("visibilitychange", autoTick);

// ============================================================ the view

function reportList(report) {
  if (!report) return "";
  const row = (path, label, cls = "") => {
    const note = noteByPath(path);
    const name = esc(path.split("/").pop().replace(/\.md$/i, ""));
    const link = note ? `<a href="#" data-open="${esc(path)}">${name}</a>` : `<span>${name}</span>`;
    return `<li class="${cls}"><span class="sync-op">${label}</span>${link}<span class="muted path">${esc(path)}</span></li>`;
  };
  const rows = [
    ...report.pulled.map((p) => row(p, "from Obsidian")),
    ...report.pushed.map((p) => row((state.obsidian.status.folder ? state.obsidian.status.folder + "/" : "") + p, "to Obsidian")),
    ...report.deletedInBrain.map((p) => row(p, "deleted here", "gone")),
    ...report.deletedInVault.map((p) => row(p, "deleted in vault", "gone")),
    ...report.conflicts.map((c) => row(c.path, `conflict · kept ${c.kept}`, "conflict")),
  ];
  if (!rows.length) return "";
  const shown = rows.slice(0, 60).join("");
  return `
    <section class="card sync-report">
      <div class="file-head">${icon("refresh", "sm")}Last sync ${report.at ? esc(relativeTime(Date.parse(report.at))) : ""}<span class="muted" style="margin-left:auto">${plural(report.changes, "change")}</span></div>
      <ul>${shown}</ul>
      ${rows.length > 60 ? `<p class="muted more">…and ${rows.length - 60} more</p>` : ""}
    </section>`;
}

const HOW = `
  <section class="card sync-how">
    <h3>How sync works</h3>
    <ul>
      <li><strong>Same files, both sides.</strong> Notes are copied as they are: frontmatter, <code>#tags</code>, <code>[[links]]</code> and all. Cairn reads Obsidian links like <code>[[Folder/Note#Heading|alias]]</code>, and daily notes named <code>2026-11-05.md</code> show up in Journal.</li>
      <li><strong>Edits flow both ways.</strong> Change a note in either app and the next sync copies it over. New notes in the synced folder appear on the other side.</li>
      <li><strong>Nothing is lost.</strong> Deleted in Cairn → the vault's <code>.trash/</code>. Deleted in Obsidian → <code>brain/.cairn/trash/</code>. Changed on both sides → the newer one wins and the other is kept as “<em>name</em> (conflict <em>date</em>)”.</li>
      <li><strong>Only Markdown.</strong> Images and other attachments stay in the vault; <code>.obsidian/</code> settings are never touched.</li>
    </ul>
    <p class="muted">Prefer one folder and no sync at all? Open <code>brain/</code> itself as a vault in Obsidian: both apps then edit the same files.</p>
  </section>`;

export function obsidianView() {
  const kind = mode();
  const info = state.obsidian || {};
  const status = info.status;
  const head = (actions = "") => pageHead("Obsidian", "Bring your Obsidian vault into this brain and keep both in sync. Same Markdown files on both sides; Obsidian keeps working exactly as before.", actions, "Import & sync");
  if (kind === "none") {
    return `${head()}<div class="card sync-card"><p>${readOnly() ? "This brain is read-only, so nothing can be imported into it." : "Open your own brain first (a repo folder, the local app, the demo or your GitHub brain) to import a vault."}</p></div>${HOW}`;
  }
  if (!status) return `${head()}<div class="card sync-card"><p class="muted">${info.error ? esc(info.error) : "Checking…"}</p></div>`;
  const busy = info.busy ? "disabled" : "";
  const progress = `<span id="obsidian-progress" class="muted" aria-live="polite">${info.busy ? "Syncing…" : ""}</span>`;
  if (!status.linked) {
    const folderField = `<label for="ob-folder">Into brain folder</label><div class="ob-prefix"><span>brain/</span><input id="ob-folder" name="folder" class="input" value="${DEFAULT_FOLDER}" autocomplete="off" spellcheck="false"></div>`;
    const forms = {
      server: `
        <label for="ob-vault">Vault folder on this computer</label>
        <input id="ob-vault" name="vault" class="input" placeholder="${navigator.platform.startsWith("Win") ? "C:\\Users\\you\\Documents\\My Vault" : "~/Documents/My Vault"}" autocomplete="off" spellcheck="false">
        ${folderField}
        <p class="hint">The folder that contains <code>.obsidian</code>. In Obsidian: <em>Settings → About → Vault location</em> or right-click the vault → <em>Reveal in file explorer</em>.</p>
        <button class="btn primary" type="submit" ${busy}>${icon("refresh", "sm")} Import and keep in sync</button>`,
      handle: `
        ${folderField}
        <p class="hint">Pick the vault folder (the one that contains <code>.obsidian</code>). Cairn imports every note, then keeps both sides in sync while this tab is open.</p>
        <button class="btn primary" type="submit" ${busy}>${icon("folder", "sm")} Choose your vault and sync</button>`,
      import: `
        ${folderField}
        <p class="hint">This browser can read a folder but not write to it, so this is a one-way import. Import again later to bring in what changed. For two-way sync use Chrome or Edge, or the local app.</p>
        <button class="btn primary" type="submit" ${busy}>${icon("folder", "sm")} Choose your vault to import</button>`,
    };
    return `${head()}
      <form class="card sync-card" id="obsidian-form" autocomplete="off">
        <div class="sync-hero">${icon("vault")}<div><h2>Connect a vault</h2><p class="muted">${kind === "server" ? "The local app syncs it on disk, even while Cairn is closed if you run <code>cairn obsidian --watch 10</code>." : "Nothing is uploaded: notes go from the folder straight into this brain."}</p></div></div>
        <div class="ob-fields">${forms[kind]}</div>
        ${progress}
      </form>
      <div class="callout" style="margin:16px 0">${icon("terminal")}<div>From a terminal: <code>cairn obsidian ~/path/to/vault</code> imports and syncs once; add <code>--watch 10</code> to keep syncing every 10 seconds.</div></div>
      ${HOW}`;
  }
  const where = status.folder ? `brain/${esc(status.folder)}/` : "the whole brain";
  const auto = status.kind === "import"
    ? `<p class="muted">One-way import from this browser. Import again to bring in changes.</p>`
    : `<label class="switch"><input type="checkbox" data-obsidian-auto ${autoOn() ? "checked" : ""}> Sync automatically every ${AUTO_SYNC_MS / 1000} s while Cairn is open</label>`;
  const warn = status.vaultFound === false
    ? `<div class="callout warn">${icon("x")}<div>The vault folder can't be found${status.vault ? ` at <code>${esc(status.vault)}</code>` : ""}. Is the drive connected? Unlink and link it again if it moved.</div></div>`
    : info.needsPermission
      ? `<div class="callout">${icon("lock")}<div>The browser needs your OK to open the vault again. Press <strong>Sync now</strong>.</div></div>`
      : "";
  return `${head(`<button class="btn primary" data-action="obsidian-sync" ${busy}>${icon("refresh")} ${status.kind === "import" ? "Import again" : "Sync now"}</button>`)}
    <section class="card sync-card linked">
      <div class="sync-hero">${icon("vault")}<div>
        <h2>${esc(status.vaultName || "Vault")} <span class="sync-arrow" aria-label="synced with">⇄</span> ${where}</h2>
        <p class="muted">${status.vault && status.kind === "server" ? `<code>${esc(status.vault)}</code> · ` : ""}${plural(status.synced || 0, "note")} in sync · ${status.lastSync ? `last synced ${esc(relativeTime(Date.parse(status.lastSync)))}` : "not synced yet"}</p>
      </div></div>
      ${warn}
      ${info.error ? `<div class="callout warn">${icon("x")}<div>${esc(info.error)}</div></div>` : ""}
      <div class="sync-row">${auto}<span class="grow"></span>${progress}<button class="btn sm ghost danger" data-action="obsidian-unlink" ${busy}>Unlink</button></div>
    </section>
    ${reportList(status.last)}
    ${HOW}`;
}

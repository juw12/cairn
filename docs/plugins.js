// Community plugins: small JavaScript modules that add commands, views, code-block renderers and
// reactions to what you do. A plugin is one ES module:
//
//   export const manifest = { id: "word-goal", name: "Word goal", version: "1.0.0", author: "you", description: "…" };
//   export default function activate(cairn) {
//     cairn.addCommand({ id: "hello", name: "Say hello", run: () => cairn.toast("Hello") });
//     return () => {};            // optional: clean-up when the plugin is turned off
//   }
//
// The full API is documented in PLUGINS.md. Plugins are installed per device (in this browser or
// desktop app), never through a shared brain, so nobody can put code on your machine by editing
// notes. They are off until you turn community plugins on, like Obsidian's restricted mode.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { $, ask, emptyState, icon, pageHead, toast, today } from "./ui.js";
import { createNote, noteByPath, readOnly, saveNote, state, upsertFile } from "./state.js";
import { insertIntoOpenNote, openNote } from "./drawer.js";
import { renderNote, resolveFrom } from "./vault.js";
import { pick } from "./palette.js";
import { render, renderNav, renderView, setView } from "./app.js";

export const API_VERSION = 1;
const STORE_KEY = "cairn.plugins.v1";
const MAX_CODE_BYTES = 400_000;
const CATALOG_URL = "plugins/index.json";
const DOCS_URL = "https://github.com/juw12/cairn/blob/main/PLUGINS.md";
const BUILT_IN_BLOCKS = new Set(["mermaid", "math"]);
const EVENTS = ["note-open", "notes-changed", "view-change"];
const SETTING_TYPES = new Set(["toggle", "text", "number", "select", "textarea"]);
// The plugin list that travels with a brain: a note listing where each plugin comes from (never its code).
export const PLUGIN_LIST_PATH = "cairn/plugins.md";
let openSettings = null; // id of the plugin whose settings are showing

const active = new Map(); // id -> { manifest, cleanups: [], error }
const registry = {
  commands: new Map(), // `${plugin}:${id}` -> { plugin, name, icon, run }
  views: new Map(), // `plugin:${plugin}:${id}` -> { plugin, name, icon, render }
  blocks: new Map(), // language -> { plugin, render }
  listeners: Object.fromEntries(EVENTS.map((name) => [name, new Set()])),
};
let catalog = null;
let observer = null;

// ============================================================ stored state

export function loadStore() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    if (saved && typeof saved === "object" && saved.installed && typeof saved.installed === "object") return saved;
  } catch {
    /* start fresh */
  }
  return { enabled: false, installed: {} };
}

function saveStore(store) {
  localStorage.setItem(STORE_KEY, JSON.stringify(store));
}

export function validManifest(manifest) {
  if (!manifest || typeof manifest !== "object") return "The plugin has no manifest (export const manifest = { id, name, version }).";
  if (typeof manifest.id !== "string" || !/^[a-z0-9][a-z0-9-]{1,48}$/.test(manifest.id)) return "The manifest id must be lowercase letters, numbers and dashes.";
  if (typeof manifest.name !== "string" || !manifest.name.trim()) return "The manifest needs a name.";
  if (typeof manifest.version !== "string" || !manifest.version.trim()) return "The manifest needs a version.";
  if (manifest.minApi && Number(manifest.minApi) > API_VERSION) return `This plugin needs a newer Cairn (plugin API ${manifest.minApi}).`;
  return "";
}

async function importCode(code) {
  const url = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
  try {
    return await import(/* @vite-ignore */ url);
  } finally {
    URL.revokeObjectURL(url);
  }
}

// ============================================================ plugin settings
// A plugin describes its options in `manifest.settings` (or with cairn.addSettings) and Cairn draws the
// settings page: [{ key, label, type: "toggle" | "text" | "number" | "select" | "textarea", default,
// options, description, min, max, step }]. Values are stored per device with cairn.settings.

export function normalizeSettings(fields) {
  if (!Array.isArray(fields)) return [];
  const seen = new Set();
  const out = [];
  for (const field of fields.slice(0, 40)) {
    if (!field || typeof field !== "object") continue;
    const key = String(field.key || "");
    if (!/^[\w.-]{1,40}$/.test(key) || seen.has(key)) continue;
    const type = SETTING_TYPES.has(field.type) ? field.type : "text";
    const options = type === "select" && Array.isArray(field.options)
      ? field.options.slice(0, 50).map((o) => (o && typeof o === "object" ? { value: String(o.value), label: String(o.label ?? o.value) } : { value: String(o), label: String(o) }))
      : [];
    if (type === "select" && !options.length) continue;
    seen.add(key);
    out.push({
      key,
      type,
      label: String(field.label || key).slice(0, 80),
      description: String(field.description || "").slice(0, 300),
      default: field.default ?? (type === "toggle" ? false : type === "number" ? 0 : type === "select" ? options[0].value : ""),
      options,
      min: Number.isFinite(field.min) ? field.min : null,
      max: Number.isFinite(field.max) ? field.max : null,
      step: Number.isFinite(field.step) ? field.step : null,
    });
  }
  return out;
}

function readSetting(id, key, fallback) {
  try {
    const raw = localStorage.getItem(`cairn.plugin.${id}.${key}`);
    if (raw !== null) return JSON.parse(raw);
  } catch {
    /* fall through to the default */
  }
  if (fallback !== undefined) return fallback;
  const field = active.get(id)?.settings?.find((f) => f.key === key);
  return field ? field.default : null;
}

function writeSetting(id, key, value) {
  localStorage.setItem(`cairn.plugin.${id}.${key}`, JSON.stringify(value));
  const record = active.get(id);
  for (const listener of record?.settingListeners || []) {
    if (listener.key && listener.key !== key) continue;
    try {
      listener.handler(value, key);
    } catch (error) {
      reportError(id, error, "a settings change");
    }
  }
}

// A plugin went wrong while running (a command, a view, a code block, an event). Its row on the
// Plugins page shows the error until the plugin is restarted; the rest of Cairn keeps working.
function reportError(id, error, where) {
  console.error(`[plugin ${id}]`, error);
  const record = active.get(id);
  if (!record) return;
  record.lastError = { message: error?.message || String(error), where, at: new Date().toISOString() };
}

export const pluginProblems = () => [...active.values()].filter((r) => r.error || r.lastError).map((r) => r.manifest.id);

// ============================================================ the API a plugin receives

function noteCopy(note) {
  if (!note) return null;
  return {
    path: note.path,
    title: note.title,
    type: note.type,
    status: note.status,
    tags: [...note.tags],
    body: note.body,
    meta: structuredClone(note.meta),
    created: typeof note.meta.created === "string" ? note.meta.created : "",
    mtime: note.mtime || 0,
  };
}

function makeApi(manifest, record) {
  const id = manifest.id;
  const own = (cleanup) => record.cleanups.push(cleanup);
  const requireEditable = () => {
    if (readOnly()) throw new Error("This brain is read-only.");
  };
  const api = {
    version: API_VERSION,
    plugin: { ...manifest },
    // ---------------------------------------------------- reading
    notes: () => state.notes.map(noteCopy),
    note: (path) => noteCopy(noteByPath(path)),
    activeNote: () => noteCopy(noteByPath(state.open)),
    resolve: (link, fromPath = "") => noteCopy(resolveFrom(fromPath)(link)),
    files: () => state.files.map((f) => ({ path: f.path, mtime: f.mtime || 0 })),
    readOnly: () => readOnly(),
    project: () => state.info?.project || "",
    today,
    // ---------------------------------------------------- writing
    async createNote({ title, type = "note", body = null, tags = [], status = null, properties = {} } = {}) {
      requireEditable();
      const note = await createNote({ type: core.TYPES.includes(type) ? type : "note", title, body, tags, status, extraMeta: properties });
      render();
      return note.path;
    },
    async updateNote(path, changes = {}) {
      requireEditable();
      if (!noteByPath(path)) throw new Error(`No note at ${path}`);
      const saved = await saveNote(path, changes);
      render();
      return Boolean(saved);
    },
    async writeFile(path, text) {
      requireEditable();
      if (!core.isBrainFile(path) || path.split("/").includes("..")) throw new Error("Plugins can write .md and .canvas files inside the brain.");
      const existing = state.files.find((f) => f.path === path);
      const { mtime } = await state.store.write(path, String(text), existing ? existing.mtime : null);
      upsertFile(path, String(text), mtime);
      render();
      return path;
    },
    insertIntoActiveNote: (text) => insertIntoOpenNote(String(text)),
    async openJournal(day = today()) {
      const date = /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : today();
      const existing = state.notes.find((n) => n.type === "journal" && (n.title === date || n.path.endsWith(`/${date}.md`) || n.path === `${date}.md`));
      if (existing) return openNote(existing.path);
      requireEditable();
      const path = `journal/${date}.md`;
      const text = core.serializeNote({ title: date, type: "journal", tags: [], created: date }, core.TEMPLATES.journal ?? "");
      const { mtime } = await state.store.write(path, text, null);
      upsertFile(path, text, mtime);
      render();
      openNote(path);
    },
    // ---------------------------------------------------- navigating
    openNote: (path, options = {}) => openNote(path, { newTab: Boolean(options.newTab) }),
    go: (view) => setView(view),
    openView: (viewId) => setView(`plugin:${id}:${viewId}`),
    rerender: () => renderView(),
    // ---------------------------------------------------- extending the UI
    addCommand({ id: commandId, name, icon: iconName = "puzzle", run }) {
      if (!commandId || !name || typeof run !== "function") throw new Error("addCommand needs id, name and run.");
      const key = `${id}:${commandId}`;
      registry.commands.set(key, { plugin: id, pluginName: manifest.name, name: String(name), icon: iconName, run });
      own(() => registry.commands.delete(key));
    },
    addView({ id: viewId, name, icon: iconName = "puzzle", render: draw }) {
      if (!viewId || !name || typeof draw !== "function") throw new Error("addView needs id, name and render.");
      const key = `plugin:${id}:${viewId}`;
      registry.views.set(key, { plugin: id, name: String(name), icon: iconName, render: draw });
      own(() => {
        registry.views.delete(key);
        if (state.view === key) setView("home");
      });
      renderNav();
    },
    registerCodeBlock(language, draw) {
      const lang = String(language || "").toLowerCase();
      if (!/^[a-z0-9][a-z0-9_-]*$/.test(lang) || BUILT_IN_BLOCKS.has(lang)) throw new Error(`Can't take over \`\`\`${lang} blocks.`);
      if (typeof draw !== "function") throw new Error("registerCodeBlock needs a render function.");
      registry.blocks.set(lang, { plugin: id, render: draw });
      own(() => {
        if (registry.blocks.get(lang)?.plugin === id) registry.blocks.delete(lang);
      });
      enhanceBlocks(document);
    },
    on(event, handler) {
      if (!registry.listeners[event]) throw new Error(`Unknown event “${event}”. Events: ${EVENTS.join(", ")}.`);
      const wrapped = (payload) => {
        try {
          handler(payload);
        } catch (error) {
          reportError(id, error, `its “${event}” handler`);
        }
      };
      registry.listeners[event].add(wrapped);
      own(() => registry.listeners[event].delete(wrapped));
    },
    addStyle(css) {
      const style = document.createElement("style");
      style.dataset.plugin = id;
      style.textContent = String(css);
      document.head.append(style);
      own(() => style.remove());
    },
    // ---------------------------------------------------- helpers
    toast: (message, options = {}) => toast(String(message), { error: Boolean(options.error) }),
    ask: (options) => ask(options),
    pick: (items, placeholder) => pick(items.map((item) => ({ icon: "note", ...item })), placeholder),
    escape: (value) => esc(value),
    icon: (name, cls = "sm") => icon(name, cls),
    renderMarkdown: (markdown, fromPath = state.open || "") => renderNote({ path: fromPath, body: String(markdown) }, { source: String(markdown), tasks: false }),
    settings: {
      // With no fallback, a key declared in the plugin's settings returns its default.
      get: (key, fallback) => readSetting(id, String(key), fallback),
      set: (key, value) => writeSetting(id, String(key), value),
      onChange(key, handler) {
        if (typeof key === "function") [key, handler] = [null, key];
        if (typeof handler !== "function") throw new Error("settings.onChange needs a function.");
        const listener = { key: key ? String(key) : null, handler };
        record.settingListeners.push(listener);
        own(() => {
          record.settingListeners = record.settingListeners.filter((l) => l !== listener);
        });
      },
    },
    addSettings(fields) {
      const extra = normalizeSettings(fields);
      const keys = new Set(extra.map((f) => f.key));
      record.settings = [...record.settings.filter((f) => !keys.has(f.key)), ...extra];
    },
  };
  return Object.freeze(api);
}

// ============================================================ turning plugins on and off

async function activate(entry) {
  const { manifest, code } = entry;
  deactivate(manifest.id);
  const record = { manifest, cleanups: [], error: "", lastError: null, settings: [], settingListeners: [] };
  active.set(manifest.id, record);
  try {
    const module = await importCode(code);
    record.settings = normalizeSettings(module.manifest?.settings);
    const api = makeApi(manifest, record);
    const main = module.default;
    let cleanup = null;
    if (typeof main === "function") cleanup = await main(api);
    else if (main && typeof main.onload === "function") {
      await main.onload(api);
      if (typeof main.onunload === "function") cleanup = () => main.onunload();
    } else throw new Error("The plugin has no default export (export default function activate(cairn) { … }).");
    if (typeof cleanup === "function") record.cleanups.push(cleanup);
  } catch (error) {
    console.error(`[plugin ${manifest.id}]`, error);
    record.error = error?.message || String(error);
    for (const cleanup of record.cleanups.splice(0).reverse()) {
      try {
        cleanup();
      } catch {
        /* keep going */
      }
    }
  }
  return record;
}

function deactivate(id) {
  const record = active.get(id);
  if (!record) return;
  for (const cleanup of record.cleanups.splice(0).reverse()) {
    try {
      cleanup();
    } catch (error) {
      console.error(`[plugin ${id}] clean-up failed`, error);
    }
  }
  active.delete(id);
}

export async function initPlugins() {
  for (const id of [...active.keys()]) deactivate(id);
  startObserver();
  const store = loadStore();
  if (!store.enabled) return;
  for (const entry of Object.values(store.installed)) {
    if (!entry.enabled) continue;
    const record = await activate(entry);
    if (record.error) toast(`Plugin “${entry.manifest.name}” couldn't start: ${record.error}`, { error: true });
  }
  renderNav();
  if (state.view === "plugins" || state.view.startsWith("plugin:")) renderView();
}

export function emit(event, payload) {
  for (const listener of registry.listeners[event] || []) listener(payload);
}

let changeTimer = null;
export function notesChanged() {
  if (!registry.listeners["notes-changed"].size) return;
  clearTimeout(changeTimer);
  changeTimer = setTimeout(() => emit("notes-changed", { count: state.notes.length }), 250);
}

// ============================================================ hooks used by the rest of the app

export function pluginCommands() {
  return [...registry.commands.values()].map((command) => ({
    label: command.name,
    kind: command.pluginName,
    icon: command.icon,
    run: () =>
      Promise.resolve()
        .then(() => command.run())
        .catch((error) => {
          reportError(command.plugin, error, `the command “${command.name}”`);
          toast(`${command.pluginName}: ${error.message || error}`, { error: true });
        }),
  }));
}

export function pluginViews() {
  return [...registry.views.entries()].map(([key, view]) => ({ id: key, label: view.name, icon: view.icon, group: "Plugins" }));
}

export const isPluginView = (view) => registry.views.has(view);

export function renderPluginView(view, container) {
  const entry = registry.views.get(view);
  if (!entry) return;
  try {
    entry.render(container);
  } catch (error) {
    reportError(entry.plugin, error, `the view “${entry.name}”`);
    container.innerHTML = `<div class="plugin-error">${esc(error.message || String(error))}</div>`;
  }
}

function enhanceBlocks(root) {
  if (!registry.blocks.size) return;
  for (const code of root.querySelectorAll('pre > code[class*="language-"]:not([data-plugin-done])')) {
    const lang = [...code.classList].find((c) => c.startsWith("language-"))?.slice(9).toLowerCase();
    const block = lang && registry.blocks.get(lang);
    if (!block) continue;
    code.dataset.pluginDone = "1";
    const pre = code.parentElement;
    const out = document.createElement("div");
    out.className = "plugin-block";
    out.dataset.pluginBlock = lang;
    const notePath = pre.closest("[data-embed]")?.dataset.embed || (pre.closest("#ed-preview, .drawer") ? state.open : null) || "";
    pre.replaceWith(out);
    try {
      block.render(code.textContent, out, { notePath, language: lang });
    } catch (error) {
      reportError(block.plugin, error, `a \`\`\`${lang} block`);
      out.innerHTML = `<div class="plugin-error">${esc(error.message || String(error))}</div>`;
    }
  }
}

function startObserver() {
  if (observer) return;
  let queued = false;
  observer = new MutationObserver(() => {
    if (queued || !registry.blocks.size) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      enhanceBlocks(document);
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

// ============================================================ installing

async function fetchText(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`Couldn't download ${url} (${response.status}).`);
  const text = await response.text();
  if (text.length > MAX_CODE_BYTES) throw new Error("That plugin is too big (over 400 KB).");
  return text;
}

// "owner/repo" (a GitHub repo with main.js at its root), a GitHub URL, or a direct https URL to a .js file.
export function pluginSource(input) {
  const value = String(input || "").trim();
  if (/^plugins\/[\w-]+\.js$/.test(value)) return { url: value, label: `Cairn plugin list (${value})`, spec: value };
  const repo = /^(?:https?:\/\/github\.com\/)?([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/i.exec(value);
  if (repo && !/^https?:\/\/(?!github\.com)/i.test(value)) {
    return { url: `https://raw.githubusercontent.com/${repo[1]}/${repo[2]}/HEAD/main.js`, label: `${repo[1]}/${repo[2]}`, spec: `${repo[1]}/${repo[2]}` };
  }
  if (/^https:\/\/\S+\.m?js(\?\S*)?$/i.test(value)) return { url: value, label: value, spec: value };
  return null;
}

export async function installPlugin(input, { quiet = false } = {}) {
  const source = typeof input === "string" ? pluginSource(input) : input;
  if (!source) throw new Error("Use owner/repo for a GitHub plugin, or a https:// link to its .js file.");
  const code = await fetchText(source.url);
  let module;
  try {
    module = await importCode(code);
  } catch (error) {
    throw new Error(`That file isn't a working plugin: ${error.message || error}`);
  }
  const manifest = module.manifest;
  const problem = validManifest(manifest);
  if (problem) throw new Error(problem);
  const store = loadStore();
  const clean = {
    id: manifest.id,
    name: String(manifest.name).slice(0, 60),
    version: String(manifest.version).slice(0, 20),
    author: String(manifest.author || "").slice(0, 60),
    description: String(manifest.description || "").slice(0, 300),
  };
  store.installed[manifest.id] = { manifest: clean, code, source: source.label, spec: source.spec || source.label, enabled: true, installedAt: new Date().toISOString() };
  store.enabled = true;
  saveStore(store);
  const record = await activate(store.installed[manifest.id]);
  renderNav();
  if (!quiet) syncBrainList();
  if (!quiet) {
    if (record.error) toast(`Installed “${clean.name}”, but it couldn't start: ${record.error}`, { error: true });
    else toast(`Installed and turned on “${clean.name}”.`);
  }
  return clean;
}

export function removePlugin(id) {
  const store = loadStore();
  const entry = store.installed[id];
  if (!entry) return;
  deactivate(id);
  delete store.installed[id];
  saveStore(store);
  for (const key of Object.keys(localStorage)) if (key.startsWith(`cairn.plugin.${id}.`)) localStorage.removeItem(key);
  if (openSettings === id) openSettings = null;
  renderNav();
  toast(`Removed “${entry.manifest.name}”.`);
  syncBrainList();
}

export async function restartPlugin(id) {
  const store = loadStore();
  const entry = store.installed[id];
  if (!entry || !store.enabled || !entry.enabled) return;
  const record = await activate(entry);
  renderNav();
  if (record.error) toast(`“${entry.manifest.name}” couldn't start: ${record.error}`, { error: true });
  else toast(`Restarted “${entry.manifest.name}”.`);
}

// ============================================================ the plugin list that travels with the brain
// Brains hold only notes and canvases, so the list is a note: brain/cairn/plugins.md, with the source of
// each plugin in its front matter. Other devices see it on the Plugins page and can install the list in
// one step; nothing is installed or run without asking, because anyone who can edit the brain can edit it.

const specOf = (entry) => entry.spec || /\((plugins\/[\w-]+\.js)\)$/.exec(entry.source || "")?.[1] || entry.source;

export function brainPluginList() {
  const note = noteByPath(PLUGIN_LIST_PATH);
  const list = Array.isArray(note?.meta?.plugins) ? note.meta.plugins : [];
  return [...new Set(list.map((item) => String(item).trim()).filter((item) => pluginSource(item)))];
}

function installedSpecs() {
  return Object.values(loadStore().installed).map(specOf);
}

export async function saveBrainList({ quiet = false } = {}) {
  if (readOnly()) throw new Error("This brain is read-only.");
  const specs = installedSpecs();
  const existing = noteByPath(PLUGIN_LIST_PATH);
  if (existing) {
    await saveNote(PLUGIN_LIST_PATH, { properties: { plugins: specs } });
  } else {
    const body = "The community plugins this brain uses. Cairn lists them on the Plugins page of every device that opens this brain and offers to install them; nothing is installed or run without asking.\n\nEdit the list on the Plugins page (Save list to this brain), or here.\n";
    const text = core.serializeNote({ title: "Cairn plugins", type: "note", tags: [], plugins: specs }, body);
    const { mtime } = await state.store.write(PLUGIN_LIST_PATH, text, null);
    upsertFile(PLUGIN_LIST_PATH, text, mtime);
  }
  render();
  if (!quiet) toast(`Saved the plugin list to brain/${PLUGIN_LIST_PATH} (${specs.length}).`);
  return specs;
}

// Once a brain has a plugin list, installing or removing a plugin keeps it up to date.
function syncBrainList() {
  if (!noteByPath(PLUGIN_LIST_PATH) || readOnly()) return;
  const saved = brainPluginList();
  const now = installedSpecs();
  if (saved.length === now.length && saved.every((s) => now.includes(s))) return;
  saveBrainList({ quiet: true }).catch((error) => console.error("[plugins] couldn't update the brain's plugin list", error));
}

export function missingFromBrainList() {
  const have = new Set(installedSpecs());
  return brainPluginList().filter((spec) => !have.has(spec));
}

async function installBrainList() {
  const missing = missingFromBrainList();
  if (!missing.length) return;
  const external = missing.filter((spec) => !spec.startsWith("plugins/"));
  const ok = await ask({
    title: `Install ${missing.length} plugin${missing.length === 1 ? "" : "s"} from this brain?`,
    text: `${missing.join(", ")}. ${external.length ? "Plugins run with the same access as Cairn on this device; only continue if you trust where they come from." : "These come with Cairn."}`,
    confirm: "Install",
    noInput: true,
  });
  if (!ok) return;
  let done = 0;
  for (const spec of missing) {
    try {
      await installPlugin(spec, { quiet: true });
      done += 1;
    } catch (error) {
      toast(`${spec}: ${error.message || error}`, { error: true });
    }
  }
  if (done) toast(`Installed ${done} plugin${done === 1 ? "" : "s"} from this brain.`);
}

export async function setPluginEnabled(id, enabled) {
  const store = loadStore();
  const entry = store.installed[id];
  if (!entry) return;
  entry.enabled = enabled;
  saveStore(store);
  if (enabled && store.enabled) {
    const record = await activate(entry);
    if (record.error) toast(`“${entry.manifest.name}” couldn't start: ${record.error}`, { error: true });
  } else deactivate(id);
  renderNav();
}

export async function setCommunityPlugins(enabled) {
  const store = loadStore();
  store.enabled = enabled;
  saveStore(store);
  await initPlugins();
  if (!enabled) renderNav();
}

async function loadCatalog() {
  if (catalog) return catalog;
  try {
    const response = await fetch(CATALOG_URL, { cache: "no-store" });
    const data = await response.json();
    catalog = Array.isArray(data.plugins) ? data.plugins : [];
  } catch {
    catalog = [];
  }
  return catalog;
}

// ============================================================ the Plugins view

export function pluginsView() {
  const store = loadStore();
  const installed = Object.values(store.installed);
  const head = pageHead("Plugins", "Add features made by the community: calendars, queries, stats and more. Plugin code lives on this device; the list of plugins can travel with the brain.", "", "Project");
  const listed = brainPluginList();
  const intro = `
    <section class="plugin-gate${store.enabled ? " on" : ""}">
      <div>
        <h2>${icon("puzzle", "sm")} Community plugins are ${store.enabled ? "on" : "off"}</h2>
        <p>A plugin is JavaScript that runs inside Cairn with the same access Cairn has: your notes and, when you're signed in, your GitHub session. Install only plugins you trust, from people you trust. You can turn them all off here at any time.</p>
      </div>
      <button class="btn ${store.enabled ? "" : "primary"}" data-plugins-toggle="${store.enabled ? "off" : "on"}">${store.enabled ? "Turn off" : "Turn on community plugins"}</button>
    </section>`;
  if (!store.enabled) {
    const hint = listed.length ? `<p class="plugin-brain-hint">${icon("note", "sm")} This brain lists ${listed.length} plugin${listed.length === 1 ? "" : "s"} (${listed.map(esc).join(", ")}). Turn on community plugins to install ${listed.length === 1 ? "it" : "them"}.</p>` : "";
    return `${head}${intro}${hint}<p class="muted plugin-docs">Want to write one? A plugin is a single JavaScript file. <a href="${DOCS_URL}" target="_blank" rel="noopener">Read the plugin guide</a>.</p>`;
  }
  const rows = installed.length
    ? installed.map((entry) => {
        const id = entry.manifest.id;
        const record = active.get(id);
        const startError = entry.enabled && record?.error ? `<p class="plugin-error" role="alert">Couldn't start: ${esc(record.error)} <button class="btn sm" data-plugin-restart="${esc(id)}">${icon("refresh", "sm")} Try again</button></p>` : "";
        const runError = entry.enabled && !record?.error && record?.lastError
          ? `<p class="plugin-error" role="alert">Went wrong in ${esc(record.lastError.where)}: ${esc(record.lastError.message)} <button class="btn sm" data-plugin-restart="${esc(id)}">${icon("refresh", "sm")} Restart</button></p>`
          : "";
        const fields = entry.enabled && store.enabled ? record?.settings || [] : [];
        const open = openSettings === id && fields.length;
        const gear = fields.length
          ? `<button class="btn sm ghost${open ? " on" : ""}" data-plugin-settings="${esc(id)}" aria-expanded="${Boolean(open)}" title="Settings">${icon("gear", "sm")}<span class="sr-only">Settings for ${esc(entry.manifest.name)}</span></button>`
          : "";
        return `<div class="plugin-row${record?.error || record?.lastError ? " has-error" : ""}" data-plugin="${esc(id)}">
          <div class="plugin-main">
            <strong>${esc(entry.manifest.name)}</strong> <span class="muted">v${esc(entry.manifest.version)}${entry.manifest.author ? ` · ${esc(entry.manifest.author)}` : ""}</span>
            <p>${esc(entry.manifest.description || "")}</p>
            <span class="plugin-source">${icon("link", "sm")} ${esc(entry.source)}</span>
            ${startError}${runError}
          </div>
          ${gear}
          <label class="switch" title="${entry.enabled ? "Turn off" : "Turn on"}"><input type="checkbox" data-plugin-enable="${esc(id)}" ${entry.enabled ? "checked" : ""} aria-label="Turn ${esc(entry.manifest.name)} on or off"><span></span></label>
          <button class="btn sm ghost danger" data-plugin-remove="${esc(id)}" title="Remove">${icon("trash", "sm")}<span class="sr-only">Remove ${esc(entry.manifest.name)}</span></button>
        </div>${open ? settingsForm(id, entry.manifest.name, fields) : ""}`;
      }).join("")
    : emptyState("puzzle", "No plugins installed", "Pick one from the list below, or install one from GitHub.");
  const problems = pluginProblems().length;
  const missing = missingFromBrainList();
  const listNote = noteByPath(PLUGIN_LIST_PATH);
  const inSync = listNote && !missing.length && installed.every((entry) => listed.includes(specOf(entry)));
  const brainSection = `
    <section class="plugin-section plugin-brain">
      <h2>Plugins in this brain</h2>
      ${missing.length
        ? `<div class="plugin-brain-missing"><p>${icon("note", "sm")} This brain uses ${missing.length} plugin${missing.length === 1 ? "" : "s"} that ${missing.length === 1 ? "isn't" : "aren't"} on this device: ${missing.map((s) => `<code>${esc(s)}</code>`).join(", ")}.</p>
            <button class="btn primary sm" data-plugins-install-list>Install ${missing.length === 1 ? "it" : "them"}</button></div>`
        : ""}
      <p class="muted">${listNote
        ? `The list is kept in <a href="#" data-open="${esc(PLUGIN_LIST_PATH)}">brain/${esc(PLUGIN_LIST_PATH)}</a>${inSync ? " and matches this device." : "."} It names where each plugin comes from, never its code, and other devices ask before installing.`
        : "Save the list of plugins you use into this brain, so your other devices (and anyone you share the brain with) can install the same ones. Only where each plugin comes from is saved, never its code."}</p>
      ${readOnly() ? "" : `<button class="btn sm" data-plugins-save-list ${inSync ? "disabled" : ""}>${icon("save", "sm")} ${listNote ? "Update the list in this brain" : "Save list to this brain"}</button>`}
    </section>`;
  return `${head}${intro}
    <section class="plugin-section"><h2>Installed${problems ? ` <span class="plugin-problems">${problems} with a problem</span>` : ""}</h2>${rows}</section>
    ${installed.length || listed.length ? brainSection : ""}
    <section class="plugin-section"><h2>Browse</h2><div class="plugin-catalog" id="plugin-catalog"><p class="muted">Loading the plugin list…</p></div></section>
    <section class="plugin-section">
      <h2>Install from GitHub or a link</h2>
      <form class="plugin-install" id="plugin-install-form" autocomplete="off">
        <input class="input" name="source" placeholder="owner/repo, or https://…/plugin.js" aria-label="Plugin source">
        <button class="btn primary" type="submit">Install</button>
      </form>
      <p class="muted plugin-docs">A GitHub plugin keeps its code in <code>main.js</code> at the root of its repo. <a href="${DOCS_URL}" target="_blank" rel="noopener">How to write a plugin</a>.</p>
    </section>`;
}

function settingsForm(id, name, fields) {
  const control = (field) => {
    const value = readSetting(id, field.key);
    const attrs = `id="ps-${esc(id)}-${esc(field.key)}" data-setting="${esc(field.key)}" data-setting-type="${field.type}"`;
    if (field.type === "toggle") return `<label class="switch"><input type="checkbox" ${attrs} ${value ? "checked" : ""}><span></span></label>`;
    if (field.type === "select") return `<select class="select" ${attrs}>${field.options.map((o) => `<option value="${esc(o.value)}" ${String(value) === o.value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
    if (field.type === "textarea") return `<textarea class="input" rows="3" ${attrs}>${esc(value ?? "")}</textarea>`;
    if (field.type === "number") return `<input class="input" type="number" ${attrs} value="${esc(value ?? "")}"${field.min !== null ? ` min="${field.min}"` : ""}${field.max !== null ? ` max="${field.max}"` : ""}${field.step !== null ? ` step="${field.step}"` : ""}>`;
    return `<input class="input" type="text" ${attrs} value="${esc(value ?? "")}">`;
  };
  return `<form class="plugin-settings" data-plugin-form="${esc(id)}" aria-label="${esc(name)} settings">
    ${fields.map((field) => `<div class="plugin-setting${field.type === "toggle" ? " inline" : ""}">
      <label for="ps-${esc(id)}-${esc(field.key)}">${esc(field.label)}${field.description ? `<span class="muted">${esc(field.description)}</span>` : ""}</label>
      ${control(field)}
    </div>`).join("")}
    <div class="plugin-settings-foot"><span class="muted">Saved as you change them, on this device.</span><button type="button" class="btn sm ghost" data-plugin-settings-reset="${esc(id)}">Reset to defaults</button></div>
  </form>`;
}

export function wirePlugins() {
  const catalogEl = $("#plugin-catalog");
  if (catalogEl) {
    loadCatalog().then((list) => {
      const store = loadStore();
      if (!catalogEl.isConnected) return;
      catalogEl.innerHTML = list.length
        ? list.map((item) => {
            const have = store.installed[item.id];
            const label = !have ? "Install" : have.manifest.version !== item.version ? `Update to ${esc(item.version)}` : "Installed";
            return `<div class="plugin-card">
              <strong>${esc(item.name)}</strong>
              <span class="muted">${esc(item.author || "")}</span>
              <p>${esc(item.description || "")}</p>
              <button class="btn sm ${have && label === "Installed" ? "ghost" : "primary"}" data-plugin-install="${esc(item.main || item.repo || "")}" data-plugin-name="${esc(item.name)}" ${have && label === "Installed" ? "disabled" : ""}>${label}</button>
            </div>`;
          }).join("")
        : `<p class="muted">The plugin list couldn't be loaded (offline?). You can still install from GitHub below.</p>`;
    });
  }
}

async function confirmInstall(label, name) {
  return ask({
    title: `Install ${name || "this plugin"}?`,
    text: `From ${label}. It will run with the same access as Cairn on this device. Only continue if you trust its author.`,
    confirm: "Install",
    noInput: true,
  });
}

export async function handlePluginClick(target) {
  const toggle = target.closest("[data-plugins-toggle]");
  if (toggle) {
    await setCommunityPlugins(toggle.dataset.pluginsToggle === "on");
    renderView();
    return true;
  }
  const install = target.closest("[data-plugin-install]");
  if (install) {
    const source = pluginSource(install.dataset.pluginInstall);
    if (!source) return true;
    const bundled = source.url.startsWith("plugins/");
    if (!bundled && !(await confirmInstall(source.label, install.dataset.pluginName))) return true;
    install.disabled = true;
    install.textContent = "Installing…";
    try {
      await installPlugin(source);
    } catch (error) {
      toast(error.message || String(error), { error: true });
    }
    renderView();
    return true;
  }
  const gear = target.closest("[data-plugin-settings]");
  if (gear) {
    openSettings = openSettings === gear.dataset.pluginSettings ? null : gear.dataset.pluginSettings;
    renderView();
    $(`[data-plugin-form="${CSS.escape(gear.dataset.pluginSettings)}"] [data-setting]`)?.focus();
    return true;
  }
  const reset = target.closest("[data-plugin-settings-reset]");
  if (reset) {
    const id = reset.dataset.pluginSettingsReset;
    for (const field of active.get(id)?.settings || []) {
      localStorage.removeItem(`cairn.plugin.${id}.${field.key}`);
      writeSetting(id, field.key, field.default);
    }
    toast("Settings reset to their defaults.");
    renderView();
    return true;
  }
  const restart = target.closest("[data-plugin-restart]");
  if (restart) {
    await restartPlugin(restart.dataset.pluginRestart);
    renderView();
    return true;
  }
  if (target.closest("[data-plugins-save-list]")) {
    try {
      await saveBrainList();
    } catch (error) {
      toast(error.message || String(error), { error: true });
    }
    renderView();
    return true;
  }
  if (target.closest("[data-plugins-install-list]")) {
    await installBrainList();
    renderView();
    return true;
  }
  const remove = target.closest("[data-plugin-remove]");
  if (remove) {
    const ok = await ask({ title: "Remove this plugin?", text: "Its code and its settings on this device are deleted. Your notes are not touched.", confirm: "Remove", noInput: true, danger: true });
    if (ok) removePlugin(remove.dataset.pluginRemove);
    renderView();
    return true;
  }
  return false;
}

export async function handlePluginChange(target) {
  const setting = target.closest("[data-setting]");
  const form = setting?.closest("[data-plugin-form]");
  if (setting && form) {
    const type = setting.dataset.settingType;
    let value = setting.value;
    if (type === "toggle") value = setting.checked;
    else if (type === "number") {
      value = setting.value === "" ? null : Number(setting.value);
      if (value !== null && !Number.isFinite(value)) return true;
    }
    writeSetting(form.dataset.pluginForm, setting.dataset.setting, value);
    return true;
  }
  const enable = target.closest("[data-plugin-enable]");
  if (!enable) return false;
  await setPluginEnabled(enable.dataset.pluginEnable, enable.checked);
  renderView();
  return true;
}

export async function handlePluginSubmit(form) {
  if (form.id !== "plugin-install-form") return false;
  const value = form.source.value.trim();
  const source = pluginSource(value);
  if (!source) {
    toast("Use owner/repo for a GitHub plugin, or a https:// link to its .js file.", { error: true });
    return true;
  }
  if (!(await confirmInstall(source.label))) return true;
  try {
    await installPlugin(source);
    form.reset();
  } catch (error) {
    toast(error.message || String(error), { error: true });
  }
  renderView();
  return true;
}

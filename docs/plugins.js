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
  const settingsKey = (key) => `cairn.plugin.${id}.${key}`;
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
          console.error(`[plugin ${id}]`, error);
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
      get(key, fallback = null) {
        try {
          const raw = localStorage.getItem(settingsKey(key));
          return raw === null ? fallback : JSON.parse(raw);
        } catch {
          return fallback;
        }
      },
      set(key, value) {
        localStorage.setItem(settingsKey(key), JSON.stringify(value));
      },
    },
  };
  return Object.freeze(api);
}

// ============================================================ turning plugins on and off

async function activate(entry) {
  const { manifest, code } = entry;
  deactivate(manifest.id);
  const record = { manifest, cleanups: [], error: "" };
  active.set(manifest.id, record);
  try {
    const module = await importCode(code);
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
    run: () => Promise.resolve(command.run()).catch((error) => toast(`${command.pluginName}: ${error.message || error}`, { error: true })),
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
  if (/^plugins\/[\w-]+\.js$/.test(value)) return { url: value, label: `Cairn plugin list (${value})` };
  const repo = /^(?:https?:\/\/github\.com\/)?([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/i.exec(value);
  if (repo && !/^https?:\/\/(?!github\.com)/i.test(value)) {
    return { url: `https://raw.githubusercontent.com/${repo[1]}/${repo[2]}/HEAD/main.js`, label: `${repo[1]}/${repo[2]}` };
  }
  if (/^https:\/\/\S+\.m?js(\?\S*)?$/i.test(value)) return { url: value, label: value };
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
  store.installed[manifest.id] = { manifest: clean, code, source: source.label, enabled: true, installedAt: new Date().toISOString() };
  store.enabled = true;
  saveStore(store);
  const record = await activate(store.installed[manifest.id]);
  renderNav();
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
  renderNav();
  toast(`Removed “${entry.manifest.name}”.`);
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
  const head = pageHead("Plugins", "Add features made by the community: calendars, queries, stats and more. Plugins live on this device and never travel with a shared brain.", "", "Project");
  const intro = `
    <section class="plugin-gate${store.enabled ? " on" : ""}">
      <div>
        <h2>${icon("puzzle", "sm")} Community plugins are ${store.enabled ? "on" : "off"}</h2>
        <p>A plugin is JavaScript that runs inside Cairn with the same access Cairn has: your notes and, when you're signed in, your GitHub session. Install only plugins you trust, from people you trust. You can turn them all off here at any time.</p>
      </div>
      <button class="btn ${store.enabled ? "" : "primary"}" data-plugins-toggle="${store.enabled ? "off" : "on"}">${store.enabled ? "Turn off" : "Turn on community plugins"}</button>
    </section>`;
  if (!store.enabled) {
    return `${head}${intro}<p class="muted plugin-docs">Want to write one? A plugin is a single JavaScript file. <a href="${DOCS_URL}" target="_blank" rel="noopener">Read the plugin guide</a>.</p>`;
  }
  const rows = installed.length
    ? installed.map((entry) => {
        const record = active.get(entry.manifest.id);
        const error = entry.enabled && record?.error ? `<p class="plugin-error">Couldn't start: ${esc(record.error)}</p>` : "";
        return `<div class="plugin-row" data-plugin="${esc(entry.manifest.id)}">
          <div class="plugin-main">
            <strong>${esc(entry.manifest.name)}</strong> <span class="muted">v${esc(entry.manifest.version)}${entry.manifest.author ? ` · ${esc(entry.manifest.author)}` : ""}</span>
            <p>${esc(entry.manifest.description || "")}</p>
            <span class="plugin-source">${icon("link", "sm")} ${esc(entry.source)}</span>
            ${error}
          </div>
          <label class="switch" title="${entry.enabled ? "Turn off" : "Turn on"}"><input type="checkbox" data-plugin-enable="${esc(entry.manifest.id)}" ${entry.enabled ? "checked" : ""} aria-label="Turn ${esc(entry.manifest.name)} on or off"><span></span></label>
          <button class="btn sm ghost danger" data-plugin-remove="${esc(entry.manifest.id)}" title="Remove">${icon("trash", "sm")}<span class="sr-only">Remove ${esc(entry.manifest.name)}</span></button>
        </div>`;
      }).join("")
    : emptyState("puzzle", "No plugins installed", "Pick one from the list below, or install one from GitHub.");
  return `${head}${intro}
    <section class="plugin-section"><h2>Installed</h2>${rows}</section>
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

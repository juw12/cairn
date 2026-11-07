// The Map view: a live, filterable graph of notes and their links, with local graphs, paths between
// notes, keyboard navigation, insights and one-click link suggestions.

import * as core from "./core.js";
import * as G from "./graph.js";
import { escapeHtml as esc } from "./md.js";
import { $, $$, TYPE_META, emptyState, icon, pageHead, plural, toast } from "./ui.js";
import { createNote, noteByPath, readOnly, saveNote, state, upsertFile } from "./state.js";
import { openNote } from "./drawer.js";
import { renderView } from "./app.js";
import { createView3D, reset3D } from "./map3d.js";

// ------------------------------------------------------------ settings and session

const SETTINGS_KEY = "cairn.map";
const DEFAULTS = {
  query: "",
  types: [],
  folder: "",
  orphans: true,
  unresolved: false,
  attachments: false,
  canvases: true,
  arrows: false,
  colour: "auto",
  depth: 1,
  view: "2d",
  spin: true,
};
const PALETTE = 8;
const LABEL_FONT = 11.5;
const LABEL_CHAR = LABEL_FONT * 0.54;

function loadSettings() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
  } catch {
    return { ...DEFAULTS };
  }
}

let settings = loadSettings();
function saveSettings(changes) {
  settings = { ...settings, ...changes };
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

// What the person is doing on the map; survives re-renders (saving a note, changing a filter).
const session = { selected: null, local: null, path: null, pathEnds: ["", ""], pathMessage: "" };
// Where every dot was last time, so filters and edits never reshuffle the map.
const positions = new Map();
let lastSignature = "";
let model = null;

// ------------------------------------------------------------ model

function nodeRadius(degree) {
  return 5 + Math.sqrt(degree) * 2.6;
}

function matchesQuery(node, query, degree) {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  return tokens.every((token) => {
    if (token === "is:orphan") return degree === 0;
    if (token.startsWith("#") || token.startsWith("tag:")) {
      const tag = token.replace(/^#|^tag:/, "");
      return node.tags.some((t) => t.toLowerCase().includes(tag));
    }
    if (token.startsWith("path:")) return node.id.toLowerCase().includes(token.slice(5));
    return node.title.toLowerCase().includes(token) || node.id.toLowerCase().includes(token);
  });
}

function topFolder(node) {
  return node.folder ? node.folder.split("/")[0] : "";
}

function colourMode(graph) {
  if (settings.colour !== "auto") return settings.colour;
  const typed = graph.nodes.filter((n) => n.type !== "note").length;
  return typed >= graph.nodes.length * 0.3 || !graph.nodes.some((n) => n.folder) ? "type" : "folder";
}

async function openCanvasNode(id) {
  const node = model?.byId.get(id);
  if (node?.kind !== "canvas") return;
  const { openCanvas } = await import("./canvas.js");
  openCanvas(node.path);
}

function buildModel() {
  const graph = core.buildGraph(state.notes);
  const adj = G.adjacency(graph);
  const degree = (id) => adj.get(id)?.size || 0;
  const clusters = G.communities(adj);
  let visible = graph.nodes;
  if (session.local && !adj.has(session.local)) session.local = null;
  if (session.selected && !adj.has(session.selected)) session.selected = null;
  if (session.local) {
    const near = G.neighbourhood(adj, session.local, settings.depth);
    visible = visible.filter((n) => near.has(n.id));
  }
  if (settings.types.length) visible = visible.filter((n) => settings.types.includes(n.type) || n.id === session.local);
  if (settings.folder) visible = visible.filter((n) => n.folder === settings.folder || n.folder.startsWith(`${settings.folder}/`) || n.id === session.local);
  if (settings.query.trim()) visible = visible.filter((n) => matchesQuery(n, settings.query, degree(n.id)) || n.id === session.local);
  if (!settings.orphans) visible = visible.filter((n) => degree(n.id) > 0 || n.id === session.local);
  const shown = new Set(visible.map((n) => n.id));
  for (const id of session.path || []) shown.add(id);
  const nodes = graph.nodes.filter((n) => shown.has(n.id)).map((n) => ({ ...n, kind: "note", r: nodeRadius(degree(n.id)) }));
  const edges = graph.edges.filter((e) => shown.has(e.source) && shown.has(e.target)).map((e) => ({ ...e }));
  const extra = (prefix, kind, title, from) => {
    const id = `${prefix}${title.toLowerCase()}`;
    if (!nodes.some((n) => n.id === id)) nodes.push({ id, title, kind, type: "note", folder: "", tags: [], r: 4.5 });
    if (!edges.some((e) => e.source === from && e.target === id)) edges.push({ source: from, target: id, both: false, kind });
  };
  if (settings.unresolved) {
    for (const u of graph.unresolved) if (shown.has(u.from)) extra("?", "ghost", u.target, u.from);
  }
  if (settings.attachments) {
    for (const a of graph.attachments) if (shown.has(a.from)) extra("@", "file", a.target.split("/").pop(), a.from);
  }
  if (settings.canvases) {
    // A canvas is a hub: one square per board, joined to every shown note it holds or links.
    for (const { canvas, note } of core.canvasLinks(state.files, state.notes)) {
      if (!shown.has(note)) continue;
      const id = `canvas:${canvas}`;
      if (!nodes.some((n) => n.id === id)) {
        nodes.push({ id, path: canvas, title: canvas.split("/").pop().replace(/\.canvas$/i, ""), kind: "canvas", type: "note", folder: "", tags: [], r: 6 });
      }
      edges.push({ source: id, target: note, both: false, kind: "canvas" });
    }
  }
  const mentions = G.unlinkedMentions(state.notes, graph);
  return { graph, adj, degree, clusters, nodes, edges, mentions, mode: colourMode(graph), byId: new Map(nodes.map((n) => [n.id, n])) };
}

function layout(m) {
  const signature = `${m.nodes.map((n) => n.id).join("|")}#${m.edges.map((e) => `${e.source}>${e.target}`).join("|")}`;
  for (const node of m.nodes) {
    const known = positions.get(node.id);
    if (known) {
      node.x = known.x;
      node.y = known.y;
      continue;
    }
    // New dots start next to a linked neighbour that already has a place.
    const anchor = m.edges
      .filter((e) => e.source === node.id || e.target === node.id)
      .map((e) => positions.get(e.source === node.id ? e.target : e.source))
      .find(Boolean);
    if (anchor) {
      node.x = anchor.x + 18;
      node.y = anchor.y + 12;
    }
  }
  const known = m.nodes.filter((n) => positions.has(n.id)).length;
  m.sim = G.createSimulation(m.nodes, m.edges, { alpha: known === m.nodes.length ? 0 : known > m.nodes.length * 0.6 ? 0.4 : 1 });
  if (signature !== lastSignature || known !== m.nodes.length) {
    if (m.sim.alpha === 0) m.sim.alpha = 0.3;
    const start = performance.now();
    m.ticks = m.sim.run(1500);
    m.layoutMs = performance.now() - start;
  }
  lastSignature = signature;
  savePositions(m);
}

function savePositions(m) {
  for (const node of m.nodes) positions.set(node.id, { x: node.x, y: node.y });
}

// ------------------------------------------------------------ colours and legend

function folderColours(m) {
  const counts = new Map();
  for (const n of m.graph.nodes) counts.set(topFolder(n), (counts.get(topFolder(n)) || 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, PALETTE).map(([name]) => name);
}

function tagColours(m) {
  const counts = new Map();
  for (const n of m.graph.nodes) if (n.tags[0]) counts.set(n.tags[0], (counts.get(n.tags[0]) || 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, PALETTE).map(([name]) => name);
}

function colourer(m) {
  if (m.mode === "folder") {
    const folders = folderColours(m);
    return {
      of: (n) => (folders.includes(topFolder(n)) ? `var(--g${folders.indexOf(topFolder(n))})` : "var(--g-other)"),
      legend: folders.map((f, i) => [`var(--g${i})`, f || "Vault root"]),
    };
  }
  if (m.mode === "cluster") {
    return {
      of: (n) => {
        const rank = m.clusters.get(n.id);
        return rank === undefined || rank < 0 ? "var(--g-other)" : rank < PALETTE ? `var(--g${rank})` : "var(--g-other)";
      },
      legend: Array.from({ length: Math.min(PALETTE, new Set([...m.clusters.values()].filter((r) => r >= 0)).size) }, (_, i) => [`var(--g${i})`, `Cluster ${i + 1}`]),
    };
  }
  if (m.mode === "tag") {
    const tags = tagColours(m);
    return {
      of: (n) => (tags.includes(n.tags[0]) ? `var(--g${tags.indexOf(n.tags[0])})` : "var(--g-other)"),
      legend: tags.map((t, i) => [`var(--g${i})`, `#${t}`]),
    };
  }
  return { of: (n) => `var(--${n.type})`, legend: core.TYPES.map((t) => [`var(--${t})`, TYPE_META[t].label]) };
}

// ------------------------------------------------------------ view

export function mapView() {
  if (!state.notes.length) {
    return `${pageHead("Map", "How your ideas, tasks, decisions and notes connect.")}<div class="card">${emptyState("map", "Nothing to map yet", "Notes appear here once you have some. Link them with [[file-name]] to draw connections.")}</div>`;
  }
  model = buildModel();
  layout(model);
  const m = model;
  const facts = G.insights(m.graph, m.adj);
  m.facts = facts;
  const colour = colourer(m);
  m.colour = colour;
  const is3d = settings.view === "3d";
  const edgeSvg = is3d ? "" : m.edges
    .map((e) => `<line class="map-edge${e.kind ? ` map-edge-${e.kind}` : ""}${e.both ? " both" : ""}" data-a="${esc(e.source)}" data-b="${esc(e.target)}"/>`)
    .join("");
  const nodeSvg = is3d ? "" : m.nodes
    .map((n) => {
      const links = m.degree(n.id);
      const label = n.kind === "note" ? `${n.title}, ${plural(links, "link")}` : n.kind === "ghost" ? `${n.title}, missing note` : n.kind === "canvas" ? `${n.title}, canvas` : `${n.title}, attachment`;
      const shape = n.kind === "file" || n.kind === "canvas"
        ? `<rect x="${-n.r}" y="${-n.r}" width="${n.r * 2}" height="${n.r * 2}" rx="${n.kind === "canvas" ? 2.5 : 1.5}"/>`
        : `<circle r="${n.r.toFixed(1)}"${n.kind === "note" ? ` style="fill:${colour.of(n)}"` : ""}/>`;
      return `<g class="map-dot ${n.kind === "note" ? "map-node" : `map-${n.kind}`}" data-node="${esc(n.id)}" tabindex="-1" role="button" aria-label="${esc(label)}"><rect class="hit"/>${shape}<text class="quiet"></text></g>`;
    })
    .join("");
  const hidden = m.graph.nodes.length - m.nodes.filter((n) => n.kind === "note").length;
  const sub = `${plural(m.graph.nodes.length, "note")} · ${plural(m.graph.edges.length, "link")} · ${plural(facts.orphans.length, "orphan")} · ${plural(facts.islands, "island")}`;
  return `${pageHead("Map", sub, `<button class="btn" data-action="map-reset">${icon("refresh")} Reset view</button>`)}
    <div class="map-layout">
      <div class="card map-wrap${session.path ? " pathing" : ""}${is3d ? " is-3d" : ""}" id="map-wrap">
        ${is3d ? `<canvas id="map-canvas" tabindex="0" role="img" aria-label="3D note map. Drag to turn it any way, right-drag or Shift-drag to pan, scroll to zoom. Arrow keys move between linked notes, Enter opens, Escape clears." aria-describedby="map-help"></canvas>
        <p id="map-help" class="sr-only">Drag to turn the map in any direction; it keeps turning briefly after a flick. Right-drag or Shift-drag pans, scroll or pinch zooms. Click a dot to select it, double-click or press Enter to open it. Press 2 for the flat map.</p>
        ${hinted3d() ? "" : `<div class="map-hint" id="map-hint">Drag to turn · Right-drag to pan · Scroll to zoom</div>`}`
        : `<svg id="map-svg" aria-label="Note map. Arrow keys move between linked notes, Enter opens, L shows the local map, Escape clears." aria-describedby="map-help">
          <defs><marker id="map-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,1L9,5L0,9z"/></marker></defs>
          <g class="map-edges${settings.arrows ? " arrows" : ""}">${edgeSvg}</g><g class="map-suggest"></g><g class="map-nodes">${nodeSvg}</g><line class="map-link-draft" id="map-link-draft" visibility="hidden"/>
        </svg>
        <p id="map-help" class="sr-only">Click a dot to select it, double-click or press Enter to open it. Drag dots to rearrange, scroll or pinch to zoom. Alt-drag one note onto another to link them. Press 3 for the 3D map.</p>`}
        <div class="map-growth" id="map-growth" aria-live="polite" hidden></div>
        <div class="map-card" id="map-card" hidden></div>
        ${session.local ? localChip(m) : ""}
        ${hidden && !session.local ? `<div class="map-chip">${plural(m.nodes.filter((n) => n.kind === "note").length, "note")} shown of ${m.graph.nodes.length} <button class="link-btn" data-map="clear-filters">Show all</button></div>` : ""}
        <div class="map-zoom" role="group" aria-label="View">
          <span class="segs map-mode" role="group" aria-label="Map view">
            <button class="seg${is3d ? "" : " on"}" data-map="view" data-view="2d" aria-pressed="${!is3d}" title="Flat map (2)">2D</button><button class="seg${is3d ? " on" : ""}" data-map="view" data-view="3d" aria-pressed="${is3d}" title="3D map you can turn any way (3)">3D</button>
          </span>
          ${is3d ? `<button class="btn sm icon-only ghost" data-map="spin" aria-pressed="${settings.spin}" aria-label="Turn slowly by itself" title="Auto-rotate (R)"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v4h-4"/></svg></button>` : ""}
          <button class="btn sm icon-only ghost" data-map="zoom-in" aria-label="Zoom in">${icon("plus")}</button>
          <button class="btn sm icon-only ghost" data-map="zoom-out" aria-label="Zoom out"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 12h14"/></svg></button>
          <button class="btn sm icon-only ghost" data-map="fit" aria-label="Fit to screen"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>
        </div>
        <div class="map-legend">${colour.legend.slice(0, 6).map(([c, label]) => `<span><i class="dot" style="background:${c}"></i>${esc(label)}</span>`).join("")}</div>
      </div>
      <aside class="map-side" aria-label="Map tools">${sidePanel(m)}</aside>
    </div>`;
}

function localChip(m) {
  const center = m.byId.get(session.local);
  const depths = [1, 2, 3]
    .map((d) => `<button class="seg${settings.depth === d ? " on" : ""}" data-map="depth" data-depth="${d}" aria-pressed="${settings.depth === d}">${d}</button>`)
    .join("");
  return `<div class="map-chip local">Local map of <strong>${esc(center?.title || "")}</strong>
    <span class="segs" role="group" aria-label="Depth">${depths}</span>
    <button class="link-btn" data-map="global">Whole map</button></div>`;
}

function sidePanel(m) {
  const typeChips = core.TYPES.map((t) => {
    const on = settings.types.includes(t);
    return `<button class="chip${on ? " on" : ""}" data-map="type" data-type="${t}" aria-pressed="${on}"><i class="dot t-${t}"></i>${TYPE_META[t].label}</button>`;
  }).join("");
  const folders = [...new Set(m.graph.nodes.map((n) => n.folder).filter(Boolean))].sort();
  const folderOptions = [`<option value="">All folders</option>`, ...folders.map((f) => `<option value="${esc(f)}"${settings.folder === f ? " selected" : ""}>${esc(f)}</option>`)].join("");
  const check = (key, label) => `<label class="check"><input type="checkbox" data-map-setting="${key}"${settings[key] ? " checked" : ""}> ${label}</label>`;
  const colourOptions = [["auto", "Automatic"], ["type", "Type"], ["folder", "Folder"], ["cluster", "Cluster"], ["tag", "First tag"]]
    .map(([value, label]) => `<option value="${value}"${settings.colour === value ? " selected" : ""}>${label}</option>`).join("");
  const f = m.facts;
  const hubs = f.hubs.map((h) => `<li><button class="row-link" data-map-select="${esc(h.id)}">${esc(h.title)}</button><span class="hub-count" title="${plural(h.links, "link")}">${h.links}</span></li>`).join("");
  const titles = m.graph.nodes.map((n) => `<option value="${esc(n.title)}">${esc(n.folder || "")}</option>`).join("");
  return `
    <section class="map-section">
      <h3>Filter</h3>
      <input id="map-filter" class="input" type="search" placeholder="Words, #tag, path:folder" value="${esc(settings.query)}" aria-label="Filter notes on the map">
      <div class="chips">${typeChips}</div>
      <select id="map-folder" class="input" aria-label="Folder">${folderOptions}</select>
      <div class="checks">${check("orphans", "Orphans")}${check("unresolved", "Missing notes")}${check("attachments", "Attachments")}${check("canvases", "Canvases")}${check("arrows", "Arrows")}</div>
      <label class="field-row">Colour by <select id="map-colour" class="input" aria-label="Colour by">${colourOptions}</select></label>
    </section>
    <section class="map-section">
      <h3>Insights</h3>
      <dl class="facts">
        <div><dt>Hubs</dt><dd><ul class="hubs">${hubs || '<li class="muted">No links yet</li>'}</ul></dd></div>
        <div><dt>Orphans</dt><dd>${f.orphans.length ? `<button class="link-btn" data-map="orphans">${f.orphans.length} with no links</button>` : "None"}</dd></div>
        <div><dt>Islands</dt><dd>${f.islands ? `${f.islands} groups, largest ${f.largest} notes` : "None"}</dd></div>
        <div><dt>Missing</dt><dd>${f.unresolved ? `${plural(f.unresolved, "link")} to notes that don't exist${settings.unresolved ? "" : ` <button class="link-btn" data-map="show-missing">Show</button>`}` : "None"}</dd></div>
      </dl>
      <div class="map-tools-row">
        <button class="btn sm" data-map="grow" id="map-grow">${icon("play")} Watch it grow</button>
        ${readOnly() ? "" : '<span class="muted small">Tip: <kbd>Alt</kbd>-drag a note onto another to link them.</span>'}
      </div>
    </section>
    <section class="map-section" id="map-suggestions">${suggestionsHtml(m)}</section>
    <section class="map-section">
      <h3>Path between notes</h3>
      <datalist id="map-titles">${titles}</datalist>
      <form id="map-path" class="path-form">
        <input class="input" name="from" list="map-titles" placeholder="From" aria-label="Path from" value="${esc(session.pathEnds[0])}">
        <input class="input" name="to" list="map-titles" placeholder="To" aria-label="Path to" value="${esc(session.pathEnds[1])}">
        <div class="path-actions"><button class="btn primary" type="submit">Find path</button>${session.path || session.pathMessage ? '<button class="btn" type="button" data-map="clear-path">Clear</button>' : ""}</div>
      </form>
      <div id="map-path-result">${pathResultHtml(m)}</div>
    </section>`;
}

function suggestionsHtml(m) {
  const selected = session.selected;
  const fixes = m.graph.unresolved.filter((u) => u.suggestion && (!selected || u.from === selected || u.suggestion === selected));
  const mentions = m.mentions.filter((s) => !selected || s.from === selected || s.to === selected);
  const total = fixes.length + mentions.length;
  const canEdit = !readOnly();
  const title = (path) => esc(noteByPath(path)?.title || path);
  const fixRows = fixes.slice(0, 10).map((u) => {
    const index = m.graph.unresolved.indexOf(u);
    return `<li class="suggestion fix"><div><strong>${title(u.from)}</strong> links to “${esc(u.target)}”, which doesn't exist. Did you mean <button class="row-link" data-map-select="${esc(u.suggestion)}">${title(u.suggestion)}</button>?</div>
      ${canEdit ? `<button class="btn sm" data-map="fix" data-index="${index}">Fix link</button>` : ""}</li>`;
  }).join("");
  const mentionRows = mentions.slice(0, 25).map((s) => {
    const index = m.mentions.indexOf(s);
    return `<li class="suggestion" data-a="${esc(s.from)}" data-b="${esc(s.to)}"><div><button class="row-link" data-map-select="${esc(s.from)}">${title(s.from)}</button> mentions <button class="row-link" data-map-select="${esc(s.to)}">${title(s.to)}</button>${s.count > 1 ? ` <span class="muted">×${s.count}</span>` : ""}<p class="context">${esc(s.context)}</p></div>
      ${canEdit ? `<button class="btn sm" data-map="link" data-index="${index}" aria-label="Link ${title(s.from)} to ${title(s.to)}">Link</button>` : ""}</li>`;
  }).join("");
  const scope = selected ? ` for ${title(selected)}` : "";
  const more = total > fixRows.split("<li").length - 1 + (mentionRows.split("<li").length - 1) ? `<p class="muted small">Showing the strongest; select a note to see its own.</p>` : "";
  return `<h3>Suggested links${scope} <span class="count${total ? "" : " zero"}">${total}</span></h3>
    ${total ? `<ul class="suggestions">${fixRows}${mentionRows}</ul>${more}` : `<p class="muted">${selected ? "No unlinked mentions for this note." : "Every mention is already linked."}</p>`}`;
}

function pathResultHtml(m) {
  if (session.pathMessage) return `<p class="muted">${esc(session.pathMessage)}</p>`;
  if (!session.path) return "";
  const steps = session.path.map((id) => `<li><button class="row-link" data-map-select="${esc(id)}">${esc(m.byId.get(id)?.title || id)}</button></li>`).join("");
  return `<p class="muted">${plural(session.path.length - 1, "step")}</p><ol class="path-steps">${steps}</ol>`;
}

// ------------------------------------------------------------ drawing

let dom = null;
let view3d = null;
const is3d = () => dom?.mode === "3d";
const HINT_KEY = "cairn.map3d.hinted";
function hinted3d() {
  try {
    return localStorage.getItem(HINT_KEY) === "1";
  } catch {
    return true;
  }
}

function scale() {
  const rect = dom.svg.getBoundingClientRect();
  return rect.width ? rect.width / dom.box.w : 1;
}

function setBox() {
  dom.svg.setAttribute("viewBox", `${dom.box.x} ${dom.box.y} ${dom.box.w} ${dom.box.h}`);
}

function fit(ids = null) {
  if (is3d()) return view3d?.fit(ids);
  const m = model;
  const nodes = ids ? m.nodes.filter((n) => ids.includes(n.id)) : m.nodes;
  if (!nodes.length) return;
  const rect = dom.svg.getBoundingClientRect();
  const width = rect.width || 800;
  const height = rect.height || 500;
  let x1 = Infinity;
  let y1 = Infinity;
  let x2 = -Infinity;
  let y2 = -Infinity;
  for (const n of nodes) {
    x1 = Math.min(x1, n.x - n.r);
    y1 = Math.min(y1, n.y - n.r);
    x2 = Math.max(x2, n.x + n.r);
    y2 = Math.max(y2, n.y + n.r);
  }
  let w = Math.max(x2 - x1, 160);
  let h = Math.max(y2 - y1, 120);
  // Leave room for labels around the edge: 70px on screen at the final scale.
  const k = Math.min(width / w, height / h, 1.4);
  const padX = 70 / k;
  const padY = 34 / k;
  w += padX * 2;
  h += padY * 2;
  const aspect = width / height;
  if (w / h < aspect) w = h * aspect;
  else h = w / aspect;
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2;
  dom.box = { x: cx - w / 2, y: cy - h / 2, w, h };
  setBox();
}

function draw() {
  if (is3d()) return;
  const m = model;
  for (const n of m.nodes) dom.nodeEls.get(n.id)?.setAttribute("transform", `translate(${n.x.toFixed(1)} ${n.y.toFixed(1)})`);
  const arrows = settings.arrows;
  for (const [line, a, b] of dom.edgeEls) {
    let { x: x1, y: y1 } = a;
    let { x: x2, y: y2 } = b;
    if (arrows) {
      const dx = x2 - x1;
      const dy = y2 - y1;
      const d = Math.hypot(dx, dy) || 1;
      x2 -= (dx / d) * (b.r + 2);
      y2 -= (dy / d) * (b.r + 2);
      if (line.classList.contains("both")) {
        x1 += (dx / d) * (a.r + 2);
        y1 += (dy / d) * (a.r + 2);
      }
    }
    line.setAttribute("x1", x1.toFixed(1));
    line.setAttribute("y1", y1.toFixed(1));
    line.setAttribute("x2", x2.toFixed(1));
    line.setAttribute("y2", y2.toFixed(1));
  }
  drawSuggestions();
}

// Dashed lines for the selected note's unlinked mentions: links you could make in one click.
function drawSuggestions() {
  if (is3d()) return view3d?.redraw();
  const m = model;
  const id = session.selected;
  const lines = !id ? [] : m.mentions.filter((s) => (s.from === id || s.to === id) && m.byId.has(s.from) && m.byId.has(s.to)).slice(0, 30);
  dom.suggest.innerHTML = lines
    .map((s) => {
      const a = m.byId.get(s.from);
      const b = m.byId.get(s.to);
      return `<line class="map-suggestion" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"/>`;
    })
    .join("");
}

// "Productivity Diary — 2026-10-23" inside the Productivity Diary folder reads as "2026-10-23":
// the folder already says the rest, and the map shows the full title on hover and in the card.
function shortLabel(n) {
  let title = n.title;
  const folder = (n.folder || "").split("/").pop();
  if (folder && title.toLowerCase().startsWith(folder.toLowerCase())) {
    const rest = title.slice(folder.length).replace(/^\s*[—–:\-|·]\s*/, "");
    if (rest && rest !== title.slice(folder.length)) title = rest;
  }
  return title.length > 30 ? `${title.slice(0, 29)}…` : title;
}

// Greedy label placement in screen space: well-connected notes claim room first; each label tries
// below, above, right, then left of its dot and never overlaps another label or dot. Labels without
// room stay hidden until their dot is hovered, focused or selected. Zooming in reveals more.
function placeLabels() {
  if (is3d()) return;
  const m = model;
  const k = scale();
  const font = LABEL_FONT / k;
  const char = LABEL_CHAR / k;
  const pad = 2 / k;
  const view = dom.box;
  const inView = (n) => n.x > view.x - 20 / k && n.x < view.x + view.w + 20 / k && n.y > view.y - 20 / k && n.y < view.y + view.h + 20 / k;
  const boxes = m.nodes.filter(inView).map((n) => ({ x1: n.x - n.r, y1: n.y - n.r, x2: n.x + n.r, y2: n.y + n.r }));
  // Overlays (legend, zoom buttons, chips, the selection card) are no-go areas for labels.
  const svgRect = dom.svg.getBoundingClientRect();
  for (const el of $$(".map-legend, .map-zoom, .map-chip, .map-card:not([hidden])", dom.wrap)) {
    const r = el.getBoundingClientRect();
    if (!r.width || getComputedStyle(el).display === "none") continue;
    boxes.push({
      x1: view.x + (r.left - svgRect.left) / k, y1: view.y + (r.top - svgRect.top) / k,
      x2: view.x + (r.right - svgRect.left) / k, y2: view.y + (r.bottom - svgRect.top) / k,
    });
  }
  const margin = 6 / k;
  const inside = (a) => a.x1 >= view.x + margin && a.x2 <= view.x + view.w - margin && a.y1 >= view.y + margin && a.y2 <= view.y + view.h - margin;
  const overlaps = (a) => !inside(a) || boxes.some((b) => a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2);
  const focusId = session.hover || session.selected;
  const near = new Set(focusId ? model.adj.get(focusId) || [] : []);
  const priority = (n) => (n.id === focusId ? 1e6 : 0) + (session.path?.includes(n.id) ? 1e5 : 0) + (near.has(n.id) ? 1e4 : 0) + m.degree(n.id);
  const order = [...m.nodes].sort((a, b) => priority(b) - priority(a) || a.title.localeCompare(b.title));
  for (const n of order) {
    const text = dom.nodeEls.get(n.id)?.querySelector("text");
    if (!text) continue;
    const label = shortLabel(n);
    if (text.textContent !== label) text.textContent = label;
    text.setAttribute("font-size", font.toFixed(3));
    // Invisible hit area: at least 34 screen px across on phones, the dot itself on desktop.
    const hit = text.previousElementSibling?.previousElementSibling;
    if (hit?.classList.contains("hit")) {
      const half = innerWidth <= 900 ? Math.max(n.r, 17 / k) : n.r;
      hit.setAttribute("x", (-half).toFixed(2));
      hit.setAttribute("y", (-half).toFixed(2));
      hit.setAttribute("width", (half * 2).toFixed(2));
      hit.setAttribute("height", (half * 2).toFixed(2));
    }
    const w = label.length * char * (n.id === session.selected ? 1.1 : 1);
    const r = n.r;
    const gap = 3 / k;
    const lh = 14 / k;
    const candidates = [
      { x: 0, y: r + gap + font * 0.95, anchor: "middle", box: [-w / 2, r + gap, w / 2, r + gap + lh] },
      { x: 0, y: -(r + gap + font * 0.3), anchor: "middle", box: [-w / 2, -(r + gap + lh), w / 2, -(r + gap)] },
      { x: r + gap * 2, y: font * 0.35, anchor: "start", box: [r + gap * 2, -lh / 2, r + gap * 2 + w, lh / 2] },
      { x: -(r + gap * 2), y: font * 0.35, anchor: "end", box: [-(r + gap * 2) - w, -lh / 2, -(r + gap * 2), lh / 2] },
    ];
    let spot = null;
    if (inView(n)) {
      for (const c of candidates) {
        const claim = { x1: n.x + c.box[0] - pad, y1: n.y + c.box[1] - pad, x2: n.x + c.box[2] + pad, y2: n.y + c.box[3] + pad };
        if (!overlaps(claim)) {
          boxes.push(claim);
          spot = c;
          break;
        }
      }
    }
    const use = spot || candidates[0];
    text.setAttribute("x", use.x.toFixed(2));
    text.setAttribute("y", use.y.toFixed(2));
    text.setAttribute("text-anchor", use.anchor);
    text.classList.toggle("quiet", !spot);
  }
}

// ------------------------------------------------------------ selection and highlighting

function neighboursOf(id) {
  return [...(model.adj.get(id) || [])].filter((other) => model.byId.has(other));
}

function highlight(id, cls = "lit") {
  if (is3d()) return view3d?.redraw();
  $$(`.${cls}`, dom.svg).forEach((el) => el.classList.remove(cls));
  if (!id) return;
  dom.nodeEls.get(id)?.classList.add(cls);
  for (const [line] of dom.edgeEls) {
    const { a, b } = line.dataset;
    if (a === id || b === id) {
      line.classList.add(cls);
      dom.nodeEls.get(a === id ? b : a)?.classList.add(cls);
    }
  }
}

function applyFocus() {
  const id = session.hover || session.selected;
  if (is3d()) {
    dom.wrap.classList.toggle("focus", Boolean(id) && !session.path);
    return view3d?.redraw();
  }
  dom.wrap.classList.toggle("focus", Boolean(id) && !session.path);
  highlight(id);
  if (!frame) scheduleLabels();
}

function applyPath() {
  if (is3d()) return view3d?.redraw();
  $$(".on-path", dom.svg).forEach((el) => el.classList.remove("on-path"));
  if (!session.path) return;
  const steps = new Set(session.path.map((id, i) => (i ? `${session.path[i - 1]}\u0000${id}` : "")));
  session.path.forEach((id) => dom.nodeEls.get(id)?.classList.add("on-path"));
  for (const [line] of dom.edgeEls) {
    const { a, b } = line.dataset;
    if (steps.has(`${a}\u0000${b}`) || steps.has(`${b}\u0000${a}`)) line.classList.add("on-path");
  }
}

function select(id, { focus = true, centre = false } = {}) {
  session.selected = id && model.byId.has(id) ? id : null;
  if (is3d()) {
    if (session.selected && centre) view3d?.focusOn(session.selected);
    applyFocus();
    renderCard();
    const panel3d = $("#map-suggestions");
    if (panel3d) panel3d.innerHTML = suggestionsHtml(model);
    return;
  }
  $$(".selected", dom.svg).forEach((el) => el.classList.remove("selected"));
  for (const [nodeId, el] of dom.nodeEls) el.setAttribute("tabindex", nodeId === session.selected ? "0" : "-1");
  if (!session.selected) {
    dom.nodeEls.values().next().value?.setAttribute("tabindex", "0");
  } else {
    const el = dom.nodeEls.get(session.selected);
    el.classList.add("selected");
    if (focus) el.focus({ preventScroll: true });
    if (centre) centreOn(model.byId.get(session.selected));
  }
  applyFocus();
  renderCard();
  drawSuggestions();
  const panel = $("#map-suggestions");
  if (panel) panel.innerHTML = suggestionsHtml(model);
  placeLabels();
}

function centreOn(node) {
  if (is3d()) return view3d?.focusOn(node.id);
  const box = dom.box;
  const visible = node.x > box.x + box.w * 0.15 && node.x < box.x + box.w * 0.85 && node.y > box.y + box.h * 0.15 && node.y < box.y + box.h * 0.85;
  if (visible) return;
  dom.box = { ...box, x: node.x - box.w / 2, y: node.y - box.h / 2 };
  setBox();
}

function renderCard() {
  const card = dom.card;
  const id = session.selected;
  const node = id && model.byId.get(id);
  if (!node) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  if (node.kind !== "note") {
    const fix = model.graph.unresolved.find((u) => node.kind === "ghost" && u.target.toLowerCase() === node.title.toLowerCase() && u.suggestion);
    card.innerHTML = `<div class="map-card-head"><strong>${esc(node.title)}</strong><button class="btn sm icon-only ghost" data-map="deselect" aria-label="Clear selection">${icon("x")}</button></div>
      <p class="muted">${node.kind === "ghost" ? "Linked to, but this note doesn't exist yet." : node.kind === "canvas" ? `A canvas that holds ${model.edges.filter((e) => e.source === node.id).length} of the notes shown here.` : "An attachment (image or file) embedded in a note."}</p>
      ${node.kind === "canvas" ? `<div class="map-card-actions"><button class="btn primary sm" data-map="open-canvas">Open canvas</button></div>` : ""}
      ${fix ? `<p>Probably means <button class="row-link" data-map-select="${esc(fix.suggestion)}">${esc(noteByPath(fix.suggestion)?.title || fix.suggestion)}</button>.</p>` : ""}
      ${node.kind === "ghost" && !readOnly() ? `<div class="map-card-actions"><button class="btn primary sm" data-map="create">Create this note</button></div>` : ""}`;
    return;
  }
  const note = noteByPath(id);
  const out = model.graph.edges.filter((e) => (e.source === id) || (e.both && e.target === id)).length;
  const back = model.graph.edges.filter((e) => e.target === id || (e.both && e.source === id)).length;
  const isLocal = session.local === id;
  card.innerHTML = `<div class="map-card-head"><span class="dot" style="background:${model.colour.of(node)}"></span><strong>${esc(node.title)}</strong><button class="btn sm icon-only ghost" data-map="deselect" aria-label="Clear selection">${icon("x")}</button></div>
    <p class="muted small">${esc(node.id)}</p>
    <p class="small">${plural(out, "outgoing link")} · ${plural(back, "backlink")}${note?.tags?.length ? ` · ${note.tags.slice(0, 4).map((t) => `#${esc(t)}`).join(" ")}` : ""}</p>
    <div class="map-card-actions">
      <button class="btn primary sm" data-open="${esc(id)}">Open</button>
      <button class="btn sm" data-map="local" aria-pressed="${isLocal}">${isLocal ? "Whole map" : "Local map"}</button>
      <button class="btn sm" data-map="path-from">Path from here</button>
    </div>`;
}

// Arrow keys walk to the linked note that lies most in that direction (or the nearest dot that way).
function step(direction) {
  const m = model;
  const from = m.byId.get(session.selected);
  if (!from) {
    const hub = m.facts.hubs.find((h) => m.byId.has(h.id))?.id || m.nodes[0]?.id;
    return select(hub, { centre: true });
  }
  const [vx, vy] = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[direction];
  // In 3D, "right" means right on screen from the current angle.
  const at = is3d() ? (n) => view3d.screenPos(n.id) || { x: Infinity, y: Infinity } : (n) => n;
  const origin = at(from);
  const score = (n) => {
    const p = at(n);
    const dx = p.x - origin.x;
    const dy = p.y - origin.y;
    const d = Math.hypot(dx, dy) || 1;
    const cos = (dx * vx + dy * vy) / d;
    return cos < 0.35 ? -Infinity : cos * 2 - d / 600;
  };
  const pick = (list) => list.map((n) => [n, score(n)]).filter(([, s]) => s > -Infinity).sort((a, b) => b[1] - a[1])[0]?.[0];
  const next = pick(neighboursOf(from.id).map((id) => m.byId.get(id))) || pick(m.nodes.filter((n) => n !== from));
  if (next) select(next.id, { centre: true });
}

// ------------------------------------------------------------ actions

async function acceptMention(index) {
  const s = model.mentions[index];
  const note = s && noteByPath(s.from);
  if (!note) return;
  const linkText = G.linkTextFor(state.notes, s.from, s.to);
  const body = G.linkMention(note.body, s.phrase, linkText);
  if (!body) return toast("That mention moved; nothing changed.", { error: true });
  const before = note.body;
  const saved = await saveNote(s.from, { body });
  if (!saved) return;
  renderView();
  toast(`Linked “${s.phrase}” in ${note.title}.`, {
    action: {
      label: "Undo",
      run: async () => {
        await saveNote(s.from, { body: before });
        renderView();
      },
    },
  });
}

async function fixLink(index) {
  const u = model.graph.unresolved[index];
  const note = u && noteByPath(u.from);
  if (!note || !u.suggestion) return;
  const linkText = G.linkTextFor(state.notes, u.from, u.suggestion);
  const body = G.retargetLink(note.body, u.target, linkText);
  if (!body) return toast("That link is in the note's properties; edit it in the note.", { error: true });
  const before = note.body;
  const saved = await saveNote(u.from, { body });
  if (!saved) return;
  renderView();
  toast(`Fixed the link in ${note.title}.`, {
    action: {
      label: "Undo",
      run: async () => {
        await saveNote(u.from, { body: before });
        renderView();
      },
    },
  });
}

// Alt-drag one note onto another: writes [[link]] at the end of the first note (undoable).
async function linkNotes(fromId, toId) {
  const from = noteByPath(fromId);
  const to = noteByPath(toId);
  if (!from || !to || fromId === toId || readOnly()) return;
  const index = core.linkIndex(state.notes);
  if (core.noteLinks(from).some(({ target }) => core.resolveTarget(index, target, fromId) === toId)) {
    toast(`“${from.title}” already links to “${to.title}”.`);
    return;
  }
  const before = from.body;
  const body = `${before.replace(/\s*$/, "")}\n\n[[${G.linkTextFor(state.notes, fromId, toId)}]]\n`;
  const saved = await saveNote(fromId, { body });
  if (!saved) return;
  session.selected = fromId;
  renderView();
  toast(`Linked “${from.title}” → “${to.title}”.`, {
    action: {
      label: "Undo",
      run: async () => {
        await saveNote(fromId, { body: before });
        renderView();
      },
    },
  });
}

// A missing note made real, named the way the link names it so every link to it resolves at once.
// Cairn-style brains get a regular Cairn note; vaults get a plain Markdown file, like Obsidian makes.
async function createMissing(id) {
  const node = id && model.byId.get(id);
  if (!node || node.kind !== "ghost" || readOnly()) return;
  const link = model.graph.unresolved.find((u) => `?${u.target.toLowerCase()}` === id);
  const target = (link?.target || node.title).replace(/\.md$/i, "").replace(/^\/+/, "").trim();
  if (!target) return;
  const cairnStyle = state.files.some((f) => /^(ideas|tasks|decisions|notes|journal)\//.test(f.path));
  let path;
  if (cairnStyle && !target.includes("/")) {
    // [[elevation-profile-chart]] becomes "Elevation profile chart" in notes/elevation-profile-chart.md.
    const title = /^[a-z0-9]+(-[a-z0-9]+)+$/.test(target) ? target.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase()) : target;
    path = (await createNote({ type: "note", title }))?.path;
  } else {
    path = `${target}.md`;
    if (state.files.some((f) => f.path.toLowerCase() === path.toLowerCase())) {
      toast(`${path} already exists.`, { error: true });
      return;
    }
    const text = `# ${target.split("/").pop()}\n`;
    const { mtime } = await state.store.write(path, text, null);
    upsertFile(path, text, mtime);
  }
  if (!path) return;
  const spot = positions.get(id);
  if (spot) positions.set(path, spot);
  session.selected = path;
  renderView();
  toast(`Created ${path}.`, { action: { label: "Open", run: () => openNote(path) } });
}

// ------------------------------------------------------------ growth time-lapse

// When a note was born: its created/date property, a date in its file name, or when it was last saved.
function noteTime(id) {
  const note = noteByPath(id);
  const meta = note?.meta || {};
  for (const key of ["created", "date"]) {
    const value = typeof meta[key] === "string" ? Date.parse(meta[key]) : NaN;
    if (!Number.isNaN(value)) return value;
  }
  const day = /(\d{4}-\d{2}-\d{2})/.exec(id.split("/").pop());
  const fromName = day ? Date.parse(day[1]) : NaN;
  return Number.isNaN(fromName) ? note?.mtime || 0 : fromName;
}

let growth = null;

function stopGrowth() {
  if (!growth) return;
  clearInterval(growth.timer);
  growth = null;
  session.future = null;
  view3d?.redraw();
  if (!dom) return;
  $$(".future", dom.svg || document).forEach((el) => el.classList.remove("future"));
  dom.wrap.classList.remove("growing");
  const caption = $("#map-growth");
  if (caption) caption.hidden = true;
  const button = $("#map-grow");
  if (button) button.innerHTML = `${icon("play")} Watch it grow`;
}

// Replays the map in the order notes were written: dots and links appear as the brain grew.
function toggleGrowth() {
  if (growth) return stopGrowth();
  const order = model.nodes
    .filter((n) => n.kind === "note")
    .map((n) => ({ id: n.id, time: noteTime(n.id) }))
    .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));
  if (order.length < 2) return;
  select(null, { focus: false });
  const notes = new Set(order.map((o) => o.id));
  const shown = new Set();
  for (const el of dom.nodeEls.values()) el.classList.add("future");
  for (const [line] of dom.edgeEls) line.classList.add("future");
  // In 3D the renderer hides the notes still to come.
  if (is3d()) session.future = new Set(notes);
  dom.wrap.classList.add("growing");
  const caption = $("#map-growth");
  caption.hidden = false;
  $("#map-grow").innerHTML = `${icon("pause")} Stop`;
  const steps = Math.min(order.length, 90);
  let revealed = 0;
  let step = 0;
  growth = {
    timer: setInterval(() => {
      step += 1;
      const until = Math.ceil((step / steps) * order.length);
      for (; revealed < until; revealed += 1) {
        shown.add(order[revealed].id);
        session.future?.delete(order[revealed].id);
        dom.nodeEls.get(order[revealed].id)?.classList.remove("future");
      }
      view3d?.redraw();
      for (const [line] of dom.edgeEls) {
        const { a, b } = line.dataset;
        const aIn = shown.has(a) || !notes.has(a);
        const bIn = shown.has(b) || !notes.has(b);
        if (aIn && bIn && (shown.has(a) || shown.has(b))) {
          line.classList.remove("future");
          dom.nodeEls.get(a)?.classList.remove("future");
          dom.nodeEls.get(b)?.classList.remove("future");
        }
      }
      const last = order[revealed - 1];
      const when = last.time ? new Date(last.time).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
      caption.textContent = `${when} · ${plural(revealed, "note")}`;
      if (revealed >= order.length) {
        clearInterval(growth.timer);
        setTimeout(stopGrowth, 1600);
      }
    }, 6500 / steps),
  };
}

function resolveTitle(text) {
  const wanted = text.trim().toLowerCase();
  if (!wanted) return null;
  const nodes = model.graph.nodes;
  return (nodes.find((n) => n.title.toLowerCase() === wanted) || nodes.find((n) => n.id.toLowerCase() === wanted) || nodes.find((n) => n.title.toLowerCase().includes(wanted)))?.id || null;
}

function findPath(fromText, toText) {
  session.pathEnds = [fromText, toText];
  session.pathMessage = "";
  session.path = null;
  const from = resolveTitle(fromText);
  const to = resolveTitle(toText);
  if (!from || !to) {
    session.pathMessage = `No note called “${!from ? fromText : toText}”.`;
  } else {
    const path = G.shortestPath(model.adj, from, to);
    if (path) session.path = path;
    else session.pathMessage = "No route: these notes are on separate islands. A link between them would join the two.";
  }
  renderView();
  if (session.path) fit(session.path);
}

export function resetMap() {
  session.selected = null;
  session.local = null;
  session.path = null;
  session.pathMessage = "";
  positions.clear();
  reset3D();
  lastSignature = "";
  renderView();
}

const mapActions = {
  "zoom-in": () => zoomBy(1 / 1.3),
  "zoom-out": () => zoomBy(1.3),
  fit: () => {
    fit(session.path || null);
    placeLabels();
  },
  deselect: () => select(null, { focus: false }),
  "open-canvas": () => session.selected && openCanvasNode(session.selected),
  local: () => {
    session.local = session.local === session.selected ? null : session.selected;
    renderView();
  },
  global: () => {
    session.local = null;
    renderView();
  },
  depth: (el) => {
    saveSettings({ depth: Number(el.dataset.depth) });
    renderView();
  },
  "path-from": () => {
    session.pathEnds = [model.byId.get(session.selected)?.title || "", session.pathEnds[1]];
    const input = $('#map-path [name="from"]');
    if (input) input.value = session.pathEnds[0];
    $('#map-path [name="to"]')?.focus();
  },
  "clear-path": () => {
    session.path = null;
    session.pathMessage = "";
    session.pathEnds = ["", ""];
    renderView();
  },
  "clear-filters": () => {
    saveSettings({ query: "", types: [], folder: "", orphans: true });
    renderView();
  },
  orphans: () => {
    saveSettings({ query: "is:orphan", orphans: true });
    renderView();
  },
  type: (el) => {
    const type = el.dataset.type;
    saveSettings({ types: settings.types.includes(type) ? settings.types.filter((t) => t !== type) : [...settings.types, type] });
    renderView();
  },
  view: (el) => setView(el.dataset.view),
  spin: () => view3d?.setSpin(!view3d.spinning),
  link: (el) => acceptMention(Number(el.dataset.index)),
  create: () => createMissing(session.selected),
  grow: () => toggleGrowth(),
  "show-missing": () => {
    saveSettings({ unresolved: true });
    renderView();
  },
  fix: (el) => fixLink(Number(el.dataset.index)),
};

function setView(view) {
  if (view !== "2d" && view !== "3d") return;
  if (view === settings.view) return;
  saveSettings({ view });
  renderView();
  (view === "3d" ? $("#map-canvas") : $('#map-svg .map-dot[tabindex="0"]'))?.focus({ preventScroll: true });
}

function zoomBy(factor, cx = null, cy = null) {
  if (is3d()) return view3d?.zoom(factor);
  const box = dom.box;
  const mx = cx ?? box.x + box.w / 2;
  const my = cy ?? box.y + box.h / 2;
  dom.box = { x: mx - (mx - box.x) * factor, y: my - (my - box.y) * factor, w: box.w * factor, h: box.h * factor };
  setBox();
  scheduleLabels();
}

let labelTimer = null;
function scheduleLabels() {
  clearTimeout(labelTimer);
  labelTimer = setTimeout(() => dom && placeLabels(), 90);
}

// ------------------------------------------------------------ animation (dragging re-heats the layout)

let frame = null;
function animate() {
  if (frame || is3d()) return;
  dom.wrap.classList.add("moving");
  const loop = () => {
    if (!dom || !model) {
      frame = null;
      return;
    }
    model.sim.tick();
    draw();
    if (model.sim.settled()) {
      frame = null;
      dom.wrap.classList.remove("moving");
      savePositions(model);
      placeLabels();
      return;
    }
    frame = requestAnimationFrame(loop);
  };
  frame = requestAnimationFrame(loop);
}

// ------------------------------------------------------------ wiring

export function wireMap() {
  view3d?.destroy();
  view3d = null;
  if (frame) cancelAnimationFrame(frame);
  frame = null;
  if (growth) {
    clearInterval(growth.timer);
    growth = null;
  }
  session.future = null;
  const wrap = $("#map-wrap");
  if (!wrap || !model) return;
  if (settings.view === "3d" && $("#map-canvas")) wire3d(wrap);
  else wire2d(wrap);
  wirePanel(wrap);
}

function wire3d(wrap) {
  const canvas = $("#map-canvas");
  dom = { mode: "3d", wrap, card: $("#map-card"), nodeEls: new Map(), edgeEls: [] };
  view3d = createView3D({
    canvas,
    model,
    session,
    colourOf: (n) => model.colour.of(n),
    label: shortLabel,
    arrows: settings.arrows,
    spin: settings.spin,
    onSelect: (id) => select(id, { focus: false }),
    onOpen: (id) => {
      if (model.byId.get(id)?.kind === "note") openNote(id);
      else if (model.byId.get(id)?.kind === "canvas") openCanvasNode(id);
    },
    onHover: (id) => {
      session.hover = id;
      applyFocus();
    },
    onSpinChange: (on) => {
      saveSettings({ spin: on });
      $('[data-map="spin"]')?.setAttribute("aria-pressed", String(on));
    },
    onInteract: dismissHint,
  });
  if (window.cairn) window.cairn.map3d = view3d;
  select(session.selected, { focus: false });
}

function dismissHint() {
  const hint = $("#map-hint");
  if (!hint) return;
  hint.classList.add("gone");
  setTimeout(() => hint.remove(), 400);
  try {
    localStorage.setItem(HINT_KEY, "1");
  } catch {
    // Private mode: the hint simply shows again next time.
  }
}

function wire2d(wrap) {
  const svg = $("#map-svg");
  if (!svg) return;
  if (window.cairn) window.cairn.map3d = null;
  const nodeEls = new Map($$(".map-dot", svg).map((el) => [el.dataset.node, el]));
  const edgeEls = $$(".map-edge", svg).map((line) => [line, model.byId.get(line.dataset.a), model.byId.get(line.dataset.b)]);
  dom = { svg, wrap, nodeEls, edgeEls, suggest: $(".map-suggest", svg), draft: $("#map-link-draft"), card: $("#map-card"), box: { x: -400, y: -300, w: 800, h: 600 } };
  draw();
  fit(session.local ? null : session.path || null);
  applyPath();
  select(session.selected, { focus: false });
  if (!model.sim.settled()) animate();

  // Hover shows a note's neighbours without changing the selection.
  svg.addEventListener("pointerover", (event) => {
    const el = event.target.closest(".map-dot");
    if (!el || drag) return;
    session.hover = el.dataset.node;
    applyFocus();
  });
  svg.addEventListener("pointerout", (event) => {
    const el = event.target.closest(".map-dot");
    if (!el || (event.relatedTarget && el.contains(event.relatedTarget))) return;
    session.hover = null;
    applyFocus();
  });
  svg.addEventListener("focusin", (event) => {
    const el = event.target.closest(".map-dot");
    if (el && el.dataset.node !== session.selected) select(el.dataset.node, { focus: false });
  });

  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = svg.getBoundingClientRect();
    const mx = dom.box.x + ((event.clientX - rect.left) / rect.width) * dom.box.w;
    const my = dom.box.y + ((event.clientY - rect.top) / rect.height) * dom.box.h;
    zoomBy(event.deltaY > 0 ? 1.12 : 1 / 1.12, mx, my);
  }, { passive: false });

  // One pointer on the background pans, one on a dot drags it, two pinch-zoom.
  const pointers = new Map();
  let drag = null;
  let pinch = null;
  const toWorld = (event) => {
    const rect = svg.getBoundingClientRect();
    return { x: dom.box.x + ((event.clientX - rect.left) / rect.width) * dom.box.w, y: dom.box.y + ((event.clientY - rect.top) / rect.height) * dom.box.h };
  };
  svg.addEventListener("pointerdown", (event) => {
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), box: { ...dom.box } };
      drag = null;
      return;
    }
    const el = event.target.closest(".map-dot");
    if (el && event.altKey && el.classList.contains("map-node") && !readOnly()) {
      event.preventDefault();
      drag = { kind: "link", id: el.dataset.node, x: event.clientX, y: event.clientY, moved: false, over: null };
      dom.wrap.classList.add("linking");
      return;
    }
    drag = el
      ? { kind: "node", id: el.dataset.node, x: event.clientX, y: event.clientY, moved: false }
      : { kind: "pan", x: event.clientX, y: event.clientY, box: { ...dom.box }, moved: false };
    if (!el) svg.classList.add("panning");
  });
  svg.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const factor = pinch.d / Math.max(20, Math.hypot(a.x - b.x, a.y - b.y));
      const cx = pinch.box.x + pinch.box.w / 2;
      const cy = pinch.box.y + pinch.box.h / 2;
      dom.box = { x: cx - (pinch.box.w * factor) / 2, y: cy - (pinch.box.h * factor) / 2, w: pinch.box.w * factor, h: pinch.box.h * factor };
      setBox();
      return;
    }
    if (!drag) return;
    const moved = Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 4;
    if (!drag.moved && !moved) return;
    if (!drag.moved) svg.setPointerCapture(event.pointerId);
    drag.moved = true;
    if (drag.kind === "link") {
      const from = model.byId.get(drag.id);
      const p = toWorld(event);
      dom.draft.setAttribute("x1", from.x.toFixed(1));
      dom.draft.setAttribute("y1", from.y.toFixed(1));
      dom.draft.setAttribute("x2", p.x.toFixed(1));
      dom.draft.setAttribute("y2", p.y.toFixed(1));
      dom.draft.setAttribute("visibility", "visible");
      const under = document.elementFromPoint(event.clientX, event.clientY)?.closest(".map-node");
      const over = under && under.dataset.node !== drag.id ? under.dataset.node : null;
      if (over !== drag.over) {
        if (drag.over) dom.nodeEls.get(drag.over)?.classList.remove("link-target");
        if (over) dom.nodeEls.get(over)?.classList.add("link-target");
        drag.over = over;
      }
      return;
    }
    if (drag.kind === "pan") {
      const rect = svg.getBoundingClientRect();
      dom.box = { ...drag.box, x: drag.box.x - ((event.clientX - drag.x) / rect.width) * drag.box.w, y: drag.box.y - ((event.clientY - drag.y) / rect.height) * drag.box.h };
      setBox();
    } else {
      const node = model.byId.get(drag.id);
      const p = toWorld(event);
      node.fx = p.x;
      node.fy = p.y;
      model.sim.alphaTarget = 0.25;
      if (model.sim.alpha < 0.25) model.sim.alpha = 0.25;
      session.hover = drag.id;
      applyFocus();
      animate();
    }
  });
  const end = (event) => {
    pointers.delete(event.pointerId);
    if (pinch) {
      if (pointers.size < 2) {
        pinch = null;
        scheduleLabels();
      }
      return;
    }
    svg.classList.remove("panning");
    if (!drag) return;
    const done = drag;
    drag = null;
    if (done.kind === "link") {
      dom.draft.setAttribute("visibility", "hidden");
      dom.wrap.classList.remove("linking");
      if (done.over) dom.nodeEls.get(done.over)?.classList.remove("link-target");
      if (done.over && event.type === "pointerup") linkNotes(done.id, done.over);
      else if (!done.moved && event.type === "pointerup") select(done.id);
      return;
    }
    if (done.kind === "pan") {
      if (done.moved) scheduleLabels();
      else if (event.type === "pointerup") select(null, { focus: false });
      return;
    }
    const node = model.byId.get(done.id);
    if (done.moved) {
      node.fx = null;
      node.fy = null;
      model.sim.alphaTarget = 0;
      session.hover = null;
      animate();
    } else if (event.type === "pointerup") {
      select(done.id);
    }
  };
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
  svg.addEventListener("dblclick", (event) => {
    const el = event.target.closest(".map-node");
    if (el) openNote(el.dataset.node);
    const board = event.target.closest(".map-canvas");
    if (board) openCanvasNode(board.dataset.node);
    const ghost = event.target.closest(".map-ghost");
    if (ghost) createMissing(ghost.dataset.node);
  });
}

// Keyboard, toolbar, side panel: the same in 2D and 3D.
function wirePanel(wrap) {
  wrap.addEventListener("keydown", (event) => {
    if (event.target.closest("input, select, textarea, button:not(.map-dot)")) return;
    const key = event.key;
    if (is3d() && event.shiftKey && key.startsWith("Arrow")) {
      // Shift+arrows turn the 3D map 15° at a time.
      event.preventDefault();
      const turn = Math.PI / 12;
      const [yaw, pitch] = { ArrowLeft: [turn, 0], ArrowRight: [-turn, 0], ArrowUp: [0, -turn], ArrowDown: [0, turn] }[key];
      view3d.rotateBy(yaw, pitch);
    } else if (key === "2" || key === "3") {
      setView(`${key}d`);
    } else if (is3d() && key.toLowerCase() === "r" && !event.ctrlKey && !event.metaKey) {
      view3d.setSpin(!view3d.spinning);
    } else if (key.startsWith("Arrow")) {
      event.preventDefault();
      step(key);
    } else if (key === "Enter" || key === " ") {
      if (session.selected && model.byId.get(session.selected)?.kind === "note") {
        event.preventDefault();
        openNote(session.selected);
      } else if (session.selected && model.byId.get(session.selected)?.kind === "canvas") {
        event.preventDefault();
        openCanvasNode(session.selected);
      }
    } else if (key === "Escape") {
      if (session.path) mapActions["clear-path"]();
      else if (session.local) mapActions.global();
      else if (session.selected) select(null, { focus: false });
      else return;
      event.stopPropagation();
    } else if (key.toLowerCase() === "l" && session.selected) {
      mapActions.local();
    } else if (key.toLowerCase() === "f") {
      mapActions.fit();
    } else if (key === "+" || key === "=") {
      zoomBy(1 / 1.3);
    } else if (key === "-") {
      zoomBy(1.3);
    }
  });

  // Toolbar, card and side panel buttons are handled by one delegated listener (their HTML is
  // re-rendered when the selection changes).
  if (!delegated) {
    $("#view").addEventListener("click", onDelegatedClick);
    delegated = true;
  }
  const filter = $("#map-filter");
  let filterTimer = null;
  filter?.addEventListener("input", () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      saveSettings({ query: filter.value });
      renderView();
      const again = $("#map-filter");
      again?.focus();
      again?.setSelectionRange(again.value.length, again.value.length);
    }, 220);
  });
  $("#map-folder")?.addEventListener("change", (event) => {
    saveSettings({ folder: event.target.value });
    renderView();
  });
  $("#map-colour")?.addEventListener("change", (event) => {
    saveSettings({ colour: event.target.value });
    renderView();
  });
  $$("[data-map-setting]").forEach((input) => input.addEventListener("change", () => {
    saveSettings({ [input.dataset.mapSetting]: input.checked });
    renderView();
  }));
  $("#map-path")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    findPath(String(data.get("from") || ""), String(data.get("to") || ""));
  });
  // Hovering a suggestion shows where that link would go.
  $("#map-suggestions")?.addEventListener("pointerover", (event) => {
    const row = event.target.closest(".suggestion[data-a]");
    if (!row) return;
    session.hover = row.dataset.a;
    session.alsoLit = row.dataset.b;
    applyFocus();
    dom.nodeEls.get(row.dataset.b)?.classList.add("lit");
  });
  $("#map-suggestions")?.addEventListener("pointerleave", () => {
    session.hover = null;
    session.alsoLit = null;
    applyFocus();
  });
}

let delegated = false;

function onDelegatedClick(event) {
  if (state.view !== "map" || !dom || !model) return;
  const pick = event.target.closest("[data-map-select]");
  if (pick) {
    event.preventDefault();
    const id = pick.dataset.mapSelect;
    if (!model.byId.has(id)) {
      // The note is filtered out: clear filters so it can be shown, then select it.
      session.selected = id;
      session.local = null;
      saveSettings({ query: "", types: [], folder: "", orphans: true });
      renderView();
    }
    select(id, { centre: true });
    return;
  }
  const action = event.target.closest("[data-map]");
  if (action) {
    event.preventDefault();
    mapActions[action.dataset.map]?.(action);
  }
}

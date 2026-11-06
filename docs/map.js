// The Map view: a live, explorable graph of every note and link.
//
// Beyond a picture of links it is a working surface:
//   live physics (drag a note and it stays pinned where you drop it), zoom, pan, pinch
//   hover a note -> neighbours light up, links show direction with moving particles, preview card
//   click -> inspector with summary, links in and out, open; double-click opens the note
//   local graph (1-3 hops), find a note, colour by type / folder / cluster / status
//   Shift-click two notes -> shortest path between them
//   Alt-drag (or Link mode) from one note onto another -> writes a [[link]] into the first
//   "unwritten" notes: [[links]] to notes that don't exist yet, one click creates them
//   tag hubs, orphan filter, a time-lapse of how the brain grew, force sliders
// Everything is drawn on a canvas so large Obsidian vaults stay smooth; a hidden list of buttons keeps
// every note reachable by keyboard and screen readers.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { $, $$, STATUS_LABEL, TYPE_META, emptyState, icon, pageHead, plural, toast } from "./ui.js";
import { createNote, noteByPath, readOnly, saveNote, state } from "./state.js";
import { openNote } from "./drawer.js";
import { renderNav, renderView } from "./app.js";
import { Simulation, adjacency, buildMapGraph, clusters, neighbourhood, shortestPath } from "./graph.js";

const SETTINGS_KEY = "cairn.map.v2";
const DEFAULTS = {
  colorBy: "type",
  ghosts: true,
  tags: false,
  orphans: true,
  local: false,
  depth: 1,
  hiddenTypes: [],
  repel: 1,
  linkDistance: 1,
  gravity: 1,
  labels: 1,
};
const STATUS_COLORS = {
  raw: "--muted", exploring: "--idea", ready: "--journal", parked: "--line-strong",
  todo: "--task", doing: "--accent", done: "--journal",
  proposed: "--decision", accepted: "--journal", superseded: "--line-strong",
};
const EXTRA_PALETTE = ["#c0567a", "#5f8f3a", "#3f8f9b", "#9c5bb5", "#b8662e", "#4b6cb7", "#8a8a2e", "#a34848"];
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

function loadSettings() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
  } catch {
    return { ...DEFAULTS };
  }
}

// Survives re-renders: positions, camera, selection and settings.
const ui = {
  settings: loadSettings(),
  positions: new Map(),
  camera: null,
  selected: null,
  path: [],
  query: "",
  linkMode: false,
  settingsOpen: false,
};
let live = null;

function saveSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(ui.settings));
}

// ============================================================ model

function model() {
  const s = ui.settings;
  const graph = buildMapGraph(state.notes, { ghosts: s.ghosts, tags: s.tags });
  const fullAdj = adjacency(graph);
  const groups = clusters(graph);
  const hidden = new Set(s.hiddenTypes);
  let visible = graph.nodes.filter((n) => {
    if (n.kind === "note" && hidden.has(n.type)) return false;
    if (n.kind === "note" && !s.orphans && !fullAdj.get(n.id).size) return false;
    return true;
  });
  let ids = new Set(visible.map((n) => n.id));
  // Ghost and tag nodes only make sense next to a visible note.
  const edgesVisible = () => graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const attached = new Set(edgesVisible().flatMap((e) => [e.source, e.target]));
  visible = visible.filter((n) => n.kind === "note" || attached.has(n.id));
  ids = new Set(visible.map((n) => n.id));
  if (s.local && ui.selected && ids.has(ui.selected)) {
    const near = neighbourhood(adjacency({ nodes: visible, edges: edgesVisible() }), ui.selected, s.depth);
    visible = visible.filter((n) => near.has(n.id));
    ids = near;
  }
  const edges = edgesVisible();
  return { graph, visible, edges, adj: adjacency({ nodes: visible, edges }), fullAdj, groups };
}

function radius(node) {
  if (node.kind === "ghost") return 3.6;
  if (node.kind === "tag") return 4 + Math.sqrt(node.degree) * 0.9;
  return 4.5 + Math.sqrt(node.degree) * 2.4;
}

function readColors(el) {
  const css = getComputedStyle(el);
  const get = (name) => css.getPropertyValue(name).trim();
  const vars = {};
  for (const name of ["--idea", "--task", "--decision", "--note", "--journal", "--accent", "--accent-soft", "--muted", "--line", "--line-strong", "--ink", "--ink-2", "--surface", "--bg"]) {
    vars[name] = get(name);
  }
  vars.palette = [vars["--task"], vars["--idea"], vars["--decision"], vars["--journal"], vars["--accent"], ...EXTRA_PALETTE];
  vars.font = get("--font") || "system-ui";
  return vars;
}

function categoryOf(node, m) {
  const by = ui.settings.colorBy;
  if (node.kind !== "note") return node.kind;
  if (by === "folder") return node.folder || "(brain root)";
  if (by === "cluster") return m.groups.get(node.id) ?? -1;
  if (by === "status") return node.status || "none";
  return node.type;
}

function colorFor(node, live) {
  const c = live.colors;
  if (node.kind === "ghost") return c["--muted"];
  if (node.kind === "tag") return c["--ink-2"];
  const by = ui.settings.colorBy;
  if (by === "type") return c[`--${node.type}`] || c["--note"];
  if (by === "status") return c[STATUS_COLORS[node.status]] || c["--note"];
  const key = categoryOf(node, live.m);
  if (by === "cluster" && key === -1) return c["--line-strong"];
  const index = live.categoryIndex.get(key) ?? 0;
  return c.palette[index % c.palette.length];
}

// ============================================================ view HTML

const toggle = (key, label, title) =>
  `<button class="map-chip" data-map-toggle="${key}" aria-pressed="${Boolean(ui.settings[key])}" title="${esc(title)}">${label}</button>`;

export function mapView() {
  if (!state.notes.length) {
    return `${pageHead("Map", "How your ideas, tasks, decisions and notes connect.")}<div class="card">${emptyState("map", "Nothing to map yet", "Notes appear here once you have some. Link them with [[file-name]] to draw connections.")}</div>`;
  }
  const s = ui.settings;
  const graph = buildMapGraph(state.notes, { ghosts: true, tags: false });
  const notes = graph.nodes.filter((n) => n.kind === "note");
  const links = graph.edges.filter((e) => e.kind === "link").length;
  const unwritten = graph.nodes.length - notes.length;
  const sub = `${plural(notes.length, "note")}, ${plural(links, "link")}${unwritten ? ` and ${unwritten} unwritten` : ""}. Drag, zoom and click to explore; double-click to open. <kbd>Shift</kbd>-click two notes for the path between them${readOnly() ? "" : ", <kbd>Alt</kbd>-drag one onto another to link them"}.`;
  const colorOptions = [["type", "Type"], ["folder", "Folder"], ["cluster", "Cluster"], ["status", "Status"]]
    .map(([v, l]) => `<option value="${v}" ${s.colorBy === v ? "selected" : ""}>${l}</option>`)
    .join("");
  const slider = (key, label, min, max, step) =>
    `<label class="map-slider"><span>${label}</span><input type="range" min="${min}" max="${max}" step="${step}" value="${s[key]}" data-map-slider="${key}"></label>`;
  return `${pageHead("Map", sub)}
    <div class="card map-wrap${ui.linkMode ? " linking" : ""}" id="map-wrap">
      <canvas id="map-canvas" tabindex="0" role="img" aria-label="Map of ${plural(notes.length, "note")} and ${plural(links, "link")}. Use the list of notes after the map to move between them."></canvas>
      <div class="map-toolbar">
        <label class="map-search">${icon("search", "sm")}<input id="map-search" type="search" placeholder="Find a note…" value="${esc(ui.query)}" autocomplete="off" aria-label="Find a note on the map"></label>
        <label class="map-select"><span>Colour</span><select id="map-color" aria-label="Colour notes by">${colorOptions}</select></label>
        <div class="map-chips" role="group" aria-label="Show">
          ${toggle("ghosts", "Unwritten", "Show [[links]] to notes that don't exist yet")}
          ${toggle("tags", "Tags", "Show tags as hubs")}
          ${toggle("orphans", "Orphans", "Show notes with no links")}
          ${toggle("local", "Local", "Only the selected note and its neighbours")}
          <select id="map-depth" class="map-depth" aria-label="Local graph depth" ${s.local ? "" : "hidden"}>${[1, 2, 3].map((d) => `<option value="${d}" ${s.depth === d ? "selected" : ""}>${d} hop${d > 1 ? "s" : ""}</option>`).join("")}</select>
        </div>
      </div>
      <div class="map-controls" role="toolbar" aria-label="Map controls">
        <button class="map-btn" data-map="zoom-in" aria-label="Zoom in" title="Zoom in (+)">${icon("plus", "sm")}</button>
        <button class="map-btn" data-map="zoom-out" aria-label="Zoom out" title="Zoom out (−)"><svg class="icon sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg></button>
        <button class="map-btn" data-map="fit" aria-label="Fit to screen" title="Fit (0)">${icon("target", "sm")}</button>
        <button class="map-btn" data-map="play" aria-label="Play how the brain grew" title="Time-lapse: how the brain grew">${icon("play", "sm")}</button>
        ${readOnly() ? "" : `<button class="map-btn" data-map="link-mode" aria-pressed="${ui.linkMode}" aria-label="Link mode: drag from one note to another" title="Link mode (or hold Alt and drag)">${icon("link", "sm")}</button>`}
        <button class="map-btn" data-map="settings" aria-pressed="${ui.settingsOpen}" aria-label="Layout settings" title="Layout">${icon("sliders", "sm")}</button>
        <button class="map-btn" data-map="fullscreen" aria-label="Full screen" title="Full screen">${icon("expand", "sm")}</button>
      </div>
      <div class="map-settings" id="map-settings" ${ui.settingsOpen ? "" : "hidden"}>
        ${slider("repel", "Spread", 0.3, 3, 0.1)}
        ${slider("linkDistance", "Link length", 0.4, 2.5, 0.1)}
        ${slider("gravity", "Pull to centre", 0, 3, 0.1)}
        ${slider("labels", "Labels", 0, 2, 0.1)}
        <button class="btn sm ghost" data-map="unpin">Release pinned notes</button>
      </div>
      <aside class="map-inspector" id="map-inspector" aria-live="polite"></aside>
      <div class="map-legend" id="map-legend"></div>
      <div class="map-tip" id="map-tip" hidden></div>
      <div class="map-caption" id="map-caption" hidden></div>
      <div class="map-a11y" id="map-a11y" aria-label="Notes on the map"></div>
    </div>`;
}

// ============================================================ wiring

export function wireMap() {
  const canvas = $("#map-canvas");
  const wrap = $("#map-wrap");
  if (!canvas) return;
  if (live) live.stop();
  const m = model();
  const now = performance.now();
  let cached = 0;
  for (const node of m.visible) {
    const saved = ui.positions.get(node.id);
    if (saved) {
      Object.assign(node, saved);
      cached += 1;
    }
  }
  // New notes start next to a neighbour that already has a place.
  for (const node of m.visible) {
    if (Number.isFinite(node.x)) continue;
    const anchor = [...(m.adj.get(node.id) || [])].map((id) => m.visible.find((n) => n.id === id)).find((n) => n && Number.isFinite(n.x));
    if (anchor) {
      node.x = anchor.x + (Math.random() - 0.5) * 40;
      node.y = anchor.y + (Math.random() - 0.5) * 40;
    }
  }
  const sim = new Simulation(m.visible, m.edges, ui.settings);
  if (m.visible.length > 800) {
    // Big vaults: settle in fewer animated frames.
    sim.alphaMin = 0.01;
    sim.alphaDecay = 1 - Math.pow(sim.alphaMin, 1 / 220);
  }
  const freshLayout = cached < m.visible.length * 0.6;
  sim.alpha = freshLayout ? 1 : cached === m.visible.length ? 0.12 : 0.5;
  if (freshLayout || reducedMotion()) {
    // Settle most of the layout before the first frame so the map opens calm, not exploding.
    const budget = reducedMotion() ? 320 : Math.max(40, Math.min(260, Math.floor(260000 / Math.max(m.visible.length, 1))));
    for (let i = 0; i < budget && sim.running; i++) sim.tick();
  }
  const byId = new Map(m.visible.map((n) => [n.id, n]));
  const counts = new Map();
  for (const n of m.visible) if (n.kind === "note") counts.set(categoryOf(n, m), (counts.get(categoryOf(n, m)) || 0) + 1);
  const categoryIndex = new Map([...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k], i) => [k, i]));

  live = {
    canvas, wrap, m, sim, byId, categoryIndex,
    ctx: canvas.getContext("2d"),
    colors: readColors(wrap),
    width: 0, height: 0, dpr: 1,
    hover: null, drag: null, pan: null, linking: null, pinch: null,
    pointers: new Map(),
    raf: 0,
    born: now,
    play: null,
    labels: [],
    stopped: false,
    stop() {
      this.stopped = true;
      cancelAnimationFrame(this.raf);
      this.resizeObserver?.disconnect();
      this.themeObserver?.disconnect();
    },
  };
  if (ui.selected && !byId.has(ui.selected) && !noteByPath(ui.selected)) ui.selected = null;
  ui.path = ui.path.filter((id) => byId.has(id));

  resize();
  if (!ui.camera || freshLayout) fit(false);
  live.resizeObserver = new ResizeObserver(() => {
    resize();
    kick();
  });
  live.resizeObserver.observe(wrap);
  live.themeObserver = new MutationObserver(() => {
    live.colors = readColors(wrap);
    live.baseKey = null;
    kick();
  });
  live.themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  wireControls();
  wirePointer();
  renderLegend();
  renderInspector();
  renderA11yList();
  kick();
  window.cairn = Object.assign(window.cairn || {}, { map: debugApi() });
}

function resize() {
  const rect = live.wrap.getBoundingClientRect();
  live.dpr = window.devicePixelRatio || 1;
  live.width = Math.max(rect.width, 1);
  live.height = Math.max(rect.height, 1);
  live.canvas.width = Math.round(live.width * live.dpr);
  live.canvas.height = Math.round(live.height * live.dpr);
  live.canvas.style.width = `${live.width}px`;
  live.canvas.style.height = `${live.height}px`;
}

// ------------------------------------------------------------ camera

const toWorld = (sx, sy) => ({ x: (sx - ui.camera.x) / ui.camera.k, y: (sy - ui.camera.y) / ui.camera.k });
const toScreen = (x, y) => ({ x: x * ui.camera.k + ui.camera.x, y: y * ui.camera.k + ui.camera.y });

function bounds(nodes) {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const n of nodes) {
    x0 = Math.min(x0, n.x);
    y0 = Math.min(y0, n.y);
    x1 = Math.max(x1, n.x);
    y1 = Math.max(y1, n.y);
  }
  return Number.isFinite(x0) ? { x0, y0, x1, y1 } : { x0: -100, y0: -100, x1: 100, y1: 100 };
}

function cameraFor(nodes) {
  const b = bounds(nodes);
  const inspector = live.width > 760 ? 300 : 0;
  const w = Math.max(live.width - inspector - 60, 120);
  const h = Math.max(live.height - 140, 120);
  const k = Math.min(2.2, Math.max(0.08, Math.min(w / (b.x1 - b.x0 + 80), h / (b.y1 - b.y0 + 80))));
  return {
    k,
    x: (live.width - inspector) / 2 - ((b.x0 + b.x1) / 2) * k,
    y: live.height / 2 + 18 - ((b.y0 + b.y1) / 2) * k,
  };
}

function animateCamera(target, animate = true) {
  if (!animate || reducedMotion() || !ui.camera) {
    ui.camera = target;
    kick();
    return;
  }
  const from = { ...ui.camera };
  const start = performance.now();
  live.cameraTween = (t) => {
    const p = Math.min(1, (t - start) / 320);
    const e = 1 - Math.pow(1 - p, 3);
    ui.camera = { k: from.k + (target.k - from.k) * e, x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e };
    if (p >= 1) live.cameraTween = null;
  };
  kick();
}

function fit(animate = true) {
  animateCamera(cameraFor(live.m.visible), animate);
}

function zoomAt(factor, sx = live.width / 2, sy = live.height / 2) {
  const c = ui.camera;
  const k = Math.min(6, Math.max(0.06, c.k * factor));
  const world = toWorld(sx, sy);
  ui.camera = { k, x: sx - world.x * k, y: sy - world.y * k };
  kick();
}

function centerOn(node, animate = true) {
  if (!node) return;
  const inspector = live.width > 760 ? 300 : 0;
  const k = Math.max(ui.camera.k, 0.9);
  animateCamera({ k, x: (live.width - inspector) / 2 - node.x * k, y: live.height / 2 - node.y * k }, animate);
}

// ------------------------------------------------------------ frame loop

function kick() {
  if (!live || live.stopped || live.raf) return;
  live.raf = requestAnimationFrame(frame);
}

function frame(t) {
  live.raf = 0;
  if (live.stopped || !live.canvas.isConnected) return live.stop();
  if (live.sim.running) {
    const ticks = live.m.visible.length > 1200 ? 3 : 2;
    for (let i = 0; i < ticks && live.sim.running; i++) live.sim.tick();
    for (const n of live.m.visible) ui.positions.set(n.id, { x: n.x, y: n.y, fx: n.fx ?? null, fy: n.fy ?? null });
  }
  live.cameraTween?.(t);
  const animating = draw(t);
  if (live.sim.running || live.cameraTween || animating) kick();
}

// ------------------------------------------------------------ drawing

function focusSet() {
  const id = live.linking?.target?.id || live.hover?.id || ui.selected;
  if (ui.path.length > 1) return new Set(ui.path);
  if (!id || !live.byId.has(id)) return null;
  return new Set([id, ...(live.m.adj.get(id) || [])]);
}

function matches() {
  const q = ui.query.trim().toLowerCase();
  if (!q) return null;
  return new Set(live.m.visible.filter((n) => n.title.toLowerCase().includes(q)).map((n) => n.id));
}

function onPath(e) {
  const i = ui.path.indexOf(e.source.id);
  const j = ui.path.indexOf(e.target.id);
  return i !== -1 && j !== -1 && Math.abs(i - j) === 1;
}

// The scene is painted in two layers. The base layer (every link and dot, undimmed) is cached while the
// layout is idle, so hovering, selecting and searching a large vault only costs one image copy plus the
// few highlighted notes on top.
function paintNodes(ctx, nodes, px, outline, radiusOf = radius) {
  const { colors } = live;
  const groups = new Map();
  const pins = new Path2D();
  const TAU = Math.PI * 2;
  const ghosts = [];
  for (const n of nodes) {
    const r = radiusOf(n);
    if (r <= 0) continue;
    if (n.kind === "ghost") {
      ghosts.push([n, r]);
      continue;
    }
    const color = colorFor(n, live);
    if (!groups.has(color)) groups.set(color, new Path2D());
    const path = groups.get(color);
    path.moveTo(n.x + r, n.y);
    path.arc(n.x, n.y, r, 0, TAU);
    if (n.fx != null && n.kind === "note") {
      const pr = Math.min(r * 0.32, 2.4);
      pins.moveTo(n.x + pr, n.y);
      pins.arc(n.x, n.y, pr, 0, TAU);
    }
  }
  ctx.strokeStyle = colors["--surface"];
  ctx.lineWidth = 2 * px;
  for (const [color, path] of groups) {
    if (outline) ctx.stroke(path);
    ctx.fillStyle = color;
    ctx.fill(path);
  }
  if (ghosts.length) {
    const ring = new Path2D();
    for (const [n, r] of ghosts) {
      ring.moveTo(n.x + r, n.y);
      ring.arc(n.x, n.y, r, 0, TAU);
    }
    ctx.fillStyle = colors["--surface"];
    ctx.fill(ring);
    ctx.setLineDash([2.5 * px, 2 * px]);
    ctx.strokeStyle = colors["--muted"];
    ctx.lineWidth = 1.4 * px;
    ctx.stroke(ring);
    ctx.setLineDash([]);
  }
  ctx.fillStyle = colors["--surface"];
  ctx.fill(pins);
}

function viewBounds(margin = 40) {
  const a = toWorld(-margin, -margin);
  const b = toWorld(live.width + margin, live.height + margin);
  return { x0: a.x, y0: a.y, x1: b.x, y1: b.y };
}

const outside = (e, v) =>
  (e.source.x < v.x0 && e.target.x < v.x0) || (e.source.x > v.x1 && e.target.x > v.x1) ||
  (e.source.y < v.y0 && e.target.y < v.y0) || (e.source.y > v.y1 && e.target.y > v.y1);

function paintBase(ctx, shown, radiusOf) {
  const { colors, dpr, sim } = live;
  const cam = ui.camera;
  const px = 1 / cam.k;
  const v = viewBounds();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, live.width, live.height);
  ctx.setTransform(dpr * cam.k, 0, 0, dpr * cam.k, dpr * cam.x, dpr * cam.y);
  const quiet = { link: new Path2D(), ghost: new Path2D(), tag: new Path2D() };
  for (const e of sim.links) {
    if (outside(e, v) || (shown && (!shown(e.source) || !shown(e.target)))) continue;
    const path = quiet[e.kind] || quiet.link;
    path.moveTo(e.source.x, e.source.y);
    path.lineTo(e.target.x, e.target.y);
  }
  ctx.globalAlpha = 0.6;
  ctx.strokeStyle = colors["--line-strong"];
  ctx.lineWidth = 1.1 * px;
  for (const [kind, path] of Object.entries(quiet)) {
    ctx.setLineDash(kind === "ghost" ? [4 * px, 3 * px] : kind === "tag" ? [1.5 * px, 3 * px] : []);
    ctx.stroke(path);
  }
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  const visibleNodes = sim.nodes.filter((n) => n.x > v.x0 - 20 && n.x < v.x1 + 20 && n.y > v.y0 - 20 && n.y < v.y1 + 20 && (!shown || shown(n)));
  paintNodes(ctx, visibleNodes, px, sim.nodes.length <= 400, radiusOf);
}

function draw(t) {
  const { ctx, colors, dpr, sim } = live;
  const cam = ui.camera;
  const focus = focusSet();
  const found = matches();
  const centre = live.linking?.target?.id || live.hover?.id || ui.selected;
  const motion = !reducedMotion();
  let animating = false;
  // Time-lapse: notes appear in the order they were made.
  let revealed = null;
  if (live.play) {
    const p = Math.min(1, (t - live.play.start) / live.play.duration);
    const count = Math.ceil(p * live.play.order.length);
    revealed = new Map(live.play.order.slice(0, count).map((n, i) => [n.id, i]));
    const latest = live.play.order[count - 1];
    const caption = $("#map-caption");
    if (caption && latest?.time) caption.textContent = new Date(latest.time).toISOString().slice(0, 10);
    if (p >= 1) {
      live.play = null;
      setTimeout(() => {
        const cap = $("#map-caption");
        if (cap) cap.hidden = true;
      }, 1200);
    } else animating = true;
  }
  const isShown = revealed
    ? (n) => (n.kind === "note" ? revealed.has(n.id) : [...(live.m.adj.get(n.id) || [])].some((id) => revealed.has(id)))
    : null;
  const grow = revealed
    ? (n) => {
        const r = radius(n);
        if (!revealed.has(n.id)) return r;
        const age = ((t - live.play.start) / live.play.duration) * live.play.order.length - revealed.get(n.id);
        return r * Math.min(1, 0.3 + Math.max(age, 0) * 0.35);
      }
    : radius;

  // Base layer, cached while nothing moves.
  const idle = !sim.running && !live.drag && !live.cameraTween && !revealed;
  const key = `${cam.k}|${cam.x}|${cam.y}|${live.width}|${live.height}|${live.colors["--surface"]}`;
  if (!live.layer) live.layer = document.createElement("canvas");
  const layer = live.layer;
  if (layer.width !== live.canvas.width || layer.height !== live.canvas.height) {
    layer.width = live.canvas.width;
    layer.height = live.canvas.height;
    live.baseKey = null;
  }
  if (!idle || live.baseKey !== key) {
    paintBase(layer.getContext("2d"), isShown, grow);
    live.baseKey = idle ? key : null;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, live.canvas.width, live.canvas.height);
  ctx.globalAlpha = focus ? 0.16 : found ? 0.3 : 1;
  ctx.drawImage(layer, 0, 0);
  ctx.globalAlpha = 1;
  ctx.setTransform(dpr * cam.k, 0, 0, dpr * cam.k, dpr * cam.x, dpr * cam.y);
  const px = 1 / cam.k;

  // Highlighted links: around the hovered/selected note, or along a found path.
  if (focus) {
    for (const e of sim.links) {
      const lit = ui.path.length > 1 ? onPath(e) : e.source.id === centre || e.target.id === centre;
      if (!lit || (isShown && (!isShown(e.source) || !isShown(e.target)))) continue;
      ctx.strokeStyle = colors["--accent"];
      ctx.lineWidth = 1.8 * px;
      ctx.setLineDash(e.kind === "ghost" ? [4 * px, 3 * px] : e.kind === "tag" ? [1.5 * px, 3 * px] : []);
      ctx.beginPath();
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
      ctx.stroke();
      ctx.setLineDash([]);
      if (e.kind === "tag") continue;
      drawArrow(e.source, e.target, px);
      if (e.mutual) drawArrow(e.target, e.source, px);
      if (motion) {
        drawParticle(e.source, e.target, t, px);
        if (e.mutual) drawParticle(e.target, e.source, t + 550, px);
        animating = true;
      }
    }
  }

  // Rubber band while linking.
  if (live.linking) {
    const { from, x, y, target } = live.linking;
    ctx.strokeStyle = colors["--accent"];
    ctx.lineWidth = 2 * px;
    ctx.setLineDash([6 * px, 4 * px]);
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(target ? target.x : x, target ? target.y : y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Search halos, then the notes that stay bright on top of the dimmed base.
  if (found) {
    const halos = new Path2D();
    for (const id of found) {
      const n = live.byId.get(id);
      if (!n || (isShown && !isShown(n))) continue;
      const r = grow(n) + 7 * px;
      halos.moveTo(n.x + r, n.y);
      halos.arc(n.x, n.y, r, 0, Math.PI * 2);
    }
    ctx.fillStyle = colors["--accent-soft"];
    ctx.fill(halos);
  }
  const bright = [];
  for (const n of sim.nodes) {
    if (isShown && !isShown(n)) continue;
    if ((focus && focus.has(n.id) && (!found || found.has(n.id))) || (!focus && found?.has(n.id))) bright.push(n);
  }
  if (bright.length) paintNodes(ctx, bright, px, true, grow);
  const rings = new Path2D();
  for (const id of new Set([ui.selected, ...ui.path, live.linking?.target?.id])) {
    const n = id && live.byId.get(id);
    if (!n) continue;
    const r = grow(n) + 4 * px;
    rings.moveTo(n.x + r, n.y);
    rings.arc(n.x, n.y, r, 0, Math.PI * 2);
  }
  ctx.strokeStyle = colors["--accent"];
  ctx.lineWidth = 2.2 * px;
  ctx.stroke(rings);
  drawLabels(focus, found, isShown);
  return animating;
}

function drawArrow(from, to, px) {
  const { ctx } = live;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const d = Math.hypot(dx, dy) || 1;
  const r = radius(to) + 3 * px;
  const x = to.x - (dx / d) * r;
  const y = to.y - (dy / d) * r;
  const size = 6 * px;
  const angle = Math.atan2(dy, dx);
  ctx.fillStyle = live.colors["--accent"];
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - size * Math.cos(angle - 0.45), y - size * Math.sin(angle - 0.45));
  ctx.lineTo(x - size * Math.cos(angle + 0.45), y - size * Math.sin(angle + 0.45));
  ctx.closePath();
  ctx.fill();
}

function drawParticle(from, to, t, px) {
  const { ctx } = live;
  for (const offset of [0, 0.5]) {
    const p = ((t / 1400) + offset) % 1;
    ctx.fillStyle = live.colors["--accent"];
    ctx.globalAlpha = Math.sin(p * Math.PI);
    ctx.beginPath();
    ctx.arc(from.x + (to.x - from.x) * p, from.y + (to.y - from.y) * p, 2.2 * px, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

// Labels in screen space: important ones first, never on top of each other or of a dot.
function drawLabels(focus, found, shown) {
  const { ctx, colors, dpr } = live;
  const cam = ui.camera;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const fontSize = 12;
  ctx.font = `${fontSize}px ${colors.font}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const centre = live.hover?.id || ui.selected;
  const threshold = 7.5 / (ui.settings.labels || 0.001);
  const items = [];
  for (const n of live.sim.nodes) {
    if (shown && !shown(n)) continue;
    const s = toScreen(n.x, n.y);
    if (s.x < -80 || s.y < -40 || s.x > live.width + 80 || s.y > live.height + 40) continue;
    const r = radius(n) * cam.k;
    let priority = n.degree + r;
    if (n.id === centre) priority += 10000;
    else if (focus?.has(n.id)) priority += 5000;
    else if (found?.has(n.id)) priority += 4000;
    else if (focus || found) continue;
    else if (r < threshold * 0.5 && ui.settings.labels < 1.95) continue;
    items.push({ n, s, r, priority });
  }
  items.sort((a, b) => b.priority - a.priority);
  // Spatial grid of occupied boxes (dots and placed labels), so collision checks stay cheap.
  const CELL = 64;
  const grid = new Map();
  const cells = (box, visit) => {
    for (let gx = Math.floor(box[0] / CELL); gx <= Math.floor(box[2] / CELL); gx++) {
      for (let gy = Math.floor(box[1] / CELL); gy <= Math.floor(box[3] / CELL); gy++) {
        if (visit(`${gx},${gy}`)) return true;
      }
    }
    return false;
  };
  const occupy = (box) => cells(box, (key) => {
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(box);
    return false;
  });
  const overlaps = (box) => cells(box, (key) => (grid.get(key) || []).some((b) => box[0] < b[2] && b[0] < box[2] && box[1] < b[3] && b[1] < box[3]));
  for (const { s, r } of items) occupy([s.x - r, s.y - r, s.x + r, s.y + r]);
  const placed = [];
  for (const { n, s, r, priority } of items.slice(0, 400)) {
    const title = n.title.length > 34 && priority < 4000 ? `${n.title.slice(0, 33)}…` : n.title;
    const w = ctx.measureText(title).width;
    const box = [s.x - w / 2 - 2, s.y + r + 3, s.x + w / 2 + 2, s.y + r + 4 + fontSize];
    if (overlaps(box)) continue;
    occupy(box);
    placed.push({ id: n.id, title, x: s.x, y: s.y + r + 4, box });
    const dim = (focus && !focus.has(n.id)) || (found && !found.has(n.id));
    ctx.globalAlpha = dim ? 0.35 : 1;
    ctx.lineWidth = 4;
    ctx.lineJoin = "round";
    ctx.strokeStyle = colors["--surface"];
    ctx.strokeText(title, s.x, s.y + r + 4);
    ctx.fillStyle = n.kind === "ghost" ? colors["--muted"] : n.id === centre ? colors["--ink"] : colors["--ink-2"];
    ctx.font = `${n.id === centre ? 600 : 400} ${fontSize}px ${colors.font}`;
    ctx.fillText(title, s.x, s.y + r + 4);
    ctx.font = `${fontSize}px ${colors.font}`;
  }
  ctx.globalAlpha = 1;
  live.labels = placed;
}

// ------------------------------------------------------------ pointer

function hit(sx, sy) {
  const w = toWorld(sx, sy);
  let best = null;
  let bestD = Infinity;
  for (const n of live.sim.nodes) {
    const d = Math.hypot(n.x - w.x, n.y - w.y);
    const reach = radius(n) + 6 / ui.camera.k;
    if (d < reach && d < bestD) {
      best = n;
      bestD = d;
    }
  }
  return best;
}

function local(event) {
  const rect = live.canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function wirePointer() {
  const { canvas } = live;
  canvas.addEventListener("pointerdown", (event) => {
    canvas.focus({ preventScroll: true });
    const p = local(event);
    live.pointers.set(event.pointerId, p);
    canvas.setPointerCapture(event.pointerId);
    if (live.pointers.size === 2) {
      const [a, b] = [...live.pointers.values()];
      live.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: ui.camera.k };
      live.drag = live.pan = live.linking = null;
      return;
    }
    const node = hit(p.x, p.y);
    if (node && node.kind === "note" && !readOnly() && (ui.linkMode || event.altKey)) {
      const w = toWorld(p.x, p.y);
      live.linking = { from: node, x: w.x, y: w.y, target: null };
      hideTip();
    } else if (node) {
      live.drag = { node, start: p, moved: false, shift: event.shiftKey };
    } else {
      live.pan = { start: p, camera: { ...ui.camera }, moved: false };
      canvas.classList.add("panning");
    }
    kick();
  });
  canvas.addEventListener("pointermove", (event) => {
    const p = local(event);
    if (live.pointers.has(event.pointerId)) live.pointers.set(event.pointerId, p);
    if (live.pinch && live.pointers.size === 2) {
      const [a, b] = [...live.pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      zoomAt((live.pinch.k * (d / live.pinch.d)) / ui.camera.k, (a.x + b.x) / 2, (a.y + b.y) / 2);
      return;
    }
    if (live.linking) {
      const w = toWorld(p.x, p.y);
      const over = hit(p.x, p.y);
      Object.assign(live.linking, { x: w.x, y: w.y, target: over && over !== live.linking.from && over.kind === "note" ? over : null });
      kick();
      return;
    }
    if (live.drag) {
      const { node, start } = live.drag;
      if (!live.drag.moved && Math.hypot(p.x - start.x, p.y - start.y) < 4) return;
      if (!live.drag.moved) {
        live.drag.moved = true;
        live.sim.alphaTarget = 0.25;
        live.sim.reheat(0.3);
        hideTip();
      }
      const w = toWorld(p.x, p.y);
      node.fx = w.x;
      node.fy = w.y;
      kick();
      return;
    }
    if (live.pan) {
      const dx = p.x - live.pan.start.x;
      const dy = p.y - live.pan.start.y;
      if (Math.hypot(dx, dy) > 3) live.pan.moved = true;
      ui.camera = { ...live.pan.camera, x: live.pan.camera.x + dx, y: live.pan.camera.y + dy };
      kick();
      return;
    }
    const over = hit(p.x, p.y);
    if (over !== live.hover) {
      live.hover = over;
      canvas.style.cursor = over ? (ui.linkMode || event.altKey ? "crosshair" : "pointer") : "";
      kick();
    }
    if (over) showTip(over, p);
    else hideTip();
  });
  const end = async (event) => {
    live.pointers.delete(event.pointerId);
    if (live.pinch) {
      if (live.pointers.size < 2) live.pinch = null;
      return;
    }
    canvas.classList.remove("panning");
    if (live.linking) {
      const { from, target } = live.linking;
      live.linking = null;
      kick();
      if (target) await linkNotes(from.id, target.id);
      return;
    }
    if (live.drag) {
      const { node, moved, shift } = live.drag;
      live.drag = null;
      live.sim.alphaTarget = 0;
      if (moved) {
        ui.positions.set(node.id, { x: node.x, y: node.y, fx: node.fx, fy: node.fy });
        renderInspector();
      } else if (event.type === "pointerup") {
        clickNode(node, shift);
      }
      kick();
      return;
    }
    if (live.pan) {
      const moved = live.pan.moved;
      live.pan = null;
      if (!moved && event.type === "pointerup") select(null);
    }
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", () => {
    if (!live.drag && !live.pan) {
      live.hover = null;
      hideTip();
      kick();
    }
  });
  canvas.addEventListener("dblclick", (event) => {
    const p = local(event);
    const node = hit(p.x, p.y);
    if (!node) return zoomAt(1.6, p.x, p.y);
    if (node.kind === "note") openNote(node.id);
    else if (node.kind === "ghost") createFromGhost(node.id);
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const p = local(event);
    const factor = Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.05 : 0.0018));
    zoomAt(factor, p.x, p.y);
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    const keys = {
      "+": () => zoomAt(1.25),
      "=": () => zoomAt(1.25),
      "-": () => zoomAt(0.8),
      0: () => fit(),
      Escape: () => (ui.path.length || ui.selected ? select(null) : null),
      Enter: () => ui.selected && live.byId.get(ui.selected)?.kind === "note" && openNote(ui.selected),
      ArrowLeft: () => panBy(60, 0),
      ArrowRight: () => panBy(-60, 0),
      ArrowUp: () => panBy(0, 60),
      ArrowDown: () => panBy(0, -60),
    };
    if (keys[event.key] && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      event.stopPropagation();
      keys[event.key]();
    }
  });
}

function panBy(dx, dy) {
  ui.camera = { ...ui.camera, x: ui.camera.x + dx, y: ui.camera.y + dy };
  kick();
}

function clickNode(node, shift) {
  if (shift && ui.selected && ui.selected !== node.id && live.byId.has(ui.selected)) {
    ui.path = shortestPath(live.m.adj, ui.selected, node.id);
    if (!ui.path.length) ui.path = [ui.selected, node.id, "none"];
    renderInspector();
    kick();
    return;
  }
  select(node.id);
}

function select(id, { center = false } = {}) {
  const wasLocal = ui.settings.local && ui.selected !== id;
  ui.selected = id;
  ui.path = [];
  if (wasLocal && id) return renderView();
  renderInspector();
  if (center && id) centerOn(live.byId.get(id));
  kick();
}

// ------------------------------------------------------------ tooltip

function showTip(node, p) {
  const tip = $("#map-tip");
  if (!tip) return;
  const note = node.kind === "note" ? noteByPath(node.id) : null;
  const summary = note ? core.summary(note.body) : "";
  const meta = node.kind === "ghost"
    ? "Not written yet · double-click to create"
    : node.kind === "tag"
      ? `Tag · ${plural(node.degree, "note")}`
      : [TYPE_META[node.type]?.label, STATUS_LABEL[node.status], `${node.out} out · ${node.in} in`].filter(Boolean).join(" · ");
  tip.innerHTML = `<strong>${esc(node.title)}</strong><span class="meta">${esc(meta)}</span>${summary ? `<span class="sum">${esc(summary)}</span>` : ""}`;
  tip.hidden = false;
  const x = Math.min(p.x + 16, live.width - tip.offsetWidth - 8);
  const y = p.y + 18 + tip.offsetHeight > live.height ? p.y - tip.offsetHeight - 12 : p.y + 18;
  tip.style.transform = `translate(${Math.max(8, x)}px, ${Math.max(8, y)}px)`;
}

function hideTip() {
  const tip = $("#map-tip");
  if (tip) tip.hidden = true;
}

// ------------------------------------------------------------ inspector, legend, a11y list

function nodeButton(id) {
  const n = live.byId.get(id) || live.m.graph.nodes.find((x) => x.id === id);
  if (!n) return "";
  const dot = n.kind === "note" ? `<i class="dot" style="background:${colorFor(n, live)}"></i>` : `<i class="dot ghost"></i>`;
  return `<button class="map-row" data-map-select="${esc(id)}">${dot}<span>${esc(n.title)}</span>${n.kind === "note" ? `<span class="muted">${n.degree}</span>` : ""}</button>`;
}

function renderInspector() {
  const el = $("#map-inspector");
  if (!el) return;
  const m = live.m;
  if (ui.path.length) {
    const none = ui.path[2] === "none";
    const [a, b] = none ? ui.path : [ui.path[0], ui.path[ui.path.length - 1]];
    const title = (id) => esc((live.byId.get(id) || {}).title || id);
    el.innerHTML = `
      <div class="insp-head"><span class="eyebrow">${icon("route", "sm")} Path</span><button class="btn ghost sm icon-only" data-map="clear" aria-label="Clear path">${icon("x", "sm")}</button></div>
      <h3>${title(a)} → ${title(b)}</h3>
      ${none ? `<p class="muted">These two notes aren't connected yet${readOnly() ? "." : ". Alt-drag one onto the other to link them."}</p>` : `<p class="muted">${plural(ui.path.length - 1, "step")} apart</p><ol class="insp-path">${ui.path.map((id) => `<li>${nodeButton(id)}</li>`).join("")}</ol>`}`;
    el.classList.remove("overview");
    return;
  }
  const node = ui.selected ? live.byId.get(ui.selected) : null;
  if (!node) {
    const notes = m.graph.nodes.filter((n) => n.kind === "note");
    const orphans = notes.filter((n) => !m.fullAdj.get(n.id).size).length;
    const ghosts = m.graph.nodes.filter((n) => n.kind === "ghost").length;
    const groupCount = new Set([...m.groups.values()].filter((g) => g >= 0)).size;
    const hubs = [...notes].sort((a, b) => b.degree - a.degree || a.title.localeCompare(b.title)).filter((n) => n.degree).slice(0, 5);
    el.innerHTML = `
      <div class="insp-head"><span class="eyebrow">${icon("map", "sm")} Overview</span></div>
      <div class="insp-stats">
        <div><strong>${notes.length}</strong><span>notes</span></div>
        <div><strong>${m.graph.edges.filter((e) => e.kind === "link").length}</strong><span>links</span></div>
        <div><strong>${groupCount}</strong><span>clusters</span></div>
        <div><strong>${orphans}</strong><span>orphans</span></div>
      </div>
      ${hubs.length ? `<h4>Hubs</h4>${hubs.map((n) => nodeButton(n.id)).join("")}` : ""}
      ${ghosts && ui.settings.ghosts ? `<p class="muted insp-note">${plural(ghosts, "link")} point${ghosts === 1 ? "s" : ""} to notes that don't exist yet: the hollow dots. Double-click one to write it.</p>` : ""}`;
    el.classList.add("overview");
    return;
  }
  el.classList.remove("overview");
  if (node.kind === "ghost") {
    const from = [...m.adj.get(node.id)];
    el.innerHTML = `
      <div class="insp-head"><span class="eyebrow">Not written yet</span><button class="btn ghost sm icon-only" data-map="clear" aria-label="Close">${icon("x", "sm")}</button></div>
      <h3>${esc(node.title)}</h3>
      <p class="muted">${plural(from.length, "note")} link${from.length === 1 ? "s" : ""} here, but there's no note with this name.</p>
      ${readOnly() ? "" : `<button class="btn primary sm" data-map="create">${icon("plus", "sm")} Write this note</button>`}
      <h4>Mentioned in</h4>${from.map(nodeButton).join("")}`;
    return;
  }
  if (node.kind === "tag") {
    const tagged = [...m.adj.get(node.id)];
    el.innerHTML = `
      <div class="insp-head"><span class="eyebrow">Tag</span><button class="btn ghost sm icon-only" data-map="clear" aria-label="Close">${icon("x", "sm")}</button></div>
      <h3>${esc(node.title)}</h3><p class="muted">${plural(tagged.length, "note")}</p>${tagged.map(nodeButton).join("")}`;
    return;
  }
  const note = noteByPath(node.id);
  const neighbours = [...(m.adj.get(node.id) || [])].sort((a, b) => (live.byId.get(b)?.degree || 0) - (live.byId.get(a)?.degree || 0));
  const summary = note ? core.summary(note.body) : "";
  el.innerHTML = `
    <div class="insp-head"><span class="pill t-${node.type}">${TYPE_META[node.type]?.label || "Note"}</span>${node.status && STATUS_LABEL[node.status] ? `<span class="pill">${STATUS_LABEL[node.status]}</span>` : ""}<button class="btn ghost sm icon-only" data-map="clear" aria-label="Close">${icon("x", "sm")}</button></div>
    <h3>${esc(node.title)}</h3>
    <p class="insp-path-text">brain/${esc(node.id)}</p>
    ${summary ? `<p class="insp-sum">${esc(summary)}</p>` : ""}
    ${node.tags.length ? `<p class="insp-tags">${node.tags.map((t) => `<span>#${esc(t)}</span>`).join(" ")}</p>` : ""}
    <div class="insp-stats small"><div><strong>${node.out}</strong><span>links out</span></div><div><strong>${node.in}</strong><span>backlinks</span></div></div>
    <div class="insp-actions">
      <button class="btn primary sm" data-open="${esc(node.id)}">${icon("note", "sm")} Open</button>
      <button class="btn sm" data-map="local-here" aria-pressed="${ui.settings.local}">${icon("target", "sm")} ${ui.settings.local ? "Show all" : "Local graph"}</button>
      ${node.fx != null ? `<button class="btn sm ghost" data-map="unpin-one">Release</button>` : ""}
    </div>
    ${neighbours.length ? `<h4>Connected</h4>${neighbours.slice(0, 30).map(nodeButton).join("")}` : `<p class="muted insp-note">No links yet.${readOnly() ? "" : " Alt-drag from this note onto another to link them."}</p>`}
    <p class="muted insp-note">Shift-click another note to see how they connect.</p>`;
}

function renderLegend() {
  const el = $("#map-legend");
  if (!el) return;
  const s = ui.settings;
  const m = live.m;
  if (s.colorBy === "type") {
    const present = new Set(m.graph.nodes.filter((n) => n.kind === "note").map((n) => n.type));
    el.innerHTML = core.TYPES.filter((t) => present.has(t))
      .map((t) => `<button class="legend-item" data-map-type="${t}" aria-pressed="${!s.hiddenTypes.includes(t)}" title="Show or hide ${TYPE_META[t].label.toLowerCase()}s"><i class="dot t-${t}"></i>${TYPE_META[t].label}</button>`)
      .join("") + (s.ghosts && m.visible.some((n) => n.kind === "ghost") ? `<span class="legend-item static"><i class="dot ghost"></i>Unwritten</span>` : "");
    return;
  }
  const items = [...live.categoryIndex.entries()].slice(0, 8);
  const label = (k) => (s.colorBy === "cluster" ? (k === -1 ? "No links" : `Cluster ${k + 1}`) : s.colorBy === "status" ? STATUS_LABEL[k] || "No status" : k);
  el.innerHTML = items
    .map(([k]) => {
      const sample = m.visible.find((n) => n.kind === "note" && categoryOf(n, m) === k);
      return `<span class="legend-item static"><i class="dot" style="background:${sample ? colorFor(sample, live) : "transparent"}"></i>${esc(String(label(k)))}</span>`;
    })
    .join("") + (live.categoryIndex.size > 8 ? `<span class="legend-item static muted">+${live.categoryIndex.size - 8} more</span>` : "");
}

function renderA11yList() {
  const el = $("#map-a11y");
  if (!el) return;
  const notes = live.m.visible.filter((n) => n.kind === "note").sort((a, b) => b.degree - a.degree || a.title.localeCompare(b.title));
  el.innerHTML = notes
    .map((n) => `<button class="map-node" data-open="${esc(n.id)}" data-node="${esc(n.id)}" aria-label="${esc(`${n.title}, ${TYPE_META[n.type]?.label || "note"}, ${plural(n.degree, "link")}`)}">${esc(n.title)}</button>`)
    .join("");
  el.addEventListener("focusin", (event) => {
    const button = event.target.closest(".map-node");
    if (!button) return;
    ui.selected = button.dataset.node;
    ui.path = [];
    renderInspector();
    centerOn(live.byId.get(button.dataset.node));
  });
}

// ------------------------------------------------------------ controls

function wireControls() {
  const wrap = live.wrap;
  const rerender = () => {
    saveSettings();
    renderView();
  };
  wrap.addEventListener("click", async (event) => {
    const pick = event.target.closest("[data-map-select]");
    if (pick) return select(pick.dataset.mapSelect, { center: true });
    const typeToggle = event.target.closest("[data-map-type]");
    if (typeToggle) {
      const type = typeToggle.dataset.mapType;
      const hidden = new Set(ui.settings.hiddenTypes);
      if (hidden.has(type)) hidden.delete(type);
      else hidden.add(type);
      ui.settings.hiddenTypes = [...hidden];
      return rerender();
    }
    const toggleEl = event.target.closest("[data-map-toggle]");
    if (toggleEl) {
      const key = toggleEl.dataset.mapToggle;
      ui.settings[key] = !ui.settings[key];
      if (key === "local" && ui.settings.local && !ui.selected) toast("Select a note first, then Local shows only its neighbourhood.");
      return rerender();
    }
    const button = event.target.closest("[data-map]");
    if (!button) return;
    const action = button.dataset.map;
    const actions = {
      "zoom-in": () => zoomAt(1.3),
      "zoom-out": () => zoomAt(1 / 1.3),
      fit: () => fit(),
      play: () => playHistory(),
      "link-mode": () => {
        ui.linkMode = !ui.linkMode;
        button.setAttribute("aria-pressed", String(ui.linkMode));
        wrap.classList.toggle("linking", ui.linkMode);
        if (ui.linkMode) toast("Link mode: drag from one note onto another to link them.");
      },
      settings: () => {
        ui.settingsOpen = !ui.settingsOpen;
        button.setAttribute("aria-pressed", String(ui.settingsOpen));
        $("#map-settings").hidden = !ui.settingsOpen;
      },
      fullscreen: () => {
        if (document.fullscreenElement) document.exitFullscreen();
        else if (wrap.requestFullscreen) wrap.requestFullscreen().catch(() => wrap.classList.toggle("full"));
        else wrap.classList.toggle("full");
      },
      unpin: () => {
        for (const n of live.sim.nodes) n.fx = n.fy = null;
        live.baseKey = null;
        live.sim.reheat(0.5);
        renderInspector();
        kick();
      },
      "unpin-one": () => {
        const n = live.byId.get(ui.selected);
        if (n) n.fx = n.fy = null;
        live.baseKey = null;
        live.sim.reheat(0.3);
        renderInspector();
        kick();
      },
      clear: () => select(null),
      create: () => createFromGhost(ui.selected),
      "local-here": () => {
        ui.settings.local = !ui.settings.local;
        rerender();
      },
    };
    actions[action]?.();
  });
  $("#map-color", wrap).addEventListener("change", (event) => {
    ui.settings.colorBy = event.target.value;
    rerender();
  });
  $("#map-depth", wrap).addEventListener("change", (event) => {
    ui.settings.depth = Number(event.target.value);
    rerender();
  });
  $$("[data-map-slider]", wrap).forEach((input) => {
    input.addEventListener("input", () => {
      const key = input.dataset.mapSlider;
      ui.settings[key] = Number(input.value);
      live.sim.settings[key] = Number(input.value);
      if (key !== "labels") live.sim.reheat(0.5);
      saveSettings();
      kick();
    });
  });
  const search = $("#map-search", wrap);
  search.addEventListener("input", () => {
    ui.query = search.value;
    kick();
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const found = matches();
      const first = found && [...found].map((id) => live.byId.get(id)).sort((a, b) => b.degree - a.degree)[0];
      if (first) select(first.id, { center: true });
    } else if (event.key === "Escape") {
      ui.query = search.value = "";
      kick();
    }
  });
}

// ------------------------------------------------------------ actions that change notes

async function linkNotes(fromId, toId) {
  const from = noteByPath(fromId);
  const to = noteByPath(toId);
  if (!from || !to) return;
  const names = new Set(core.noteNames(to));
  if (core.extractLinks(from.body).some((slug) => names.has(slug))) {
    toast(`“${from.title}” already links to “${to.title}”.`);
    return;
  }
  const before = from.body;
  const body = `${before.replace(/\s*$/, "")}\n\n[[${to.slug}]]\n`;
  const saved = await saveNote(fromId, { body });
  if (!saved) return;
  ui.selected = fromId;
  renderView();
  toast(`Linked “${from.title}” → “${to.title}”`, {
    action: {
      label: "Undo",
      run: async () => {
        await saveNote(fromId, { body: before });
        renderView();
      },
    },
  });
}

async function createFromGhost(id) {
  if (readOnly() || !id?.startsWith("ghost:")) return;
  const ghost = live.byId.get(id);
  if (!ghost) return;
  const note = await createNote({ type: "note", title: ghost.title });
  const spot = ui.positions.get(id);
  if (spot) ui.positions.set(note.path, spot);
  ui.selected = note.path;
  renderView();
  renderNav();
  toast(`Created brain/${note.path}`, { action: { label: "Open", run: () => openNote(note.path) } });
}

function playHistory() {
  const order = live.m.visible.filter((n) => n.kind === "note").sort((a, b) => a.time - b.time || a.title.localeCompare(b.title));
  if (!order.length) return;
  if (live.play) {
    live.play = null;
    $("#map-caption").hidden = true;
    return kick();
  }
  live.play = { start: performance.now(), duration: reducedMotion() ? 1 : Math.min(9000, 1600 + order.length * 140), order };
  $("#map-caption").hidden = false;
  select(null);
  kick();
}

// ------------------------------------------------------------ test hooks

function debugApi() {
  return {
    screen(id) {
      const n = live?.byId.get(id);
      if (!n) return null;
      const rect = live.canvas.getBoundingClientRect();
      const s = toScreen(n.x, n.y);
      return { x: rect.left + s.x, y: rect.top + s.y };
    },
    nodes: () => live?.m.visible.map((n) => ({ id: n.id, kind: n.kind, x: n.x, y: n.y, pinned: n.fx != null })) || [],
    labels: () => live?.labels || [],
    selected: () => ui.selected,
    timeDraw(frames = 20) {
      const start = performance.now();
      for (let i = 0; i < frames; i++) draw(performance.now());
      const drawMs = (performance.now() - start) / frames;
      const t0 = performance.now();
      for (let i = 0; i < frames; i++) live.sim.tick();
      return { drawMs, tickMs: (performance.now() - t0) / frames };
    },
    path: () => [...ui.path],
    settle() {
      if (!live) return;
      for (let i = 0; i < 400 && live.sim.running; i++) live.sim.tick();
      live.sim.alpha = 0;
      for (const n of live.m.visible) ui.positions.set(n.id, { x: n.x, y: n.y, fx: n.fx ?? null, fy: n.fy ?? null });
      fit(false);
      draw(performance.now());
    },
    reset() {
      ui.settings = { ...DEFAULTS };
      ui.positions.clear();
      ui.camera = null;
      ui.selected = null;
      ui.path = [];
      ui.query = "";
      ui.linkMode = false;
      saveSettings();
    },
  };
}

// The Map view: a force-laid graph of notes and their links.

import * as core from "./core.js";
import { escapeHtml as esc } from "./md.js";
import { $, $$, TYPE_META, emptyState, icon, pageHead, plural } from "./ui.js";
import { state } from "./state.js";

// ------------------------------------------------------------ map

function layoutGraph(graph) {
  const n = graph.nodes.length;
  const pos = new Map();
  graph.nodes.forEach((node, index) => {
    const angle = (index / Math.max(n, 1)) * Math.PI * 2;
    const radius = 160 + (index % 3) * 40;
    pos.set(node.id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, vx: 0, vy: 0 });
  });
  const edges = graph.edges.map((e) => [pos.get(e.source), pos.get(e.target)]);
  const points = [...pos.values()];
  for (let step = 0; step < 360; step++) {
    const cooling = 1 - step / 360;
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const a = points[i];
        const b = points[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dist2 = dx * dx + dy * dy;
        if (dist2 < 0.01) {
          dx = (i - j) * 0.1;
          dy = 0.1;
          dist2 = 0.02;
        }
        const force = 9000 / dist2;
        const dist = Math.sqrt(dist2);
        a.vx += (dx / dist) * force;
        a.vy += (dy / dist) * force;
        b.vx -= (dx / dist) * force;
        b.vy -= (dy / dist) * force;
      }
    }
    for (const [a, b] of edges) {
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (dist - 130) * 0.02;
      a.vx += (dx / dist) * force;
      a.vy += (dy / dist) * force;
      b.vx -= (dx / dist) * force;
      b.vy -= (dy / dist) * force;
    }
    for (const p of points) {
      p.vx -= p.x * 0.008;
      p.vy -= p.y * 0.008;
      p.x += Math.max(-12, Math.min(12, p.vx)) * cooling;
      p.y += Math.max(-12, Math.min(12, p.vy)) * cooling;
      p.vx *= 0.5;
      p.vy *= 0.5;
    }
  }
  return pos;
}

// Greedy label placement: well-connected notes claim space first; each label tries below, above, right,
// then left of its dot and avoids other labels and dots. Labels with no free spot stay hidden until the
// dot is hovered or focused, so the map never shows text on top of text.
const LABEL_FONT = 11.5;
const LABEL_CHAR = LABEL_FONT * 0.54;

function placeLabels(nodes, pos, radius, labelOf, degree) {
  const boxes = nodes.map((node) => {
    const p = pos.get(node.id);
    const r = radius.get(node.id);
    return { x1: p.x - r, y1: p.y - r, x2: p.x + r, y2: p.y + r };
  });
  const overlaps = (a) => boxes.some((b) => a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2);
  const order = [...nodes].sort((a, b) => degree.get(b.id) - degree.get(a.id) || a.title.localeCompare(b.title));
  const placed = new Map();
  for (const node of order) {
    const p = pos.get(node.id);
    const r = radius.get(node.id);
    const w = labelOf(node).length * LABEL_CHAR;
    const candidates = [
      { x: 0, y: r + 14, anchor: "middle", box: [-w / 2, r + 3, w / 2, r + 17] },
      { x: 0, y: -(r + 6), anchor: "middle", box: [-w / 2, -(r + 17), w / 2, -(r + 3)] },
      { x: r + 6, y: 4, anchor: "start", box: [r + 6, -7, r + 6 + w, 7] },
      { x: -(r + 6), y: 4, anchor: "end", box: [-(r + 6) - w, -7, -(r + 6), 7] },
    ];
    const pad = 2;
    const fit = candidates.find((c) => {
      const box = { x1: p.x + c.box[0] - pad, y1: p.y + c.box[1] - pad, x2: p.x + c.box[2] + pad, y2: p.y + c.box[3] + pad };
      if (overlaps(box)) return false;
      c.claim = box;
      return true;
    });
    if (fit) {
      boxes.push(fit.claim);
      placed.set(node.id, { x: fit.x, y: fit.y, anchor: fit.anchor, quiet: false });
    } else {
      placed.set(node.id, { ...candidates[0], quiet: true });
    }
  }
  return placed;
}

let mapState = null;

export function mapView() {
  const graph = core.buildGraph(state.notes);
  if (!graph.nodes.length) {
    return `${pageHead("Map", "How your ideas, tasks, decisions and notes connect.")}<div class="card">${emptyState("map", "Nothing to map yet", "Notes appear here once you have some. Link them with [[file-name]] to draw connections.")}</div>`;
  }
  const pos = layoutGraph(graph);
  const degree = new Map(graph.nodes.map((n) => [n.id, 0]));
  for (const e of graph.edges) {
    degree.set(e.source, degree.get(e.source) + 1);
    degree.set(e.target, degree.get(e.target) + 1);
  }
  const xs = [...pos.values()].map((p) => p.x);
  const ys = [...pos.values()].map((p) => p.y);
  const pad = 90;
  const box = { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + pad * 2, h: Math.max(...ys) - Math.min(...ys) + pad * 2 };
  mapState = { box: { ...box }, edges: graph.edges };
  const edgeSvg = graph.edges
    .map((e) => {
      const a = pos.get(e.source);
      const b = pos.get(e.target);
      return `<line class="map-edge" data-a="${esc(e.source)}" data-b="${esc(e.target)}" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"/>`;
    })
    .join("");
  const radius = new Map(graph.nodes.map((n) => [n.id, 7 + Math.sqrt(degree.get(n.id)) * 3]));
  const labelOf = (node) => (node.title.length > 28 ? node.title.slice(0, 27) + "…" : node.title);
  const labels = placeLabels(graph.nodes, pos, radius, labelOf, degree);
  const nodeSvg = graph.nodes
    .map((node) => {
      const p = pos.get(node.id);
      const r = radius.get(node.id);
      const spot = labels.get(node.id);
      return `<g class="map-node" data-open="${esc(node.id)}" data-node="${esc(node.id)}" transform="translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})" tabindex="0" role="button" aria-label="${esc(node.title)}">
        <circle r="${r.toFixed(1)}" fill="var(--${node.type})"/><text${spot.quiet ? ' class="quiet"' : ""} x="${spot.x.toFixed(1)}" y="${spot.y.toFixed(1)}" text-anchor="${spot.anchor}">${esc(labelOf(node))}</text></g>`;
    })
    .join("");
  const legend = core.TYPES.map((t) => `<span><i class="dot t-${t}"></i>${TYPE_META[t].label}</span>`).join("");
  return `${pageHead("Map", `${plural(graph.nodes.length, "note")} and ${plural(graph.edges.length, "link")}. Hover to see neighbours, scroll to zoom, drag to pan, click to open.`,
    `<button class="btn" data-action="map-reset">${icon("refresh")} Reset view</button>`)}
    <div class="card map-wrap" id="map-wrap">
      <svg id="map-svg" viewBox="${box.x} ${box.y} ${box.w} ${box.h}" aria-label="Note map">${edgeSvg}${nodeSvg}</svg>
      <div class="map-legend">${legend}</div>
    </div>`;
}

export function wireMap() {
  const svg = $("#map-svg");
  const wrap = $("#map-wrap");
  if (!svg || !mapState) return;
  const setBox = () => svg.setAttribute("viewBox", `${mapState.box.x} ${mapState.box.y} ${mapState.box.w} ${mapState.box.h}`);
  $$(".map-node", svg).forEach((node) => {
    node.addEventListener("mouseenter", () => {
      const id = node.dataset.node;
      wrap.classList.add("focus");
      node.classList.add("lit");
      $$(".map-edge", svg).forEach((edge) => {
        if (edge.dataset.a === id || edge.dataset.b === id) {
          edge.classList.add("lit");
          const other = edge.dataset.a === id ? edge.dataset.b : edge.dataset.a;
          $$(".map-node", svg).find((n) => n.dataset.node === other)?.classList.add("lit");
        }
      });
    });
    node.addEventListener("mouseleave", () => {
      wrap.classList.remove("focus");
      $$(".lit", svg).forEach((el) => el.classList.remove("lit"));
    });
  });
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = svg.getBoundingClientRect();
    const scale = event.deltaY > 0 ? 1.12 : 1 / 1.12;
    const mx = mapState.box.x + ((event.clientX - rect.left) / rect.width) * mapState.box.w;
    const my = mapState.box.y + ((event.clientY - rect.top) / rect.height) * mapState.box.h;
    mapState.box.w *= scale;
    mapState.box.h *= scale;
    mapState.box.x = mx - (mx - mapState.box.x) * scale;
    mapState.box.y = my - (my - mapState.box.y) * scale;
    setBox();
  }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".map-node")) return;
    drag = { x: event.clientX, y: event.clientY, box: { ...mapState.box } };
    svg.classList.add("panning");
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const rect = svg.getBoundingClientRect();
    mapState.box.x = drag.box.x - ((event.clientX - drag.x) / rect.width) * drag.box.w;
    mapState.box.y = drag.box.y - ((event.clientY - drag.y) / rect.height) * drag.box.h;
    setBox();
  });
  const end = () => {
    drag = null;
    svg.classList.remove("panning");
  };
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
}

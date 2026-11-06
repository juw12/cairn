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
  const nodeSvg = graph.nodes
    .map((node) => {
      const p = pos.get(node.id);
      const r = 7 + Math.sqrt(degree.get(node.id)) * 3;
      const label = node.title.length > 28 ? node.title.slice(0, 27) + "…" : node.title;
      return `<g class="map-node" data-open="${esc(node.id)}" data-node="${esc(node.id)}" transform="translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})" tabindex="0" role="button" aria-label="${esc(node.title)}">
        <circle r="${r.toFixed(1)}" fill="var(--${node.type})"/><text y="${(r + 14).toFixed(1)}" text-anchor="middle">${esc(label)}</text></g>`;
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

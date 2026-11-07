// The Map in 3D: the same notes and links laid out in space and drawn on a canvas with perspective,
// depth fog and shaded spheres. Dragging turns the map like a trackball, so it rotates a full 360°
// in every direction with no top or bottom to get stuck at. Right-drag or Shift-drag pans, scroll or
// pinch zooms, click selects, double-click opens. Plain canvas 2D: no libraries, works offline.

import { createSimulation3D } from "./graph.js";

const FOV = (50 * Math.PI) / 180;
const NEAR = 8;
const ROTATE_PER_PIXEL = 0.0065; // radians
const SPIN_PER_MS = 0.00021; // about one turn every 30 seconds
const LABEL_FONT = 12;
const MAX_LABELS = 70;

// The 3D layout and the camera survive re-renders (filters, saves) and switching between 2D and 3D.
const positions = new Map();
let savedCamera = null;

export function reset3D() {
  positions.clear();
  savedCamera = null;
}

// ------------------------------------------------------------ 3x3 rotation matrices (row-major)

function multiply(a, b) {
  const out = new Array(9);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      out[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
    }
  }
  return out;
}

function rotX(t) {
  const c = Math.cos(t);
  const s = Math.sin(t);
  return [1, 0, 0, 0, c, -s, 0, s, c];
}

function rotY(t) {
  const c = Math.cos(t);
  const s = Math.sin(t);
  return [c, 0, s, 0, 1, 0, -s, 0, c];
}

// Keep the rows unit length and perpendicular so thousands of small turns never skew the view.
function orthonormalise(m) {
  const norm = (v) => {
    const l = Math.hypot(v[0], v[1], v[2]) || 1;
    return v.map((x) => x / l);
  };
  const a = norm([m[0], m[1], m[2]]);
  let b = [m[3], m[4], m[5]];
  const d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  b = norm([b[0] - d * a[0], b[1] - d * a[1], b[2] - d * a[2]]);
  const c = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  return [...a, ...b, ...c];
}

// Camera-space vector back to world space (the transpose of a rotation is its inverse).
function toWorld(m, x, y, z) {
  return {
    x: m[0] * x + m[3] * y + m[6] * z,
    y: m[1] * x + m[4] * y + m[7] * z,
    z: m[2] * x + m[5] * y + m[8] * z,
  };
}

function hash(text) {
  let h = 2166136261;
  for (const ch of text) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) / 4294967295) * 2 - 1;
}

const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));
const ease = (t) => 1 - Math.pow(1 - t, 3);

// ------------------------------------------------------------ colours (CSS variables -> canvas)

function parseColour(text) {
  const value = String(text || "").trim();
  let match = /^#([0-9a-f]{3})$/i.exec(value);
  if (match) return match[1].split("").map((h) => parseInt(h + h, 16));
  match = /^#([0-9a-f]{6})/i.exec(value);
  if (match) return [0, 2, 4].map((i) => parseInt(match[1].slice(i, i + 2), 16));
  match = /rgba?\(([^)]+)\)/i.exec(value);
  if (match) return match[1].split(/[\s,/]+/).filter(Boolean).slice(0, 3).map(Number);
  return [128, 128, 128];
}

const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const rgba = (c, alpha = 1) => `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;

function readPalette() {
  const style = getComputedStyle(document.documentElement);
  const get = (name) => parseColour(style.getPropertyValue(name));
  return {
    style,
    surface: get("--surface"),
    bg: get("--bg"),
    ink: get("--ink"),
    ink2: get("--ink-2"),
    muted: get("--muted"),
    line: get("--line-strong"),
    accent: get("--accent"),
    sunken: get("--sunken"),
    font: style.getPropertyValue("--font").trim() || "system-ui, sans-serif",
    dark: document.documentElement.dataset.theme === "dark" || get("--bg").reduce((s, v) => s + v, 0) < 200,
  };
}

// A lit sphere, pre-rendered once per colour and scaled with drawImage: fast enough for thousands.
function sphereSprite(base, rim) {
  const size = 96;
  const sprite = document.createElement("canvas");
  sprite.width = size;
  sprite.height = size;
  const g = sprite.getContext("2d");
  const half = size / 2;
  const fill = g.createRadialGradient(size * 0.36, size * 0.3, size * 0.03, half, half, half);
  fill.addColorStop(0, rgba(mix(base, [255, 255, 255], 0.62)));
  fill.addColorStop(0.42, rgba(base));
  fill.addColorStop(1, rgba(mix(base, [0, 0, 0], 0.48)));
  g.fillStyle = fill;
  g.beginPath();
  g.arc(half, half, half - 2, 0, Math.PI * 2);
  g.fill();
  g.lineWidth = 2.5;
  g.strokeStyle = rgba(rim, 0.85);
  g.stroke();
  return sprite;
}

// ------------------------------------------------------------ the view

export function createView3D(options) {
  const { canvas, model, session, colourOf, label, onSelect, onOpen, onHover, onSpinChange, onInteract } = options;
  const ctx = canvas.getContext("2d");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const arrows = Boolean(options.arrows);

  // Bodies: one per dot, seeded from the 2D layout so clusters keep their place between modes.
  const bodies = new Map();
  let unknown = 0;
  for (const node of model.nodes) {
    const body = { id: node.id, node, x: NaN, y: NaN, z: NaN };
    const known = positions.get(node.id);
    if (known) {
      Object.assign(body, known);
    } else {
      unknown += 1;
      const anchor = model.edges
        .filter((e) => e.source === node.id || e.target === node.id)
        .map((e) => positions.get(e.source === node.id ? e.target : e.source))
        .find(Boolean);
      if (anchor) {
        body.x = anchor.x + 14 * hash(`${node.id}x`);
        body.y = anchor.y + 14 * hash(`${node.id}y`);
        body.z = anchor.z + 14 * hash(`${node.id}z`);
      } else if (Number.isFinite(node.x) && Number.isFinite(node.y)) {
        body.x = node.x * 0.85;
        body.y = node.y * 0.85;
        body.z = 150 * hash(node.id);
      }
    }
    bodies.set(node.id, body);
  }
  const list = [...bodies.values()];
  const sim = createSimulation3D(list, model.edges, { alpha: unknown === 0 ? 0 : unknown > list.length * 0.4 ? 1 : 0.5 });
  if (!sim.settled()) sim.run(650);

  const signature = list.map((b) => b.id).join("|");
  const sameView = savedCamera && savedCamera.signature === signature;
  const cam = {
    rot: savedCamera ? [...savedCamera.rot] : multiply(rotX(-0.38), rotY(0.6)),
    target: sameView ? { ...savedCamera.target } : { x: 0, y: 0, z: 0 },
    dist: sameView ? savedCamera.dist : 650,
  };
  let interacted = sameView;
  let spinning = Boolean(options.spin) && !reducedMotion;

  // ---------------------------------------------------------- sizing

  let width = 800;
  let height = 500;
  let dpr = 1;
  const focal = () => height / 2 / Math.tan(FOV / 2);
  function resize() {
    const rect = canvas.getBoundingClientRect();
    width = rect.width || 800;
    height = rect.height || 500;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    request();
  }
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);

  let palette = readPalette();
  const sprites = new Map();
  const resolved = new Map();
  function colourFor(node) {
    const key = colourOf(node);
    if (!resolved.has(key)) {
      const name = /^var\((--[^)]+)\)$/.exec(key)?.[1];
      resolved.set(key, parseColour(name ? palette.style.getPropertyValue(name) : key));
    }
    return resolved.get(key);
  }
  function spriteFor(node) {
    const base = colourFor(node);
    const key = base.join(",");
    if (!sprites.has(key)) sprites.set(key, sphereSprite(base, palette.surface));
    return sprites.get(key);
  }
  function refreshColours() {
    palette = readPalette();
    sprites.clear();
    resolved.clear();
    widths.clear();
    request();
  }
  const themeObserver = new MutationObserver(refreshColours);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class", "style"] });
  const colourScheme = matchMedia("(prefers-color-scheme: dark)");
  colourScheme.addEventListener?.("change", refreshColours);

  // ---------------------------------------------------------- projection

  const projected = new Map();
  function project() {
    const R = cam.rot;
    const t = cam.target;
    const f = focal();
    const cx = width / 2;
    const cy = height / 2;
    projected.clear();
    for (const b of list) {
      if (session.future?.has(b.id)) continue;
      const px = b.x - t.x;
      const py = b.y - t.y;
      const pz = b.z - t.z;
      const vx = R[0] * px + R[1] * py + R[2] * pz;
      const vy = R[3] * px + R[4] * py + R[5] * pz;
      const vz = R[6] * px + R[7] * py + R[8] * pz;
      const depth = vz + cam.dist;
      if (depth < NEAR) continue;
      const s = f / depth;
      projected.set(b.id, { x: cx + vx * s, y: cy + vy * s, s, depth, body: b, rad: Math.max(1.6, b.node.r * s) });
    }
  }

  // ---------------------------------------------------------- camera moves

  function rotate(yaw, pitch) {
    cam.rot = orthonormalise(multiply(multiply(rotY(yaw), rotX(pitch)), cam.rot));
  }

  function pan(dx, dy) {
    const s = focal() / cam.dist;
    const move = toWorld(cam.rot, -dx / s, -dy / s, 0);
    cam.target.x += move.x;
    cam.target.y += move.y;
    cam.target.z += move.z;
  }

  function maxDistance() {
    let radius = 200;
    for (const b of list) radius = Math.max(radius, Math.hypot(b.x, b.y, b.z));
    return radius * 12;
  }

  // Zoom towards the point under the pointer (or the centre).
  function zoomAt(factor, sx = width / 2, sy = height / 2) {
    const next = clamp(cam.dist * factor, 25, maxDistance());
    const actual = next / cam.dist;
    const s = focal() / cam.dist;
    const offset = toWorld(cam.rot, (sx - width / 2) / s, (sy - height / 2) / s, 0);
    cam.target.x += offset.x * (1 - actual);
    cam.target.y += offset.y * (1 - actual);
    cam.target.z += offset.z * (1 - actual);
    cam.dist = next;
  }

  let tween = null;
  function moveCamera(target, dist, animate = true) {
    if (!animate || reducedMotion) {
      cam.target = { ...target };
      cam.dist = dist;
      tween = null;
    } else {
      tween = { from: { ...cam.target }, fromDist: cam.dist, to: { ...target }, toDist: dist, start: performance.now(), ms: 480 };
    }
    request();
  }

  function fit(ids = null, animate = true) {
    const chosen = ids ? list.filter((b) => ids.includes(b.id)) : list;
    if (!chosen.length) return;
    let x0 = Infinity;
    let y0 = Infinity;
    let z0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    let z1 = -Infinity;
    for (const b of chosen) {
      x0 = Math.min(x0, b.x);
      y0 = Math.min(y0, b.y);
      z0 = Math.min(z0, b.z);
      x1 = Math.max(x1, b.x);
      y1 = Math.max(y1, b.y);
      z1 = Math.max(z1, b.z);
    }
    const centre = { x: (x0 + x1) / 2, y: (y0 + y1) / 2, z: (z0 + z1) / 2 };
    let radius = 60;
    for (const b of chosen) radius = Math.max(radius, Math.hypot(b.x - centre.x, b.y - centre.y, b.z - centre.z) + b.node.r);
    const halfY = FOV / 2;
    const halfX = Math.atan(Math.tan(halfY) * (width / height));
    // A bounding sphere seen from outside: distance so it fills the narrower field of view, plus room for labels.
    const dist = (radius / Math.sin(Math.min(halfX, halfY))) * 1.04 + 40;
    moveCamera(centre, dist, animate);
  }

  function focusOn(id) {
    const b = bodies.get(id);
    if (!b) return;
    moveCamera({ x: b.x, y: b.y, z: b.z }, Math.min(cam.dist, 700));
  }

  // ---------------------------------------------------------- drawing

  const widths = new Map();
  function textWidth(id, text) {
    if (!widths.has(id)) {
      ctx.font = `${LABEL_FONT}px ${palette.font}`;
      widths.set(id, ctx.measureText(text).width);
    }
    return widths.get(id);
  }

  function focusState() {
    const focusId = session.hover || session.selected;
    const lit = new Set();
    if (focusId) {
      lit.add(focusId);
      for (const other of model.adj.get(focusId) || []) lit.add(other);
    }
    if (session.alsoLit) lit.add(session.alsoLit);
    const path = session.path ? new Set(session.path) : null;
    const steps = new Set();
    (session.path || []).forEach((id, i) => {
      if (!i) return;
      steps.add(`${session.path[i - 1]}\u0000${id}`);
      steps.add(`${id}\u0000${session.path[i - 1]}`);
    });
    return { focusId, lit, path, steps, mode: path ? "path" : focusId ? "focus" : null };
  }

  function draw() {
    project();
    const p = palette;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // Backdrop: a soft glow in the middle fading outwards, so the space reads as depth.
    const glow = ctx.createRadialGradient(width / 2, height * 0.45, 0, width / 2, height / 2, Math.hypot(width, height) * 0.62);
    glow.addColorStop(0, rgba(mix(p.surface, p.dark ? [255, 255, 255] : p.surface, p.dark ? 0.03 : 0)));
    glow.addColorStop(1, rgba(mix(p.surface, p.dark ? [0, 0, 0] : p.ink, p.dark ? 0.35 : 0.06)));
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, width, height);

    let near = Infinity;
    let far = -Infinity;
    for (const q of projected.values()) {
      near = Math.min(near, q.depth);
      far = Math.max(far, q.depth);
    }
    const fog = (depth) => (far > near ? 1 - 0.72 * ((depth - near) / (far - near)) : 1);
    const focus = focusState();

    // Painter's algorithm: links and dots together, farthest first, so near dots hide what's behind.
    const items = [];
    for (const e of model.edges) {
      const a = projected.get(e.source);
      const b = projected.get(e.target);
      if (a && b) items.push({ edge: e, a, b, depth: (a.depth + b.depth) / 2 });
    }
    for (const q of projected.values()) items.push({ dot: q, depth: q.depth - 0.001 });
    items.sort((u, v) => v.depth - u.depth);
    for (const item of items) {
      if (item.edge) drawEdge(item, fog, focus);
      else drawDot(item.dot, fog, focus);
    }
    ctx.globalAlpha = 1;
    drawSuggestions();
    drawLabels(fog, focus);
  }

  function drawEdge({ edge, a, b, depth }, fog, focus) {
    const isLit = focus.focusId && (edge.source === focus.focusId || edge.target === focus.focusId);
    const onPath = focus.path && focus.steps.has(`${edge.source}\u0000${edge.target}`);
    let alpha = fog(depth) * 0.8;
    if (focus.mode === "path" && !onPath) alpha *= 0.1;
    else if (focus.mode === "focus" && !isLit) alpha *= 0.14;
    // Stop at the rims so lines never cross the face of a sphere.
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const length = Math.hypot(dx, dy);
    if (length < a.rad + b.rad + 1) return;
    const ux = dx / length;
    const uy = dy / length;
    const x1 = a.x + ux * (a.rad + 1);
    const y1 = a.y + uy * (a.rad + 1);
    const x2 = b.x - ux * (b.rad + 1);
    const y2 = b.y - uy * (b.rad + 1);
    const scale = (a.s + b.s) / 2;
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = rgba(onPath || isLit ? palette.accent : palette.line);
    ctx.lineWidth = clamp(scale * (onPath ? 3 : isLit ? 1.9 : 1.15), 0.5, 4.5);
    ctx.setLineDash(edge.kind ? [3, 3] : []);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.setLineDash([]);
    if (arrows) {
      const size = clamp(6 * scale, 3, 9);
      const drawHead = (x, y, vx, vy) => {
        ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x - vx * size - vy * size * 0.55, y - vy * size + vx * size * 0.55);
        ctx.lineTo(x - vx * size + vy * size * 0.55, y - vy * size - vx * size * 0.55);
        ctx.fill();
      };
      drawHead(x2, y2, ux, uy);
      if (edge.both) drawHead(x1, y1, -ux, -uy);
    }
  }

  function drawDot(q, fog, focus) {
    const node = q.body.node;
    const id = node.id;
    let alpha = fog(q.depth);
    const floor = palette.dark ? 0.32 : 0.2;
    if (focus.mode === "path" && !focus.path.has(id)) alpha *= floor;
    else if (focus.mode === "focus" && !focus.lit.has(id)) alpha *= floor;
    ctx.globalAlpha = alpha;
    const r = q.rad;
    if (node.kind === "note") {
      ctx.drawImage(spriteFor(node), q.x - r, q.y - r, r * 2, r * 2);
    } else if (node.kind === "ghost") {
      ctx.fillStyle = rgba(palette.surface);
      ctx.strokeStyle = rgba(palette.muted);
      ctx.lineWidth = 1.2;
      ctx.setLineDash([3, 2]);
      ctx.beginPath();
      ctx.arc(q.x, q.y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.setLineDash([]);
    } else {
      ctx.fillStyle = rgba(palette.sunken);
      ctx.strokeStyle = rgba(palette.muted);
      ctx.lineWidth = 1.2;
      ctx.fillRect(q.x - r, q.y - r, r * 2, r * 2);
      ctx.strokeRect(q.x - r, q.y - r, r * 2, r * 2);
    }
    const ring = id === session.selected || focus.path?.has(id) ? palette.accent : id === session.hover ? palette.ink : null;
    if (ring) {
      ctx.globalAlpha = Math.max(alpha, 0.9);
      ctx.strokeStyle = rgba(ring);
      ctx.lineWidth = id === session.selected ? 2.6 : 1.6;
      ctx.beginPath();
      ctx.arc(q.x, q.y, r + 3, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  // Dashed lines for the selected note's unlinked mentions: links you could make in one click.
  function drawSuggestions() {
    const id = session.selected;
    if (!id) return;
    ctx.globalAlpha = 0.85;
    ctx.strokeStyle = rgba(palette.accent);
    ctx.lineWidth = 1.4;
    ctx.setLineDash([5, 4]);
    for (const s of model.mentions) {
      if (s.from !== id && s.to !== id) continue;
      const a = projected.get(s.from);
      const b = projected.get(s.to);
      if (!a || !b) continue;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }

  // Greedy labels in screen space: the focused note, the path and neighbours first, then the
  // best-connected and nearest notes; a label is skipped when it would overlap another label, a
  // dot or an overlay (legend, toolbar, card). Far-away small dots stay unlabelled until you come closer.
  function drawLabels(fog, focus) {
    const canvasRect = canvas.getBoundingClientRect();
    const boxes = [];
    for (const el of canvas.parentElement?.querySelectorAll(".map-legend, .map-zoom, .map-chip, .map-card:not([hidden]), .map-hint") || []) {
      const r = el.getBoundingClientRect();
      if (!r.width) continue;
      boxes.push({ x1: r.left - canvasRect.left, y1: r.top - canvasRect.top, x2: r.right - canvasRect.left, y2: r.bottom - canvasRect.top });
    }
    const dots = [...projected.values()];
    for (const q of dots) {
      if (q.rad > 4) boxes.push({ x1: q.x - q.rad, y1: q.y - q.rad, x2: q.x + q.rad, y2: q.y + q.rad, owner: q.body.id });
    }
    const important = (id) => id === focus.focusId || focus.path?.has(id) || (focus.focusId && focus.lit.has(id));
    const priority = (q) => {
      const id = q.body.id;
      return (id === focus.focusId ? 1e7 : 0) + (focus.path?.has(id) ? 1e6 : 0) + (focus.lit.has(id) ? 1e5 : 0) + model.degree(id) * 40 + q.s * 100;
    };
    const order = dots
      .filter((q) => q.x > -50 && q.x < width + 50 && q.y > -20 && q.y < height + 20)
      .sort((a, b) => priority(b) - priority(a));
    let shown = 0;
    ctx.textBaseline = "alphabetic";
    for (const q of order) {
      const id = q.body.id;
      const must = important(id);
      if (!must && (shown >= MAX_LABELS || q.rad < 3.4 || (focus.mode && !must))) continue;
      const text = label(q.body.node);
      const size = clamp(LABEL_FONT * (0.8 + 0.3 * q.s), 10, 14.5);
      const w = textWidth(id, text) * (size / LABEL_FONT) * (id === session.selected ? 1.08 : 1);
      const h = size + 3;
      const spots = [
        { x: q.x, y: q.y + q.rad + 3 + size * 0.9, box: [q.x - w / 2, q.y + q.rad + 2, q.x + w / 2, q.y + q.rad + 2 + h] },
        { x: q.x, y: q.y - q.rad - 5, box: [q.x - w / 2, q.y - q.rad - 3 - h, q.x + w / 2, q.y - q.rad - 3] },
      ];
      let spot = null;
      for (const s of spots) {
        const [x1, y1, x2, y2] = s.box;
        const inside = x1 >= 4 && x2 <= width - 4 && y1 >= 4 && y2 <= height - 4;
        const clash = boxes.some((b) => b.owner !== id && x1 < b.x2 && b.x1 < x2 && y1 < b.y2 && b.y1 < y2);
        if (inside && !clash) {
          spot = s;
          boxes.push({ x1, y1, x2, y2 });
          break;
        }
      }
      if (!spot) continue;
      shown += 1;
      const selected = id === session.selected;
      ctx.font = `${selected ? 600 : 400} ${size.toFixed(1)}px ${palette.font}`;
      ctx.textAlign = "center";
      ctx.globalAlpha = must ? 1 : clamp(fog(q.depth) + 0.15, 0.35, 1);
      ctx.lineJoin = "round";
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = rgba(palette.surface, 0.92);
      ctx.strokeText(text, spot.x, spot.y);
      ctx.fillStyle = rgba(selected ? palette.ink : q.body.node.kind === "note" ? palette.ink2 : palette.muted);
      ctx.fillText(text, spot.x, spot.y);
    }
    ctx.globalAlpha = 1;
  }

  // ---------------------------------------------------------- animation loop (runs only while something moves)

  let raf = 0;
  let lastTime = 0;
  let inertia = null;
  let gesture = null;
  let destroyed = false;
  let frames = 0;

  function request() {
    if (!raf && !destroyed) raf = requestAnimationFrame(frame);
  }

  function frame(time) {
    raf = 0;
    if (!canvas.isConnected) {
      destroy();
      return;
    }
    const dt = lastTime ? Math.min(50, time - lastTime) : 16;
    lastTime = time;
    let active = false;
    if (!sim.settled()) {
      sim.tick();
      active = true;
      if (sim.settled()) {
        savePositions();
        if (!interacted) fit(null, true);
      } else if (!interacted && frames % 12 === 0) {
        fit(null, false);
      }
    }
    if (spinning && !gesture) {
      rotate(-SPIN_PER_MS * dt, 0);
      active = true;
    }
    if (inertia) {
      rotate(inertia.yaw, inertia.pitch);
      inertia.yaw *= 0.92;
      inertia.pitch *= 0.92;
      if (Math.abs(inertia.yaw) + Math.abs(inertia.pitch) < 2e-4) inertia = null;
      else active = true;
    }
    if (tween) {
      const t = clamp((time - tween.start) / tween.ms, 0, 1);
      const k = ease(t);
      cam.target = {
        x: tween.from.x + (tween.to.x - tween.from.x) * k,
        y: tween.from.y + (tween.to.y - tween.from.y) * k,
        z: tween.from.z + (tween.to.z - tween.from.z) * k,
      };
      cam.dist = tween.fromDist + (tween.toDist - tween.fromDist) * k;
      if (t >= 1) tween = null;
      else active = true;
    }
    frames += 1;
    draw();
    if (active) request();
    else lastTime = 0;
  }

  function savePositions() {
    for (const b of list) positions.set(b.id, { x: b.x, y: b.y, z: b.z });
  }

  function setSpin(on) {
    spinning = Boolean(on);
    onSpinChange?.(spinning);
    request();
  }

  function noteInteraction() {
    interacted = true;
    tween = null;
    if (spinning) setSpin(false);
    onInteract?.();
  }

  // ---------------------------------------------------------- pointer, wheel and touch

  function pointerPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function hitTest(event) {
    const { x, y } = pointerPoint(event);
    let best = null;
    for (const q of projected.values()) {
      const reach = Math.max(6, q.rad) + 3;
      if (Math.hypot(q.x - x, q.y - y) <= reach && (!best || q.depth < best.depth)) best = q;
    }
    return best?.body.id || null;
  }

  const pointers = new Map();
  let lastMoveTime = 0;
  let hoverId = null;

  canvas.addEventListener("pointerdown", (event) => {
    canvas.focus({ preventScroll: true });
    pointers.set(event.pointerId, pointerPoint(event));
    inertia = null;
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      gesture = { kind: "pinch", d: Math.hypot(a.x - b.x, a.y - b.y), mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, dist: cam.dist };
      noteInteraction();
      return;
    }
    const panning = event.button === 1 || event.button === 2 || event.shiftKey || event.ctrlKey || event.metaKey;
    const start = pointerPoint(event);
    gesture = { kind: panning ? "pan" : "orbit", start, last: start, moved: false };
  });

  canvas.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) {
      const id = hitTest(event);
      if (id !== hoverId) {
        hoverId = id;
        canvas.style.cursor = id ? "pointer" : "";
        onHover?.(id);
      }
      return;
    }
    const point = pointerPoint(event);
    pointers.set(event.pointerId, point);
    if (gesture?.kind === "pinch" && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.max(20, Math.hypot(a.x - b.x, a.y - b.y));
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      cam.dist = clamp(gesture.dist * (gesture.d / d), 25, maxDistance());
      pan(mid.x - gesture.mid.x, mid.y - gesture.mid.y);
      gesture.mid = mid;
      request();
      return;
    }
    if (!gesture || gesture.kind === "pinch") return;
    if (!gesture.moved) {
      if (Math.hypot(point.x - gesture.start.x, point.y - gesture.start.y) < 4) return;
      gesture.moved = true;
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add("dragging");
      noteInteraction();
    }
    const dx = point.x - gesture.last.x;
    const dy = point.y - gesture.last.y;
    gesture.last = point;
    if (gesture.kind === "orbit") {
      const yaw = -dx * ROTATE_PER_PIXEL;
      const pitch = dy * ROTATE_PER_PIXEL;
      rotate(yaw, pitch);
      inertia = { yaw, pitch };
      lastMoveTime = performance.now();
    } else {
      pan(dx, dy);
    }
    request();
  });

  const end = (event) => {
    pointers.delete(event.pointerId);
    canvas.classList.remove("dragging");
    if (gesture?.kind === "pinch") {
      if (!pointers.size) gesture = null;
      return;
    }
    const done = gesture;
    gesture = null;
    if (!done) return;
    if (!done.moved) {
      inertia = null;
      if (event.type === "pointerup") onSelect?.(hitTest(event));
      return;
    }
    // Let a flick keep turning and slow down by itself.
    if (done.kind === "orbit" && inertia && !reducedMotion && performance.now() - lastMoveTime < 80) request();
    else inertia = null;
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", () => {
    if (hoverId && !pointers.size) {
      hoverId = null;
      canvas.style.cursor = "";
      onHover?.(null);
    }
  });
  canvas.addEventListener("dblclick", (event) => {
    const id = hitTest(event);
    if (id) onOpen?.(id);
  });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const lines = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1;
    const point = pointerPoint(event);
    noteInteraction();
    zoomAt(Math.exp(clamp(event.deltaY * lines, -200, 200) * 0.0015), point.x, point.y);
    request();
  }, { passive: false });

  // ---------------------------------------------------------- lifecycle

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    resizeObserver.disconnect();
    themeObserver.disconnect();
    colourScheme.removeEventListener?.("change", refreshColours);
    savePositions();
    savedCamera = { rot: [...cam.rot], target: { ...cam.target }, dist: cam.dist, signature };
  }

  resize();
  if (!sameView) fit(null, false);
  draw();
  request();

  return {
    redraw: request,
    fit: (ids = null) => {
      interacted = true;
      fit(ids, true);
    },
    zoom: (factor) => {
      noteInteraction();
      zoomAt(factor);
      request();
    },
    rotateBy: (yaw, pitch) => {
      noteInteraction();
      rotate(yaw, pitch);
      request();
    },
    focusOn: (id) => {
      interacted = true;
      focusOn(id);
    },
    screenPos: (id) => {
      project();
      const q = projected.get(id);
      return q ? { x: q.x, y: q.y, depth: q.depth } : null;
    },
    setSpin,
    get spinning() {
      return spinning;
    },
    get camera() {
      return { rot: [...cam.rot], target: { ...cam.target }, dist: cam.dist };
    },
    get settled() {
      return sim.settled();
    },
    bodies: () => list.map((b) => ({ id: b.id, x: b.x, y: b.y, z: b.z })),
    destroy,
  };
}

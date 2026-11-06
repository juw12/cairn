// Graph model and physics for the Map. Pure: no DOM, no storage.
//
// buildMapGraph  notes -> nodes + directed edges, with optional "ghost" nodes for [[links]] to notes that
//                don't exist yet and tag hub nodes.
// Simulation     a small force layout (many-body repulsion via a Barnes-Hut quadtree, springs on links,
//                gentle gravity), the same model d3-force uses, so thousands of notes stay smooth.
// neighbourhood, shortestPath, clusters: the analysis behind local graph, path finding and colouring.

import * as core from "./core.js";

const LINK_RE = /\[\[([^\]\n]+)\]\]/g;
const ATTACHMENT_RE = /\.(png|jpe?g|gif|webp|svg|bmp|pdf|mp3|mp4|webm|mov|wav|ogg|m4a|canvas|excalidraw|base|csv|zip)$/i;

function linkLabel(raw) {
  const target = raw.split("|")[0].split("#")[0].trim().replaceAll("\\", "/").split("/").pop();
  return target.replace(/\.md$/i, "") || raw;
}

function noteTime(note) {
  const created = typeof note.meta?.created === "string" ? Date.parse(note.meta.created) : NaN;
  if (!Number.isNaN(created)) return created;
  if (note.type === "journal" && /^\d{4}-\d{2}-\d{2}$/.test(note.slug)) return Date.parse(note.slug);
  return note.mtime || 0;
}

export function buildMapGraph(notes, { ghosts = true, tags = false } = {}) {
  const byName = new Map();
  for (const note of notes) {
    for (const name of core.noteNames(note)) if (!byName.has(name)) byName.set(name, note.path);
  }
  const nodes = notes.map((note) => ({
    id: note.path,
    kind: "note",
    title: note.title,
    type: note.type,
    status: note.status,
    tags: note.tags,
    folder: note.path.includes("/") ? note.path.slice(0, note.path.lastIndexOf("/")) : "",
    time: noteTime(note),
  }));
  const index = new Map(nodes.map((n) => [n.id, n]));
  const edges = [];
  const seen = new Map();
  const addEdge = (source, target, kind) => {
    const key = `${source}\u0000${target}`;
    if (seen.has(key)) return;
    const reverse = seen.get(`${target}\u0000${source}`);
    if (reverse) {
      reverse.mutual = true;
      seen.set(key, reverse);
      return;
    }
    const edge = { source, target, kind, mutual: false };
    seen.set(key, edge);
    edges.push(edge);
  };
  for (const note of notes) {
    for (const match of note.body.matchAll(LINK_RE)) {
      const raw = match[1];
      if (ATTACHMENT_RE.test(raw.split("|")[0].split("#")[0].trim())) continue;
      const slug = core.linkSlug(raw);
      if (!slug) continue;
      const target = byName.get(slug);
      if (target) {
        if (target !== note.path) addEdge(note.path, target, "link");
      } else if (ghosts) {
        const id = `ghost:${slug}`;
        if (!index.has(id)) {
          const node = { id, kind: "ghost", title: linkLabel(raw), type: "ghost", status: "", tags: [], folder: "", time: noteTime(note) };
          index.set(id, node);
          nodes.push(node);
        }
        addEdge(note.path, id, "ghost");
      }
    }
    if (tags) {
      for (const tag of note.tags) {
        const id = `tag:${tag}`;
        if (!index.has(id)) {
          const node = { id, kind: "tag", title: `#${tag}`, type: "tag", status: "", tags: [], folder: "", time: 0 };
          index.set(id, node);
          nodes.push(node);
        }
        addEdge(note.path, id, "tag");
      }
    }
  }
  const degree = new Map(nodes.map((n) => [n.id, 0]));
  const linksOut = new Map(nodes.map((n) => [n.id, 0]));
  const linksIn = new Map(nodes.map((n) => [n.id, 0]));
  for (const e of edges) {
    degree.set(e.source, degree.get(e.source) + 1);
    degree.set(e.target, degree.get(e.target) + 1);
    if (e.kind !== "tag") {
      linksOut.set(e.source, linksOut.get(e.source) + 1);
      linksIn.set(e.target, linksIn.get(e.target) + 1);
      if (e.mutual) {
        linksOut.set(e.target, linksOut.get(e.target) + 1);
        linksIn.set(e.source, linksIn.get(e.source) + 1);
      }
    }
  }
  for (const node of nodes) {
    node.degree = degree.get(node.id);
    node.out = linksOut.get(node.id);
    node.in = linksIn.get(node.id);
  }
  return { nodes, edges };
}

export function adjacency(graph) {
  const adj = new Map(graph.nodes.map((n) => [n.id, new Set()]));
  for (const e of graph.edges) {
    adj.get(e.source)?.add(e.target);
    adj.get(e.target)?.add(e.source);
  }
  return adj;
}

// Every node within `depth` hops of `id` (links in either direction).
export function neighbourhood(adj, id, depth = 1) {
  const found = new Set([id]);
  let frontier = [id];
  for (let level = 0; level < depth && frontier.length; level++) {
    const next = [];
    for (const current of frontier) {
      for (const other of adj.get(current) || []) {
        if (!found.has(other)) {
          found.add(other);
          next.push(other);
        }
      }
    }
    frontier = next;
  }
  return found;
}

// Fewest hops from a to b, as [a, ..., b], or [] when they aren't connected.
export function shortestPath(adj, a, b) {
  if (!adj.has(a) || !adj.has(b)) return [];
  if (a === b) return [a];
  const previous = new Map([[a, null]]);
  const queue = [a];
  while (queue.length) {
    const current = queue.shift();
    for (const other of adj.get(current)) {
      if (previous.has(other)) continue;
      previous.set(other, current);
      if (other === b) {
        const path = [b];
        let step = current;
        while (step !== null) {
          path.unshift(step);
          step = previous.get(step);
        }
        return path;
      }
      queue.push(other);
    }
  }
  return [];
}

// Communities by label propagation: each note takes the label most of its neighbours have. Deterministic
// (stable order, ties go to the smallest label). Returns Map id -> cluster number, 0 = largest cluster;
// notes with no links share the label -1.
export function clusters(graph) {
  const adj = adjacency(graph);
  const ids = graph.nodes.filter((n) => n.kind !== "tag").map((n) => n.id);
  const label = new Map(ids.map((id, i) => [id, i]));
  // A fixed pseudo-random visiting order and tie-break keep results stable without letting one label
  // flood the whole graph (always preferring the smallest label does exactly that).
  const mix = (x) => {
    x = Math.imul(x ^ (x >>> 16), 0x45d9f3b);
    x = Math.imul(x ^ (x >>> 16), 0x45d9f3b);
    return (x ^ (x >>> 16)) >>> 0;
  };
  const visitOrder = ids.map((id, i) => [mix(i + 1), id]).sort((a, b) => a[0] - b[0]).map(([, id]) => id);
  for (let round = 0; round < 30; round++) {
    let changed = false;
    for (const id of visitOrder) {
      const counts = new Map();
      for (const other of adj.get(id)) {
        if (!label.has(other)) continue;
        const l = label.get(other);
        counts.set(l, (counts.get(l) || 0) + 1);
      }
      if (!counts.size) continue;
      let best = label.get(id);
      let bestCount = counts.get(best) || 0;
      for (const [l, count] of counts) {
        if (count > bestCount || (count === bestCount && mix(l + round) < mix(best + round))) {
          best = l;
          bestCount = count;
        }
      }
      if (best !== label.get(id)) {
        label.set(id, best);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const sizes = new Map();
  for (const id of ids) {
    if (!adj.get(id).size) continue;
    sizes.set(label.get(id), (sizes.get(label.get(id)) || 0) + 1);
  }
  const order = [...sizes.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0]).map(([l]) => l);
  const rank = new Map(order.map((l, i) => [l, i]));
  const result = new Map();
  for (const id of ids) result.set(id, adj.get(id).size ? rank.get(label.get(id)) : -1);
  return result;
}

// ------------------------------------------------------------ physics

class Quad {
  constructor(x, y, w) {
    this.x = x;
    this.y = y;
    this.w = w;
    this.mass = 0;
    this.cx = 0;
    this.cy = 0;
    this.body = null;
    this.kids = null;
    this.stacked = false;
  }
}

function put(node, body, depth) {
  const half = node.w / 2;
  const qx = body.x >= node.x + half ? 1 : 0;
  const qy = body.y >= node.y + half ? 1 : 0;
  const i = qx + 2 * qy;
  if (!node.kids[i]) node.kids[i] = new Quad(node.x + qx * half, node.y + qy * half, half);
  insert(node.kids[i], body, depth + 1);
}

function insert(node, body, depth) {
  if (!node.kids && !node.body && !node.stacked) {
    node.body = body;
    node.mass = body.charge;
    node.cx = body.x;
    node.cy = body.y;
    return;
  }
  if (!node.kids) {
    if (depth > 28) {
      // Bodies on (almost) the same spot: keep them together as one leaf.
      const mass = node.mass + body.charge;
      node.cx = (node.cx * node.mass + body.x * body.charge) / mass;
      node.cy = (node.cy * node.mass + body.y * body.charge) / mass;
      node.mass = mass;
      node.stacked = true;
      return;
    }
    const old = node.body;
    node.body = null;
    node.kids = [null, null, null, null];
    put(node, old, depth);
  }
  const mass = node.mass + body.charge;
  node.cx = (node.cx * node.mass + body.x * body.charge) / mass;
  node.cy = (node.cy * node.mass + body.y * body.charge) / mass;
  node.mass = mass;
  put(node, body, depth);
}

function repel(node, body, strength, alpha, theta2) {
  if (!node || node.mass === 0 || node.body === body) return;
  let dx = node.cx - body.x;
  let dy = node.cy - body.y;
  let d2 = dx * dx + dy * dy;
  if (node.kids && (node.w * node.w) / (d2 || 1e-9) >= theta2) {
    for (const kid of node.kids) repel(kid, body, strength, alpha, theta2);
    return;
  }
  if (d2 === 0) {
    dx = (Math.random() - 0.5) * 1e-3;
    dy = (Math.random() - 0.5) * 1e-3;
    d2 = dx * dx + dy * dy;
  }
  if (d2 < 36) d2 = Math.sqrt(36 * d2); // soften very close encounters
  const f = (strength * node.mass * alpha) / d2;
  body.vx += dx * f;
  body.vy += dy * f;
}

export class Simulation {
  constructor(nodes, edges, { repel = 1, linkDistance = 1, gravity = 1 } = {}) {
    this.nodes = nodes;
    this.alpha = 1;
    this.alphaMin = 0.002;
    this.alphaDecay = 1 - Math.pow(this.alphaMin, 1 / 320);
    this.alphaTarget = 0;
    this.velocityDecay = 0.42;
    this.settings = { repel, linkDistance, gravity };
    const byId = new Map(nodes.map((n) => [n.id, n]));
    this.links = edges
      .map((e) => ({ source: byId.get(e.source), target: byId.get(e.target), kind: e.kind, mutual: Boolean(e.mutual) }))
      .filter((l) => l.source && l.target);
    const count = new Map(nodes.map((n) => [n.id, 0]));
    for (const l of this.links) {
      count.set(l.source.id, count.get(l.source.id) + 1);
      count.set(l.target.id, count.get(l.target.id) + 1);
    }
    for (const l of this.links) {
      const a = count.get(l.source.id);
      const b = count.get(l.target.id);
      l.bias = a / (a + b);
      l.strength = 1 / Math.min(a, b);
    }
    nodes.forEach((n, i) => {
      if (!Number.isFinite(n.x) || !Number.isFinite(n.y)) {
        const radius = 12 * Math.sqrt(0.5 + i);
        const angle = i * Math.PI * (3 - Math.sqrt(5));
        n.x = Math.cos(angle) * radius;
        n.y = Math.sin(angle) * radius;
      }
      n.vx = n.vx || 0;
      n.vy = n.vy || 0;
    });
  }

  get running() {
    return this.alpha >= this.alphaMin || this.alphaTarget > 0;
  }

  reheat(alpha = 0.6) {
    this.alpha = Math.max(this.alpha, alpha);
  }

  tick() {
    const { nodes, links } = this;
    const alpha = (this.alpha += (this.alphaTarget - this.alpha) * this.alphaDecay);
    const strength = -170 * this.settings.repel;
    const distance = 62 * this.settings.linkDistance;
    const gravity = 0.045 * this.settings.gravity;
    // Springs.
    for (const l of links) {
      const s = l.source;
      const t = l.target;
      let x = t.x + t.vx - s.x - s.vx || 1e-6;
      let y = t.y + t.vy - s.y - s.vy || 1e-6;
      let d = Math.sqrt(x * x + y * y);
      const want = l.kind === "tag" ? distance * 0.8 : distance;
      d = ((d - want) / d) * alpha * l.strength * (l.kind === "tag" ? 0.5 : 1);
      x *= d;
      y *= d;
      t.vx -= x * l.bias;
      t.vy -= y * l.bias;
      s.vx += x * (1 - l.bias);
      s.vy += y * (1 - l.bias);
    }
    // Repulsion (Barnes-Hut).
    if (nodes.length) {
      let x0 = Infinity;
      let y0 = Infinity;
      let x1 = -Infinity;
      let y1 = -Infinity;
      for (const n of nodes) {
        if (n.x < x0) x0 = n.x;
        if (n.y < y0) y0 = n.y;
        if (n.x > x1) x1 = n.x;
        if (n.y > y1) y1 = n.y;
      }
      const root = new Quad(x0 - 1, y0 - 1, Math.max(x1 - x0, y1 - y0) + 2);
      for (const n of nodes) {
        n.charge = n.kind === "tag" ? 0.6 : 1 + Math.min(n.degree || 0, 12) * 0.08;
        insert(root, n, 0);
      }
      for (const n of nodes) repel(root, n, strength * (n.kind === "tag" ? 0.6 : 1), alpha, 0.81);
    }
    // Gravity, then move.
    for (const n of nodes) {
      n.vx -= n.x * gravity * alpha;
      n.vy -= n.y * gravity * alpha;
      if (n.fx != null) {
        n.x = n.fx;
        n.y = n.fy;
        n.vx = 0;
        n.vy = 0;
      } else {
        n.vx *= 1 - this.velocityDecay;
        n.vy *= 1 - this.velocityDecay;
        n.x += n.vx;
        n.y += n.vy;
      }
    }
  }
}

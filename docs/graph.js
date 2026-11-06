// Graph algorithms behind the Map: force layout, neighbourhoods, paths, clusters and link suggestions.
// Pure functions with no DOM access, so they can be tested and timed outside the browser.

import { linkIndex, resolveTarget } from "./core.js";

// ------------------------------------------------------------ structure

export function adjacency(graph) {
  const adj = new Map(graph.nodes.map((n) => [n.id, new Set()]));
  for (const edge of graph.edges) {
    adj.get(edge.source)?.add(edge.target);
    adj.get(edge.target)?.add(edge.source);
  }
  return adj;
}

// Every node within `depth` links of `center`, with its distance.
export function neighbourhood(adj, center, depth) {
  const seen = new Map([[center, 0]]);
  let frontier = [center];
  for (let level = 1; level <= depth && frontier.length; level++) {
    const next = [];
    for (const id of frontier) {
      for (const other of adj.get(id) || []) {
        if (seen.has(other)) continue;
        seen.set(other, level);
        next.push(other);
      }
    }
    frontier = next;
  }
  return seen;
}

// Fewest-links route between two notes (breadth-first, ties broken alphabetically), or null.
export function shortestPath(adj, from, to) {
  if (!adj.has(from) || !adj.has(to)) return null;
  if (from === to) return [from];
  const previous = new Map([[from, null]]);
  let frontier = [from];
  while (frontier.length) {
    const next = [];
    for (const id of frontier) {
      for (const other of [...(adj.get(id) || [])].sort()) {
        if (previous.has(other)) continue;
        previous.set(other, id);
        if (other === to) {
          const path = [to];
          let step = id;
          while (step !== null) {
            path.unshift(step);
            step = previous.get(step);
          }
          return path;
        }
        next.push(other);
      }
    }
    frontier = next;
  }
  return null;
}

// Connected groups of notes, biggest first.
export function components(adj) {
  const seen = new Set();
  const groups = [];
  for (const start of [...adj.keys()].sort()) {
    if (seen.has(start)) continue;
    const group = [...neighbourhood(adj, start, Infinity).keys()];
    group.forEach((id) => seen.add(id));
    groups.push(group.sort());
  }
  return groups.sort((a, b) => b.length - a.length || (a[0] < b[0] ? -1 : 1));
}

// Clusters of closely linked notes (deterministic label propagation). Returns id -> cluster rank,
// where 0 is the biggest cluster. Orphans get -1.
export function communities(adj, rounds = 20) {
  const ids = [...adj.keys()].sort();
  const label = new Map(ids.map((id) => [id, id]));
  for (let round = 0; round < rounds; round++) {
    let changed = false;
    for (const id of ids) {
      const counts = new Map();
      for (const other of adj.get(id)) counts.set(label.get(other), (counts.get(label.get(other)) || 0) + 1);
      if (!counts.size) continue;
      let best = label.get(id);
      let bestCount = counts.get(best) || 0;
      for (const [candidate, count] of counts) {
        if (count > bestCount || (count === bestCount && candidate < best)) {
          best = candidate;
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
  for (const id of ids) if (adj.get(id).size) sizes.set(label.get(id), (sizes.get(label.get(id)) || 0) + 1);
  const ranked = [...sizes.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([key]) => key);
  const rank = new Map(ranked.map((key, index) => [key, index]));
  return new Map(ids.map((id) => [id, adj.get(id).size ? rank.get(label.get(id)) : -1]));
}

export function insights(graph, adj) {
  const degree = (id) => adj.get(id)?.size || 0;
  const hubs = graph.nodes
    .filter((n) => degree(n.id) > 0)
    .sort((a, b) => degree(b.id) - degree(a.id) || a.title.localeCompare(b.title))
    .slice(0, 5)
    .map((n) => ({ id: n.id, title: n.title, links: degree(n.id) }));
  const orphans = graph.nodes.filter((n) => degree(n.id) === 0).map((n) => n.id);
  const islands = components(adj).filter((group) => group.length > 1);
  return {
    hubs,
    orphans,
    islands: islands.length,
    largest: islands[0]?.length || 0,
    unresolved: graph.unresolved.length,
    nearMisses: graph.unresolved.filter((u) => u.suggestion).length,
  };
}

// ------------------------------------------------------------ link suggestions

const STOP_NAMES = new Set([
  "home", "index", "readme", "untitled", "notes", "note", "todo", "tasks", "task", "ideas", "idea",
  "journal", "inbox", "daily", "draft", "drafts", "log", "update", "updates", "projects", "project", "misc",
]);
const DATE_NAME = /^\d{4}-\d{2}-\d{2}$/;
const MASK_RE = /```[\s\S]*?```|`[^`\n]*`|!?\[\[[^\]\n]*\]\]|!?\[[^\]\n]*\]\([^)\n]*\)|<[^>\n]+>|[a-z][a-z0-9+.-]*:\/\/\S+/gi;

const escapeRegex = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const fileName = (path) => path.split("/").pop().replace(/\.md$/i, "");

function maskedRanges(body) {
  return [...body.matchAll(MASK_RE)].map((m) => [m.index, m.index + m[0].length]);
}

function inRanges(ranges, start, end) {
  return ranges.some(([a, b]) => start < b && end > a);
}

function mentionRegex(names) {
  const sorted = [...names].sort((a, b) => b.length - a.length);
  return new RegExp(`(?<![\\p{L}\\p{N}_])(${sorted.map(escapeRegex).join("|")})(?![\\p{L}\\p{N}_])`, "giu");
}

// Notes that mention another note's title (or file name) in plain text without any link between them.
// These are Obsidian's "unlinked mentions", but for the whole vault at once, so the Map can draw them.
export function unlinkedMentions(notes, graph) {
  const adj = adjacency(graph);
  const owners = new Map();
  const spelling = new Map();
  for (const note of notes) {
    for (const name of new Set([note.title.trim(), fileName(note.path)])) {
      const key = name.toLowerCase();
      if (name.length < 4 || DATE_NAME.test(name) || /^\d+$/.test(name) || STOP_NAMES.has(key)) continue;
      if (!owners.has(key)) owners.set(key, new Set());
      owners.get(key).add(note.path);
      if (!spelling.has(key)) spelling.set(key, new Set());
      spelling.get(key).add(name);
    }
  }
  // A one-word name only counts when written the same way ("Glow" the project, not "glow" the word).
  const sameWord = (text) => /\s/.test(text.trim()) || spelling.get(text.toLowerCase())?.has(text);
  const names = [...owners.entries()].filter(([, paths]) => paths.size === 1).map(([key]) => key);
  if (!names.length) return [];
  const regex = mentionRegex(names);
  const out = [];
  for (const note of notes) {
    const ranges = maskedRanges(note.body);
    const found = new Map();
    for (const match of note.body.matchAll(regex)) {
      if (inRanges(ranges, match.index, match.index + match[0].length) || !sameWord(match[0])) continue;
      const target = [...owners.get(match[0].toLowerCase())][0];
      if (target === note.path || adj.get(note.path)?.has(target)) continue;
      const hit = found.get(target);
      if (hit) {
        hit.count += 1;
        continue;
      }
      const start = Math.max(0, match.index - 40);
      const end = Math.min(note.body.length, match.index + match[0].length + 40);
      const context = `${start > 0 ? "…" : ""}${note.body.slice(start, end).replace(/\s+/g, " ").trim()}${end < note.body.length ? "…" : ""}`;
      found.set(target, { from: note.path, to: target, phrase: match[0], count: 1, context });
    }
    out.push(...found.values());
  }
  return out.sort((a, b) => b.count - a.count || (a.from < b.from ? -1 : a.from > b.from ? 1 : a.to < b.to ? -1 : 1));
}

// The shortest [[link text]] that resolves to `toPath` when written in `fromPath` (Obsidian's default).
export function linkTextFor(notes, fromPath, toPath) {
  const index = linkIndex(notes);
  const name = fileName(toPath);
  return resolveTarget(index, name, fromPath) === toPath ? name : toPath.replace(/\.md$/i, "");
}

// Turn the first plain-text mention of `phrase` into a wiki link. Returns the new body, or null.
export function linkMention(body, phrase, linkText) {
  const ranges = maskedRanges(body);
  const regex = new RegExp(`(?<![\\p{L}\\p{N}_])${escapeRegex(phrase)}(?![\\p{L}\\p{N}_])`, "giu");
  for (const match of body.matchAll(regex)) {
    const start = match.index;
    const end = start + match[0].length;
    if (inRanges(ranges, start, end)) continue;
    const link = match[0] === linkText ? `[[${linkText}]]` : `[[${linkText}|${match[0]}]]`;
    return body.slice(0, start) + link + body.slice(end);
  }
  return null;
}

// Point every [[oldTarget…]] link at `linkText`, keeping headings, aliases and embeds. Returns body or null.
export function retargetLink(body, oldTarget, linkText) {
  const code = [...body.matchAll(/```[\s\S]*?```|`[^`\n]*`/g)].map((m) => [m.index, m.index + m[0].length]);
  let changed = false;
  const out = body.replace(/(!?)\[\[([^\]\n|#]+)((?:#[^\]\n|]*)?)((?:\|[^\]\n]*)?)\]\]/g, (whole, bang, target, heading, alias, offset) => {
    if (target.trim() !== oldTarget.trim() || inRanges(code, offset, offset + whole.length)) return whole;
    changed = true;
    return `${bang}[[${linkText}${heading}${alias}]]`;
  });
  return changed ? out : null;
}

// ------------------------------------------------------------ force layout

// A small force simulation in the spirit of d3-force: springs along links, Barnes-Hut repulsion,
// gentle gravity (so islands and orphans stay in view) and collision so dots never overlap.
export function createSimulation(nodes, edges, options = {}) {
  const settings = {
    linkDistance: 70,
    charge: -260,
    gravity: 0.06,
    theta2: 0.81,
    velocityDecay: 0.42,
    alphaMin: 0.002,
    alphaDecay: 1 - Math.pow(0.002, 1 / 300),
    collidePad: 6,
    ...options,
  };
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links = edges
    .map((e) => ({ source: byId.get(e.source), target: byId.get(e.target) }))
    .filter((l) => l.source && l.target && l.source !== l.target);
  const count = new Map(nodes.map((n) => [n.id, 0]));
  for (const link of links) {
    count.set(link.source.id, count.get(link.source.id) + 1);
    count.set(link.target.id, count.get(link.target.id) + 1);
  }
  for (const link of links) {
    const a = count.get(link.source.id);
    const b = count.get(link.target.id);
    link.strength = 1 / Math.min(a, b);
    link.bias = a / (a + b);
  }
  nodes.forEach((node, index) => {
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) {
      // Phyllotaxis start: even, deterministic, no random jumps between visits.
      const radius = 12 * Math.sqrt(0.5 + index);
      const angle = index * Math.PI * (3 - Math.sqrt(5));
      node.x = radius * Math.cos(angle);
      node.y = radius * Math.sin(angle);
    }
    node.vx = node.vx || 0;
    node.vy = node.vy || 0;
    node.r = node.r || 6;
  });

  const sim = {
    nodes,
    alpha: options.alpha ?? 1,
    alphaTarget: 0,
    settings,
    tick() {
      sim.alpha += (sim.alphaTarget - sim.alpha) * settings.alphaDecay;
      const alpha = sim.alpha;
      for (const link of links) {
        const { source: s, target: t } = link;
        let dx = t.x + t.vx - s.x - s.vx || 1e-6;
        let dy = t.y + t.vy - s.y - s.vy || 1e-6;
        let length = Math.sqrt(dx * dx + dy * dy);
        length = ((length - settings.linkDistance) / length) * alpha * link.strength;
        dx *= length;
        dy *= length;
        t.vx -= dx * link.bias;
        t.vy -= dy * link.bias;
        s.vx += dx * (1 - link.bias);
        s.vy += dy * (1 - link.bias);
      }
      manyBody(nodes, settings.charge, alpha, settings.theta2);
      for (const node of nodes) {
        node.vx -= node.x * settings.gravity * alpha;
        node.vy -= node.y * settings.gravity * alpha;
      }
      collide(nodes, settings.collidePad);
      for (const node of nodes) {
        if (node.fx !== undefined && node.fx !== null) {
          node.x = node.fx;
          node.y = node.fy;
          node.vx = 0;
          node.vy = 0;
          continue;
        }
        node.vx *= 1 - settings.velocityDecay;
        node.vy *= 1 - settings.velocityDecay;
        node.x += node.vx;
        node.y += node.vy;
      }
    },
    settled() {
      return sim.alpha < settings.alphaMin && sim.alphaTarget === 0;
    },
    // Tick until settled or the time budget runs out. Returns the number of ticks run.
    run(budgetMs = 1500, now = () => performance.now()) {
      const start = now();
      let ticks = 0;
      while (!sim.settled() && now() - start < budgetMs) {
        sim.tick();
        ticks += 1;
      }
      return ticks;
    },
  };
  return sim;
}

function manyBody(nodes, strength, alpha, theta2) {
  if (nodes.length < 2) return;
  const root = buildQuadtree(nodes);
  for (const node of nodes) applyQuad(root, node, strength, alpha, theta2);
}

function buildQuadtree(nodes) {
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
  const size = Math.max(x1 - x0, y1 - y0, 1) + 1;
  const root = { x0, y0, size, kids: null, bodies: [], count: 0, cx: 0, cy: 0 };
  for (const n of nodes) insert(root, n, 0);
  summarise(root);
  return root;
}

function insert(quad, node, depth) {
  if (!quad.kids) {
    if (!quad.bodies.length || depth > 24 || quad.bodies.every((b) => b.x === node.x && b.y === node.y)) {
      quad.bodies.push(node);
      return;
    }
    const existing = quad.bodies;
    quad.bodies = [];
    quad.kids = [null, null, null, null];
    for (const body of existing) insertChild(quad, body, depth);
  }
  insertChild(quad, node, depth);
}

function insertChild(quad, node, depth) {
  const half = quad.size / 2;
  const right = node.x >= quad.x0 + half ? 1 : 0;
  const bottom = node.y >= quad.y0 + half ? 1 : 0;
  const index = bottom * 2 + right;
  if (!quad.kids[index]) {
    quad.kids[index] = { x0: quad.x0 + right * half, y0: quad.y0 + bottom * half, size: half, kids: null, bodies: [], count: 0, cx: 0, cy: 0 };
  }
  insert(quad.kids[index], node, depth + 1);
}

function summarise(quad) {
  let count = 0;
  let sx = 0;
  let sy = 0;
  if (quad.kids) {
    for (const kid of quad.kids) {
      if (!kid) continue;
      summarise(kid);
      count += kid.count;
      sx += kid.cx * kid.count;
      sy += kid.cy * kid.count;
    }
  } else {
    for (const body of quad.bodies) {
      count += 1;
      sx += body.x;
      sy += body.y;
    }
  }
  quad.count = count;
  quad.cx = count ? sx / count : 0;
  quad.cy = count ? sy / count : 0;
}

function applyQuad(quad, node, strength, alpha, theta2) {
  if (!quad.count) return;
  const dx = quad.cx - node.x;
  const dy = quad.cy - node.y;
  let l = dx * dx + dy * dy;
  if (quad.kids && (quad.size * quad.size) / theta2 < l) {
    if (l < 1) l = Math.sqrt(l);
    const f = (strength * quad.count * alpha) / l;
    node.vx += dx * f;
    node.vy += dy * f;
    return;
  }
  if (quad.kids) {
    for (const kid of quad.kids) if (kid) applyQuad(kid, node, strength, alpha, theta2);
    return;
  }
  for (const body of quad.bodies) {
    if (body === node) continue;
    let bx = body.x - node.x;
    let by = body.y - node.y;
    if (!bx && !by) {
      bx = (node.index ?? 1) % 2 ? 1e-3 : -1e-3;
      by = 1e-3;
    }
    let bl = bx * bx + by * by;
    if (bl < 1) bl = Math.sqrt(bl);
    const f = (strength * alpha) / bl;
    node.vx += bx * f;
    node.vy += by * f;
  }
}

// Push overlapping dots apart using a uniform grid (neighbouring cells only).
function collide(nodes, pad) {
  let maxR = 0;
  for (const n of nodes) maxR = Math.max(maxR, n.r);
  const cell = 2 * maxR + pad;
  const grid = new Map();
  for (const n of nodes) {
    const key = `${Math.floor(n.x / cell)},${Math.floor(n.y / cell)}`;
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(n);
  }
  for (const n of nodes) {
    const cx = Math.floor(n.x / cell);
    const cy = Math.floor(n.y / cell);
    for (let gx = cx - 1; gx <= cx + 1; gx++) {
      for (let gy = cy - 1; gy <= cy + 1; gy++) {
        for (const m of grid.get(`${gx},${gy}`) || []) {
          if (m === n || m.id < n.id) continue;
          let dx = m.x + m.vx - n.x - n.vx;
          let dy = m.y + m.vy - n.y - n.vy;
          const min = n.r + m.r + pad;
          let d2 = dx * dx + dy * dy;
          if (d2 >= min * min) continue;
          if (!d2) {
            dx = 1e-3;
            dy = 0;
            d2 = 1e-6;
          }
          const d = Math.sqrt(d2);
          const push = ((min - d) / d) * 0.5;
          dx *= push;
          dy *= push;
          n.vx -= dx;
          n.vy -= dy;
          m.vx += dx;
          m.vy += dy;
        }
      }
    }
  }
}

// ------------------------------------------------------------ force layout in 3D

// The same forces in three dimensions, for the 3D map: springs along links, Barnes-Hut repulsion
// over an octree, and gentle gravity. Bodies are plain {id, x, y, z} objects; missing positions
// start on a Fibonacci sphere (even and deterministic).
export function createSimulation3D(bodies, edges, options = {}) {
  const settings = {
    linkDistance: 62,
    charge: -210,
    gravity: 0.045,
    theta2: 0.81,
    velocityDecay: 0.4,
    alphaMin: 0.003,
    alphaDecay: 1 - Math.pow(0.003, 1 / 300),
    ...options,
  };
  const byId = new Map(bodies.map((b) => [b.id, b]));
  const links = edges
    .map((e) => ({ source: byId.get(e.source), target: byId.get(e.target) }))
    .filter((l) => l.source && l.target && l.source !== l.target);
  const count = new Map(bodies.map((b) => [b.id, 0]));
  for (const link of links) {
    count.set(link.source.id, count.get(link.source.id) + 1);
    count.set(link.target.id, count.get(link.target.id) + 1);
  }
  for (const link of links) {
    const a = count.get(link.source.id);
    const b = count.get(link.target.id);
    link.strength = 1 / Math.min(a, b);
    link.bias = a / (a + b);
  }
  const total = bodies.length || 1;
  bodies.forEach((body, index) => {
    if (![body.x, body.y, body.z].every(Number.isFinite)) {
      const radius = 14 * Math.cbrt(0.5 + index);
      const phi = Math.acos(1 - (2 * (index + 0.5)) / total);
      const theta = index * Math.PI * (3 - Math.sqrt(5));
      body.x = radius * Math.sin(phi) * Math.cos(theta);
      body.y = radius * Math.sin(phi) * Math.sin(theta);
      body.z = radius * Math.cos(phi);
    }
    body.vx = body.vx || 0;
    body.vy = body.vy || 0;
    body.vz = body.vz || 0;
  });

  const sim = {
    bodies,
    alpha: options.alpha ?? 1,
    alphaTarget: 0,
    settings,
    tick() {
      sim.alpha += (sim.alphaTarget - sim.alpha) * settings.alphaDecay;
      const alpha = sim.alpha;
      for (const link of links) {
        const { source: s, target: t } = link;
        let dx = t.x + t.vx - s.x - s.vx || 1e-6;
        let dy = t.y + t.vy - s.y - s.vy || 1e-6;
        let dz = t.z + t.vz - s.z - s.vz || 1e-6;
        let length = Math.sqrt(dx * dx + dy * dy + dz * dz);
        length = ((length - settings.linkDistance) / length) * alpha * link.strength;
        dx *= length;
        dy *= length;
        dz *= length;
        t.vx -= dx * link.bias;
        t.vy -= dy * link.bias;
        t.vz -= dz * link.bias;
        s.vx += dx * (1 - link.bias);
        s.vy += dy * (1 - link.bias);
        s.vz += dz * (1 - link.bias);
      }
      if (bodies.length > 1) {
        const root = buildOctree(bodies);
        for (const body of bodies) applyOctree(root, body, settings.charge, alpha, settings.theta2);
      }
      for (const body of bodies) {
        body.vx -= body.x * settings.gravity * alpha;
        body.vy -= body.y * settings.gravity * alpha;
        body.vz -= body.z * settings.gravity * alpha;
        body.vx *= 1 - settings.velocityDecay;
        body.vy *= 1 - settings.velocityDecay;
        body.vz *= 1 - settings.velocityDecay;
        body.x += body.vx;
        body.y += body.vy;
        body.z += body.vz;
      }
    },
    settled() {
      return sim.alpha < settings.alphaMin && sim.alphaTarget === 0;
    },
    run(budgetMs = 1500, now = () => performance.now()) {
      const start = now();
      let ticks = 0;
      while (!sim.settled() && now() - start < budgetMs) {
        sim.tick();
        ticks += 1;
      }
      return ticks;
    },
  };
  return sim;
}

function octCell(x0, y0, z0, size) {
  return { x0, y0, z0, size, kids: null, bodies: [], count: 0, cx: 0, cy: 0, cz: 0 };
}

function buildOctree(bodies) {
  let x0 = Infinity;
  let y0 = Infinity;
  let z0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  let z1 = -Infinity;
  for (const b of bodies) {
    x0 = Math.min(x0, b.x);
    y0 = Math.min(y0, b.y);
    z0 = Math.min(z0, b.z);
    x1 = Math.max(x1, b.x);
    y1 = Math.max(y1, b.y);
    z1 = Math.max(z1, b.z);
  }
  const root = octCell(x0, y0, z0, Math.max(x1 - x0, y1 - y0, z1 - z0, 1) + 1);
  for (const b of bodies) octInsert(root, b, 0);
  octSummarise(root);
  return root;
}

function octInsert(cell, body, depth) {
  if (!cell.kids) {
    if (!cell.bodies.length || depth > 20 || cell.bodies.every((b) => b.x === body.x && b.y === body.y && b.z === body.z)) {
      cell.bodies.push(body);
      return;
    }
    const existing = cell.bodies;
    cell.bodies = [];
    cell.kids = new Array(8).fill(null);
    for (const b of existing) octChild(cell, b, depth);
  }
  octChild(cell, body, depth);
}

function octChild(cell, body, depth) {
  const half = cell.size / 2;
  const right = body.x >= cell.x0 + half ? 1 : 0;
  const down = body.y >= cell.y0 + half ? 1 : 0;
  const back = body.z >= cell.z0 + half ? 1 : 0;
  const index = right + down * 2 + back * 4;
  if (!cell.kids[index]) {
    cell.kids[index] = octCell(cell.x0 + right * half, cell.y0 + down * half, cell.z0 + back * half, half);
  }
  octInsert(cell.kids[index], body, depth + 1);
}

function octSummarise(cell) {
  let count = 0;
  let sx = 0;
  let sy = 0;
  let sz = 0;
  if (cell.kids) {
    for (const kid of cell.kids) {
      if (!kid) continue;
      octSummarise(kid);
      count += kid.count;
      sx += kid.cx * kid.count;
      sy += kid.cy * kid.count;
      sz += kid.cz * kid.count;
    }
  } else {
    for (const b of cell.bodies) {
      count += 1;
      sx += b.x;
      sy += b.y;
      sz += b.z;
    }
  }
  cell.count = count;
  cell.cx = count ? sx / count : 0;
  cell.cy = count ? sy / count : 0;
  cell.cz = count ? sz / count : 0;
}

function applyOctree(cell, body, strength, alpha, theta2) {
  if (!cell.count) return;
  const dx = cell.cx - body.x;
  const dy = cell.cy - body.y;
  const dz = cell.cz - body.z;
  let l = dx * dx + dy * dy + dz * dz;
  if (cell.kids && (cell.size * cell.size) / theta2 < l) {
    if (l < 1) l = Math.sqrt(l);
    const f = (strength * cell.count * alpha) / l;
    body.vx += dx * f;
    body.vy += dy * f;
    body.vz += dz * f;
    return;
  }
  if (cell.kids) {
    for (const kid of cell.kids) if (kid) applyOctree(kid, body, strength, alpha, theta2);
    return;
  }
  for (const other of cell.bodies) {
    if (other === body) continue;
    let bx = other.x - body.x;
    let by = other.y - body.y;
    let bz = other.z - body.z;
    if (!bx && !by && !bz) {
      bx = 1e-3;
      by = -1e-3;
      bz = 1e-3;
    }
    let bl = bx * bx + by * by + bz * bz;
    if (bl < 1) bl = Math.sqrt(bl);
    const f = (strength * alpha) / bl;
    body.vx += bx * f;
    body.vy += by * f;
    body.vz += bz * f;
  }
}

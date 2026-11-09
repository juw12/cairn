// Founder Studio - simulation engine (pure functions, no DOM).
// The heuristic buyer is a transparent, seeded rule model for instant previews.
// It is NOT an AI and NOT evidence; the AI-buyer mode and the skill's scripts are the real path.

const STOP = new Set(("the a an and or of to for in on at is are be it my our your this that with from not too " +
  "already do does i we you they me us them can will would just only free yet").split(" "));

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function words(text) {
  return (text || "").toLowerCase().match(/[\p{L}\p{N}$]+/gu)?.filter((w) => w.length > 3 && !STOP.has(w)) || [];
}

// Which objections does the pitch text appear to answer? (keyword overlap - a suggestion the user can override)
export function suggestAddressed(objections, pitch) {
  const pitchWords = new Set(words(pitch));
  const result = {};
  for (const objection of objections) {
    const objectionWords = words(objection);
    const hits = objectionWords.filter((w) => pitchWords.has(w) || [...pitchWords].some((p) => p.startsWith(w.slice(0, 5))));
    result[objection] = objectionWords.length > 0 && hits.length / objectionWords.length >= 0.34;
  }
  return result;
}

const CODE_HINTS = [
  ["price", /price|cost|cheap|expens|pay|rp\d|us\$|nt\$|free|budget|subscription/i],
  ["trust", /trust|who are|track record|brand|unknown|data|privacy|secure|safe|vendor|support|leaves/i],
  ["habit", /already|habit|canva|excel|spreadsheet|chatgpt|my own|in-house|script/i],
  ["timing", /yet|wait|later|neighbour|first|hq|change/i],
  ["quality", /accura|generic|quality|work with|fake|reliab|down on/i],
  ["convenience", /setup|hassle|maintenance|export|install|time/i],
  ["values", /disrespect|odd|feel|sketchy|spammy|judg/i],
];

export function codeFor(objection) {
  for (const [code, pattern] of CODE_HINTS) {
    if (pattern.test(objection)) return code;
  }
  return "need";
}

export function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  if (!sorted.length) return 0;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// ctx: { price, anchor (typical market price), addressed: {objection: bool}, need: {behaviourName: 0..1}, seed, medianIncome }
export function heuristicAnswer(persona, ctx) {
  const idNumber = parseInt(String(persona.id).replace(/\D/g, ""), 10) || 1;
  const rng = mulberry32((ctx.seed || 1) * 7919 + idNumber * 104729);
  const incomeRatio = ctx.medianIncome > 0 ? Math.max(0.4, Math.min(2.5, persona.income / ctx.medianIncome)) : 1;
  const willingness = ctx.anchor * Math.sqrt(incomeRatio) * (0.75 + rng() * 0.5);
  const prices = {
    price_too_cheap: round(willingness * 0.25),
    price_cheap: round(willingness * 0.55),
    price_expensive: round(willingness * 1.0),
    price_too_expensive: round(willingness * 1.6),
  };
  let priceFit = 1;
  if (ctx.price > prices.price_expensive) {
    priceFit = Math.max(0, (prices.price_too_expensive - ctx.price) / (prices.price_too_expensive - prices.price_expensive));
  }
  const need = ctx.need?.[persona.behaviour] ?? 0.5;
  const answered = !!ctx.addressed?.[persona.objection];
  const probability = need * (answered ? 0.85 : 0.3) * priceFit;
  const buys = rng() < probability;
  let reasonCode = answered ? "need" : codeFor(persona.objection);
  if (priceFit < 0.5) reasonCode = "price";
  const reason = buys
    ? `${persona.behaviour_how || persona.behaviour} The pitch settles my main doubt and the price fits.`
    : priceFit < 0.5
      ? `At ${Number(ctx.price).toFixed(2)} it's more than I'd pay for this.`
      : `"${persona.objection}" - the pitch doesn't settle that for me.`;
  return {
    id: persona.id,
    buys,
    purchases_first_month: buys ? 1 : 0,
    reason_code: buys ? "need" : reasonCode,
    reason,
    objection: persona.objection,
    would_change_my_mind: answered ? "" : `A clear answer to: ${persona.objection}`,
    ...prices,
    _engine: "heuristic",
    _probability: Math.round(probability * 100) / 100,
  };
}

function round(value) {
  if (value >= 1000) return Math.round(value / 100) * 100;
  if (value >= 100) return Math.round(value);
  return Math.round(value * 100) / 100;
}

// Live Van Westendorp preview (the saved, authoritative curve comes from van_westendorp.py).
export function vanWestendorp(answers) {
  const rows = answers
    .map((a) => [a.price_too_cheap, a.price_cheap, a.price_expensive, a.price_too_expensive].map(Number))
    .filter((r) => r.every((v) => Number.isFinite(v) && v > 0));
  if (rows.length < 3) return null;
  const all = rows.flat().sort((a, b) => a - b);
  const low = all[0];
  const high = all[all.length - 1];
  const steps = 120;
  const grid = Array.from({ length: steps + 1 }, (_, i) => low + ((high - low) * i) / steps);
  const share = (fn) => grid.map((p) => rows.filter((r) => fn(r, p)).length / rows.length);
  const curves = {
    tooCheap: share((r, p) => r[0] >= p),
    cheap: share((r, p) => r[1] >= p),
    expensive: share((r, p) => r[2] <= p),
    tooExpensive: share((r, p) => r[3] <= p),
  };
  const notCheap = curves.cheap.map((v) => 1 - v);
  const notExpensive = curves.expensive.map((v) => 1 - v);
  const cross = (a, b) => {
    for (let i = 1; i < grid.length; i++) {
      const d0 = a[i - 1] - b[i - 1];
      const d1 = a[i] - b[i];
      if (d0 === 0) return grid[i - 1];
      if (d0 * d1 < 0) return grid[i - 1] + ((grid[i] - grid[i - 1]) * d0) / (d0 - d1);
    }
    return null;
  };
  return {
    grid,
    curves,
    points: {
      PMC: cross(curves.tooCheap, notCheap),
      OPP: cross(curves.tooCheap, curves.tooExpensive),
      IPP: cross(curves.cheap, curves.expensive),
      PME: cross(curves.tooExpensive, notExpensive),
    },
  };
}

export function groupRates(answers, personas, key) {
  const byId = Object.fromEntries(personas.map((p) => [p.id, p]));
  const groups = {};
  for (const answer of answers) {
    const persona = byId[answer.id];
    if (!persona) continue;
    const label = persona[key];
    groups[label] ||= { label, n: 0, buys: 0 };
    groups[label].n += 1;
    groups[label].buys += answer.buys ? 1 : 0;
  }
  return Object.values(groups).sort((a, b) => b.buys / b.n - a.buys / a.n || b.n - a.n);
}

export function reasonCounts(answers, buys) {
  const counts = {};
  for (const answer of answers.filter((a) => !!a.buys === buys)) {
    counts[answer.reason_code] = (counts[answer.reason_code] || 0) + 1;
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}

// Founder Studio - review loop logic (pure functions, no DOM).
// Reviewer bots apply the skill's board lenses (lenses.md) to the pitch and the last panel run.
// They suggest; they never grade. The judge is the same buyer panel re-run with the same seed,
// and the loop keeps a new version only when more of those buyers buy.

import { suggestAddressed } from "./engine.js";

export const REVIEWERS = [
  {
    id: "R1",
    name: "Offers lens",
    focus: "Value: answer the doubt that stops the most buyers",
    lens: "The Offers lens: raise the value equation (dream outcome, likelihood, time, effort). Find the obstacle that stops the most buyers and make the pitch remove it.",
  },
  {
    id: "R2",
    name: "Product lens",
    focus: "The weakest segment and the moment it beats today's habit",
    lens: "The Product lens: start from the customer's moment and work back. Look at the segment that buys least and what it does today instead. Focus means one clear reason, said simply.",
  },
  {
    id: "R3",
    name: "Monopoly lens",
    focus: "Why this and not the nearest alternative",
    lens: "The Monopoly lens: competition destroys profit. The pitch must say why this and not the nearest alternative, with a difference a rival cannot copy quickly.",
  },
  {
    id: "R4",
    name: "Clarity check",
    focus: "Plain words, the price stated, short enough to read",
    lens: "A copy editor: plain words, no superlatives, the price stated once, 3 to 6 short lines. Never add claims.",
  },
  {
    id: "R5",
    name: "CFO lens",
    focus: "Price against the simulated acceptable range",
    lens: "The CFO lens: is the price inside the range buyers accept? A price change is the founder's decision, so only advise.",
  },
];

const SUPERLATIVES = [
  "best", "amazing", "revolutionary", "world-class", "world class", "ultimate", "incredible",
  "unbeatable", "perfect", "game-changing", "game changing", "cutting-edge", "cutting edge",
  "next-generation", "next generation", "unparalleled", "best-in-class", "awesome",
];
const DIFFERENCE = /\b(only|unlike|instead of|than|compared|vs\.?|versus|no other|the one)\b/i;

// Placeholder text the quick (rule) editor inserts where a true answer is needed.
export const PLACEHOLDER = "[your one-line answer]";

// A line that names the doubt in the buyer's own words and leaves a slot for the founder's honest answer.
export function answerLine(objection) {
  const doubt = objection.trim().replace(/[.!]+$/, "");
  return /\?$/.test(doubt) ? `${doubt} ${PLACEHOLDER}` : `On "${doubt}": ${PLACEHOLDER}`;
}

export function stripSuperlatives(pitch) {
  let text = pitch;
  const removed = [];
  for (const word of SUPERLATIVES) {
    const pattern = new RegExp(`\\b(the\\s+)?${word.replace(/[-\s]/g, "[-\\s]")}\\b\\s*`, "gi");
    if (pattern.test(text)) {
      removed.push(word);
      text = text.replace(pattern, "");
    }
  }
  text = text.replace(/[ \t]{2,}/g, " ").replace(/ ([,.])/g, "$1");
  return { pitch: text, removed };
}

function mentionsPrice(pitch, price) {
  if (!price) return true;
  const plain = String(price).replace(/\.0+$/, "");
  return pitch.includes(plain) || pitch.includes(Number(price).toFixed(2));
}

function wordCount(text) {
  return (text.match(/\S+/g) || []).length;
}

// Doubts held by buyers who passed, most common first, with the segments that hold them.
export function passDoubts(answers, personas) {
  const byId = Object.fromEntries(personas.map((p) => [p.id, p]));
  const doubts = {};
  for (const answer of answers) {
    if (answer.buys) continue;
    const persona = byId[answer.id];
    const objection = answer.objection || persona?.objection;
    if (!objection) continue;
    doubts[objection] ||= { objection, count: 0, segments: {} };
    doubts[objection].count += 1;
    const segment = persona?.segment || "";
    doubts[objection].segments[segment] = (doubts[objection].segments[segment] || 0) + 1;
  }
  return Object.values(doubts).sort((a, b) => b.count - a.count);
}

export function weakestSegment(answers, personas) {
  const byId = Object.fromEntries(personas.map((p) => [p.id, p]));
  const groups = {};
  for (const answer of answers) {
    const segment = byId[answer.id]?.segment;
    if (!segment) continue;
    groups[segment] ||= { segment, n: 0, buys: 0 };
    groups[segment].n += 1;
    groups[segment].buys += answer.buys ? 1 : 0;
  }
  return Object.values(groups)
    .filter((g) => g.n >= 2)
    .sort((a, b) => a.buys / a.n - b.buys / b.n || b.n - a.n)[0] || null;
}

// Which doubts the pitch already answers: the keyword suggestion, plus the founder's own toggles.
export function answeredMap(objections, pitch, overrides = {}) {
  return { ...suggestAddressed(objections, pitch), ...overrides };
}

// Rule review for one reviewer. ctx: { pitch, answers, personas, objections, overrides,
// tried: Set of objections already tried, claimed: Set of objections another reviewer took this round,
// price, priceLabel, priceRange: [low, high] | null }.
// Returns { verdict: "fix" | "ok" | "you", note, fix?: { kind, objection?, line? } }.
export function ruleReview(reviewer, ctx) {
  const answered = answeredMap(ctx.objections, ctx.pitch, ctx.overrides);
  const open = passDoubts(ctx.answers, ctx.personas)
    .filter((d) => !answered[d.objection] && !ctx.tried.has(d.objection) && !ctx.claimed.has(d.objection));
  if (reviewer.id === "R1") {
    const top = open[0];
    if (!top) {
      return { verdict: "ok", note: "Every doubt that stopped a buyer is already answered or was tried. More words will not move this panel." };
    }
    return {
      verdict: "fix",
      note: `"${top.objection}" stops ${top.count} buyer${top.count === 1 ? "" : "s"} and the pitch never answers it. Answer it in their words.`,
      fix: { kind: "answer", objection: top.objection, line: answerLine(top.objection) },
    };
  }
  if (reviewer.id === "R2") {
    const weak = weakestSegment(ctx.answers, ctx.personas);
    if (!weak) return { verdict: "ok", note: "Not enough buyers per segment to single one out." };
    const theirs = open.filter((d) => d.segments[weak.segment]).sort((a, b) => b.segments[weak.segment] - a.segments[weak.segment])[0];
    if (!theirs) {
      return {
        verdict: "ok",
        note: `${weak.segment} buys ${weak.buys}/${weak.n}, but their doubts are already answered. What is left is need or price, which wording alone will not fix.`,
      };
    }
    return {
      verdict: "fix",
      note: `${weak.segment} buys least (${weak.buys}/${weak.n}). Their top open doubt is "${theirs.objection}".`,
      fix: { kind: "answer", objection: theirs.objection, line: answerLine(theirs.objection) },
    };
  }
  if (reviewer.id === "R3") {
    // Only the founder's own lines count; doubt lines the loop inserted quote buyers, not a difference.
    const ownLines = ctx.pitch.split("\n").filter((line) => !line.includes(PLACEHOLDER)).join("\n");
    if (DIFFERENCE.test(ownLines)) {
      return { verdict: "ok", note: "The pitch names a difference from the alternative. Check that a rival can't copy it in a month." };
    }
    return {
      verdict: "you",
      note: "Nothing says why this and not what buyers use today. Add the one difference a rival can't copy quickly - it needs your facts, so the loop won't invent it.",
    };
  }
  if (reviewer.id === "R4") {
    const { removed } = stripSuperlatives(ctx.pitch);
    const missingPrice = !mentionsPrice(ctx.pitch, ctx.price);
    if (removed.length || missingPrice) {
      const parts = [];
      if (removed.length) parts.push(`drop "${removed.join('", "')}" (claims buyers discount)`);
      if (missingPrice) parts.push(`state the price (${ctx.priceLabel})`);
      return { verdict: "fix", note: `${parts.join(" and ")}.`.replace(/^./, (c) => c.toUpperCase()), fix: { kind: "clarity", removed, addPrice: missingPrice } };
    }
    const words = wordCount(ctx.pitch);
    if (words > 110) return { verdict: "you", note: `${words} words. Buyers skim; cut to the 3 to 6 lines that matter.` };
    return { verdict: "ok", note: `Plain, priced and ${words} words. Nothing to fix.` };
  }
  if (reviewer.id === "R5") {
    const priceStops = ctx.answers.filter((a) => !a.buys && a.reason_code === "price").length;
    const range = ctx.priceRange;
    if (!range?.[0] || !range?.[1]) return { verdict: "ok", note: "Not enough price answers for a range yet." };
    const text = `${round(range[0])} to ${round(range[1])}`;
    if (ctx.price > range[1]) {
      return {
        verdict: "you",
        note: `${ctx.price} is above the simulated acceptable range (${text}) and price stops ${priceStops}. Consider ${round(range[1])} - your call, the loop never changes the price.`,
      };
    }
    if (ctx.price < range[0]) {
      return { verdict: "you", note: `${ctx.price} is below the simulated range (${text}); a low price can read as low quality. Your call.` };
    }
    return { verdict: "ok", note: `${ctx.price} sits inside the simulated range (${text}). Fix the message, not the price.` };
  }
  return { verdict: "ok", note: "" };
}

function round(value) {
  return Math.round(Number(value) * 100) / 100;
}

// Applies the rule fixes to the pitch. Returns { pitch, changes: [text], answered: [objection] }.
export function applyFixes(pitch, fixes, { priceLabel } = {}) {
  let text = pitch.trim();
  const changes = [];
  const answered = [];
  for (const fix of fixes) {
    if (fix.kind === "answer" && fix.line && !text.includes(fix.line)) {
      text = `${text}\n${fix.line}`;
      changes.push(`Added: ${fix.line}`);
      answered.push(fix.objection);
    }
    if (fix.kind === "clarity") {
      if (fix.removed?.length) {
        text = stripSuperlatives(text).pitch;
        changes.push(`Removed: ${fix.removed.join(", ")}`);
      }
      if (fix.addPrice && priceLabel) {
        text = `${text}\nPrice: ${priceLabel}.`;
        changes.push(`Added the price: ${priceLabel}`);
      }
    }
  }
  return { pitch: text, changes, answered };
}

// Keep-best decision: a version is kept only when strictly more buyers buy.
export function keepBest(best, candidate) {
  return !best || candidate.rate > best.rate;
}

export function placeholderCount(pitch) {
  return (pitch.match(/\[[^\]]+\]/g) || []).length;
}

// Brief for one AI reviewer (AI mode). The model applies one lens and returns JSON.
export function reviewerBrief(reviewer, ctx) {
  const doubts = passDoubts(ctx.answers, ctx.personas).slice(0, 6)
    .map((d) => `- "${d.objection}": ${d.count} buyers passed`).join("\n") || "- none";
  const weak = weakestSegment(ctx.answers, ctx.personas);
  const buys = ctx.answers.filter((a) => a.buys).length;
  return `You review a pitch for a small business, applying ONLY this lens:
${reviewer.lens}

Business: ${ctx.business}
Price: ${ctx.priceLabel}
Pitch the buyers read:
"""
${ctx.pitch}
"""
Simulated panel result: ${buys} of ${ctx.answers.length} would buy.
Weakest segment: ${weak ? `${weak.segment} (${weak.buys}/${weak.n})` : "n/a"}
Top doubts of buyers who passed:
${doubts}
Already tried without a gain: ${[...ctx.tried].join("; ") || "nothing"}

Rules: no invented facts, prices, reviews or numbers. You may propose one concrete change; if it needs a fact not in the pitch, say so.
Reply with ONLY this JSON: {"verdict": "fix" | "ok" | "you", "note": "one or two plain sentences", "change": "the one edit to make, or empty"}
"fix" = an edit the editor can make with the facts above; "you" = needs the founder's facts or decision; "ok" = nothing to change.`;
}

// Brief for the AI editor: merges the reviewers' changes into the next pitch version.
export function editorBrief(ctx, notes) {
  const asks = notes.filter((n) => n.verdict === "fix" && n.change)
    .map((n) => `- ${n.reviewer}: ${n.change}`).join("\n");
  return `Rewrite this pitch for ${ctx.business} (price ${ctx.priceLabel}) applying these reviewer changes:
${asks}

Current pitch:
"""
${ctx.pitch}
"""
Rules: 3 to 6 short plain lines. Keep every true fact and the price. No superlatives. Never invent facts, numbers, reviews or offers;
if a change needs one, write a concrete proposal and end that line with "(confirm)".
Reply with ONLY this JSON: {"pitch": "the full new pitch", "changes": ["what you changed, one item per change"]}`;
}

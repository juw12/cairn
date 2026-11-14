// Share card: the last two weeks of vibe coding as one picture, the way a running app shares a week.
// Drawn on a canvas in the browser and handed to the phone's share sheet (or saved as a PNG). Nothing
// is uploaded, and only totals go on the card: no session titles, folders or prompts.

import { compact } from "./recap.js";
import { escapeHtml as esc } from "./md.js";
import { $, toast } from "./ui.js";
import { copyText, modal } from "./actions.js";

export const FORMATS = {
  story: { label: "Story (9:16)", width: 1080, height: 1920 },
  square: { label: "Square (1:1)", width: 1080, height: 1080 },
};
export const HEADLINES = { hours: "Hours", tokens: "Tokens", sessions: "Sessions", streak: "Streak" };

const LAYOUT = {
  story: { top: 150, eyebrow: 400, hero: 720, heroSize: 340, heroLabel: 800, bars: 900, barsHeight: 250, tiles: 1250, columns: 2, row: 160, value: 88, label: 28, model: 1770, foot: 1840 },
  square: { top: 110, eyebrow: 220, hero: 450, heroSize: 230, heroLabel: 510, bars: 550, barsHeight: 120, tiles: 772, columns: 3, row: 104, value: 62, label: 22, model: 0, foot: 1030 },
};
const PAD = 88;
const INK = "#14110d";
const PAPER = "#f6f1e7";
const MUTED = "#b3ab9c";
const ACCENT = "#f0764d";
const DISPLAY = '"Instrument Serif", "Iowan Old Style", Georgia, serif';
const SANS = '"Geist", ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif';
const MONO = '"Geist Mono", ui-monospace, "Cascadia Code", Menlo, Consolas, monospace';

// 45m, 7.5h, 84h: a headline number, so no minutes once it is past ten hours.
export function hoursText(minutes) {
  const total = Math.max(0, Math.round(minutes || 0));
  if (total < 60) return `${total}m`;
  if (total < 600) return `${(total / 60).toFixed(1).replace(/\.0$/, "")}h`;
  return `${Math.round(total / 60)}h`;
}

function shortDay(day) {
  const date = new Date(`${day}T12:00:00`);
  return Number.isNaN(date.getTime()) ? day : date.toLocaleDateString("en", { day: "numeric", month: "short" });
}

// Everything the card says, as plain data: the dialog draws it, the caption repeats it, the tests read it.
export function cardLines(stats, { headline = "hours", models = false, days = 14 } = {}) {
  const facts = {
    hours: { value: hoursText(stats.minutes), label: "with an agent working", short: "agent time", daily: "minutes", per: "agent time per day" },
    tokens: { value: compact(stats.tokens), label: "tokens burnt", short: "tokens", daily: "tokens", per: "tokens per day" },
    sessions: { value: String(stats.sessions), label: stats.sessions === 1 ? "coding session" : "coding sessions", short: "sessions", daily: "sessions", per: "sessions per day" },
    streak: { value: String(stats.streak), label: stats.streak === 1 ? "day in a row" : "days in a row", short: "day streak", daily: "minutes", per: "agent time per day" },
    prompts: { value: compact(stats.prompts), short: "prompts" },
    active: { value: `${stats.active_days}/${days}`, short: "days active" },
    shipped: { value: String(stats.shipped), short: "changed code" },
  };
  const key = facts[headline]?.label ? headline : "hours";
  const hero = facts[key];
  const daily = (stats.daily || []).slice(-days);
  const top = models ? stats.models?.[0] : null;
  const range = daily.length ? `${shortDay(daily[0].day)} – ${shortDay(daily[daily.length - 1].day)}` : "";
  return {
    headline: key,
    eyebrow: "My vibe coding",
    period: `Last ${days} days`,
    hero: { value: hero.value, label: hero.label },
    bars: daily.map((day) => Number(day[hero.daily]) || 0),
    barsLabel: hero.per,
    tiles: Object.keys(facts)
      .filter((name) => name !== key)
      .map((name) => ({ value: facts[name].value, label: facts[name].short })),
    model: top ? top.model : "",
    range,
    foot: "Made with Cairn",
    caption: `${days} days of vibe coding: ${facts.hours.value} with an agent working, ${facts.sessions.value} sessions, ${facts.tokens.value} tokens, ${stats.streak}-day streak.${top ? ` Mostly on ${top.model}.` : ""} #vibecoding`,
  };
}

function glow(ctx, x, y, radius, colour) {
  const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
  gradient.addColorStop(0, colour);
  gradient.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = gradient;
  ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
}

// Draw text no wider than maxWidth, shrinking the type instead of letting a long number run off the card.
function write(ctx, text, x, y, { size, family = SANS, weight = 400, colour = PAPER, align = "left", maxWidth = 0, spacing = "0px" }) {
  let fitted = size;
  ctx.letterSpacing = spacing;
  ctx.font = `${weight} ${fitted}px ${family}`;
  while (maxWidth && fitted > 12 && ctx.measureText(text).width > maxWidth) {
    fitted -= 4;
    ctx.font = `${weight} ${fitted}px ${family}`;
  }
  ctx.fillStyle = colour;
  ctx.textAlign = align;
  ctx.fillText(text, x, y);
  ctx.letterSpacing = "0px";
}

export function drawCard(canvas, lines, format = "story") {
  const { width, height } = FORMATS[format] || FORMATS.story;
  const at = LAYOUT[format] || LAYOUT.story;
  const inner = width - PAD * 2;
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.textBaseline = "alphabetic";

  ctx.fillStyle = INK;
  ctx.fillRect(0, 0, width, height);
  glow(ctx, width * 0.92, height * 0.06, width * 0.95, "rgba(240, 118, 77, 0.5)");
  glow(ctx, width * 0.04, height * 0.97, width * 0.85, "rgba(36, 116, 138, 0.5)");

  write(ctx, "cairn", PAD, at.top, { size: 68, family: DISPLAY });
  write(ctx, lines.period.toUpperCase(), width - PAD, at.top - 8, { size: 26, family: MONO, colour: MUTED, align: "right", spacing: "3px" });
  write(ctx, lines.eyebrow.toUpperCase(), PAD, at.eyebrow, { size: 30, family: MONO, colour: ACCENT, spacing: "5px" });
  write(ctx, lines.hero.value, PAD - 8, at.hero, { size: at.heroSize, family: DISPLAY, maxWidth: inner });
  write(ctx, lines.hero.label, PAD, at.heroLabel, { size: 46, colour: PAPER, maxWidth: inner });

  const count = lines.bars.length;
  if (count) {
    const gap = 12;
    const barWidth = (inner - gap * (count - 1)) / count;
    const peak = Math.max(...lines.bars, 1);
    lines.bars.forEach((value, index) => {
      const tall = value > 0 ? Math.max(10, (value / peak) * at.barsHeight) : 6;
      ctx.fillStyle = value > 0 ? ACCENT : "rgba(246, 241, 231, 0.16)";
      ctx.beginPath();
      ctx.roundRect(PAD + index * (barWidth + gap), at.bars + at.barsHeight - tall, barWidth, tall, Math.min(10, barWidth / 2));
      ctx.fill();
    });
    write(ctx, lines.barsLabel, PAD, at.bars + at.barsHeight + 44, { size: 24, family: MONO, colour: MUTED });
    write(ctx, lines.range, width - PAD, at.bars + at.barsHeight + 44, { size: 24, family: MONO, colour: MUTED, align: "right" });
  }

  ctx.fillStyle = "rgba(246, 241, 231, 0.16)";
  ctx.fillRect(PAD, at.tiles - 24, inner, 2);
  const column = inner / at.columns;
  lines.tiles.forEach((tile, index) => {
    const x = PAD + (index % at.columns) * column;
    const y = at.tiles + Math.floor(index / at.columns) * at.row;
    write(ctx, tile.value, x, y + at.value, { size: at.value, family: DISPLAY, maxWidth: column - 24 });
    write(ctx, tile.label, x, y + at.value + at.label + 12, { size: at.label, family: MONO, colour: MUTED, maxWidth: column - 24 });
  });

  const model = lines.model ? `Mostly on ${lines.model}` : "";
  if (model && at.model) write(ctx, model, PAD, at.model, { size: 32, colour: PAPER, maxWidth: inner });
  write(ctx, model && !at.model ? `${lines.foot} · ${model}` : lines.foot, PAD, at.foot, { size: 24, family: MONO, colour: MUTED, maxWidth: inner });
  return canvas;
}

const pngBlob = (canvas) => new Promise((resolve) => canvas.toBlob(resolve, "image/png"));

function save(blob, name) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// A phone opens its share sheet with the picture attached; a desktop browser without one saves the PNG.
async function send(canvas, caption, name) {
  const blob = await pngBlob(canvas);
  if (!blob) return toast("Couldn’t draw the picture.", { error: true });
  const file = new File([blob], name, { type: "image/png" });
  if (navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], text: caption });
    } catch (error) {
      if (error.name !== "AbortError") toast(error.message, { error: true });
    }
    return;
  }
  save(blob, name);
  toast("Saved the picture. Post it from your downloads.");
}

export function openShare(stats, { days = 14 } = {}) {
  const options = (map, label) => Object.entries(map).map(([value, item]) => `<option value="${esc(value)}">${esc(label(item))}</option>`).join("");
  const canShare = typeof navigator.canShare === "function" && typeof File === "function" && navigator.canShare({ files: [new File([""], "cairn-vibe.png", { type: "image/png" })] });
  modal(
    `<div class="modal share" role="dialog" aria-modal="true" aria-labelledby="share-title">
      <div class="share-preview"><canvas class="share-canvas" role="img" aria-label="Your stats as a picture"></canvas></div>
      <div class="share-side">
        <h2 id="share-title">Share your stats</h2>
        <p>Your last ${days} days as a picture. Totals only: no session titles, folders or prompts. It is drawn on this device and goes nowhere until you post it.</p>
        <label class="share-field">Headline <select class="select" name="headline">${options(HEADLINES, (item) => item)}</select></label>
        <label class="share-field">Shape <select class="select" name="format">${options(FORMATS, (item) => item.label)}</select></label>
        <label class="check"><input type="checkbox" name="models"> Name the model I used most</label>
        <div class="actions">
          <button type="button" class="btn ghost" data-cancel>Close</button>
          <button type="button" class="btn" data-share="copy">Copy caption</button>
          <button type="button" class="btn${canShare ? "" : " primary"}" data-share="save">Save image</button>
          ${canShare ? `<button type="button" class="btn primary" data-share="send">Share</button>` : ""}
        </div>
      </div>
    </div>`,
    (wrap) => {
      const canvas = $(".share-canvas", wrap);
      let lines;
      const draw = () => {
        const format = $("[name=format]", wrap).value;
        lines = cardLines(stats, { headline: $("[name=headline]", wrap).value, models: $("[name=models]", wrap).checked, days });
        drawCard(canvas, lines, format);
        canvas.dataset.format = format;
        canvas.dataset.headline = lines.headline;
        canvas.dataset.hero = lines.hero.value;
        canvas.dataset.model = lines.model;
      };
      draw();
      // Canvas text only uses a web font once it has loaded, so draw again when the display face arrives.
      document.fonts?.load(`400 100px ${DISPLAY.split(",")[0]}`).then(() => wrap.isConnected && draw(), () => {});
      wrap.addEventListener("change", draw);
      wrap.addEventListener("click", async (event) => {
        const action = event.target.closest("[data-share]")?.dataset.share;
        const name = `cairn-vibe-${canvas.dataset.format}.png`;
        if (action === "copy") copyText(lines.caption, "Caption copied.");
        if (action === "send") send(canvas, lines.caption, name);
        if (action === "save") {
          const blob = await pngBlob(canvas);
          if (blob) save(blob, name);
        }
      });
    },
  );
}

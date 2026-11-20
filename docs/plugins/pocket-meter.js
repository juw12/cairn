// Pocket meter: a money meter drawn from one note in your brain. The plugin holds no figures of its own.
//
// It shows the first note whose properties include `pocket-meter: true`:
//   balance   what has actually arrived (the filled part of the bar)
//   ceiling   the most the plan can pay if everything comes true (the end of the bar)
//   expected  the modelled average (the marker); low / high: the likely range (the band)
//   chance    how likely a result above zero is, 0 to 1
//   currency, since, days, settlements, changed: labels
// A table in the note's body lists what contributes: | Source | Expected | Ceiling | Detail |

export const manifest = {
  id: "pocket-meter",
  name: "Pocket meter",
  version: "1.0.0",
  author: "Cairn",
  description: "A meter from zero to your ceiling: what has arrived, what is expected and which sources contribute.",
};

const number = (value) => {
  const n = Number(String(value ?? "").trim());
  return String(value ?? "").trim() !== "" && Number.isFinite(n) ? n : null;
};

export function readMeter(note) {
  const meta = note.meta || {};
  const sources = [];
  for (const line of String(note.body || "").split("\n")) {
    const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
    const expected = number(cells[1]);
    const ceiling = number(cells[2]);
    if (!line.trim().startsWith("|") || cells.length < 3 || expected === null || ceiling === null) continue;
    sources.push({ name: cells[0], expected, ceiling, detail: cells[3] || "" });
  }
  return {
    balance: number(meta.balance) ?? 0,
    ceiling: number(meta.ceiling),
    expected: number(meta.expected),
    low: number(meta.low),
    high: number(meta.high),
    chance: number(meta.chance),
    currency: String(meta.currency ?? ""),
    since: String(meta.since ?? ""),
    days: number(meta.days),
    settlements: number(meta.settlements),
    changed: String(meta.changed ?? ""),
    sources,
  };
}

const EXAMPLE = `---
pocket-meter: true
currency: Rp
balance: 0
ceiling: 400000
expected: 30000
low: 0
high: 100000
chance: 0.45
days: 30
---
| Source | Expected | Ceiling | Detail |
| --- | --- | --- | --- |
| My app | 30000 | 400000 | 20 trials at 5% |`;

export default function activate(cairn) {
  const esc = cairn.escape;
  const isMeter = (note) => ["true", "yes", "1"].includes(String(note.meta?.["pocket-meter"] ?? "").toLowerCase());

  function draw(el) {
    const note = cairn.notes().find(isMeter);
    const head = `<div class="page-head"><div><p class="eyebrow">Plugin</p><h1>Pocket meter</h1><p>What has arrived, against the most the plan can pay.</p></div></div>`;
    if (!note) {
      el.innerHTML = `${head}
        <p class="muted">No meter note in this brain yet. Add these properties to any note and the meter appears here. Keep the note private if the figures are.</p>
        <pre class="pm-example">${esc(EXAMPLE)}</pre>`;
      return;
    }
    const meter = readMeter(note);
    const money = (n) => `${n < 0 ? "-" : ""}${esc(meter.currency)}${Math.abs(Math.round(n)).toLocaleString()}`;
    const planned = meter.ceiling !== null && meter.ceiling > 0;
    const scale = Math.max(meter.ceiling || 0, meter.balance, 1);
    const at = (n) => `${Math.min(100, Math.max(0, (n / scale) * 100)).toFixed(2)}%`;
    const window = meter.days ? `next ${meter.days} days` : "the plan";
    const band = planned && meter.low !== null && meter.high !== null
      ? `<span class="pm-band" style="left:${at(meter.low)};width:calc(${at(meter.high)} - ${at(meter.low)})"></span>` : "";
    const marker = planned && meter.expected !== null ? `<span class="pm-mark" style="left:${at(meter.expected)}"></span>` : "";
    const peak = Math.max(1, ...meter.sources.map((source) => source.ceiling));
    el.innerHTML = `${head}
      <section class="pm-card">
        <div class="pm-top"><div><span class="pm-label">In the pocket</span><strong class="pm-balance">${money(meter.balance)}</strong></div>
          <div class="pm-right"><span class="pm-label">If everything comes true</span><strong>${planned ? money(meter.ceiling) : "Not set"}</strong></div></div>
        <div class="pm-bar" role="img" aria-label="${money(meter.balance)} arrived${planned ? ` of a ${money(meter.ceiling)} ceiling` : ""}">
          ${band}<span class="pm-fill" style="width:${at(meter.balance)}"></span>${marker}</div>
        <div class="pm-scale"><span>${esc(meter.currency)}0</span><span>${planned ? `${((meter.balance / meter.ceiling) * 100).toFixed(1)}% of the ceiling has arrived` : "No plan to measure against yet"}</span></div>
      </section>
      <div class="pm-tiles">
        <div class="pm-tile"><strong>${meter.chance === null ? "Not estimated" : `${(meter.chance * 100).toFixed(1)}%`}</strong><span>chance of ending above zero, ${esc(window)}</span></div>
        <div class="pm-tile"><strong>${meter.expected === null ? "Not estimated" : money(meter.expected)}</strong><span>expected, ${esc(window)} (the marker)</span></div>
        <div class="pm-tile"><strong>${meter.low === null || meter.high === null ? "Not estimated" : `${money(meter.low)} to ${money(meter.high)}`}</strong><span>likely range, 8 in 10 results (the band)</span></div>
        <div class="pm-tile"><strong>${meter.settlements ?? 0}</strong><span>settlement${meter.settlements === 1 ? "" : "s"} recorded${meter.since ? ` since ${esc(meter.since)}` : ""}</span></div>
      </div>
      <section class="pm-section"><h2>What contributes</h2>
        ${meter.sources.length
          ? `<ul class="pm-sources">${meter.sources.map((source) => `<li>
              <div class="pm-row"><span class="pm-name">${esc(source.name)}</span><span>${money(source.expected)} expected of ${money(source.ceiling)}</span></div>
              <div class="pm-track" style="width:${Math.max(4, (source.ceiling / peak) * 100).toFixed(1)}%"><span style="width:${source.ceiling > 0 ? Math.min(100, Math.max(0, (source.expected / source.ceiling) * 100)).toFixed(1) : 0}%"></span></div>
              ${source.detail ? `<small>${esc(source.detail)}</small>` : ""}</li>`).join("")}</ul>`
          : `<p class="muted">No sources listed in the note yet.</p>`}
      </section>
      <p class="pm-foot muted">Expected and ceiling are a model, not money. Only the filled part has arrived.
        Figures come from <a class="wikilink" href="#" data-open="${esc(note.path)}">${esc(note.title)}</a>${meter.changed ? `, last changed ${esc(meter.changed)}` : ""}.</p>`;
  }

  cairn.addView({ id: "meter", name: "Pocket meter", icon: "sliders", render: draw });
  cairn.on("notes-changed", () => {
    const el = document.querySelector('[data-plugin-view="plugin:pocket-meter:meter"]');
    if (el) draw(el);
  });

  cairn.addStyle(`
    .pm-card { border: 1px solid var(--line); border-radius: 10px; background: var(--surface); padding: 18px; }
    .pm-top { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
    .pm-top strong { display: block; font-family: var(--display); font-weight: 400; font-size: 24px; line-height: 1.15; overflow-wrap: anywhere; }
    .pm-top .pm-balance { font-size: clamp(34px, 8vw, 52px); }
    .pm-right { text-align: right; margin-left: auto; }
    .pm-label, .pm-tile span, .pm-scale, .pm-sources small { color: var(--muted); font-size: 12.5px; }
    .pm-bar { position: relative; height: 22px; margin-top: 16px; border-radius: 6px; background: var(--sunken); overflow: hidden; }
    .pm-fill { position: absolute; inset: 0 auto 0 0; background: var(--journal); }
    .pm-band { position: absolute; top: 0; bottom: 0; background: var(--journal-soft); }
    .pm-mark { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--ink); }
    .pm-scale { display: flex; justify-content: space-between; gap: 12px; margin-top: 6px; }
    .pm-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-top: 10px; }
    .pm-tile { border: 1px solid var(--line); border-radius: 10px; background: var(--surface); padding: 14px 16px; display: flex; flex-direction: column; gap: 2px; }
    .pm-tile strong { font-family: var(--display); font-weight: 400; font-size: 22px; line-height: 1.2; overflow-wrap: anywhere; }
    .pm-section { margin-top: 26px; }
    .pm-section h2 { font-size: 13px; font-weight: 600; color: var(--muted); margin: 0 0 10px; }
    .pm-sources { list-style: none; margin: 0; padding: 0; }
    .pm-sources li { border-bottom: 1px solid var(--line); padding: 10px 0; }
    .pm-row { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .pm-name { font-weight: 600; overflow-wrap: anywhere; }
    .pm-track { height: 6px; margin: 6px 0 4px; border-radius: 3px; background: var(--sunken); overflow: hidden; }
    .pm-track span { display: block; height: 100%; background: var(--journal); }
    .pm-foot { margin-top: 18px; font-size: 12.5px; }
    .pm-example { border: 1px solid var(--line); border-radius: 10px; background: var(--surface); padding: 14px; overflow-x: auto; font-family: var(--mono); font-size: 12.5px; }
  `);
}

// Calendar: a month view of the journal. Days with an entry are marked; click a day to open it
// (or start it), and see the notes created that day underneath.

export const manifest = {
  id: "calendar",
  name: "Calendar",
  version: "1.0.0",
  author: "Cairn",
  description: "A month calendar of your journal.",
};

const pad = (n) => String(n).padStart(2, "0");
const dayKey = (year, month, day) => `${year}-${pad(month + 1)}-${pad(day)}`;

export default function activate(cairn) {
  const esc = cairn.escape;
  let shown = null; // { year, month }
  let picked = cairn.today();

  function journalDays() {
    const days = new Map();
    for (const note of cairn.notes()) {
      if (note.type !== "journal") continue;
      const match = /(\d{4}-\d{2}-\d{2})/.exec(note.title) || /(\d{4}-\d{2}-\d{2})/.exec(note.path);
      if (match) days.set(match[1], note);
    }
    return days;
  }

  function draw(el) {
    const now = new Date();
    shown ||= { year: now.getFullYear(), month: now.getMonth() };
    const { year, month } = shown;
    const days = journalDays();
    const created = cairn.notes().filter((n) => n.created === picked && n.type !== "journal");
    const first = new Date(year, month, 1);
    const offset = (first.getDay() + 6) % 7; // weeks start on Monday
    const count = new Date(year, month + 1, 0).getDate();
    const today = cairn.today();
    const cells = [];
    for (let i = 0; i < offset; i++) cells.push(`<span class="cal-cell empty"></span>`);
    for (let day = 1; day <= count; day++) {
      const key = dayKey(year, month, day);
      const entry = days.get(key);
      const words = entry ? entry.body.split(/\s+/).filter(Boolean).length : 0;
      const size = entry ? Math.min(10, 4 + Math.round(Math.log2(words + 1))) : 0;
      cells.push(`<button class="cal-cell${key === today ? " today" : ""}${key === picked ? " picked" : ""}${entry ? " has" : ""}" data-cal-day="${key}" aria-label="${key}${entry ? `, journal entry, ${words} words` : ""}">
        <span>${day}</span>${entry ? `<i style="--dot:${size}px"></i>` : ""}</button>`);
    }
    const title = first.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    const entry = days.get(picked);
    el.innerHTML = `
      <div class="page-head"><div><p class="eyebrow">Plugin</p><h1>Calendar</h1><p>Your journal by month. Click a day to open it, or start it if it's empty.</p></div>
        <div class="actions"><button class="btn" data-cal-today>Today</button></div></div>
      <div class="cal">
        <div class="cal-head">
          <button class="btn sm ghost icon-only" data-cal-move="-1" aria-label="Previous month">‹</button>
          <strong>${esc(title)}</strong>
          <button class="btn sm ghost icon-only" data-cal-move="1" aria-label="Next month">›</button>
        </div>
        <div class="cal-grid">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => `<span class="cal-dow">${d}</span>`).join("")}${cells.join("")}</div>
      </div>
      <section class="cal-day">
        <h2>${esc(picked)}</h2>
        ${entry ? `<p><a class="wikilink" href="#" data-open="${esc(entry.path)}">Open the journal entry</a></p>` : `<p class="muted">No journal entry yet. <button class="btn sm" data-cal-open="${esc(picked)}">${cairn.readOnly() ? "Nothing here" : "Start this day"}</button></p>`}
        ${created.length ? `<h3>Created this day</h3><ul>${created.map((n) => `<li><a class="wikilink" href="#" data-open="${esc(n.path)}">${esc(n.title)}</a> <span class="muted">· ${esc(n.type)}</span></li>`).join("")}</ul>` : ""}
      </section>`;
    el.onclick = (event) => {
      const move = event.target.closest("[data-cal-move]");
      if (move) {
        const next = new Date(shown.year, shown.month + Number(move.dataset.calMove), 1);
        shown = { year: next.getFullYear(), month: next.getMonth() };
        return draw(el);
      }
      if (event.target.closest("[data-cal-today]")) {
        shown = null;
        picked = cairn.today();
        return draw(el);
      }
      const day = event.target.closest("[data-cal-day]");
      if (day) {
        picked = day.dataset.calDay;
        if (days.has(picked)) cairn.openNote(days.get(picked).path);
        return draw(el);
      }
      const open = event.target.closest("[data-cal-open]");
      if (open && !cairn.readOnly()) cairn.openJournal(open.dataset.calOpen).then(() => draw(el));
    };
  }

  cairn.addView({ id: "calendar", name: "Calendar", icon: "journal", render: draw });
  cairn.addCommand({ id: "open", name: "Open the calendar", icon: "journal", run: () => cairn.openView("calendar") });
  cairn.on("notes-changed", () => {
    const el = document.querySelector('[data-plugin-view="plugin:calendar:calendar"]');
    if (el) draw(el);
  });

  cairn.addStyle(`
    .cal { max-width: 560px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); padding: 14px; }
    .cal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; font-size: 15px; }
    .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
    .cal-dow { text-align: center; font-size: 11.5px; color: var(--muted); padding-bottom: 4px; }
    .cal-cell { position: relative; aspect-ratio: 1; border: 1px solid transparent; border-radius: 6px; background: var(--surface-2); color: var(--ink); font: inherit; font-size: 13px; display: flex; align-items: flex-start; justify-content: flex-start; padding: 6px; cursor: pointer; }
    .cal-cell.empty { background: none; cursor: default; }
    .cal-cell:hover { border-color: var(--line-strong); }
    .cal-cell.today span { font-weight: 700; color: var(--accent-ink); }
    .cal-cell.picked { border-color: var(--ink); }
    .cal-cell i { position: absolute; left: 50%; bottom: 7px; width: var(--dot); height: var(--dot); margin-left: calc(var(--dot) / -2); border-radius: 50%; background: var(--journal); }
    .cal-day { margin-top: 22px; }
    .cal-day h2 { font-family: var(--display); font-weight: 400; font-size: 26px; margin: 0 0 6px; }
    .cal-day h3 { font-size: 13px; color: var(--muted); margin: 16px 0 6px; }
  `);
}

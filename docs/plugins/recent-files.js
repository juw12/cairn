// Recent files: remembers the notes you open on this device, newest first.

export const manifest = {
  id: "recent-files",
  name: "Recent files",
  version: "1.0.0",
  author: "Cairn",
  description: "Lists the notes you opened recently, newest first.",
};

const LIMIT = 30;

export default function activate(cairn) {
  const esc = cairn.escape;
  const key = () => `recent:${cairn.project()}`;
  const recent = () => cairn.settings.get(key(), []).filter((item) => cairn.note(item.path));

  cairn.on("note-open", ({ path }) => {
    const list = cairn.settings.get(key(), []).filter((item) => item.path !== path);
    list.unshift({ path, at: Date.now() });
    cairn.settings.set(key(), list.slice(0, LIMIT));
    const el = document.querySelector('[data-plugin-view="plugin:recent-files:recent"]');
    if (el) draw(el);
  });

  function draw(el) {
    const list = recent();
    el.innerHTML = `
      <div class="page-head"><div><p class="eyebrow">Plugin</p><h1>Recent files</h1><p>Notes you opened on this device, newest first.</p></div>
        <div class="actions">${list.length ? `<button class="btn sm ghost" data-recent-clear>Clear</button>` : ""}</div></div>
      ${list.length
        ? `<div class="rf-list">${list.map((item) => {
            const note = cairn.note(item.path);
            return `<button class="list-row" data-open="${esc(note.path)}"><span class="title">${esc(note.title)}</span><span class="muted">${esc(note.path)}</span><span class="muted">${new Date(item.at).toLocaleString()}</span></button>`;
          }).join("")}</div>`
        : `<p class="muted">Open a few notes and they'll show up here.</p>`}`;
    el.onclick = (event) => {
      if (event.target.closest("[data-recent-clear]")) {
        cairn.settings.set(key(), []);
        draw(el);
      }
    };
  }

  cairn.addView({ id: "recent", name: "Recent files", icon: "list", render: draw });
  cairn.addCommand({
    id: "last",
    name: "Go back to the last note",
    icon: "arrow",
    run: () => {
      const current = cairn.activeNote()?.path;
      const previous = recent().find((item) => item.path !== current);
      if (!previous) return cairn.toast("No other recent note yet.");
      cairn.openNote(previous.path);
    },
  });
  cairn.addStyle(`.rf-list .list-row { display: grid; grid-template-columns: 1.2fr 1fr auto; gap: 12px; width: 100%; text-align: left; }`);
}

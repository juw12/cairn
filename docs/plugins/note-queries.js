// Note queries: live lists of notes from a ```query block. Each line is "key: value".
//
//   ```query
//   type: task
//   status: todo, doing
//   tag: release
//   folder: tasks
//   text: offline
//   sort: updated desc        (updated | created | title | status, asc or desc)
//   limit: 10
//   show: table               (table | list | count)
//   ```

export const manifest = {
  id: "note-queries",
  name: "Note queries",
  version: "1.0.0",
  author: "Cairn",
  description: "Live lists and tables of notes from a query code block.",
};

const EXAMPLE = "```query\ntype: task\nstatus: todo, doing\nsort: updated desc\nlimit: 10\nshow: table\n```\n";

export function parseQuery(source) {
  const query = { type: [], status: [], tag: [], folder: "", text: "", sort: "updated", order: "desc", limit: 50, show: "list" };
  for (const raw of String(source).split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes(":")) continue;
    const key = line.slice(0, line.indexOf(":")).trim().toLowerCase();
    const value = line.slice(line.indexOf(":") + 1).trim();
    const list = value.split(",").map((v) => v.trim().replace(/^#/, "").toLowerCase()).filter(Boolean);
    if (key === "type" || key === "types") query.type = list;
    else if (key === "status") query.status = list;
    else if (key === "tag" || key === "tags") query.tag = list;
    else if (key === "folder" || key === "path") query.folder = value.replace(/^\/+|\/+$/g, "").toLowerCase();
    else if (key === "text" || key === "where" || key === "contains") query.text = value.toLowerCase();
    else if (key === "sort") {
      const [field, order] = value.toLowerCase().split(/\s+/);
      query.sort = ["updated", "created", "title", "status"].includes(field) ? field : "updated";
      query.order = order === "asc" ? "asc" : order === "desc" ? "desc" : field === "title" ? "asc" : "desc";
    } else if (key === "limit") query.limit = Math.max(1, Math.min(500, Number(value) || 50));
    else if (key === "show" || key === "as") query.show = ["table", "list", "count"].includes(value.toLowerCase()) ? value.toLowerCase() : "list";
  }
  return query;
}

export function runQuery(notes, query, selfPath = "") {
  const matches = notes.filter((note) => {
    if (note.path === selfPath) return false;
    if (query.type.length && !query.type.includes(note.type)) return false;
    if (query.status.length && !query.status.includes(String(note.status || "").toLowerCase())) return false;
    if (query.tag.length && !query.tag.every((tag) => note.tags.map((t) => t.toLowerCase()).includes(tag))) return false;
    if (query.folder && !note.path.toLowerCase().startsWith(`${query.folder}/`)) return false;
    if (query.text && !`${note.title}\n${note.body}`.toLowerCase().includes(query.text)) return false;
    return true;
  });
  const key = {
    updated: (n) => n.mtime || 0,
    created: (n) => n.created || "",
    title: (n) => n.title.toLowerCase(),
    status: (n) => String(n.status || ""),
  }[query.sort];
  matches.sort((a, b) => {
    const x = key(a);
    const y = key(b);
    const order = x < y ? -1 : x > y ? 1 : 0;
    return query.order === "asc" ? order : -order;
  });
  return { total: matches.length, rows: matches.slice(0, query.limit) };
}

function when(ms) {
  if (!ms) return "";
  const date = new Date(ms);
  return date.toISOString().slice(0, 10);
}

export default function activate(cairn) {
  const esc = cairn.escape;
  const link = (note) => `<a class="wikilink" href="#" data-open="${esc(note.path)}">${esc(note.title)}</a>`;

  cairn.registerCodeBlock("query", (source, el, context) => {
    const query = parseQuery(source);
    const { total, rows } = runQuery(cairn.notes(), query, context.notePath);
    el.classList.add("nq");
    if (query.show === "count") {
      el.innerHTML = `<p class="nq-count"><strong>${total}</strong> matching note${total === 1 ? "" : "s"}</p>`;
      return;
    }
    if (!rows.length) {
      el.innerHTML = `<p class="nq-empty">No notes match this query.</p>`;
      return;
    }
    const more = total > rows.length ? `<p class="nq-more">Showing ${rows.length} of ${total}.</p>` : "";
    if (query.show === "table") {
      el.innerHTML = `<table class="nq-table"><thead><tr><th>Note</th><th>Type</th><th>Status</th><th>Tags</th><th>Updated</th></tr></thead><tbody>${rows
        .map((n) => `<tr><td>${link(n)}</td><td>${esc(n.type)}</td><td>${esc(n.status || "")}</td><td>${n.tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join(" ")}</td><td>${when(n.mtime)}</td></tr>`)
        .join("")}</tbody></table>${more}`;
      return;
    }
    el.innerHTML = `<ul class="nq-list">${rows.map((n) => `<li>${link(n)}${n.status ? ` <span class="muted">· ${esc(n.status)}</span>` : ""}</li>`).join("")}</ul>${more}`;
  });

  cairn.addCommand({
    id: "insert-query",
    name: "Insert a note query into this note",
    icon: "list",
    run: async () => {
      if (!cairn.activeNote()) return cairn.toast("Open a note first, then run this again.");
      await cairn.insertIntoActiveNote(EXAMPLE);
      cairn.toast("Query added. Switch to Preview to see the results.");
    },
  });

  cairn.addStyle(`
    .plugin-block.nq { margin: 12px 0; }
    .nq-table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
    .nq-table th { text-align: left; font-weight: 600; color: var(--muted); border-bottom: 1px solid var(--line); padding: 6px 8px; }
    .nq-table td { border-bottom: 1px solid var(--line); padding: 6px 8px; vertical-align: top; }
    .nq-list { margin: 0; padding-left: 20px; }
    .nq-more, .nq-empty, .nq-count { color: var(--muted); font-size: 13px; margin: 6px 0 0; }
    .nq-count strong { color: var(--ink); font-size: 18px; }
  `);
}

// The main views (Today, Ideas, Board, Decisions, Notes, Journal, Code inbox, Context pack) and board drag and drop.
// Today, Ideas, Board, Decisions, Notes and Code inbox are pages of blocks (vibelayout.js): a Dashboard, a Recap
// and the page's own content, each of which folds and moves.
// The sidebar holds six hubs (Today, Journal, Chat, Map, Build, People); Notes, Code inbox and Context pack sit under Tools.

import * as core from "./core.js";
import { renderMarkdown, escapeHtml as esc } from "./md.js";
import { $, $$, STATUS_HINT, STATUS_LABEL, TYPE_META, byRecent, checklist, emptyState, icon, pageHead, plural, relativeTime, rowItem, snippetOf, statusPill, tagChips, toast, today } from "./ui.js";
import { fileByPath, guide, markGuide, noteByPath, readOnly, saveNote, state } from "./state.js";
import { bookmarks, countNotes, folderTree, searchNotes } from "./vault.js";
import { render } from "./app.js";
import { homeLogStrip, homeLogSummary } from "./voicelog.js";
import { boardHtml, dashboardHtml, noteRecap } from "./vibelayout.js";
import { homeBlocks, homeData, homeLede } from "./home.js";

// Past this many notes a brain is not new any more, and a Home column shows this many rows.
const ESTABLISHED_NOTES = 50;
const FOCUS_ROWS = 5;

// ============================================================ views

export function home() {
  const data = homeData();
  const hour = new Date().getHours();
  const greeting = readOnly()
    ? `What @${esc(state.info.owner)} is working on`
    : hour < 12 ? "Good morning." : hour < 18 ? "Good afternoon." : "Good evening.";
  const dateLabel = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  // The three first moves are for a brain that is just starting, not for one with a history.
  const g = guide();
  const established = state.notes.length > ESTABLISHED_NOTES;
  const showGuide = !readOnly() && !established && !g.dismissed && !(g.captured && g.moved && g.promoted);
  const step = (isDone, n, title, text) =>
    `<li class="guide-step ${isDone ? "done" : ""}"><span class="num">${isDone ? icon("check", "sm") : n}</span><div><strong>${title}</strong><span>${text}</span></div></li>`;
  const guideHtml = showGuide
    ? `<section class="guide" id="guide" aria-label="Getting started">
        <div class="guide-intro"><h2>Three moves to get the hang of it</h2><p>Everything you do here is saved as plain Markdown in <code>brain/</code>.</p></div>
        <ol class="guide-steps">
          ${step(g.captured, 1, "Capture a thought", "Type in the bar at the top, press Enter.")}
          ${step(g.moved, 2, "Move it forward", "On Ideas, drag a card from Raw to Exploring.")}
          ${step(g.promoted, 3, "Turn it into work", "Open an idea, press Promote to task.")}
        </ol>
        <button class="btn ghost sm icon-only guide-close" data-action="dismiss-guide" aria-label="Hide the guide">${icon("x", "sm")}</button>
      </section>`
    : "";

  const emptyBrain = state.notes.length === 0;
  const sampleButton = emptyBrain && state.info?.canLoadSample ? `<button class="btn primary" data-action="load-sample">${icon("play")} Load the sample project</button>` : "";
  const intro = emptyBrain
    ? "An empty brain. Write the first thing on your mind in the bar above."
    : readOnly()
      ? `A read-only look at <strong>${esc(state.info.owner)}/${esc(state.info.repoName)}</strong>.`
      : homeLede() || `Here's where <strong>${esc(state.info.project)}</strong> stands.`;

  // A column shows its first rows; the rest are one tap away on the column's own page.
  const column = (title, view, linkLabel, rows, empty) => `
    <section class="column-block">
      <header><h2>${title}</h2><button class="text-link" data-go="${view}">${linkLabel} ${icon("arrow", "sm")}</button></header>
      ${rows.length ? `<div class="rows">${rows.slice(0, FOCUS_ROWS).join("")}</div>` : `<p class="empty">${empty}</p>`}
      ${rows.length > FOCUS_ROWS ? `<p class="recap-more"><button class="text-link" data-go="${view}">${rows.length - FOCUS_ROWS} more ${icon("arrow", "sm")}</button></p>` : ""}
    </section>`;

  const { doing, fresh, proposed, done } = data;
  const logStrip = homeLogStrip();
  const blocks = {
    ...homeBlocks(),
    log: logStrip ? { html: logStrip, summary: homeLogSummary() } : null,
    focus: {
      html: `<div class="columns-3">
        ${column("Doing now", "board", "Board", doing.map((n) => rowItem(n, checklistMeta(n))), "Nothing in progress. Drag a task to Doing.")}
        ${column("Fresh ideas", "ideas", "Ideas", fresh.map((n) => rowItem(n, relativeTime(n.mtime))), "No raw ideas. Capture one above.")}
        ${column("Needs a decision", "decisions", "Decisions", proposed.map((n) => rowItem(n, "proposed")), "No open questions.")}
      </div>`,
      summary: `${doing.length} doing · ${fresh.length} fresh ${fresh.length === 1 ? "idea" : "ideas"} · ${proposed.length} to decide`,
    },
  };

  return `
    <header class="today">
      <div class="today-text">
        <p class="eyebrow">${dateLabel}</p>
        <h1>${greeting}</h1>
        <p class="lede">${intro}</p>
        <div class="actions">
          ${readOnly() ? "" : `<button class="btn" data-action="journal-today">${icon("journal")} Today's journal</button>`}
          ${sampleButton}
        </div>
      </div>
      <figure class="cairn">
        ${cairnSvg(done)}
        <figcaption><strong>${done}</strong> ${done === 1 ? "stone" : "stones"}<span>one for every task finished</span></figcaption>
      </figure>
    </header>
    ${guideHtml}
    ${boardHtml("home", blocks)}`;
}

function checklistMeta(note) {
  const { total, done } = checklist(note);
  return total ? `${done}/${total}` : relativeTime(note.mtime);
}

function itemCard(note) {
  const snippet = snippetOf(note);
  const { total, done } = checklist(note);
  const links = core.extractLinks(note.body).length;
  const source = typeof note.meta.source === "string" ? note.meta.source : "";
  const statuses = core.STATUSES[note.type];
  return `
    <article class="item-card t-${note.type}" draggable="${readOnly() ? "false" : "true"}" data-open="${esc(note.path)}" data-drag="${esc(note.path)}" tabindex="0" aria-label="${esc(note.title)}">
      <div class="title">${esc(note.title)}</div>
      ${snippet ? `<div class="snippet">${esc(snippet)}</div>` : ""}
      ${total ? `<div class="progress" title="${done} of ${total} done"><i style="width:${Math.round((done / total) * 100)}%"></i></div>` : ""}
      <div class="foot">
        ${tagChips(note.tags, 2)}<span class="grow"></span>
        ${source ? `<span class="source-chip" title="${esc(source)}">${icon("code", "sm")}${esc(source.split("/").pop())}</span>` : ""}
        ${links ? `<span class="metric" title="${plural(links, "link")}">${icon("link", "sm")}${links}</span>` : ""}
        ${total ? `<span class="metric">${done}/${total}</span>` : ""}
      </div>
      ${readOnly() ? "" : `<select class="move-select" data-move="${esc(note.path)}" aria-label="Move ${esc(note.title)}">
        ${statuses.map((s) => `<option value="${s}" ${s === note.status ? "selected" : ""}>${STATUS_LABEL[s]}</option>`).join("")}
      </select>`}
    </article>`;
}

function boardColumns(type) {
  const statuses = core.STATUSES[type];
  const items = state.notes.filter((n) => n.type === type);
  return `<div class="board" data-board="${type}">${statuses
    .map((status) => {
      const inColumn = items.filter((n) => n.status === status).sort(byRecent);
      const placeholder = type === "idea" ? "Add an idea…" : "Add a task…";
      return `
        <section class="column" data-column="${status}" aria-label="${STATUS_LABEL[status]}">
          <div class="column-head"><span class="dot status-${status}" style="background:var(--t)"></span><h2>${STATUS_LABEL[status]}</h2><span class="count">${inColumn.length}</span></div>
          ${STATUS_HINT[status] ? `<p class="column-hint">${STATUS_HINT[status]}</p>` : ""}
          ${status !== "done" && status !== "parked" && !readOnly() ? `<form class="quick-add" data-quick="${type}:${status}"><input name="title" placeholder="${placeholder}" aria-label="${placeholder} (${STATUS_LABEL[status]})"></form>` : ""}
          ${inColumn.map(itemCard).join("")}
        </section>`;
    })
    .join("")}</div>`;
}

// The numbers and the latest changes of a board (tasks or ideas), for its Dashboard and Recap blocks.
function boardBlocks(type, extra = "") {
  const statuses = core.STATUSES[type];
  const items = state.notes.filter((n) => n.type === type);
  const count = (status) => items.filter((n) => n.status === status).length;
  const week = Date.now() - 7 * 86400000;
  const noun = type === "idea" ? "idea" : "task";
  const tiles = statuses.map((status) => ({ value: count(status), label: STATUS_LABEL[status].toLowerCase(), title: STATUS_HINT[status] || "" }));
  tiles.push({ value: items.filter((n) => (n.mtime || 0) > week).length, label: "changed this week" });
  if (type === "task") {
    const lists = items.map(checklist).filter((c) => c.total);
    const total = lists.reduce((sum, c) => sum + c.total, 0);
    const ticked = lists.reduce((sum, c) => sum + c.done, 0);
    tiles.push({ value: total ? `${Math.round((ticked / total) * 100)}%` : "–", label: "checklist items ticked", sub: total ? `${ticked} of ${total}` : "" });
  }
  const open = statuses.filter((status) => status !== "done" && status !== "parked");
  return {
    dashboard: { html: dashboardHtml(tiles), summary: `${plural(items.length, noun)} · ${open.map((status) => `${count(status)} ${STATUS_LABEL[status].toLowerCase()}`).join(" · ")}` },
    recap: {
      html: noteRecap(items, (n) => STATUS_LABEL[n.status] || n.status, type === "idea" ? "No ideas yet. Capture one in the bar above." : "No tasks yet. Add one below."),
      summary: items.length ? `last: ${[...items].sort(byRecent)[0].title}` : `no ${noun}s yet`,
    },
    cards: { html: `${extra}${boardColumns(type)}`, summary: plural(items.length, noun) },
  };
}

export function ideas() {
  const spark = state.spark && !state.open ? `<div class="spark-card">${icon("idea")}<div><strong>${esc(state.spark)}</strong><br><span class="muted">Pick an idea and answer this in it, or capture what it makes you think of.</span></div></div>` : "";
  return `${pageHead("Ideas", readOnly()
    ? "What this brain is thinking about, from raw to ready. Open one to read it in full."
    : "Brainstorm here. Capture without judging, then drag ideas right as they firm up. Open one to spark it with a prompt or promote it to a task.",
    `<button class="btn" data-action="spark-random">${icon("shuffle")} Spark a prompt</button><button class="btn primary" data-action="new" data-type="idea">${icon("plus")} New idea</button>`)}
    ${boardHtml("ideas", boardBlocks("idea", spark))}`;
}

export function board() {
  return `${pageHead("Board", readOnly()
    ? "What this brain is working on, from to do to done. Open a card to read its checklist."
    : "Everything you are going to do, from to do to done. Drag cards between columns; open one to edit its checklist.",
    `<button class="btn primary" data-action="new" data-type="task">${icon("plus")} New task</button>`)}
    ${boardHtml("board", boardBlocks("task"))}`;
}

function listRow(note) {
  const snippet = snippetOf(note);
  return `
    <button class="list-row" data-open="${esc(note.path)}">
      <span class="type-icon t-${note.type}">${icon(TYPE_META[note.type].icon)}</span>
      <span class="main-col"><div class="title">${esc(note.title)}</div>${snippet ? `<div class="snippet">${esc(snippet)}</div>` : ""}</span>
      <span class="tags">${statusPill(note.status)}${note.tags.slice(0, 2).map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}</span>
      <span class="when">${relativeTime(note.mtime)}</span>
    </button>`;
}

export function decisions() {
  const all = state.notes.filter((n) => n.type === "decision");
  const groups = core.STATUSES.decision
    .map((status) => {
      const items = all.filter((n) => n.status === status).sort((a, b) => (a.slug < b.slug ? 1 : -1));
      if (!items.length) return "";
      return `<div class="group-title">${statusPill(status)} ${items.length}</div><div class="card list">${items.map(listRow).join("")}</div>`;
    })
    .join("");
  const count = (status) => all.filter((n) => n.status === status).length;
  const week = Date.now() - 7 * 86400000;
  const tiles = [
    { value: all.length, label: "decisions" },
    ...core.STATUSES.decision.map((status) => ({ value: count(status), label: (STATUS_LABEL[status] || status).toLowerCase() })),
    { value: all.filter((n) => (n.mtime || 0) > week).length, label: "changed this week" },
  ];
  const latest = [...all].sort(byRecent)[0];
  const blocks = {
    dashboard: { html: dashboardHtml(tiles), summary: `${plural(all.length, "decision")} · ${count("proposed")} proposed` },
    recap: {
      html: noteRecap(all, (n) => STATUS_LABEL[n.status] || n.status, "No decisions yet."),
      summary: latest ? `last: ${latest.title}` : "no decisions yet",
    },
    log: {
      html: all.length ? groups : `<div class="card">${emptyState("decision", "No decisions yet", "Capture one with “decision: use SQLite” or press New decision. Each one gets a numbered file and a template.")}</div>`,
      summary: plural(all.length, "decision"),
    },
  };
  return `${pageHead("Decisions", "A lightweight decision log: context, options, decision, consequences. Reviewed in pull requests like code, so the “why” never gets lost.",
    `<button class="btn primary" data-action="new" data-type="decision">${icon("plus")} New decision</button>`)}
    ${boardHtml("decisions", blocks)}`;
}

function folderHtml(folder, depth = 0) {
  const folders = [...folder.folders.values()].sort((a, b) => (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1));
  const notes = [...folder.notes].sort((a, b) => (a.path.toLowerCase() < b.path.toLowerCase() ? -1 : 1));
  const closed = state.closedFolders || (state.closedFolders = new Set());
  return `${folders
    .map((child) => `<details class="folder" data-folder="${esc(child.path)}" ${closed.has(child.path) ? "" : "open"} style="--depth:${depth}">
        <summary>${icon("folder", "sm")}<span class="name">${esc(child.name)}</span><span class="count">${countNotes(child)}</span></summary>
        ${folderHtml(child, depth + 1)}
      </details>`)
    .join("")}${notes
    .map((note) => `<button class="tree-note" data-open="${esc(note.path)}" style="--depth:${depth}" title="${esc(note.path)}"><span class="type-icon t-${note.type}">${icon(TYPE_META[note.type].icon, "sm")}</span><span class="name">${esc(note.path.split("/").pop().replace(/\.md$/i, ""))}</span>${note.title !== note.slug ? `<span class="muted title">${esc(note.title)}</span>` : ""}</button>`)
    .join("")}`;
}

export function notesView() {
  const filters = [["all", "All"], ["note", "Notes"], ["idea", "Ideas"], ["task", "Tasks"], ["decision", "Decisions"], ["journal", "Journal"], ["bookmarks", "Bookmarks"]];
  const query = state.notesQuery.trim();
  const tagCounts = new Map();
  for (const note of state.notes) for (const tag of note.tags) tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
  const topTags = [...tagCounts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).slice(0, 12);
  const starred = state.notesFilter === "bookmarks" ? bookmarks() : null;
  const filtered = state.notes
    .filter((n) => state.notesFilter === "all" || (starred ? starred.includes(n.path) : n.type === state.notesFilter))
    .filter((n) => !state.notesTag || n.tags.includes(state.notesTag));
  const items = (query ? searchNotes(filtered, query) : filtered).sort(starred && !query ? (a, b) => starred.indexOf(a.path) - starred.indexOf(b.path) : byRecent);
  const layout = state.notesLayout === "folders" ? "folders" : "list";
  const pageKey = [state.notesFilter, state.notesTag, query, layout].join("\u0000");
  if (state.notesMore?.key !== pageKey) state.notesMore = { key: pageKey, limit: NOTES_PAGE };
  const shown = state.notesMore.limit;
  const more = layout === "list" && items.length > shown
    ? `<div class="list-more"><button class="btn" data-action="notes-more">Show ${Math.min(NOTES_PAGE, items.length - shown)} more</button><span class="muted">${shown} of ${items.length}</span></div>`
    : "";
  const empty = starred && !query
    ? emptyState("star", "No bookmarks yet", "Open a note and press the star to keep it one click away.")
    : emptyState("search", "Nothing matches", "Try another word, or clear the filters. Search understands tag:, path:, file:, type:, status:, \"exact phrase\" and -exclude.");
  const week = Date.now() - 7 * 86400000;
  const ofType = (type) => state.notes.filter((n) => n.type === type).length;
  const tiles = [
    { value: state.notes.length, label: "notes in the brain" },
    ...[["idea", "ideas"], ["task", "tasks"], ["decision", "decisions"], ["journal", "journal pages"]].map(([type, label]) => ({ value: ofType(type), label })),
    { value: tagCounts.size, label: tagCounts.size === 1 ? "tag" : "tags" },
    { value: state.notes.filter((n) => (n.mtime || 0) > week).length, label: "changed this week" },
  ];
  const newest = [...state.notes].sort(byRecent)[0];
  const blocks = {
    dashboard: { html: dashboardHtml(tiles), summary: `${plural(state.notes.length, "note")} · ${plural(tagCounts.size, "tag")}` },
    recap: {
      html: noteRecap(state.notes, (n) => `${TYPE_META[n.type].label}${n.status ? ` · ${STATUS_LABEL[n.status] || n.status}` : ""}`, "Nothing yet."),
      summary: newest ? `last touched: ${newest.title}, ${relativeTime(newest.mtime)}` : "nothing yet",
    },
    all: {
      html: `<div class="toolbar">
      <div class="segmented" role="tablist" aria-label="Filter by type">${filters.map(([id, label]) => `<button class="${state.notesFilter === id ? "on" : ""}" data-filter="${id}" role="tab" aria-selected="${state.notesFilter === id}">${id === "bookmarks" ? icon("star", "sm") : ""}${label}</button>`).join("")}</div>
      <label class="search-field">${icon("search", "sm")}<span class="sr-only">Search notes</span><input class="input" id="notes-search" placeholder="Search… try tag:release or path:ideas/" value="${esc(state.notesQuery)}"></label>
      <div class="segmented layout-toggle" role="tablist" aria-label="Layout">
        <button class="${layout === "list" ? "on" : ""}" data-notes-layout="list" role="tab" aria-selected="${layout === "list"}" title="List">${icon("list", "sm")}<span class="sr-only">List</span></button>
        <button class="${layout === "folders" ? "on" : ""}" data-notes-layout="folders" role="tab" aria-selected="${layout === "folders"}" title="Folders">${icon("folder", "sm")}<span class="sr-only">Folders</span></button>
      </div>
    </div>
    ${topTags.length ? `<div class="tags" style="margin-bottom:14px">${topTags.map(([tag, count]) => `<button class="tag ${state.notesTag === tag ? "on" : ""}" data-tag="${esc(tag)}">#${esc(tag)} <span class="muted">&nbsp;${count}</span></button>`).join("")}</div>` : ""}
    ${query ? `<p class="muted search-summary">${plural(items.length, "note")} match <code>${esc(query)}</code></p>` : ""}
    ${layout === "folders"
      ? `<div class="card tree" id="notes-tree">${items.length ? folderHtml(folderTree(items)) : empty}</div>`
      : `<div class="card list" id="notes-list">${items.length ? items.slice(0, shown).map(listRow).join("") : empty}</div>${more}`}`,
      summary: `${plural(items.length, "note")}${query || state.notesTag || state.notesFilter !== "all" ? " shown" : ""}`,
    },
  };
  return `${pageHead("Notes", "Everything in the brain. Link notes with [[file-name]]; backlinks show up in each note.",
    `<button class="btn primary" data-action="new" data-type="note">${icon("plus")} New note</button>`)}
    ${boardHtml("notes", blocks)}`;
}

// Big vaults: render the list a page at a time (a new filter or search starts again at one page).
const NOTES_PAGE = 300;

export function showMoreNotes() {
  state.notesMore = { key: state.notesMore?.key, limit: (state.notesMore?.limit || NOTES_PAGE) + NOTES_PAGE };
}

export function journal() {
  const pages = state.notes.filter((n) => n.type === "journal").sort((a, b) => (a.slug < b.slug ? 1 : -1));
  return `${pageHead("Journal", "One page per day. Log progress as you go, or capture “log: fixed the GPX bug” from anywhere to add a timestamped line to today’s page.",
    `<button class="btn primary" data-action="journal-today">${icon("journal")} Open today</button>`)}
    <div class="card list">${pages.length ? pages.map(listRow).join("") : emptyState("journal", "No journal pages yet", "Open today’s page to start. It’s just a Markdown file named after the date.")}</div>`;
}


// ------------------------------------------------------------ code inbox

export function sourceKey(item) {
  return `${item.path}:${item.line}`;
}

export function codeView() {
  const canScan = Boolean(state.info?.canScan);
  const refresh = canScan ? `<button class="btn" data-action="refresh-todos">${icon("refresh")} Rescan</button>` : "";
  const head = pageHead("Code inbox", "TODO, FIXME and HACK comments found in the code around your brain. Turn the ones that matter into tasks; each task remembers its file and line.", refresh);
  const todos = state.todos || [];
  // `source` is also where a synced note came from (github-sync, opencode); a task made here points at file:line.
  const fromCode = state.notes.filter((n) => n.type === "task" && typeof n.meta.source === "string" && /:\d+$/.test(n.meta.source));
  const tracked = new Set(fromCode.map((n) => n.meta.source));
  const groups = new Map();
  for (const item of todos) {
    if (!groups.has(item.path)) groups.set(item.path, []);
    groups.get(item.path).push(item);
  }
  let inbox;
  // A brain read from GitHub holds notes, not the code around them, so there is nothing to wait for.
  if (!canScan) inbox = `<div class="card">${emptyState("code", "No code to scan here", "This brain holds your notes only. Open Cairn in the project folder on your computer to see its TODO, FIXME and HACK comments.")}</div>`;
  else if (state.todos === null) inbox = `<div class="card">${emptyState("code", "Scanning your code…", "Looking for TODO, FIXME and HACK comments.")}</div>`;
  else if (state.todosError) inbox = `<div class="card">${emptyState("code", "Couldn’t scan the code", esc(state.todosError))}</div>`;
  else if (!todos.length) inbox = `<div class="card">${emptyState("check", "Inbox zero", state.info.emptyScanHint || "No TODO, FIXME or HACK comments found. Nice and tidy.")}</div>`;
  else {
    inbox = [...groups.entries()]
      .map(([path, items]) => `
      <section class="card file-group">
        <div class="file-head">${icon("code", "sm")}${esc(path)}<span class="muted" style="margin-left:auto">${plural(items.length, "item")}</span></div>
        ${items
          .map((item) => {
            const key = sourceKey(item);
            const action = tracked.has(key)
              ? `<span class="tracked">${icon("check", "sm")} Tracked</span>`
              : `<button class="btn sm" data-action="todo-task" data-source="${esc(key)}">${icon("plus", "sm")} Make task</button>`;
            return `<div class="todo-row"><span class="pill tag-${esc(item.tag)}">${esc(item.tag)}</span><span>${esc(item.text)}</span><span class="line">line ${item.line}</span>${action}</div>`;
          })
          .join("")}
      </section>`)
      .join("");
  }
  const kinds = new Map();
  for (const item of todos) kinds.set(item.tag, (kinds.get(item.tag) || 0) + 1);
  const trackedCount = todos.filter((item) => tracked.has(sourceKey(item))).length;
  const scanned = state.todos !== null && !state.todosError;
  const tiles = [
    { value: scanned ? todos.length : "–", label: "comments found" },
    ...[...kinds.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([tag, count]) => ({ value: count, label: tag })),
    { value: scanned ? groups.size : "–", label: groups.size === 1 ? "file" : "files" },
    { value: scanned ? trackedCount : "–", label: "already tasks" },
    { value: scanned ? todos.length - trackedCount : "–", label: "not tracked yet" },
  ];
  const blocks = {
    dashboard: { html: dashboardHtml(tiles), summary: scanned ? `${plural(todos.length, "comment")} · ${trackedCount} tracked` : canScan ? "scanning…" : "no code on this device" },
    recap: {
      html: noteRecap(fromCode, (n) => `${STATUS_LABEL[n.status] || n.status} · from ${n.meta.source}`, "No tasks made from code comments yet."),
      summary: fromCode.length ? `${plural(fromCode.length, "task")} made from code` : "no tasks from code yet",
    },
    inbox: { html: inbox, summary: scanned ? plural(todos.length, "comment") : "" },
  };
  return `${head}${boardHtml("code", blocks)}`;
}

// ------------------------------------------------------------ context pack

export function contextView() {
  const pack = core.contextPack(state.notes, state.info.project, today());
  const existing = fileByPath("CONTEXT.md");
  const saved = existing ? `Last saved ${relativeTime(existing.mtime)}${existing.text === pack ? " · up to date" : " · out of date"}` : "Not saved yet";
  const body = state.contextMode === "preview"
    ? `<div class="prose" style="padding:22px 26px">${renderMarkdown(pack, (t) => core.resolveLink(state.notes, t)).replace(/<(\/?)h([1-5])>/g, (_, slash, level) => `<${slash}h${+level + 1}>`)}</div>`
    : `<pre class="pre-box">${esc(pack)}</pre>`;
  return `${pageHead("Context pack", "A one-page brief of where the project stands, for teammates and AI assistants (Copilot, Claude, Cursor, ChatGPT…). No API keys: it’s just a file your tools can read.",
    `<button class="btn" data-action="copy-context">${icon("copy")} Copy</button><button class="btn primary" data-action="save-context">${icon("save")} Save to brain/CONTEXT.md</button>`)}
    <div class="callout" style="margin-bottom:16px">${icon("terminal")}<div>Tell your assistant: <strong>“Read brain/CONTEXT.md before you start.”</strong> Or add that line to <code>AGENTS.md</code>, <code>CLAUDE.md</code> or <code>.github/copilot-instructions.md</code> so it happens every time. Regenerate after big changes; <code>cairn context</code> does the same from the terminal.</div></div>
    <div class="toolbar"><div class="segmented" role="tablist" aria-label="View"><button class="${state.contextMode === "preview" ? "on" : ""}" data-context-mode="preview">Preview</button><button class="${state.contextMode === "raw" ? "on" : ""}" data-context-mode="raw">Markdown</button></div><span class="muted" style="margin-left:auto;font-size:12.5px">${saved} · ${pack.split("\n").length} lines</span></div>
    <section class="card">${body}</section>
    ${mcpCard()}`;
}

// ------------------------------------------------------------ MCP: let an assistant query the brain directly

export const MCP_CLIENTS = [
  ["claude", "Claude Desktop / Cursor"],
  ["vscode", "VS Code / Copilot"],
  ["opencode", "OpenCode"],
  ["claude-code", "Claude Code"],
];

const MCP_FILES = {
  claude: "<code>claude_desktop_config.json</code> or the repo’s <code>.cursor/mcp.json</code>",
  vscode: "<code>.vscode/mcp.json</code>",
  opencode: "<code>opencode.json</code>",
  "claude-code": "a terminal (once per machine)",
};

/** The text to paste into an MCP client, built from the running server's entry (null outside server mode). */
export function mcpSnippet(client = state.mcpClient || "claude", entry = state.info.mcp) {
  if (!entry) return "cairn mcp --print-config";
  const { name, command, args, env } = entry;
  if (client === "vscode") return JSON.stringify({ servers: { [name]: { type: "stdio", command, args, env } } }, null, 2);
  if (client === "opencode") return JSON.stringify({ mcp: { [name]: { type: "local", command: [command, ...args], environment: env, enabled: true } } }, null, 2);
  if (client === "claude-code") {
    const quote = (value) => (/[\s"]/.test(value) ? `"${value.replace(/"/g, '\\"')}"` : value);
    const envFlags = Object.entries(env || {}).map(([key, value]) => `--env ${key}=${quote(value)}`).join(" ");
    return `claude mcp add ${name} ${envFlags} -- ${[command, ...args].map(quote).join(" ")}`.replace(/\s+--\s/, " -- ");
  }
  return JSON.stringify({ mcpServers: { [name]: { command, args, env } } }, null, 2);
}

function mcpCard() {
  const client = state.mcpClient || "claude";
  const entry = state.info.mode === "server" ? state.info.mcp : null;
  const tabs = MCP_CLIENTS.map(([id, label]) => `<button class="${id === client ? "on" : ""}" data-mcp-client="${id}" aria-pressed="${id === client}">${label}</button>`).join("");
  const how = entry
    ? `Paste this into ${MCP_FILES[client]}, then restart the assistant. It can then search, read and update this brain itself.`
    : `Run <code>cairn mcp --print-config</code> in the project folder: it prints this snippet filled in for your machine. (This page can’t see local paths outside <code>cairn serve</code>.)`;
  return `<section class="card mcp-card" style="margin-top:16px;padding:18px 22px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px"><h2 style="margin:0;font-size:16px">Connect your assistant (MCP)</h2>
        <button class="btn" style="margin-left:auto" data-action="copy-mcp">${icon("copy")} Copy</button></div>
      <p class="muted" style="margin:0 0 12px">Better than a file: with <code>cairn mcp</code> running, Claude, Cursor, Copilot or OpenCode can search notes, read backlinks, capture ideas and log decisions as you work. Local, no API keys; add <code>--read-only</code> to keep it look-only.</p>
      ${entry ? `<div class="segmented" role="group" aria-label="Assistant" style="margin-bottom:10px">${tabs}</div>` : ""}
      <p style="margin:0 0 8px;font-size:13px">${how}</p>
      <pre class="pre-box">${esc(mcpSnippet(client, entry))}</pre>
    </section>`;
}

// ------------------------------------------------------------ the cairn: one stone per finished task

function cairnSvg(count) {
  const shown = Math.min(count, 8);
  const tones = ["#c9bfac", "#b7ab95", "#d3cab9", "#a99c84", "#c2b6a1", "#9f927b", "#cdc3b1", "#b2a58e"];
  let y = 156;
  const stones = [];
  for (let i = 0; i < shown; i++) {
    const width = 112 - i * 11;
    const height = 30 - i * 2;
    const cx = 80 + (((i * 37) % 13) - 6);
    const tilt = ((i * 53) % 11) - 5;
    y -= height * 0.8;
    const cy = y.toFixed(1);
    stones.push(
      `<g transform="rotate(${tilt} ${cx} ${cy})">` +
        `<ellipse cx="${cx}" cy="${cy}" rx="${width / 2}" ry="${height / 2}" fill="${tones[i]}" stroke="var(--ink)" stroke-opacity=".28"/>` +
        `<ellipse cx="${cx - width * 0.12}" cy="${(y - height * 0.18).toFixed(1)}" rx="${width * 0.26}" ry="${height * 0.16}" fill="#fff" fill-opacity=".28"/>` +
      `</g>`,
    );
  }
  const nextWidth = 112 - shown * 11;
  const ghost = shown < 8
    ? `<ellipse cx="80" cy="${(y - (30 - shown * 2) * 0.8).toFixed(1)}" rx="${nextWidth / 2}" ry="${(30 - shown * 2) / 2}" fill="none" stroke="var(--accent)" stroke-dasharray="3 4" stroke-opacity=".8"/>`
    : "";
  return `<svg class="cairn-art" viewBox="0 0 160 168" aria-hidden="true"><line x1="6" y1="160" x2="154" y2="160" stroke="var(--line-strong)"/>${stones.join("")}${ghost}</svg>`;
}

// ============================================================ boards: drag and drop

export function wireBoard() {
  let dragging = null;
  $$(".item-card[data-drag]").forEach((card) => {
    card.addEventListener("dragstart", (event) => {
      dragging = card.dataset.drag;
      event.dataTransfer.setData("text/plain", dragging);
      event.dataTransfer.effectAllowed = "move";
      requestAnimationFrame(() => card.classList.add("dragging"));
    });
    card.addEventListener("dragend", () => {
      dragging = null;
      card.classList.remove("dragging");
      $$(".column.drop").forEach((c) => c.classList.remove("drop"));
    });
  });
  $$(".column[data-column]").forEach((column) => {
    // Only a card is dropped on a column: a block dragged by its handle passes over untouched.
    column.addEventListener("dragover", (event) => {
      if (!dragging) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      column.classList.add("drop");
    });
    column.addEventListener("dragleave", (event) => {
      if (!column.contains(event.relatedTarget)) column.classList.remove("drop");
    });
    column.addEventListener("drop", (event) => {
      if (!dragging) return;
      event.preventDefault();
      column.classList.remove("drop");
      const path = event.dataTransfer.getData("text/plain") || dragging;
      if (path) moveNote(path, column.dataset.column);
    });
  });
}

export async function moveNote(path, status) {
  const note = noteByPath(path);
  if (!note || note.status === status) return;
  await saveNote(path, { status });
  if (note.type === "idea") markGuide("moved");
  render();
  if (note.type === "task" && status === "done") {
    // The one celebrated moment: finishing work adds a stone to the cairn on Today.
    const stones = state.notes.filter((n) => n.type === "task" && n.status === "done").length;
    toast(`Done: “${note.title}”. That's ${stones} ${stones === 1 ? "stone" : "stones"} on the cairn.`);
    return;
  }
  toast(`“${note.title}” → ${STATUS_LABEL[status] || status}`);
}

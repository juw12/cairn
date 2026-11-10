// The "/" menu in the note editor, like Notion's: type / at the start of a line (or after a space) and pick
// a block. Everything it inserts is plain Markdown that Obsidian, GitHub and any editor read the same way:
// headings, lists, Obsidian callouts and foldable toggles, tables, code, maths, diagrams, links, embeds,
// dates, and live ```database blocks.

import { escapeHtml as esc } from "./md.js";
import { icon, today } from "./ui.js";

// ¦ marks where the caret lands after inserting.
const CARET = "¦";

const dbBlock = (title, view, extra = "") =>
  `\`\`\`database\ntitle: ${title}\n${extra}view: ${view}\n\`\`\`\n${CARET}`;

const time = () => new Date().toTimeString().slice(0, 5);
const tomorrow = () => new Date(Date.now() + 86400000).toISOString().slice(0, 10);

export const SLASH_COMMANDS = [
  { id: "h1", label: "Heading 1", hint: "Big section title", icon: "note", keys: "title h1 #", text: `# ${CARET}`, block: true },
  { id: "h2", label: "Heading 2", hint: "Medium section title", icon: "note", keys: "subtitle h2 ##", text: `## ${CARET}`, block: true },
  { id: "h3", label: "Heading 3", hint: "Small section title", icon: "note", keys: "h3 ###", text: `### ${CARET}`, block: true },
  { id: "todo", label: "To-do list", hint: "Track tasks with a checkbox", icon: "task", keys: "checkbox task check", text: `- [ ] ${CARET}`, block: true },
  { id: "bullet", label: "Bulleted list", hint: "A simple list", icon: "list", keys: "ul list unordered", text: `- ${CARET}`, block: true },
  { id: "numbered", label: "Numbered list", hint: "A list with numbers", icon: "list", keys: "ol ordered 1.", text: `1. ${CARET}`, block: true },
  { id: "toggle", label: "Toggle", hint: "Folds open and closed (Obsidian callout)", icon: "chevron", keys: "collapse fold details", text: `> [!note]- ${CARET}\n> `, block: true },
  { id: "callout", label: "Callout", hint: "Make a line stand out", icon: "sparkle", keys: "info tip note admonition", text: `> [!tip] ${CARET}\n> `, block: true },
  { id: "warning", label: "Warning", hint: "A warning callout", icon: "zap", keys: "caution danger alert", text: `> [!warning] ${CARET}\n> `, block: true },
  { id: "quote", label: "Quote", hint: "Capture a quote", icon: "chat", keys: "blockquote", text: `> ${CARET}`, block: true },
  { id: "divider", label: "Divider", hint: "Separate sections", icon: "arrow", keys: "hr line rule separator", text: `---\n${CARET}`, block: true },
  { id: "table", label: "Table", hint: "A simple Markdown table", icon: "columns", keys: "grid", text: `| ${CARET} | Column |\n| --- | --- |\n|  |  |`, block: true },
  { id: "database", label: "Database: table", hint: "Live, editable table of notes", icon: "columns", keys: "db notion table view", text: dbBlock("Open tasks", "table", "type: task\nwhere: status != done\ncolumns: status, tags, due\n"), block: true },
  { id: "board", label: "Database: board", hint: "Kanban of notes; drag to change status", icon: "board", keys: "db kanban notion", text: dbBlock("Task board", "board", "type: task\ngroup: status\n"), block: true },
  { id: "gallery", label: "Database: gallery", hint: "Cards with a preview of each note", icon: "canvas", keys: "db cards notion", text: dbBlock("Ideas", "gallery", "type: idea\n"), block: true },
  { id: "calendar", label: "Database: calendar", hint: "Notes on a month grid by date", icon: "journal", keys: "db month dates notion", text: dbBlock("Due this month", "calendar", "where: has due\ndate: due\n"), block: true },
  { id: "code", label: "Code", hint: "A code block", icon: "code", keys: "snippet pre", text: `\`\`\`\n${CARET}\n\`\`\``, block: true },
  { id: "math", label: "Math", hint: "A LaTeX equation", icon: "sparkle", keys: "latex equation formula katex", text: `$$\n${CARET}\n$$`, block: true },
  { id: "diagram", label: "Diagram", hint: "A Mermaid flowchart", icon: "map", keys: "mermaid flowchart chart graph", text: `\`\`\`mermaid\ngraph TD\n  A[${CARET}] --> B\n\`\`\``, block: true },
  { id: "link", label: "Link to note", hint: "[[wikilink]]", icon: "link", keys: "wiki page mention", text: `[[${CARET}` },
  { id: "embed", label: "Embed note", hint: "Show another note inline", icon: "note", keys: "transclude include", text: `![[${CARET}` },
  { id: "tag", label: "Tag", hint: "#tag", icon: "sparkle", keys: "label hashtag", text: `#${CARET}` },
  { id: "today", label: "Today", hint: () => today(), icon: "journal", keys: "date now day", text: () => `[[${today()}]]${CARET}` },
  { id: "tomorrow", label: "Tomorrow", hint: () => tomorrow(), icon: "journal", keys: "date next", text: () => `[[${tomorrow()}]]${CARET}` },
  { id: "time", label: "Time", hint: () => time(), icon: "activity", keys: "clock now", text: () => `${time()} ${CARET}` },
  { id: "highlight", label: "Highlight", hint: "==marked text==", icon: "pencil", keys: "mark", text: `==${CARET}==` },
  { id: "comment", label: "Hidden comment", hint: "%%only visible while editing%%", icon: "lock", keys: "private note", text: `%%${CARET}%%` },
];

// The commands that match what was typed after the slash, best first.
export function slashMatches(query) {
  const q = String(query || "").toLowerCase();
  if (!q) return SLASH_COMMANDS;
  return SLASH_COMMANDS
    .map((command) => {
      const label = command.label.toLowerCase();
      const words = `${label} ${command.keys} ${command.id}`;
      const score = label.startsWith(q) || command.id.startsWith(q) ? 3 : words.split(/\s+/).some((w) => w.startsWith(q)) ? 2 : words.includes(q) ? 1 : 0;
      return { command, score };
    })
    .filter((m) => m.score)
    .sort((a, b) => b.score - a.score)
    .map((m) => m.command);
}

// Where a slash command is being typed: { start (the slash), end (the caret), query } or null.
export function slashQuery(value, caret) {
  const before = value.slice(0, caret);
  const match = /(^|[\s(])\/([\w-]{0,24})$/.exec(before);
  if (!match) return null;
  const start = caret - match[2].length - 1;
  // Not inside a code block: count the fences above the caret.
  if (((before.match(/^```/gm) || []).length) % 2 === 1) return null;
  return { start, end: caret, query: match[2] };
}

// The text that replaces "/query" and where the caret goes, for a command typed on `line` (the text before the slash).
export function slashInsert(command, lineBefore) {
  let text = typeof command.text === "function" ? command.text() : command.text;
  if (!text.includes(CARET)) text += CARET;
  // Blocks start on their own line.
  if (command.block && lineBefore.trim()) text = `\n${text}`;
  const caret = text.indexOf(CARET);
  return { text: text.replace(CARET, ""), caret };
}

// ------------------------------------------------------------ the menu

let menu = null;

export function slashOpen() {
  return Boolean(menu);
}

export function closeSlash() {
  menu?.el.remove();
  menu = null;
}

export function updateSlash(body, caretPoint) {
  const context = body.selectionStart === body.selectionEnd ? slashQuery(body.value, body.selectionStart) : null;
  const items = context ? slashMatches(context.query) : [];
  if (!context || !items.length) return closeSlash();
  if (!menu) {
    const el = document.createElement("div");
    el.className = "link-suggest slash-menu";
    el.setAttribute("role", "listbox");
    el.setAttribute("aria-label", "Insert a block");
    document.body.appendChild(el);
    el.addEventListener("mousedown", (e) => {
      const option = e.target.closest("[data-slash]");
      if (!option) return;
      e.preventDefault();
      menu.active = Number(option.dataset.slash);
      acceptSlash(body);
    });
    menu = { el, active: 0, items: [], context, body };
  }
  if (menu.context?.query !== context.query) menu.active = 0;
  menu.items = items;
  menu.context = context;
  menu.body = body;
  menu.active = Math.min(menu.active, items.length - 1);
  menu.el.innerHTML = items
    .map((item, index) => {
      const hint = typeof item.hint === "function" ? item.hint() : item.hint;
      return `<button type="button" class="suggest-item${index === menu.active ? " on" : ""}" data-slash="${index}" role="option" aria-selected="${index === menu.active}">
        ${icon(item.icon, "sm")}<span class="label">${esc(item.label)}</span><span class="detail">${esc(hint)}</span></button>`;
    })
    .join("") + `<div class="suggest-hint"><kbd>↑</kbd><kbd>↓</kbd> choose · <kbd>Enter</kbd> insert · <kbd>Esc</kbd> close</div>`;
  const point = caretPoint(body, context.start);
  const width = Math.min(340, window.innerWidth - 16);
  const height = Math.min(menu.el.offsetHeight || 320, 340);
  const top = point.top + height > window.innerHeight - 8 ? Math.max(8, point.top - height - 26) : point.top + 4;
  menu.el.style.width = `${width}px`;
  menu.el.style.left = `${Math.max(8, Math.min(point.left - 12, window.innerWidth - width - 8))}px`;
  menu.el.style.top = `${top}px`;
  menu.el.querySelector(".suggest-item.on")?.scrollIntoView({ block: "nearest" });
}

export function handleSlashKey(e, body, caretPoint) {
  if (!menu) return false;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const step = e.key === "ArrowDown" ? 1 : -1;
    menu.active = (menu.active + step + menu.items.length) % menu.items.length;
    updateSlash(body, caretPoint);
    return true;
  }
  if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    acceptSlash(body);
    return true;
  }
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    closeSlash();
    return true;
  }
  return false;
}

function acceptSlash(body) {
  const item = menu?.items[menu.active];
  const context = slashQuery(body.value, body.selectionStart) || menu?.context;
  closeSlash();
  if (!item || !context) return;
  const lineStart = body.value.lastIndexOf("\n", context.start - 1) + 1;
  const { text, caret } = slashInsert(item, body.value.slice(lineStart, context.start));
  body.setRangeText(text, context.start, context.end, "end");
  body.setSelectionRange(context.start + caret, context.start + caret);
  body.focus();
  body.dispatchEvent(new Event("input"));
}

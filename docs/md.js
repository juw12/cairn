// A small, safe Markdown renderer with Obsidian's flavour: callouts, ==highlights==, #tags,
// [[links#headings]], ![[embeds]], images, footnotes, %%comments%%, nested and task lists.
// Text is HTML-escaped before any markup is added, so notes can never inject HTML.

export function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeUrl(url) {
  const trimmed = url.trim();
  return /^(https?:|mailto:|#|\/|\.\/|\.\.\/)/i.test(trimmed) || !/^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? trimmed : "#";
}

const IMAGE_RE = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
const ATTACHMENT_RE = /\.(png|jpe?g|gif|webp|svg|bmp|avif|pdf|mp3|wav|m4a|ogg|mp4|webm|mov|zip|csv|txt|json|canvas|excalidraw)$/i;
const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const FENCE_OPEN = /^\s*(```|~~~)/;
const HEADING_RE = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const FOOTNOTE_DEF = /^\[\^([^\]\s]+)\]:\s?(.*)$/;
const EMBED_LINE = /^\s*!\[\[([^\]\n]+)\]\]\s*$/;

// Obsidian's callout types and their aliases.
const CALLOUTS = {
  note: "note", info: "info", todo: "todo",
  abstract: "abstract", summary: "abstract", tldr: "abstract",
  tip: "tip", hint: "tip", important: "tip",
  success: "success", check: "success", done: "success",
  question: "question", help: "question", faq: "question",
  warning: "warning", caution: "warning", attention: "warning",
  failure: "failure", fail: "failure", missing: "failure",
  danger: "danger", error: "danger", bug: "bug", example: "example",
  quote: "quote", cite: "quote",
};

// The id a heading gets, and what [[note#Heading]] looks for.
export function headingSlug(text) {
  return String(text)
    .toLowerCase()
    .replace(/\[\[([^\]|]*\|)?([^\]]*)\]\]/g, "$2")
    .replace(/[`*_~=[\]()#^]/g, "")
    .trim()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "") || "section";
}

function plainHeading(text) {
  return String(text)
    .replace(/\[\[([^\]|]*\|)?([^\]]*)\]\]/g, "$2")
    .replace(/[`*_~=]/g, "")
    .trim();
}

// [[target#heading|alias]] -> parts. A heading starting with ^ is a block reference.
export function splitLink(raw) {
  const [left, ...aliasParts] = String(raw).replace(/\\\|/g, "|").split("|");
  const alias = aliasParts.join("|").trim();
  const hashAt = left.indexOf("#");
  const target = (hashAt === -1 ? left : left.slice(0, hashAt)).trim();
  const heading = hashAt === -1 ? "" : left.slice(hashAt + 1).trim();
  return { target, heading, alias };
}

export const isImage = (name) => IMAGE_RE.test(String(name).trim());
export const isAttachmentName = (name) => ATTACHMENT_RE.test(String(name).trim());

function safeDecode(text) {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

function sizeAttrs(spec) {
  const size = /^(\d+)(?:x(\d+))?$/.exec(spec.trim());
  if (!size) return "";
  return ` width="${size[1]}"${size[2] ? ` height="${size[2]}"` : ""}`;
}

function wikiHtml(raw, ctx) {
  const { target, heading, alias } = splitLink(raw);
  const block = heading.startsWith("^");
  const shownHeading = heading.replace(/^\^/, "");
  const shown = alias || (target ? (heading ? `${target} › ${shownHeading}` : target) : shownHeading);
  const label = escapeHtml(shown);
  const anchor = heading && !block ? ` data-heading="${escapeHtml(headingSlug(heading))}"` : "";
  if (!target) {
    return heading ? `<a class="wikilink" href="#"${anchor || ' data-heading=""'} data-local="1">${label}</a>` : label;
  }
  const note = ctx.resolve ? ctx.resolve(target) : null;
  if (note) return `<a class="wikilink" href="#" data-open="${escapeHtml(note.path)}"${anchor}>${label}</a>`;
  if (isAttachmentName(target)) return attachmentHtml(target, alias || target);
  return `<span class="wikilink missing" data-missing="${escapeHtml(target)}" title="No note called ${escapeHtml(target)} yet. Click to create it.">${label}</span>`;
}

function attachmentHtml(target, label) {
  return `<a class="attachment" href="#" data-asset-link="${escapeHtml(target)}">${escapeHtml(label)}</a>`;
}

function embedHtml(raw, ctx) {
  const { target, heading, alias } = splitLink(raw);
  if (isImage(target)) {
    const size = sizeAttrs(alias);
    const alt = alias && !size ? alias : target;
    return `<img class="md-img" data-asset="${escapeHtml(target)}" alt="${escapeHtml(alt)}"${size} loading="lazy">`;
  }
  if (isAttachmentName(target)) return attachmentHtml(target, alias || target);
  const html = ctx.embed ? ctx.embed(target, heading, ctx.depth || 0) : null;
  return html ?? wikiHtml(raw, ctx);
}

function imageHtml(alt, url, ctx) {
  const [text, size = ""] = alt.split("|");
  const attrs = sizeAttrs(size);
  const trimmed = url.trim();
  if (/^https?:\/\//i.test(trimmed) || /^data:image\/(png|jpe?g|gif|webp);base64,/i.test(trimmed)) {
    return `<img class="md-img" src="${escapeHtml(trimmed)}" alt="${escapeHtml(text)}"${attrs} loading="lazy" referrerpolicy="no-referrer">`;
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return escapeHtml(text);
  return `<img class="md-img" data-asset="${escapeHtml(safeDecode(trimmed))}" alt="${escapeHtml(text)}"${attrs} loading="lazy">`;
}

function linkHtml(label, url, ctx) {
  const trimmed = url.trim();
  const external = /^[a-z][a-z0-9+.-]*:/i.test(trimmed);
  if (!external && !trimmed.startsWith("#")) {
    // [text](other note.md) is a link to a note, like in Obsidian.
    const target = safeDecode(trimmed.split("#")[0]);
    const note = target && ctx.resolve ? ctx.resolve(target) : null;
    if (note) return `<a class="wikilink" href="#" data-open="${escapeHtml(note.path)}">${escapeHtml(label)}</a>`;
    if (isAttachmentName(target)) return attachmentHtml(target, label);
  }
  return `<a href="${escapeHtml(safeUrl(trimmed))}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`;
}

function inline(text, ctx) {
  const tokens = [];
  const keep = (html) => {
    tokens.push(html);
    return `\u0000${tokens.length - 1}\u0000`;
  };
  let out = String(text).replace(/%%[\s\S]*?%%/g, "");
  out = out.replace(/`([^`]+)`/g, (_, code) => keep(`<code>${escapeHtml(code)}</code>`));
  // $x^2$ like Obsidian: no space just inside the dollars, and "$5 and $6" stays money.
  out = out.replace(/(^|[^\\$\w])\$(?=[^\s$])([^$\n]*?[^\s\\$])\$(?![\d$])/g, (_, before, tex) =>
    before + keep(`<span class="math-inline">${escapeHtml(tex)}</span>`));
  out = out.replace(/(^|[^\\$\w])\$([^\s$\\])\$(?![\d$])/g, (_, before, tex) =>
    before + keep(`<span class="math-inline">${escapeHtml(tex)}</span>`));
  out = out.replace(/!\[\[([^\]\n]+)\]\]/g, (_, raw) => keep(embedHtml(raw, ctx)));
  out = out.replace(/\[\[([^\]\n]+)\]\]/g, (_, raw) => keep(wikiHtml(raw, ctx)));
  out = out.replace(/!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, alt, url) => keep(imageHtml(alt, url, ctx)));
  out = out.replace(/\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, label, url) => keep(linkHtml(label, url, ctx)));
  out = out.replace(/<(https?:\/\/[^>\s]+)>/g, (_, url) => keep(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>`));
  out = out.replace(/\bhttps?:\/\/[^\s<>"'`\u0000]*[^\s<>"'`.,;:!?)\]\u0000]/g, (url) => keep(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>`));
  out = out.replace(/\[\^([^\]\s]+)\]/g, (_, id) => {
    if (!ctx.footnotes.order.includes(id)) ctx.footnotes.order.push(id);
    const number = ctx.footnotes.order.indexOf(id) + 1;
    return keep(`<sup class="fn-ref"><a href="#" data-footnote="${escapeHtml(id)}">${number}</a></sup>`);
  });
  out = escapeHtml(out)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^\p{L}\p{N}_])__([^_]+)__(?![\p{L}\p{N}_])/gu, "$1<strong>$2</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>")
    .replace(/==([^=\n]+)==/g, "<mark>$1</mark>")
    .replace(/(^|\s)#([\p{L}\p{N}_/-]*[\p{L}_/-][\p{L}\p{N}_/-]*)/gu, (_, before, tag) =>
      `${before}<a href="#" class="tag-link" data-tag-link="${tag.toLowerCase()}">#${tag}</a>`)
    .replace(/\n/g, ctx.breaks ? "<br>" : " ");
  return out.replace(/\u0000(\d+)\u0000/g, (_, index) => tokens[Number(index)]);
}

function splitCells(line) {
  const masked = line.trim().replace(/\\\|/g, "\u0001").replace(/^\||\|$/g, "");
  return masked.split("|").map((cell) => cell.trim().replace(/\u0001/g, "\\|"));
}

function table(lines, ctx) {
  const rows = lines.map(splitCells);
  const [head, , ...body] = rows;
  const cells = (row, tag) => row.map((c) => `<${tag}>${inline(c, ctx)}</${tag}>`).join("");
  return `<table><thead><tr>${cells(head, "th")}</tr></thead><tbody>${body
    .map((row) => `<tr>${cells(row, "td")}</tr>`)
    .join("")}</tbody></table>`;
}

function indentWidth(space) {
  return space.replace(/\t/g, "    ").length;
}

function taskHtml(item, ctx) {
  const task = /^\[([^\]])\]\s+([\s\S]*)$/.exec(item.text);
  if (!task) return { cls: "", html: inline(item.text, ctx) };
  const mark = task[1];
  const checked = mark !== " " ? " checked" : "";
  const number = ctx.taskIndex++;
  const disabled = ctx.tasks === false ? " disabled" : "";
  const status = mark !== " " && mark.toLowerCase() !== "x" ? ` data-status="${escapeHtml(mark)}"` : "";
  return {
    cls: ` class="task-item${checked ? " done" : ""}"${status}`,
    html: `<input type="checkbox" data-task="${number}"${checked}${disabled} aria-label="Toggle item"> <span>${inline(task[2], ctx)}</span>`,
  };
}

function renderItems(items, ctx) {
  const base = items[0].indent;
  const ordered = items[0].ordered;
  const start = ordered && items[0].number !== 1 ? ` start="${items[0].number}"` : "";
  const parts = [];
  let index = 0;
  while (index < items.length) {
    const item = items[index++];
    const head = taskHtml(item, ctx); // before the children, so checkboxes count in reading order
    const children = [];
    while (index < items.length && items[index].indent > base) children.push(items[index++]);
    const nested = children.length ? renderItems(children, ctx) : "";
    parts.push(`<li${head.cls}>${head.html}${nested}</li>`);
  }
  const tag = ordered ? "ol" : "ul";
  return `<${tag}${start}>${parts.join("")}</${tag}>`;
}

function renderBlocks(lines, ctx) {
  const html = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const fence = FENCE_OPEN.exec(line);
    if (fence) {
      const marker = fence[1];
      const language = line.trim().slice(3).trim().split(/\s+/)[0] || "";
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith(marker)) code.push(lines[index++]);
      index += 1;
      const cls = language ? ` class="language-${escapeHtml(language)}"` : "";
      html.push(`<pre${language ? ` data-lang="${escapeHtml(language)}"` : ""}><code${cls}>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    if (/^\s*%%/.test(line) && !line.trim().slice(2).includes("%%")) {
      // A %% comment %% block: hidden in reading view, like Obsidian.
      index += 1;
      while (index < lines.length && !lines[index].includes("%%")) index += 1;
      index += 1;
      continue;
    }
    if (/^\s*\$\$/.test(line)) {
      const math = [line.replace(/^\s*\$\$/, "")];
      index += 1;
      if (!/\$\$\s*$/.test(math[0])) {
        while (index < lines.length && !/\$\$\s*$/.test(lines[index])) math.push(lines[index++]);
        if (index < lines.length) math.push(lines[index++].replace(/\$\$\s*$/, ""));
      } else {
        math[0] = math[0].replace(/\$\$\s*$/, "");
      }
      html.push(`<pre class="math"><code>${escapeHtml(math.join("\n").trim())}</code></pre>`);
      continue;
    }
    const heading = HEADING_RE.exec(line);
    if (heading) {
      const level = heading[1].length;
      let id = headingSlug(heading[2]);
      const seen = ctx.slugs.get(id) || 0;
      ctx.slugs.set(id, seen + 1);
      if (seen) id = `${id}-${seen}`;
      html.push(`<h${level} id="${ctx.idPrefix}${escapeHtml(id)}" data-heading-id="${escapeHtml(id)}">${inline(heading[2], ctx)}</h${level}>`);
      index += 1;
      continue;
    }
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      html.push("<hr>");
      index += 1;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || "")) {
      const rows = [];
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) rows.push(lines[index++]);
      html.push(table(rows, ctx));
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      const callout = /^\[!([\w-]+)\]([+-]?)\s*(.*)$/.exec(quote[0] || "");
      if (callout) {
        const raw = callout[1].toLowerCase();
        const kind = CALLOUTS[raw] || "note";
        const title = callout[3] || raw.charAt(0).toUpperCase() + raw.slice(1);
        const inner = renderBlocks(quote.slice(1), ctx);
        const titleHtml = `<span class="callout-icon" aria-hidden="true"></span><span class="callout-title-text">${inline(title, ctx)}</span>`;
        if (callout[2]) {
          html.push(`<details class="callout" data-callout="${kind}" data-type="${escapeHtml(raw)}"${callout[2] === "+" ? " open" : ""}><summary class="callout-title">${titleHtml}</summary><div class="callout-content">${inner}</div></details>`);
        } else {
          html.push(`<div class="callout" data-callout="${kind}" data-type="${escapeHtml(raw)}"><div class="callout-title">${titleHtml}</div>${inner ? `<div class="callout-content">${inner}</div>` : ""}</div>`);
        }
      } else {
        html.push(`<blockquote>${renderBlocks(quote, ctx)}</blockquote>`);
      }
      continue;
    }
    if (LIST_RE.test(line)) {
      const items = [];
      while (index < lines.length) {
        const match = LIST_RE.exec(lines[index]);
        if (match) {
          items.push({ indent: indentWidth(match[1]), ordered: /\d/.test(match[2]), number: parseInt(match[2], 10) || 1, text: match[3] });
          index += 1;
        } else if (items.length && lines[index].trim() && /^\s+/.test(lines[index])) {
          items[items.length - 1].text += `\n${lines[index].trim()}`;
          index += 1;
        } else {
          break;
        }
      }
      html.push(renderItems(items, ctx));
      continue;
    }
    const footnote = FOOTNOTE_DEF.exec(line);
    if (footnote) {
      const text = [footnote[2]];
      index += 1;
      while (index < lines.length && /^\s{2,}\S/.test(lines[index])) text.push(lines[index++].trim());
      ctx.footnotes.defs.set(footnote[1], text.join(" "));
      continue;
    }
    const embed = EMBED_LINE.exec(line);
    if (embed && !isAttachmentName(splitLink(embed[1]).target) && ctx.embed) {
      html.push(embedHtml(embed[1], ctx));
      index += 1;
      continue;
    }
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() !== "" &&
      !/^(#{1,6}\s|\s*```|\s*~~~|\s*>|\s*([-*+]|\d+[.)])\s+|\s*\||\s*%%|\s*\$\$|\[\^[^\]\s]+\]:|\s*(---|\*\*\*|___)\s*$)/.test(lines[index]) &&
      !(paragraph.length && EMBED_LINE.test(lines[index]))
    ) {
      paragraph.push(lines[index++]);
    }
    if (!paragraph.length) paragraph.push(lines[index++]);
    const text = paragraph.join("\n");
    if (text.replace(/%%[\s\S]*?%%/g, "").trim()) html.push(`<p>${inline(text, ctx)}</p>`);
  }
  return html.join("\n");
}

function footnotesHtml(ctx) {
  const ids = [...ctx.footnotes.order, ...[...ctx.footnotes.defs.keys()].filter((id) => !ctx.footnotes.order.includes(id))];
  if (!ids.length) return "";
  const items = ids
    .map((id) => `<li id="${ctx.idPrefix}fn-${escapeHtml(id)}" data-footnote-id="${escapeHtml(id)}">${inline(ctx.footnotes.defs.get(id) ?? "", { ...ctx, footnotes: { order: [], defs: new Map() } })}</li>`)
    .join("");
  return `\n<section class="footnotes"><ol>${items}</ol></section>`;
}

// options: embed(target, heading, depth) -> html | null for ![[Note]] transclusion; depth; tasks:false
// makes checkboxes read-only; breaks:false joins lines like CommonMark; idPrefix for heading ids.
export function renderMarkdown(source, resolve, options = {}) {
  const ctx = {
    resolve,
    embed: options.embed || null,
    depth: options.depth || 0,
    tasks: options.tasks,
    breaks: options.breaks !== false,
    idPrefix: options.idPrefix ?? "h-",
    taskIndex: 0,
    slugs: new Map(),
    footnotes: { order: [], defs: new Map() },
  };
  const lines = String(source).replaceAll("\r\n", "\n").split("\n");
  return renderBlocks(lines, ctx) + footnotesHtml(ctx);
}

// The headings of a note, for the outline: [{ level, text, id, line }]. Ids match the rendered ones.
export function headings(source) {
  const out = [];
  const seen = new Map();
  let fenced = null;
  String(source).replaceAll("\r\n", "\n").split("\n").forEach((line, number) => {
    const fence = FENCE_OPEN.exec(line);
    if (fence) {
      if (!fenced) fenced = fence[1];
      else if (line.trim().startsWith(fenced)) fenced = null;
      return;
    }
    if (fenced) return;
    const match = HEADING_RE.exec(line);
    if (!match) return;
    let id = headingSlug(match[2]);
    const count = seen.get(id) || 0;
    seen.set(id, count + 1);
    if (count) id = `${id}-${count}`;
    out.push({ level: match[1].length, text: plainHeading(match[2]), id, line: number });
  });
  return out;
}

// The part of a note under one heading (up to the next heading of the same or a higher level).
export function sectionOf(source, heading) {
  const wanted = headingSlug(heading);
  const lines = String(source).replaceAll("\r\n", "\n").split("\n");
  const found = headings(source).find((h) => h.id === wanted || headingSlug(h.text) === wanted);
  if (!found) return null;
  let end = lines.length;
  for (const h of headings(source)) {
    if (h.line > found.line && h.level <= found.level) {
      end = h.line;
      break;
    }
  }
  return lines.slice(found.line, end).join("\n");
}

// Words, like Obsidian's status bar: runs of letters/digits, and each CJK character on its own.
export function wordCount(source) {
  const text = String(source).replace(/```[\s\S]*?```/g, " ").replace(/%%[\s\S]*?%%/g, " ");
  const cjk = (text.match(/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/g) || []).length;
  const words = (text.replace(/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/g, " ").match(/[\p{L}\p{N}][\p{L}\p{N}'’_-]*/gu) || []).length;
  return words + cjk;
}

// Inline #tags written in the body (not in code), lower-cased and without the #.
export function inlineTags(source) {
  const text = String(source).replace(/```[\s\S]*?```/g, " ").replace(/`[^`\n]*`/g, " ").replace(/%%[\s\S]*?%%/g, " ");
  const tags = new Set();
  for (const match of text.matchAll(/(^|\s)#([\p{L}\p{N}_/-]*[\p{L}_/-][\p{L}\p{N}_/-]*)/gu)) {
    if (/^#{1,6}\s/.test(match[0].trim() + " ")) continue;
    tags.add(match[2].toLowerCase());
  }
  return [...tags];
}

// Toggle the n-th "- [ ]" checkbox in the source text (same order the renderer numbers them).
export function toggleTask(source, taskNumber) {
  let seen = -1;
  let fenced = null;
  return source
    .split("\n")
    .map((line) => {
      const fence = FENCE_OPEN.exec(line);
      if (fence) {
        if (!fenced) fenced = fence[1];
        else if (line.trim().startsWith(fenced)) fenced = null;
        return line;
      }
      if (fenced) return line;
      const match = /^((?:\s*>)*\s*(?:[-*+]|\d+[.)])\s+)\[([^\]])\](\s.*)$/.exec(line);
      if (!match) return line;
      seen += 1;
      if (seen !== taskNumber) return line;
      return `${match[1]}[${match[2] === " " ? "x" : " "}]${match[3]}`;
    })
    .join("\n");
}

// A small, safe Markdown renderer. All input is HTML-escaped first, so notes can never
// inject markup. Supports the subset people actually write in project notes.

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

function inline(text, resolve) {
  const codes = [];
  let out = escapeHtml(text).replace(/`([^`]+)`/g, (_, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  out = out.replace(/\[\[([^\]]+)\]\]/g, (_, raw) => {
    const [target, alias] = raw.split("|");
    const note = resolve ? resolve(target.replace(/&amp;/g, "&").replace(/&#39;/g, "'")) : null;
    const label = (alias || target).trim();
    return note
      ? `<a class="wikilink" href="#" data-open="${escapeHtml(note.path)}">${label}</a>`
      : `<span class="wikilink missing" title="No note called ${label} yet">${label}</span>`;
  });
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
    return `<a href="${safeUrl(url)}" target="_blank" rel="noopener">${label}</a>`;
  });
  out = out
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>");
  return out.replace(/\u0000(\d+)\u0000/g, (_, index) => `<code>${codes[Number(index)]}</code>`);
}

function table(lines, resolve) {
  const rows = lines.map((line) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
  const [head, , ...body] = rows;
  const cells = (row, tag) => row.map((c) => `<${tag}>${inline(c, resolve)}</${tag}>`).join("");
  return `<table><thead><tr>${cells(head, "th")}</tr></thead><tbody>${body
    .map((row) => `<tr>${cells(row, "td")}</tr>`)
    .join("")}</tbody></table>`;
}

export function renderMarkdown(source, resolve) {
  const lines = String(source).replaceAll("\r\n", "\n").split("\n");
  const html = [];
  let index = 0;
  let taskIndex = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (/^```/.test(line)) {
      const code = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) code.push(lines[index++]);
      index += 1;
      html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      html.push(`<h${level}>${inline(heading[2], resolve)}</h${level}>`);
      index += 1;
      continue;
    }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) {
      html.push("<hr>");
      index += 1;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || "")) {
      const rows = [];
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) rows.push(lines[index++]);
      html.push(table(rows, resolve));
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quote = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^>\s?/, ""));
      html.push(`<blockquote>${inline(quote.join(" "), resolve)}</blockquote>`);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (index < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[index])) {
        const item = lines[index++].replace(/^\s*([-*]|\d+\.)\s+/, "");
        const task = /^\[([ xX])\]\s+(.*)$/.exec(item);
        if (task) {
          const checked = task[1] !== " " ? " checked" : "";
          items.push(
            `<li class="task-item"><input type="checkbox" data-task="${taskIndex++}"${checked} aria-label="Toggle item"> <span>${inline(task[2], resolve)}</span></li>`,
          );
        } else {
          items.push(`<li>${inline(item, resolve)}</li>`);
        }
      }
      const tag = ordered ? "ol" : "ul";
      html.push(`<${tag}>${items.join("")}</${tag}>`);
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
      !/^(#{1,4}\s|```|>|\s*([-*]|\d+\.)\s+|\s*\|)/.test(lines[index])
    ) {
      paragraph.push(lines[index++]);
    }
    if (!paragraph.length) paragraph.push(lines[index++]);
    html.push(`<p>${inline(paragraph.join(" "), resolve)}</p>`);
  }
  return html.join("\n");
}

// Toggle the n-th "- [ ]" checkbox in the source text.
export function toggleTask(source, taskNumber) {
  let seen = -1;
  return source
    .split("\n")
    .map((line) => {
      const match = /^(\s*(?:[-*]|\d+\.)\s+)\[([ xX])\](.*)$/.exec(line);
      if (!match) return line;
      seen += 1;
      if (seen !== taskNumber) return line;
      return `${match[1]}[${match[2] === " " ? "x" : " "}]${match[3]}`;
    })
    .join("\n");
}

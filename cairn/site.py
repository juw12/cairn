"""Build a public page you can host anywhere from the notes marked ``publish: true``.

``cairn publish --site FOLDER`` writes plain HTML: an index of your work (each claim with the links
that back it) and one page per public note. There is no GitHub call and no account: open
``index.html`` from disk, or put the folder on any static host (Cloudflare Pages, Netlify, GitHub
Pages, your own server). ``cairn publish --to OWNER/REPO`` puts the same pages in the public repo's
``docs/`` folder, where GitHub Pages serves them.

The index has a search box over every public note. It is one small script (``search.js``) that
carries the notes' words with it, so it works from disk and asks no server; without JavaScript the
box stays hidden and every page reads the same. With ``--url`` (the address the folder will have)
each page also names its own address and a ``sitemap.xml`` lists them all, which is what Google and
Bing read to find the pages.

Privacy follows publish.py: only ``publish: true`` notes are read, and links to notes that stay
private become plain text. Note text is always escaped, so a note can't add markup or script to the
page. A manifest (``.cairn-site.json``) remembers which files this wrote, so a note you un-publish
loses its page on the next build, and files you added to the folder by hand are never touched.
"""

from __future__ import annotations

import html
import json
import posixpath
import re
import urllib.parse
from pathlib import Path

from . import core, publish

MANIFEST = ".cairn-site.json"
STYLESHEET = "site.css"
SEARCH_SCRIPT = "search.js"
SITEMAP = "sitemap.xml"
ROBOTS = "robots.txt"
NO_JEKYLL = ".nojekyll"  # GitHub Pages then serves the files as they are
SEARCH_TEXT_LIMIT = 20000  # characters of one note the search script carries
HOME_URL = "https://juw12.github.io/p/cairn/"

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_TASK_RE = re.compile(r"^\[([ xX])\]\s+")
_INLINE_RE = re.compile(
    r"(?P<tick>`+)(?P<code>.+?)(?P=tick)"
    r"|(?P<embed>!?)\[\[(?P<wiki>[^\]\n]+)\]\]"
    r"|(?P<image>!?)\[(?P<label>[^\]\n]*)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"\n]*\")?\)"
    r"|(?P<url>https?://[^\s<>\[\]\"']+)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|(?<![\w*])\*(?!\s)(?P<italic>[^*\n]+?)(?<!\s)\*(?![\w*])"
)


def page_path(note_path: str) -> str:
    return note_path[:-3] + ".html"


def _href(from_page: str, to_file: str) -> str:
    """A relative link, so the site works from disk and from any sub-folder of a host."""
    relative = posixpath.relpath(to_file, posixpath.dirname(from_page) or ".")
    return urllib.parse.quote(relative)


def _web_url(url: str) -> str:
    """Only links that open a web page or a mail window are kept as links."""
    return url if re.match(r"(?i)(https?://|mailto:)", url) else ""


# ------------------------------------------------------------ Markdown (the subset notes use)

def render_inline(text: str, link) -> str:
    """`link(target)` returns the href of a public note for a [[wikilink]], or "" to leave plain text."""
    parts: list[str] = []
    position = 0
    for match in _INLINE_RE.finditer(text):
        parts.append(html.escape(text[position:match.start()]))
        position = match.end()
        if match["code"] is not None:
            parts.append(f"<code>{html.escape(match['code'].strip())}</code>")
        elif match["wiki"] is not None:
            target, _, alias = match["wiki"].partition("|")
            label = alias.strip() or target.split("#")[0].strip().split("/")[-1].removesuffix(".md")
            href = "" if match["embed"] else link(target.split("#")[0])
            parts.append(f'<a href="{html.escape(href)}">{html.escape(label)}</a>' if href else html.escape(label))
        elif match["href"] is not None:
            url, label = _web_url(match["href"]), match["label"]
            if match["image"]:
                image = f'<img src="{html.escape(url)}" alt="{html.escape(label)}" loading="lazy">'
                parts.append(image if url.lower().startswith("http") else html.escape(label))
            elif url:
                parts.append(f'<a href="{html.escape(url)}">{render_inline(label, link)}</a>')
            else:
                parts.append(render_inline(label, link))
        elif match["url"] is not None:
            url = match["url"].rstrip(".,;:!?)")
            position -= len(match["url"]) - len(url)
            parts.append(f'<a href="{html.escape(url)}">{html.escape(url)}</a>')
        elif match["bold"] is not None:
            parts.append(f"<strong>{render_inline(match['bold'], link)}</strong>")
        else:
            parts.append(f"<em>{render_inline(match['italic'], link)}</em>")
    parts.append(html.escape(text[position:]))
    return "".join(parts)


def _list_html(items: list[tuple[int, bool, str]], link) -> str:
    out: list[str] = []
    open_lists: list[tuple[int, str]] = []
    for indent, ordered, text in items:
        while open_lists and indent < open_lists[-1][0]:
            out.append(f"</li></{open_lists.pop()[1]}>")
        if open_lists and indent == open_lists[-1][0]:
            out.append("</li>")
        else:
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>")
            open_lists.append((indent, tag))
        task = _TASK_RE.match(text)
        box = ("&#9745; " if task[1].strip() else "&#9744; ") if task else ""
        out.append(f"<li>{box}{render_inline(text[task.end():] if task else text, link)}")
    while open_lists:
        out.append(f"</li></{open_lists.pop()[1]}>")
    return "".join(out)


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _starts_block(line: str) -> bool:
    return bool(
        _FENCE_RE.match(line) or _HEADING_RE.match(line) or _RULE_RE.match(line)
        or _ITEM_RE.match(line) or line.lstrip().startswith(">")
    )


def render_markdown(body: str, link=lambda target: "") -> str:
    lines = body.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
        elif _FENCE_RE.match(line):
            fence = _FENCE_RE.match(line)[1]
            end = index + 1
            while end < len(lines) and not lines[end].strip().startswith(fence):
                end += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(lines[index + 1:end]))}</code></pre>")
            index = end + 1
        elif _HEADING_RE.match(line):
            marks, text = _HEADING_RE.match(line).groups()
            out.append(f"<h{len(marks)}>{render_inline(text, link)}</h{len(marks)}>")
            index += 1
        elif _RULE_RE.match(line):
            out.append("<hr>")
            index += 1
        elif line.lstrip().startswith(">"):
            quoted = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            out.append(f"<blockquote>{render_markdown(chr(10).join(quoted), link)}</blockquote>")
        elif _ITEM_RE.match(line):
            items: list[tuple[int, bool, str]] = []
            while index < len(lines) and lines[index].strip():
                item = _ITEM_RE.match(lines[index])
                if item:
                    items.append((len(item[1].expandtabs(4)), item[2][0].isdigit(), item[3]))
                elif lines[index].startswith((" ", "\t")):
                    indent, ordered, text = items[-1]
                    items[-1] = (indent, ordered, f"{text} {lines[index].strip()}")
                else:
                    break
                index += 1
            out.append(_list_html(items, link))
        elif "|" in line and index + 1 < len(lines) and _TABLE_RULE_RE.match(lines[index + 1]):
            head = "".join(f"<th>{render_inline(cell, link)}</th>" for cell in _cells(line))
            index += 2
            rows = []
            while index < len(lines) and "|" in lines[index]:
                rows.append("<tr>" + "".join(f"<td>{render_inline(cell, link)}</td>" for cell in _cells(lines[index])) + "</tr>")
                index += 1
            out.append(f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
        else:
            paragraph = [line.strip()]
            index += 1
            while index < len(lines) and lines[index].strip() and not _starts_block(lines[index]):
                paragraph.append(lines[index].strip())
                index += 1
            out.append(f"<p>{render_inline(' '.join(paragraph), link)}</p>")
    return "\n".join(out)


# ------------------------------------------------------------ plain words (search, descriptions)

_PLAIN_LINK_RE = re.compile(
    r"!\[\[[^\]\n]+\]\]"
    r"|\[\[(?P<wiki>[^\]\n]+)\]\]"
    r"|!\[[^\]\n]*\]\([^)\s]+(?:\s+\"[^\"\n]*\")?\)"
    r"|\[(?P<label>[^\]\n]*)\]\([^)\s]+(?:\s+\"[^\"\n]*\")?\)"
)
_LINE_MARK_RE = re.compile(r"^\s*(?:>\s?)*(?:#{1,6}\s+|(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)?")


def plain_text(body: str) -> str:
    """The words of a note without its Markdown marks, on one line."""

    def label(match: re.Match) -> str:
        if match["wiki"] is not None:
            target, _, alias = match["wiki"].partition("|")
            return alias.strip() or target.split("#")[0].strip().split("/")[-1].removesuffix(".md")
        return match["label"] or ""

    words: list[str] = []
    for line in body.replace("\r\n", "\n").split("\n"):
        if _FENCE_RE.match(line) or _RULE_RE.match(line) or _TABLE_RULE_RE.match(line):
            continue
        line = _PLAIN_LINK_RE.sub(label, _LINE_MARK_RE.sub("", line))
        words.append(re.sub(r"\*\*|`+", "", line).replace("|", " "))
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.-") + "\u2026"


def _body(note: dict) -> str:
    return re.sub(r"\A\s*#\s+.+\n?", "", note["body"], count=1)  # the title is already the page heading


def _summary(note: dict) -> str:
    """One or two lines that say what the note is: its claim, or how it starts."""
    return _shorten(_text(note["meta"].get("claim")) or plain_text(_body(note)), 160)


# ------------------------------------------------------------ search (one script, no server)

SEARCH_JS = """\
// Search for this page. The notes' words are in this file, so it works from disk and asks no server.
(function () {
  "use strict";
  var notes = __NOTES__;
  var MOST = 50;
  var form = document.getElementById("search");
  var box = document.getElementById("q");
  var count = document.getElementById("search-count");
  var results = document.getElementById("search-results");
  var listing = document.getElementById("listing");
  if (!form || !box || !count || !results || !listing) return;

  // Lower case and without accents, so "cafe" finds the same word written with an accent.
  function fold(text) {
    text = String(text).toLowerCase();
    return text.normalize ? text.normalize("NFD").replace(/[\\u0300-\\u036f]/g, "") : text;
  }

  notes.forEach(function (note) {
    note.title = fold(note.t);
    note.tags = fold(note.g.join(" "));
    note.text = fold(note.x);
  });

  function times(text, word) {
    var found = 0;
    var at = text.indexOf(word);
    while (at >= 0 && found < 5) {
      found += 1;
      at = text.indexOf(word, at + word.length);
    }
    return found;
  }

  // Every word must be somewhere in the note. A word in the title counts most, then a tag, then the text.
  function score(note, words) {
    var total = 0;
    for (var i = 0; i < words.length; i += 1) {
      var inTitle = note.title.indexOf(words[i]) >= 0;
      var inTags = note.tags.indexOf(words[i]) >= 0;
      var inText = times(note.text, words[i]);
      if (!inTitle && !inTags && !inText) return 0;
      total += (inTitle ? 20 : 0) + (inTags ? 8 : 0) + inText;
    }
    return total + (note.title === words.join(" ") ? 100 : 0);
  }

  function snippet(note, words) {
    var lower = note.x.toLowerCase();
    var at = -1;
    for (var i = 0; i < words.length && at < 0; i += 1) at = lower.indexOf(words[i]);
    var start = Math.max(0, at - 70);
    if (start > 0) {
      var space = note.x.indexOf(" ", start);
      if (space >= 0 && space < at) start = space + 1;
    }
    var text = note.x.slice(start, start + 220);
    var more = start + 220 < note.x.length;
    if (more && text.lastIndexOf(" ") > 120) text = text.slice(0, text.lastIndexOf(" "));
    return (start > 0 ? "\\u2026 " : "") + text + (more ? " \\u2026" : "");
  }

  // Text goes in as text nodes, never as markup, so a note cannot add anything to the page.
  function marked(text, words) {
    var parent = document.createElement("p");
    var escaped = words.map(function (word) { return word.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&"); });
    var parts = text.split(new RegExp("(" + escaped.join("|") + ")", "ig"));
    parts.forEach(function (part, index) {
      if (!part) return;
      if (index % 2) {
        var mark = document.createElement("mark");
        mark.textContent = part;
        parent.appendChild(mark);
      } else {
        parent.appendChild(document.createTextNode(part));
      }
    });
    return parent;
  }

  function card(note, words) {
    var section = document.createElement("section");
    section.className = "card";
    var details = [note.d, note.g.slice(0, 4).join(", ")].filter(Boolean).join(" \\u00b7 ");
    if (details) {
      var meta = document.createElement("p");
      meta.className = "meta";
      meta.textContent = details;
      section.appendChild(meta);
    }
    var heading = document.createElement("h3");
    var link = document.createElement("a");
    link.href = note.u;
    link.textContent = note.t;
    heading.appendChild(link);
    section.appendChild(heading);
    if (note.x) section.appendChild(marked(snippet(note, words), words));
    return section;
  }

  function remember(query) {
    try {
      history.replaceState(null, "", query ? "?q=" + encodeURIComponent(query) : location.pathname);
    } catch (error) { /* opened from disk in a browser that refuses: the search still works */ }
  }

  function show(query) {
    var words = fold(query).split(/\\s+/).filter(Boolean);
    while (results.firstChild) results.removeChild(results.firstChild);
    var searching = words.length > 0;
    results.hidden = !searching;
    count.hidden = !searching;
    listing.hidden = searching;
    remember(searching ? query.trim() : "");
    if (!searching) return;
    var hits = [];
    notes.forEach(function (note) {
      var points = score(note, words);
      if (points) hits.push({ note: note, points: points });
    });
    hits.sort(function (a, b) {
      return b.points - a.points || (a.note.d < b.note.d ? 1 : a.note.d > b.note.d ? -1 : 0);
    });
    var asked = "\\u201c" + query.trim() + "\\u201d";
    if (!hits.length) {
      count.textContent = "Nothing found for " + asked + ". Try fewer or other words.";
    } else {
      count.textContent = hits.length + (hits.length === 1 ? " note" : " notes") + " for " + asked
        + (hits.length > MOST ? ", the first " + MOST + " shown. Add a word to narrow it down." : "");
    }
    hits.slice(0, MOST).forEach(function (hit) { results.appendChild(card(hit.note, words)); });
  }

  form.hidden = false;
  form.addEventListener("submit", function (event) { event.preventDefault(); });
  box.addEventListener("input", function () { show(box.value); });
  document.addEventListener("keydown", function (event) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName || "");
    if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      box.focus();
    } else if (event.key === "Escape" && event.target === box && box.value) {
      box.value = "";
      show("");
    }
  });
  var asked = /[?&]q=([^&]*)/.exec(location.search);
  if (asked) {
    try {
      box.value = decodeURIComponent(asked[1].replace(/\\+/g, " "));
    } catch (error) { box.value = ""; }
    if (box.value) show(box.value);
  }
})();
"""


def _search_script(notes: list[dict]) -> str:
    entries = [
        {
            "t": note["title"],
            "u": _href("index.html", page_path(note["path"])),
            "d": _when(note),
            "g": [str(tag) for tag in note["tags"]],
            "x": " ".join(part for part in (_text(note["meta"].get("claim")), plain_text(_body(note))) if part)[:SEARCH_TEXT_LIMIT],
        }
        for note in sorted(notes, key=lambda note: note["path"])
    ]
    # ensure_ascii keeps the file plain ASCII, which is also valid JavaScript whatever the notes hold.
    return SEARCH_JS.replace("__NOTES__", json.dumps(entries, ensure_ascii=True, separators=(",", ":")))


# ------------------------------------------------------------ addresses (canonical links, sitemap)

def base_url(url: str) -> str:
    """`url` as the address of the site's folder, ending in a slash. "" when no address was given."""
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"(?i)https?://[^/\s]+", url) or re.search(r"[\s<>\"']", url):
        raise publish.PublishError("--url needs the full web address the page will have, e.g. https://you.github.io/brain/")
    return url.split("#")[0].split("?")[0].rstrip("/") + "/"


def _structured(data: dict) -> str:
    """`data` as the JSON-LD block search engines read. "<" is written as \\u003c, so no text can end the tag."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{text}</script>'


def _day(note: dict) -> str:
    when = _when(note)
    return when if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else ""


def _address(base: str, page: str) -> str:
    return base + ("" if page == "index.html" else urllib.parse.quote(page)) if base else ""


def _sitemap(notes: list[dict], base: str) -> str:
    rows = [(base, max((_day(note) for note in notes), default=""))]
    rows += [(_address(base, page_path(note["path"])), _day(note)) for note in sorted(notes, key=lambda note: note["path"])]
    urls = "".join(
        f"  <url><loc>{html.escape(loc)}</loc>{f'<lastmod>{when}</lastmod>' if when else ''}</url>\n" for loc, when in rows
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n'


# ------------------------------------------------------------ pages

CSS = """\
:root { --bg: #f4f1ea; --surface: #fffdf8; --line: #e1dbcf; --ink: #1b1a17; --ink-2: #4a463e;
  --muted: #6c665d; --accent: #a8340f; --sunken: #ebe6db; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #151412; --surface: #1c1b18; --line: #2e2c28; --ink: #efebe3; --ink-2: #c8c2b6;
    --muted: #968f83; --accent: #ff9a76; --sunken: #191816; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); line-height: 1.6;
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 48px 20px 32px; }
h1, h2, h3 { font-family: "Instrument Serif", "Iowan Old Style", Georgia, serif; font-weight: 400; line-height: 1.2; }
h1 { font-size: 2.6rem; margin: 0 0 8px; }
h2 { font-size: 1.7rem; margin: 40px 0 12px; }
h3 { font-size: 1.3rem; margin: 28px 0 8px; }
a { color: var(--accent); text-underline-offset: 3px; overflow-wrap: anywhere; }
.lead, .meta, footer { color: var(--muted); }
.meta { font-size: 0.875rem; margin: 0 0 4px; }
.back { display: inline-block; margin-bottom: 24px; font-size: 0.875rem; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 20px; margin: 0 0 14px; }
.card h3 { margin: 0 0 6px; }
.card p { margin: 0 0 10px; color: var(--ink-2); }
.card ul, .plain { list-style: none; margin: 0; padding: 0; font-size: 0.875rem; }
.plain li { padding: 6px 0; border-bottom: 1px solid var(--line); }
.claim { background: var(--surface); border-left: 3px solid var(--accent); padding: 14px 18px; margin: 20px 0; color: var(--ink-2); }
.claim ul { margin: 8px 0 0; padding-left: 18px; font-size: 0.875rem; }
.tags span { display: inline-block; background: var(--sunken); border-radius: 99px; padding: 1px 10px; margin: 0 4px 4px 0; font-size: 0.8rem; color: var(--ink-2); }
code { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 0.9em; background: var(--sunken); padding: 1px 5px; border-radius: 4px; }
pre { background: var(--sunken); padding: 14px; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { margin: 16px 0; padding: 2px 16px; border-left: 3px solid var(--line); color: var(--ink-2); }
img { max-width: 100%; height: auto; border-radius: 8px; }
.table { overflow-x: auto; }
table { border-collapse: collapse; font-size: 0.9rem; }
th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; vertical-align: top; }
hr { border: 0; border-top: 1px solid var(--line); margin: 28px 0; }
.search { margin: 20px 0 8px; }
.search label { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.search input { width: 100%; font: inherit; color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.search input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
#search-count { margin: 0 0 14px; }
#search-results .card p { overflow-wrap: anywhere; }
mark { background: color-mix(in srgb, var(--accent) 22%, transparent); color: inherit; border-radius: 3px; padding: 0 2px; }
[hidden] { display: none !important; }
footer { max-width: 760px; margin: 0 auto; padding: 0 20px 48px; font-size: 0.8rem; }
"""


def _page(title: str, content: str, css_href: str, description: str = "", address: str = "", kind: str = "article", head: str = "") -> str:
    """`description` is what a search result shows under the title; `address` is the page's own web address."""
    extra = [
        f'<meta name="description" content="{html.escape(description)}">' if description else "",
        f'<link rel="canonical" href="{html.escape(address)}">' if address else "",
        f'<meta property="og:type" content="{kind}">',
        f'<meta property="og:title" content="{html.escape(title)}">',
        f'<meta property="og:description" content="{html.escape(description)}">' if description else "",
        f'<meta property="og:url" content="{html.escape(address)}">' if address else "",
        '<meta name="twitter:card" content="summary">',
        head,
    ]
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="generator" content="Cairn">\n'
        f"<title>{html.escape(title)}</title>\n"
        + "".join(f"{line}\n" for line in extra if line)
        + f'<link rel="stylesheet" href="{css_href}">\n</head>\n<body>\n<main>\n{content}\n</main>\n'
        f'<footer>Made with <a href="{HOME_URL}">Cairn</a> from notes marked <code>publish: true</code>. '
        "Everything else stays private.</footer>\n</body>\n</html>\n"
    )


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _evidence(meta: dict) -> list[str]:
    value = meta.get("evidence")
    urls = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    return [url.strip() for url in urls if isinstance(url, str) and url.strip().lower().startswith("http")]


def _evidence_html(urls: list[str]) -> str:
    items = "".join(
        f'<li><a href="{html.escape(url)}">{html.escape(re.sub(r"^https?://", "", url).rstrip("/"))}</a></li>'
        for url in urls
    )
    return f"<ul>{items}</ul>" if items else ""


def _when(note: dict) -> str:
    meta = note["meta"]
    return str(meta.get("updated") or meta.get("date") or meta.get("created") or "")[:10]


def _note_page(note: dict, site_title: str, link, base: str = "") -> str:
    meta, path = note["meta"], page_path(note["path"])
    body = _body(note)
    details = " · ".join(html.escape(part) for part in (_text(meta.get("role")), _when(note)) if part)
    tags = "".join(f"<span>{html.escape(str(tag))}</span>" for tag in note["tags"])
    claim = _text(meta.get("claim"))
    content = [
        f'<a class="back" href="{_href(path, "index.html")}">&larr; {html.escape(site_title)}</a>',
        f"<h1>{html.escape(note['title'])}</h1>",
        f'<p class="meta">{details}</p>' if details else "",
        f'<p class="tags">{tags}</p>' if tags else "",
        f'<div class="claim">{html.escape(claim)}{_evidence_html(_evidence(meta))}</div>' if claim else "",
        render_markdown(body, link),
    ]
    # A search result shows this title. "titip" alone says nothing to someone who has never heard of it, so a
    # note with a claim is titled by the claim ("Built titip: Outlook inbox from a paired phone").
    headline = _shorten(claim, 70) if claim else note["title"]
    address = _address(base, path)
    about = {
        "@context": "https://schema.org", "@type": "Article",
        "headline": _shorten(headline, 110), "name": note["title"], "description": _summary(note),
        "author": {"@type": "Person", "name": site_title},
    }
    if _day(note):
        about["dateModified"] = _day(note)
    if note["tags"]:
        about["keywords"] = ", ".join(str(tag) for tag in note["tags"])
    if address:
        about["url"] = about["mainEntityOfPage"] = address
    if _evidence(meta):
        about["citation"] = _evidence(meta)
    return _page(
        f"{headline} · {site_title}", "\n".join(part for part in content if part), _href(path, STYLESHEET),
        description=_summary(note), address=address, head=_structured(about),
    )


def _index_page(notes: list[dict], site_title: str, base: str = "") -> str:
    newest_first = sorted(notes, key=lambda note: (_when(note), note["title"].lower()), reverse=True)
    work = [note for note in newest_first if _text(note["meta"].get("claim"))]
    other = [note for note in newest_first if not _text(note["meta"].get("claim"))]
    content = [f"<h1>{html.escape(site_title)}</h1>"]
    pieces = f'{len(work)} piece{"s" if len(work) != 1 else ""} of work'
    if work:
        content.append(f'<p class="lead">{pieces}, each with links to the evidence.</p>')
    elif not other:
        content.append('<p class="lead">Nothing is published yet.</p>')
    if notes:
        # Hidden until search.js runs, so a browser without JavaScript shows no dead box.
        ask = f'Search {len(notes)} note{"s" if len(notes) != 1 else ""}'
        content.append(
            '<form class="search" id="search" role="search" hidden>'
            f'<label for="q">{ask}</label>'
            f'<input id="q" name="q" type="search" placeholder="{ask}" autocomplete="off" spellcheck="false">'
            "</form>\n"
            '<p class="meta" id="search-count" role="status" hidden></p>\n'
            '<div id="search-results" hidden></div>'
        )
    content.append('<div id="listing">')
    for note in work:
        href = _href("index.html", page_path(note["path"]))
        details = " · ".join(html.escape(part) for part in (_text(note["meta"].get("role")), _when(note)) if part)
        content.append(
            f'<section class="card"><p class="meta">{details}</p>'
            f'<h3><a href="{href}">{html.escape(note["title"])}</a></h3>'
            f"<p>{html.escape(_text(note['meta'].get('claim')))}</p>{_evidence_html(_evidence(note['meta']))}</section>"
        )
    if other:
        rows = "".join(
            f'<li><a href="{_href("index.html", page_path(note["path"]))}">{html.escape(note["title"])}</a>'
            f' <span class="meta">{html.escape(_when(note))}</span></li>'
            for note in other
        )
        content.append(f'<h2>Notes</h2>\n<ul class="plain">{rows}</ul>')
    content.append("</div>")
    count = f'{len(notes)} public note{"s" if len(notes) != 1 else ""}'
    description = f"{site_title}: {pieces if work else count}, each on its own page. Search them all."
    address = _address(base, "index.html")
    listed = {
        "@context": "https://schema.org", "@type": "CollectionPage", "name": site_title,
        "author": {"@type": "Person", "name": site_title},
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(notes),
            "itemListElement": [
                {
                    "@type": "ListItem", "position": position, "name": note["title"],
                    **({"url": _address(base, page_path(note["path"]))} if base else {}),
                }
                for position, note in enumerate(work + other, 1)
            ],
        },
    }
    if notes:
        listed["description"] = description
    if address:
        listed["url"] = address
    head = [_structured(listed)] + ([f'<script src="{SEARCH_SCRIPT}" defer></script>'] if notes else [])
    # The heading stays the name; the title a search result shows also says what is on the page.
    title = f"{site_title} · public brain: {pieces + ' with evidence' if work else count}" if notes else site_title
    return _page(
        title, "\n".join(content), STYLESHEET,
        description=description if notes else "", address=address, kind="website", head="\n".join(head),
    )


def files(public: dict[str, str], title: str = "", url: str = "") -> dict[str, str]:
    """Every file of the site as {path: text}. `public` is {note path: text}, private links already removed
    (publish.unlink_private). `url` is the address the folder will be served at, when that is known."""
    site_title = title.strip() or "Public brain"
    base = base_url(url)
    notes = []
    for path, text in public.items():
        note = core.note_from_file(path, text)
        parsed = core.parse_note(text)
        notes.append({**note, "meta": parsed["meta"], "body": parsed["body"]})
    index = core.link_index(notes)

    pages = {STYLESHEET: CSS, NO_JEKYLL: "Tells GitHub Pages to serve these files as they are.\n", "index.html": _index_page(notes, site_title, base)}
    if notes:
        pages[SEARCH_SCRIPT] = _search_script(notes)
    if base:
        pages[SITEMAP] = _sitemap(notes, base)
        pages[ROBOTS] = f"User-agent: *\nAllow: /\n\nSitemap: {base}{SITEMAP}\n"  # read by crawlers when the folder is a site's root
    for note in notes:
        here = page_path(note["path"])

        def link(target: str, here: str = here, source_note: str = note["path"]) -> str:
            found = core.resolve_target(index, target.strip(), source_note)
            return _href(here, page_path(found)) if found else ""

        pages.setdefault(here, _note_page(note, site_title, link, base))  # a note never replaces the index
    return pages


def manifest_text(paths) -> str:
    return json.dumps({"builtBy": "cairn", "files": sorted(paths)}, indent=2) + "\n"


def build(source: Path, destination: Path, title: str = "", url: str = "") -> dict:
    """Write the site for `source` into `destination`. Returns {"write", "delete", "notes"}."""
    if destination.resolve() == source.resolve():
        raise publish.PublishError("Pick a separate folder for the site, not the brain itself.")
    public = publish.unlink_private(publish.select_public(source))
    pages = files(public, title, url)

    manifest_path = destination / MANIFEST
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])
    except (OSError, ValueError):
        previous = []
    written, removed = [], []
    for relative, text in pages.items():
        target = destination / relative
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            written.append(relative)
    for relative in previous:
        if relative not in pages and (destination / relative).is_file():
            (destination / relative).unlink()
            removed.append(relative)
    manifest_path.write_text(manifest_text(pages), encoding="utf-8", newline="\n")
    return {"write": sorted(written), "delete": sorted(removed), "notes": len(public)}

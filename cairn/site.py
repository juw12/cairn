"""Build a public page you can host anywhere from the notes marked ``publish: true``.

``cairn publish --site FOLDER`` writes plain HTML: an index of your work (each claim with the links
that back it) and one page per public note. There is no JavaScript, no GitHub call and no account:
open ``index.html`` from disk, or put the folder on any static host (Cloudflare Pages, Netlify,
GitHub Pages, your own server).

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
footer { max-width: 760px; margin: 0 auto; padding: 0 20px 48px; font-size: 0.8rem; }
"""


def _page(title: str, content: str, css_href: str) -> str:
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="generator" content="Cairn">\n'
        f"<title>{html.escape(title)}</title>\n"
        f'<link rel="stylesheet" href="{css_href}">\n</head>\n<body>\n<main>\n{content}\n</main>\n'
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


def _note_page(note: dict, site_title: str, link) -> str:
    meta, path = note["meta"], page_path(note["path"])
    body = re.sub(r"\A\s*#\s+.+\n?", "", note["body"], count=1)  # the title is already the page heading
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
    return _page(f"{note['title']} · {site_title}", "\n".join(part for part in content if part), _href(path, STYLESHEET))


def _index_page(notes: list[dict], site_title: str) -> str:
    newest_first = sorted(notes, key=lambda note: (_when(note), note["title"].lower()), reverse=True)
    work = [note for note in newest_first if _text(note["meta"].get("claim"))]
    other = [note for note in newest_first if not _text(note["meta"].get("claim"))]
    content = [f"<h1>{html.escape(site_title)}</h1>"]
    if work:
        content.append(f'<p class="lead">{len(work)} piece{"s" if len(work) != 1 else ""} of work, each with links to the evidence.</p>')
    elif not other:
        content.append('<p class="lead">Nothing is published yet.</p>')
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
    return _page(site_title, "\n".join(content), STYLESHEET)


def build(source: Path, destination: Path, title: str = "") -> dict:
    """Write the site for `source` into `destination`. Returns {"write", "delete", "notes"}."""
    if destination.resolve() == source.resolve():
        raise publish.PublishError("Pick a separate folder for the site, not the brain itself.")
    site_title = title.strip() or "Public brain"
    public = publish.unlink_private(publish.select_public(source))
    notes = []
    for path, text in public.items():
        note = core.note_from_file(path, text)
        parsed = core.parse_note(text)
        notes.append({**note, "meta": parsed["meta"], "body": parsed["body"]})
    index = core.link_index(notes)

    files = {STYLESHEET: CSS, "index.html": _index_page(notes, site_title)}
    for note in notes:
        here = page_path(note["path"])

        def link(target: str, here: str = here, source_note: str = note["path"]) -> str:
            found = core.resolve_target(index, target.strip(), source_note)
            return _href(here, page_path(found)) if found else ""

        files[here] = _note_page(note, site_title, link)

    manifest_path = destination / MANIFEST
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])
    except (OSError, ValueError):
        previous = []
    written, removed = [], []
    for relative, text in files.items():
        target = destination / relative
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            written.append(relative)
    for relative in previous:
        if relative not in files and (destination / relative).is_file():
            (destination / relative).unlink()
            removed.append(relative)
    manifest_path.write_text(json.dumps({"builtBy": "cairn", "files": sorted(files)}, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"write": sorted(written), "delete": sorted(removed), "notes": len(notes)}

"""Pure functions shared (and parity-tested) with docs/core.js.

Nothing in here touches the disk, so the same rules hold for the local server,
the CLI and the browser-only app.
"""

from __future__ import annotations

import re
import unicodedata

TYPES = ("idea", "task", "decision", "note", "journal")

FOLDERS = {
    "idea": "ideas",
    "task": "tasks",
    "decision": "decisions",
    "note": "notes",
    "journal": "journal",
}

STATUSES = {
    "idea": ["raw", "exploring", "ready", "parked"],
    "task": ["todo", "doing", "done"],
    "decision": ["proposed", "accepted", "superseded"],
    "note": [],
    "journal": [],
}

TEMPLATES = {
    "idea": "## Why\n\n\n## What if\n\n",
    "task": "",
    "decision": "## Context\n\n\n## Options\n\n\n## Decision\n\n\n## Consequences\n\n",
    "note": "",
    "journal": "## Log\n\n- \n",
}

CAPTURE_PREFIXES = {
    "idea": "idea",
    "todo": "task",
    "task": "task",
    "decision": "decision",
    "decide": "decision",
    "note": "note",
    "journal": "journal",
    "log": "journal",
}

SPECIAL_FILES = ("CONTEXT.md", "README.md")

_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
_TAG_RE = re.compile(r"(^|\s)#([A-Za-z0-9][A-Za-z0-9_/-]*)")
_LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
_TODO_RE = re.compile(
    r"(?:#|//|/\*|<!--|--|;|\*)\s*(TODO|FIXME|HACK|XXX)\b(?:\([^)]*\))?[\s:\-]*(.*)$"
)
_CAPTURE_RE = re.compile(r"^([A-Za-z]+):\s*(.*)$", re.S)


# ---------------------------------------------------------------- frontmatter


def _parse_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace('\\"', '"') if value[0] == '"' else inner
    return re.sub(r"\s+#.*$", "", value).strip()


def _split_list(inner: str) -> list[str]:
    """Split `a, "b, c", d` on commas that are outside quotes."""
    parts, current, quote = [], "", ""
    for index, ch in enumerate(inner):
        if quote:
            current += ch
            if ch == quote and inner[index - 1] != "\\":
                quote = ""
        elif ch in "\"'":
            quote = ch
            current += ch
        elif ch == ",":
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def _parse_list(inner: str) -> list[str]:
    items = [_parse_scalar(part) for part in _split_list(inner)]
    return [item for item in items if item != ""]


def parse_note(text: str) -> dict:
    """Split a Markdown file into {meta, body}.

    Frontmatter is a strict YAML subset: `key: scalar`, `key: [a, b]` and block lists
    (`key:` followed by `  - item` lines). Anything else under a key is kept verbatim as
    {"raw": "..."} so other tools' fields survive a save.
    """
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {"meta": {}, "body": text}
    end = text.find("\n---", 3)
    while end != -1 and text[end + 4 : end + 5] not in ("\n", ""):
        end = text.find("\n---", end + 4)
    if end == -1:
        return {"meta": {}, "body": text}
    header = text[4:end]
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]

    meta: dict = {}
    lines = header.split("\n") if header else []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _KEY_RE.match(line)
        if not match:
            index += 1
            continue
        key, rest = match.group(1), match.group(2)
        block = []
        while index + 1 < len(lines) and (lines[index + 1].startswith((" ", "\t")) or lines[index + 1] == ""):
            index += 1
            block.append(lines[index])
        while block and block[-1].strip() == "":
            block.pop()
        stripped = rest.strip()
        if block:
            items = [b.strip() for b in block]
            if stripped == "" and all(item.startswith("- ") or item == "-" for item in items):
                meta[key] = [_parse_scalar(item[1:]) for item in items if _parse_scalar(item[1:]) != ""]
            else:
                meta[key] = {"raw": "\n".join([line] + block)}
        elif re.match(r"^\[.*\]\s*(#.*)?$", stripped):
            inner = stripped[1 : stripped.rindex("]")]
            meta[key] = _parse_list(inner)
        else:
            meta[key] = _parse_scalar(rest)
        index += 1
    return {"meta": meta, "body": body}


def _format_scalar(value: str) -> str:
    value = str(value)
    needs_quotes = (
        value != value.strip()
        or ": " in value
        or " #" in value
        or (value[:1] in "[{\"'#&*!|>%@`" and value != "")
    )
    if needs_quotes:
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _format_list_item(value: str) -> str:
    value = str(value)
    if "," in value or value[:1] in "[]\"'" or value != value.strip():
        return '"' + value.replace('"', '\\"') + '"'
    return value


def serialize_note(meta: dict, body: str) -> str:
    if not meta:
        return body
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, dict) and "raw" in value:
            lines.append(value["raw"])
        elif isinstance(value, list):
            lines.append(f"{key}: [{', '.join(_format_list_item(v) for v in value)}]")
        elif value is None:
            lines.append(f"{key}:")
        else:
            lines.append(f"{key}: {_format_scalar(value)}".rstrip())
    lines.append("---")
    return "\n".join(lines) + "\n" + body


# ---------------------------------------------------------------- names


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M")).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text[:60].strip("-")
    return text or "untitled"


def unique_slug(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


def note_path(note_type: str, title: str, existing_paths: list[str], today: str) -> str:
    """Relative path (inside brain/) for a new note, following the naming rules."""
    folder = FOLDERS.get(note_type, "notes")
    taken = {p[len(folder) + 1 : -3] for p in existing_paths if p.startswith(folder + "/") and p.endswith(".md")}
    if note_type == "journal":
        return f"{folder}/{unique_slug(today, taken)}.md"
    slug = slugify(title)
    if note_type == "decision":
        numbers = [int(m.group(1)) for name in taken if (m := re.match(r"^(\d{4})-", name))]
        slug = f"{(max(numbers) + 1 if numbers else 1):04d}-{slug}"
    return f"{folder}/{unique_slug(slug, taken)}.md"


def default_status(note_type: str) -> str:
    statuses = STATUSES.get(note_type) or []
    return statuses[0] if statuses else ""


# ---------------------------------------------------------------- capture


def parse_capture(text: str) -> dict:
    """`todo: ship v1 #release` -> {type, title, tags, status, body}."""
    text = text.replace("\r\n", "\n").strip()
    first, _, rest = text.partition("\n")
    note_type = "idea"
    match = _CAPTURE_RE.match(first)
    if match and match.group(1).lower() in CAPTURE_PREFIXES:
        note_type = CAPTURE_PREFIXES[match.group(1).lower()]
        first = match.group(2)
    tags = []
    for tag in _TAG_RE.findall(first):
        name = tag[1].lower()
        if name not in tags:
            tags.append(name)
    title = re.sub(r"\s+", " ", _TAG_RE.sub(r"\1", first)).strip()
    return {
        "type": note_type,
        "title": title,
        "tags": tags,
        "status": default_status(note_type),
        "body": rest.strip(),
    }


# ---------------------------------------------------------------- notes


def note_from_file(path: str, text: str, mtime: int = 0) -> dict:
    parsed = parse_note(text)
    meta, body = parsed["meta"], parsed["body"]
    folder = path.split("/")[0] if "/" in path else ""
    folder_type = next((t for t, f in FOLDERS.items() if f == folder), "note")
    note_type = meta.get("type") if isinstance(meta.get("type"), str) and meta.get("type") in TYPES else folder_type
    title = meta.get("title") if isinstance(meta.get("title"), str) else ""
    if not title:
        heading = re.search(r"^#\s+(.+)$", body, re.M)
        title = heading.group(1).strip() if heading else path.rsplit("/", 1)[-1][:-3]
    tags = meta.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = []
    status = meta.get("status") if isinstance(meta.get("status"), str) else ""
    if not status:
        status = default_status(note_type)
    return {
        "path": path,
        "slug": path.rsplit("/", 1)[-1][:-3],
        "type": note_type,
        "title": title,
        "status": status,
        "tags": tags,
        "meta": meta,
        "body": body,
        "mtime": mtime,
    }


def extract_links(body: str) -> list[str]:
    links = []
    for raw in _LINK_RE.findall(body):
        target = slugify(raw.split("|")[0].split("#")[0])
        if target not in links:
            links.append(target)
    return links


def build_graph(notes: list[dict]) -> dict:
    by_slug = {}
    for note in notes:
        by_slug.setdefault(note["slug"], note["path"])
        by_slug.setdefault(slugify(note["title"]), note["path"])
    nodes = [{"id": n["path"], "title": n["title"], "type": n["type"]} for n in notes]
    edges = []
    seen = set()
    for note in notes:
        for target in extract_links(note["body"]):
            target_path = by_slug.get(target)
            if not target_path or target_path == note["path"]:
                continue
            key = tuple(sorted((note["path"], target_path)))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": note["path"], "target": target_path})
    return {"nodes": nodes, "edges": edges}


def backlinks(notes: list[dict], path: str) -> list[str]:
    target = next((n for n in notes if n["path"] == path), None)
    if not target:
        return []
    names = {target["slug"], slugify(target["title"])}
    return [n["path"] for n in notes if n["path"] != path and names.intersection(extract_links(n["body"]))]


# ---------------------------------------------------------------- code inbox


def scan_todos(text: str, path: str) -> list[dict]:
    items = []
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        match = _TODO_RE.search(line)
        if not match:
            continue
        message = re.sub(r"\s*(\*/|-->)\s*$", "", match.group(2)).strip()
        items.append({"path": path, "line": number, "tag": match.group(1), "text": message or "(no description)"})
    return items


# ---------------------------------------------------------------- context pack


def _summary(body: str) -> str:
    for line in body.split("\n"):
        line = line.strip()
        if line and not line.startswith(("#", "---", "```")):
            line = re.sub(r"^[-*>]\s*(\[[ xX]\]\s*)?", "", line).strip()
            if line:
                return line if len(line) <= 120 else line[:117].rstrip() + "..."
    return ""


def _sort_key(note: dict):
    return (note["title"].lower(), note["path"])


def context_pack(notes: list[dict], project: str, today: str) -> str:
    """A compact brief of the brain for teammates and AI assistants."""

    def bullet(note: dict) -> str:
        summary = _summary(note["body"])
        tags = f" `{' '.join('#' + t for t in note['tags'])}`" if note["tags"] else ""
        line = f"- **{note['title']}**{tags} ({note['path']})"
        return line + (f": {summary}" if summary else "")

    def pick(note_type: str, statuses: tuple[str, ...]) -> list[dict]:
        return sorted((n for n in notes if n["type"] == note_type and n["status"] in statuses), key=_sort_key)

    sections = [
        ("Doing now", pick("task", ("doing",))),
        ("Up next", pick("task", ("todo",))),
        ("Decisions in force", pick("decision", ("accepted",))),
        ("Open questions (proposed decisions)", pick("decision", ("proposed",))),
        ("Ideas being explored", pick("idea", ("exploring", "ready"))),
    ]
    counts = {t: sum(1 for n in notes if n["type"] == t) for t in TYPES}
    out = [
        f"# {project}: project context",
        "",
        f"_Generated by Cairn on {today} from the `brain/` folder. Regenerate instead of editing._",
        "",
        f"{counts['task']} tasks · {counts['idea']} ideas · {counts['decision']} decisions · "
        f"{counts['note']} notes · {counts['journal']} journal pages",
        "",
    ]
    for heading, items in sections:
        out.append(f"## {heading}")
        out.append("")
        if items:
            out.extend(bullet(n) for n in items)
        else:
            out.append("_Nothing yet._")
        out.append("")
    out.append("## How to use this file")
    out.append("")
    out.append("Read this before working on the project. Details live in the linked files; "
               "decisions in force are binding unless a newer decision supersedes them.")
    return "\n".join(out) + "\n"

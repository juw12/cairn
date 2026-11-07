"""Pure functions shared (and parity-tested) with docs/core.js.

Nothing in here touches the disk, so the same rules hold for the local server,
the CLI and the browser-only app.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from urllib.parse import unquote
import unicodedata

TYPES = ("idea", "task", "decision", "note", "journal", "session", "feedback")

FOLDERS = {
    "idea": "ideas",
    "task": "tasks",
    "decision": "decisions",
    "note": "notes",
    "journal": "journal",
    "session": "sessions",
    "feedback": "feedback",
}

STATUSES = {
    "idea": ["raw", "exploring", "ready", "parked"],
    "task": ["todo", "doing", "done"],
    "decision": ["proposed", "accepted", "superseded"],
    "note": [],
    "journal": [],
    "session": ["live", "shipped", "partial", "dropped"],
    "feedback": ["new", "planned", "addressed", "wontfix"],
}

TEMPLATES = {
    "idea": "## Why\n\n\n## What if\n\n",
    "task": "",
    "decision": "## Context\n\n\n## Options\n\n\n## Decision\n\n\n## Consequences\n\n",
    "note": "",
    "journal": "## Log\n\n- \n",
    "session": "## Goal\n\n\n## Log\n\n",
    "feedback": "",
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
    "feedback": "feedback",
    "fb": "feedback",
}

SPECIAL_FILES = ("CONTEXT.md", "README.md")

_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
_TAG_RE = re.compile(r"(^|\s)#([A-Za-z0-9][A-Za-z0-9_/-]*)")
_LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
_TODO_RE = re.compile(
    r"(?:#|//|/\*|<!--|--|;|\*)\s*(TODO|FIXME|HACK|XXX)\b(?:\([^)]*\))?[\s:\-]*(.*)$"
)
_CAPTURE_RE = re.compile(r"^([A-Za-z]+):\s*(.*)$", re.S)
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    elif note_type == "session":
        slug = f"{today}-{slug}"
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
    file_name = path.rsplit("/", 1)[-1][:-3]
    if folder_type == "note" and _DAY_RE.match(file_name):
        folder_type = "journal"  # Obsidian-style daily notes (2026-11-05.md) anywhere in the brain
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


# ---------------------------------------------------------------- links (Obsidian-compatible)

_WIKI_RE = re.compile(r"(!?)\[\[([^\]\n]+)\]\]")
_MD_LINK_RE = re.compile(r"(!?)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FENCE_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_MD_SUFFIX_RE = re.compile(r"\.md$", re.I)


def _folder_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _join_path(folder: str, target: str) -> str:
    parts: list[str] = []
    for part in (f"{folder}/{target}" if folder else target).split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def _safe_decode(text: str) -> str:
    try:
        return unquote(text, errors="strict")
    except UnicodeDecodeError:
        return text


def link_targets(body: str) -> list[dict]:
    """Every link in a body, in order; links inside code are ignored, like Obsidian does."""
    text = _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", body))
    out = []
    for embed, inner in _WIKI_RE.findall(text):
        target = inner.split("|")[0].split("#")[0].strip()
        if target:
            out.append({"target": target, "embed": embed == "!"})
    for embed, href in _MD_LINK_RE.findall(text):
        if _URL_RE.match(href) or href.startswith("#"):
            continue
        target = _safe_decode(href.split("#")[0]).strip()
        if target:
            out.append({"target": target, "embed": embed == "!"})
    return out


def extract_links(body: str) -> list[str]:
    links = []
    for link in link_targets(body):
        if link["embed"]:
            continue
        name = slugify(_MD_SUFFIX_RE.sub("", link["target"]).split("/")[-1])
        if name not in links:
            links.append(name)
    return links


def link_index(notes: list[dict]) -> dict:
    by_path: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    by_slug: dict[str, list[str]] = {}
    by_title: dict[str, str] = {}
    for note in notes:
        by_path[note["path"].lower()] = note["path"]
        file_name = note["path"].rsplit("/", 1)[-1][:-3]
        by_name.setdefault(file_name.lower(), []).append(note["path"])
        by_slug.setdefault(slugify(file_name), []).append(note["path"])
        by_title.setdefault(slugify(note["title"]), note["path"])
    return {"by_path": by_path, "by_name": by_name, "by_slug": by_slug, "by_title": by_title}


def _closest(paths: list[str], folder: str) -> str:
    same = [p for p in paths if _folder_of(p) == folder]
    if same:
        return same[0]
    return sorted(paths, key=lambda p: (len(p.split("/")), p))[0]


def _is_slug_name(path: str) -> bool:
    file_name = path.rsplit("/", 1)[-1][:-3]
    return slugify(file_name) == file_name


def _loose_match(index: dict, name: str, folder: str) -> str | None:
    slug = slugify(name)
    if slug in index["by_slug"]:
        return _closest(index["by_slug"][slug], folder)
    return index["by_title"].get(slug)


def resolve_target(index: dict, target: str, source: str = "") -> str | None:
    """Resolve a link written in `source` to a note path, or None (attachment or missing note)."""
    name = target.strip().replace("\\", "/")
    if not name:
        return None
    explicit_note = bool(_MD_SUFFIX_RE.search(name))
    if explicit_note:
        name = name[:-3]
    folder = _folder_of(source)
    if "/" in name:
        for option in (_join_path(folder, name), _join_path("", name)):
            hit = index["by_path"].get(f"{option.lower()}.md")
            if hit:
                return hit
        return None
    named = index["by_name"].get(name.lower())
    if named:
        return _closest(named, folder)
    if not explicit_note and _EXTENSION_RE.search(name):
        return None
    loose = _loose_match(index, name, folder)
    return loose if loose and _is_slug_name(loose) else None


def is_attachment(target: str) -> bool:
    name = target.strip()
    return not _MD_SUFFIX_RE.search(name) and bool(_EXTENSION_RE.search(name))


def near_miss(index: dict, target: str, source: str = "") -> str | None:
    if resolve_target(index, target, source) or is_attachment(target):
        return None
    name = _MD_SUFFIX_RE.sub("", target.strip().replace("\\", "/")).split("/")[-1]
    return _loose_match(index, name, _folder_of(source))


def note_links(note: dict) -> list[dict]:
    """Links a note makes: wiki links in its properties plus its body (Obsidian graphs both)."""
    from_meta = []

    def walk(value):
        if isinstance(value, str):
            for embed, inner in _WIKI_RE.findall(value):
                target = inner.split("|")[0].split("#")[0].strip()
                if target:
                    from_meta.append({"target": target, "embed": embed == "!"})
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict) and isinstance(value.get("raw"), str):
            walk(value["raw"])

    for value in (note.get("meta") or {}).values():
        walk(value)
    return from_meta + link_targets(note["body"])


def build_graph(notes: list[dict]) -> dict:
    index = link_index(notes)
    nodes = [
        {
            "id": n["path"],
            "title": n["title"],
            "type": n["type"],
            "kind": n["meta"]["type"] if isinstance(n["meta"].get("type"), str) and n["meta"]["type"] else n["type"],
            "folder": _folder_of(n["path"]),
            "tags": n["tags"],
            "status": n["status"],
        }
        for n in notes
    ]
    edges: list[dict] = []
    by_pair: dict[tuple, dict] = {}
    attachments: list[dict] = []
    unresolved: list[dict] = []
    for note in notes:
        for link in note_links(note):
            target, embed = link["target"], link["embed"]
            target_path = resolve_target(index, target, note["path"])
            if not target_path:
                if is_attachment(target):
                    attachments.append({"from": note["path"], "target": target, "embed": embed})
                else:
                    unresolved.append({"from": note["path"], "target": target, "embed": embed,
                                       "suggestion": near_miss(index, target, note["path"])})
                continue
            if target_path == note["path"]:
                continue
            key = tuple(sorted((note["path"], target_path)))
            existing = by_pair.get(key)
            if existing:
                if existing["source"] != note["path"]:
                    existing["both"] = True
                continue
            edge = {"source": note["path"], "target": target_path, "both": False}
            by_pair[key] = edge
            edges.append(edge)
    return {"nodes": nodes, "edges": edges, "attachments": attachments, "unresolved": unresolved}


def backlinks(notes: list[dict], path: str) -> list[str]:
    if not any(n["path"] == path for n in notes):
        return []
    index = link_index(notes)
    return [
        n["path"]
        for n in notes
        if n["path"] != path and any(resolve_target(index, l["target"], n["path"]) == path for l in note_links(n))
    ]


# ---------------------------------------------------------------- lossless edits


def _split_lines(text: str) -> list[str]:
    return [line for line in re.split(r"(?<=\n)", text) if line != ""]


def _strip_eol(line: str) -> str:
    return re.sub(r"\r?\n$", "", line)


def _format_property(key: str, value, style_lines: list[str], eol: str) -> list[str]:
    if isinstance(value, list):
        block_item = next((_strip_eol(l) for l in style_lines[1:] if re.match(r"^\s*-\s", _strip_eol(l))), None)
        if block_item and value:
            indent = re.match(r"^(\s*)-", block_item).group(1)
            return [f"{key}:{eol}"] + [f"{indent}- {_format_list_item(item)}{eol}" for item in value]
        return [f"{key}: [{', '.join(_format_list_item(item) for item in value)}]{eol}"]
    if value is None or value == "":
        return [f"{key}:{eol}"]
    return [f"{key}: {_format_scalar(value)}{eol}"]


def update_note_text(original: str, changes: dict | None = None, body: str | None = None) -> str:
    """Apply property changes and/or a new body to a note, touching nothing else (see core.js)."""
    changes = changes or {}
    lines = _split_lines(original)
    eol = "\r\n" if "\r\n" in original else "\n"
    has_header = bool(lines) and _strip_eol(lines[0]) == "---"
    closing = -1
    if has_header:
        for index in range(1, len(lines)):
            if _strip_eol(lines[index]) == "---":
                closing = index
                break
    parsed = parse_note(original)
    current = parsed["meta"]
    wanted = [(k, v) for k, v in changes.items() if json.dumps(current.get(k)) != json.dumps(v)]
    raw_body = original if closing == -1 else "".join(lines[closing + 1:])
    body_changed = body is not None and body != parsed["body"]
    if body_changed:
        normalized = body.replace("\r\n", "\n")
        raw_body = normalized.replace("\n", "\r\n") if eol == "\r\n" else normalized
    if not wanted and not body_changed:
        return original
    if closing == -1:
        if not wanted:
            return raw_body
        header = [line for k, v in wanted for line in _format_property(k, v, [], eol)]
        return f"---{eol}{''.join(header)}---{eol}{raw_body}"
    header_lines = lines[1:closing]
    segments: list[dict] = []
    index = 0
    while index < len(header_lines):
        match = _KEY_RE.match(_strip_eol(header_lines[index]))
        if not match:
            segments.append({"key": None, "lines": [header_lines[index]]})
            index += 1
            continue
        segment = {"key": match.group(1), "lines": [header_lines[index]]}
        while index + 1 < len(header_lines) and (
            re.match(r"^[ \t]", header_lines[index + 1]) or _strip_eol(header_lines[index + 1]) == ""
        ):
            index += 1
            segment["lines"].append(header_lines[index])
        segments.append(segment)
        index += 1
    for key, value in wanted:
        segment = next((s for s in segments if s["key"] == key), None)
        if segment:
            trailing_blank = []
            while len(segment["lines"]) > 1 and _strip_eol(segment["lines"][-1]) == "":
                trailing_blank.insert(0, segment["lines"].pop())
            segment["lines"] = _format_property(key, value, segment["lines"], eol) + trailing_blank
        else:
            segments.append({"key": key, "lines": _format_property(key, value, [], eol)})
    if segments and not segments[-1]["lines"][-1].endswith("\n"):
        segments[-1]["lines"].append(eol)
    closing_line = lines[closing]
    if not closing_line.endswith("\n") and raw_body:
        closing_line += eol
    return lines[0] + "".join(line for s in segments for line in s["lines"]) + closing_line + raw_body


# ---------------------------------------------------------------- folder sync (Obsidian)


def plan_sync(vault: dict, brain: dict, manifest: dict) -> list[dict]:
    """Two-way sync plan between a vault folder and a brain folder.

    vault / brain: {relative path: {"hash": str, "mtime": int}}; manifest: {path: hash both sides had at the
    last sync}. Returns [{path, op[, keep]}] sorted by path, where op is pull (vault -> brain), push
    (brain -> vault), delete-brain, delete-vault or conflict (keep = the newer side). An edit always wins over
    a delete, so nothing written on either side is lost.
    """
    plan = []
    for path in sorted(set(vault) | set(brain)):
        v, b, m = vault.get(path), brain.get(path), manifest.get(path)
        vh = v["hash"] if v else None
        bh = b["hash"] if b else None
        if vh == bh:
            continue
        if m is None:
            op = "pull" if v and not b else "push" if b and not v else "conflict"
        else:
            vault_changed, brain_changed = vh != m, bh != m
            if vault_changed and not brain_changed:
                op = "pull" if v else "delete-brain"
            elif brain_changed and not vault_changed:
                op = "push" if b else "delete-vault"
            else:
                op = "push" if not v else "pull" if not b else "conflict"
        step = {"path": path, "op": op}
        if op == "conflict":
            step["keep"] = "brain" if b["mtime"] > v["mtime"] else "vault"
        plan.append(step)
    return plan


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


# ---------------------------------------------------------------- vibe sessions and feedback

# A session log line: `- 14:02 pass: pytest (12s)`. Anything else in the body is free text.
LOG_KINDS = ("start", "note", "prompt", "pass", "fail", "commit", "end")
_LOG_LINE_RE = re.compile(r"^\s*[-*]\s+(\d{1,2}:\d{2})\s+([a-z]+):\s*(.*)$")
_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")
FINISHED = ("shipped", "partial", "dropped")


def session_log(body: str) -> list[dict]:
    """The timestamped steps of a session, in the order they were written."""
    entries = []
    for line in body.replace("\r\n", "\n").split("\n"):
        match = _LOG_LINE_RE.match(line)
        if match and match.group(2) in LOG_KINDS:
            entries.append({"time": match.group(1).zfill(5), "kind": match.group(2), "text": match.group(3).strip()})
    return entries


def stamp_minutes(stamp) -> int | None:
    """`2026-11-07T14:02` -> minutes on a common clock (only differences are meaningful)."""
    match = _STAMP_RE.match(stamp) if isinstance(stamp, str) else None
    if not match:
        return None
    year, month, day, hour, minute = (int(g) for g in match.groups())
    try:
        days = dt.date(year, month, day).toordinal() - dt.date(1970, 1, 1).toordinal()
    except ValueError:
        return None
    return days * 1440 + hour * 60 + minute


def session_minutes(note: dict) -> int:
    start = stamp_minutes(note["meta"].get("started"))
    end = stamp_minutes(note["meta"].get("ended"))
    if start is None or end is None or end < start:
        return 0
    return end - start


def _count(value) -> int:
    return int(value) if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()) else 0


def feedback_rating(note: dict) -> int | None:
    rating = _count(note["meta"].get("rating"))
    return rating if 1 <= rating <= 5 else None


def _session_day(note: dict) -> str:
    for key in ("started", "created"):
        value = note["meta"].get(key)
        if isinstance(value, str) and _DAY_RE.match(value[:10]):
            return value[:10]
    return ""


def _percent(part: int, whole: int) -> int | None:
    return int(100 * part / whole + 0.5) if whole else None


def vibe_stats(notes: list[dict], today: str, days: int = 14) -> dict:
    """Totals for the Vibe view: sessions, results, checks, code churn and feedback."""
    sessions = [n for n in notes if n["type"] == "session"]
    feedback = [n for n in notes if n["type"] == "feedback"]
    finished = [s for s in sessions if s["status"] in FINISHED]
    logs = [entry for s in sessions for entry in session_log(s["body"])]
    passed = sum(1 for e in logs if e["kind"] == "pass")
    failed = sum(1 for e in logs if e["kind"] == "fail")
    ratings = [r for r in (feedback_rating(f) for f in feedback) if r is not None]
    end = dt.date.fromisoformat(today)
    daily = []
    for offset in range(days - 1, -1, -1):
        day = (end - dt.timedelta(days=offset)).isoformat()
        on_day = [s for s in sessions if _session_day(s) == day]
        daily.append({
            "day": day,
            "sessions": len(on_day),
            "minutes": sum(session_minutes(s) for s in on_day),
            "shipped": sum(1 for s in on_day if s["status"] == "shipped"),
        })
    return {
        "sessions": len(sessions),
        "live": sum(1 for s in sessions if s["status"] == "live"),
        "shipped": sum(1 for s in sessions if s["status"] == "shipped"),
        "partial": sum(1 for s in sessions if s["status"] == "partial"),
        "dropped": sum(1 for s in sessions if s["status"] == "dropped"),
        "ship_rate": _percent(sum(1 for s in finished if s["status"] == "shipped"), len(finished)),
        "minutes": sum(session_minutes(s) for s in finished),
        "prompts": sum(1 for e in logs if e["kind"] == "prompt"),
        "passed": passed,
        "failed": failed,
        "pass_rate": _percent(passed, passed + failed),
        "commits": sum(_count(s["meta"].get("commits")) for s in sessions),
        "added": sum(_count(s["meta"].get("added")) for s in sessions),
        "removed": sum(_count(s["meta"].get("removed")) for s in sessions),
        "feedback": len(feedback),
        "feedback_open": sum(1 for f in feedback if f["status"] in ("new", "planned")),
        "feedback_addressed": sum(1 for f in feedback if f["status"] == "addressed"),
        "rating": int(10 * sum(ratings) / len(ratings) + 0.5) / 10 if ratings else None,
        "ratings": [ratings.count(stars) for stars in range(1, 6)],
        "daily": daily,
    }

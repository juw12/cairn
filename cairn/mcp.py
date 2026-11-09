"""Model Context Protocol server: AI assistants read and write the brain directly.

`cairn mcp` speaks MCP over stdio (newline-delimited JSON-RPC 2.0) with the standard
library only. Claude Desktop, Claude Code, Cursor, VS Code, OpenCode and any other MCP
client can then search the brain, read notes with their backlinks, capture ideas, tasks
and decisions, move a task along, log progress to today's journal and pull the context
pack, all as the same plain Markdown files the app and Obsidian use.

`cairn mcp --print-config` prints the snippet to paste into each client.
`cairn mcp --read-only` leaves out every tool that writes.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

from . import __version__, core
from .brain import Brain, ConflictError, PathError

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
RESOURCE_PREFIX = "cairn://brain/"
CONTEXT_URI = "cairn://context"

INSTRUCTIONS = (
    "This server is the project's Cairn brain: ideas, tasks, decisions, notes and a daily journal kept as "
    "plain Markdown in the repo. Before answering questions about why the project works the way it does, "
    "search it (decisions record the reasons). When the user settles on something, capture it as a decision; "
    "when work is agreed, capture a task; when you finish a step, log it to the journal and move the task "
    "with update_note. Call context first for a one-page brief of open tasks, accepted decisions and active ideas."
)

_WORD_RE = re.compile(r"[\w#@-]+", re.UNICODE)


# ---------------------------------------------------------------- helpers


def _today() -> str:
    return dt.date.today().isoformat()


def _now() -> str:
    return dt.datetime.now().strftime("%H:%M")


class ToolError(Exception):
    """A problem the assistant should see and can fix (bad path, unknown status...)."""


def _find_note(notes: list[dict], ref: str) -> dict:
    """Accept a path (tasks/ship-v1.md), a path without .md, a slug, a [[wikilink]] or a title."""
    wanted = (ref or "").strip()
    if not wanted:
        raise ToolError("Give a note path, slug or title.")
    wanted = wanted.removeprefix(RESOURCE_PREFIX).strip("[]").split("|", 1)[0].strip()
    by_path = {n["path"]: n for n in notes}
    for candidate in (wanted, wanted + ".md"):
        if candidate in by_path:
            return by_path[candidate]
    resolved = core.resolve_target(core.link_index(notes), wanted)
    if resolved and resolved in by_path:
        return by_path[resolved]
    lowered = wanted.lower()
    for note in notes:
        if note["title"].lower() == lowered or note["slug"].lower() == lowered:
            return note
    raise ToolError(f"No note matches {ref!r}. Use search or list_notes to find the path.")


def _summary_line(note: dict) -> str:
    status = f" [{note['status']}]" if note["status"] else ""
    tags = (" " + " ".join("#" + t for t in note["tags"])) if note["tags"] else ""
    return f"- {note['path']} ({note['type']}{status}) {note['title']}{tags}"


def _snippet(body: str, terms: list[str], width: int = 160) -> str:
    flat = " ".join(body.split())
    lowered = flat.lower()
    hits = [lowered.find(t) for t in terms if lowered.find(t) >= 0]
    if not hits:
        return flat[:width] + ("..." if len(flat) > width else "")
    start = max(0, min(hits) - width // 3)
    piece = flat[start:start + width]
    return ("..." if start else "") + piece + ("..." if start + width < len(flat) else "")


def search_notes(notes: list[dict], query: str, note_type: str = "", limit: int = 10) -> list[tuple[float, dict, str]]:
    """Rank notes for a query: every word must appear somewhere; titles and tags weigh most."""
    terms = [t.lower().lstrip("#") for t in _WORD_RE.findall(query or "") if t.strip("#")]
    if not terms:
        return []
    results = []
    for note in notes:
        if note_type and note["type"] != note_type:
            continue
        title = note["title"].lower()
        tags = " ".join(note["tags"]).lower()
        body = note["body"].lower()
        score = 0.0
        for term in terms:
            in_title = term in title
            in_tags = term in tags
            body_hits = body.count(term)
            if not (in_title or in_tags or body_hits or term in note["path"].lower()):
                break
            score += 6 * in_title + 4 * in_tags + min(body_hits, 5) + (1 if term in note["path"].lower() else 0)
        else:
            if note["type"] == "decision":
                score += 0.5  # the "why" is usually what an assistant is looking for
            results.append((score, note, _snippet(note["body"], terms)))
    results.sort(key=lambda item: (-item[0], -(item[1].get("mtime") or 0)))
    return results[: max(1, min(int(limit or 10), 50))]


# ---------------------------------------------------------------- tools


def _tool_search(brain: Brain, args: dict) -> str:
    hits = search_notes(brain.notes(), args.get("query", ""), args.get("type", ""), args.get("limit", 10))
    if not hits:
        return f"Nothing in the brain matches {args.get('query', '')!r}."
    lines = [f"{len(hits)} match(es) for {args.get('query', '')!r}:"]
    for _, note, snippet in hits:
        lines.append(_summary_line(note))
        if snippet:
            lines.append(f"  {snippet}")
    return "\n".join(lines)


def _tool_read_note(brain: Brain, args: dict) -> str:
    notes = brain.notes()
    note = _find_note(notes, args.get("path", ""))
    text = brain.read(note["path"])["text"]
    back = core.backlinks(notes, note["path"])
    links = sorted(set(back))
    footer = "\n\n---\nLinked from: " + (", ".join(links) if links else "nothing yet")
    return f"# brain/{note['path']}\n\n{text.rstrip()}{footer}"


def _tool_list_notes(brain: Brain, args: dict) -> str:
    notes = brain.notes()
    note_type = args.get("type") or ""
    status = args.get("status") or ""
    tag = (args.get("tag") or "").lstrip("#").lower()
    picked = [
        n for n in notes
        if (not note_type or n["type"] == note_type)
        and (not status or n["status"] == status)
        and (not tag or tag in [t.lower() for t in n["tags"]])
    ]
    order = {t: i for i, t in enumerate(core.TYPES)}
    picked.sort(key=lambda n: (order.get(n["type"], 99), n["status"], -(n.get("mtime") or 0)))
    limit = max(1, min(int(args.get("limit") or 50), 500))
    if not picked:
        return "No notes match."
    lines = [_summary_line(n) for n in picked[:limit]]
    if len(picked) > limit:
        lines.append(f"... and {len(picked) - limit} more (raise limit or filter by type/status/tag)")
    return "\n".join(lines)


def _tool_capture(brain: Brain, args: dict) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        raise ToolError('Give text, e.g. "decision: use SQLite for the cache #storage".')
    brain.init()
    captured = core.parse_capture(text)
    if captured["type"] == "journal":
        match = core._CAPTURE_RE.match(text)
        page = brain.log(match.group(2).strip() if match else text, _today(), _now())
        return f"Logged to brain/{page['path']}"
    body = (args.get("body") or "").strip() or captured["body"]
    note = brain.create(captured["type"], captured["title"] or _today(), captured["tags"], body, _today())
    return f"Created {captured['type']} brain/{note['path']}"


def _tool_update_note(brain: Brain, args: dict) -> str:
    notes = brain.notes()
    note = _find_note(notes, args.get("path", ""))
    changes: dict = {}
    status = (args.get("status") or "").strip()
    if status:
        allowed = core.STATUSES.get(note["type"], [])
        if allowed and status not in allowed:
            raise ToolError(f"{note['type']} status must be one of: {', '.join(allowed)}")
        changes["status"] = status
    add_tags = [t.lstrip("#") for t in args.get("add_tags") or [] if t.strip("#")]
    remove_tags = {t.lstrip("#").lower() for t in args.get("remove_tags") or []}
    if add_tags or remove_tags:
        tags = [t for t in note["tags"] if t.lower() not in remove_tags]
        tags += [t for t in add_tags if t.lower() not in {x.lower() for x in tags}]
        changes["tags"] = tags
    append = (args.get("append") or "").strip()
    if not changes and not append:
        raise ToolError("Nothing to change: give status, add_tags, remove_tags or append.")
    current = brain.read(note["path"])
    body = None
    if append:
        existing = core.parse_note(current["text"])["body"].rstrip()
        body = (existing + "\n\n" if existing else "") + append + "\n"
    updated = core.update_note_text(current["text"], changes, body)
    try:
        brain.write(note["path"], updated, current["mtime"])
    except ConflictError:
        raise ToolError(f"brain/{note['path']} changed while updating; read it again and retry.") from None
    done = [f"status -> {status}"] if status else []
    if "tags" in changes:
        done.append("tags -> " + (", ".join(changes["tags"]) or "none"))
    if append:
        done.append("appended text")
    return f"Updated brain/{note['path']}: " + "; ".join(done)


def _tool_log(brain: Brain, args: dict) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        raise ToolError("Give the line to log, e.g. \"Fixed the export bug, tests pass\".")
    from .cli import _LOG_KINDS

    brain.init()
    day = _today()
    match = core._CAPTURE_RE.match(text)
    prefix = match.group(1).lower() if match else ""
    kind = core.CAPTURE_PREFIXES.get(prefix)
    created = ""
    if kind in _LOG_KINDS:
        captured = core.parse_capture(text)
        note = brain.create(kind, captured["title"] or day, captured["tags"], captured["body"], day)
        slug = note["path"].rsplit("/", 1)[-1][:-3]
        line = f"{_LOG_KINDS[kind]}: [[{slug}|{captured['title']}]]"
        created = f"Created {kind} brain/{note['path']}; "
    else:
        line = match.group(2).strip() if kind == "journal" else text
    page = brain.log(line, day, _now())
    return f"{created}logged to brain/{page['path']}"


def _tool_context(brain: Brain, args: dict) -> str:
    return core.context_pack(brain.notes(), brain.project_name, _today())


def _tool_recent(brain: Brain, args: dict) -> str:
    days = max(1, min(int(args.get("days") or 7), 60))
    notes = brain.notes()
    start = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    pages = sorted((n for n in notes if n["type"] == "journal" and n["slug"] >= start), key=lambda n: n["slug"], reverse=True)
    lines = []
    for page in pages:
        entries = core.log_entries(page["body"])
        if entries:
            lines.append(f"## {page['slug']}")
            lines += [f"- {e.get('time', '')} {e.get('text', '')}".rstrip() for e in entries]
    changed = sorted(
        (n for n in notes if n["type"] not in ("journal", "session") and (n.get("mtime") or 0) > 0),
        key=lambda n: -(n.get("mtime") or 0),
    )[:10]
    if changed:
        lines.append("## Recently changed notes")
        lines += [_summary_line(n) for n in changed]
    return "\n".join(lines) or f"No journal entries in the last {days} day(s)."


def _tool_code_todos(brain: Brain, args: dict) -> str:
    limit = max(1, min(int(args.get("limit") or 50), 500))
    items = brain.scan_code(limit)
    if not items:
        return "No TODO/FIXME/HACK comments in the code."
    return "\n".join(f"- {i['path']}:{i['line']} {i['tag']} {i['text']}" for i in items)


_TYPE_ENUM = list(core.TYPES)
_REF = {"type": "string", "description": "Note path (tasks/ship-v1.md), slug (ship-v1) or title"}

TOOLS: list[dict] = [
    {
        "name": "search",
        "description": "Search ideas, tasks, decisions, notes and journal pages. Every word must match; titles and tags rank first. Use it before answering 'why did we...' questions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to find, #tags allowed"},
                "type": {"type": "string", "enum": _TYPE_ENUM, "description": "Only this kind of note"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
        "handler": _tool_search,
    },
    {
        "name": "read_note",
        "description": "Read one note in full (frontmatter and Markdown body) plus the notes that link to it.",
        "inputSchema": {"type": "object", "properties": {"path": _REF}, "required": ["path"]},
        "annotations": {"readOnlyHint": True},
        "handler": _tool_read_note,
    },
    {
        "name": "list_notes",
        "description": "List notes, optionally filtered by type, status (e.g. task todo/doing/done, decision accepted) or tag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": _TYPE_ENUM},
                "status": {"type": "string"},
                "tag": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
        },
        "annotations": {"readOnlyHint": True},
        "handler": _tool_list_notes,
    },
    {
        "name": "context",
        "description": "One-page project brief: goals, open tasks, accepted decisions, active ideas and the recent log. Read this first.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
        "handler": _tool_context,
    },
    {
        "name": "recent",
        "description": "What happened lately: journal entries of the last N days and the most recently changed notes.",
        "inputSchema": {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 60, "default": 7}}},
        "annotations": {"readOnlyHint": True},
        "handler": _tool_recent,
    },
    {
        "name": "code_todos",
        "description": "TODO, FIXME and HACK comments found in the repo's code, with file:line.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}}},
        "annotations": {"readOnlyHint": True},
        "handler": _tool_code_todos,
    },
    {
        "name": "capture",
        "description": "Create a note from one line. Prefix picks the kind: 'idea:', 'todo:', 'decision:', 'note:', 'feedback:'; #tags become tags. Example: 'decision: cache in SQLite, not Redis #storage'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Prefix, title and #tags on one line"},
                "body": {"type": "string", "description": "Optional Markdown body (for a decision: context, options, decision, consequences)"},
            },
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
        "write": True,
        "handler": _tool_capture,
    },
    {
        "name": "update_note",
        "description": "Change a note in place: set its status (task todo/doing/done, idea raw/exploring/ready/parked, decision proposed/accepted/superseded), add or remove tags, or append Markdown. Other fields are kept as they are.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _REF,
                "status": {"type": "string"},
                "add_tags": {"type": "array", "items": {"type": "string"}},
                "remove_tags": {"type": "array", "items": {"type": "string"}},
                "append": {"type": "string", "description": "Markdown added at the end of the body"},
            },
            "required": ["path"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
        "write": True,
        "handler": _tool_update_note,
    },
    {
        "name": "log",
        "description": "Add a timestamped line to today's journal page. 'todo:'/'idea:'/'decision:' lines also create that note and link it.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
        "write": True,
        "handler": _tool_log,
    },
]


# ---------------------------------------------------------------- protocol


class Server:
    def __init__(self, brain: Brain, read_only: bool = False):
        self.brain = brain
        self.read_only = read_only
        self.tools = [t for t in TOOLS if not (read_only and t.get("write"))]

    # Each handler returns a JSON-RPC result or raises RpcError.
    def handle(self, message: dict) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid request")
        method = message["method"]
        params = message.get("params") or {}
        is_notification = "id" not in message
        try:
            result = self.dispatch(method, params)
        except RpcError as error:
            return None if is_notification else _error(message.get("id"), error.code, error.message)
        except Exception as error:  # a bug must not kill the session
            print(f"cairn mcp: {method} failed: {error!r}", file=sys.stderr)
            return None if is_notification else _error(message.get("id"), -32603, f"Internal error: {error}")
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}

    def dispatch(self, method: str, params: dict):
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {
                "protocolVersion": version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False, "subscribe": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": "cairn", "title": f"Cairn brain: {self.brain.project_name}", "version": __version__},
                "instructions": INSTRUCTIONS + (" This server is read-only." if self.read_only else ""),
            }
        if method in ("notifications/initialized", "notifications/cancelled", "initialized"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{k: v for k, v in t.items() if k not in ("handler", "write")} for t in self.tools]}
        if method == "tools/call":
            return self.call_tool(params.get("name", ""), params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": self.resources()}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            return {"contents": [self.read_resource(params.get("uri", ""))]}
        if method == "prompts/list":
            return {"prompts": [{
                "name": "project-brief",
                "title": "Project brief from the brain",
                "description": "Open tasks, accepted decisions, active ideas and the recent log, as context for this chat.",
            }]}
        if method == "prompts/get":
            if params.get("name") != "project-brief":
                raise RpcError(-32602, f"Unknown prompt: {params.get('name')}")
            brief = core.context_pack(self.brain.notes(), self.brain.project_name, _today())
            return {
                "description": "Project brief from the brain",
                "messages": [{"role": "user", "content": {"type": "text", "text": "Here is my project's brief from Cairn. Use it as context.\n\n" + brief}}],
            }
        raise RpcError(-32601, f"Method not found: {method}")

    def call_tool(self, name: str, arguments: dict) -> dict:
        tool = next((t for t in self.tools if t["name"] == name), None)
        if tool is None:
            hidden = any(t["name"] == name for t in TOOLS)
            reason = "is turned off (read-only server)" if hidden else "does not exist"
            raise RpcError(-32602, f"Tool {name!r} {reason}")
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "arguments must be an object")
        try:
            text = tool["handler"](self.brain, arguments)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except (ToolError, PathError, ValueError, OSError) as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}

    def resources(self) -> list[dict]:
        items = [{
            "uri": CONTEXT_URI,
            "name": "context",
            "title": "Project brief",
            "description": "Open tasks, accepted decisions, active ideas, recent log",
            "mimeType": "text/markdown",
        }]
        for note in sorted(self.brain.notes(), key=lambda n: n["path"]):
            items.append({
                "uri": RESOURCE_PREFIX + note["path"],
                "name": note["slug"],
                "title": note["title"],
                "description": f"{note['type']}" + (f" ({note['status']})" if note["status"] else ""),
                "mimeType": "text/markdown",
            })
        return items

    def read_resource(self, uri: str) -> dict:
        if uri == CONTEXT_URI:
            text = core.context_pack(self.brain.notes(), self.brain.project_name, _today())
        elif uri.startswith(RESOURCE_PREFIX):
            try:
                text = self.brain.read(uri[len(RESOURCE_PREFIX):])["text"]
            except (PathError, OSError) as error:
                raise RpcError(-32002, f"Resource not found: {uri} ({error})") from None
        else:
            raise RpcError(-32002, f"Resource not found: {uri}")
        return {"uri": uri, "mimeType": "text/markdown", "text": text}


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(brain: Brain, read_only: bool = False, stdin=None, stdout=None) -> int:
    """Read one JSON-RPC message per line from stdin, answer on stdout. Logs go to stderr only."""
    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout.buffer
    server = Server(brain, read_only)
    print(f"cairn mcp: serving brain {brain.dir} ({'read-only' if read_only else 'read/write'})", file=sys.stderr)
    for raw in source:
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            replies = [_error(None, -32700, "Parse error")]
        else:
            batch = message if isinstance(message, list) else [message]
            replies = [r for r in (server.handle(m) for m in batch) if r is not None]
        for reply in replies:
            sink.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            sink.flush()
    return 0


def server_entry(brain: Brain, read_only: bool = False) -> dict:
    """How an MCP client starts this brain's server: name, command, args and env."""
    return {
        "name": "cairn-" + (core.slugify(brain.project_name) or "brain"),
        "command": sys.executable or "python",
        "args": ["-m", "cairn", "--root", str(brain.root), "mcp"] + (["--read-only"] if read_only else []),
        "env": {"PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    }


def client_config(brain: Brain, read_only: bool = False) -> str:
    """The snippets to paste into common MCP clients, filled in for this brain."""
    entry = server_entry(brain, read_only)
    python, args, name, env = entry["command"], entry["args"], entry["name"], entry["env"]
    package_parent = env["PYTHONPATH"]
    claude = {"mcpServers": {name: {"command": python, "args": args, "env": env}}}
    vscode = {"servers": {name: {"type": "stdio", "command": python, "args": args, "env": env}}}
    opencode = {"mcp": {name: {"type": "local", "command": [python, *args], "environment": env, "enabled": True}}}
    quoted = " ".join(f'"{a}"' if " " in a else a for a in [python, *args])
    return "\n".join([
        f"Cairn MCP server for {brain.dir}",
        "",
        "Claude Desktop (claude_desktop_config.json), Cursor (.cursor/mcp.json), Windsurf:",
        json.dumps(claude, indent=2),
        "",
        "VS Code / GitHub Copilot (.vscode/mcp.json):",
        json.dumps(vscode, indent=2),
        "",
        "OpenCode (opencode.json):",
        json.dumps(opencode, indent=2),
        "",
        "Claude Code:",
        f"  claude mcp add {name} --env PYTHONPATH={package_parent} -- {quoted}",
        "",
        "Test it by hand: run the command above and type",
        '  {"jsonrpc":"2.0","id":1,"method":"tools/list"}',
    ])

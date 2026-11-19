---
name: cairn-mcp
description: Work with a Cairn brain through its MCP server (the cairn tools - search, read_note, list_notes, context, recent, capture, log, update_note and the work queue). Use when cairn tools are available and the user asks about their projects, decisions, tasks, ideas, notes or past work, or asks you to record a decision, add a task, log progress or pick up the next queued job.
---

# Cairn over MCP

`cairn mcp` serves one brain to the assistant. The tools read and write the same Markdown files the Cairn app shows, so nothing needs a separate sync step.

## Which tools exist

Check your tool list first. A server started with `--read-only` has only the reading tools; if the writing tools are missing, do not try to edit the brain's files by hand instead, unless your instructions name a folder you may write in.

### Reading

| Tool | Arguments | Use it to |
|---|---|---|
| `context` | none | Get the one-page brief: goals, open tasks, accepted decisions, active ideas, latest journal lines. Call it first in a new piece of work. |
| `search` | `query` (required), `type`, `limit` | Find notes. Every word must match; titles and tags rank first. Search before answering any "why did we..." question. |
| `read_note` | `path` (required) | Read one note in full, with the notes that link to it. `path` may be a path (`tasks/ship-v1.md`), a slug (`ship-v1`) or a title. |
| `list_notes` | `type`, `status`, `tag`, `limit` | List notes by type, status or tag, for example every task that is `doing`. |
| `recent` | `days` | See the journal of the last N days and the notes changed most recently. |
| `code_todos` | `limit` | List `TODO`, `FIXME` and `HACK` comments in the project's code with `file:line`. |
| `precedent_brief` | `goal`, `k` | Before starting a delegated goal: what the user asked for after similar goals, lessons, a draft definition of done. |
| `queue_next` | `auto_only` | Get the next open `#queue` task, skipping items tagged `#needs-me`. |

### Writing (absent in read-only mode)

| Tool | Arguments | Use it to |
|---|---|---|
| `capture` | `text` (required), `body` | Create a note from one line. The prefix picks the kind: `idea:`, `todo:`, `decision:`, `note:`, `feedback:`. `#tags` become tags. |
| `log` | `text` (required) | Add a timestamped line to today's journal. A `todo:`, `idea:` or `decision:` line also creates that note and links it. |
| `update_note` | `path` (required), `status`, `add_tags`, `remove_tags`, `append` | Change a note in place: move its status, change tags or append Markdown. It refuses to overwrite a newer edit. |
| `queue_add` | `text` (required), `auto`, `needs_me` | Queue a follow-up instead of asking the user. `auto`: safe unattended. `needs_me`: blocked until the user acts. |
| `precedent_log` | `goal` (required), `delivered` (required), `correction`, `queue_path` | When a goal is done: record what was delivered and any correction the user made. `queue_path` also marks that queue task done. |

Every note is also a resource, `cairn://brain/<path>`. The `project-brief` prompt starts a session from the context pack; the `precedent` prompt hands over a goal.

## How to work with it

1. **Start** with `context`. Treat decisions with status `accepted` as binding.
2. **Look things up** with `search`, then `read_note` on the best hits. Use two or three short queries with different words when the first finds nothing; every word has to match, so long queries miss.
3. **Answer with sources**: name the note each fact came from, for example `decisions/0007-...`.
4. **Record as you go** (when writing tools exist):
   - the user settles something: `capture` with `decision:`;
   - work is agreed: `capture` with `todo:`;
   - a step is finished: `log` one plain sentence, and `update_note` to move the task to `doing` or `done`;
   - something comes up that is not part of the current job: `queue_add`, not a question to the user.
5. **Finish** a delegated goal with `precedent_log`.

## Mistakes to avoid

- Do not answer from memory when the brain may hold the answer. Search first.
- Do not create a second note for something that exists. Search for it, then `update_note`.
- Do not invent a note path. Use a path that `search` or `list_notes` returned.
- Do not set a status that the type does not have: task `todo`/`doing`/`done`, idea `raw`/`exploring`/`ready`/`parked`, decision `proposed`/`accepted`/`superseded`.
- Do not paste whole notes into the chat. Quote the lines that answer the question.

## Setting it up

`cairn mcp --print-config` prints ready-to-paste configuration for OpenCode, Claude Desktop, Cursor, VS Code and Claude Code. For an Obsidian vault that is the brain itself, start it as `cairn --root <vault> --brain . mcp`, and add `--read-only` to hide the writing tools.

---
name: cairn-notes
description: Read and write notes in a Cairn brain (a second brain kept as plain Markdown in a repo or Obsidian vault). Use when creating or editing ideas, tasks, decisions, notes, journal pages, sessions or feedback, when choosing a note's folder, file name, front matter, status or tags, or when the user mentions Cairn, their brain, the ideas wall, the board or the decision log.
---

# Cairn notes

A Cairn brain is a folder of Markdown files. Cairn, Obsidian, GitHub and AI assistants all read the same files, so a note is correct when its folder, file name and front matter follow the rules below.

## Find the brain first

- In a code project the brain is the `brain/` folder at the project root.
- An Obsidian vault can be the brain itself. Then the type folders (`ideas/`, `tasks/`, ...) sit at the vault root.
- Look for `CONTEXT.md` or `CAIRN-LAYOUT.md` next to the type folders to confirm you are in the right place.
- If your own instructions say the brain is read-only or that you may write only in one folder, follow them. This skill does not override that.

## Layout

| Type | Folder | Statuses (first is the default) |
|---|---|---|
| `idea` | `ideas/` | `raw`, `exploring`, `ready`, `parked` |
| `task` | `tasks/` | `todo`, `doing`, `done` |
| `decision` | `decisions/` | `proposed`, `accepted`, `superseded` |
| `note` | `notes/` | none |
| `journal` | `journal/` | none |
| `session` | `sessions/` | `live`, `shipped`, `partial`, `dropped`, `done` |
| `feedback` | `feedback/` | `new`, `planned`, `addressed`, `wontfix` |

Special files at the brain root, never edited by hand: `CONTEXT.md` (generated brief), `README.md`, `CAIRN-LAYOUT.md` (page order shared by every device).

## Front matter

```markdown
---
title: Ship v1
type: task
status: doing
tags: [release, web]
created: 2026-11-05
source: src/app.py:42
---
Plain Markdown. Link other notes with [[offline-maps]].
```

- `title`, `type` and `created` (YYYY-MM-DD) are always present. `status` is present for types that have statuses.
- `tags` is an inline list without `#`: `tags: [release, web]`. Write `tags: []` when there are none.
- `source` is optional: the `file:line` in the code a task came from.
- Quote a title that contains a colon or starts with a quote: `title: "tradebot: retry the sender"`.
- Keep every field you do not recognise (aliases, plugin data, `publish`, `due`). Cairn preserves unknown fields and so must you.
- `publish: true` marks a note for the user's public brain. Never add or remove it unless asked.

## File names and links

- The file name is the link target: `tasks/ship-v1.md` is linked as `[[ship-v1]]`. The slug is the title in lower case, accents removed, every run of other characters turned into one hyphen, cut to 60 characters. If the slug is taken, add `-2`, `-3`.
- Decisions are numbered in order: `decisions/0001-use-pmtiles.md`, `decisions/0002-...`. Take the next free number.
- Journal pages are one per day: `journal/2026-11-05.md`. A weekly review is `journal/2026-W45.md`.
- Session files start with the date: `sessions/2026-11-18-add-dark-mode.md`.
- Renaming a file breaks every `[[link]]` to it. Do not rename unless asked, and then update the links.

## Body templates

- Idea: `## Why` then `## What if`.
- Decision: `## Context`, `## Options`, `## Decision`, `## Consequences`. An `accepted` decision is binding: do not work against it; propose a new decision that supersedes it instead.
- Journal: `## Log` followed by one line per entry, `- HH:MM text`. A line that created a note links it: `- 10:40 Task: [[ship-v1|Ship v1]]`. Add new lines at the end, never rewrite earlier lines.
- Session: `## Goal` then `## Log`, one line per step as `- HH:MM kind: text`, where kind is `start`, `note`, `prompt`, `pass`, `fail`, `commit`, `skill` or `end`.
- Feedback: optional `from`, `rating` (1 to 5) and `about: "[[session-slug]]"` in the front matter.
- Task, note, feedback: free Markdown. Tasks may hold a checklist (`- [ ] step`), which the board shows as progress.

## One-line capture

A capture line picks the type from its prefix, and `#words` become tags:

| Prefix | Creates |
|---|---|
| `idea:` or no prefix | idea |
| `todo:` or `task:` | task |
| `decision:` or `decide:` | decision |
| `note:` | note |
| `log:` or `journal:` | a line in today's journal |
| `feedback:` or `fb:` | feedback |

Example: `todo: ship v1 #release` becomes `tasks/ship-v1.md` with `status: todo` and `tags: [release]`.

## The work queue

The queue is ordinary tasks tagged `queue`. `auto` means safe to run unattended; `needs-me` means blocked until the user acts (a login, a purchase, a decision only they can make). Never remove `needs-me` yourself.

## Rules that keep several devices in sync

- Change one note at a time and keep the edit small; other devices may be editing the same brain.
- If a note changed since you read it, read it again before saving.
- Do not delete notes. Mark a task `done`, an idea `parked` or a decision `superseded`.

See [references/EXAMPLES.md](references/EXAMPLES.md) for one complete example of each type.

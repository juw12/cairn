---
name: cairn-databases
description: Write and edit Cairn database blocks, the Notion-style live views (table, board, gallery, list, calendar) inside a note that filter and sort other notes by their front matter. Use when the user asks for a dashboard, a board, an overdue list, a calendar or any filtered view of tasks, ideas, decisions or notes in their Cairn brain.
---

# Cairn databases

A database is a fenced code block with the language `database` inside any note. Cairn renders it as a live view of other notes. Every row is still a plain Markdown file: editing a cell changes that note's front matter.

## Block format

One `key: value` per line. Lines starting with `#` or `//` are ignored. Every key is optional.

````markdown
```database
title: Release work
from: tasks
type: task
tag: release
where: status != done
where: due <= today+7
sort: due asc
view: table
group: status
columns: status, tags, due
date: due
limit: 100
```
````

| Key | Meaning | Default |
|---|---|---|
| `title` | Heading shown above the view | none |
| `from` | Only notes in this folder, such as `tasks` | every folder |
| `type` | Note types, comma separated: `idea`, `task`, `decision`, `note`, `journal`, `session`, `feedback` | every type |
| `tag` | Tags the note must have; every one must match | none |
| `where` | One condition per line; all must hold | none |
| `sort` | A property, or `title`, `created`, `updated`, then `asc` or `desc` | none |
| `view` | `table`, `board`, `gallery`, `list` or `calendar` | `table` |
| `group` | Property that makes the board's columns | `status` |
| `columns` | Properties shown next to the title | chosen by Cairn |
| `date` | Property the calendar places notes by | `due`, then `date`, then `created` |
| `limit` | Most rows shown | 200 |

## Conditions

`where:` takes `property operator value`:

- Comparisons: `=`, `!=`, `>`, `<`, `>=`, `<=`
- Text or list contains: `tags contains release`
- Presence: `has due`, `no due`
- Relative dates: `today`, `tomorrow`, `yesterday`, `today+7`, `today-3`. Example: `where: due < today` stays an overdue view every day.

## Examples

Overdue tasks:

````markdown
```database
title: Overdue
type: task
where: status != done
where: due < today
sort: due asc
```
````

Idea wall as a board:

````markdown
```database
title: Ideas
type: idea
view: board
group: status
```
````

Decisions in force:

````markdown
```database
title: Decisions in force
type: decision
where: status = accepted
sort: created desc
view: list
```
````

This week on a calendar:

````markdown
```database
type: task
where: due >= today
where: due <= today+7
view: calendar
date: due
```
````

## Rules

- A property named in `where`, `sort`, `group` or `columns` must exist in the notes' front matter, for example `due: 2026-11-30`. Add the field to the notes if the view needs it.
- Switching the view in the app rewrites the block's `view:` line, so do not treat a changed `view:` as an error.
- Do not put a database block inside another code block or a callout.

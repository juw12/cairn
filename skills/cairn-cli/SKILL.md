---
name: cairn-cli
description: Use the Cairn command line to capture ideas, tasks and decisions, write the daily journal, list notes, find TODO comments, refresh the context pack and publish notes from a Cairn brain. Use when the user asks to add, log, list or publish something in Cairn from a terminal, or when no Cairn MCP tools are available.
---

# Cairn command line

`cairn` works on the brain of the current project. It needs Python 3.13 or newer and has no other dependencies.

## Before using it

1. Run `cairn --version`. If the command is missing, Cairn may be a clone that is not installed: run it as `python -m cairn` from inside the clone, or with `PYTHONPATH=<path to the clone>` set.
2. Choose the brain with the two global options, placed before the command:
   - `--root <folder>`: the project folder (default: the current folder).
   - `--brain <folder>`: the brain folder relative to the root (default: `brain/`). For an Obsidian vault that is the brain itself, use `--root <vault> --brain .`.
3. Every command below except `list`, `todos`, `log` with no text and `context --stdout` changes files. If your instructions say the brain is read-only on this device, use only those.

Pass note text as one quoted argument, never as shell syntax.

## Capture

```bash
cairn add "todo: ship v1 #release"        # tasks/ship-v1.md, status todo, tag release
cairn add "decision: use SQLite"          # decisions/0001-use-sqlite.md, status proposed
cairn add "idea: offline maps"            # ideas/offline-maps.md, status raw
cairn add "note: how the cache works"     # notes/how-the-cache-works.md
```

The prefix picks the type (`idea:`, `todo:` or `task:`, `decision:` or `decide:`, `note:`, `feedback:` or `fb:`); no prefix means an idea. `#words` become tags.

## Journal

```bash
cairn log "fixed the GPX bug #beta"       # a timestamped line in today's journal page
cairn log "todo: book the train"          # creates the task and logs a link to it
cairn log                                 # show today's log
cairn log --day yesterday                 # or --day 2026-11-05
cairn log --days 7                        # the last seven days
```

## Read

```bash
cairn list --type task --status doing     # types: idea task decision note journal session feedback
cairn todos                               # TODO, FIXME and HACK comments in the code, with file:line
cairn context --stdout                    # print the one-page brief without writing it
```

## Maintain

```bash
cairn init                                # create the brain folder layout; never overwrites
cairn context                             # write CONTEXT.md: doing now, up next, decisions in force, open questions
cairn publish --dry-run                   # show which notes marked "publish: true" would go to the public brain
cairn publish --to owner/repo             # copy those notes to the public repo in one commit, with a web page of them (search box, sitemap) at https://owner.github.io/repo/
cairn publish --site folder --url https://example.org/   # the same web page as plain files, for any web host
cairn obsidian <vault folder>             # import an Obsidian vault and keep it in sync both ways (--dry-run to preview)
cairn serve --port 4777                   # open the app in the browser (the default command)
```

## Good habits

- Run `cairn context --stdout` at the start of a piece of work to learn what is in progress and which decisions are binding.
- Log what you finished with `cairn log`, in one plain sentence, as soon as it is done.
- Use `--dry-run` first wherever it exists (`publish`, `obsidian`).
- `cairn publish` sends notes to a public repository. Run it only when the user asks for it.
- Read a command's own help (`cairn <command> --help`) before using an option that is not listed here.

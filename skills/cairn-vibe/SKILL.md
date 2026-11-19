---
name: cairn-vibe
description: Report an AI coding session to Cairn's Vibe page with the cairn vibe commands - start a session, log steps and progress, record test results, and end it with an outcome. Use when working on code in a project that has a Cairn brain and the user wants live progress, or when they mention Vibe, a live session, a progress bar or session feedback.
---

# Cairn Vibe sessions

A session is one Markdown file in the brain's `sessions/` folder. While you work it has `status: live` and a log of timestamped steps; the Cairn app shows it as a live card with a progress bar, time left and an effort badge. Report honestly: the page is evidence the user can open as a file.

These commands write to the brain. If your instructions say the brain is read-only on this device, do not use them.

## The loop

```bash
cairn vibe start "add dark mode" --agent opencode       # opens a live session and prints its slug
cairn vibe log "asked for a theme toggle" --kind prompt # what the user asked
cairn vibe log --progress 40 "wiring the toggle"        # a step, with how far along the work is (0-100)
cairn vibe check -- python -m pytest                    # runs the command and logs pass or fail with its summary line
cairn vibe end --result shipped "toggle works"          # closes it and records commits and lines changed since the start
```

1. **Start** once per goal. Use the goal in the user's words. `--agent` is who is coding (`opencode`, `claude`, `cursor`, ...). `--task ship-v1` links an existing task.
2. **Log** after each real step, in one plain sentence. Kinds for `--kind`: `note` (default), `prompt`, `pass`, `fail`, `commit`, `skill`.
3. **Give progress** with `--progress N` whenever it changes. Without it Cairn has to guess from past sessions.
4. **Check** with `cairn vibe check -- <test command>` instead of running tests separately, so the result is on the record. Do not log `pass` by hand for something you did not run.
5. **End** with the true outcome: `shipped` (done and verified), `partial` (some of it works; say what is left) or `dropped` (abandoned; say why).

## Other commands

```bash
cairn vibe skill /refine "6.5 -> 8.7"     # record a skill run; any log text starting with a slash command counts as one
cairn vibe status                         # the same numbers as the Vibe page, in the terminal
cairn vibe github                         # this month's GitHub Actions minutes as the brain has them (a number sets GitHub's own figure)
cairn feedback "toggle is hard to find on mobile" --rating 3 --from @alice   # feedback on the latest session, rating 1-5
```

## Several sessions at once

`start` prints the session's slug. To keep parallel agents apart, set `CAIRN_SESSION=<slug>` in that terminal, or pass `--session <slug>` to each command. Without either, commands go to the newest live session.

## Automatic detection

`cairn vibe watch` notices OpenCode and Vector sessions without anyone running `start`:

```bash
cairn vibe watch --once          # one pass, then exit
cairn vibe watch                 # keep watching every 60 s (--every 30 to change)
cairn vibe watch --everywhere    # OpenCode sessions from any folder, not only this project
```

The watcher reads only session metadata and message times from OpenCode's local database, never message text. If a watcher is running, do not also `start` a session for the same work: the page would show it twice. The first pass on a computer takes every session its OpenCode still has; later passes look back 14 days (`--days 0` for everything again).

## Several computers in one brain

Every session note names the computer it ran on in `machine:`, so two computers can write sessions into one brain and the Vibe page can show them together or one at a time.

```bash
cairn vibe device            # the name this computer puts on its sessions
cairn vibe device --adopt    # once, on the computer that made them: name older sessions that name no computer
```

- The name is the computer's model. Set `CAIRN_DEVICE="Work laptop"` to give it another, for example when two computers are the same model.
- A watcher only opens and closes the sessions of its own computer. Leave sessions with another `machine:` alone, and never change that field by hand.
- Another computer is only as current as its last sync into the brain, so say "as of its last sync" when you report its numbers.

## What a session file looks like

```markdown
---
title: add dark mode
type: session
status: live
tags: []
created: 2026-11-05
started: 2026-11-05T14:02
machine: ASUS EXPERTBOOK P5405CSA
agent: opencode
---
## Goal

add dark mode

## Log

- 14:02 start: add dark mode
- 14:05 prompt: asked for a theme toggle
- 14:20 note: [40%] wiring the toggle
- 14:41 pass: python -m pytest (31 passed)
- 14:50 end: shipped: toggle works
```

Do not edit a session file by hand while it is live; use the commands so the times and counts stay right.

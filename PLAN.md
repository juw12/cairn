# Cairn: product and build plan

> A second brain that lives inside your repo. Capture, brainstorm, decide and ship,
> stored as plain Markdown next to your code.

## 1. Problem

Project thinking is scattered. Ideas sit in Notion, tasks in GitHub Issues, decisions in
Slack threads, TODOs in code comments, and context in someone's head. The tools that exist
each solve one slice:

| Tool | Good at | Gap for "a brain inside the project" |
|---|---|---|
| Notion | Databases, dashboards | Lives in its cloud, not the repo. Can't be diffed, reviewed or read by tools that read the repo |
| Obsidian | Linking, local Markdown | Knows nothing about the repo: no code TODOs, no git, no project loop. Needs plugin setup per vault |
| Foam (VS Code) | Wikilinks inside VS Code | Editor-locked. No board, no brainstorm flow |
| GitHub Issues/Projects | Task tracking | Heavy for half-formed ideas. Needs network. Not a thinking tool |

**Gap:** nothing gives you one calm place that understands the repo and follows the real
loop of project work: **capture → brainstorm → decide → do → remember**, without lock-in.

## 2. Users

1. **Solo builders / engineers** with many side projects (primary; the author).
2. **Small teams** who want decisions and ideas reviewed in PRs like code.
3. **AI-assisted developers** who want their assistant (Copilot, Claude, Cursor, Codex) to
   read project context from the repo.

## 3. Principles (non-negotiable)

1. **Plain files win.** Everything is Markdown + a tiny frontmatter header in a `brain/` folder.
   Delete Cairn and the brain still reads fine on GitHub, in Obsidian, in VS Code or in `cat`.
2. **Zero install.** Python 3.10 standard library, or just a browser. No Node, no database, no account.
3. **Universal.** Any folder (git or not), any OS, any editor, any AI assistant.
4. **One loop, few screens.** Every feature must serve capture → brainstorm → decide → do → remember.
5. **Local and private.** The server binds to 127.0.0.1. The web version never uploads.

## 4. The loop and the features that serve it

| Step | Feature | Acceptance check |
|---|---|---|
| Capture | **Quick capture bar** on every screen and `cairn add` in the terminal. Prefixes route the item: `idea:`, `todo:`, `decision:`, `note:`, plus `#tags` | Typing `todo: ship v1 #release` creates `brain/tasks/ship-v1.md` with `status: todo`, `tags: [release]` (unit + e2e) |
| Capture | **Code inbox**: scans the repo for `TODO`/`FIXME`/`HACK` comments; one click turns one into a task linked to `file:line` | Fixture repo with 3 TODOs → 3 inbox items; "Make task" writes `source: src/x.py:12` (unit + API) |
| Brainstorm | **Ideas wall**: cards grouped Raw → Exploring → Ready → Parked, drag to move | Dragging a card changes `status` in the file (e2e) |
| Brainstorm | **Spark prompts**: an offline deck of thinking prompts ("What would make this 10× simpler?") inserted into the idea | Prompt appends to the note body (e2e) |
| Decide | **Decisions log** (lightweight ADRs): context / options / decision / consequences template, status proposed → accepted → superseded | New decision has all 4 headings (unit) |
| Do | **Board**: tasks Todo → Doing → Done, drag and drop; "Promote idea to task" keeps a link back | Drag updates `status`; promote creates task with `[[idea]]` link (e2e) |
| Remember | **Notes + journal**: Markdown editor with preview, `[[wikilinks]]`, backlinks, tags, daily journal page | Link to an existing note renders as a link; backlinks list the source (unit + e2e) |
| Remember | **Map**: graph of how ideas, tasks, decisions and notes link | Node count = note count; edges = resolved links (unit on edge builder) |
| Remember | **Context pack**: writes `brain/CONTEXT.md`, a compact brief (goals, open tasks, accepted decisions, active ideas) for teammates and AI assistants. Copy button too | Output contains every open task and accepted decision, under 200 lines on the demo brain (unit) |
| All | **First-run guide**: an empty brain shows 3 steps (capture an idea → move it on the wall → promote it to a task) and a "Load sample brain" button | Fresh demo shows the guide; finishing step 1 ticks it (e2e) |
| All | **Search + command palette** (Ctrl/⌘+K) across titles, bodies and commands | Searching a body word finds the note (e2e) |
| All | **Git awareness** (local server only): shows changed brain files, one-click "Commit brain" | Temp git repo: commit creates a commit touching only `brain/` (API test, skipped if git missing) |

## 5. Data format (the contract)

```
your-repo/
  brain/
    ideas/      offline-sync.md
    tasks/      ship-v1.md
    decisions/  0001-use-sqlite.md
    notes/      architecture.md
    journal/    2026-11-05.md
    CONTEXT.md  (generated)
    README.md   (explains the folder to humans who don't use Cairn)
```

```markdown
---
title: Ship v1
type: task            # idea | task | decision | note | journal
status: doing         # per type, see below
tags: [release, web]
created: 2026-11-05
source: src/app.py:42 # optional: code location
---
Body in plain Markdown. Link with [[offline-sync]].
```

Statuses: idea `raw|exploring|ready|parked` · task `todo|doing|done` ·
decision `proposed|accepted|superseded` · note/journal: none.
Frontmatter is a strict YAML subset (scalars and `[a, b]` lists) so Python and JS parse it
identically without a YAML library. Unknown keys are preserved on save (other tools' fields survive).

File names are `slug.md` from the title (lowercase, `a-z0-9-`, max 60 chars); a clash gets `-2`, `-3`.
Decisions are numbered `0001-slug.md`; journal pages are `YYYY-MM-DD.md`. Because links are
`[[slug]]` = file name, Obsidian and Foam resolve them natively.

Brain folder discovery: `--brain PATH`, else an existing `brain/`, else `.brain/`, else create `brain/`.

## 6. Architecture

```
cairn/ (Python, stdlib only)          docs/ (static web app, no build step)
  core.py    parse/serialize, capture,  core.js   same pure functions (parity-tested)
             links, context pack        store.js  3 adapters behind one interface:
  brain.py   files on disk, code scan,             ServerStore  -> local Python API (full features + git)
             git status/commit                     FolderStore  -> File System Access API (browser only)
  server.py  JSON API + serves docs/                DemoStore    -> sample brain in localStorage
  cli.py     init/add/list/context/serve  app.js    UI (views, palette, drag and drop)
```

Runtimes: **(a)** `python -m cairn` in any folder → full app at `http://127.0.0.1:4777`.
**(b)** GitHub Pages → open a repo folder in Chrome/Edge (read/write, no git), or the demo anywhere.

Conflict safety: every save sends the file's last-seen `mtime`; if the file changed on disk
(e.g. edited in Obsidian) the server refuses and the UI reloads the note instead of overwriting.

## 7. Scope

**MVP (this build):** everything in section 4, light and dark theme, mobile layout, PWA install, CLI.

**Non-goals (on purpose):**
- Real-time multi-user editing (git is the collaboration layer).
- A free-form whiteboard canvas (the Ideas wall + Map cover brainstorming at a fraction of the cost).
- Built-in AI calls (the Context pack feeds whatever assistant you already use, with no API keys).
- Push/pull/merge UI (use your normal git tool; Cairn only commits).
- Plugins.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Python and JS parsers drift | Shared fixtures + parity test in CI |
| Overwriting edits made in another editor | mtime check on save (section 6) |
| Path traversal via the local API | Resolve and reject anything outside `brain/`; bind 127.0.0.1 only |
| File System Access API is Chromium-only | Demo works everywhere; the README says so |
| Code scan slow on big repos | Skip `.git`, `node_modules`, venvs, build dirs and files > 1 MB; cap at 500 items |

## 9. Success metrics

- A new user captures an idea, brainstorms it and moves a task in **under 1 minute** without docs.
- `brain/` renders cleanly on github.com with no Cairn installed.
- All tests green in CI; e2e has zero console errors.

## 10. Test plan

- `tests/test_core.py`: frontmatter round-trip, capture routing, slugs, links/backlinks, context pack.
- `tests/test_server.py`: API CRUD, path traversal refused, mtime conflict, code inbox, git commit.
- `tests/test_cli.py`: init/add/list/context.
- `tests/test_parity.py`: Python vs JS (run in a headless browser) on `tests/fixtures/parity.json`.
- `tests/test_e2e.py`: Playwright on the demo: every view, capture, drag, edit, palette, 390 px and dark mode, console errors = 0.

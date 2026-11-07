<!-- HERO:START -->
<p align="center">
  <a href="https://juw12.github.io/cairn/"><img src="docs/media/banner.jpg" alt="Cairn: a second brain that lives inside your repo." width="100%"></a>
</p>

<p align="center">
  <b>A second brain that lives inside your repo.</b><br>
  Capture ideas, brainstorm, record decisions and move tasks, all as plain Markdown next to your code.
</p>

<p align="center">
  <a href="https://juw12.github.io/cairn/?demo=1"><b>Try it live</b></a> ·
  <a href="#see-it-in-action">See it in action</a> ·
  <a href="#highlights">Highlights</a> ·
  <a href="https://juw12.github.io/p/cairn/">Landing page</a> ·
  <a href="https://github.com/juw12">More by juw12</a>
</p>

<p align="center">
  <a href="https://github.com/juw12/cairn/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/juw12/cairn/tests.yml?branch=main&label=tests" alt="Tests"></a>
  <a href="https://github.com/juw12/cairn"><img src="https://img.shields.io/github/last-commit/juw12/cairn?label=updated&color=0f766e" alt="Last updated"></a>
  <a href="https://juw12.github.io/cairn/?demo=1"><img src="https://img.shields.io/badge/demo-live-2ea44f" alt="Live demo"></a>
  <img src="https://img.shields.io/badge/Web-24292f" alt="Web">
  <img src="https://img.shields.io/badge/Python_CLI-24292f" alt="Python CLI">
  <img src="https://img.shields.io/badge/zero_dependencies-24292f" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/license-MIT-0f766e" alt="MIT license">
</p>

<a id="highlights"></a>
<table>
  <tr>
    <td width="33%" valign="top">
      <b>Plain files, no lock-in</b><br>
      Everything is Markdown in a <code>brain/</code> folder. Delete Cairn and it still reads fine on GitHub, in Obsidian or in VS Code.
    </td>
    <td width="33%" valign="top">
      <b>Knows your code</b><br>
      Turns <code>TODO</code>/<code>FIXME</code> comments into tasks that remember the file and line, and commits the brain in one click.
    </td>
    <td width="33%" valign="top">
      <b>Made for AI assistants too</b><br>
      One click writes <code>brain/CONTEXT.md</code>, a project brief Copilot, Claude, Cursor or ChatGPT can read. No API keys.
    </td>
  </tr>
</table>

## See it in action

<p align="center">
  <a href="https://juw12.github.io/cairn/?demo=1"><img src="docs/media/demo.png" alt="Cairn ideas wall" width="820"></a><br>
  <sub>Open the interactive demo at <a href="https://juw12.github.io/cairn/?demo=1">juw12.github.io/cairn</a></sub>
</p>
<!-- HERO:END -->

## About

**Your project's thinking is scattered.** Ideas sit in Notion, tasks in GitHub Issues, decisions
in Slack threads, TODOs in code comments, and the "why" lives in someone's head. Obsidian and
Notion are great at notes, but neither knows anything about your repo.

Cairn puts one calm place **inside the project**, built around how project work actually moves:

```
capture  →  brainstorm  →  decide  →  do  →  remember
  bar        ideas wall    decision    board    notes, journal,
  + CLI      + sparks      log                  map, context pack
```

| Screen | What it does |
|---|---|
| **Quick capture** | One bar on every screen. `todo: ship v1 #release`, `decision: use SQLite`, `log: fixed the bug` or just text (an idea). It shows where the note will go before you press Enter. |
| **Ideas wall** | Raw → Exploring → Ready → Parked. Drag ideas right as they firm up. **Spark** drops a thinking prompt into an idea; **Promote to task** creates a linked task. |
| **Board** | To do → Doing → Done with drag and drop, checklists and progress bars. |
| **Decisions** | Lightweight decision records (context, options, decision, consequences), numbered and reviewed in PRs like code. |
| **Notes, Journal** | Markdown editor with preview, `[[wikilinks]]`, backlinks, tags and a page per day. |
| **Map** | A live graph of every note: drag notes (they stay pinned), zoom, pinch, hover to light up neighbours with direction arrows, click for an inspector with summary and links in/out. **Shift-click** two notes for the path between them, **Alt-drag** one onto another to write a `[[link]]`, double-click a hollow "unwritten" link to create that note. Local graph (1-3 hops), search, colour by type / folder / cluster / status, tag hubs, orphan filter, a time-lapse of how the brain grew. Canvas-rendered, smooth with thousands of notes, and every note stays reachable by keyboard. |
| **Canvas** | Infinite boards of notes, cards, links and groups joined by arrows, saved as Obsidian-compatible `.canvas` files. |
| **Obsidian sync** | Import an Obsidian vault and keep it in sync both ways (notes and canvases). Files are mirrored as they are; nothing is converted. |
| **Plugins** | Community plugins: install from the list, a GitHub repo or a link. Write your own in one file ([PLUGINS.md](PLUGINS.md)). |
| **Code inbox** | Every `TODO`, `FIXME` and `HACK` comment in the repo. One click makes a task that remembers `file:line`. |
| **Context pack** | A one-page brief (doing now, up next, decisions in force, open questions) saved as `brain/CONTEXT.md` for teammates and AI assistants. |
| **Search** | <kbd>Ctrl</kbd>/<kbd>⌘</kbd>+<kbd>K</kbd> across every note and command. <kbd>C</kbd> jumps to capture. |
| **Autopilot** (Pro) | Type a goal once. A coding agent on your computer (OpenCode) reads the brain for precedents (CONTEXT.md, decisions, similar notes, your `brain/autopilot.md` defaults), then works round after round on its own, without you typing *go* or *continue*, until the goal is done and checked. Every goal is a task on the Board with the agent's progress logged in it. Comments are optional steering; it stops only for hard stops (paying, sending to people, final submit, deleting your data, passwords). |
| **People** | Look up anyone on GitHub and see their proof of work, read from their brain's files: working on, shipped, decisions, open to collaborate. Open their brain read-only and **Ask to join** (opens a prefilled GitHub issue). |

## Cairn Pro: Autopilot

Everything above is free and stays free. **Autopilot** is the one paid feature, and it is how you support Cairn.
Buying and unlocking both happen inside the app; there is no store website.

- **Try it free:** every computer gets one trial goal.
- **Buy in the app:** *Autopilot -> Cairn Pro* shows the plans (30 days or lifetime) and an embedded checkout
  (card or PayPal). Pro turns on the moment the payment completes; the receipt is kept in `~/.cairn/license.json`
  (never in your brain or repo).
- **Pro keys:** a key (`CAIRN1...`) is signed by the seller and checked offline by the app, so it works without
  internet and cannot be made up. Use one to move Pro to another computer: *Autopilot -> Have a Pro key?*
- **What runs where:** Autopilot drives [OpenCode](https://opencode.ai) on your own machine with whatever model you
  configured there. The only outside call Cairn makes for Pro is the checkout itself, when you choose to buy.
- **Make it yours:** add `brain/autopilot.md` with your standing defaults ("big files go to E:", "always add tests",
  "publish to GitHub when done") and Autopilot follows them on every goal.

**Selling it (maintainer):** `cairn pro keygen --install` makes the signing key once (private half stays in
`~/.cairn/seller/`, back it up), `cairn pro issue --email buyer@x.com --plan lifetime` prints a key, prices live in
`PLANS` in `cairn/pro.py`, and `cairn pro sell --paypal-client-id <id>` (the public id of a PayPal REST app) switches
the in-app checkout on in one step.

Cairn is MIT-licensed, so you can read exactly what Autopilot and Pro do (`cairn/autopilot.py`, `cairn/pro.py`). The
lock is honest, not DRM: Pro buys you the convenience, updates and the knowledge that the project keeps going.

## Use it

There are four ways to run it. All of them read and write the same plain files.

**0. Desktop app for Windows (easiest).** [Download `Cairn-Setup.cmd`](https://juw12.github.io/cairn/Cairn-Setup.cmd)
and double-click it, or paste this in PowerShell: `irm https://juw12.github.io/cairn/install.ps1 | iex`.
No admin rights needed. It installs whatever is missing (Python, Git, the window and tray libraries), then opens
Cairn in its own window. After that, everything happens inside the app:

- **Your notes open by themselves.** The first time, Cairn opens the Obsidian vault you used last (or a fresh
  brain in `Documents\Cairn`). **Switch brain** opens another folder, **gets a repo from GitHub** (private
  ones too; GitHub's own sign-in window appears once), or starts a new brain.
- **One Sync button.** It saves your edits, gets everyone else's and sends yours (commit + pull + push). It also
  runs on its own every 10 minutes. If two people changed the same lines, nothing is lost: your edits stay
  committed locally and Cairn tells you. Behind an office proxy, Sync tries every way out it can find (git's own
  setting, the Windows proxy, `HTTPS_PROXY`, signing in to the proxy as you) and remembers the one that works.
- **Your public brain stays current.** In Settings, name your public repo (e.g. `you/brain`) and turn on
  *Publish automatically*: after each sync, notes marked `publish: true` are published, but only when one of
  them changed.
- **Always there.** Closing the window keeps Cairn in the tray and it starts with Windows.
- **Updates itself, no restarts.** Cairn checks for a new version every 20 minutes. Changes to the app's
  screens are swapped in with a quick reload the moment you pause (your open note stays open); the rarer
  changes to its engine restart Cairn quietly while it's in the tray, or after a couple of idle minutes,
  and bring the window back where you were. **Settings** turns each of these on or off.

**1. Local app from the terminal.** Python 3.10+, no dependencies:

```bash
git clone https://github.com/juw12/cairn
cd your-project
python -m cairn --root .          # run from the cairn folder, or `pip install ./cairn` and run `cairn`
```

It opens `http://127.0.0.1:4777` with code scanning and one-click **Commit brain** (commits only `brain/`; push with your usual git tool).

**2. In the browser, on your real repo.** Open [juw12.github.io/cairn](https://juw12.github.io/cairn/), choose
**Open a repo folder**, and pick your project. Chrome and Edge on desktop can read and write the folder directly
(File System Access API). Nothing is uploaded.

**3. Demo.** [Try the sample project](https://juw12.github.io/cairn/?demo=1) (an offline hiking app). It's saved only in your browser.

### Share your brain and explore others

Cairn uses GitHub as the network, so there is nothing new to sign up for.

1. Keep `brain/` in a **public** repo and add the topic `cairn-brain` (repo page → About → Topics).
2. Share your profile link: `https://juw12.github.io/cairn/?u=<your-username>`, or a brain directly:
   `https://juw12.github.io/cairn/?brain=<owner>/<repo>`.

This repo dogfoods it: see [@juw12's profile](https://juw12.github.io/cairn/?u=juw12) or
[Cairn's own brain](https://juw12.github.io/cairn/?brain=juw12/cairn). Only repos with the topic ever appear;
other people's brains are always read-only. Unsigned GitHub API calls are limited to 60 per hour per IP, so
results are cached for 10 minutes.

### Show proof of work

Write a claim as a note with a `claim:` line and links to the work under `evidence:` (repos, merged
pull requests, commits, releases). The **Proof** view and everyone's profile check each link on
GitHub and show a level nobody can type in: L1 claimed, L2 backed by work you own, L3 used by others
(merged into someone else's repo, forked or starred), L4 vouched (another person committed a vouch
file in their own repo; the **Vouch** button prefills it). Keep your brain private and publish only
the notes marked `publish: true`:

```sh
python -m cairn publish --to YOUR-NAME/brain --source "path/to/your/vault"
```

The full format is in [PROOF.md](PROOF.md).

### Edit your brain from any device

Sign in with GitHub on the web app and your brain repo becomes editable from your phone, a borrowed laptop or
any browser. Every save is a commit to `brain/` in your repo; there is still no Cairn server or database.

1. Open [juw12.github.io/cairn](https://juw12.github.io/cairn/) → **Sign in with GitHub**.
2. Paste a token. The dialog links to a [pre-filled fine-grained token](https://github.com/settings/personal-access-tokens/new?name=Cairn&expires_in=90&contents=write)
   (name, 90-day expiry and *Contents: Read and write* filled in; you pick *Only select repositories* and your
   brain repo). That is the narrowest access. A [classic token with `public_repo`](https://github.com/settings/tokens/new?scopes=public_repo&description=Cairn)
   also lets you create new brains from the People page.
3. You land in your brain (the one you last edited, or your only one). Edit as usual: typing is batched, so a
   burst of edits becomes one commit.

If the same note was changed somewhere else, Cairn shows the newer version and keeps yours next to it
(*Use my version*, *Copy my text* or *Keep the newer one*). Failed saves (offline, expired sign-in) keep your text
the same way. Repos you can't push to stay read-only.

Or click **Continue with GitHub**: approve on github.com with a short code, no token to copy. GitHub's OAuth
endpoints don't allow browser calls, so this goes through a tiny Cloudflare Worker that holds no secret and
forwards only those two requests: see [`worker/README.md`](worker/README.md).

### Use it with your Obsidian vault

Cairn opens an Obsidian vault as it is: no import, no copy, no `brain/` folder needed. A folder with
`.obsidian/` in it (or a GitHub repo of notes) is used directly, `.obsidian/`, `.trash/` and other
hidden folders are skipped, and links resolve the way Obsidian resolves them: `[[folder/note|alias]]`,
`[[note#heading]]`, `![[embeds]]`, `[text](note.md)` and wiki links in properties.

- **On this computer:** `python -m cairn --root "path/to/your vault" serve`, or *Choose a folder* in the browser.
- **On any device:** keep the vault in a GitHub repo (the Obsidian Git plugin can push and pull it),
  sign in and open `you/your-vault`.

Edits only change what you changed: properties keep their order, comments and list style, and files
keep their line endings, so Obsidian and git see clean one-line diffs.

The Map goes further than Obsidian's graph: filters, colour by type, folder, cluster or tag, local maps
of depth 1 to 3, the shortest path between two notes, keyboard navigation (arrow keys walk the links,
Enter opens, L for local), hubs, orphans and islands at a glance, and *suggested links*: notes that
mention another note's title without linking it, linked in one click. **Alt-drag** one note onto another to
write the link, double-click a missing note (a link to a note that doesn't exist yet) to create it, and
*Watch it grow* replays the map in the order your notes were written. Daily notes named `2026-11-05.md`
show up in Journal, wherever they live in the vault.

### Writing notes

The editor speaks Obsidian-flavoured Markdown, so a vault reads the same in both apps:

- **Markdown:** callouts (`> [!tip]`, foldable with `+`/`-`), `==highlights==`, task lists you can tick in
  the preview, nested lists, tables, footnotes, `%%comments%%`, inline `#tags` (click one to filter Notes),
  images and attachments (`![[diagram.png|300]]`, PDFs) and note embeds (`![[note#Heading]]`).
- **Write, Split or Preview**, with a live word count. In the editor, type `[[` for link suggestions (add `#` to
  pick a heading); Enter continues lists; Ctrl+B, Ctrl+I and Ctrl+E (toggle preview) work as you'd expect.
- **Under every note:** an outline of its headings, backlinks, links out, and *unlinked mentions* you can turn
  into links in one click. Hover any link for a preview of the note.
- **Properties:** edit, add or remove any front-matter key, not just the built-in ones.
- **Rename** a note and every link to it is rewritten. Links to missing notes are shown dimmed; click one to create it.
- **Find things:** Notes has a list or a folder tree, a ★ Bookmarks filter, and search operators: `tag:release`,
  `path:ideas/`, `file:`, `type:`, `status:`, `line:`, `"exact phrase"` and `-exclude`.
- **Templates:** notes in `Templates/` (or `_templates/`) appear in the command palette (Ctrl+K), as a new note or
  inserted into the open one; `{{title}}`, `{{date:YYYY-MM-DD}}` and `{{time}}` are filled in. The palette also
  has *Random note* and *Rename*.
- **First run:** a short guided tour points at each part of the app. Skip it any time; replay it with the **?**
  button at the bottom of the sidebar or *Take the tour* in the palette.

- **Maths and diagrams:** `$E=mc^2$` and `$$…$$` are typeset with KaTeX, and ```` ```mermaid ```` blocks become
  diagrams. Both libraries load only when a note uses them (pinned versions, integrity-checked); offline, the note
  shows the source instead.

- **Canvas:** an infinite board for laying out notes, cards, links and groups and drawing arrows between them.
  Double-click to add a card, drag a dot on a card's edge to connect, colour, undo, zoom and pan with mouse, keys
  or touch. Saved as `.canvas` files in the open JSON Canvas format, so boards open in Obsidian and sync with your
  vault both ways; fields Cairn doesn't know are kept.
- **Tabs:** keep several notes open. Ctrl/⌘+click or middle-click any note to open it in a new tab; drag tabs to
  reorder, Ctrl+Alt+←/→ to switch, Ctrl+Alt+W to close, Ctrl+Alt+T to reopen. Tabs are remembered per brain.
- **Community plugins:** turn them on under **Plugins**, then install from the built-in list (Note queries,
  Calendar, Writing stats, Recent files), from any GitHub repo (`owner/repo`) or from a link. A plugin is one
  JavaScript file that can add commands, sidebar pages, ```` ```lang ```` block renderers and event hooks; see
  [PLUGINS.md](PLUGINS.md). Plugins live on your device, never in a shared brain. Obsidian's own plugins don't run
  here (they need Obsidian's API), but Cairn's API mirrors theirs, so porting is usually quick.

### Keep a separate brain in sync with a vault

When your notes live in a project's `brain/` folder (or a GitHub repo) and you also keep an Obsidian vault,
open **Obsidian sync** in the sidebar or run `cairn obsidian ~/path/to/vault`. Cairn copies the vault into
`brain/obsidian/` and from then on keeps both sides in sync: edit a note in either app and the next sync
copies it over. (If Cairn opened the vault itself, there is nothing to sync and the page says so.)

* **Local app:** paste the vault path; it syncs every 20 s while Cairn is open. `cairn obsidian --watch 10`
  keeps syncing with the app closed. `--folder .` mirrors the vault into `brain/` itself (`--folder ""` too,
  except in PowerShell, which drops empty arguments); `--dry-run` shows what would be copied.
* **Browser, Chrome/Edge:** pick the vault folder; two-way sync while the tab is open, nothing uploaded.
* **Other browsers:** one-way import; import again to bring in changes.

Nothing is ever lost: a note deleted in Cairn goes to the vault's `.trash/`, one deleted in Obsidian goes to
`brain/.cairn/trash/`, and if both sides changed a note the newer wins and the other is kept as
`Name (conflict 2026-11-06).md`. A vault that suddenly looks empty (unplugged drive) stops the sync instead
of deleting anything. Only Markdown is synced; attachments and `.obsidian/` settings stay in the vault.

Switch the Map between **2D and 3D** with the toggle in its corner (or press 2 / 3). The 3D map lays your
notes out in space with perspective, depth and lit spheres; drag to turn it a full 360° any way, right-drag
or Shift-drag to pan, scroll or pinch to zoom, R for auto-rotate. Selection, local maps, paths, filters
and suggested links work the same in both, and Cairn remembers which one you like.

### Command line

```bash
cairn init                               # create brain/ (never overwrites)
cairn add "todo: ship v1 #release"       # capture from the terminal
cairn add "decision: use SQLite"         # → brain/decisions/0001-use-sqlite.md
cairn list --type task --status doing
cairn todos                              # TODO/FIXME/HACK comments in the code
cairn context                            # write brain/CONTEXT.md
cairn obsidian ~/Documents/MyVault       # import a vault into brain/obsidian/ and sync both ways
cairn obsidian --watch 10                # keep syncing every 10 s (--dry-run to preview)
cairn serve --port 4777                  # the app (default command)
```

## The data format

```
your-repo/
  brain/
    ideas/      offline-maps.md
    tasks/      ship-v1.md
    decisions/  0001-use-pmtiles.md
    notes/      architecture.md
    journal/    2026-11-05.md
    CONTEXT.md  (generated)
    README.md   (explains the folder to people who don't use Cairn)
```

```markdown
---
title: Ship v1
type: task            # idea | task | decision | note | journal
status: doing         # idea: raw/exploring/ready/parked · task: todo/doing/done · decision: proposed/accepted/superseded
tags: [release, web]
created: 2026-11-05
source: src/app.py:42 # optional: where in the code this came from
---
Plain Markdown. Link other notes with [[offline-maps]].
```

* File names are the links, so `[[offline-maps]]` resolves in **Obsidian** and **Foam** too. Open `brain/` as an Obsidian vault if you like.
* Fields Cairn doesn't know (aliases, other plugins' data) are preserved when it saves.
* If a note changed in another editor since you opened it, Cairn loads the newer version instead of overwriting it.

### Make your AI assistant use it

Add one line to `AGENTS.md`, `CLAUDE.md` or `.github/copilot-instructions.md`:

```
Read brain/CONTEXT.md before starting. Decisions in brain/decisions/ marked "accepted" are binding.
```

## Why

| Tool | Great at | Missing for "a brain inside the project" |
|---|---|---|
| Notion | Databases, dashboards | Lives in its cloud: not diffable, not reviewable, invisible to tools that read the repo |
| Obsidian | Linking local Markdown | Knows nothing about the repo: no code TODOs, no git, no project flow without plugin setup. Cairn syncs with your vault, so you can keep using both |
| Foam (VS Code) | Wikilinks in the editor | Editor-locked; no board or brainstorm flow |
| GitHub Issues/Projects | Tracking work | Heavy for half-formed ideas; needs network; not a thinking tool |

Cairn doesn't replace them. It's the thin layer that keeps a project's ideas, decisions and next steps
**with the code**, in a format all of those tools (and your AI assistant) can read.

## Privacy and security

* No Cairn account and no telemetry. Fonts (SIL OFL) and icons are bundled.
* The only external requests are to `api.github.com` and `raw.githubusercontent.com`, and only when you look someone up, open a published brain or sign in. A local brain never leaves your machine.
* Signing in is optional. Your token is kept in this tab only (`sessionStorage`) unless you tick *Remember on this device*; it is sent only to `api.github.com` (tested), and *Sign out* deletes it. With the optional Worker, the device-flow token passes through your own Worker once on its way back to the browser.
* The local server binds to `127.0.0.1` only, checks the `Host` header and requires a custom header on every API call, so other websites can't reach it.
* It reads and writes only `.md` files inside the brain folder; paths that escape it are refused (tested).

## Development

```bash
python -m unittest discover -s tests          # core, API, CLI, parity, e2e, Obsidian sync, map, sign-in, Worker
pip install playwright && playwright install chromium   # for the parity and e2e tests
python scripts/make_media.py                  # regenerate the banner and screenshot
python scripts/live_github_check.py           # sign-in + edits against the real GitHub API on a throwaway repo
```

The web app in `docs/` is plain HTML, CSS and ES modules with no build step. `cairn/core.py` and
`docs/core.js` implement the same rules and are checked against each other by `tests/test_parity.py`.
Design rationale (type, colour and the psychology behind it): [`plan/DESIGN.md`](plan/DESIGN.md).
How it was planned and scored: [`PLAN.md`](PLAN.md), [`plan/rubric.md`](plan/rubric.md), [`plan/refine_log.md`](plan/refine_log.md).

## License

[MIT](LICENSE) © Juwen Lie. An independent personal project.

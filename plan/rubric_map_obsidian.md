# Rubric: step 3, a Map better than Obsidian's graph + Obsidian vaults as Cairn brains (FROZEN)

Goal: (1) Cairn's Map does everything Obsidian's graph view does, plus things it cannot; (2) an existing Obsidian
vault can be opened by Cairn directly (folder on disk, or its GitHub repo from any device) and edited by both apps
without either damaging the other's files. No copying, so nothing drifts.

Baseline facts (measured 2026-11-06 on a scratch copy of the owner's vault, 270 notes; original never touched):
- 96/270 notes (35%) would have their frontmatter reformatted on first save; 242/270 use CRLF and would be
  converted to LF on save.
- Cairn finds 111 links and 143 orphans; all 270 notes show as type "note".
- Obsidian graph view (help.obsidian.md/Plugins/Graph+view): filters (search, tags, attachments, existing files
  only, orphans), groups (colour by query), display (arrows, text fade, node size, link thickness, animate), forces
  (center, repel, link force, link distance), local graph with depth. No unlinked-mention edges, no path finding,
  no keyboard navigation of nodes.

## Hard gates (all must pass; checked by tools/tests, not opinion)

| # | Gate | Check |
|---|------|-------|
| V1 | Lossless round-trip: editing only the body of any vault note leaves its frontmatter bytes and line endings unchanged; changing one property rewrites only that property's line(s). | script over all 270 notes of the vault copy + unit tests |
| V2 | Links resolve like Obsidian: `[[name]]`, `[[folder/name]]`, `[[name\|alias]]`, `[[name#heading]]`, `[text](path.md)`; duplicates prefer same folder; embeds of non-notes are attachments, not broken links. Cairn's note-to-note edges equal an independent Obsidian-style resolver's on the vault copy. | script + unit tests |
| V3 | Python core and JS core agree (graph, links, lossless update) on fixtures and on the vault copy. | parity test |
| V4 | Vault folders open as brains: `.obsidian/`, `.git/`, `.trash/` ignored; notes at any depth load; GitHub repos without a `brain/` folder open with the repo root as the brain. | tests (folder, server, GitHub mock) |
| V5 | The owner's real vault is never modified by this work: `git status` of the vault is identical before and after. | before/after snapshot |
| M1 | Map has Obsidian parity: live force layout with draggable nodes; search, type, folder, tag, orphan and attachment filters; colour by type/folder/tag; local graph with depth 1-3; arrows and node size by links. | e2e tests |
| M2 | Map has what Obsidian lacks, each usable from the Map: suggested links (unlinked mentions drawn as dashed edges, one click writes the `[[link]]`), path between two notes, keyboard navigation (arrows move between linked notes, Enter opens), an insight strip (hubs, orphans, clusters). | e2e tests |
| M3 | Performs on the real vault: 270 notes render and settle in under 3 s; median simulation tick under 8 ms. | measured in browser on the vault copy |
| M4 | Existing gates still hold: labels never overlap, no sideways scroll at 320 px, light/dark, no console errors; all previous tests pass. | test suite |

## Soft criteria (0-10)

| Criterion | Weight | 4 | 7 | 10 |
|---|---|---|---|---|
| S1 Map beats Obsidian | 0.30 | Parity on paper only | Parity plus one real extra | Parity plus every M2 extra, each obviously useful on a real 270-note vault |
| S2 Sync story | 0.20 | Needs copying/exporting | Opens the vault locally | Same files everywhere: local folder, GitHub from any device, Obsidian side by side; clear, honest steps for the owner |
| S3 Safety | 0.20 | Could rewrite notes | Lossless for common cases | Provably lossless on the real vault, conflict-safe, nothing pushed without the owner's go |
| S4 Design | 0.15 | Cluttered | Clean, on-brand | Calm at 270+ nodes, readable labels, controls discoverable, works on phone |
| S5 Verification | 0.15 | A few tests | Each gate tested | Gates tested on fixtures and the real vault copy, plus an honest not-verified list |

**Done** = all gates pass AND weighted score >= 8.5. Max 5 rounds; stop after 2 rounds without a kept improvement.
Judge: self-judged unless an independent judge is available (label it).

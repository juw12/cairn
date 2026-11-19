# Cairn note examples

One complete, valid file of each type. Paths are relative to the brain folder.

## ideas/offline-maps.md

```markdown
---
title: Offline maps
type: idea
status: exploring
tags: [maps]
created: 2026-11-05
---
## Why

Hikers lose signal exactly where they need the map.

## What if

We ship one region as a PMTiles file and render it locally?
```

## tasks/ship-v1.md

```markdown
---
title: Ship v1
type: task
status: doing
tags: [release, queue, auto]
created: 2026-11-05
source: src/app.py:42
---
- [x] Freeze the feature list
- [ ] Write release notes
- [ ] Tag the release

Comes from [[offline-maps]].
```

## decisions/0001-use-pmtiles.md

```markdown
---
title: Use PMTiles for offline maps
type: decision
status: accepted
tags: [maps, storage]
created: 2026-11-05
---
## Context

The app must show maps with no connection.

## Options

1. MBTiles served by a local process.
2. PMTiles read straight from one file.

## Decision

PMTiles: one file, no server.

## Consequences

Region files are larger; updates replace the whole file.
```

## notes/architecture.md

```markdown
---
title: Architecture
type: note
tags: [overview]
created: 2026-11-05
---
The app has three parts: capture, storage and the map view. See [[0001-use-pmtiles]].
```

## journal/2026-11-05.md

```markdown
---
title: 2026-11-05
type: journal
tags: []
created: 2026-11-05
---
## Log

- 09:12 fixed the GPX bug #beta
- 10:40 Task: [[ship-v1|Ship v1]]
- 16:05 Decision: [[0001-use-pmtiles|Use PMTiles for offline maps]]
```

## sessions/2026-11-05-add-dark-mode.md

```markdown
---
title: add dark mode
type: session
status: live
tags: []
created: 2026-11-05
started: 2026-11-05T14:02
agent: opencode
---
## Goal

Add a theme toggle that remembers the choice.

## Log

- 14:02 start: add dark mode
- 14:05 prompt: asked for a theme toggle
- 14:20 note: [40%] wiring the toggle
- 14:41 pass: python -m pytest (31 passed)
- 14:50 end: shipped: toggle works and remembers the choice
```

## feedback/toggle-is-hard-to-find-on-mobile.md

```markdown
---
title: Toggle is hard to find on mobile
type: feedback
status: new
tags: []
created: 2026-11-05
from: "@alice"
rating: 3
about: "[[2026-11-05-add-dark-mode]]"
---
The theme toggle sits below the fold on small screens.
```

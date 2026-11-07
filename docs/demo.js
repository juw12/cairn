// Sample brain for the demo: an offline hiking-route app called Trailmix.
// Generated from tests/fixtures/demo_brain.json and tests/fixtures/demo_canvases/ by scripts/build_demo.py; edit those, not this file.

export const DEMO_FILES = [
  {
    "path": "ideas/offline-maps.md",
    "text": "---\ntitle: Offline maps for the whole route\ntype: idea\nstatus: exploring\ntags: [maps, offline]\ncreated: 2026-10-28\n---\n## Why\n\nHalf our interviewees lose signal on the trail (see [[user-interviews]]). If the map dies, the app is useless.\n\n## What if\n\n- Download a corridor 2 km around the planned route, not the whole region\n- Use vector tiles so a 40 km route stays under 30 MB\n- Decided format in [[0001-use-pmtiles-for-offline-maps]]\n\n### Spark: What is the smallest version we could test this week?\n\nPre-download one popular trail and ship it to five beta hikers.\n"
  },
  {
    "path": "ideas/weather-overlay.md",
    "text": "---\ntitle: Weather overlay on the elevation profile\ntype: idea\nstatus: ready\ntags: [weather, ux]\ncreated: 2026-10-30\n---\nShow wind and rain along the profile, so you see *where* on the route the weather turns, not just *when*.\n\nDepends on [[elevation-profile-chart]].\n"
  },
  {
    "path": "ideas/share-trip-link.md",
    "text": "---\ntitle: Share a live trip link with family\ntype: idea\nstatus: raw\ntags: [safety]\ncreated: 2026-11-03\n---\nA link that shows last known position and planned finish time. No account for the viewer.\n"
  },
  {
    "path": "ideas/voice-notes-on-trail.md",
    "text": "---\ntitle: Voice notes pinned to the map\ntype: idea\nstatus: raw\ntags: [capture]\ncreated: 2026-11-04\n---\nHold the volume button to record a note at your current position: \"water source dry\", \"great camp spot\".\n"
  },
  {
    "path": "ideas/ar-peak-finder.md",
    "text": "---\ntitle: AR peak finder\ntype: idea\nstatus: parked\ntags: [ar]\ncreated: 2026-10-12\n---\nPoint the camera at the skyline and label the peaks. Fun, but not why people install a route app. Parked until after v1.\n"
  },
  {
    "path": "tasks/ship-v1-beta.md",
    "text": "---\ntitle: Ship v1 beta to 20 hikers\ntype: task\nstatus: doing\ntags: [release]\ncreated: 2026-10-25\n---\n- [x] TestFlight build\n- [x] Play Store internal track\n- [ ] Invite list from [[user-interviews]]\n- [ ] Feedback form\n"
  },
  {
    "path": "tasks/gpx-import.md",
    "text": "---\ntitle: GPX import handles multi-segment tracks\ntype: task\nstatus: doing\ntags: [import]\ncreated: 2026-11-01\nsource: src/import/gpx.py:42\n---\nStrava exports split a track into segments when you pause. We only read the first one.\n"
  },
  {
    "path": "tasks/elevation-profile-chart.md",
    "text": "---\ntitle: Elevation profile chart\ntype: task\nstatus: todo\ntags: [ux]\ncreated: 2026-11-02\n---\nDraw the profile under the map; tap a point to highlight it on the route.\n"
  },
  {
    "path": "tasks/onboarding-copy.md",
    "text": "---\ntitle: Write onboarding copy\ntype: task\nstatus: todo\ntags: [ux, copy]\ncreated: 2026-11-04\n---\nThree screens max. Lead with \"works without signal\".\n"
  },
  {
    "path": "tasks/set-up-ci.md",
    "text": "---\ntitle: Set up CI for tests\ntype: task\nstatus: done\ntags: [infra]\ncreated: 2026-10-20\n---\nGitHub Actions runs the test suite on every push.\n"
  },
  {
    "path": "tasks/dark-mode.md",
    "text": "---\ntitle: Dark mode\ntype: task\nstatus: done\ntags: [ux]\ncreated: 2026-10-22\n---\nFollows the system setting.\n"
  },
  {
    "path": "decisions/0001-use-pmtiles-for-offline-maps.md",
    "text": "---\ntitle: Use PMTiles for offline maps\ntype: decision\nstatus: accepted\ntags: [maps, offline]\ncreated: 2026-10-29\n---\n## Context\n\nWe need offline maps along a route ([[offline-maps]]) without running a tile server.\n\n## Options\n\n1. Raster MBTiles: simple, but 5x larger\n2. PMTiles vector tiles: one static file, range requests, small\n3. Mapbox offline packs: easy, but per-user cost and lock-in\n\n## Decision\n\nPMTiles, served as static files from object storage.\n\n## Consequences\n\nNeed a style for vector rendering; no per-user cost; works with any CDN.\n"
  },
  {
    "path": "decisions/0002-sqlite-for-local-storage.md",
    "text": "---\ntitle: SQLite for on-device storage\ntype: decision\nstatus: accepted\ntags: [storage]\ncreated: 2026-10-24\n---\n## Context\n\nRoutes, tracks and notes must work offline and sync later.\n\n## Options\n\n1. SQLite\n2. A JSON file per route\n3. IndexedDB only (web)\n\n## Decision\n\nSQLite on mobile, wrapped behind one storage interface.\n\n## Consequences\n\nMigrations need a version table from day one.\n"
  },
  {
    "path": "decisions/0003-no-accounts-in-v1.md",
    "text": "---\ntitle: No accounts in v1\ntype: decision\nstatus: proposed\ntags: [product, privacy]\ncreated: 2026-11-04\n---\n## Context\n\nAccounts add sign-up friction and a privacy burden. [[share-trip-link]] would need some identity.\n\n## Options\n\n1. No accounts; share links are random URLs\n2. Email magic link\n3. Sign in with Apple/Google\n\n## Decision\n\nProposed: option 1 for v1. Revisit when sync ships.\n\n## Consequences\n\nNo cross-device sync in v1.\n"
  },
  {
    "path": "notes/architecture.md",
    "text": "---\ntitle: Architecture overview\ntype: note\ntags: [architecture]\ncreated: 2026-10-20\n---\n# Architecture overview\n\n- **App**: React Native, one codebase for iOS and Android\n- **Maps**: vector tiles, see [[0001-use-pmtiles-for-offline-maps]]\n- **Storage**: [[0002-sqlite-for-local-storage]]\n- **Import**: GPX and FIT parsers in `src/import/`\n\n```\napp/ -> storage/ -> sqlite\n     -> maps/    -> pmtiles (static files)\n```\n"
  },
  {
    "path": "notes/user-interviews.md",
    "text": "---\ntitle: User interviews (October)\ntype: note\ntags: [research]\ncreated: 2026-10-18\n---\nTalked to 9 hikers.\n\n- 5 of 9 lost signal on their last hike, and 3 of them turned back early because of it\n- Everyone screenshots maps \"just in case\"\n- 4 want to share their route with someone at home\n\nLeads to [[offline-maps]] and [[share-trip-link]].\n"
  },
  {
    "path": "notes/competitor-scan.md",
    "text": "---\ntitle: Competitor scan\ntype: note\ntags: [research]\ncreated: 2026-10-15\n---\n| App | Offline | Price |\n|---|---|---|\n| AllTrails | Paid tier | $36/yr |\n| Gaia GPS | Paid tier | $40/yr |\n| Komoot | Per region | one-off |\n\nGap: offline by default, free for one route at a time.\n"
  },
  {
    "path": "journal/2026-11-04.md",
    "text": "---\ntitle: 2026-11-04\ntype: journal\ncreated: 2026-11-04\n---\n## Log\n\n- Beta builds are up on both stores\n- Found the GPX segment bug while testing a Strava export\n- Idea: [[voice-notes-on-trail]]\n"
  },
  {
    "path": "canvases/v1 launch plan.canvas",
    "text": "{\n\t\"nodes\": [\n\t\t{\n\t\t\t\"id\": \"g-beta\",\n\t\t\t\"type\": \"group\",\n\t\t\t\"label\": \"Beta must-haves\",\n\t\t\t\"x\": 380,\n\t\t\t\"y\": -40,\n\t\t\t\"width\": 460,\n\t\t\t\"height\": 620,\n\t\t\t\"color\": \"4\"\n\t\t},\n\t\t{\n\t\t\t\"id\": \"t-goal\",\n\t\t\t\"type\": \"text\",\n\t\t\t\"x\": -80,\n\t\t\t\"y\": 0,\n\t\t\t\"width\": 320,\n\t\t\t\"height\": 170,\n\t\t\t\"color\": \"2\",\n\t\t\t\"text\": \"# v1 beta\\nShip to 50 hikers by **Nov 30**.\\nEverything on this board must work with **no signal**.\"\n\t\t},\n\t\t{\n\t\t\t\"id\": \"f-gpx\",\n\t\t\t\"type\": \"file\",\n\t\t\t\"file\": \"tasks/gpx-import.md\",\n\t\t\t\"x\": 410,\n\t\t\t\"y\": 0,\n\t\t\t\"width\": 400,\n\t\t\t\"height\": 160\n\t\t},\n\t\t{\n\t\t\t\"id\": \"f-elev\",\n\t\t\t\"type\": \"file\",\n\t\t\t\"file\": \"tasks/elevation-profile-chart.md\",\n\t\t\t\"x\": 410,\n\t\t\t\"y\": 200,\n\t\t\t\"width\": 400,\n\t\t\t\"height\": 160\n\t\t},\n\t\t{\n\t\t\t\"id\": \"f-onb\",\n\t\t\t\"type\": \"file\",\n\t\t\t\"file\": \"tasks/onboarding-copy.md\",\n\t\t\t\"x\": 410,\n\t\t\t\"y\": 400,\n\t\t\t\"width\": 400,\n\t\t\t\"height\": 150\n\t\t},\n\t\t{\n\t\t\t\"id\": \"f-idea\",\n\t\t\t\"type\": \"file\",\n\t\t\t\"file\": \"ideas/offline-maps.md\",\n\t\t\t\"x\": -80,\n\t\t\t\"y\": 300,\n\t\t\t\"width\": 320,\n\t\t\t\"height\": 240\n\t\t},\n\t\t{\n\t\t\t\"id\": \"f-pmtiles\",\n\t\t\t\"type\": \"file\",\n\t\t\t\"file\": \"decisions/0001-use-pmtiles-for-offline-maps.md\",\n\t\t\t\"x\": -80,\n\t\t\t\"y\": 640,\n\t\t\t\"width\": 320,\n\t\t\t\"height\": 200\n\t\t},\n\t\t{\n\t\t\t\"id\": \"t-questions\",\n\t\t\t\"type\": \"text\",\n\t\t\t\"x\": 410,\n\t\t\t\"y\": 660,\n\t\t\t\"width\": 400,\n\t\t\t\"height\": 170,\n\t\t\t\"text\": \"## Open questions\\n- Corridor width on switchbacks?\\n- Do we need accounts? See [[0003-no-accounts-in-v1]]\\n- Resume interrupted downloads #offline\"\n\t\t},\n\t\t{\n\t\t\t\"id\": \"l-pmtiles\",\n\t\t\t\"type\": \"link\",\n\t\t\t\"url\": \"https://docs.protomaps.com/pmtiles/\",\n\t\t\t\"x\": 920,\n\t\t\t\"y\": 0,\n\t\t\t\"width\": 300,\n\t\t\t\"height\": 110\n\t\t}\n\t],\n\t\"edges\": [\n\t\t{\n\t\t\t\"id\": \"e1\",\n\t\t\t\"fromNode\": \"t-goal\",\n\t\t\t\"fromSide\": \"right\",\n\t\t\t\"toNode\": \"g-beta\",\n\t\t\t\"toSide\": \"left\",\n\t\t\t\"label\": \"needs\"\n\t\t},\n\t\t{\n\t\t\t\"id\": \"e2\",\n\t\t\t\"fromNode\": \"f-idea\",\n\t\t\t\"fromSide\": \"bottom\",\n\t\t\t\"toNode\": \"f-pmtiles\",\n\t\t\t\"toSide\": \"top\",\n\t\t\t\"label\": \"decided by\"\n\t\t},\n\t\t{\n\t\t\t\"id\": \"e3\",\n\t\t\t\"fromNode\": \"f-pmtiles\",\n\t\t\t\"fromSide\": \"right\",\n\t\t\t\"toNode\": \"t-questions\",\n\t\t\t\"toSide\": \"left\",\n\t\t\t\"color\": \"1\"\n\t\t},\n\t\t{\n\t\t\t\"id\": \"e4\",\n\t\t\t\"fromNode\": \"f-gpx\",\n\t\t\t\"fromSide\": \"right\",\n\t\t\t\"toNode\": \"l-pmtiles\",\n\t\t\t\"toSide\": \"left\",\n\t\t\t\"label\": \"format docs\"\n\t\t}\n\t]\n}\n"
  }
];

export const DEMO_TODOS = [
  {
    "path": "src/import/gpx.py",
    "line": 42,
    "tag": "TODO",
    "text": "read every <trkseg>, not just the first"
  },
  {
    "path": "src/maps/download.ts",
    "line": 17,
    "tag": "FIXME",
    "text": "corridor width ignores switchbacks"
  },
  {
    "path": "src/maps/download.ts",
    "line": 88,
    "tag": "TODO",
    "text": "resume interrupted downloads"
  },
  {
    "path": "src/storage/migrations.ts",
    "line": 9,
    "tag": "HACK",
    "text": "hard-coded schema version until the version table lands"
  },
  {
    "path": "app/screens/Onboarding.tsx",
    "line": 23,
    "tag": "TODO",
    "text": "final copy (see onboarding task)"
  }
];

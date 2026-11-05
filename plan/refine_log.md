# Refine log: PLAN.md against rubric Part A

Judge: **self-judged** (no separate model available in this session). Scores were made in a
cleared pass against `plan/rubric.md` only, quoting evidence; treat them as indicative.
Objective checks for the plan are the hard gates (section checklist), which are binary.

| Round | Change | Gates A1-A5 | Universality | Fit | Differentiation | Scope | Verifiability | Weighted | Pairwise vs best | Kept? |
|---|---|---|---|---|---|---|---|---|---|---|
| v0 | First draft (`plan/PLAN_v0.md`) | A1 fail, A4 fail | 3 | 4 | 2 | 3 | 1 | **2.70** | baseline | baseline |
| 1 | Full rewrite: loop-driven feature table with acceptance checks, data contract, 3 runtimes, non-goals (dropped whiteboard + sync), risks, metrics, test plan | all pass | 9 | 9 | 8 | 7.5 | 9 | **8.63** | v1 wins | yes |
| 2 | Added first-run guide (the "< 1 minute" metric had no feature behind it) and slug/collision/Obsidian link rules | all pass | 9 | 9.5 | 8 | 7.5 | 9 | **8.75** | v2 wins (narrowly) | yes (gain 0.12 < 0.3 MIN_GAIN, kept only because it closes a gap the judge flagged; noted) |

**Stop reason:** done definition met (all gates pass, 8.75 >= 8.5). Further rounds would only
polish wording; the remaining weak spot is Scope (7.5): the Map and Spark prompts are the
least essential MVP items and are the first to cut if the build runs long.

Evidence quotes used by the judge (round 2):
- Universality 9: "Any folder (git or not), any OS, any editor, any AI assistant" + FolderStore browser path. Not 10: no mobile-native app.
- Differentiation 8: Code inbox, Context pack and Commit brain all depend on knowing the repo; Obsidian/Notion can't do them out of the box.
- Scope 7.5: non-goals are explicit, but 12 MVP rows is a lot for one build.

---

# Refine log: the app against rubric Part B

Objective checks (the checker) ran first; soft scores are **self-judged** against `plan/rubric.md`
from screenshots of every view in light, dark and 390 px mobile layouts.

| Round | Change | Gates B1-B6 | Polish | UX clarity | Code | Docs | Weighted | Pairwise | Kept? |
|---|---|---|---|---|---|---|---|---|---|
| v0 | First complete build | B2 fail (2 console errors: API probe 404 on static hosting), B6 fail (no README/media) | 7 | 7.5 | 8 | 3 | **6.55** | baseline | baseline |
| 1 | Server-marker instead of probing; plain-text card snippets (no raw `[[links]]`); hamburger hidden on desktop; capture icon aligned; map spacing; balanced headline wrap; short mobile placeholder; "HEAD" branch label fixed; README, banner, demo shot, CI, LICENSE | all pass (34 tests, 0 console errors) | 8.5 | 8.5 | 8.5 | 9 | **8.60** | v1 wins | yes |

**Stop reason:** done definition met (all gates pass, 8.60 >= 8.5).

Checker evidence: `python -m unittest discover -s tests` → 34 tests OK (core 14, API 7 incl. git commit and
path traversal, CLI 3, parity 1 covering 11 rule groups, e2e 9 incl. 390 px + dark mode with no sideways scroll).
Also checked by hand: real local server on a temp git repo (capture wrote to disk, git card shows changes);
`pip install .` into a clean venv, then the `cairn` command runs and the bundled web app is found.

Still below 10, and why:
- Polish 8.5: map labels can still overlap on dense graphs (no label collision avoidance).
- UX 8.5: the "Open a repo folder" path (File System Access API) is not covered by automated tests,
  because headless browsers can't use the folder picker. It shares the store interface with the tested adapters,
  but treat it as manually verified only.
- Scores are self-judged; a separate reviewer would make them more trustworthy.

## Round: network step 1 + redesign (self-judged)

Goal from the user: explore other people's brains via GitHub, and a UI that doesn't look AI-generated,
grounded in psychology. Design rationale lives in `plan/DESIGN.md`.

Changes
- People view: look up any GitHub user or repo link; profile shows proof of work read from their
  brain files (working on, shipped, decisions, open to collaborate). Discovery via the `cairn-brain` topic.
- Read-only brain mode (`?brain=owner/repo`): banner with Ask to join (prefilled issue), no create
  buttons or drag, drawer is preview-only, action guard as a second line of defence.
- Redesign: warm paper, ink, one signal colour, Instrument Serif + Geist (bundled, OFL), hairline rules,
  editorial Today page with a cairn that grows a stone per finished task.
- Cairn dogfoods itself: `brain/` in this repo is a real brain.

Checks
- 40 tests pass (15 browser e2e incl. profile, unknown user, bad input, read-only brain, explore box).
- Screenshots reviewed: profile, read-only board, welcome, dark home. Fixed: duplicate "Published
  brains" section, create buttons on read-only pages, cramped command block on welcome. CI caught a real race: the discover list re-rendered the
  People view and wiped what the user was typing; fixed and covered by a regression test.

Not verified
- Live GitHub API behaviour beyond the mocked responses (rate limits, very large repos).
- Real users: the "five people publish a brain" task is the actual test of the network idea.

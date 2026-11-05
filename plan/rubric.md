# Rubric v1 (frozen before any plan or code was written)

Goal: "An app that universally solves *a second brain inside a GitHub project* — easier to brainstorm and manage everything than Obsidian/Notion."

Done = every hard gate passes AND weighted soft score >= 8.5. (10/10 is not the target; it invites inflation.)

## Part A: the plan (PLAN.md)

### Hard gates (pass/fail)
| ID | Gate | How checked |
|---|---|---|
| A1 | Covers problem, users, gap vs existing tools, features, data format, architecture, scope + non-goals, risks, success metrics, test plan | section checklist |
| A2 | Data is plain Markdown that stays useful with the app deleted (no lock-in, no database) | stated + data spec |
| A3 | Runs with nothing but Python 3.10 stdlib **or** just a browser | dependency list |
| A4 | Every MVP feature has an acceptance check | feature table |
| A5 | Buildable in this session: no Node toolchain, no paid API, no server to host | stack list |

### Soft criteria (0-10, weights)
| Criterion | w | 4 = | 7 = | 10 = |
|---|---|---|---|---|
| Universality | 0.25 | one repo layout, one editor | any repo, any OS; coexists with Obsidian/VS Code | any folder (git or not), any OS, any editor, any AI assistant, browser-only path too |
| Problem fit (brainstorm + manage) | 0.25 | a note editor | notes + tasks | capture -> brainstorm -> decide -> do loop, each step one click, linked to code |
| Differentiation | 0.15 | Obsidian clone | one repo-specific feature | several things the incumbents can't do because they don't know about the repo |
| Scope discipline | 0.15 | feature soup | MVP listed | MVP is small, non-goals explicit, each feature justified by the loop |
| Verifiability | 0.20 | "test it" | unit tests named | unit + parity + browser e2e, objective gates defined up front |

## Part B: the app

### Hard gates
| ID | Gate | How checked |
|---|---|---|
| B1 | Python unit + API tests pass | `python -m unittest` |
| B2 | Browser e2e passes in demo mode: every view renders, capture works, board drag works, note edit persists, zero console errors | Playwright |
| B3 | Python and JS parse the same fixtures identically (frontmatter, capture, links) | parity test |
| B4 | Server refuses paths outside the brain folder and binds to 127.0.0.1 only | API test |
| B5 | Usable at 390 px wide and in dark mode, no horizontal scroll | Playwright viewport check |
| B6 | Repo has README (profile template), LICENSE (MIT), landing page, demo media, CI workflow | file check |

### Soft criteria (0-10, weights)
| Criterion | w | 4 = | 7 = | 10 = |
|---|---|---|---|---|
| Visual polish | 0.30 | default-looking | consistent spacing/type/colour | calm, professional, matches a shipped product |
| UX clarity | 0.30 | needs docs | main flows discoverable | first-time user captures, brainstorms and moves a task in < 1 min without reading |
| Code quality | 0.20 | one big file | modules, named functions | small pure core shared by both runtimes, tested |
| Docs / repo presentation | 0.20 | bare README | template README | template README + landing page + screenshots + clear install |

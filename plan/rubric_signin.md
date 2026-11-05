# Rubric: step 2, sign in and edit your brain from any device (FROZEN)

Frozen before any code was written. The maker may not edit it.

Goal (from the user): "yes do it": build step 2 of the GitHub-as-backend plan, i.e. sign in with
GitHub and edit your own brain (stored in your GitHub repo) from the browser on any device.
Iterate until it meets this rubric.

Assumptions recorded up front:
- Sign-in uses GitHub's OAuth **device flow** through a tiny Cloudflare Worker that only adds CORS
  headers (GitHub's device endpoints do not allow browser calls; the flow needs no client secret).
- The Worker and the GitHub OAuth App need the owner's own Cloudflare and GitHub accounts, so I can
  build and test them but not deploy them. A **personal access token** option must therefore work
  today with zero setup, so the feature is usable before the Worker exists.
- Writes go through the GitHub Contents API; each save is one commit in the user's repo.

## Hard gates (all must pass; checked by tools)

| # | Gate | How it is checked |
|---|---|---|
| G1 | Full test suite passes locally with `CAIRN_REQUIRE_BROWSER=1`, and CI is green after push. | `python -m unittest discover -s tests`, `gh run list` |
| G2 | No page errors in any mode: demo, visitor, read-only brain, signed-in editable brain. | e2e tests collect `pageerror` |
| G3 | Token handling: kept in `sessionStorage` by default; `localStorage` only if the user ticks "Remember on this device"; sign-out removes both; the `Authorization` header is only ever sent to `api.github.com`. | e2e test inspects storage and every request |
| G4 | Edit round trip on a writable GitHub brain: create, edit, move and delete send the right Contents API calls (PUT with the current `sha`, DELETE with `sha`) and the UI reflects success. | e2e test against a stateful mock |
| G5 | Conflicts lose nothing: if the file changed on GitHub (stale `sha`), the user sees the conflict and their local text is kept. | e2e test |
| G6 | Permission respected: signed in but no push access means the brain stays read-only. | e2e test |
| G7 | Worker proxies only the two device-flow endpoints, forces the configured `client_id`, answers CORS only for allowed origins, and rejects everything else. | browser-run unit test of the Worker module |
| G8 | Expired or revoked token (401) signs the user out with a clear message instead of failing silently. | e2e test |

## Soft criteria (0-10, weighted)

| Criterion | Weight | 4 | 7 | 10 |
|---|---|---|---|---|
| S1 Usefulness and flow | 0.25 | Works, but needs docs to figure out | A new user can sign in and edit their brain in a few obvious steps | From any device: sign in, land in *your* brain (or create one in one click), capture and move tasks, saved to GitHub with no extra steps |
| S2 Trust and safety UX | 0.20 | Token asked for with little explanation | Says what access is granted, where the token lives, how to sign out | Plain-language scopes, recommends the narrowest token, visible signed-in state, one-click sign out, honest about the Worker seeing the token in transit |
| S3 Robustness | 0.20 | Happy path only | Conflicts, 401 and rate-limit errors handled | Plus offline/network errors, save failures keep text, burst edits don't make a commit per keystroke |
| S4 Code quality | 0.15 | Logic duplicated, unclear ownership | Clear modules (auth, store, worker), small functions | Same store interface as other adapters, no special cases leaking into views, readable comments where non-obvious |
| S5 Design consistency | 0.10 | Looks bolted on | Matches DESIGN.md (paper, ink, one signal colour) | Feels native; calm, no dark patterns, states are clear (signing in, saving, saved, error) |
| S6 Verification depth | 0.10 | A couple of tests | Each gate has a test | Gates tested plus edge cases, a live check of what can be checked live, and an honest list of what can't |

**Done** = all gates pass AND weighted score >= 8.5. Max 5 rounds; stop after 2 rounds without a kept
improvement. Scores are **self-judged** (no separate judge model is available in this environment);
the judge pass is run as a separate, evidence-quoting review against this rubric only.

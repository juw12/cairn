# Proof of work in Cairn (v0.1)

A CV says what you did. A Cairn proof says what you did **and links to the work**, and the app checks
every link on GitHub. Nobody types their own level: it is computed from what GitHub answers.

Everything is plain Markdown in Git. There is no Cairn server and no database.

## 1. A claim is a note

Any note with a `claim:` property is a claim.

```markdown
---
title: Built Cairn
claim: Built Cairn, a second brain that lives inside your repo
role: author                 # optional: author, contributor, lead, reviewer…
outcome: 40 testers          # optional, free text (shown, never checked)
evidence:
  - https://github.com/juw12/cairn
  - https://github.com/someone/project/pull/42
attested_by:
  - https://github.com/alice/brain/blob/main/vouches/juw12/built-cairn.md
publish: true
created: 2026-11-07
---
What you did, and what changed because of it.
```

The claim's id is the note's file name without `.md` (`built-cairn` above). Vouches refer to it.

## 2. Evidence Cairn understands

| Link | Checked on GitHub |
| --- | --- |
| `github.com/OWNER/REPO` (or `/tree/…`, `/blob/…`) | owner, fork or not, stars, forks |
| `github.com/OWNER/REPO/pull/N` | who opened it, merged or not |
| `github.com/OWNER/REPO/commit/SHA` | the GitHub account of the commit author |
| `github.com/OWNER/REPO/releases/tag/TAG` | who published it |
| anything else | shown as a link; never raises the level |

## 3. Levels

A claim gets the highest level any one of its links earns. A link that fails a check is shown with the
reason and earns nothing.

| Level | Name | Earned when |
| --- | --- | --- |
| L1 | Claimed | The note says so. No passing evidence. |
| L2 | Backed by work | A repo you own (not a fork), a commit you authored, a release you published, or a merged pull request into your own repo. |
| L3 | Used by others | Your pull request was **merged into someone else's repo**, or your repo has at least one fork or 3 stars. |
| L4 | Vouched | Another GitHub user committed a vouch file for this claim in **their own** repo (section 4). |

What a level does **not** prove: that the work was good, or that you did all of it. It proves the
link is real and points at you. Stars can be gamed a little (you can star your own repo once, hence
the threshold of 3); merged pull requests and vouches are much harder to fake.

## 4. Vouching

A vouch lives in the voucher's repo, so only the voucher can write it. Path:
`vouches/<subject>/<claim-id>.md`.

```markdown
---
vouch_for: juw12
claim: built-cairn
claim_text: "Built Cairn, a second brain that lives inside your repo"
relation: colleague          # colleague, client, user, manager, collaborator…
by: alice
date: 2026-11-07
---
I worked with @juw12 on this. What I saw first-hand: …
```

It counts when the file exists, is **not** in the subject's own repo, says `vouch_for: <subject>` and
`claim: <claim-id>`. The subject then adds the file's link under `attested_by:`.

In the app, the **Vouch** button on someone's claim opens GitHub's "new file" page in your own repo
with this text filled in. You commit it with your own account; no token is given to Cairn.

## 5. Publishing from a private brain

Keep your real brain (or Obsidian vault) private. Mark the notes you want to show with
`publish: true`, then:

```sh
python -m cairn publish --to YOUR-NAME/brain --source "path/to/vault"
```

- Only notes with `publish: true` (and not `private: true`) are copied, into `brain/` of the public repo.
- One commit per run. The repo is created (public) if it doesn't exist and gets the `cairn-brain` topic,
  so People and Proof find it.
- Links to notes that stay private become plain text; embeds of unpublished files are dropped.
- `brain/.cairn-published.json` remembers what was published. Un-publish a note and the next run
  removes it. Files you add to the public repo by hand are never touched.
- `--dry-run` shows the plan; `--folder PATH` writes into a local clone instead of using the API.

## 6. Limits of v0.1

- Checks run in the visitor's browser against GitHub's public API (60 requests an hour signed out,
  cached for 10 minutes). One profile spends at most 40 checks.
- Vouches are found through `attested_by:` links, not by searching GitHub.
- Private repos can't be evidence: a visitor couldn't check them.

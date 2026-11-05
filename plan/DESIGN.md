# Cairn design notes

Cairn should feel like a field notebook, not a dashboard. Most "AI-looking" apps
share the same defaults: purple gradients, rounded cards inside cards, emoji
icons, a wall of equally loud numbers. This file records what we chose instead
and why, so later changes can be checked against it.

## Look

| Choice | Value | Why |
|---|---|---|
| Paper | `#f4f1ea` warm off-white, ink `#1b1a17` | Reads like paper; lower glare than pure white for long sessions. |
| One signal colour | trail orange `#d9481f` | Used only where attention matters: the active page, the date, the one thing to do next. If everything is highlighted, nothing is. |
| Headings | Instrument Serif | A human, editorial voice. Instantly separates Cairn from the generic sans-serif SaaS look. |
| UI text | Geist | Neutral and very legible at 13-14 px. |
| Labels, dates, counts | Geist Mono | Metadata looks like metadata, so the eye skips it until needed. |
| Structure | Hairline rules, not boxes | Fewer borders means less visual noise; grouping comes from spacing and alignment (Gestalt proximity). |

Fonts are bundled in `docs/fonts/` (SIL OFL), so there is no call to a font CDN.

## Psychology we use (and the line we don't cross)

1. **Calm technology.** No red badges, unread counts or streaks that punish a
   day off. Counts in the sidebar are muted mono text, never alerts. The app
   should be something you return to, not something that pulls you back.
2. **Hick's law.** Navigation is grouped by intent (Think & do, Remember,
   Project, Network) so you scan four groups instead of ten items. The quick
   capture bar is the one obvious action on every page.
3. **Goal gradient and visible progress.** The cairn on Today grows one stone
   per finished task. People speed up when they can see progress
   accumulating; a pile of stones is a quiet, honest version of that, with no
   points or levels.
4. **Endowed progress.** New brains start with a three-step "get the hang of it"
   strip. Finishing a step ticks it off, so the first minute already feels
   like progress.
5. **Peak-end rule.** Finishing a task is the peak moment, so it gets the
   reward (a new stone and a short confirmation). Everything else stays quiet.
6. **Evidence over vanity metrics.** Profiles show proof of work read from
   files: what someone is working on, has shipped, has decided and is open to.
   No follower counts, likes or endorsements (decision 0003).
7. **Aesthetic-usability effect.** A considered look makes people more patient
   with rough edges, which buys time while the product is young. It is not a
   substitute for fixing them.
8. **Honest affordances.** On someone else's brain the create buttons and drag
   handles disappear rather than failing on click. Read-only is shown once, in
   a banner, with the one useful action ("Ask to join").

Never: dark patterns, fake scarcity, notification bait, streak guilt or
confirm-shaming. If a feature needs one of these to work, the feature is wrong.

## AI agents as users (planned)

Agents will show up as labeled members (a distinct mono tag, never a human
avatar), with scoped access and propose-only writes: an agent's change is a
suggestion a human approves. Trust comes from seeing who did what, so every
agent contribution stays attributable in git history.

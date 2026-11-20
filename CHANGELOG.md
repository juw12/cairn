# Changelog

Every build of Cairn, newest first. Written from `docs/versions.json` by `scripts/make_changelog.py`;
the app shows the same list under Version history.

## Build 82 (2026-11-20): Pocket meter plugin

- Plugins: a new built-in plugin, Pocket meter. It draws a bar from zero to your ceiling from one note in your brain: the filled part is money that has arrived, a marker shows what is expected, a band shows the likely range, and a list shows which sources contribute ([#67](https://github.com/juw12/cairn/pull/67))
- The plugin holds no figures. They live in a note you mark with pocket-meter: true, so they stay as private as that note. With no such note, the page shows the properties to add ([#67](https://github.com/juw12/cairn/pull/67))

## Build 81 (2026-11-20): Private pictures open directly

- Latest updates uses the vault’s known paths to load a private picture without asking GitHub for a file that isn’t there. A picture beside the note still wins when both folders contain it ([#66](https://github.com/juw12/cairn/pull/66))
- A phone-sized WebKit test now opens the Map with 4,000 notes and checks that it draws the linked notes and responds to a tap ([#66](https://github.com/juw12/cairn/pull/66))

## Build 80 (2026-11-20): Map opens on a big brain

- Map and a note's Unlinked mentions no longer freeze on a large brain. On 4,000 notes the search for mentions took 5 seconds in Chrome and did not finish in Safari on a phone; it takes half a second in both now, with the same results ([#64](https://github.com/juw12/cairn/pull/64))
- Home: the first line of an update is no longer the name of its picture when the note starts with one ([#64](https://github.com/juw12/cairn/pull/64))
- Home: a note named for a time of day that has not come yet no longer sits on top of Latest updates. It keeps its day and shows no time ([#64](https://github.com/juw12/cairn/pull/64))

## Build 79 (2026-11-20): A Home that shows what matters

- Home leads with what waits for you: sessions that stopped on an error, GitHub minutes running low, the newest tasks marked needs-me and decisions that wait for a yes or no ([#62](https://github.com/juw12/cairn/pull/62))
- Home: Working now lists the running sessions with their computer, assistant, last step and tokens. The numbers are today's: working now, shipped, sessions, tokens, waiting for you, open tasks ([#62](https://github.com/juw12/cairn/pull/62))
- Home: an activity chart you can tap or drag across. Sessions, shipped, tokens or journal lines per day over 14, 30 or 90 days, and under it what happened on the day you picked ([#62](https://github.com/juw12/cairn/pull/62))
- Home: Latest updates shows your dated notes with a first line and a picture, from the note itself or from the project it names. A run of automatic notes folds into one row ([#62](https://github.com/juw12/cairn/pull/62))
- Home: Computers shows every computer that writes into the brain, what runs there and when it was last heard ([#62](https://github.com/juw12/cairn/pull/62))
- Phone: recap rows no longer run into each other on Home, Board, Ideas, Decisions and Notes. Long task titles and the Autopilot tiles no longer push the page sideways on a small phone ([#62](https://github.com/juw12/cairn/pull/62))
- Pictures in a private brain on GitHub now load on the phone when you are signed in ([#62](https://github.com/juw12/cairn/pull/62))

## Build 78 (2026-11-20): Dashboards has its own icon

- Dashboards on phone: the page has its own icon (four tiles on paper) on the Home Screen, in the tab and in its bar, so it no longer looks like Cairn. The mark in the bar is no longer a link to Cairn's home. Dashboards are still set up and signed in from Cairn as before ([#60](https://github.com/juw12/cairn/pull/60))

## Build 77 (2026-11-20): Your totals across computers

- People: your row on the leaderboard counts every computer that syncs into the brain, says so, and lets you pick which computers count. What you publish follows the same choice ([#58](https://github.com/juw12/cairn/pull/58))
- People: an unpublished row now reads "not published" instead of "only on this device", which sounded as if only one computer was counted ([#58](https://github.com/juw12/cairn/pull/58))

## Build 76 (2026-11-20): Who pushes, on the folded GitHub block

- Vibe: folded, the GitHub minutes block still says which account this computer pushes as, next to the percent used ([#56](https://github.com/juw12/cairn/pull/56))

## Build 75 (2026-11-19): Which GitHub account this computer pushes as

- Vibe: where the GitHub CLI is signed in to several accounts, a chip per account under the GitHub meters picks the one this computer pushes as. git, the GitHub CLI and an assistant working there all follow it, and a switch made in a terminal shows on the page ([#55](https://github.com/juw12/cairn/pull/55))
- cairn vibe github push-as says which account pushes, and with a name switches to it ([#55](https://github.com/juw12/cairn/pull/55))

## Build 74 (2026-11-19): GitHub minutes of a second account

- Vibe: a meter of GitHub Actions minutes for every account you are signed in to, not only the main one. Name the others with cairn vibe watch --github-account NAME ([#53](https://github.com/juw12/cairn/pull/53))
- Vibe: hand a private repo to another of your accounts from the meter, so its minutes count there. GitHub mails the new owner to confirm, and the old owner keeps push access ([#53](https://github.com/juw12/cairn/pull/53))

## Build 73 (2026-11-19): Dashboard buttons from the phone

- Dashboards on phone: a dashboard's buttons can run on your PC. Each press runs once, is refused when it is older than 5 minutes, and is taken back when the PC does not pick it up ([#51](https://github.com/juw12/cairn/pull/51))
- Dashboards on phone: the open tab and folded blocks of a dashboard stay as they were when a newer copy arrives ([#51](https://github.com/juw12/cairn/pull/51))

## Build 72 (2026-11-19): Version history

- Version history inside the app: every build with its date and what changed. Open it at the bottom of the sidebar, from Settings or from the command palette ([#50](https://github.com/juw12/cairn/pull/50))

## Build 71 (2026-11-19): Journal per day, several computers

- Journal: one line per day, mood worked out for you, any period, and its own sidebar entry ([#48](https://github.com/juw12/cairn/pull/48))
- Vibe: several computers in one brain with a device switch; a Mac can run the watcher ([#49](https://github.com/juw12/cairn/pull/49))
- Every remaining page is made of blocks with a dashboard and a recap ([#47](https://github.com/juw12/cairn/pull/47))

## Build 68 (2026-11-18): Blocks you fold and arrange

- Vibe blocks you fold and arrange, shared through the brain; the sidebar goes from 9 entries to 6; Founder Studio leaves Cairn ([#45](https://github.com/juw12/cairn/pull/45))
- Vibe: a session works or is finished, never waits; one live list; Projects, People and Proof as blocks; 5 sidebar entries ([#46](https://github.com/juw12/cairn/pull/46))

## Build 66 (2026-11-17): Smooth phone map, public page

- The phone map stays smooth on a big brain, a Telegram bot reports Cairn's status daily, UI tidy ([#43](https://github.com/juw12/cairn/pull/43))
- A public page you can host anywhere: cairn publish --site ([#44](https://github.com/juw12/cairn/pull/44))

## Build 65 (2026-11-16): Faster Vibe, GitHub minutes

- Vibe shows the computer (CPU, memory, power, time since restart) and effort follows its real load
- The Vibe page never waits on git or on the computer reading, and no longer re-reads every note per poll
- GitHub Actions minutes against the free limit, with a warning near it and a history
- Autopilot: a lost connection no longer blocks a job, it waits and tries again
- Desktop: updates apply even when git is slow; chat opens without waiting for the model list
- Phone: any-network sign-in opens on Vibe, the app reloads into new versions, a GitHub brain loads every session
- Plugin catalogue: Kilat 0.2.3 ([#42](https://github.com/juw12/cairn/pull/42))

## Build 58 (2026-11-14): Tokens, models and sharing

- The Vibe watcher reads OpenCode 2.2: model, effort, tokens and turn state
- Vibe stats: tokens, usage per model, prompts, active days and streak
- A live recap band; continue an OpenCode session or start a chat in its folder from the recap
- A share card for the last 14 days (story and square picture)
- People: friends and a leaderboard from totals you choose to share

## Build 54 (2026-11-13): Dashboards on the phone, MCP Registry

- Dashboards on the phone: cairn dashboards publishes local dashboard pages to the private relay repo, on a schedule
- The home-screen app opens your brain signed in, with live sync for brains opened from GitHub
- A Cairn tile for the iPhone widget ([#41](https://github.com/juw12/cairn/pull/41))
- Published to the official MCP Registry as a bundle; every MCP tool declares its hints
- Plugin catalogue: Kilat 0.2.2 ([#40](https://github.com/juw12/cairn/pull/40))

## Build 50 (2026-11-12): Sign in by QR, several PCs on one relay

- Sign in a phone by QR code from Settings, on any network
- Several PCs on one relay: the phone picks which one to talk to
- Signed-in browsers reopen the last brain, so phone, laptop and PC show the same Map and Live
- Big vaults open fast (notes fetched 100 per call), with a loading message while a brain opens
- Plugins: Kilat, calculations with units, joins the catalogue ([#35](https://github.com/juw12/cairn/pull/35), [#36](https://github.com/juw12/cairn/pull/36))
- Minimum Python is 3.13 ([#38](https://github.com/juw12/cairn/pull/38))

## Build 44 (2026-11-11): Phone and chat

- Pair a phone by QR code to watch Cairn from it
- Phone access from any network through Tailscale
- Live sync between phone and PC
- Chat with your OpenCode models from any device, also through a private GitHub relay repo
- A live progress bar, time left and effort for running sessions

## Build 42 (2026-11-10): Hubs, projects and databases

- Sidebar: 17 entries become 8 hubs with tabs inside the page ([#30](https://github.com/juw12/cairn/pull/30))
- Projects ranking, Notion-style databases and a / menu ([#31](https://github.com/juw12/cairn/pull/31))
- Vibe: an overview band with every status at a glance and what is running now ([#32](https://github.com/juw12/cairn/pull/32))
- Founder Studio follows Cairn's controls and theme ([#28](https://github.com/juw12/cairn/pull/28), [#29](https://github.com/juw12/cairn/pull/29), [#33](https://github.com/juw12/cairn/pull/33))
- The local server still answers when the git check stalls ([#34](https://github.com/juw12/cairn/pull/34))

## Build 37 (2026-11-09): MCP, precedent and autopilot

- cairn mcp connects AI assistants, and stays fast on vaults of 2,000 notes ([#17](https://github.com/juw12/cairn/pull/17))
- Precedent and autopilot inside Cairn ([#19](https://github.com/juw12/cairn/pull/19), [#23](https://github.com/juw12/cairn/pull/23))
- Vibe: honest numbers at 286 sessions, 'code changed' for detected sessions, tap a figure to have it explained ([#18](https://github.com/juw12/cairn/pull/18), [#21](https://github.com/juw12/cairn/pull/21), [#22](https://github.com/juw12/cairn/pull/22))
- Phones: the Log button sits in thumb reach and tucks away while you read ([#24](https://github.com/juw12/cairn/pull/24))
- Founder Studio page: run the buyer panel inside Cairn ([#26](https://github.com/juw12/cairn/pull/26), [#27](https://github.com/juw12/cairn/pull/27))

## Build 29 (2026-11-08): Sessions found on their own, offline voice

- Vibe finds OpenCode and Vector sessions by itself ([#12](https://github.com/juw12/cairn/pull/12))
- Voice log: offline voice on the device, recall of any day, voice search, a 12-month heatmap ([#13](https://github.com/juw12/cairn/pull/13))
- Vibe: clock-time totals, commit evidence, history grouped by day ([#14](https://github.com/juw12/cairn/pull/14))
- Vibe: outcome filters, days that fold, and the desktop app runs the watcher ([#15](https://github.com/juw12/cairn/pull/15))
- Voice log: monthly review, reviews that are due, days that read alike ([#16](https://github.com/juw12/cairn/pull/16))

## Build 23 (2026-11-07): Obsidian, canvas, voice log and Vibe

- Obsidian import and two-way sync, and a live interactive map ([#1](https://github.com/juw12/cairn/pull/1))
- Proof of work page and a design pass ([#2](https://github.com/juw12/cairn/pull/2))
- Obsidian parity: callouts, embeds, split view, link suggestions, rename, properties, folders, templates, a first-run tour ([#3](https://github.com/juw12/cairn/pull/3))
- The desktop app updates itself, no restarts by hand ([#4](https://github.com/juw12/cairn/pull/4))
- Sync through office proxies, auto-publish of the public brain, maths and diagrams ([#5](https://github.com/juw12/cairn/pull/5))
- Canvas, tabs and community plugins ([#6](https://github.com/juw12/cairn/pull/6), [#9](https://github.com/juw12/cairn/pull/9))
- Voice daily log: mic button, spoken commands, 'Hey Cairn', languages, @people ([#10](https://github.com/juw12/cairn/pull/10), [#11](https://github.com/juw12/cairn/pull/11))
- Vibe: live AI coding sessions, results, stats, feedback and skill runs ([#8](https://github.com/juw12/cairn/pull/8))

## Build 11 (2026-11-06): Desktop app and the map

- Desktop app for Windows: one-click installer, tray icon, sync, open and settings inside the app
- Map: filters, local maps, paths, suggested links, keyboard navigation; opens Obsidian vaults as they are
- The map in 2D or 3D
- Continue with GitHub sign-in
- Design pass: map labels never overlap, 320 px phones fit

## Build 4 (2026-11-05): First release

- Cairn 1.0: a second brain that lives inside your repo
- People: explore GitHub profiles and read-only brains
- Sign in with GitHub and edit your brain from any device

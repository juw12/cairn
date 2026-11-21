---
name: cairn-memory
description: The user's long-term memory is their Cairn brain, a folder of Markdown notes shared by their devices. Use when a chat starts, and whenever the user says "remember", "recall", "note this", "save this decision", "what did we decide about X", "like last time", mentions Cairn, the brain or another device, or asks about a project, app or preference that this chat has not explained.
---

# Cairn memory

A new chat starts with no memory. The user's memory is the Cairn brain {{BRAIN}}: plain Markdown notes
(ideas, tasks, decisions, notes, a journal, past work sessions) that the Cairn app shows and syncs.
Cairn put this skill here by itself and keeps it up to date; edits to these files are replaced.

Run the two scripts with this Python, the one Cairn runs on:

    "{{PYTHON}}" "{{SKILL}}/recall.py" ...
    "{{PYTHON}}" "{{SKILL}}/remember.py" ...

## When a chat starts

1. Read the standing decisions and follow them: `recall.py rules`. Open one that touches the task with
   `recall.py read "<path or title>"`.
2. Search the brain for the subject of the request before answering, and before asking the user something
   they may already have answered:

       recall.py search "two or three words" [--type decision|task|idea|note|journal|session] [--limit 10]
       recall.py recent [--days 3]
       recall.py brief

3. Never say "I don't know that" about one of the user's projects, devices or preferences before searching.
4. Look at what the user's other computers left for this one: `recall.py handoffs`. Tell the user what is
   waiting. `recall.py handoffs "a few words"` shows one conversation in full. It fetches the newest
   messages first and `remember.py handoff` and `answer` send at once, also while the Cairn app is closed,
   where the brain is set up to sync by itself; each command says what it did.

A short question that needs no memory (a sum, a definition) needs none of this.

## Recall

- Every word of a search must match, so use two or three words, then try other words.
- Answer from what the notes say and give the path of each note used. If two notes disagree, show both and
  say which is newer.
- If nothing is found, say so. Never make a memory up.
- Reading a file in the brain directly is fine too.

## Remember

Save when the user says to remember something. Save without being asked when a chat produces a decision, a
preference, a lesson or a handoff that another chat or device will need. Say what was saved and where.

    remember.py note "Short clear title" --body "The facts, with dates, versions and paths." --tags project,topic
    remember.py decision "Short clear title" --context "..." --options "..." --decision "..." --consequences "..."
    remember.py check

- Search first. Add to a note that already covers it instead of making a second one.
{{WRITE_RULES}}
- Facts only. No passwords, keys, tokens or bank details: `remember.py` refuses text that looks like one.
- After each save the script prints a check: the file on this computer, and whether the brain's copy on its
  git remote is up to date. Report a failed check; do not call it saved.

## Other computers

The brain is shared by the user's computers, and they leave each other messages in it (Cairn shows them on
Today under "Between computers").

    recall.py handoffs [--all]
    remember.py answer "a few words of the conversation" --says picked-up
    remember.py answer "a few words of the conversation" --says done --text "What was done, with the numbers."
    remember.py handoff "Short title" --to "computer name" --body "What it must know, check or do."

- When you start on something another computer left: `--says picked-up`. When it is finished: `--says done`
  with the result in `--text`, because the other computer reads that, not this chat. A question back is
  `--says note --text "..."`. `--says closed` ends the conversation for every computer.
- When a chat ends with something only another computer can do or check (another system, a login or a
  device it has, a test on its hardware), leave it with `remember.py handoff` as well as telling the user.
  `--to` takes a computer's name or a few words of it, several times for several, or `all`.
- These messages are the one thing written outside this computer's own folder: each is a new file in
  `handoffs/` that nobody edits afterwards.

## When the brain is not on this computer

A brain that is pushed to GitHub does not need its folder here. If the folder is missing (a new computer, a
deleted or moved folder) both scripts work on GitHub by themselves and say so in their first line: they fetch
the notes' text once (seconds to a minute), then only what changed, and `remember.py` makes the note on GitHub.
The same rules apply. Add `--cloud` to do that on purpose, for the newest notes when Cairn here has not synced:

    recall.py search "words" --cloud
    remember.py note "Title" --body "..." --cloud

The messages between computers are the one thing that needs the folder: without it, `recall.py handoffs` says
so, and their files can still be read (`recall.py search "handoff"`, `recall.py read "handoffs/..."`).

This needs a GitHub sign-in on the computer (`gh auth login`, or the one git already uses). If a script says it
has none, tell the user that one command; do not ask them for a token in the chat.

## Limits

- This skill reads and writes the brain only. It never needs the AI app's own files or settings.
- Sending the brain to its git remote is Cairn's job (its sync). `remember.py check` only reports the state.

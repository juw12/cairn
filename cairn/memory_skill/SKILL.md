---
name: cairn-memory
description: The user's long-term memory is their Cairn brain, a folder of Markdown notes shared by their devices. Use when a chat starts, and whenever the user says "remember", "recall", "note this", "save this decision", "what did we decide about X", "like last time", mentions Cairn, the brain or another device, or asks about a project, app or preference that this chat has not explained.
---

# Cairn memory

A new chat starts with no memory. The user's memory is the Cairn brain at `{{BRAIN}}`: plain Markdown notes
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

## Limits

- This skill reads and writes the brain only. It never needs the AI app's own files or settings.
- Sending the brain to its git remote is Cairn's job (its sync). `remember.py check` only reports the state.

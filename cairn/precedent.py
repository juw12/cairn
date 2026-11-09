"""Precedent: say what you want once; get back what you will also want, from your own history.

Last time you asked for X, you then asked for Y and Z. Precedent remembers that, so the next
time X comes up, Y and Z are part of the plan from the start instead of three more messages.

* `learn()` builds a local library from your own history: prompts you typed into AI coding
  agents (OpenCode's database, Claude-style JSONL transcripts), the `Update Log/` folder next to
  the brain and the brain's session notes. Secrets are redacted before anything is written.
* `search()` ranks past goals against a new one (TF-IDF, no dependencies). A past goal's
  follow-ups are the hidden requirements: what you asked for next is what you will want again.
* `brief()` turns a goal into a one-page plan for an agent: closest precedents, lessons, related
  notes, your profile (`notes/precedent-profile.md`), the queue and the working loop.
* The queue is ordinary tasks tagged `#queue` (`#auto` = safe to run unattended, `#needs-me` =
  waiting on you), so it shows on the Board like any other task.

The library lives outside the brain, in ~/.cairn/precedent (or $CAIRN_HOME/precedent): it is
built from private chat history and is never committed. Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

from . import core
from .brain import Brain

PROFILE_PATH = "notes/precedent-profile.md"
QUEUE_TAG = "queue"
AUTO_TAG = "auto"
NEEDS_ME_TAG = "needs-me"
LIBRARY_FILES = ("precedents.jsonl", "updates.jsonl", "learned.jsonl")

TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")
STOPWORDS = set(
    "a an the and or to of in on for with is are be it this that i u you me my can "
    "make do also so just pls please kindly want wanna need then like have has will "
    "from as at by into not no but if all its it's what how why".split()
)

# Prompts that tools or agents wrote, not the person.
MACHINE_PREFIXES = (
    "analyze the attached file", "[search-mode]", "[analyze-mode]", "[context", "reply with exactly",
    "connection test", "called the read tool", "continue your previous session", "continue your prior",
    "resume", "your previous run", "verification of", "verification failed", "confirmation pass",
    "final re-verification", "comprehensive verification", "<system", "you are ",
)

# "go", "done?", "continue": nudges the autopilot exists to make unnecessary.
NUDGE_PATTERN = re.compile(
    r"^(done|go|continue|sure|yes|ok|okay|do it|all|both|hurry|u do for me|"
    r"do for me|go do it|yes sure|do all|yes go|okay do it|go again)\b[\s?？!.,a-z]{0,12}$",
    re.IGNORECASE,
)

SECRET_PATTERNS = [
    (re.compile(r"(password|passwd|pwd|passcode|密碼|密码)\b[^.\n;]{0,60}", re.IGNORECASE), r"\1 [REDACTED]"),
    (re.compile(r"(token|api[_ -]?key|secret)\s*(?:is|[:=])\s*\S+", re.IGNORECASE), r"\1 [REDACTED]"),
    (re.compile(r"\b(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[REDACTED_HEX]"),
]

LOOP = """## How to work (the loop)

1. **Load.** Read the profile and the closest precedents below; check the queue.
2. **Expand the goal.** Write a one-screen Definition of Done: the literal ask; the hidden asks
   (what was asked for after similar goals); and how you will prove each item (live URL loads,
   app opens, test passes, screenshot, file count). Pick defaults from precedent and write them
   as `Assumed: ...` instead of asking.
3. **Execute.** Split multi-part goals; keep code changes small and complete.
4. **Verify like the owner would.** Open the live page, app or file, not the commit. A failed
   check means fix and re-check, not report.
5. **Iterate.** Below the bar? Improve and re-check. Never loop on the same failure more than
   3 times: change approach or record a blocker.
6. **Ship everywhere it belongs** (repo, docs, release notes, wherever the profile says).
7. **Log and learn.** `cairn precedent log "goal" "what was delivered" "correction (if any)"`.
8. **Keep going inside this goal.** Add follow-up ideas as queue items
   (`cairn precedent queue "..."`, add `--auto` only when safe to run unattended: no sending,
   paying, submitting or posting). Never start a different queue item on your own.

Never invent results, links or numbers; say what is unverified. Never copy passwords or tokens
into notes, code, commits or logs. Ask only at a hard stop (money, sending to other people,
final submit, deleting data, a password only the owner can type), batching every open question
into one message with your recommended default, and keep working on everything not blocked.
"""

PROFILE_TEMPLATE = """## Who
- Name, role, where files live, which AI agents you use.

## What I really want (the goal behind the goal)
- e.g. finished and visible, not "pushed"; zero hand-holding; real apps, not demos.

## Standing defaults (apply without asking)
- e.g. big outputs go to D:, free tools only, light and dark theme, plain words.

## Routing
- e.g. UI polish -> /hone; code changes -> keep them small; documents -> the docx skill.

## Hard stops (the only reasons to pause and ask)
- Spending money, sending things to other people, final submits, deleting data, passwords.
"""


# ---------------------------------------------------------------- storage


def home_dir() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")


def library_dir() -> Path:
    return home_dir() / "precedent"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def redact(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _is_human(text: str) -> bool:
    lowered = text.lower().strip()
    if not lowered or lowered.startswith(MACHINE_PREFIXES):
        return False
    if "## " in text or "do not modify" in lowered:
        return False
    return len(text) <= 1500


# ---------------------------------------------------------------- learning sources


def read_jsonl_transcripts(folder: Path) -> list[dict]:
    """User prompts from Claude-style transcripts: one JSON object per line, type "user"."""
    prompts = []
    if not folder.exists():
        return prompts
    for path in sorted(folder.glob("*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"user"' not in line[:60]:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "user":
                    continue
                content = record.get("content")
                if content is None and isinstance(record.get("message"), dict):
                    content = record["message"].get("content")
                if isinstance(content, list):
                    content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                if not isinstance(content, str):
                    continue
                text = re.sub(r"\s+", " ", content).strip()
                if _is_human(text):
                    prompts.append({"ts": str(record.get("timestamp", "")), "session": path.stem, "text": redact(text)})
    return prompts


def read_opencode_prompts(db_path: Path) -> list[dict]:
    """User prompts from OpenCode's local database (read-only). Only text parts the person typed."""
    if not db_path or not Path(db_path).exists():
        return []
    prompts = []
    try:
        connection = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = connection.execute(
            "SELECT m.session_id, m.time_created, p.data FROM message m JOIN part p ON p.message_id = m.id "
            "WHERE m.data LIKE '%\"role\":\"user\"%' ORDER BY m.time_created"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        connection.close()
    for session_id, created, data in rows:
        try:
            part = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            continue
        if part.get("type") != "text" or part.get("synthetic"):
            continue
        text = re.sub(r"\s+", " ", part.get("text") or "").strip()
        if not _is_human(text):
            continue
        stamp = dt.datetime.fromtimestamp((created or 0) / 1000).isoformat(timespec="seconds") if created else ""
        prompts.append({"ts": stamp, "session": session_id, "text": redact(text)})
    return prompts


def build_sessions(prompts: list[dict]) -> list[dict]:
    """One precedent per session: the first real ask is the goal, later asks are the follow-ups."""
    by_session: dict[str, list[dict]] = {}
    seen = set()
    for item in sorted(prompts, key=lambda p: p["ts"]):
        key = (item["session"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        by_session.setdefault(item["session"], []).append(item)
    sessions = []
    for session_id, items in by_session.items():
        substantive = [item for item in items if not NUDGE_PATTERN.match(item["text"])]
        if not substantive:
            continue
        goal = substantive[0]
        sessions.append({
            "session": session_id,
            "date": goal["ts"][:10],
            "goal": goal["text"][:600],
            "followups": [item["text"][:300] for item in substantive[1:13]],
            "nudges": len(items) - len(substantive),
            "source": "history",
        })
    sessions.sort(key=lambda item: item["date"])
    return sessions


def read_updates(brain: Brain) -> list[dict]:
    """What got shipped: `Update Log/*.md` next to the brain, plus the brain's session notes."""
    updates = []
    for folder in (brain.root / "Update Log", brain.dir / "Update Log"):
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            body = re.sub(r"^---.*?---", "", text, flags=re.DOTALL).strip()
            updates.append({
                "date": path.stem[:10],
                "title": path.stem,
                "summary": redact(re.sub(r"\s+", " ", body))[:700],
                "source": "update-log",
            })
    for note in brain.notes():
        if note["type"] != "session" or note.get("status") in ("", "live"):
            continue
        body = re.sub(r"\s+", " ", note["body"]).strip()
        updates.append({
            "date": str(note.get("created") or "")[:10],
            "title": note["title"],
            "summary": redact(f"[{note.get('status')}] {body}")[:700],
            "source": "session",
        })
    return updates


def learn(brain: Brain, transcripts: list[Path] | None = None, opencode_db: Path | None = None) -> dict:
    """Rebuild the library from every source that exists. Lessons (learned.jsonl) are kept."""
    from .watch import opencode_db_path

    folders = transcripts if transcripts is not None else [Path.home() / ".claude" / "transcripts"]
    prompts = []
    for folder in folders:
        prompts += read_jsonl_transcripts(Path(folder))
    prompts += read_opencode_prompts(opencode_db if opencode_db is not None else opencode_db_path())
    sessions = build_sessions(prompts)
    updates = read_updates(brain)
    folder = library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    # Keep precedents learned earlier from sources that are gone now (e.g. imported history).
    known = {row.get("session"): row for row in _read_jsonl(folder / "precedents.jsonl")}
    known.update({row["session"]: row for row in sessions})
    merged = sorted(known.values(), key=lambda row: row.get("date", ""))
    _write_jsonl(folder / "precedents.jsonl", merged)
    _write_jsonl(folder / "updates.jsonl", updates)
    lessons = _read_jsonl(folder / "learned.jsonl")
    nudges = sum(s["nudges"] for s in sessions)
    stats = [
        "# Precedent library (generated by `cairn precedent learn`)",
        "",
        f"- Prompts read: {len(prompts)}",
        f"- Sessions with a goal: {len(merged)} ({len(sessions)} from current sources)",
        f"- Pure nudges (go / done? / continue): {nudges}",
        f"- Shipped notes (Update Log + sessions): {len(updates)}",
        f"- Lessons logged: {len(lessons)}",
        f"- Updated: {dt.datetime.now():%Y-%m-%d %H:%M}",
    ]
    (folder / "stats.md").write_text("\n".join(stats) + "\n", encoding="utf-8")
    return {"prompts": len(prompts), "precedents": len(merged), "updates": len(updates),
            "lessons": len(lessons), "nudges": nudges, "folder": str(folder)}


def library_age_days() -> float | None:
    path = library_dir() / "precedents.jsonl"
    if not path.exists():
        return None
    return (dt.datetime.now().timestamp() - path.stat().st_mtime) / 86400


# ---------------------------------------------------------------- search


def tokenize(text: str) -> list[str]:
    return [
        token for token in TOKEN_PATTERN.findall((text or "").lower())
        if (token not in STOPWORDS and len(token) > 1) or "\u4e00" <= token <= "\u9fff"
    ]


def _documents() -> list[tuple[dict, Counter]]:
    folder = library_dir()
    sources = [
        ("precedents.jsonl", "precedent", lambda r: r.get("goal", "") + " " + " ".join(r.get("followups", []))),
        ("updates.jsonl", "shipped", lambda r: r.get("title", "") + " " + r.get("summary", "")),
        ("learned.jsonl", "lesson", lambda r: " ".join(str(v) for v in r.values())),
    ]
    documents = []
    for filename, kind, to_text in sources:
        for row in _read_jsonl(folder / filename):
            row = dict(row, _kind=kind)
            documents.append((row, Counter(tokenize(to_text(row)))))
    return documents


def search(query: str, k: int = 5) -> list[tuple[float, dict]]:
    documents = _documents()
    if not documents:
        return []
    count = len(documents)
    frequency: Counter = Counter()
    for _, counts in documents:
        frequency.update(counts.keys())

    def weigh(counts: Counter) -> dict:
        return {t: (1 + math.log(c)) * math.log(1 + count / (1 + frequency[t])) for t, c in counts.items()}

    query_vector = weigh(Counter(tokenize(query)))
    query_norm = math.sqrt(sum(v * v for v in query_vector.values())) or 1.0
    recent = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    scored = []
    for row, counts in documents:
        vector = weigh(counts)
        dot = sum(w * vector.get(t, 0.0) for t, w in query_vector.items())
        if dot <= 0:
            continue
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        boost = 1.1 if str(row.get("date", "")) >= recent else 1.0
        scored.append((dot / (query_norm * norm) * boost, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:k]


def log_lesson(goal: str, delivered: str, correction: str = "") -> dict:
    row = {
        "date": dt.date.today().isoformat(),
        "goal": redact(goal.strip())[:500],
        "delivered": redact(delivered.strip())[:800],
        "correction": redact(correction.strip())[:500],
    }
    path = library_dir() / "learned.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def hidden_asks(results: list[tuple[float, dict]], limit: int = 8) -> list[str]:
    """Follow-ups of the closest past goals, most-shared first: the asks nobody wrote down."""
    seen = set()
    asks = []
    for _, row in results:
        if row["_kind"] != "precedent":
            continue
        for followup in row.get("followups", []):
            key = " ".join(tokenize(followup))[:80]
            if not key or key in seen or NUDGE_PATTERN.match(followup):
                continue
            seen.add(key)
            asks.append(followup[:220])
    return asks[:limit]


# ---------------------------------------------------------------- profile


def profile_text(brain: Brain) -> str:
    try:
        return core.parse_note(brain.read(PROFILE_PATH)["text"])["body"].strip()
    except (OSError, ValueError, Exception):  # noqa: BLE001 - missing or unreadable profile is normal
        return ""


def ensure_profile(brain: Brain, body: str = "") -> tuple[str, bool]:
    """Create notes/precedent-profile.md from `body` (or the template). Never overwrites."""
    if brain.resolve(PROFILE_PATH).exists():
        return PROFILE_PATH, False
    meta = {"title": "Precedent profile", "type": "note", "tags": ["precedent"], "created": dt.date.today().isoformat()}
    text = core.serialize_note(meta, (body.strip() or PROFILE_TEMPLATE).rstrip() + "\n")
    brain.write(PROFILE_PATH, text, None)
    return PROFILE_PATH, True


# ---------------------------------------------------------------- queue (tasks tagged #queue)


def queue_items(brain: Brain, include_done: bool = False) -> list[dict]:
    items = []
    for note in brain.notes():
        tags = [t.lower() for t in note["tags"]]
        if note["type"] != "task" or QUEUE_TAG not in tags:
            continue
        if note["status"] == "done" and not include_done:
            continue
        items.append({
            "path": note["path"],
            "title": note["title"],
            "text": _queue_text(note),
            "status": note["status"],
            "auto": AUTO_TAG in tags,
            "needs_me": NEEDS_ME_TAG in tags,
            "created": str(note.get("created") or ""),
            "tags": note["tags"],
        })
    order = {"doing": 0, "todo": 1, "done": 2}
    items.sort(key=lambda i: (order.get(i["status"], 1), i["needs_me"], i["created"], i["path"]))
    return items


def _queue_text(note: dict) -> str:
    """The full ask: the body's first paragraph when the title was shortened, else the title."""
    body = note["body"].strip()
    first = body.split("\n\n", 1)[0].strip() if body else ""
    if first and first.startswith(note["title"].rstrip(".").rstrip("…")[:40]):
        return first
    return note["title"]


def queue_next(brain: Brain, auto_only: bool = False, exclude: set[str] | None = None) -> dict | None:
    for item in queue_items(brain):
        if item["needs_me"] or (exclude and item["path"] in exclude):
            continue
        if auto_only and not item["auto"]:
            continue
        return item
    return None


def _title_for(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def queue_add(brain: Brain, text: str, auto: bool = False, needs_me: bool = False, extra: str = "") -> dict:
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("Give the queue item text.")
    found = set(re.findall(r"#([\w-]+)", text))
    auto = auto or "auto" in found
    needs_me = needs_me or bool(found & {"needs-me", "needs-juwen", "needs-you"})
    clean = re.sub(r"\s#(auto|needs-me|needs-juwen|needs-you|queue)\b", "", " " + text).strip()
    tags = [QUEUE_TAG] + ([AUTO_TAG] if auto else []) + ([NEEDS_ME_TAG] if needs_me else [])
    title = _title_for(clean)
    body = clean if title != clean else ""
    if extra:
        body = (body + "\n\n" if body else "") + extra
    brain.init()
    return brain.create("task", title, tags, body + ("\n" if body else ""), dt.date.today().isoformat())


def queue_done(brain: Brain, path: str, result: str = "") -> None:
    current = brain.read(path)
    body = core.parse_note(current["text"])["body"].rstrip()
    line = f"Done {dt.date.today().isoformat()}" + (f": {result.strip()}" if result.strip() else "")
    updated = core.update_note_text(current["text"], {"status": "done"}, (body + "\n\n" if body else "") + line + "\n")
    brain.write(path, updated, current["mtime"])


# ---------------------------------------------------------------- the brief


def brief(brain: Brain, goal: str, k: int = 5) -> str:
    goal = " ".join((goal or "").split())
    results = search(goal, k + 3) if goal else []
    precedents = [r for r in results if r[1]["_kind"] == "precedent"][:k]
    shipped = [r for r in results if r[1]["_kind"] == "shipped"][:3]
    lessons = [r for r in results if r[1]["_kind"] == "lesson"][:3]
    lines = [f"# Precedent brief: {goal or '(no goal: take the top queue item)'}", ""]

    lines += ["## Definition of Done (draft: confirm or adjust, then write it into your reply)", ""]
    lines.append(f"- Literal ask: {goal or 'see the queue below'}")
    asks = hidden_asks(precedents)
    if asks:
        lines.append("- Hidden asks (what was asked for next after similar goals):")
        lines += [f"  - {ask}" for ask in asks]
    lines.append("- Proof for each item: open the live result (page loads, app opens, test passes, file count, screenshot).")
    lines.append("")

    if precedents:
        lines += ["## Closest precedents", ""]
        for score, row in precedents:
            lines.append(f"- [{score:.2f}] {row.get('date', '')}: {row.get('goal', '')[:300]}")
            for followup in row.get("followups", [])[:5]:
                lines.append(f"  - then: {followup[:200]}")
        lines.append("")
    if shipped:
        lines += ["## Shipped before (similar)", ""]
        lines += [f"- {row.get('date', '')} {row.get('title', '')}: {row.get('summary', '')[:240]}" for _, row in shipped]
        lines.append("")
    if lessons:
        lines += ["## Lessons logged", ""]
        for _, row in lessons:
            correction = f" | correction: {row['correction']}" if row.get("correction") else ""
            lines.append(f"- {row.get('date', '')} {row.get('goal', '')[:160]} -> {row.get('delivered', '')[:200]}{correction}")
        lines.append("")
    if not results:
        age = library_age_days()
        lines += ["## Precedents", "",
                  "- None found." + (" Run `cairn precedent learn` to build the library from your history." if age is None else ""),
                  ""]

    if goal:
        from .mcp import search_notes

        related = search_notes(brain.notes(), " ".join(tokenize(goal)[:4]), limit=4)
        if related:
            lines += ["## Related in this brain", ""]
            lines += [f"- brain/{note['path']} ({note['type']}) {note['title']}" for _, note, _ in related]
            lines.append("")

    profile = profile_text(brain)
    lines += ["## Profile (brain/" + PROFILE_PATH + ")", ""]
    lines.append(profile[:6000] if profile else "- No profile yet: `cairn precedent init` creates one. Until then, ask nothing you can decide; write `Assumed: ...`.")
    lines.append("")

    queue = queue_items(brain)[:5]
    if queue:
        lines += ["## Queue (tasks tagged #queue)", ""]
        for item in queue:
            flags = (" #auto" if item["auto"] else "") + (" #needs-me" if item["needs_me"] else "")
            lines.append(f"- [{item['status']}] {item['title']}{flags} (brain/{item['path']})")
        lines.append("")
    lines.append(LOOP)
    return "\n".join(lines)


# ---------------------------------------------------------------- moving in from the standalone skill


def import_legacy(brain: Brain, skill_dir: Path | None = None, queue_file: Path | None = None) -> dict:
    """One-time move from a standalone precedent skill folder and its Queue.md into Cairn.

    Copies the library (data/*.jsonl) where Cairn has none, merges lessons, turns profile.md into
    the profile note and every open `- [ ]` line of the queue file into a #queue task.
    Safe to re-run: nothing is duplicated or overwritten.
    """
    report = {"library": 0, "lessons": 0, "profile": False, "queue": 0, "skipped_queue": 0}
    folder = library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if skill_dir and Path(skill_dir).exists():
        data = Path(skill_dir) / "data"
        for name in ("precedents.jsonl", "updates.jsonl"):
            source, target = data / name, folder / name
            if source.exists() and not target.exists():
                rows = _read_jsonl(source)
                _write_jsonl(target, rows)
                report["library"] += len(rows)
        lessons = _read_jsonl(folder / "learned.jsonl")
        known = {json.dumps(row, sort_keys=True, ensure_ascii=False) for row in lessons}
        for row in _read_jsonl(data / "learned.jsonl"):
            key = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if key not in known:
                lessons.append(row)
                known.add(key)
                report["lessons"] += 1
        if report["lessons"]:
            _write_jsonl(folder / "learned.jsonl", lessons)
        profile_file = Path(skill_dir) / "profile.md"
        if profile_file.exists():
            body = profile_file.read_text(encoding="utf-8")
            body = re.sub(r"^# .*\n", "", body, count=1)
            _, report["profile"] = ensure_profile(brain, body)
    if queue_file and Path(queue_file).exists():
        existing = {" ".join(i["text"].split()).lower() for i in queue_items(brain, include_done=True)}
        existing |= {i["title"].lower() for i in queue_items(brain, include_done=True)}
        section = ""
        for line in Path(queue_file).read_text(encoding="utf-8").splitlines():
            heading = re.match(r"\s*#{2,4}\s+(.+)", line)
            if heading:
                section = heading.group(1).strip()
                continue
            match = re.match(r"\s*- \[ \]\s+(.+)", line)
            if not match:
                continue
            text = " ".join(match.group(1).split())
            clean = re.sub(r"\s#(auto|needs-me|needs-juwen|needs-you|queue)\b", "", " " + text).strip()
            if clean.lower() in existing or _title_for(clean).lower() in existing:
                report["skipped_queue"] += 1
                continue
            extra = f"From the old queue ({section})." if section and section not in ("Now", "Next") else ""
            queue_add(brain, text, extra=extra)
            existing.add(clean.lower())
            report["queue"] += 1
    return report


# ---------------------------------------------------------------- agent commands (/precedent)

MARKER = "<!-- cairn:managed -->"
OLD_MARKERS = (MARKER, "<!-- precedent:managed -->")


def agent_command_files() -> list[Path]:
    home = Path.home()
    return [
        home / ".claude" / "commands" / "precedent.md",
        home / ".config" / "opencode" / "command" / "precedent.md",
        home / ".codex" / "prompts" / "precedent.md",
    ]


def command_body(entry: dict) -> str:
    quoted = " ".join(f'"{part}"' if " " in part else part for part in [entry["command"], *entry["args"][:-1]])
    return f"""---
description: Precedent - give a goal once; Cairn adds what you asked for after similar goals, then the agent does it, verifies, ships and keeps going
---
{MARKER}

Run this and follow the brief it prints exactly (it includes the working loop):

    {quoted} precedent brief "$ARGUMENTS"

If the goal is empty or just "go"/"continue", run `{quoted} precedent next` and work on that item.
When done: `{quoted} precedent log "<goal>" "<what you delivered>" "<correction, if any>"`.
Follow-ups go in the queue: `{quoted} precedent queue "<item>"` (add --auto only if safe unattended).
"""


def install_commands(brain: Brain) -> list[str]:
    """Write /precedent commands for agents that exist here. Only replaces files we (or the old skill) made."""
    from .mcp import server_entry

    entry = server_entry(brain)
    written = []
    for path in agent_command_files():
        if not path.parent.parent.exists():
            continue
        if path.exists() and not any(m in path.read_text(encoding="utf-8", errors="ignore") for m in OLD_MARKERS):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(command_body(entry), encoding="utf-8")
        written.append(str(path))
    return written

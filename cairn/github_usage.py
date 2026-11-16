"""How much of the month's free GitHub Actions minutes are used (`cairn vibe watch` notes it hourly).

GitHub gives a personal account a monthly allowance of Actions minutes for **private** repos (public
repos are free) and mails a warning at 75%, 90% and 100%. This reads the account's billing usage,
works out the same figure, and leaves it in one note per month in `github-usage/`, so the Vibe page
can show a meter, warn before the allowance runs out, and keep a history of past months.

It is an estimate: GitHub's own meter is the authority and can read higher (it can lag this API, and
a repo that was private earlier in the month and is public now is counted there but not here).
Read-only towards GitHub; it needs the same token `cairn publish` uses.
"""

from __future__ import annotations

import calendar
import datetime as dt
import subprocess
import time

from . import core, publish
from .brain import Brain, ConflictError

FOLDER = "github-usage"
REFRESH_MINUTES = 60  # the current month's note is rewritten this often
RETRY_SECONDS = 1800  # after GitHub could not be reached, wait this long before asking again
HISTORY_MONTHS = 5  # past months filled in once, so the history does not start empty
PACE_DAYS = 7  # the month-end figure assumes the pace of this many most recent days
TOP_REPOS = 6
PLAN_MINUTES = {"free": 2000, "pro": 3000}
DEFAULT_MINUTES = 2000
WARN_PCT, CRITICAL_PCT = 75, 90  # the points GitHub itself mails at, with 100

_retry_after = 0.0


def multiplier(sku: str) -> int:
    """What one minute on this runner costs in allowance minutes: Windows 2, macOS 10, Linux 1."""
    name = str(sku).lower()
    if "macos" in name:
        return 10
    if "windows" in name:
        return 2
    return 1


def usage_level(pct: int, projected_pct: int) -> str:
    if pct >= 100:
        return "over"
    if pct >= CRITICAL_PCT:
        return "critical"
    if pct >= WARN_PCT or projected_pct >= 100:
        return "warn"
    return "ok"


def quota(items: list[dict], public: set[str], year: int, month: int, day: int, limit: int) -> dict:
    """One month's reading from the billing usage rows. `public` names the repos whose minutes are
    free; a repo not in it (private, or deleted since) counts, so the figure errs on the high side.
    `day` is the last day to count from: today for the running month, its last day for a past one."""
    last_day = calendar.monthrange(year, month)[1]
    day = max(1, min(day, last_day))
    days = [0.0] * day
    repos: dict[str, float] = {}
    free = 0.0
    for item in items:
        if str(item.get("product", "")).lower() != "actions" or str(item.get("unitType", "")).lower() != "minutes":
            continue  # storage is metered separately, in gigabyte-hours
        stamp = str(item.get("date", ""))[:10]
        try:
            when = dt.date.fromisoformat(stamp)
            minutes = float(item.get("quantity") or 0) * multiplier(item.get("sku", ""))
        except (TypeError, ValueError):
            continue
        if (when.year, when.month) != (year, month):
            continue
        name = str(item.get("repositoryName") or "")
        if name in public:
            free += minutes
            continue
        repos[name or "(no repo)"] = repos.get(name or "(no repo)", 0.0) + minutes
        if when.day <= day:
            days[when.day - 1] += minutes
    used = round(sum(repos.values()))
    recent = days[-PACE_DAYS:]
    projected = round(used + sum(recent) / len(recent) * (last_day - day))
    limit = max(1, int(limit))
    pct = round(used * 100 / limit)
    projected_pct = round(projected * 100 / limit)
    reset = dt.date(year + (month == 12), month % 12 + 1, 1)
    top = sorted(repos.items(), key=lambda pair: (-pair[1], pair[0]))[:TOP_REPOS]
    return {
        "month": f"{year:04d}-{month:02d}",
        "used": used,
        "limit": limit,
        "pct": pct,
        "level": usage_level(pct, projected_pct),
        "projected": projected,
        "projected_pct": projected_pct,
        "resets": reset.isoformat(),
        "repos": [(name, round(minutes)) for name, minutes in top if round(minutes)],
        "days": [round(minutes) for minutes in days],
        "public": round(free),
    }


# ------------------------------------------------------------ the note


def note_path(month: str) -> str:
    return f"{FOLDER}/{month}.md"


def usage_note(reading: dict) -> tuple[str, str]:
    """(path, text) of the note that carries one month's reading. Frontmatter is what the Vibe page
    reads; the body says the same for a person (or a phone opening the brain from GitHub)."""
    repos = "; ".join(f"{name} {minutes}" for name, minutes in reading["repos"])
    final = bool(reading.get("final"))
    meta = {
        "title": f"GitHub Actions {reading['month']}",
        "type": "note",
        "tags": ["github"],
        "gh_account": str(reading.get("account", "")),
        "gh_month": reading["month"],
        "gh_at": str(reading.get("at", "")),
        "gh_used": str(reading["used"]),
        "gh_limit": str(reading["limit"]),
        "gh_pct": str(reading["pct"]),
        "gh_level": reading["level"],
        "gh_projected": str(reading["projected"]),
        "gh_projected_pct": str(reading["projected_pct"]),
        "gh_resets": reading["resets"],
        "gh_repos": repos,
        "gh_days": ";".join(str(minutes) for minutes in reading["days"]),
        "gh_public": str(reading["public"]),
        "gh_final": "yes" if final else "no",
    }
    lines = [
        "Written by the Cairn session watcher, so the Vibe page can show how much of the month's free",
        "GitHub Actions minutes are used. Only private repos count; public repos are free. It is an",
        "estimate from GitHub's billing usage: GitHub's own meter decides, and can read higher.",
        "",
        f"- {reading['used']:,} of {reading['limit']:,} free minutes used ({reading['pct']}%)",
    ]
    if not final:
        lines.append(
            f"- At the last {PACE_DAYS} days' pace: about {reading['projected']:,} by the end of the month "
            f"({reading['projected_pct']}%)"
        )
        lines.append(f"- Starts again from zero on {reading['resets']}")
    lines += [f"- Most minutes: {repos}"] if repos else []
    lines += [f"- Public repos: {reading['public']:,} minutes, free and not counted"] if reading["public"] else []
    lines += [f"- Read at {meta['gh_at'].replace('T', ' ')}"] if meta["gh_at"] else []
    if any(reading["days"]):
        lines += ["", "| Day | Minutes |", "| --- | ---: |"]
        lines += [f"| {reading['month']}-{index:02d} | {minutes} |" for index, minutes in enumerate(reading["days"], 1)]
    return note_path(reading["month"]), core.serialize_note(meta, "\n".join(lines) + "\n")


def _note_meta(brain: Brain, month: str) -> dict | None:
    path = note_path(month)
    if not brain.resolve(path).exists():
        return None
    return core.parse_note(brain.read(path)["text"])["meta"]


def _write(brain: Brain, reading: dict) -> None:
    path, text = usage_note(reading)
    if brain.resolve(path).exists():
        brain.write(path, text, brain.read(path)["mtime"])
    else:
        brain.write(path, text)


# ------------------------------------------------------------ asking GitHub


def _get(http, path: str, what: str):
    status, data = http("GET", path)
    if status >= 300 or data is None:
        message = data.get("message", "") if isinstance(data, dict) else ""
        raise publish.PublishError(f"GitHub refused to {what} ({status}{': ' + message if message else ''}).")
    return data


def fetch_account(http) -> tuple[str, int]:
    """(login, free minutes a month) of the signed-in account."""
    user = _get(http, "/user", "say whose token this is")
    plan = str((user.get("plan") or {}).get("name", "")).lower()
    return str(user["login"]), PLAN_MINUTES.get(plan, DEFAULT_MINUTES)


def fetch_public_repos(http) -> set[str]:
    names: set[str] = set()
    for page in range(1, 21):
        repos = _get(http, f"/user/repos?per_page=100&affiliation=owner&page={page}", "list your repos")
        names.update(str(repo["name"]) for repo in repos if not repo.get("private"))
        if len(repos) < 100:
            break
    return names


def fetch_usage(http, login: str, year: int, month: int) -> list[dict]:
    data = _get(http, f"/users/{login}/settings/billing/usage?year={year}&month={month}", "read your Actions usage")
    return list(data.get("usageItems") or [])


def _months_back(year: int, month: int, count: int) -> list[tuple[int, int]]:
    months = []
    for _ in range(count):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        months.append((year, month))
    return months


def _is_fresh(meta: dict | None, now: dt.datetime) -> bool:
    try:
        age = now - dt.datetime.fromisoformat(str((meta or {}).get("gh_at", "")))
    except ValueError:
        return False
    return abs(age.total_seconds()) < REFRESH_MINUTES * 60


def sync(brain: Brain, now: dt.datetime | None = None, http=None) -> bool:
    """Note this month's Actions minutes when the note is missing or an hour old, and fill in past
    months once. True when the current month's note was written. Quiet on purpose: no token, no
    network and a refusal from GitHub write nothing and are tried again later."""
    global _retry_after
    now = dt.datetime.now() if now is None else now
    today = now.astimezone(dt.timezone.utc).date()  # GitHub's month runs on UTC
    month = f"{today.year:04d}-{today.month:02d}"
    try:
        if _is_fresh(_note_meta(brain, month), now) or time.monotonic() < _retry_after:
            return False
        http = publish.make_http(publish.find_token()) if http is None else http
        login, limit = fetch_account(http)
        public = fetch_public_repos(http)
        stamp = now.strftime("%Y-%m-%dT%H:%M")
        reading = quota(fetch_usage(http, login, today.year, today.month), public, today.year, today.month, today.day, limit)
        _write(brain, {**reading, "account": login, "at": stamp})
        for year, past in _months_back(today.year, today.month, HISTORY_MONTHS):
            if (_note_meta(brain, f"{year:04d}-{past:02d}") or {}).get("gh_final") == "yes":
                continue
            closed = quota(fetch_usage(http, login, year, past), public, year, past, 31, limit)
            _write(brain, {**closed, "account": login, "at": stamp, "final": True})
        return True
    except (publish.PublishError, ConflictError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        _retry_after = time.monotonic() + RETRY_SECONDS
        return False

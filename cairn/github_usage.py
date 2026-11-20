"""How much of the month's free GitHub Actions minutes are used (`cairn vibe watch` notes it hourly).

GitHub gives a personal account a monthly allowance of Actions minutes for **private** repos (public
repos are free) and mails a warning at 75%, 90% and 100%. This reads the account's billing usage,
works out the same figure, and leaves it in one note per month in `github-usage/`, so the Vibe page
can show a meter, warn before the allowance runs out, and keep a history of past months.

GitHub's own meter is the authority, and the billing rows this reads can sum to less than it shows.
So the figure can be set to GitHub's number (`cairn vibe github 1801`, from its mail or billing page):
the note keeps that reading and adds what the billing rows show since, scaled by how far the rows
were behind at that moment. Read-only towards GitHub; it needs the same token `cairn publish` uses.

A second account you are signed in to with the GitHub CLI (`cairn vibe watch --github-account NAME`)
gets its own notes in `github-usage/NAME/`, read with that account's own sign-in, so the Vibe page
shows a meter for each. Minutes count for the account that owns a repo, whoever pushes to it.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import subprocess
import time

from . import core, publish
from .brain import Brain, ConflictError

FOLDER = "github-usage"
REFRESH_MINUTES = 60  # the current month's note is rewritten this often
RETRY_SECONDS = 1800  # after GitHub could not be reached, wait this long before asking again
HISTORY_MONTHS = 5  # past months filled in once, so the history does not start empty
PACE_DAYS = 7  # the month-end figure assumes the pace of this many most recent days
PACE_MIN_DAYS = 1.0  # after a reading of GitHub's meter, wait this long before pacing from it
SCALE_RANGE = (0.5, 3.0)  # how far GitHub's meter may sit from the billing rows before it looks like a typo
TOP_REPOS = 6
PLAN_MINUTES = {"free": 2000, "pro": 3000}
DEFAULT_MINUTES = 2000
WARN_PCT, CRITICAL_PCT = 75, 90  # the points GitHub itself mails at, with 100
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")  # a GitHub account name, and so a safe folder name

_retry_after = 0.0  # the main account
_retry_accounts: dict[str, float] = {}  # every other account, by name


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


# ------------------------------------------------------------ GitHub's own count


def _utc(moment: dt.datetime) -> dt.datetime:
    return moment.astimezone(dt.timezone.utc)  # a time without a zone is this computer's, as `sync` reads it


def anchor_of(meta: dict | None, month: str) -> dict | None:
    """The reading of GitHub's own meter a month's note keeps (`used` at `at`, when the billing rows
    summed to `api`), or None when it has none or it belongs to another month."""
    try:
        at = dt.datetime.fromisoformat(str((meta or {})["gh_anchor_at"]))
        anchor = {"used": int(meta["gh_anchor_used"]), "at": at, "api": int(meta["gh_anchor_api"])}
    except (KeyError, TypeError, ValueError):
        return None
    if _utc(at).strftime("%Y-%m") != month or anchor["used"] < 0 or anchor["api"] < 0:
        return None
    return anchor


def kept_anchor(meta: dict | None, month: str, login: str) -> dict | None:
    """GitHub's own figure kept in a note, when it was read for `login`. The main note follows whoever
    the GitHub CLI is signed in as, so after `gh auth switch` it becomes another account's month:
    counting on from the old figure there would show minutes that account never used."""
    noted = str((meta or {}).get("gh_account", "")).strip().lower()
    if noted and noted != login.strip().lower():
        return None
    return anchor_of(meta, month)


def calibrate(reading: dict, anchor: dict, now: dt.datetime, final: bool = False) -> dict:
    """The month's reading counted from GitHub's own figure: that figure, plus what the billing rows
    added since, scaled by how far the rows were behind when it was read. The month-end figure paces
    from the reading too, once a day has passed; before that it promises nothing."""
    api_used = int(reading["used"])
    low, high = SCALE_RANGE
    scale = min(high, max(low, anchor["used"] / anchor["api"])) if anchor["api"] else 1.0
    since = max(0, round((api_used - anchor["api"]) * scale))
    used = anchor["used"] + since
    limit = reading["limit"]
    elapsed = (_utc(now) - _utc(anchor["at"])).total_seconds() / 86400
    reset = dt.datetime.fromisoformat(reading["resets"]).replace(tzinfo=dt.timezone.utc)
    left = max(0.0, (reset - _utc(now)).total_seconds() / 86400)
    pace = None if final or elapsed < PACE_MIN_DAYS else since / elapsed
    projected = used if pace is None else round(used + pace * left)
    pct = round(used * 100 / limit)
    projected_pct = round(projected * 100 / limit)
    return {
        **reading,
        "used": used,
        "pct": pct,
        "projected": projected,
        "projected_pct": projected_pct,
        "level": usage_level(pct, projected_pct),
        "api_used": api_used,
        "since": since,
        "scale": round(scale, 3),
        "pace": None if pace is None else round(pace, 1),
        "anchor": anchor,
    }


# ------------------------------------------------------------ the note


def account_name(account: str) -> str:
    """A second account's name as given, or ValueError when it cannot be a GitHub account."""
    name = str(account or "").strip().lstrip("@")
    if not LOGIN_RE.fullmatch(name):
        raise ValueError(f"{account!r} is not a GitHub account name.")
    return name


def note_path(month: str, account: str = "") -> str:
    """Where a month's reading is kept: `github-usage/2026-11.md` for the main account (the one the
    default token belongs to), `github-usage/NAME/2026-11.md` for any other."""
    return f"{FOLDER}/{account_name(account)}/{month}.md" if account else f"{FOLDER}/{month}.md"


def usage_note(reading: dict, account: str = "") -> tuple[str, str]:
    """(path, text) of the note that carries one month's reading. Frontmatter is what the Vibe page
    reads; the body says the same for a person (or a phone opening the brain from GitHub)."""
    repos = "; ".join(f"{name} {minutes}" for name, minutes in reading["repos"])
    final = bool(reading.get("final"))
    command = f"cairn vibe github <minutes> --account {account}" if account else "cairn vibe github <minutes>"
    meta = {
        "title": f"GitHub Actions {reading['month']}" + (f" ({account})" if account else ""),
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
    anchor = reading.get("anchor")
    left = max(0, reading["limit"] - reading["used"])
    lines = [
        "Written by the Cairn session watcher, so the Vibe page can show how much of the month's free",
        "GitHub Actions minutes are used. Only private repos count; public repos are free.",
    ]
    if anchor:
        when = anchor["at"].strftime("%Y-%m-%dT%H:%M")
        pace = reading.get("pace")
        meta.update(
            {
                "gh_anchor_used": str(anchor["used"]),
                "gh_anchor_at": when,
                "gh_anchor_api": str(anchor["api"]),
                "gh_api_used": str(reading["api_used"]),
                "gh_since": str(reading["since"]),
                "gh_scale": f"{reading['scale']:g}",
                "gh_pace": "" if pace is None else f"{pace:g}",
            }
        )
        lines += [
            f"It counts on from GitHub's own figure (set with `{command}`), adding what",
            "GitHub's billing rows show since.",
            "",
            f"- {reading['used']:,} of {reading['limit']:,} free minutes used ({reading['pct']}%), {left:,} left",
            f"- GitHub's own count: {anchor['used']:,} at {when.replace('T', ' ')}, and {reading['since']:,} more since "
            f"(billing rows x{reading['scale']:g}, how far they were behind that count)",
        ]
        if not final:
            lines += (
                [f"- At the pace since that count ({pace:g} a day): about {reading['projected']:,} by the end of the month ({reading['projected_pct']}%)"]
                if pace is not None
                else []
            )
            lines.append(f"- Starts again from zero on {reading['resets']}")
        lines.append(f"- The billing rows itemise {reading['api_used']:,} of them" + (f": {repos}" if repos else ""))
    else:
        lines += [
            "It is summed from GitHub's billing rows, which can read lower than GitHub's own meter: run",
            f"`{command}` with the number GitHub shows or mails to count on from that.",
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
    return note_path(reading["month"], account), core.serialize_note(meta, "\n".join(lines) + "\n")


def _note_meta(brain: Brain, month: str, account: str = "") -> dict | None:
    path = note_path(month, account)
    if not brain.resolve(path).exists():
        return None
    return core.parse_note(brain.read(path)["text"])["meta"]


def _write(brain: Brain, reading: dict, account: str = "") -> None:
    path, text = usage_note(reading, account)
    if brain.resolve(path).exists():
        brain.write(path, text, brain.read(path)["mtime"])
    else:
        brain.write(path, text)


def _anchor_for(brain: Brain, month: str, account: str, login: str) -> dict | None:
    """GitHub's own figure for `login` this month. It is looked for in the note being written and in
    the other place the same account may be noted: an account is the main one while the GitHub CLI is
    signed in as it and a named one (`--account NAME`) otherwise, and its figure has to follow it."""
    found = [kept_anchor(_note_meta(brain, month, account), month, login)]
    elsewhere = None
    if account:
        elsewhere = _note_meta(brain, month)
    else:
        # The folder keeps the name as it was asked for, which need not be cased as GitHub has it.
        shelves = brain.dir / FOLDER
        names = [entry.name for entry in shelves.iterdir() if entry.is_dir()] if shelves.is_dir() else []
        for name in names:
            if name.lower() == login.strip().lower() and LOGIN_RE.fullmatch(name):
                elsewhere = _note_meta(brain, month, name)
    if str((elsewhere or {}).get("gh_account", "")).strip().lower() == login.strip().lower():
        found.append(anchor_of(elsewhere, month))
    return max((anchor for anchor in found if anchor), key=lambda anchor: _utc(anchor["at"]), default=None)


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
    path = f"/users/{login}/settings/billing/usage?year={year}&month={month}"
    status, data = http("GET", path)
    if status == 404:
        # GitHub answers "Not Found" to a sign-in that may not see billing, which reads like a typo.
        raise publish.PublishError(
            f"GitHub would not show the Actions usage of {login}. Its sign-in needs the `user` scope: "
            "make it the active account (gh auth switch) and run  gh auth refresh -h github.com -s user"
        )
    if status >= 300 or data is None:
        message = data.get("message", "") if isinstance(data, dict) else ""
        raise publish.PublishError(f"GitHub refused to read your Actions usage ({status}{': ' + message if message else ''}).")
    return list(data.get("usageItems") or [])


def account_token(account: str) -> str:
    """The GitHub CLI's token for one of the accounts signed in on this computer. Never printed or
    stored: it goes straight into the calls for that account."""
    name = account_name(account)
    try:
        result = subprocess.run(["gh", "auth", "token", "--hostname", "github.com", "--user", name], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    raise publish.PublishError(f"The GitHub CLI is not signed in as {name} on this computer. Sign in once with: gh auth login")


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


def _note_months(brain: Brain, now: dt.datetime, http, new_anchor: dict | None = None, account: str = "") -> dict:
    """Ask GitHub, write this month's note (counting on from `new_anchor`, else from the reading the
    note already keeps) and fill in past months once. Returns this month's reading. `account` names
    an account besides the main one: its own sign-in is used and its notes go in its own folder."""
    today = _utc(now).date()  # GitHub's month runs on UTC
    month = f"{today.year:04d}-{today.month:02d}"
    if http is None:
        http = publish.make_http(account_token(account) if account else publish.find_token())
    login, limit = fetch_account(http)
    if account and login.lower() != account.lower():
        raise publish.PublishError(f"That sign-in belongs to {login}, not {account}.")
    public = fetch_public_repos(http)
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    reading = quota(fetch_usage(http, login, today.year, today.month), public, today.year, today.month, today.day, limit)
    anchor = _anchor_for(brain, month, account, login) if new_anchor is None else new_anchor
    if anchor is not None:
        if anchor.get("api") is None:
            anchor = {**anchor, "api": reading["used"]}  # read just now, so the rows' sum of this moment
        reading = calibrate(reading, anchor, now)
    reading = {**reading, "account": login, "at": stamp}
    _write(brain, reading, account)
    for year, past in _months_back(today.year, today.month, HISTORY_MONTHS):
        name = f"{year:04d}-{past:02d}"
        meta = _note_meta(brain, name, account)
        if (meta or {}).get("gh_final") == "yes":
            continue
        closed = quota(fetch_usage(http, login, year, past), public, year, past, 31, limit)
        kept = kept_anchor(meta, name, login)  # a month that was counted from GitHub's figure closes on it too
        closed = calibrate(closed, kept, now, final=True) if kept else closed
        _write(brain, {**closed, "account": login, "at": stamp, "final": True}, account)
    return reading


def sync(brain: Brain, now: dt.datetime | None = None, http=None, account: str = "") -> bool:
    """Note this month's Actions minutes when the note is missing or an hour old, and fill in past
    months once. True when the current month's note was written. Quiet on purpose: no token, no
    network and a refusal from GitHub write nothing and are tried again later. `account` notes
    another account you are signed in to, in its own folder and with its own wait."""
    global _retry_after
    now = dt.datetime.now() if now is None else now
    month = _utc(now).strftime("%Y-%m")
    try:
        retry_after = _retry_accounts.get(account.lower(), 0.0) if account else _retry_after
        if _is_fresh(_note_meta(brain, month, account), now) or time.monotonic() < retry_after:
            return False
        _note_months(brain, now, http, account=account)
        return True
    except (publish.PublishError, ConflictError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        if account:
            _retry_accounts[account.lower()] = time.monotonic() + RETRY_SECONDS
        else:
            _retry_after = time.monotonic() + RETRY_SECONDS
        return False


def read_now(brain: Brain, account: str = "", now: dt.datetime | None = None, http=None) -> dict:
    """Ask GitHub at once, whatever the note's age, and return the month's reading. Where `sync` stays
    quiet, this raises PublishError with the reason, which is what someone setting an account up needs."""
    return _note_months(brain, dt.datetime.now() if now is None else now, http, account=account)


def sync_accounts(brain: Brain, accounts, now: dt.datetime | None = None, http_for=None) -> list[str]:
    """Note the minutes of every other account named, each with its own sign-in. Returns the names
    whose note was written. `http_for(name)` stands in for GitHub in tests."""
    noted = []
    for account in dict.fromkeys(str(name).strip().lstrip("@") for name in accounts or ()):
        if account and sync(brain, now, http_for(account) if http_for else None, account=account):
            noted.append(account)
    return noted


def set_anchor(
    brain: Brain,
    used: int,
    at: dt.datetime | None = None,
    api: int | None = None,
    now: dt.datetime | None = None,
    http=None,
    account: str = "",
) -> dict:
    """Count on from GitHub's own figure: `used` minutes at `at` (now when left out). `api` is what
    the billing rows summed to at that moment; left out, it is what they sum to right now, which is
    right for a figure just read. Asks GitHub at once and returns the month's reading. Raises
    ValueError for a figure that cannot be this month's, PublishError when GitHub cannot be reached."""
    now = dt.datetime.now() if now is None else now
    at = now if at is None else at
    if used < 0 or (api is not None and api < 0):
        raise ValueError("Minutes cannot be negative.")
    if _utc(at) > _utc(now) + dt.timedelta(minutes=5):
        raise ValueError("That reading is in the future.")
    if _utc(at).strftime("%Y-%m") != _utc(now).strftime("%Y-%m"):
        raise ValueError("That reading is from another month; GitHub's month runs on UTC.")
    return _note_months(brain, now, http, {"used": int(used), "at": at.replace(second=0, microsecond=0), "api": api}, account)


def summary(reading: dict) -> str:
    """One line a terminal can print after a reading."""
    left = max(0, reading["limit"] - reading["used"])
    who = f" ({reading['account']})" if reading.get("account") else ""
    line = f"GitHub Actions {reading['month']}{who}: {reading['used']:,} of {reading['limit']:,} minutes ({reading['pct']}%), {left:,} left."
    anchor = reading.get("anchor")
    if anchor:
        line += f" GitHub's count of {anchor['used']:,} at {anchor['at']:%Y-%m-%d %H:%M}, plus {reading['since']:,} since."
    return line

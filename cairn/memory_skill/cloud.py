"""The brain read and written straight on GitHub, for a computer that has no copy of it (or lost it).

GitHub cannot search inside a private repo's notes, so this keeps the notes' text in a small folder that it
builds and refreshes by itself: one download of the notes as they are now (no history, no pictures), then only
what changed. A new note is written on GitHub first and to that folder after. The folder can be deleted at any
time; it is made again from GitHub.

Standard library only, and nothing from Cairn's own code: this must still work when Cairn's folder is gone.
Needs a GitHub sign-in: `gh auth login`, the one git already uses, or GITHUB_TOKEN.
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

TEXT_SUFFIXES = (".md", ".canvas")
STATE = ".cairn-cloud.json"
FRESH_SECONDS = 60  # several questions in a row ask GitHub once
ONE_BY_ONE = 150  # more changed notes than this: one download of everything is quicker
MAC_CERTS = "/etc/ssl/cert.pem"
FOLDER_OF_TYPE = {"idea": "ideas", "task": "tasks", "decision": "decisions", "note": "notes",
                  "journal": "journal", "session": "sessions", "feedback": "feedback"}


class CloudError(Exception):
    """GitHub could not be asked or said no; the message says what to do."""


def api() -> str:
    return (os.environ.get("CAIRN_GITHUB_API") or "https://api.github.com").rstrip("/")


def cairn_home() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")


# ---------------------------------------------------------------- signing in


def find_token() -> str:
    for name in ("CAIRN_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    quiet = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    gh = shutil.which("gh") or next((path for path in ("/usr/local/bin/gh", "/opt/homebrew/bin/gh") if os.path.exists(path)), "")
    if gh:
        try:
            done = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=15,
                                  stdin=subprocess.DEVNULL, **quiet)
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:  # whatever `git push` to GitHub already uses; never asks here
        done = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                              capture_output=True, text=True, timeout=45, **quiet,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
        for line in done.stdout.splitlines():
            if line.startswith("password=") and len(line) > 12:
                return line[len("password="):].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raise CloudError("No GitHub sign-in on this computer. Run `gh auth login` once, or set GITHUB_TOKEN.")


def _certificates():
    """The Python from python.org on a Mac has no certificates until a script is run; use macOS's own."""
    if sys.platform != "darwin":
        return None
    paths = ssl.get_default_verify_paths()
    try:
        has_folder = bool(paths.capath) and bool(os.listdir(paths.capath))
    except OSError:
        has_folder = False
    if paths.cafile or has_folder or not os.path.isfile(MAC_CERTS):
        return None
    return ssl.create_default_context(cafile=MAC_CERTS)


class GitHub:
    def __init__(self, token: str = ""):
        self.token = token or find_token()
        self.context = _certificates()

    def ask(self, method: str, path: str, body: dict | None = None, raw: bool = False, timeout: int = 60):
        """(status, parsed JSON or bytes). 0 means GitHub could not be reached at all."""
        request = urllib.request.Request(
            api() + path, method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "User-Agent": "cairn-memory", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=self.context) as response:
                status, data = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, data = error.code, error.read()
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            return 0, None
        if raw and status < 300:
            return status, data
        try:
            return status, json.loads(data.decode("utf-8")) if data else None
        except ValueError:
            return status, None


def refusal(status: int, data, what: str) -> CloudError:
    if status == 0:
        return CloudError(f"GitHub cannot be reached, so I could not {what}. Check the connection and try again.")
    said = data.get("message", "") if isinstance(data, dict) else ""
    hint = {401: " The GitHub sign-in on this computer is no longer valid: run `gh auth login`.",
            403: " This sign-in may not do that, or GitHub's hourly limit is used up.",
            404: " This sign-in cannot see that repo, or it is not there."}.get(status, "")
    return CloudError(f"GitHub refused to {what} ({status}{': ' + said if said else ''}).{hint}")


# ---------------------------------------------------------------- names and front matter (as Cairn writes them)


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M")).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60].strip("-") or "untitled"


def _scalar(value: str) -> str:
    plain = re.match(r"^[A-Za-z0-9][A-Za-z0-9 _./()+-]*$", value) and value.lower() not in ("true", "false", "null", "yes", "no")
    return value if plain else json.dumps(value, ensure_ascii=False)


def note_text(title: str, kind: str, tags: list[str], body: str, today: str, status: str = "") -> str:
    lines = ["---", f"title: {_scalar(title)}", f"type: {kind}"]
    if status:
        lines.append(f"status: {status}")
    lines += [f"tags: [{', '.join(tags)}]", f"created: {today}", "---"]
    return "\n".join(lines) + "\n" + body


def front_matter(text: str) -> dict:
    """The few fields a result line shows. Not a YAML reader."""
    fields = {}
    if text.startswith("---"):
        for line in text.split("\n---", 1)[0].splitlines()[1:]:
            key, colon, value = line.partition(":")
            if colon and key.strip() in ("title", "type", "status", "tags"):
                fields[key.strip()] = value.strip().strip("\"'")
    return fields


def _body(text: str) -> str:
    if text.startswith("---") and "\n---" in text:
        return text.split("\n---", 1)[1].lstrip("\n")
    return text


# ---------------------------------------------------------------- the brain on GitHub


class Cloud:
    def __init__(self, repo: str, folder: str = "", branch: str = "", cache: Path | None = None, github: GitHub | None = None):
        if not re.match(r"^[\w.-]+/[\w.-]+$", repo or ""):
            raise CloudError(f"Not a GitHub repo name: {repo!r} (expected owner/name).")
        self.repo = repo
        self.folder = folder.strip("/")
        self.branch = branch
        name = re.sub(r"[^\w.-]+", "_", repo + ("_" + self.folder if self.folder else ""))
        self.cache = cache or cairn_home() / "cloud" / name
        self._github = github
        self.note = ""  # one line for the reader: where this answer comes from

    @property
    def github(self) -> GitHub:
        if self._github is None:
            self._github = GitHub()
        return self._github

    # ------------------------------------------------------------ state

    def _state(self) -> dict:
        try:
            state = json.loads((self.cache / STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        same = isinstance(state, dict) and state.get("repo") == self.repo and state.get("folder", "") == self.folder
        return state if same else {}

    def _save_state(self, state: dict) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        state.update({"repo": self.repo, "folder": self.folder})
        temporary = self.cache / (STATE + ".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.cache / STATE)

    def _inside(self, relative: str) -> Path:
        target = (self.cache / relative).resolve()
        if self.cache.resolve() not in target.parents:
            raise CloudError(f"Not a path inside the brain: {relative!r}")
        return target

    def _remote(self, relative: str) -> str:
        return urllib.parse.quote((self.folder + "/" if self.folder else "") + relative)

    # ------------------------------------------------------------ refresh

    def refresh(self, max_age: int = FRESH_SECONDS) -> None:
        """Make the folder match GitHub. With no connection, an older folder is used and the reader is told."""
        state = self._state()
        age = time.time() - float(state.get("checked", 0))
        if state.get("shas") and 0 <= age < max_age:
            self.note = self._describe(state)
            return
        try:
            self._refresh(state)
        except CloudError as error:
            if not state.get("shas"):
                raise
            self.note = (f"GitHub was not reached ({error}) This is {self.repo} as it was at "
                         f"{dt.datetime.fromtimestamp(float(state.get('checked', 0))):%Y-%m-%d %H:%M}.")
            return
        self.note = self._describe(self._state())

    def _describe(self, state: dict) -> str:
        return (f"From GitHub ({self.repo}, {len(state.get('shas', {}))} notes as of "
                f"{dt.datetime.fromtimestamp(float(state.get('checked', 0))):%Y-%m-%d %H:%M}); "
                "this computer keeps no synced copy of the brain.")

    def _refresh(self, state: dict) -> None:
        github = self.github
        branch = self.branch or state.get("branch", "")
        if not branch:
            status, data = github.ask("GET", f"/repos/{self.repo}")
            if status != 200:
                raise refusal(status, data, f"open {self.repo}")
            branch = data["default_branch"]
        status, data = github.ask("GET", f"/repos/{self.repo}/branches/{urllib.parse.quote(branch)}")
        if status != 200:
            raise refusal(status, data, f"read branch {branch} of {self.repo}")
        commit = data["commit"]["sha"]
        when = (((data["commit"].get("commit") or {}).get("committer") or {}).get("date")) or ""
        if state.get("commit") == commit and state.get("shas"):
            state.update({"checked": time.time(), "branch": branch})
            self._save_state(state)
            return

        status, data = github.ask("GET", f"/repos/{self.repo}/git/trees/{commit}?recursive=1")
        if status != 200:
            raise refusal(status, data, "list the notes")
        prefix = self.folder + "/" if self.folder else ""
        wanted = {}
        for entry in data.get("tree", []):
            path = entry.get("path", "")
            if entry.get("type") == "blob" and path.startswith(prefix) and path.lower().endswith(TEXT_SUFFIXES):
                wanted[path[len(prefix):]] = entry["sha"]
        have = state.get("shas") or {}
        changed = [path for path, sha in wanted.items() if have.get(path) != sha or not (self.cache / path).is_file()]
        gone = [path for path in have if path not in wanted]

        if len(changed) > ONE_BY_ONE or data.get("truncated"):
            self._download_everything(commit, prefix, wanted)
        else:
            for path in changed:
                status, blob = github.ask("GET", f"/repos/{self.repo}/git/blobs/{wanted[path]}")
                if status != 200 or not isinstance(blob, dict):
                    raise refusal(status, blob, f"read {path}")
                self._put(path, base64.b64decode(blob.get("content", "")))
        for path in gone:
            try:
                self._inside(path).unlink()
            except OSError:
                pass
        self._save_state({"commit": commit, "committed": when, "branch": branch, "shas": wanted, "checked": time.time()})

    def _put(self, relative: str, data: bytes) -> None:
        target = self._inside(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".cairn-tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)

    def _download_everything(self, commit: str, prefix: str, wanted: dict) -> None:
        status, data = self.github.ask("GET", f"/repos/{self.repo}/zipball/{commit}", raw=True, timeout=600)
        if status != 200 or not isinstance(data, (bytes, bytearray)):
            raise refusal(status, data if isinstance(data, dict) else None, "download the notes")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                inner = info.filename.split("/", 1)[1] if "/" in info.filename else ""
                if info.is_dir() or not inner.startswith(prefix):
                    continue
                relative = inner[len(prefix):]
                if relative in wanted:
                    self._put(relative, archive.read(info))

    # ------------------------------------------------------------ reading

    def paths(self) -> list[str]:
        return sorted((self._state().get("shas") or {}).keys())

    def _text(self, relative: str) -> str:
        try:
            return self._inside(relative).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _line(self, relative: str, text: str) -> str:
        fields = front_matter(text)
        kind = fields.get("type") or next((k for k, f in FOLDER_OF_TYPE.items() if relative.startswith(f + "/")), "note")
        status = f" [{fields['status']}]" if fields.get("status") else ""
        tags = " ".join("#" + tag.strip() for tag in fields.get("tags", "").strip("[]").split(",") if tag.strip())
        return f"- {relative} ({kind}{status}) {fields.get('title') or Path(relative).stem}{' ' + tags if tags else ''}"

    def search(self, query: str, note_type: str = "", limit: int = 10) -> str:
        words = [word.lower() for word in query.split() if word.strip()]
        if not words:
            raise CloudError("Give words to search for.")
        found = []
        for relative in self.paths():
            if not relative.endswith(".md"):
                continue
            text = self._text(relative)
            fields = front_matter(text)
            if note_type and (fields.get("type") or "") != note_type and not relative.startswith(FOLDER_OF_TYPE.get(note_type, "\0") + "/"):
                continue
            head = (relative + " " + fields.get("title", "") + " " + fields.get("tags", "")).lower()
            lower = text.lower()
            if not all(word in head or word in lower for word in words):
                continue
            score = sum(3 for word in words if word in head) + sum(1 for word in words if word in lower)
            body = _body(text)
            at = min((position for position in (body.lower().find(word) for word in words) if position >= 0), default=0)
            excerpt = " ".join(body[max(0, at - 60): at + 160].split())
            found.append((-score, relative, self._line(relative, text) + (f"\n  ...{excerpt}..." if excerpt else "")))
        found.sort(key=lambda row: (row[0], row[1]))
        if not found:
            return f"No notes match {query!r}."
        return f"{len(found)} match(es) for {query!r}:\n" + "\n".join(row[2] for row in found[: max(1, min(limit, 50))])

    def find(self, reference: str) -> str:
        """A note by path, by file name, or by a few words of its name or title."""
        paths = self.paths()
        wanted = reference.strip().strip("/")
        for candidate in (wanted, wanted + ".md"):
            if candidate in paths:
                return candidate
        stem = slugify(Path(wanted).stem)
        named = [path for path in paths if slugify(Path(path).stem) == stem]
        if not named:
            named = [path for path in paths if stem in slugify(Path(path).stem)]
        if not named:
            lower = wanted.lower()
            named = [path for path in paths if path.endswith(".md") and front_matter(self._text(path)).get("title", "").lower() == lower]
        if not named:
            raise CloudError(f"No note called {reference!r}. Try: recall.py search \"...\"")
        if len(named) > 1:
            raise CloudError(f"{len(named)} notes match {reference!r}; give the path of one:\n" + "\n".join("  " + path for path in named[:12]))
        return named[0]

    def read(self, reference: str) -> str:
        relative = self.find(reference)
        return f"# {relative}\n\n{self._text(relative)}"

    def recent(self, days: int = 7) -> str:
        today = dt.date.today()
        parts = []
        for back in range(max(1, min(days, 60))):
            day = (today - dt.timedelta(days=back)).isoformat()
            relative = f"journal/{day}.md"
            if relative in self.paths():
                lines = [line for line in _body(self._text(relative)).splitlines() if line.strip()]
                more = f"\n  (... {len(lines) - 40} more lines: recall.py read {relative})" if len(lines) > 40 else ""
                parts.append(f"## {day}\n" + "\n".join(lines[-40:]) + more)
        return "\n\n".join(parts) if parts else f"No journal pages in the last {days} day(s)."

    def rules(self, write_folder: str = "") -> str:
        folder = (write_folder.strip("/") + "/" if write_folder else "") + "decisions/"
        rows = []
        for relative in self.paths():
            if relative.startswith(folder) and relative.endswith(".md"):
                fields = front_matter(self._text(relative))
                if write_folder or fields.get("status") == "accepted":
                    rows.append(f"- {relative}\n    {fields.get('title') or Path(relative).stem}")
        if not rows:
            return "No standing decisions found."
        return f"Standing decisions ({folder}):\n" + "\n".join(rows)

    def brief(self) -> str:
        for name in ("CONTEXT.md", "README.md"):
            if name in self.paths():
                return self._text(name)
        return "This brain has no CONTEXT.md. Use: recall.py recent, recall.py rules, recall.py search."

    # ------------------------------------------------------------ writing

    def new_path(self, kind: str, title: str, today: str, write_folder: str = "") -> str:
        """Where a new note goes, named the way Cairn names it (and numbered the way the folder already is)."""
        slug = slugify(title)
        base = write_folder.strip("/") + "/" if write_folder else ""
        paths = self.paths()
        if kind == "decision":
            folder = base + "decisions/"
            prefixes, highest = {}, {}
            for path in paths:
                match = re.match(r"^((?:[a-z]+-)?)(\d{4})-", path[len(folder):]) if path.startswith(folder) else None
                if match:
                    prefixes[match.group(1)] = prefixes.get(match.group(1), 0) + 1
                    highest[match.group(1)] = max(highest.get(match.group(1), 0), int(match.group(2)))
            prefix = max(prefixes, key=prefixes.get) if prefixes else ""
            return f"{folder}{prefix}{highest.get(prefix, 0) + 1:04d}-{slug}.md"
        return f"{base}notes/{today + '-' if write_folder else ''}{slug}.md"

    def save(self, relative: str, text: str, message: str) -> dict:
        """Create one note on GitHub. Never replaces a note that is there."""
        body = {"message": message, "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if self.branch or self._state().get("branch"):
            body["branch"] = self.branch or self._state()["branch"]
        status, data = self.github.ask("PUT", f"/repos/{self.repo}/contents/{self._remote(relative)}", body)
        if status == 422:
            raise CloudError(f"Not saved: {relative} already exists on GitHub. Add to that note instead.")
        if status not in (200, 201) or not isinstance(data, dict):
            raise refusal(status, data, f"save {relative}")
        self._put(relative, text.encode("utf-8"))
        state = self._state()
        state.setdefault("shas", {})[relative] = (data.get("content") or {}).get("sha", "")
        self._save_state(state)
        return {"path": relative, "commit": ((data.get("commit") or {}).get("sha") or "")[:7]}

    def on_github(self, relative: str) -> int:
        """The size GitHub holds for a note now (0: not there)."""
        status, data = self.github.ask("GET", f"/repos/{self.repo}/contents/{self._remote(relative)}")
        return int(data.get("size", 0)) if status == 200 and isinstance(data, dict) else 0


def repo_of(remote: str) -> str:
    """owner/name from a GitHub address as git prints it ("" when it is not GitHub)."""
    match = re.search(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", (remote or "").strip())
    return f"{match.group(1)}/{match.group(2)}" if match else ""

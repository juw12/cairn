"""Serve docs/ as plain static files (the GitHub Pages runtime) for browser tests."""

from __future__ import annotations

import base64
import functools
import hashlib
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

DOCS = Path(__file__).resolve().parents[1] / "docs"


class QuietHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".webmanifest": "application/manifest+json",
    }

    def log_message(self, *args):
        pass


def start_static_server() -> tuple[ThreadingHTTPServer, str]:
    handler = functools.partial(QuietHandler, directory=str(DOCS))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def launch_browser(playwright):
    """Bundled Chromium if installed, else the system's Edge or Chrome."""
    for options in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return playwright.chromium.launch(**options)
        except Exception:  # noqa: BLE001 - try the next browser
            continue
    raise RuntimeError("no Chromium-based browser available for Playwright")


BRAIN = Path(__file__).resolve().parents[1] / "brain"
MOCK_USER = {
    "login": "juw12",
    "name": "Juwen Lie",
    "avatar_url": "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
    "bio": "R&D mechanical engineer who ships software.",
    "location": "Batam, Indonesia",
    "blog": "https://juw12.github.io/",
    "html_url": "https://github.com/juw12",
    "created_at": "2019-03-01T00:00:00Z",
    "type": "User",
}
MOCK_REPO = {
    "name": "cairn",
    "full_name": "juw12/cairn",
    "owner": {"login": "juw12", "avatar_url": MOCK_USER["avatar_url"]},
    "description": "A second brain that lives inside your repo.",
    "html_url": "https://github.com/juw12/cairn",
    "default_branch": "main",
    "stargazers_count": 3,
    "pushed_at": "2026-11-05T10:00:00Z",
}


VALID_TOKEN = "ghp_" + "a" * 36
OTHER_TOKEN = "ghp_" + "b" * 36  # a real user without push access to juw12/cairn
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept",
    "Access-Control-Allow-Methods": "GET, PUT, POST, DELETE, OPTIONS",
    "Access-Control-Expose-Headers": "x-oauth-scopes, x-ratelimit-remaining",
}


def git_sha(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class GitHubMock:
    """A small, stateful stand-in for api.github.com and raw.githubusercontent.com.

    Repos hold real file contents with git blob shas, so the app's sha-checked writes,
    conflicts and deletes behave like the real API. Every request is logged with its
    method, URL and whether it carried an Authorization header.
    """

    def __init__(self, push: bool = True):
        files = {p.relative_to(BRAIN.parent).as_posix(): p.read_text(encoding="utf-8") for p in BRAIN.rglob("*.md")}
        files["README.md"] = "# Cairn\n"
        self.repos = {"juw12/cairn": {"meta": dict(MOCK_REPO, topics=["cairn-brain"]), "files": files}}
        self.push = push
        self.revoked = False
        self.requests: list[dict] = []

    # ---- helpers for tests
    def text(self, repo: str, path: str):
        return self.repos[repo]["files"].get(path)

    def set_text(self, repo: str, path: str, text: str) -> None:
        self.repos[repo]["files"][path] = text

    def writes(self):
        return [r for r in self.requests if r["method"] in ("PUT", "POST", "DELETE")]

    # ---- routing
    def install(self, context) -> "GitHubMock":
        context.route("https://api.github.com/**", self.api)
        context.route("https://raw.githubusercontent.com/**", self.raw)
        return self

    def reply(self, route, payload, status=200, headers=None):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload), headers={**CORS, **(headers or {})})

    def who(self, request):
        header = request.headers.get("authorization", "")
        token = header[len("Bearer "):] if header.startswith("Bearer ") else None
        if token is None:
            return None, False
        if self.revoked or token not in (VALID_TOKEN, OTHER_TOKEN):
            return None, True
        return ("juw12" if token == VALID_TOKEN else "someone-else"), False

    def repo_payload(self, name, login):
        meta = dict(self.repos[name]["meta"])
        if login:
            meta["permissions"] = {"admin": False, "push": login == "juw12" and self.push, "pull": True}
        return meta

    def api(self, route):
        request = route.request
        url = urlparse(request.url)
        path = unquote(url.path)
        self.requests.append({"method": request.method, "url": request.url, "auth": "authorization" in request.headers})
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=CORS)
        login, bad_token = self.who(request)
        if bad_token:
            return self.reply(route, {"message": "Bad credentials"}, 401)

        if path == "/user":
            if not login:
                return self.reply(route, {"message": "Requires authentication"}, 401)
            user = dict(MOCK_USER) if login == "juw12" else dict(MOCK_USER, login=login, name="Someone Else")
            return self.reply(route, user, headers={"x-oauth-scopes": "public_repo"})
        if path == "/users/juw12":
            return self.reply(route, MOCK_USER)
        if path.startswith("/users/"):
            return self.reply(route, {"message": "Not Found"}, 404)
        if path == "/search/repositories":
            query = unquote(url.query)
            items = [self.repos["juw12/cairn"]["meta"]] if ("topic:cairn-brain" in query and ("user:juw12" in query or "user:" not in query)) else []
            return self.reply(route, {"total_count": len(items), "items": items})
        if path == "/user/repos":
            if not login:
                return self.reply(route, {"message": "Requires authentication"}, 401)
            if request.method == "POST":
                body = json.loads(request.post_data or "{}")
                name = f"{login}/{body['name']}"
                if name in self.repos:
                    return self.reply(route, {"message": "name already exists on this account"}, 422)
                meta = dict(MOCK_REPO, name=body["name"], full_name=name, html_url=f"https://github.com/{name}", topics=[], description=body.get("description", ""))
                self.repos[name] = {"meta": meta, "files": {"README.md": f"# {body['name']}\n"}}
                return self.reply(route, self.repo_payload(name, login), 201)
            mine = [self.repo_payload(n, login) for n in self.repos if n.startswith(f"{login}/")]
            return self.reply(route, mine)

        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "repos":
            name = f"{parts[1]}/{parts[2]}"
            if name not in self.repos:
                return self.reply(route, {"message": "Not Found"}, 404)
            repo = self.repos[name]
            rest = parts[3:]
            if not rest:
                return self.reply(route, self.repo_payload(name, login))
            if rest[0] == "topics" and request.method == "PUT":
                repo["meta"]["topics"] = json.loads(request.post_data or "{}").get("names", [])
                return self.reply(route, {"names": repo["meta"]["topics"]})
            if rest[:2] == ["git", "trees"]:
                tree = [{"path": p, "type": "blob", "sha": git_sha(t)} for p, t in sorted(repo["files"].items())]
                return self.reply(route, {"tree": tree})
            if rest[:2] == ["git", "blobs"]:
                for text in repo["files"].values():
                    if git_sha(text) == rest[2]:
                        return self.reply(route, {"content": base64.b64encode(text.encode("utf-8")).decode("ascii"), "encoding": "base64"})
                return self.reply(route, {"message": "Not Found"}, 404)
            if rest[0] == "contents":
                return self.contents(route, request, repo, "/".join(rest[1:]), login)
        return self.reply(route, {"message": "Not Found"}, 404)

    def contents(self, route, request, repo, file_path, login):
        files = repo["files"]
        current = files.get(file_path)
        if request.method == "GET":
            if current is None:
                return self.reply(route, {"message": "Not Found"}, 404)
            encoded = base64.b64encode(current.encode("utf-8")).decode("ascii")
            return self.reply(route, {"content": encoded, "sha": git_sha(current), "path": file_path})
        if not login or not (login == "juw12" and self.push):
            return self.reply(route, {"message": "Resource not accessible"}, 403)
        body = json.loads(request.post_data or "{}")
        if request.method == "PUT":
            if current is not None and body.get("sha") != git_sha(current):
                status = 422 if not body.get("sha") else 409
                return self.reply(route, {"message": "sha does not match"}, status)
            text = base64.b64decode(body["content"]).decode("utf-8")
            files[file_path] = text
            return self.reply(route, {"content": {"path": file_path, "sha": git_sha(text)}}, 200 if current is not None else 201)
        if request.method == "DELETE":
            if current is None:
                return self.reply(route, {"message": "Not Found"}, 404)
            if body.get("sha") != git_sha(current):
                return self.reply(route, {"message": "sha does not match"}, 409)
            del files[file_path]
            return self.reply(route, {"commit": {}})
        return self.reply(route, {"message": "Not Found"}, 404)

    def raw(self, route):
        path = unquote(urlparse(route.request.url).path).strip("/")
        owner, name, _branch, *rest = path.split("/")
        repo = self.repos.get(f"{owner}/{name}")
        text = repo["files"].get("/".join(rest)) if repo else None
        if text is None:
            return route.fulfill(status=404, body="", headers=CORS)
        route.fulfill(status=200, content_type="text/plain; charset=utf-8", body=text, headers=CORS)


def install_github_mock(context, push: bool = True) -> GitHubMock:
    """Serve GitHub API + raw file requests from an in-memory copy of brain/."""
    return GitHubMock(push=push).install(context)

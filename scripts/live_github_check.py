"""End-to-end check of signed-in editing against the REAL GitHub API (no mocks).

    python scripts/live_github_check.py            # uses CAIRN_LIVE_TOKEN, else `gh auth token`

It signs in to the web app (served locally) with your token, creates a throwaway public brain repo
called cairn-live-check-<time>, then creates, edits, moves, conflicts and deletes a note through the UI,
checking every result with the GitHub API. The throwaway repo is deleted at the end (needs the
delete_repo scope; otherwise it is left for you to delete). Nothing else in your account is written to.
The token is never printed and is only sent to api.github.com (checked).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from webserver import launch_browser, start_static_server  # noqa: E402

API = "https://api.github.com"
TIMEOUT_S = 30


def get_token() -> str:
    token = os.environ.get("CAIRN_LIVE_TOKEN", "").strip()
    if token:
        return token
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None):
        request = urllib.request.Request(
            f"{API}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            return error.code, None

    def file(self, repo: str, path: str):
        status, data = self.call("GET", f"/repos/{repo}/contents/{path}?nocache={time.time()}")
        if status != 200:
            return None, None
        return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]

    def commits_touching(self, repo: str, path: str) -> int:
        status, data = self.call("GET", f"/repos/{repo}/commits?path={path}&per_page=100&nocache={time.time()}")
        return len(data) if status == 200 else 0


def wait_for(description: str, check, timeout: float = TIMEOUT_S):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = check()
        if last:
            return last
        time.sleep(1)
    raise AssertionError(f"timed out waiting for: {description} (last: {last!r})")


def step(message: str):
    print(f"  ok  {message}", flush=True)


def main() -> int:
    token = get_token()
    github = GitHub(token)
    status, me = github.call("GET", "/user")
    if status != 200:
        print("The token was refused by GitHub.")
        return 2
    login = me["login"]
    repo_name = f"cairn-live-check-{int(time.time())}"
    repo = f"{login}/{repo_name}"
    note = "brain/tasks/live-check-from-cairn.md"
    server, url = start_static_server()
    errors: list[str] = []
    authorized_hosts: set[str] = set()
    print(f"Live check as @{login} on throwaway repo {repo}")
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            context = browser.new_context(viewport={"width": 1280, "height": 860})
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_S * 1000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: errors.append(message.text)
                if message.type == "error" and not (message.text.startswith("Failed to load resource") and "409" in message.text)
                else None,
            )
            page.on(
                "request",
                lambda request: authorized_hosts.add(request.url.split("/")[2]) if "authorization" in request.headers else None,
            )

            # 1. Sign in from the welcome page with the token.
            page.goto(url)
            page.click('.welcome-account [data-action="sign-in"]')
            page.fill("#token-input", token)
            page.click("#token-submit")
            page.wait_for_function("() => window.cairn?.state?.info?.mode === 'github' || location.search.includes('u=')")
            step(f"signed in as @{login}; landed on {page.url.split('?')[-1]}")

            # 2. Create a brain from People (POST /user/repos, topics, starter files).
            page.goto(f"{url}?u={login}")
            page.wait_for_selector("#new-brain-name")
            page.fill("#new-brain-name", repo_name)
            page.press("#new-brain-name", "Enter")
            page.wait_for_function(f"() => window.cairn?.state?.info?.repoName === '{repo_name}' && !window.cairn.state.info.readOnly")
            status, meta = github.call("GET", f"/repos/{repo}/topics")
            assert status == 200 and "cairn-brain" in meta["names"], meta
            step("created the brain repo with the cairn-brain topic and opened it editable")

            # 3. Create a note with quick capture.
            page.fill("#capture-input", "todo: live check from Cairn #live")
            page.press("#capture-input", "Enter")
            text = wait_for("captured task on GitHub", lambda: github.file(repo, note)[0])
            assert "tags: [live]" in text, text
            step("quick capture created a commit with the new task")

            # 4. Edit: a burst of typing becomes one commit.
            page.click('#nav [data-go="board"]')
            page.click('.item-card[data-open="tasks/live-check-from-cairn.md"]')
            page.wait_for_selector("#ed-body")
            commits_before = github.commits_touching(repo, note)
            page.click("#ed-body")
            page.keyboard.type(" Typed in a real browser.", delay=20)
            wait_for("edited text on GitHub", lambda: "Typed in a real browser." in (github.file(repo, note)[0] or ""))
            page.wait_for_function("() => document.querySelector('#save-state')?.textContent === 'Saved to GitHub'")
            time.sleep(3)
            commits = github.commits_touching(repo, note) - commits_before
            assert commits == 1, f"expected 1 commit for the burst of typing, got {commits}"
            step("typing a sentence made exactly one commit")

            # 5. Move: change the status.
            page.select_option("#ed-status", "doing")
            wait_for("status on GitHub", lambda: "status: doing" in (github.file(repo, note)[0] or ""))
            step("status change saved")

            # 6. Conflict: another device saves first; my text must survive.
            remote_text, sha = github.file(repo, note)
            other = remote_text.replace("Typed in a real browser.", "Edited on my laptop.")
            status, _ = github.call(
                "PUT",
                f"/repos/{repo}/contents/{note}",
                {"message": "Edit from another device", "content": base64.b64encode(other.encode()).decode(), "sha": sha},
            )
            assert status == 200, status
            page.click("#ed-body")
            page.keyboard.press("End")
            page.keyboard.type(" And from my phone.", delay=20)
            page.wait_for_selector(".held-card")
            held = page.evaluate("() => window.cairn.state.held.text")
            assert "And from my phone." in held, held
            page.click('[data-action="held-use"]')
            wait_for("my version on GitHub", lambda: "And from my phone." in (github.file(repo, note)[0] or ""))
            step("conflict detected; my text kept and saved on request")

            # 7. Delete.
            page.wait_for_selector(".held-card", state="detached")
            page.click('[data-action="delete"]')
            page.click(".modal .btn.danger")
            wait_for("note deleted on GitHub", lambda: github.file(repo, note)[0] is None)
            step("delete removed the file")

            # 8. Sign out clears the token.
            page.keyboard.press("Escape")
            page.click('[data-action="sign-out"]')
            stored = page.evaluate("() => [sessionStorage.getItem('cairn.session'), localStorage.getItem('cairn.session')]")
            assert stored == [None, None], "token left in storage after sign-out"
            step("sign-out removed the token from the browser")

            assert authorized_hosts == {"api.github.com"}, authorized_hosts
            step("the token was only sent to api.github.com")
            assert not errors, errors
            step("no page errors")
            browser.close()
        print("PASS")
        return 0
    finally:
        server.shutdown()
        status, _ = github.call("DELETE", f"/repos/{repo}")
        if status == 204:
            print(f"Deleted throwaway repo {repo}")
        elif status == 404:
            print(f"Throwaway repo {repo} was never created")
        else:
            print(f"Could not delete {repo} (HTTP {status}); delete it on GitHub.")


if __name__ == "__main__":
    sys.exit(main())

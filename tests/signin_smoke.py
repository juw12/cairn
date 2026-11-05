"""Screenshots of the sign-in flow for visual review: python tests/signin_smoke.py -> .shots/signin-*.png"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import VALID_TOKEN, install_github_mock, launch_browser, start_static_server  # noqa: E402

SHOTS = Path(__file__).resolve().parents[1] / ".shots"
SESSION = json.dumps({"token": VALID_TOKEN, "method": "token", "login": "juw12", "name": "Juwen Lie", "avatar": "", "remember": False})


def new_page(browser, signed_in=False, viewport=(1280, 860)):
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
    mock = install_github_mock(context)
    page = context.new_page()
    page.add_init_script("localStorage.setItem('cairn.theme', 'light')")
    if signed_in:
        page.add_init_script(f"sessionStorage.setItem('cairn.session', {json.dumps(SESSION)})")
    return context, page, mock


def main() -> None:
    SHOTS.mkdir(exist_ok=True)
    server, url = start_static_server()
    with sync_playwright() as p:
        browser = launch_browser(p)

        context, page, _ = new_page(browser)
        page.goto(url + "?brain=juw12/cairn")
        page.wait_for_selector(".ro-banner")
        page.click('#sidebar-foot [data-action="sign-in"]')
        page.wait_for_selector("#signin-dialog")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "signin-dialog.png"))
        context.close()

        context, page, _ = new_page(browser, signed_in=True)
        page.goto(url + "?brain=juw12/cairn#/board")
        page.wait_for_function("() => window.cairn?.state?.info && !window.cairn.state.info.readOnly")
        page.screenshot(path=str(SHOTS / "signin-board.png"))
        context.close()

        context, page, mock = new_page(browser, signed_in=True)
        page.goto(url + "?brain=juw12/cairn#/board")
        page.wait_for_function("() => window.cairn?.state?.info && !window.cairn.state.info.readOnly")
        page.click('.item-card[data-open="tasks/mcp-server.md"]')
        page.wait_for_selector("#ed-body")
        mock.set_text("juw12/cairn", "brain/tasks/mcp-server.md", "---\ntitle: MCP server for AI assistants\ntype: task\nstatus: doing\n---\nEdited on my laptop.\n")
        page.click("#ed-body")
        page.keyboard.type(" My phone edit.")
        page.wait_for_selector(".held-card")
        page.screenshot(path=str(SHOTS / "signin-conflict.png"))
        context.close()

        context, page, _ = new_page(browser, signed_in=True)
        page.goto(url + "?u=juw12")
        page.wait_for_selector(".my-brains .repo-row")
        page.screenshot(path=str(SHOTS / "signin-people.png"))
        context.close()

        context, page, _ = new_page(browser)
        page.goto(url)
        page.wait_for_selector(".welcome-account")
        page.screenshot(path=str(SHOTS / "signin-welcome.png"))
        context.close()

        context, page, _ = new_page(browser, viewport=(390, 844))
        page.goto(url + "?brain=juw12/cairn")
        page.wait_for_selector(".ro-banner")
        page.click("#menu-btn")
        page.click('#sidebar-foot [data-action="sign-in"]')
        page.wait_for_selector("#signin-dialog")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "signin-mobile.png"))
        context.close()

        browser.close()
    server.shutdown()
    print("ok")


if __name__ == "__main__":
    main()

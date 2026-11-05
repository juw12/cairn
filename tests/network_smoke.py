"""Screenshots of the network screens with a mocked GitHub (writes .shots/)."""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import install_github_mock, launch_browser, start_static_server  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / ".shots"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    server, url = start_static_server()
    with sync_playwright() as p:
        browser = launch_browser(p)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        install_github_mock(context)
        context.add_init_script("localStorage.setItem('cairn.theme', 'light')")
        page = context.new_page()
        page.goto(url + "?u=juw12")
        page.wait_for_selector(".proof-grid")
        page.screenshot(path=str(OUT / "people-profile.png"), full_page=True)
        page.goto(url + "?brain=juw12/cairn")
        page.wait_for_selector(".ro-banner")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "brain-home.png"))
        page.goto(url + "?brain=juw12/cairn#/board")
        page.wait_for_selector(".item-card")
        page.screenshot(path=str(OUT / "brain-board.png"))
        page.evaluate("localStorage.clear()")
        page.goto(url)
        page.wait_for_selector(".choices")
        page.screenshot(path=str(OUT / "welcome.png"), full_page=True)
        dark = browser.new_context(viewport={"width": 1440, "height": 900})
        install_github_mock(dark)
        dark.add_init_script("localStorage.setItem('cairn.theme', 'dark')")
        dark_page = dark.new_page()
        dark_page.goto(url + "?demo=1#/home")
        dark_page.wait_for_selector(".today")
        dark_page.screenshot(path=str(OUT / "dark-home.png"))
        browser.close()
    server.shutdown()
    print("ok")


if __name__ == "__main__":
    main()

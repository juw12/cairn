"""Quick visual smoke run: every view in demo mode, console errors, screenshots to .shots/."""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import launch_browser, start_static_server  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / ".shots"
VIEWS = ["home", "ideas", "board", "decisions", "notes", "journal", "map", "code", "context"]


def main() -> int:
    OUT.mkdir(exist_ok=True)
    server, url = start_static_server()
    errors: list[str] = []
    with sync_playwright() as p:
        browser = launch_browser(p)
        for theme in ("light", "dark"):
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.add_init_script(f"localStorage.setItem('cairn.theme', '{theme}')")
            page.goto(url + "?demo=1#/home")
            page.wait_for_selector("#nav button")
            for view in VIEWS if theme == "light" else ["home", "ideas"]:
                page.evaluate(f"location.hash = '#/{view}'; dispatchEvent(new PopStateEvent('popstate'))")
                page.wait_for_timeout(250)
                page.screenshot(path=str(OUT / f"{theme}-{view}.png"))
            page.close()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url)
        page.evaluate("localStorage.clear()")
        page.goto(url)
        page.wait_for_selector(".choices")
        page.screenshot(path=str(OUT / "welcome.png"))
        browser.close()
    server.shutdown()
    print("errors:", errors or "none")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

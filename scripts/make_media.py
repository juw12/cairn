"""Regenerate docs/media/banner.jpg and docs/media/demo.png from the live demo.

    python scripts/make_media.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from webserver import launch_browser, start_static_server  # noqa: E402

MEDIA = ROOT / "docs" / "media"
LOGO = (ROOT / "docs" / "icons" / "icon.svg").read_text(encoding="utf-8")

FONTS = ROOT / "docs" / "fonts"


def font_face(family: str, file_name: str, style: str = "normal") -> str:
    data = base64.b64encode((FONTS / file_name).read_bytes()).decode("ascii")
    return (
        f"@font-face {{ font-family: '{family}'; font-style: {style}; "
        f"src: url(data:font/woff2;base64,{data}) format('woff2'); }}"
    )


BANNER = """<!doctype html><html><head><meta charset="utf-8"><style>
  __FONTS__
  * { box-sizing: border-box; margin: 0; }
  body { width: 1600px; height: 800px; overflow: hidden; font-family: Geist, "Segoe UI", system-ui, sans-serif;
         background: #f4f1ea; color: #1b1a17; }
  .left { position: absolute; left: 90px; top: 0; bottom: 0; width: 660px; display: flex; flex-direction: column; justify-content: center; }
  .brand { display: flex; align-items: center; gap: 14px; font-family: "Instrument Serif", Georgia, serif; font-size: 40px; margin-bottom: 36px; }
  .brand svg { width: 52px; height: 52px; }
  h1 { font-family: "Instrument Serif", Georgia, serif; font-weight: 400; font-size: 92px; line-height: 0.98; letter-spacing: -0.02em; }
  h1 em { color: #d9481f; font-style: italic; }
  p { margin-top: 28px; font-size: 24px; line-height: 1.45; color: #46443e; max-width: 580px; }
  .rule { margin-top: 34px; border-top: 1px solid #1b1a17; padding-top: 16px; font-family: "Geist Mono", ui-monospace, monospace;
          font-size: 16px; letter-spacing: 0.06em; text-transform: uppercase; color: #6b6860; }
  .shot { position: absolute; left: 800px; top: 90px; width: 1100px; border-radius: 10px; overflow: hidden;
          box-shadow: 0 30px 80px -24px rgba(27,26,23,.35), 0 0 0 1px rgba(27,26,23,.12); }
  .shot img { display: block; width: 100%; }
</style></head><body>
  <div class="left">
    <div class="brand">__LOGO__<span>Cairn</span></div>
    <h1>A second brain that lives <em>inside your repo</em></h1>
    <p>Capture, brainstorm, decide and ship, as plain Markdown next to your code. Explore what others are building, straight from their repos.</p>
    <div class="rule">Ideas · Board · Decisions · Code TODOs · People</div>
  </div>
  <div class="shot"><img src="data:image/png;base64,__SHOT__"></div>
</body></html>"""


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    server, url = start_static_server()
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page.add_init_script("localStorage.setItem('cairn.theme', 'light'); localStorage.setItem('cairn.guide', JSON.stringify({dismissed: true}))")
        page.goto(url + "?demo=1#/ideas")
        page.wait_for_selector(".item-card")
        page.screenshot(path=str(MEDIA / "demo.png"))
        page.goto(url + "?demo=1#/home")
        page.wait_for_selector(".figures")
        page.wait_for_timeout(400)
        home_png = page.screenshot()

        banner = browser.new_page(viewport={"width": 1600, "height": 800}, device_scale_factor=1)
        shot = base64.b64encode(home_png).decode("ascii")
        fonts = "\n".join(
            [
                font_face("Instrument Serif", "instrument-serif-normal.woff2"),
                font_face("Instrument Serif", "instrument-serif-italic.woff2", "italic"),
                font_face("Geist", "geist-normal.woff2"),
                font_face("Geist Mono", "geist-mono-normal.woff2"),
            ]
        )
        html = BANNER.replace("__FONTS__", fonts).replace("__LOGO__", LOGO).replace("__SHOT__", shot)
        banner.set_content(html)
        banner.wait_for_timeout(800)
        banner.screenshot(path=str(MEDIA / "banner.jpg"), type="jpeg", quality=88)
        browser.close()
    server.shutdown()
    print("wrote", MEDIA / "banner.jpg", "and", MEDIA / "demo.png")


if __name__ == "__main__":
    main()

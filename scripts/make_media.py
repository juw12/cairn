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

BANNER = """<!doctype html><html><head><meta charset="utf-8"><style>
  * { box-sizing: border-box; margin: 0; }
  body { width: 1600px; height: 800px; overflow: hidden; font-family: Inter, "Segoe UI", system-ui, sans-serif;
         background: radial-gradient(900px 500px at 15% 10%, #dff0ec, transparent 70%), #f6f6f3; color: #1d1d1b; }
  .left { position: absolute; left: 90px; top: 0; bottom: 0; width: 640px; display: flex; flex-direction: column; justify-content: center; }
  .brand { display: flex; align-items: center; gap: 16px; font-size: 34px; font-weight: 700; margin-bottom: 34px; }
  .brand svg { width: 56px; height: 56px; }
  h1 { font-size: 66px; line-height: 1.04; letter-spacing: -0.035em; font-weight: 750; }
  h1 span { color: #0f766e; }
  p { margin-top: 24px; font-size: 25px; line-height: 1.45; color: #44433f; max-width: 590px; }
  .chips { display: flex; gap: 10px; margin-top: 34px; flex-wrap: wrap; }
  .chip { padding: 9px 16px; border-radius: 999px; background: #fff; border: 1px solid #e0ddd5; font-size: 18px; font-weight: 600; color: #44433f; }
  .shot { position: absolute; left: 790px; top: 90px; width: 1100px; border-radius: 18px; overflow: hidden;
          box-shadow: 0 40px 90px -20px rgba(20,20,15,.35), 0 0 0 1px rgba(20,20,15,.08); }
  .shot img { display: block; width: 100%; }
</style></head><body>
  <div class="left">
    <div class="brand">__LOGO__<span>Cairn</span></div>
    <h1>A second brain that lives <span>inside your repo</span></h1>
    <p>Capture, brainstorm, decide and ship, as plain Markdown next to your code.</p>
    <div class="chips"><span class="chip">Ideas wall</span><span class="chip">Task board</span><span class="chip">Decision log</span><span class="chip">Code TODO inbox</span><span class="chip">AI context pack</span></div>
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
        page.wait_for_selector(".stats")
        home_png = page.screenshot()

        banner = browser.new_page(viewport={"width": 1600, "height": 800}, device_scale_factor=1)
        shot = base64.b64encode(home_png).decode("ascii")
        banner.set_content(BANNER.replace("__LOGO__", LOGO).replace("__SHOT__", shot))
        banner.wait_for_timeout(300)
        banner.screenshot(path=str(MEDIA / "banner.jpg"), type="jpeg", quality=88)
        browser.close()
    server.shutdown()
    print("wrote", MEDIA / "banner.jpg", "and", MEDIA / "demo.png")


if __name__ == "__main__":
    main()

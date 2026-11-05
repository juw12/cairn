"""Manual check: run the real local server on a temp git repo and screenshot it (writes .shots/)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from cairn.brain import Brain  # noqa: E402
from cairn.server import make_server  # noqa: E402
from webserver import launch_browser, start_static_server  # noqa: E402

OUT = ROOT / ".shots"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    repo = Path(tempfile.mkdtemp()) / "trailmix"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "gpx.py").write_text("def parse():\n    # TODO: read every segment\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    brain = Brain(repo)
    brain.init()
    brain.create("idea", "Offline maps", ["maps"], "Link to [[ship-v1]].\n", "2026-11-05")
    brain.create("task", "Ship v1", ["release"], "- [x] build\n- [ ] invite\n", "2026-11-05")
    server = make_server(brain, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    errors: list[str] = []
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.wait_for_selector(".git-card")
        mode = page.inner_text("#project-mode")
        page.fill("#capture-input", "todo: from the server test #e2e")
        page.press("#capture-input", "Enter")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "server-home.png"))
        page.click('#nav [data-go="ideas"]')
        page.click(".item-card")
        page.wait_for_selector("#ed-body")
        page.click('[data-editor-mode="preview"]')
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "server-drawer.png"))
        on_disk = (repo / "brain" / "tasks" / "from-the-server-test.md").exists()
        browser.close()

        static, static_url = start_static_server()
        browser = launch_browser(p)
        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        mobile.goto(static_url + "?demo=1#/board")
        mobile.wait_for_selector(".item-card")
        mobile.screenshot(path=str(OUT / "mobile-board.png"))
        browser.close()
        static.shutdown()
    server.shutdown()
    shutil.rmtree(repo.parent, ignore_errors=True)
    print("mode:", mode, "| file on disk:", on_disk, "| errors:", errors or "none")
    return 0 if on_disk and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

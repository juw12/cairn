"""A frozen WebKit page cannot answer a Playwright timeout; bound the whole probe."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

from webserver import start_static_server


def probe():
    from playwright.sync_api import sync_playwright

    server, url = start_static_server()
    try:
        with sync_playwright() as playwright:
            browser = playwright.webkit.launch()
            page = browser.new_page(**playwright.devices["iPhone 13"])
            page.add_init_script("localStorage.setItem('cairn.tour', 'done')")
            page.goto(url + "?demo=1#/home")
            page.wait_for_selector('.vibe-board[data-page="home"]')
            page.evaluate(r"""() => {
                const {state, core} = window.cairn;
                state.files = Array.from({length: 4000}, (_, i) => {
                    const link = i < 600 ? ` [[n${(i + 1) % 600}]]` : '';
                    return {path: `notes/n${i}.md`, mtime: 1, text: `# Topic number ${i} alpha\n`
                        + 'plain words of a note go on and on here. '.repeat(40)
                        + `It mentions Topic number ${(i * 7 + 13) % 4000} alpha once.${link}`};
                });
                state.notes = state.files.map((f) => core.noteFromFile(f.path, f.text, f.mtime));
                location.hash = '#/map';
            }""")
            graph = page.locator('.vibe-panel[data-panel="graph"]')
            toggle = graph.locator('.vp-toggle')
            if toggle.get_attribute('aria-expanded') == 'false':
                toggle.tap()
            nodes = graph.locator('#map-svg .map-node circle')
            nodes.first.wait_for(state='attached')
            assert nodes.count() == 600, f'expected 600 linked nodes, got {nodes.count()}'
            toggle.tap()
            page.wait_for_selector('.vibe-panel[data-panel="graph"].folded')
            assert nodes.count() == 0, 'folding the Map must remove its drawing'
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


class WebKitMapTests(unittest.TestCase):
    def test_expanded_map_remains_responsive(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            if os.environ.get('CAIRN_REQUIRE_BROWSER'):
                raise
            self.skipTest('playwright not installed')
        with sync_playwright() as playwright:
            if not Path(playwright.webkit.executable_path).is_file() and not os.environ.get('CAIRN_REQUIRE_BROWSER'):
                self.skipTest('WebKit not installed')
        process = subprocess.Popen([sys.executable, __file__], start_new_session=os.name != 'nt')
        try:
            self.assertEqual(process.wait(timeout=75), 0, 'WebKit Map probe failed')
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            self.fail('WebKit did not finish the Map probe within 75 seconds')


if __name__ == '__main__':
    probe()

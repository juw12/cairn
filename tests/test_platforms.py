"""Versions (docs/platforms.js): a repo's Windows, Mac, iPhone and Android versions on its Look tab."""

import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import install_github_mock, launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, If-None-Match"}
MANIFEST = json.dumps({"name": "Ruang", "display": "standalone", "start_url": "./"})
PAGE = '<!doctype html><link rel="manifest" href="manifest.webmanifest"><link rel="apple-touch-icon" href="icon.png"><h1>Ruang</h1>'


def asset(name, size=4_200_000):
    return {"name": name, "size": size, "browser_download_url": f"https://github.com/juw12/x/releases/download/t/{name}"}


def release(tag, names, day="2026-11-10"):
    return {"tag_name": tag, "draft": False, "published_at": f"{day}T08:00:00Z", "assets": [asset(name) for name in names]}


# One repo per case. files: what the repo holds; releases: GitHub's list, newest first; readme: its rendered HTML.
REPOS = {
    # An app with three of four: no Mac build. Its web app is ready for an iPhone.
    "ruang": {
        "homepage": "https://juw12.github.io/ruang/",
        "files": {"README.md": "# Ruang\n", "web/index.html": PAGE, "web/manifest.webmanifest": MANIFEST, "src/main.rs": "fn main() {}\n"},
        "releases": [
            release("v1.3.0", ["Ruang-1.3.0-windows-x64-setup.exe", "Ruang-1.3.0-windows-x64.exe", "Ruang-1.3.0-android.apk", "SHA256SUMS.txt"]),
            release("v1.2.0", ["Ruang-1.2.0-windows-x64.exe", "Ruang-1.2.0-android.apk"], "2026-11-01"),
        ],
        "readme": "<article><h1>Ruang</h1></article>",
    },
    # The Mac build was left behind by a newer release; the Android app is only linked from the README.
    "buddy": {
        "homepage": "",
        "files": {"README.md": "# Buddy\n", "docs/index.html": "<h1>landing page</h1>", "docs/manifest.json": json.dumps({"display": "browser"})},
        "releases": [release("v1.10.0", ["Buddy-win64.zip", "Buddy-linux.tar.gz"]), release("v1.6.0", ["Buddy-win64.zip", "Buddy-mac.zip"], "2026-10-01")],
        "readme": '<article><p><a href="https://juw12.github.io/buddy/Buddy-phone.apk">Download for Android</a> · <a href="https://img.shields.io/badge/win-ok.svg">badge</a></p></article>',
    },
    # A repo of many apps: the releases belong to the apps, each with its own tags.
    "projects": {
        "homepage": "",
        "files": {
            "README.md": "# Projects\n",
            "apps/glow/README.md": "# Glow\n",
            "apps/glow/web/index.html": PAGE,
            "apps/glow/web/manifest.webmanifest": MANIFEST,
            "apps/twin/README.md": "# Twin\n",
            "apps/twin/src/app.py": "print('twin')\n",
            "apps/notes/README.md": "# Notes\n",
        },
        "releases": [
            release("glow-mac", ["Glow-mac.dmg"]),
            release("twin", ["Twin-Windows-Setup.exe", "Twin-macOS-Intel.dmg", "Twin-macOS-AppleSilicon.dmg"]),
            release("glow-windows", ["Glow-Setup.exe"]),
            release("glow-android", ["Glow-android.apk"]),
            release("mint", ["Mint-Android.apk"]),
        ],
        "readme": "<article><h1>Projects</h1></article>",
        "folder_readme": '<article><p><a href="https://juw12.github.io/projects/other/">Other</a> <a href="https://juw12.github.io/projects/glow/">Live demo</a></p></article>',
    },
    # A study: nothing to install.
    "study": {"homepage": "", "files": {"README.md": "# Study\n", "results/table.csv": "a,b\n"}, "releases": [], "readme": "<article><h1>Study</h1></article>"},
}


def repo_meta(name):
    return {
        "name": name, "full_name": f"juw12/{name}", "owner": {"login": "juw12", "avatar_url": ""}, "description": "", "html_url": f"https://github.com/juw12/{name}",
        "default_branch": "main", "private": False, "visibility": "public", "stargazers_count": 0, "language": "Python", "size": 10,
        "homepage": REPOS[name]["homepage"], "created_at": "2026-06-01T00:00:00Z", "pushed_at": "2026-11-20T08:00:00Z",
    }


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class VersionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = start_static_server()
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()

    def open(self, address="#/projects", viewport=(1440, 900), releases_fail=False):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        install_github_mock(context)
        self.api = []

        def json_reply(route, payload, status=200):
            route.fulfill(status=status, headers={**CORS, "Content-Type": "application/json"}, body=json.dumps(payload))

        def github(route):
            request = route.request
            path = unquote(urlparse(request.url).path)
            if request.method == "OPTIONS":
                return route.fulfill(status=204, headers=CORS)
            parts = path.strip("/").split("/")
            if parts[:2] != ["repos", "juw12"] or len(parts) < 3 or parts[2] not in REPOS:
                return route.fallback()
            name, rest = parts[2], parts[3:]
            self.api.append("/".join(rest))
            repo = REPOS[name]
            if not rest:
                return json_reply(route, repo_meta(name))
            if rest[:2] == ["git", "trees"]:
                tree = [{"path": p, "type": "blob", "size": len(data), "sha": f"{i:040x}"} for i, (p, data) in enumerate(sorted(repo["files"].items()))]
                return json_reply(route, {"tree": tree, "truncated": False})
            if rest == ["releases"]:
                if releases_fail:
                    return json_reply(route, {"message": "Server Error"}, 500)
                return json_reply(route, repo["releases"] if "page=1" in request.url else [])
            if rest[0] == "contents" and "html" in request.headers.get("accept", ""):
                html = repo["readme"] if rest[1:] == ["README.md"] else repo.get("folder_readme", "<article></article>")
                return route.fulfill(status=200, headers={**CORS, "Content-Type": "text/html"}, body=html)
            return json_reply(route, {"message": "Not Found"}, 404)

        def raw(route):
            parts = unquote(urlparse(route.request.url).path).strip("/").split("/")
            data = REPOS.get(parts[1], {}).get("files", {}).get("/".join(parts[3:]))
            if parts[0] == "juw12" and data is not None:
                return route.fulfill(status=200, headers={**CORS, "Content-Type": "text/plain"}, body=data)
            return route.fulfill(status=404, headers=CORS, body="")

        context.route("https://api.github.com/**", github)
        context.route("https://raw.githubusercontent.com/**", raw)
        context.set_default_timeout(60_000)
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script("localStorage.setItem('cairn.theme', 'light'); localStorage.setItem('cairn.projects.owner', '\"juw12\"');")
        page.goto(self.url + "?demo=1" + address, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector("#nav button")
        self.addCleanup(context.close)
        return page

    def lines(self, page):
        """{platform: (state, text)} of the Versions block, once nothing in it is still being read."""
        page.wait_for_function("() => document.querySelectorAll('.repo-version').length === 4 && !document.querySelector('.repo-version.unknown[data-version=\"iPhone\"]')")
        rows = page.eval_on_selector_all(".repo-version", "els => els.map((el) => [el.dataset.version, el.className.replace('repo-version', '').trim(), el.innerText.replace(/\\s+/g, ' ').trim()])")
        return {platform: (state, text) for platform, state, text in rows}

    # ------------------------------------------------------------------ pure logic

    def test_a_file_is_told_apart_by_platform_and_a_release_by_the_app_it_belongs_to(self):
        page = self.open()
        result = page.evaluate(
            """async () => {
                const P = await import('./platforms.js');
                const names = ['Buddy-win64.zip', 'Buddy-mac.zip', 'EarlyStar-Setup-x64.exe', 'EarlyStar-macOS.dmg', 'Ruang-1.3.0-android.apk', 'Twin-macOS-Intel.dmg',
                  'Twin-Windows-Portable.zip', 'spry-macos-universal.tar.gz', 'kilat.exe', 'Cairn-Setup.cmd', 'mac-install.sh', 'Tagalong-unsigned.ipa', 'cairn.mcpb',
                  'Spry-macos.zip.sha256', 'SHA256SUMS.txt', 'latest.yml', 'spry-linux-x64.tar.gz', 'sync-iphone-simulator.png', 'Twin-notes.zip'];
                const releases = P.slimReleases([
                  { tag_name: 'glow-mac', assets: [{ name: 'Glow-mac.dmg' }] }, { tag_name: 'airiz-mobile-v1.0.7', assets: [{ name: 'A.apk' }] },
                  { tag_name: 'twin', assets: [{ name: 'Twin-Setup.exe' }] }, { tag_name: 'glow-windows', assets: [{ name: 'Glow-Setup.exe' }] },
                  { tag_name: 'airiz-desktop-v1.11.29', assets: [{ name: 'A-Setup.exe' }] }, { tag_name: 'draft-1', draft: true, assets: [{ name: 'x.exe' }] },
                ]);
                const buddy = P.slimReleases([{ tag_name: 'v1.10.0', assets: [{ name: 'Buddy-win64.zip' }] }, { tag_name: 'v1.6.0', assets: [{ name: 'Buddy-win64.zip' }, { name: 'Buddy-mac.zip' }] }]);
                return {
                  kinds: names.map((name) => P.platformOf(name)),
                  series: ['v1.3.0', 'v2.0.1-3', 'glow-mac', 'airiz-desktop-v1.11.29', 'haru-latest'].map(P.seriesOf),
                  glow: P.ownReleases(releases, 'apps/glow').map((r) => r.tag),
                  airiz: P.ownReleases(releases, 'apps/airiz-motor').map((r) => r.tag),
                  twin: P.ownReleases(releases, 'apps/twin').map((r) => r.tag),
                  all: P.ownReleases(releases, '').length,
                  macBehind: P.buildsFor(buddy, 'Mac'),
                  winFine: P.buildsFor(buddy, 'Windows').behind,
                  none: P.buildsFor(buddy, 'Android'),
                };
            }"""
        )
        self.assertEqual(
            result["kinds"],
            ["Windows", "Mac", "Windows", "Mac", "Android", "Mac", "Windows", "Mac", "Windows", "Windows", "Mac", "", "", "", "", "", "", "", ""],
            "an .ipa, a checksum, a Linux build, a picture and the letters w-i-n inside 'Twin' are not a version",
        )
        self.assertEqual(result["series"], ["", "", "glow-mac", "airiz-desktop", "haru-latest"])
        self.assertEqual(result["glow"], ["glow-mac", "glow-windows"])
        self.assertEqual(result["airiz"], ["airiz-mobile-v1.0.7", "airiz-desktop-v1.11.29"], "a folder owns the tags that start with its first word")
        self.assertEqual(result["twin"], ["twin"])
        self.assertEqual(result["all"], 5, "a draft is nobody's release")
        self.assertEqual(result["macBehind"]["tag"], "v1.6.0")
        self.assertEqual(result["macBehind"]["behind"], "v1.10.0")
        self.assertEqual(result["winFine"], "")
        self.assertIsNone(result["none"])

    def test_a_readme_gives_downloads_and_a_web_app_needs_its_icon_and_its_own_window(self):
        page = self.open()
        result = page.evaluate(
            """async () => {
                const P = await import('./platforms.js');
                const R = await import('./repo.js');
                const html = '<a href="https://x.dev/Cairn-Setup.cmd">Windows</a> <a href="https://github.com/me/app/releases/latest/download/App-macOS.dmg?a=1&amp;b=2">Mac</a>'
                  + '<a href="https://img.shields.io/badge/win-ok.svg">badge</a> <a href="https://me.github.io/app/">live</a> <a href="#/projects/me/app">inside</a> <a href="https://x.dev/Cairn-Setup.cmd">again</a>';
                const index = R.indexTree(['apps/a/web/manifest.json', 'apps/a/web/deep/er/manifest.webmanifest', 'docs/manifest.webmanifest', 'docs/site.webmanifest', 'package.json']
                  .map((path) => ({ path, type: 'blob', size: 1 })));
                const web = (manifest, page) => P.webAppFacts(manifest, page);
                const four = (extra) => P.fourVersions({ releases: [], readmeHtml: html, ...extra }).map((v) => [v.platform, v.state, v.files.map((f) => f.name), v.note]);
                return {
                  links: P.readmeLinks(html),
                  manifests: ['', 'docs', 'apps/a', 'apps', 'src'].map((folder) => P.manifestOf(index, folder)),
                  ready: web('{"display":"standalone"}', '<link rel="apple-touch-icon" href="i.png">'),
                  browser: web('{"display":"browser"}', '<link href="i.png" rel=apple-touch-icon>'),
                  capable: web('not json', '<meta name="apple-mobile-web-app-capable" content="yes">'),
                  noWeb: four({}),
                  webReady: four({ web: { standalone: true, icon: true }, live: 'https://me.github.io/app/' }),
                  webHalf: four({ web: { standalone: true, icon: false } }),
                  unread: P.fourVersions({ releases: [], releasesKnown: false }).map((v) => v.state),
                };
            }"""
        )
        self.assertEqual([link["name"] for link in result["links"]["Windows"]], ["Cairn-Setup.cmd"], "a badge is not a download, and the same link counts once")
        self.assertEqual(result["links"]["Mac"][0]["url"], "https://github.com/me/app/releases/latest/download/App-macOS.dmg?a=1&b=2")
        self.assertEqual(result["links"]["live"], ["https://me.github.io/app/"])
        self.assertEqual(result["manifests"], ["docs/manifest.webmanifest", "docs/manifest.webmanifest", "apps/a/web/manifest.json", "", ""], "a repo does not borrow the manifest of an app three folders down")
        self.assertEqual(result["ready"], {"standalone": True, "icon": True})
        self.assertEqual(result["browser"], {"standalone": False, "icon": True})
        self.assertEqual(result["capable"], {"standalone": True, "icon": False})
        self.assertEqual(result["noWeb"][0], ["Windows", "there", ["Cairn-Setup.cmd"], "linked in the README"])
        self.assertEqual(result["noWeb"][2][:2], ["iPhone", "missing"])
        self.assertEqual(result["noWeb"][3], ["Android", "missing", [], "not published"])
        self.assertEqual(result["webReady"][2], ["iPhone", "there", ["Open the web app"], "in Safari: Share, then Add to Home Screen"])
        self.assertEqual(result["webHalf"][2][1], "missing")
        self.assertIn("apple-touch-icon", result["webHalf"][2][3])
        self.assertEqual(result["unread"], ["unknown", "unknown", "missing", "unknown"], "releases that could not be read are not reported as nothing published")

    # ------------------------------------------------------------------ in the app

    def test_a_repo_opens_with_its_four_versions(self):
        page = self.open("#/projects/juw12/ruang")
        rows = self.lines(page)
        self.assertEqual(page.inner_text(".repo-versions h2").replace("\n", " "), "Versions 3 of 4")
        self.assertEqual(rows["Windows"][0], "there")
        self.assertIn("Ruang-1.3.0-windows-x64-setup.exe", rows["Windows"][1])
        self.assertIn("v1.3.0", rows["Windows"][1])
        self.assertIn("4 MB", rows["Windows"][1])
        self.assertNotIn("SHA256SUMS", rows["Windows"][1])
        self.assertNotIn("1.2.0", rows["Windows"][1], "only the newest release that has the version is shown")
        self.assertEqual(rows["Mac"], ("missing", "Mac not published"))
        self.assertEqual(rows["Android"][0], "there")
        self.assertEqual(rows["iPhone"][0], "there")
        self.assertIn("Add to Home Screen", rows["iPhone"][1])
        self.assertEqual(page.get_attribute('.repo-version[data-version="iPhone"] a', "href"), "https://juw12.github.io/ruang/")
        download = page.locator('.repo-version[data-version="Android"] a')
        self.assertEqual(download.get_attribute("href"), "https://github.com/juw12/x/releases/download/t/Ruang-1.3.0-android.apk")
        self.assertEqual(download.get_attribute("rel"), "noopener noreferrer")
        # The block comes first, and the rest of the Look tab is still there.
        self.assertEqual(page.evaluate("document.querySelector('.repo-body').firstElementChild.className"), "repo-versions")
        page.wait_for_selector(".repo-doc")
        # A folder that is no app of its own has no block; the releases were asked for once.
        page.evaluate("location.hash = '#/projects/juw12/ruang/look/src'")
        page.wait_for_function("() => document.querySelector('.repo-head h1').textContent === 'src'")
        self.assertEqual(page.locator(".repo-versions").count(), 0)
        page.go_back()
        self.lines(page)
        self.assertEqual(len([call for call in self.api if call == "releases"]), 1)
        self.assertEqual(self.errors, [])

    def test_a_version_left_behind_and_a_download_from_the_readme_are_said(self):
        page = self.open("#/projects/juw12/buddy")
        rows = self.lines(page)
        self.assertEqual(rows["Mac"][0], "behind")
        self.assertIn("Buddy-mac.zip", rows["Mac"][1])
        self.assertIn("left behind: the newest release is v1.10.0", rows["Mac"][1])
        self.assertEqual(rows["Windows"][0], "there")
        self.assertIn("v1.10.0", rows["Windows"][1])
        self.assertNotIn("linux", rows["Windows"][1])
        self.assertEqual(rows["Android"][0], "there")
        self.assertIn("Buddy-phone.apk", rows["Android"][1])
        self.assertIn("linked in the README", rows["Android"][1])
        # A landing page with a manifest that opens in the browser is not an app for the home screen.
        self.assertEqual(rows["iPhone"][0], "missing")
        self.assertIn("display: standalone", rows["iPhone"][1])
        self.assertIn("apple-touch-icon", rows["iPhone"][1])
        self.assertEqual(page.inner_text(".repo-versions h2").replace("\n", " "), "Versions 3 of 4")
        self.assertEqual(self.errors, [])

    def test_each_app_of_a_repo_of_many_shows_its_own_versions(self):
        page = self.open("#/projects/juw12/projects")
        page.wait_for_function("() => /belong to 3 apps/.test(document.querySelector('.repo-versions')?.textContent || '')")
        self.assertEqual(page.locator(".repo-version").count(), 0, "the repo's own page does not mix the apps' files")
        page.evaluate("location.hash = '#/projects/juw12/projects/look/apps/glow'")
        page.wait_for_function("() => document.querySelector('.repo-head h1').textContent === 'glow'")
        rows = self.lines(page)
        self.assertEqual([rows[name][0] for name in ("Windows", "Mac", "iPhone", "Android")], ["there", "there", "there", "there"])
        self.assertIn("Glow-Setup.exe", rows["Windows"][1])
        self.assertIn("Glow-mac.dmg", rows["Mac"][1])
        self.assertNotIn("Mint", rows["Android"][1])
        # The live app is the README's link that names the folder, not the first link in it.
        self.assertEqual(page.get_attribute('.repo-version[data-version="iPhone"] a', "href"), "https://juw12.github.io/projects/glow/")
        page.evaluate("location.hash = '#/projects/juw12/projects/look/apps/twin'")
        page.wait_for_function("() => document.querySelector('.repo-head h1').textContent === 'twin'")
        rows = self.lines(page)
        self.assertEqual([rows[name][0] for name in ("Windows", "Mac", "iPhone", "Android")], ["there", "there", "missing", "missing"])
        self.assertIn("Twin-macOS-Intel.dmg", rows["Mac"][1])
        # An app with no version at all gets no block, like any other folder.
        page.evaluate("location.hash = '#/projects/juw12/projects/look/apps/notes'")
        page.wait_for_function("() => document.querySelector('.repo-head h1').textContent === 'notes'")
        page.wait_for_selector(".repo-doc")
        self.assertEqual(page.locator(".repo-versions").count(), 0)
        self.assertEqual(self.errors, [])

    def test_nothing_published_is_one_line_and_unread_releases_are_not_called_missing(self):
        page = self.open("#/projects/juw12/study")
        page.wait_for_function("() => /No Windows, Mac, iPhone or Android version is published/.test(document.querySelector('.repo-versions')?.textContent || '')")
        self.assertEqual(page.inner_text(".repo-versions h2").replace("\n", " "), "Versions 0 of 4")
        self.assertEqual(self.errors, [])

        page = self.open("#/projects/juw12/ruang", releases_fail=True)
        rows = self.lines(page)
        self.assertEqual(rows["Windows"][0], "unknown")
        self.assertIn("could not be read", rows["Windows"][1])
        self.assertEqual(rows["iPhone"][0], "there", "the web app is read from the repo, not from the releases")
        page.wait_for_timeout(600)
        self.assertEqual(len([call for call in self.api if call == "releases"]), 1, "a failed read is not tried again on every redraw")

    def test_a_phone_stacks_the_four_and_nothing_scrolls_sideways(self):
        page = self.open("#/projects/juw12/ruang", viewport=(390, 844))
        self.lines(page)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        boxes = page.eval_on_selector_all(".repo-version", "els => els.map((el) => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right)]; })")
        self.assertTrue(all(left >= 0 and right <= 390 for left, right in boxes), boxes)
        self.assertEqual(len({left for left, _ in boxes}), 1, "one version per row on a phone")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

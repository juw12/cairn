"""Repo pages (docs/repo.js): look inside a GitHub repo from Cairn - pictures, videos, README, pushes and files."""

import base64
import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import install_github_mock, launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

# The smallest PNG there is (one transparent pixel): enough for <img> to load.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
# A real film of a second and a half (160 x 90, VP8), so a picture can be taken from it and it can play.
VIDEO = (Path(__file__).resolve().parent / "fixtures" / "tiny.webm").read_bytes()
TOKEN = "ghp_" + "a" * 36
SESSION = json.dumps({"token": TOKEN, "method": "token", "login": "juw12", "name": "Juwen Lie", "avatar": "", "scopes": ["repo"]})

FILES = {
    "README.md": b"# Gallery\n",
    "LICENSE": b"MIT\n",
    "docs/media/banner.png": PNG,
    "docs/media/demo.webm": VIDEO,
    "web/icons/icon-192.png": PNG,
    "apps/hazel/README.md": b"# Hazel\n",
    "apps/hazel/docs/screenshot.png": PNG,
    "apps/hazel/src/main.py": b"print('hello from hazel')\n",
    "apps/plain/notes.txt": b"no pictures here\n",
    "tests/test_it.py": b"assert True\n",
}

README_HTML = """
<div id="readme"><article class="markdown-body">
<p align="center"><a href="https://example.com/site"><img src="docs/media/banner.png" alt="Banner" width="100%" style="position:fixed" onerror="window.hacked = 1"></a></p>
<div class="markdown-heading"><h2 class="heading-element">See it in action</h2><a id="user-content-see-it-in-action" class="anchor" href="#see-it-in-action"><svg></svg></a></div>
<p><a href="#see-it-in-action">Jump</a> · <a href="apps/hazel">Hazel folder</a> · <a href="apps/hazel/src/main.py">the code</a> ·
<a href="javascript:window.hacked = 2">bad link</a> · <a href="https://github.com/juw12/gallery/blob/main/LICENSE">license</a></p>
<script>window.hacked = 3</script>
<iframe src="https://example.com/frame"></iframe>
<video src="docs/media/demo.webm"></video>
<ul><li><input type="checkbox" checked> done</li></ul>
</article></div>
"""

# Another branch holds other files.
NEXT_FILES = {
    "README.md": b"# Gallery, next\n",
    "next/only-here.txt": b"written on the next branch\n",
    "next/shot.png": PNG,
    # Named a film, but not one any browser can play.
    "next/broken.mp4": b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64,
}

RELEASES = [
    {
        "tag_name": "v2.0.0", "name": "Gallery 2", "draft": False, "prerelease": False, "published_at": "2026-11-19T08:00:00Z", "author": {"login": "juw12"},
        "body_html": '<p>Bigger pictures. <a href="https://github.com/juw12/gallery/pull/7">#7</a></p><script>window.hacked = 4</script>',
        "assets": [{"name": "gallery-2.zip", "size": 2048576, "download_count": 12, "browser_download_url": "https://github.com/juw12/gallery/releases/download/v2.0.0/gallery-2.zip"}],
    },
    {"tag_name": "v2.1.0-beta", "name": "", "draft": False, "prerelease": True, "published_at": "2026-11-20T08:00:00Z", "author": {"login": "juw12"}, "body_html": "", "assets": []},
]

TALK = {
    "open": [
        {"number": 7, "title": "Show the pictures bigger", "state": "open", "user": {"login": "juw12"}, "updated_at": "2026-11-20T09:00:00Z", "comments": 1,
         "labels": [{"name": "look"}], "pull_request": {"merged_at": None}},
        {"number": 5, "title": "The banner is cut on a phone", "state": "open", "user": {"login": "friend"}, "updated_at": "2026-11-18T09:00:00Z", "comments": 0, "labels": []},
    ],
    "closed": [
        {"number": 3, "title": "Start the gallery", "state": "closed", "user": {"login": "juw12"}, "updated_at": "2026-11-10T09:00:00Z", "comments": 0,
         "labels": [], "pull_request": {"merged_at": "2026-11-10T09:00:00Z"}},
    ],
}
THREAD = {
    "issue": dict(TALK["open"][0], created_at="2026-11-19T09:00:00Z",
                  body_html='<p>Tiles are small. See <a href="docs/media/banner.png">the banner</a>.</p><img src="docs/media/banner.png" onerror="window.hacked = 5" onload="window.hacked = 6">'),
    "comments": [{"user": {"login": "friend"}, "created_at": "2026-11-19T10:00:00Z", "body_html": "<p>Yes please.</p>"}],
    "pull": {"head": {"ref": "bigger"}, "base": {"ref": "main"}, "changed_files": 1, "additions": 3, "deletions": 1, "merged_at": None},
    "reviews": [
        {"user": {"login": "friend"}, "state": "APPROVED", "submitted_at": "2026-11-19T12:00:00Z", "body_html": "<p>Looks right.</p>"},
        {"user": {"login": "friend"}, "state": "COMMENTED", "submitted_at": "2026-11-19T11:00:00Z", "body_html": ""},
    ],
    "lines": [{"user": {"login": "friend"}, "created_at": "2026-11-19T11:00:00Z", "path": "apps/hazel/src/main.py", "line": 2, "body_html": "<p>Why this line?</p>"}],
    "files": [{"filename": "apps/hazel/src/main.py", "status": "modified", "additions": 3, "deletions": 1, "patch": "@@ -1 +1,3 @@\n-print('hi')\n+print('hello')"}],
}

COMMITS = [
    {"sha": "a" * 40, "commit": {"message": "Add the Hazel screenshot\n\nTaken on a phone.", "author": {"name": "Juwen Lie", "date": "2026-11-20T08:00:00Z"}}, "author": {"login": "juw12"}},
    {"sha": "b" * 40, "commit": {"message": "Start the gallery", "author": {"name": "Juwen Lie", "date": "2026-11-18T08:00:00Z"}}, "author": {"login": "juw12"}},
]
COMMIT_DETAIL = {
    **COMMITS[0],
    "stats": {"additions": 3, "deletions": 1},
    "files": [
        {"filename": "apps/hazel/docs/screenshot.png", "status": "added", "additions": 0, "deletions": 0},
        {"filename": "apps/hazel/src/main.py", "status": "modified", "additions": 3, "deletions": 1, "patch": "@@ -1 +1,3 @@\n-print('hi')\n+print('hello from hazel')\n+<b>not markup</b>"},
        {"filename": "old.txt", "status": "removed", "additions": 0, "deletions": 0},
    ],
}


def repo_meta(name, private=False):
    return {
        "name": name,
        "full_name": f"juw12/{name}",
        "owner": {"login": "juw12", "avatar_url": ""},
        "description": "Pictures of what was built.",
        "html_url": f"https://github.com/juw12/{name}",
        "default_branch": "main",
        "private": private,
        "visibility": "private" if private else "public",
        "stargazers_count": 2,
        "language": "Python",
        "size": 900,
        "created_at": "2026-06-01T00:00:00Z",
        "pushed_at": "2026-11-20T08:00:00Z",
    }


CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, If-None-Match"}


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class RepoPageTests(unittest.TestCase):
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

    def open(self, address="#/projects", signed_in=False, viewport=(1440, 900)):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        install_github_mock(context)
        self.api, self.raw = [], []

        def json_reply(route, payload, status=200):
            route.fulfill(status=status, headers={**CORS, "Content-Type": "application/json"}, body=json.dumps(payload))

        def github(route):
            request = route.request
            path = unquote(urlparse(request.url).path)
            if request.method == "OPTIONS":
                return route.fulfill(status=204, headers=CORS)
            authed = request.headers.get("authorization") == f"Bearer {TOKEN}"
            self.api.append({"path": path, "auth": "authorization" in request.headers, "accept": request.headers.get("accept", "")})
            if path == "/user":
                return json_reply(route, {"login": "juw12", "name": "Juwen Lie"})
            if path in ("/users/juw12/repos", "/user/repos"):
                return json_reply(route, [repo_meta("gallery")] + ([repo_meta("secret", private=True)] if authed else []))
            parts = path.strip("/").split("/")
            if parts[:2] != ["repos", "juw12"] or len(parts) < 3 or parts[2] not in ("gallery", "secret"):
                return route.fallback()
            name, rest = parts[2], parts[3:]
            if name == "secret" and not authed:
                return json_reply(route, {"message": "Not Found"}, 404)
            if not rest:
                return json_reply(route, repo_meta(name, private=name == "secret"))
            query = parse_qs(urlparse(request.url).query)
            if rest == ["branches"]:
                return json_reply(route, [{"name": "next"}, {"name": "main"}])
            if rest[:2] == ["git", "trees"]:
                if rest[2] not in ("main", "next"):
                    return json_reply(route, {"message": "Not Found"}, 404)
                files = NEXT_FILES if rest[2] == "next" else FILES
                tree = [{"path": p, "type": "blob", "size": len(data), "sha": f"{i:040x}"} for i, (p, data) in enumerate(sorted(files.items()))]
                return json_reply(route, {"tree": tree, "truncated": False})
            if rest == ["releases"]:
                return json_reply(route, RELEASES)
            if rest == ["issues"]:
                return json_reply(route, TALK[query.get("state", ["open"])[0]])
            if rest == ["issues", "7"]:
                return json_reply(route, THREAD["issue"])
            if rest == ["issues", "7", "comments"]:
                return json_reply(route, THREAD["comments"])
            if rest == ["pulls", "7"]:
                return json_reply(route, THREAD["pull"])
            if rest == ["pulls", "7", "reviews"]:
                return json_reply(route, THREAD["reviews"])
            if rest == ["pulls", "7", "comments"]:
                return json_reply(route, THREAD["lines"])
            if rest == ["pulls", "7", "files"]:
                return json_reply(route, THREAD["files"])
            if rest == ["commits"]:
                return json_reply(route, COMMITS)
            if rest[0] == "commits":
                return json_reply(route, COMMIT_DETAIL)
            if rest[0] == "contents":
                file_path = "/".join(rest[1:])
                if not file_path:
                    return json_reply(route, [{"name": "README.md"}, {"name": "tests"}, {"name": "LICENSE"}])
                files = NEXT_FILES if query.get("ref", ["main"])[0] == "next" else FILES
                if file_path not in files:
                    return json_reply(route, {"message": "Not Found"}, 404)
                accept = request.headers.get("accept", "")
                if "html" in accept:
                    html = README_HTML if file_path == "README.md" and files is FILES else f"<article><h1>{file_path}</h1></article>"
                    return route.fulfill(status=200, headers={**CORS, "Content-Type": "text/html"}, body=html)
                if "raw" in accept:
                    return route.fulfill(status=200, headers={**CORS, "Content-Type": "application/octet-stream"}, body=files[file_path])
            return json_reply(route, {"message": "Not Found"}, 404)

        def raw(route):
            parts = unquote(urlparse(route.request.url).path).strip("/").split("/")
            self.raw.append("/".join(parts))
            file_path = "/".join(parts[3:])
            files = NEXT_FILES if parts[2:3] == ["next"] else FILES
            if parts[:2] == ["juw12", "gallery"] and file_path in files:
                # Like the real host: a video is "application/octet-stream".
                kind = "image/png" if file_path.endswith(".png") else "application/octet-stream" if file_path.endswith(".webm") else "text/plain"
                return route.fulfill(status=200, headers={**CORS, "Content-Type": kind}, body=files[file_path])
            return route.fulfill(status=404, headers=CORS, body="")

        context.route("https://api.github.com/**", github)
        context.route("https://raw.githubusercontent.com/**", raw)
        context.set_default_timeout(60_000)
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        script = "localStorage.setItem('cairn.theme', 'light'); localStorage.setItem('cairn.projects.owner', '\"juw12\"');"
        if signed_in:
            script += f"sessionStorage.setItem('cairn.session', {json.dumps(SESSION)});"
        page.add_init_script(script)
        page.goto(self.url + "?demo=1" + address, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector("#nav button")
        self.addCleanup(context.close)
        return page

    # ------------------------------------------------------------------ pure logic

    def test_an_address_names_a_repo_a_tab_and_a_folder(self):
        page = self.open()
        result = page.evaluate(
            """async () => {
                const R = await import('./repo.js');
                return {
                  root: R.parseRepoHash('#/projects/juw12/projects'),
                  folder: R.parseRepoHash('#/projects/juw12/projects/look/apps/hazel'),
                  file: R.parseRepoHash('#/projects/juw12/my.repo/file/docs/a%20b.png'),
                  climbing: R.parseRepoHash('#/projects/juw12/projects/files/../../etc'),
                  ranking: R.parseRepoHash('#/projects'),
                  other: R.parseRepoHash('#/vibe/juw12/projects'),
                  badOwner: R.parseRepoHash('#/projects/-bad-/projects'),
                  made: [
                    R.repoHash({ owner: 'juw12', name: 'projects' }),
                    R.repoHash({ owner: 'juw12', name: 'projects', tab: 'files', path: 'apps/my app' }),
                    R.repoHash({ owner: 'juw12', name: 'projects', tab: 'pushes' }),
                  ],
                };
            }"""
        )
        self.assertEqual(result["root"], {"owner": "juw12", "name": "projects", "tab": "look", "path": ""})
        self.assertEqual(result["folder"], {"owner": "juw12", "name": "projects", "tab": "look", "path": "apps/hazel"})
        self.assertEqual(result["file"], {"owner": "juw12", "name": "my.repo", "tab": "file", "path": "docs/a b.png"})
        self.assertEqual(result["climbing"]["path"], "etc")
        self.assertIsNone(result["ranking"])
        self.assertIsNone(result["other"])
        self.assertIsNone(result["badOwner"])
        self.assertEqual(result["made"], ["#/projects/juw12/projects", "#/projects/juw12/projects/files/apps/my%20app", "#/projects/juw12/projects/pushes"])

    def test_a_file_list_becomes_folders_counts_and_a_gallery_that_leads_with_what_shows_the_project(self):
        page = self.open()
        result = page.evaluate(
            """async (paths) => {
                const R = await import('./repo.js');
                const index = R.indexTree(paths.map((path, i) => ({ path, type: 'blob', size: 10 + i, sha: 's' + i })).concat([{ path: 'apps', type: 'tree' }]));
                return {
                  root: index.dirs.get(''),
                  hazel: index.dirs.get('apps/hazel'),
                  counts: Object.fromEntries([...index.counts]),
                  gallery: R.galleryOf(index, '').map((item) => item.path),
                  hazelGallery: R.galleryOf(index, 'apps/hazel').map((item) => item.path),
                  kinds: ['a.PNG', 'b.mov', 'c.mp3', 'd.py', 'noext'].map(R.mediaKind),
                  sizes: [0, 900, 4096, 1680367, 52428800].map(R.humanSize),
                };
            }""",
            sorted(FILES),
        )
        self.assertEqual(result["root"]["dirs"], ["apps", "docs", "tests", "web"])
        self.assertEqual([f["name"] for f in result["root"]["files"]], ["LICENSE", "README.md"])
        self.assertEqual(result["hazel"]["dirs"], ["docs", "src"])
        self.assertEqual(result["counts"][""], {"files": 10, "images": 3, "videos": 1})
        self.assertEqual(result["counts"]["apps"], {"files": 4, "images": 1, "videos": 0})
        self.assertEqual(result["counts"]["apps/plain"], {"files": 1, "images": 0, "videos": 0})
        # The video and the banner lead; an app icon comes last.
        self.assertEqual(result["gallery"][-1], "web/icons/icon-192.png")
        self.assertEqual(set(result["gallery"][:2]), {"docs/media/demo.webm", "docs/media/banner.png"})
        self.assertEqual(result["hazelGallery"], ["apps/hazel/docs/screenshot.png"])
        self.assertEqual(result["kinds"], ["image", "video", "audio", None, None])
        self.assertEqual(result["sizes"], ["0 B", "900 B", "4 KB", "1.6 MB", "50 MB"])

    def test_a_readme_is_cleaned_and_pointed_at_the_repo(self):
        page = self.open()
        result = page.evaluate(
            """async (html) => {
                const R = await import('./repo.js');
                const ctx = (isPrivate) => ({ owner: 'juw12', name: 'gallery', ref: 'main', dir: '', isPrivate, isDir: (p) => p === 'apps/hazel' });
                const read = (isPrivate) => {
                  const box = document.createElement('div');
                  box.innerHTML = R.cleanHtml(html, ctx(isPrivate));
                  const links = Object.fromEntries([...box.querySelectorAll('a')].map((a) => [a.textContent.trim() || 'picture', {
                    href: a.getAttribute('href'), target: a.getAttribute('target'), rel: a.getAttribute('rel'),
                    go: a.hasAttribute('data-repo-go'), anchor: a.getAttribute('data-repo-anchor'),
                  }]));
                  const img = box.querySelector('img');
                  const video = box.querySelector('video');
                  return {
                    html: box.innerHTML, links,
                    img: { src: img.getAttribute('src'), wait: img.getAttribute('data-repo-src'), style: img.getAttribute('style'), onerror: img.getAttribute('onerror'), width: img.getAttribute('width') },
                    video: { src: video.getAttribute('src'), wait: video.getAttribute('data-repo-src'), controls: video.hasAttribute('controls') },
                    heading: box.querySelector('h2').id,
                    box: box.querySelector('input').outerHTML,
                  };
                };
                const refs = [
                  ['../up.png', 'apps/hazel'], ['../../../out.png', 'apps'], ['/abs/x.png', 'apps'], ['./a/./b.png?raw=true', 'docs'],
                  ['https://github.com/JUW12/Gallery/raw/main/docs/x.png', ''], ['https://raw.githubusercontent.com/juw12/gallery/main/y.png', ''],
                  ['https://github.com/juw12/gallery/blob/other/z.png', ''], ['https://github.com/someone/else/blob/main/z.png', ''],
                  ['data:text/html,<script>1</script>', ''], ['mailto:a@b.co', ''], ['#top', ''],
                ].map(([raw, dir]) => R.resolveRef(raw, { owner: 'juw12', name: 'gallery', ref: 'main', dir }));
                return { open: read(false), closed: read(true), refs, hacked: window.hacked ?? null };
            }""",
            README_HTML,
        )
        opened, closed = result["open"], result["closed"]
        self.assertIsNone(result["hacked"], "nothing in a README may run")
        for forbidden in ("<script", "<iframe", "<svg", "javascript:", "onerror", "position:fixed", "window.hacked"):
            self.assertNotIn(forbidden, opened["html"])
        # A public repo's picture loads straight from the raw host; a video is fetched first (the raw host serves it as a download).
        self.assertEqual(opened["img"], {"src": "https://raw.githubusercontent.com/juw12/gallery/main/docs/media/banner.png", "wait": None, "style": None, "onerror": None, "width": "100%"})
        self.assertEqual(opened["video"], {"src": None, "wait": "docs/media/demo.webm", "controls": True})
        # A private repo's picture is fetched with the sign-in instead.
        self.assertEqual(closed["img"]["src"], None)
        self.assertEqual(closed["img"]["wait"], "docs/media/banner.png")
        links = opened["links"]
        self.assertEqual(links["picture"], {"href": "https://example.com/site", "target": "_blank", "rel": "noopener noreferrer", "go": False, "anchor": None})
        self.assertEqual(links["Jump"]["anchor"], "see-it-in-action")
        self.assertEqual(opened["heading"], "repo-anchor-see-it-in-action")
        self.assertEqual(links["Hazel folder"], {"href": "#/projects/juw12/gallery/look/apps/hazel", "target": None, "rel": None, "go": True, "anchor": None})
        self.assertEqual(links["the code"]["href"], "#/projects/juw12/gallery/file/apps/hazel/src/main.py")
        self.assertEqual(links["license"]["href"], "#/projects/juw12/gallery/file/LICENSE")
        self.assertIsNone(links["bad link"]["href"])
        self.assertIn("disabled", opened["box"])
        self.assertEqual(
            result["refs"],
            [
                {"kind": "repo", "path": "apps/up.png"}, None, {"kind": "repo", "path": "abs/x.png"}, {"kind": "repo", "path": "docs/a/b.png"},
                {"kind": "repo", "path": "docs/x.png"}, {"kind": "repo", "path": "y.png"},
                {"kind": "web", "url": "https://github.com/juw12/gallery/blob/other/z.png"}, {"kind": "web", "url": "https://github.com/someone/else/blob/main/z.png"},
                None, {"kind": "web", "url": "mailto:a@b.co"}, {"kind": "anchor", "name": "top"},
            ],
        )

    # ------------------------------------------------------------------ in the app

    def test_a_project_opens_inside_cairn_with_its_pictures_videos_readme_and_folders(self):
        page = self.open()
        page.wait_for_selector(".proj-card")
        link = page.locator('.proj-card[data-project="juw12/gallery"] .proj-title a')
        self.assertEqual(link.get_attribute("href"), "#/projects/juw12/gallery")
        # GitHub itself is still one click away, in the card's row of facts.
        self.assertEqual(page.locator('.proj-card[data-project="juw12/gallery"] .proj-meta a[href="https://github.com/juw12/gallery"]').count(), 1)
        link.click()
        page.wait_for_selector(".repo-doc")
        self.assertEqual(page.evaluate("location.hash"), "#/projects/juw12/gallery")
        self.assertEqual(page.evaluate("window.cairn.state.view"), "projects")
        self.assertEqual(page.inner_text(".repo-head h1"), "gallery")
        self.assertIn("3 pictures · 1 video", page.inner_text(".repo-sec h2"))
        tiles = page.eval_on_selector_all(".repo-grid .repo-tile", "els => els.map((el) => el.title)")
        self.assertEqual(len(tiles), 4)
        self.assertEqual(tiles[-1], "web/icons/icon-192.png")
        # Pictures load: the first picture tile has real pixels.
        page.wait_for_function("() => [...document.querySelectorAll('.repo-tile.image img')].every((img) => img.complete && img.naturalWidth > 0)")
        # A video's tile is a picture taken from the video, not a player: a phone gives a page only a few players.
        page.wait_for_function("() => { const img = document.querySelector('.repo-tile.video img'); return img && img.src.startsWith('blob:') && img.naturalWidth === 160; }")
        self.assertEqual(page.locator(".repo-grid video").count(), 0)
        # And it is a picture of the film (orange, in this one), not the black a browser hands over when it is
        # asked for a frame too early. Build 92 showed black tiles in Safari.
        light = page.evaluate(
            """() => {
                const canvas = document.createElement('canvas');
                canvas.width = 16; canvas.height = 9;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(document.querySelector('.repo-tile.video img'), 0, 0, 16, 9);
                const data = ctx.getImageData(0, 0, 16, 9).data;
                let red = 0;
                for (let i = 0; i < data.length; i += 4) red += data[i];
                return red / (data.length / 4);
            }"""
        )
        self.assertGreater(light, 150)
        self.assertEqual(page.evaluate("fetch(document.querySelector('.repo-tile.video img').src).then((r) => r.blob()).then((b) => b.type)"), "image/jpeg")
        # Folders with pictures are cards with a cover; the rest are plain links to their files.
        cards = page.eval_on_selector_all(".repo-folder strong", "els => els.map((el) => el.textContent)")
        self.assertEqual(cards, ["apps", "docs", "web"])
        self.assertIn("tests", page.inner_text(".repo-plain"))
        self.assertIn("1 picture · 4 files", page.inner_text('.repo-folder[href$="/look/apps"]'))
        # The README is GitHub's own rendering, cleaned.
        self.assertIn("See it in action", page.inner_text(".repo-doc"))
        self.assertIsNone(page.evaluate("window.hacked ?? null"))
        self.assertEqual(page.locator(".repo-doc script, .repo-doc iframe").count(), 0)
        page.wait_for_function("() => document.querySelector('.repo-doc img').naturalWidth > 0")

        # Into a folder, and its own README and pictures show.
        page.click('.repo-folder[href$="/look/apps"]')
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/look/apps'")
        self.assertEqual(page.inner_text(".repo-head h1"), "apps")
        page.click('.repo-folder[href$="/look/apps/hazel"]')
        page.wait_for_selector(".repo-doc")
        self.assertEqual(page.inner_text(".repo-head h1"), "hazel")
        self.assertIn("apps/hazel/README.md", page.inner_text(".repo-doc"))
        self.assertEqual(page.eval_on_selector_all(".repo-grid .repo-tile", "els => els.map((el) => el.title)"), ["apps/hazel/docs/screenshot.png"])

        # Back goes up one step at a time, and ends on the ranking.
        page.go_back()
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/look/apps'")
        self.assertEqual(page.inner_text(".repo-head h1"), "apps")
        page.go_back()
        page.go_back()
        page.wait_for_selector(".proj-card")
        self.assertEqual(page.locator(".repo").count(), 0)
        # One look costs three GitHub calls: the repo, its file list and the README. Nothing is asked twice.
        asked = [call["path"] for call in self.api if call["path"].startswith("/repos/juw12/gallery") and not call["path"].endswith("/contents/")]
        self.assertEqual(asked.count("/repos/juw12/gallery"), 1)
        self.assertEqual(len([p for p in asked if "/git/trees/" in p]), 1)
        self.assertEqual(asked.count("/repos/juw12/gallery/contents/README.md"), 1)
        self.assertEqual(self.errors, [])

    def test_pictures_and_videos_open_full_screen_and_step_with_the_arrows(self):
        page = self.open("#/projects/juw12/gallery")
        page.wait_for_selector(".repo-grid .repo-tile")
        page.click('.repo-tile[title="docs/media/banner.png"]')
        page.wait_for_selector(".repo-lightbox img")
        page.wait_for_function("() => document.querySelector('.repo-lightbox img').naturalWidth > 0")
        caption = page.inner_text(".repo-lb-cap")
        self.assertIn("docs/media/banner.png", caption)
        self.assertIn("of 4", caption)
        before = caption
        page.keyboard.press("ArrowRight")
        page.wait_for_function("(before) => document.querySelector('.repo-lb-cap').innerText !== before", arg=before)
        page.keyboard.press("ArrowLeft")
        page.wait_for_function("(before) => document.querySelector('.repo-lb-cap').innerText === before", arg=before)
        # The video plays from a blob, with controls.
        page.keyboard.press("Escape")
        self.assertEqual(page.locator(".repo-lightbox").count(), 0)
        page.click(".repo-tile.video")
        page.wait_for_function("() => (document.querySelector('.repo-lightbox video')?.src || '').startsWith('blob:')")
        self.assertTrue(page.evaluate("document.querySelector('.repo-lightbox video').controls"))
        # The raw host calls a video "a file to download", which a phone will not play; the blob says what it is.
        self.assertEqual(page.evaluate("fetch(document.querySelector('.repo-lightbox video').src).then((r) => r.blob()).then((b) => b.type)"), "video/webm")
        # It loads and its clock runs.
        page.wait_for_function("() => { const v = document.querySelector('.repo-lightbox video'); return v.videoWidth === 160 && v.currentTime > 0.2; }")
        # The file's name in the caption opens the file's own page.
        page.click(".repo-lb-cap a")
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/file/docs/media/demo.webm'")
        self.assertEqual(page.locator(".repo-lightbox").count(), 0)
        page.wait_for_function("() => (document.querySelector('.repo-stage video')?.src || '').startsWith('blob:')")
        self.assertEqual(self.errors, [])

    def test_pushes_open_to_their_pictures_files_and_changes_and_files_open_as_text(self):
        page = self.open("#/projects/juw12/gallery/pushes")
        page.wait_for_selector(".repo-push")
        rows = page.eval_on_selector_all(".repo-push-msg", "els => els.map((el) => el.textContent)")
        self.assertEqual(rows, ["Add the Hazel screenshot", "Start the gallery"])
        page.click(".repo-push-row")
        page.wait_for_selector(".repo-changed")
        detail = page.inner_text(".repo-push-detail")
        self.assertIn("Taken on a phone.", detail)
        self.assertIn("3 files", detail)
        self.assertEqual(page.eval_on_selector_all(".repo-status", "els => els.map((el) => el.textContent)"), ["new", "changed", "deleted"])
        # The picture pushed in that commit is shown as it was in that commit.
        self.assertEqual(page.get_attribute(".repo-push-detail .repo-tile img", "src"), f"https://raw.githubusercontent.com/juw12/gallery/{'a' * 40}/apps/hazel/docs/screenshot.png")
        # A change is text, never markup.
        page.click(".repo-changed summary")
        self.assertIn("<b>not markup</b>", page.inner_text(".repo-patch"))
        self.assertEqual(page.locator(".repo-patch b").count(), 0)
        self.assertEqual(page.locator(".repo-patch .add").count(), 2)
        self.assertEqual(page.locator(".repo-patch .del").count(), 1)

        # Files: the folder's listing, then one file as text.
        page.click(".repo-tabs button:has-text('Files')")
        page.wait_for_selector(".repo-files")
        names = page.eval_on_selector_all(".repo-files a span:nth-child(2)", "els => els.map((el) => el.textContent)")
        self.assertEqual(names, ["apps", "docs", "tests", "web", "LICENSE", "README.md"])
        page.click(".repo-files a:has-text('tests')")
        page.click(".repo-files a:has-text('test_it.py')")
        page.wait_for_selector(".repo-code")
        self.assertEqual(page.inner_text(".repo-code").strip(), "assert True")
        self.assertEqual(page.evaluate("location.hash"), "#/projects/juw12/gallery/file/tests/test_it.py")
        # Typing user/repo in the ranking's box opens that repo.
        page.go_back()
        page.evaluate("history.pushState(null, '', '#/projects'); window.cairn.rerender()")
        page.wait_for_selector("#projects-owner")
        page.fill("#projects-owner", "juw12/gallery")
        page.press("#projects-owner", "Enter")
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery'")
        self.assertEqual(self.errors, [])

    def test_a_private_repo_is_read_with_the_sign_in_and_never_from_the_public_host(self):
        page = self.open("#/projects/juw12/secret", signed_in=True)
        page.wait_for_selector(".repo-grid .repo-tile")
        self.assertIn("private", page.inner_text(".repo-chips"))
        # Every picture is fetched through the API with the token and shown as a blob.
        page.wait_for_function("() => [...document.querySelectorAll('.repo-tile.image img')].every((img) => img.src.startsWith('blob:') && img.naturalWidth > 0)")
        page.wait_for_function("() => (document.querySelector('.repo-doc img')?.src || '').startsWith('blob:')")
        files = [call for call in self.api if call["path"].startswith("/repos/juw12/secret/contents/") and "raw" in call["accept"]]
        self.assertGreaterEqual(len(files), 3)
        self.assertTrue(all(call["auth"] for call in files))
        self.assertEqual([path for path in self.raw if path.startswith("juw12/secret")], [], "a private file is never asked of the public host")
        self.assertEqual(self.errors, [])

    def test_a_repo_that_cannot_be_read_says_so_once(self):
        # Signed out, a private repo looks like it is not there.
        page = self.open("#/projects/juw12/secret")
        page.wait_for_selector(".repo .empty-state")
        page.wait_for_function("() => document.querySelector('.repo .empty-state h3').textContent.startsWith(\"Couldn't open\")")
        self.assertIn("signed in", page.inner_text(".repo .empty-state"))
        page.wait_for_timeout(800)
        asked = [call for call in self.api if call["path"] == "/repos/juw12/secret"]
        self.assertEqual(len(asked), 1, "a failed look must not be retried on every redraw")
        # A folder that is not there says so, and the rest of the page still works.
        page.evaluate("history.pushState(null, '', '#/projects/juw12/gallery/look/nowhere'); window.cairn.rerender()")
        page.wait_for_selector(".repo-tabs")
        self.assertIn("No such folder", page.inner_text(".repo-body"))

    def test_a_phone_shows_two_tiles_a_row_and_nothing_scrolls_sideways(self):
        page = self.open("#/projects/juw12/gallery", viewport=(390, 844))
        page.wait_for_selector(".repo-doc")
        columns = page.evaluate("getComputedStyle(document.querySelector('.repo-grid')).gridTemplateColumns.split(' ').length")
        self.assertEqual(columns, 2)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        # Full screen, the two arrows sit under the picture, so they never cover a video's own controls.
        page.click(".repo-tile.video")
        page.wait_for_selector(".repo-lightbox video")
        stage, arrow = page.evaluate(
            "() => ['.repo-lb-stage', '.repo-lb-nav.next'].map((s) => { const r = document.querySelector(s).getBoundingClientRect(); return [r.top, r.bottom]; })"
        )
        self.assertGreaterEqual(arrow[0], stage[1])
        self.assertLessEqual(arrow[1], 844)
        page.keyboard.press("Escape")
        page.click(".repo-tabs button:has-text('Pushes')")
        page.wait_for_selector(".repo-push")
        page.click(".repo-push-row")
        page.wait_for_selector(".repo-changed")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------------ branches, releases, issues, links

    def test_a_github_address_becomes_a_place_in_cairn(self):
        page = self.open()
        result = page.evaluate(
            """async () => {
                const R = await import('./repo.js');
                const to = (path) => R.githubTarget('https://github.com/' + path);
                return {
                  places: [
                    'juw12/cairn', 'juw12/cairn.git', 'juw12/cairn/', 'juw12/cairn/tree/main/docs/media', 'juw12/cairn/blob/next/README.md',
                    'juw12/cairn/tree/HEAD', 'juw12/cairn/pull/72', 'juw12/cairn/pull/72/files', 'juw12/cairn/issues/5#issuecomment-1',
                    'juw12/cairn/issues', 'juw12/cairn/pulls', 'juw12/cairn/commit/2E657F4abc', 'juw12/cairn/commits/next',
                    'juw12/cairn/releases', 'juw12/cairn/releases/tag/v1.0.0', 'juw12/cairn/releases/latest',
                  ].map(to),
                  left: [
                    'juw12', 'settings/tokens/new', 'login/device', 'orgs/acme/people', 'juw12/cairn/issues/new?title=x',
                    'juw12/cairn/releases/download/v1/cairn.zip', 'juw12/cairn/releases/latest/download/cairn.zip', 'juw12/cairn/actions/runs/1',
                    'juw12/cairn/blob/main', 'juw12/cairn/compare/a...b', 'user-attachments/assets/abc',
                  ].map(to),
                  elsewhere: [R.githubTarget('https://gitlab.com/juw12/cairn'), R.githubTarget('https://api.github.com/repos/juw12/cairn'), R.githubTarget('not an address'), R.githubTarget('javascript:alert(1)')],
                  www: R.githubTarget('http://www.github.com/juw12/cairn'),
                  parsed: [
                    R.parseRepoHash('#/projects/juw12/cairn/files/docs?ref=feature%2Fbig'),
                    R.parseRepoHash('#/projects/juw12/cairn/pushes?commit=2e657f4'),
                    R.parseRepoHash('#/projects/juw12/cairn/talk?state=closed'),
                    R.parseRepoHash('#/projects/juw12/cairn/talk/72'),
                    R.parseRepoHash('#/projects/juw12/cairn?ref=..%2Fup&commit=zzz&state=other'),
                  ],
                  made: R.repoHash({ owner: 'juw12', name: 'cairn', tab: 'files', path: 'docs', ref: 'feature/big' }),
                };
            }"""
        )
        self.assertEqual(
            result["places"],
            [
                "#/projects/juw12/cairn", "#/projects/juw12/cairn", "#/projects/juw12/cairn", "#/projects/juw12/cairn/look/docs/media?ref=main",
                "#/projects/juw12/cairn/file/README.md?ref=next", "#/projects/juw12/cairn", "#/projects/juw12/cairn/talk/72", "#/projects/juw12/cairn/talk/72",
                "#/projects/juw12/cairn/talk/5", "#/projects/juw12/cairn/talk", "#/projects/juw12/cairn/talk", "#/projects/juw12/cairn/pushes?commit=2e657f4abc",
                "#/projects/juw12/cairn/pushes?ref=next", "#/projects/juw12/cairn/releases", "#/projects/juw12/cairn/releases", "#/projects/juw12/cairn/releases",
            ],
        )
        self.assertEqual(result["left"], [None] * 11, "pages Cairn has no place for, and downloads, stay on GitHub")
        self.assertEqual(result["elsewhere"], [None] * 4)
        self.assertEqual(result["www"], "#/projects/juw12/cairn")
        self.assertEqual(
            result["parsed"],
            [
                {"owner": "juw12", "name": "cairn", "tab": "files", "path": "docs", "ref": "feature/big"},
                {"owner": "juw12", "name": "cairn", "tab": "pushes", "path": "", "commit": "2e657f4"},
                {"owner": "juw12", "name": "cairn", "tab": "talk", "path": "", "state": "closed"},
                {"owner": "juw12", "name": "cairn", "tab": "talk", "path": "72"},
                {"owner": "juw12", "name": "cairn", "tab": "look", "path": ""},
            ],
        )
        self.assertEqual(result["made"], "#/projects/juw12/cairn/files/docs?ref=feature%2Fbig")

    def test_another_branch_shows_its_own_files(self):
        page = self.open("#/projects/juw12/gallery/files")
        page.wait_for_selector(".repo-files")
        chip = page.locator("[data-repo-branches]")
        self.assertEqual(chip.inner_text().strip(), "main")
        # The branches are asked of GitHub only when the chip is pressed.
        self.assertEqual([call for call in self.api if call["path"].endswith("/branches")], [])
        chip.click()
        page.wait_for_selector(".repo-branches a")
        names = page.eval_on_selector_all(".repo-branches a", "els => els.map((el) => el.textContent.trim())")
        self.assertEqual(names, ["main default", "next"])
        self.assertEqual(page.get_attribute(".repo-branches a.on", "href"), "#/projects/juw12/gallery/files")
        page.click(".repo-branches a:has-text('next')")
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/files?ref=next'")
        page.wait_for_function("() => document.querySelector('[data-repo-branches]')?.textContent.trim() === 'next'")
        self.assertEqual(page.eval_on_selector_all(".repo-files a span:nth-child(2)", "els => els.map((el) => el.textContent)"), ["next", "README.md"])
        # Every step from here stays on that branch: a folder, a file, a picture.
        page.click(".repo-files a:has-text('next')")
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/files/next?ref=next'")
        page.click(".repo-files a:has-text('only-here.txt')")
        page.wait_for_selector(".repo-code")
        self.assertEqual(page.inner_text(".repo-code").strip(), "written on the next branch")
        self.assertEqual(page.get_attribute(".repo-head a[data-github]", "href"), "https://github.com/juw12/gallery/blob/next/next/only-here.txt")
        page.click(".repo-tabs button:has-text('Look')")
        page.wait_for_selector(".repo-tile.image img")
        self.assertEqual(page.get_attribute(".repo-tile.image img", "src"), "https://raw.githubusercontent.com/juw12/gallery/next/next/shot.png")
        # A film the browser cannot play says so and offers to save it, instead of an empty box.
        page.click(".repo-tile.video")
        page.wait_for_function("() => /cannot play broken\\.mp4/.test(document.querySelector('.repo-lightbox .repo-lb-wait')?.innerText || '')")
        with page.expect_download() as saved:
            page.click(".repo-lightbox [data-repo-download]")
        self.assertEqual(saved.value.suggested_filename, "broken.mp4")
        page.keyboard.press("Escape")
        page.click(".repo-tabs button:has-text('Files')")
        page.click(".repo-files a:has-text('broken.mp4')")
        page.wait_for_function("() => /cannot play this file/.test(document.querySelector('.repo-stage')?.innerText || '')")
        # Back on the default branch nothing of the other one shows, and the two are kept apart.
        page.goto(self.url + "?demo=1#/projects/juw12/gallery/files", wait_until="domcontentloaded")
        page.wait_for_selector(".repo-files")
        self.assertIn("apps", page.inner_text(".repo-files"))
        self.assertEqual(self.errors, [])
        # A branch that is not there says so.
        page.evaluate("history.pushState(null, '', '#/projects/juw12/gallery?ref=gone'); window.cairn.rerender()")
        page.wait_for_function("() => /no branch or tag named gone/.test(document.querySelector('.repo .empty-state')?.innerText || '')")

    def test_releases_show_their_notes_and_files(self):
        page = self.open("#/projects/juw12/gallery")
        page.wait_for_selector(".repo-tabs")
        page.click(".repo-tabs button:has-text('Releases')")
        page.wait_for_selector(".repo-release")
        self.assertEqual(page.evaluate("location.hash"), "#/projects/juw12/gallery/releases")
        titles = page.eval_on_selector_all(".repo-release > h2", "els => els.map((el) => el.innerText.replace(/\\s+/g, ' ').trim())")
        self.assertEqual(titles, ["Gallery 2 latest", "v2.1.0-beta pre-release"])
        self.assertIn("Bigger pictures.", page.inner_text(".repo-release .repo-doc"))
        self.assertIsNone(page.evaluate("window.hacked ?? null"))
        self.assertEqual(page.locator(".repo-release script").count(), 0)
        # A file is saved straight from GitHub's file store: a download link, never turned into a page of Cairn.
        asset = page.locator(".repo-assets a").first
        self.assertEqual(asset.get_attribute("href"), "https://github.com/juw12/gallery/releases/download/v2.0.0/gallery-2.zip")
        self.assertIsNotNone(asset.get_attribute("download"))
        self.assertIsNotNone(asset.get_attribute("data-github"))
        self.assertIn("2.0 MB · 12 downloads", page.inner_text(".repo-assets li"))
        self.assertIn("Source code (zip)", page.inner_text(".repo-assets"))
        # The link to a pull request inside the release notes opens that pull request here.
        page.click(".repo-release .repo-doc a")
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/talk/7'")
        self.assertEqual(self.errors, [])

    def test_issues_and_pull_requests_open_with_everything_said_on_them(self):
        page = self.open("#/projects/juw12/gallery/talk")
        page.wait_for_selector(".repo-talk-row")
        rows = page.eval_on_selector_all(".repo-talk-row", "els => els.map((el) => el.innerText.replace(/\\s+/g, ' ').trim())")
        self.assertEqual(len(rows), 2)
        self.assertIn("Show the pictures bigger look open pull request #7 · juw12", rows[0])
        self.assertIn("1 comment", rows[0])
        self.assertIn("The banner is cut on a phone open issue #5 · friend", rows[1])
        # Closed ones are one press away, and a merged pull request says so.
        page.click(".repo-kinds button:has-text('Closed')")
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/talk?state=closed'")
        page.wait_for_function("() => /Start the gallery/.test(document.querySelector('.repo-talk-row')?.innerText || '')")
        self.assertIn("merged", page.inner_text(".repo-talk-row"))
        page.go_back()
        page.wait_for_function("() => /Show the pictures bigger/.test(document.querySelector('.repo-talk-row')?.innerText || '')")

        page.click(".repo-talk-row")
        page.wait_for_selector(".repo-thread")
        self.assertEqual(page.evaluate("location.hash"), "#/projects/juw12/gallery/talk/7")
        self.assertEqual(page.inner_text(".repo-thread-title").replace("\n", " "), "Show the pictures bigger #7")
        facts = page.inner_text(".repo-thread-facts")
        self.assertIn("bigger into main", facts)
        self.assertIn("1 file", facts)
        said = page.eval_on_selector_all(".repo-say", "els => els.map((el) => el.innerText.replace(/\\s+/g, ' ').trim())")
        self.assertEqual(len(said), 4, "the opening text, a comment, a line comment and a review; an empty review is left out")
        self.assertTrue(said[0].startswith("juw12 opened this"))
        self.assertIn("Tiles are small.", said[0])
        self.assertIn("Yes please.", said[1])
        self.assertTrue(said[2].startswith("friend on apps/hazel/src/main.py line 2"))
        self.assertTrue(said[3].startswith("friend approved"))
        # Nothing in what people wrote can run, and a link to a file of the repo opens that file here.
        self.assertIsNone(page.evaluate("window.hacked ?? null"))
        self.assertEqual(page.get_attribute(".repo-say .repo-doc a", "href"), "#/projects/juw12/gallery/file/docs/media/banner.png")
        # The files the pull request changes, with their lines.
        page.click(".repo-changed summary")
        self.assertIn("+print('hello')", page.inner_text(".repo-patch"))
        self.assertEqual(page.get_attribute(".repo-head a[data-github]", "href"), "https://github.com/juw12/gallery/issues/7")
        page.click(".repo-filebar a")
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/talk'")
        self.assertEqual(self.errors, [])

    def test_a_link_to_github_anywhere_in_cairn_opens_here(self):
        page = self.open("#/home")
        page.wait_for_function("() => window.cairn.state.view === 'home'")
        page.evaluate(
            """() => {
                const box = document.createElement('p');
                box.id = 'links';
                box.innerHTML = `<a id="to-pull" href="https://github.com/juw12/gallery/pull/7" target="_blank" rel="noopener">a pull request</a>
                  <a id="to-commit" href="https://github.com/juw12/gallery/commit/aaaaaaa" target="_blank">a push</a>
                  <a id="to-github" href="https://github.com/juw12/gallery" data-github>the GitHub button</a>
                  <a id="to-person" href="https://github.com/juw12">a person</a>
                  <a id="to-download" href="https://github.com/juw12/gallery/releases/download/v2.0.0/gallery-2.zip">a download</a>`;
                document.querySelector('#view').prepend(box);
                window.kept = [];
                // Whatever is left for the browser is stopped here, so the test never leaves the page.
                document.addEventListener('click', (event) => {
                  const a = event.target.closest('#links a');
                  if (a && !event.defaultPrevented) { window.kept.push(a.id); event.preventDefault(); }
                });
            }"""
        )
        for kept in ("to-github", "to-person", "to-download"):
            page.click(f"#{kept}")
        self.assertEqual(page.evaluate("window.kept"), ["to-github", "to-person", "to-download"])
        self.assertEqual(page.evaluate("window.cairn.state.view"), "home")
        # Cmd or Shift with the click keeps GitHub too (a new tab or window, as with any link).
        page.click("#to-pull", modifiers=["Meta"])
        page.click("#to-pull", modifiers=["Shift"])
        self.assertEqual(page.evaluate("window.kept.length"), 5)
        # A plain click opens the pull request inside Cairn, on the Projects page.
        page.click("#to-pull")
        page.wait_for_selector(".repo-thread")
        self.assertEqual(page.evaluate("[location.hash, window.cairn.state.view]"), ["#/projects/juw12/gallery/talk/7", "projects"])
        # Back returns to where the link was.
        page.go_back()
        page.wait_for_function("() => window.cairn.state.view === 'home'")
        # A link to one push shows that push above the list.
        page.evaluate("document.querySelector('#view').insertAdjacentHTML('afterbegin', '<a id=\"again\" href=\"https://github.com/juw12/gallery/commit/aaaaaaa\">a push</a>')")
        page.click("#again")
        page.wait_for_selector(".repo-picked .repo-changed")
        self.assertEqual(page.evaluate("location.hash"), "#/projects/juw12/gallery/pushes?commit=aaaaaaa")
        self.assertIn("Add the Hazel screenshot", page.inner_text(".repo-picked"))
        self.assertIn("Latest pushes", page.inner_text(".repo-body"))
        # The version history is a dialog: its link to a pull request closes it and opens the pull request.
        page.evaluate("document.querySelector('[data-action=\"versions\"]').click()")
        page.wait_for_selector(".modal.versions .ver")
        page.evaluate("document.querySelector('.modal.versions .ver li a').setAttribute('href', 'https://github.com/juw12/gallery/pull/7')")
        page.evaluate("document.querySelector('.modal.versions .ver li a').click()")
        page.wait_for_function("() => location.hash === '#/projects/juw12/gallery/talk/7'")
        self.assertEqual(page.locator(".modal.versions").count(), 0)
        self.assertEqual(self.errors, [])

    def test_videos_lead_the_gallery_and_one_kind_can_be_shown(self):
        page = self.open("#/projects/juw12/gallery")
        page.wait_for_selector(".repo-grid .repo-tile")
        result = page.evaluate(
            """async () => {
                const R = await import('./repo.js');
                const covers = Array.from({ length: 40 }, (_, i) => `videos/ep${String(i).padStart(2, '0')}/cover.png`);
                const films = Array.from({ length: 9 }, (_, i) => `videos/ep${String(i).padStart(2, '0')}/final_cut.mp4`);
                const index = R.indexTree([...covers, ...films].map((path) => ({ path, type: 'blob', size: 5 })));
                return R.galleryOf(index, '').slice(0, 12).map((item) => item.kind);
            }"""
        )
        self.assertEqual(result, ["video"] * 6 + ["image"] * 6, "forty covers must not push every video off the first screen")
        self.assertEqual(page.eval_on_selector_all(".repo-kinds button", "els => els.map((el) => el.textContent)"), ["All", "Pictures 3", "Videos 1"])
        page.click(".repo-kinds button:has-text('Videos')")
        page.wait_for_function("() => document.querySelectorAll('.repo-grid .repo-tile').length === 1")
        self.assertEqual(page.eval_on_selector_all(".repo-grid .repo-tile", "els => els.map((el) => el.title)"), ["docs/media/demo.webm"])
        page.click(".repo-tile.video")
        page.wait_for_selector(".repo-lightbox video")
        self.assertIn("1 of 1", page.inner_text(".repo-lb-cap"))
        page.keyboard.press("Escape")
        page.click(".repo-kinds button:has-text('Pictures')")
        page.wait_for_function("() => document.querySelectorAll('.repo-grid .repo-tile').length === 3")
        self.assertEqual(page.locator(".repo-grid .repo-tile.video").count(), 0)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

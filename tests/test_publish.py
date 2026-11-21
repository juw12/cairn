"""cairn publish: only notes marked publish: true leave the brain, in one commit, and un-publishing removes them."""

import base64
import json
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cairn import publish
from cairn.cli import main

PUBLIC = "---\ntitle: Built X\nclaim: Built X\nevidence: [https://github.com/me/x]\npublish: true\n---\nSee [[Secret plan]] and [[Other public|the other one]]. ![[diagram.png]]\n"
OTHER = "---\ntitle: Other public\npublish: true\n---\nback to [[Built X]]\n"
PRIVATE = "---\ntitle: Secret plan\n---\nsalary numbers\n"
VETOED = "---\ntitle: Draft\npublish: true\nprivate: true\n---\nnot yet\n"


def make_source(root: Path) -> Path:
    files = {
        "notes/built-x.md": PUBLIC,
        "Other public.md": OTHER,
        "Secret plan.md": PRIVATE,
        "notes/draft.md": VETOED,
        ".obsidian/workspace.md": "---\npublish: true\n---\nignored: hidden folder\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class FakeGitHub:
    """Just enough of the GitHub API for publish_to_github: repos, topics, refs, trees, commits."""

    def __init__(self, exists=True, private=False, admin=True, pages=None):
        self.files = {"README.md": "# brain\n"} if exists else {}
        self.exists = exists
        self.private = private
        self.admin = admin  # False: a collaborator, who may push but not change the repo's settings
        self.pages = pages  # what GET /pages answers; None = GitHub Pages is off
        self.homepage = ""
        self.topics = []
        self.commits = 0
        self.calls = []

    def sha(self, text):
        return publish.git_blob_sha(text)

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if path == "/repos/me/brain" and method == "GET":
            if not self.exists:
                return 404, {"message": "Not Found"}
            return 200, {"default_branch": "main", "private": self.private, "topics": self.topics,
                         "permissions": {"admin": self.admin, "push": True}, "homepage": self.homepage}
        if path == "/repos/me/brain" and method == "PATCH":
            self.homepage = body["homepage"]
            return 200, {}
        if path == "/repos/me/brain/pages" and method == "GET":
            return (200, self.pages) if self.pages else (404, {"message": "Not Found"})
        if path == "/repos/me/brain/pages" and method == "POST":
            if not self.admin:
                return 403, {"message": "Resource not accessible"}
            self.pages = {"html_url": "https://me.github.io/brain/", "source": body["source"], "build_type": "legacy"}
            return 201, self.pages
        if path == "/users/me":
            return 200, {"login": "me", "name": "Ada Lovelace"}
        if path == "/user/repos" and method == "POST":
            self.exists = True
            self.files = {"README.md": "# brain\n"}
            return 201, {"default_branch": "main", "private": body["private"], "topics": []}
        if path == "/repos/me/brain/topics" and method == "PUT":
            self.topics = body["names"]
            return 200, {"names": self.topics}
        if path == "/repos/me/brain/git/ref/heads/main":
            return 200, {"object": {"sha": f"commit{self.commits}"}}
        if path.startswith("/repos/me/brain/git/trees/commit"):
            tree = [{"path": p, "type": "blob", "sha": self.sha(t)} for p, t in self.files.items()]
            return 200, {"sha": f"tree{self.commits}", "tree": tree}
        if path.startswith("/repos/me/brain/git/blobs/"):
            wanted = path.rsplit("/", 1)[1]
            for text in self.files.values():
                if self.sha(text) == wanted:
                    return 200, {"content": base64.b64encode(text.encode()).decode()}
            return 404, {}
        if path == "/repos/me/brain/git/trees" and method == "POST":
            self.pending = dict(self.files)
            for entry in body["tree"]:
                if entry.get("sha", "x") is None:
                    self.pending.pop(entry["path"], None)
                else:
                    self.pending[entry["path"]] = entry["content"]
            return 201, {"sha": "newtree"}
        if path == "/repos/me/brain/git/commits" and method == "POST":
            self.message = body["message"]
            return 201, {"sha": f"commit{self.commits + 1}"}
        if path == "/repos/me/brain/git/refs/heads/main" and method == "PATCH":
            self.files = self.pending
            self.commits += 1
            return 200, {}
        return 404, {"message": f"unexpected {method} {path}"}


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.source = make_source(self.tmp / "vault")

    def test_only_marked_notes_are_selected_and_private_links_become_text(self):
        chosen = publish.unlink_private(publish.select_public(self.source))
        self.assertEqual(sorted(chosen), ["Other public.md", "notes/built-x.md"])
        text = chosen["notes/built-x.md"]
        self.assertIn("See Secret plan and [[Other public|the other one]].", text)
        self.assertNotIn("[[Secret plan]]", text)
        self.assertNotIn("diagram.png", text)
        self.assertIn("[[Built X]]", chosen["Other public.md"])

    def test_plan_writes_changes_and_removes_unpublished(self):
        public = {"a.md": "A\n", "b.md": "B\n"}
        remote = {"brain/a.md": publish.git_blob_sha("A\n"), "brain/old.md": "x", "brain/mine.md": "y"}
        changes = publish.plan(public, remote, previous=["brain/a.md", "brain/old.md"])
        self.assertEqual(list(changes["write"]), ["brain/b.md"])
        self.assertEqual(changes["delete"], ["brain/old.md"])  # never brain/mine.md: we didn't publish it

    def test_publish_to_folder_round_trip(self):
        destination = self.tmp / "public"
        report = publish.publish_to_folder(self.source, destination)
        self.assertEqual(report["write"], ["brain/Other public.md", "brain/notes/built-x.md"])
        self.assertFalse((destination / "brain/Secret plan.md").exists())
        (destination / "brain/hand-written.md").write_text("mine\n", encoding="utf-8")
        again = publish.publish_to_folder(self.source, destination)
        self.assertEqual((again["write"], again["delete"]), ([], []))
        # Un-publish one note: it is removed from the public copy; the hand-written file stays.
        (self.source / "Other public.md").write_text(OTHER.replace("publish: true", "publish: false"), encoding="utf-8")
        third = publish.publish_to_folder(self.source, destination)
        self.assertEqual(third["delete"], ["brain/Other public.md"])
        self.assertFalse((destination / "brain/Other public.md").exists())
        self.assertTrue((destination / "brain/hand-written.md").exists())

    def test_publish_to_github_creates_repo_tags_it_and_commits_once(self):
        github = FakeGitHub(exists=False)
        report = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")
        self.assertTrue(report["created"])
        self.assertEqual(github.commits, 1)
        self.assertEqual(github.topics, ["cairn-brain"])
        self.assertIn("brain/notes/built-x.md", github.files)
        self.assertNotIn("brain/Secret plan.md", github.files)
        self.assertIn("public brain", github.files["README.md"])
        manifest = json.loads(github.files[publish.MANIFEST])
        self.assertEqual(manifest["files"], ["brain/Other public.md", "brain/notes/built-x.md"])
        self.assertEqual(github.message, "cairn: publish 2 public notes (2026-11-07)")
        # Nothing changed: no second commit.
        again = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")
        self.assertIsNone(again["commit"])
        self.assertEqual(github.commits, 1)
        # Un-publish: removed in the next commit.
        (self.source / "notes/built-x.md").write_text(PRIVATE, encoding="utf-8")
        third = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")
        self.assertEqual(third["delete"], ["brain/notes/built-x.md", "docs/notes/built-x.html"])
        self.assertNotIn("brain/notes/built-x.md", github.files)
        self.assertNotIn("docs/notes/built-x.html", github.files)

    def test_the_notes_also_go_online_as_a_web_page_with_search(self):
        github = FakeGitHub(exists=False)
        report = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")
        self.assertEqual(github.commits, 1, "notes and web page travel in the same commit")
        self.assertEqual(report["site"]["url"], "https://me.github.io/brain/")
        self.assertTrue(report["site"]["live"])
        self.assertEqual(github.pages["source"], {"branch": "main", "path": "/docs"})
        self.assertEqual(github.homepage, "https://me.github.io/brain/")
        index = github.files["docs/index.html"]
        self.assertIn("<h1>Ada Lovelace</h1>", index)  # the name on the GitHub profile
        self.assertIn('<link rel="canonical" href="https://me.github.io/brain/">', index)
        self.assertIn('id="search"', index)
        self.assertIn("Built X", github.files["docs/search.js"])
        self.assertIn("<loc>https://me.github.io/brain/notes/built-x.html</loc>", github.files["docs/sitemap.xml"])
        self.assertIn(".nojekyll", json.loads(github.files[publish.SITE_MANIFEST])["files"])
        self.assertIn("https://me.github.io/brain/", github.files["README.md"])
        everything = "\n".join(github.files.values())
        for private in ("salary numbers", "not yet", "hidden folder", "diagram.png"):
            self.assertNotIn(private, everything)
        # Nothing changed: no commit, and Pages is not switched on a second time.
        calls = len(github.calls)
        again = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")
        self.assertIsNone(again["commit"])
        self.assertTrue(again["site"]["live"])
        self.assertNotIn(("POST", "/repos/me/brain/pages"), github.calls[calls:])

    def test_the_report_names_the_pages_that_changed_so_search_engines_can_be_told(self):
        github = FakeGitHub(pages={"html_url": "https://me.github.io/brain/", "source": {"branch": "main", "path": "/docs"}, "build_type": "legacy"})
        first = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")["site"]
        self.assertEqual(
            first["changed"],
            ["https://me.github.io/brain/Other%20public.html", "https://me.github.io/brain/", "https://me.github.io/brain/notes/built-x.html"],
        )
        self.assertEqual(first["feed"], "")  # these notes carry no date, so there is no feed to speak of
        self.assertEqual(first["check"]["url"], "https://me.github.io/brain/Other%20public.html")
        self.assertEqual(first["check"]["sha"], publish.git_blob_sha(github.files["docs/Other public.html"]))
        # One note changes and gets a date: its page, the first page and the feed are named, the other page is not.
        (self.source / "Other public.md").write_text(OTHER.replace("publish: true", "publish: true\ndate: 2026-11-08"), encoding="utf-8")
        second = publish.publish_to_github(self.source, "me/brain", github, "2026-11-08")["site"]
        self.assertEqual(second["changed"], ["https://me.github.io/brain/Other%20public.html", "https://me.github.io/brain/"])
        self.assertEqual(second["feed"], "https://me.github.io/brain/feed.xml")
        self.assertEqual(second["check"], {"url": "https://me.github.io/brain/feed.xml", "sha": publish.git_blob_sha(github.files["docs/feed.xml"])})
        # Nothing changed: nothing to tell.
        third = publish.publish_to_github(self.source, "me/brain", github, "2026-11-08")["site"]
        self.assertNotIn("changed", third)

    def test_a_collaborator_publishes_the_page_and_is_told_how_the_owner_puts_it_online(self):
        github = FakeGitHub(admin=False)
        report = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")
        self.assertIn("docs/index.html", github.files)
        self.assertFalse(report["site"]["live"])
        self.assertIn("Settings > Pages", report["site"]["note"])
        self.assertEqual(github.files["README.md"], "# brain\n", "a README somebody wrote is never replaced")
        # Later the owner publishes: nothing new to commit, but Pages is switched on.
        github.admin = True
        later = publish.publish_to_github(self.source, "me/brain", github, "2026-11-08")
        self.assertIsNone(later["commit"])
        self.assertTrue(later["site"]["live"])
        self.assertEqual(github.pages["source"]["path"], "/docs")

    def test_cairns_own_readme_gets_the_link_once_the_page_can_go_online(self):
        github = FakeGitHub()
        github.files["README.md"] = publish._readme("me")  # written by an older Cairn, before the web page
        publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")
        self.assertIn("Read and search it on the web: https://me.github.io/brain/", github.files["README.md"])

    def test_a_docs_folder_that_holds_something_else_is_left_alone(self):
        github = FakeGitHub()
        github.files["docs/index.html"] = "<h1>my own web app</h1>\n"
        report = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07")
        self.assertEqual(github.files["docs/index.html"], "<h1>my own web app</h1>\n")
        self.assertFalse(any(path.startswith("docs/") for path in report["write"]))
        self.assertFalse(report["site"]["live"])
        self.assertIn("other files in docs/", report["site"]["note"])
        self.assertIsNone(github.pages)
        self.assertIn("brain/notes/built-x.md", github.files, "the notes are still published")

    def test_pages_set_up_another_way_is_reported_and_no_site_leaves_docs_out(self):
        root = FakeGitHub(pages={"html_url": "https://me.example/", "source": {"branch": "main", "path": "/"}, "build_type": "legacy"})
        report = publish.publish_to_github(self.source, "me/brain", root, "2026-11-07")
        self.assertEqual(
            {key: report["site"][key] for key in ("url", "live", "note")},
            {"url": "https://me.example/docs/", "live": True, "note": ""},
        )
        self.assertIn("https://me.example/docs/notes/built-x.html", report["site"]["changed"])
        self.assertIn('href="https://me.example/docs/"', root.files["docs/index.html"])
        workflow = FakeGitHub(pages={"html_url": "https://me.github.io/brain/", "source": {"branch": "main", "path": "/"}, "build_type": "workflow"})
        report = publish.publish_to_github(self.source, "me/brain", workflow, "2026-11-07")
        self.assertFalse(report["site"]["live"])
        self.assertIn("show something else", report["site"]["note"])
        plain = FakeGitHub()
        report = publish.publish_to_github(self.source, "me/brain", plain, "2026-11-07", site=False)
        self.assertNotIn("site", report)
        self.assertFalse(any(path.startswith("docs/") for path in plain.files))
        self.assertNotIn(("GET", "/repos/me/brain/pages"), plain.calls)

    def test_refuses_a_private_target_and_dry_run_writes_nothing(self):
        with self.assertRaises(publish.PublishError):
            publish.publish_to_github(self.source, "me/brain", FakeGitHub(private=True), "2026-11-07")
        github = FakeGitHub(exists=False)
        report = publish.publish_to_github(self.source, "me/brain", github, "2026-11-07", dry_run=True)
        self.assertTrue(report["created"])
        self.assertFalse(any(method != "GET" for method, _ in github.calls))

    def test_cli_publish_to_folder(self):
        destination = self.tmp / "out"
        code = main(["publish", "--source", str(self.source), "--folder", str(destination)])
        self.assertEqual(code, 0)
        self.assertTrue((destination / "brain/notes/built-x.md").exists())
        self.assertEqual(main(["--root", str(self.tmp), "publish"]), 2)  # no brain/ folder and no --to


class CertificateTests(unittest.TestCase):
    """The Python from python.org on a Mac has no certificates until a script is run; GitHub must still be reachable."""

    def paths(self, cafile=None, capath=None):
        return ssl.DefaultVerifyPaths(cafile, capath, "SSL_CERT_FILE", "/missing/cert.pem", "SSL_CERT_DIR", "/missing/certs")

    def test_a_mac_without_certificates_uses_the_systems_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "cert.pem"
            bundle.write_text("", encoding="utf-8")
            with mock.patch.object(publish.sys, "platform", "darwin"), mock.patch.object(publish, "MAC_CERTS", str(bundle)), \
                    mock.patch.object(ssl, "get_default_verify_paths", return_value=self.paths()), \
                    mock.patch.object(ssl, "create_default_context", return_value="system roots") as create:
                self.assertEqual(publish.ssl_context(), "system roots")
            create.assert_called_once_with(cafile=str(bundle))

    def test_pythons_own_certificates_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "cert.pem"
            bundle.write_text("", encoding="utf-8")
            (Path(tmp) / "certs").mkdir()
            (Path(tmp) / "certs" / "root.0").write_text("", encoding="utf-8")
            cases = {
                "another system": ("linux", self.paths(), str(bundle)),
                "a certificate file": ("darwin", self.paths(cafile=str(bundle)), str(bundle)),
                "a certificate folder": ("darwin", self.paths(capath=str(Path(tmp) / "certs")), str(bundle)),
                "no system bundle either": ("darwin", self.paths(), str(Path(tmp) / "gone.pem")),
            }
            for name, (platform, paths, system_bundle) in cases.items():
                with self.subTest(name), mock.patch.object(publish.sys, "platform", platform), mock.patch.object(publish, "MAC_CERTS", system_bundle), \
                        mock.patch.object(ssl, "get_default_verify_paths", return_value=paths):
                    self.assertIsNone(publish.ssl_context())


if __name__ == "__main__":
    unittest.main()

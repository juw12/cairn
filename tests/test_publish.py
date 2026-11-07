"""cairn publish: only notes marked publish: true leave the brain, in one commit, and un-publishing removes them."""

import base64
import json
import tempfile
import unittest
from pathlib import Path

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

    def __init__(self, exists=True, private=False):
        self.files = {"README.md": "# brain\n"} if exists else {}
        self.exists = exists
        self.private = private
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
            return 200, {"default_branch": "main", "private": self.private, "topics": self.topics}
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
        self.assertEqual(third["delete"], ["brain/notes/built-x.md"])
        self.assertNotIn("brain/notes/built-x.md", github.files)

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


if __name__ == "__main__":
    unittest.main()

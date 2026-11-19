"""The agent skills in skills/ must be valid and must agree with the code they describe."""
import re
import unittest
from pathlib import Path

from cairn import core, mcp

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
EXPECTED = ("cairn-cli", "cairn-databases", "cairn-mcp", "cairn-notes", "cairn-vibe")


def read_skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def front_matter(text: str) -> dict:
    match = FRONT_MATTER.match(text)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


class SkillFilesTest(unittest.TestCase):
    def test_every_expected_skill_exists(self):
        found = sorted(path.parent.name for path in SKILLS.glob("*/SKILL.md"))
        self.assertEqual(found, sorted(EXPECTED))

    def test_no_markdown_at_the_folder_root(self):
        # OpenCode reads a Markdown file at the root of a skills folder as a skill of its own.
        self.assertEqual([path.name for path in SKILLS.glob("*.md")], [])

    def test_front_matter_names_the_folder_and_describes_when_to_use_it(self):
        for name in EXPECTED:
            with self.subTest(skill=name):
                fields = front_matter(read_skill(name))
                self.assertEqual(fields.get("name"), name)
                description = fields.get("description", "")
                self.assertTrue(20 <= len(description) <= 1024, len(description))
                self.assertIn("Use when", description)

    def test_links_to_reference_files_resolve(self):
        for name in EXPECTED:
            text = read_skill(name)
            for target in re.findall(r"\]\((references/[^)#]+)\)", text):
                with self.subTest(skill=name, target=target):
                    self.assertTrue((SKILLS / name / target).is_file())


class SkillsMatchTheCodeTest(unittest.TestCase):
    def test_notes_skill_lists_every_type_folder_and_status(self):
        text = read_skill("cairn-notes")
        for note_type in core.TYPES:
            with self.subTest(note_type=note_type):
                self.assertIn(f"`{note_type}`", text)
                self.assertIn(f"`{core.FOLDERS[note_type]}/`", text)
                for status in core.STATUSES[note_type]:
                    self.assertIn(f"`{status}`", text)

    def test_notes_skill_lists_every_capture_prefix_and_special_file(self):
        text = read_skill("cairn-notes")
        for prefix in core.CAPTURE_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertIn(f"`{prefix}:`", text)
        for name in core.SPECIAL_FILES:
            with self.subTest(special_file=name):
                self.assertIn(f"`{name}`", text)

    def test_notes_skill_default_status_is_the_first_one(self):
        text = read_skill("cairn-notes")
        for note_type, statuses in core.STATUSES.items():
            if statuses:
                with self.subTest(note_type=note_type):
                    row = next(line for line in text.splitlines() if line.startswith(f"| `{note_type}` |"))
                    self.assertTrue(row.split("|")[3].strip().startswith(f"`{statuses[0]}`"), row)

    def test_mcp_skill_names_every_tool_and_no_tool_that_is_gone(self):
        text = read_skill("cairn-mcp")
        real = {tool["name"] for tool in mcp.TOOLS}
        for name in real:
            with self.subTest(tool=name):
                self.assertIn(f"| `{name}` |", text)
        documented = set(re.findall(r"^\| `([a-z_]+)` \|", text, re.M))
        self.assertEqual(documented - real, set())

    def test_mcp_skill_marks_required_arguments(self):
        text = read_skill("cairn-mcp")
        for tool in mcp.TOOLS:
            row = next(line for line in text.splitlines() if line.startswith(f"| `{tool['name']}` |"))
            for argument in tool.get("inputSchema", {}).get("required", []):
                with self.subTest(tool=tool["name"], argument=argument):
                    self.assertIn(f"`{argument}` (required)", row)

    def test_vibe_skill_lists_every_log_kind_and_result(self):
        text = read_skill("cairn-vibe")
        for kind in core.LOG_KINDS:
            with self.subTest(kind=kind):
                self.assertRegex(text, rf"`{kind}`|\b{kind}:")
        for result in core.FINISHED:
            with self.subTest(result=result):
                self.assertIn(f"`{result}`", text)

    def test_databases_skill_lists_every_view_and_key_the_app_reads(self):
        text = read_skill("cairn-databases")
        source = (ROOT / "docs" / "database.js").read_text(encoding="utf-8")
        views = re.search(r"export const VIEWS = \[(.*?)\]", source).group(1)
        for view in re.findall(r'"([a-z]+)"', views):
            with self.subTest(view=view):
                self.assertIn(f"`{view}`", text)
        spec = re.search(r"const spec = \{(.*?)\};", source).group(1)
        for key in re.findall(r"(\w+):", spec):
            with self.subTest(key=key):
                self.assertIn(f"| `{key}` |", text)


if __name__ == "__main__":
    unittest.main()

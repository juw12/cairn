"""Write CHANGELOG.md from docs/versions.json, the one place the version history is kept.

    python scripts/make_changelog.py            # rewrite CHANGELOG.md
    python scripts/make_changelog.py --check    # exit 1 when CHANGELOG.md is out of date
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "versions.json"
TARGET = ROOT / "CHANGELOG.md"
PULLS = "https://github.com/juw12/cairn/pull/"


def changelog(data: dict) -> str:
    lines = [
        "# Changelog",
        "",
        "Every build of Cairn, newest first. Written from `docs/versions.json` by `scripts/make_changelog.py`;",
        "the app shows the same list under Version history.",
    ]
    for release in data["releases"]:
        lines += ["", f"## Build {release['build']} ({release['date']}): {release['title']}", ""]
        for change in release["changes"]:
            lines.append("- " + re.sub(r"#(\d+)", lambda found: f"[#{found[1]}]({PULLS}{found[1]})", change))
    return "\n".join(lines) + "\n"


def main(arguments: list[str]) -> int:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    text = changelog(data)
    if "--check" in arguments:
        current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
        if current != text:
            print("CHANGELOG.md is out of date: run python scripts/make_changelog.py")
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {TARGET.name}: {len(data['releases'])} builds")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

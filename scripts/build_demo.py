"""Rebuild docs/demo.js from tests/fixtures/demo_brain.json (the single source of the sample brain)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
fixture = json.loads((ROOT / "tests" / "fixtures" / "demo_brain.json").read_text(encoding="utf-8"))
files = [{"path": path, "text": text} for path, text in fixture["files"].items()]
# Sample canvases (JSON Canvas, as Obsidian writes them) live next to the fixture as real .canvas files.
for canvas in sorted((ROOT / "tests" / "fixtures" / "demo_canvases").glob("*.canvas")):
    files.append({"path": f"canvases/{canvas.name}", "text": canvas.read_text(encoding="utf-8")})
output = (
    "// Sample brain for the demo: an offline hiking-route app called Trailmix.\n"
    "// Generated from tests/fixtures/demo_brain.json and tests/fixtures/demo_canvases/ by scripts/build_demo.py; edit those, not this file.\n\n"
    f"export const DEMO_FILES = {json.dumps(files, indent=2, ensure_ascii=False)};\n\n"
    f"export const DEMO_TODOS = {json.dumps(fixture['todos'], indent=2)};\n"
)
(ROOT / "docs" / "demo.js").write_text(output, encoding="utf-8")
print("wrote docs/demo.js")

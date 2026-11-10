"""Founder Studio page: the embedded studio is wired into the Cairn shell."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


class FounderStudioTests(unittest.TestCase):
    def test_founder_view_is_registered(self):
        assert re.search(r'id: "founder", label: "Founder Studio"', read("state.js"))
        app = read("app.js")
        assert 'from "./founder.js"' in app
        assert "founder: founderView" in app

    def test_founder_bridge_is_cached_by_the_service_worker(self):
        assert '"founder.js"' in read("sw.js")

    def test_embedded_studio_runs_in_browser_mode(self):
        index = read("founder/index.html")
        assert 'data-backend="browser"' in index
        assert "data-pyodide=" in index
        assert not (DOCS / "founder" / "pyodide").exists(), "Pyodide loads from the founder-studio site, not a copy"

    def test_results_only_cross_same_origin(self):
        assert "event.origin !== location.origin" in read("founder.js")
        assert "location.origin" in read("founder/js/app.js")

    def test_steps_follow_studio_events(self):
        studio = read("founder/js/app.js")
        host = read("founder.js")
        for event in ("founder-studio:project", "founder-studio:run-start", "founder-studio:run-stopped"):
            assert event in studio, f"studio never sends {event}"
            assert event in host, f"Cairn ignores {event}"
        assert "founder-studio:edit" in studio and "founder-studio:edit" in host
        assert "event.source !== window.parent" in studio, "the studio only takes edit requests from its host"

    def test_saved_panels_and_next_step_on_the_page(self):
        host = read("founder.js")
        assert 'id="founder-saved"' in host, "past panels are listed on the page"
        assert 'includes("founder-studio")' in host, "the list reads notes tagged founder-studio"
        assert "data-open=" in host, "each saved panel opens its note"
        assert "function nextStep" in host and "founder-next" in host, "a finished panel points at the next thing to test"
        for reason in ("price", "habit", "need", "trust", "quality", "convenience", "timing", "values"):
            assert f"  {reason}:" in host, f"no next step for buyers who pass on {reason}"

    def test_embedded_studio_drops_its_own_brand(self):
        studio = read("founder/js/app.js")
        assert 'classList.add("embedded")' in studio
        assert ".embedded .brand { display: none; }" in read("founder/styles.css")
        assert "Quick sim is a rule model, not evidence" in studio, "the honesty note stays when embedded"

    def test_embedded_studio_takes_cairns_theme_before_it_first_paints(self):
        head = read("founder/index.html").split("</head>")[0]
        assert 'root.classList.add("embedded")' in head, "the studio knows it is embedded before it first paints"
        assert "window.parent.document.documentElement" in head, "the theme is read from Cairn's page"
        assert "MutationObserver" in head, "a theme change in Cairn reaches the studio"

    def test_studio_dark_theme_leaves_the_3d_square_in_daylight(self):
        css = read("founder/styles.css")
        marker = ':root[data-theme="dark"] body {'
        assert css.count(marker) == 1, "one dark block, set on body"
        dark = css.split(marker)[1].split("}")[0]
        for token in ("--bg", "--panel", "--line", "--text", "--muted", "--ink", "--accent", "--buy", "--pass", "--think"):
            assert f"{token}:" in dark, f"the dark theme has no value for {token}"
        assert "--scene-" not in dark, "the 3D square keeps its daylight colours in both themes"
        assert "getComputedStyle(document.documentElement)" in read("founder/js/arena.js"), (
            "the 3D square reads its colours from the root, which the dark theme leaves alone"
        )


if __name__ == "__main__":
    unittest.main()

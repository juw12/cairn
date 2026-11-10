"""Founder Studio page: the embedded studio is wired into the Cairn shell."""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def test_founder_view_is_registered():
    assert re.search(r'id: "founder", label: "Founder Studio"', read("state.js"))
    app = read("app.js")
    assert 'from "./founder.js"' in app
    assert "founder: founderView" in app


def test_founder_bridge_is_cached_by_the_service_worker():
    assert '"founder.js"' in read("sw.js")


def test_embedded_studio_runs_in_browser_mode():
    index = read("founder/index.html")
    assert 'data-backend="browser"' in index
    assert "data-pyodide=" in index
    assert not (DOCS / "founder" / "pyodide").exists(), "Pyodide loads from the founder-studio site, not a copy"


def test_results_only_cross_same_origin():
    assert "event.origin !== location.origin" in read("founder.js")
    assert "location.origin" in read("founder/js/app.js")


def test_steps_follow_studio_events():
    studio = read("founder/js/app.js")
    host = read("founder.js")
    for event in ("founder-studio:project", "founder-studio:run-start", "founder-studio:run-stopped"):
        assert event in studio, f"studio never sends {event}"
        assert event in host, f"Cairn ignores {event}"
    assert "founder-studio:edit" in studio and "founder-studio:edit" in host
    assert "event.source !== window.parent" in studio, "the studio only takes edit requests from its host"


def test_saved_panels_and_next_step_on_the_page():
    host = read("founder.js")
    assert 'id="founder-saved"' in host, "past panels are listed on the page"
    assert 'includes("founder-studio")' in host, "the list reads notes tagged founder-studio"
    assert "data-open=" in host, "each saved panel opens its note"
    assert "function nextStep" in host and "founder-next" in host, "a finished panel points at the next thing to test"
    for reason in ("price", "habit", "need", "trust", "quality", "convenience", "timing", "values"):
        assert f"  {reason}:" in host, f"no next step for buyers who pass on {reason}"


def test_embedded_studio_drops_its_own_brand():
    studio = read("founder/js/app.js")
    assert 'classList.add("embedded")' in studio
    assert ".embedded .brand { display: none; }" in read("founder/styles.css")
    assert "Quick sim is a rule model, not evidence" in studio, "the honesty note stays when embedded"

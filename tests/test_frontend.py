"""The front end ships in the repository and needs no CDN at runtime."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_frontend_assets_exist_and_are_local():
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    for asset in ("/assets/app.js", "/assets/style.css"):
        assert asset in index
    assert "cdn" not in index.lower(), "the front end must not depend on a CDN"
    app = (ROOT / "frontend" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "/vendor/three.module.js" in app
    assert (ROOT / "frontend" / "vendor" / "three.module.js").exists()
    assert (ROOT / "frontend" / "vendor" / "OrbitControls.js").exists()


def test_every_view_has_a_section_and_a_route():
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    for view in ("home", "explorer", "graph", "gaps", "papers", "agent", "report"):
        assert f'id="view-{view}"' in index, view

def test_every_element_the_script_reaches_for_exists_in_the_markup():
    """A selector typo is a silent no-op in vanilla JS — so check the ids directly."""
    import re

    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "assets" / "app.js").read_text(encoding="utf-8")
    # An id counts as declared when the markup carries it *or* the script injects it
    # into the DOM itself (several panels are rendered from template literals).
    declared = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', index))
    injected = set(re.findall(r'id=\\?"([a-zA-Z0-9_-]+)\\?"', app))
    wanted = set(re.findall(r'\$\("#([a-zA-Z0-9_-]+)"\)', app))
    missing = sorted(wanted - declared - injected)
    assert not missing, f"app.js targets elements nothing declares: {missing}"


def test_the_planner_card_and_engine_strip_are_present():
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "assets" / "app.js").read_text(encoding="utf-8")
    for element in ("plan-dsl", "plan-run", "plan-cypher", "plan-engine", "home-services"):
        assert f'id="{element}"' in index, element
    assert 'api("/api/plan"' in app
    assert 'api("/api/services")' in app
    assert "/vendor/three.module.js" in app


def test_the_markup_is_balanced():
    """Hand-written HTML: an unclosed <div> silently breaks the layout, so check it."""
    from html.parser import HTMLParser

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
            "param", "source", "track", "wbr"}

    class Balanced(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, int]] = []
            self.problems: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag not in VOID:
                self.stack.append((tag, self.getpos()[0]))

        def handle_endtag(self, tag):
            if tag in VOID:
                return
            if not self.stack:
                self.problems.append(f"</{tag}> on line {self.getpos()[0]} closes nothing")
                return
            open_tag, line = self.stack.pop()
            if open_tag != tag:
                self.problems.append(f"<{open_tag}> (line {line}) closed by </{tag}> on line {self.getpos()[0]}")

    parser = Balanced()
    parser.feed((ROOT / "frontend" / "index.html").read_text(encoding="utf-8"))
    parser.close()
    leftover = [f"<{tag}> on line {line}" for tag, line in parser.stack]
    assert not parser.problems, parser.problems
    assert not leftover, f"unclosed elements: {leftover}"

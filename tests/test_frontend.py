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

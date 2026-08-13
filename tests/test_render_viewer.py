"""Tests for lvkit.render.render_viewer — the single-VI render viewer builder
and the ``render --format html`` CLI path (feat/vi-diff-action).

Mirrors ``tests/test_diff_viewer.py``: a pure-unit test with a stub SVG (no
sample VI needed) plus an end-to-end test over ``.tmp/wd_base.vi`` (skip if
absent). Also guards the determinism contract: the default ``--format svg``
output stays byte-identical to the legacy light render.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.render import render_vi
from lvkit.render.render_viewer import build_render_viewer
from lvkit.render.theme_control import THEME_CONTROL_BTN_ID

WD_BASE_VI = Path(__file__).resolve().parent.parent / ".tmp" / "wd_base.vi"


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), mode=LoadMode.NONE, layout=True)
    return graph, graph.resolve_vi_name(vi_path.name)


def _require_vi() -> None:
    if not WD_BASE_VI.exists():
        pytest.skip(f"sample VI not available: {WD_BASE_VI}")


class TestBuildRenderViewerPureUnit:
    """A stub SVG string, no sample VI needed — proves the substitution logic
    (embed, toolbar controls, doctype prefix) in isolation."""

    def test_embeds_svg_zoom_and_theme_controls(self):
        svg = "<svg id='lv-stub'>DIAGRAM-MARKER</svg>"
        html = build_render_viewer(svg, title="Stub VI")

        assert html.startswith("<!doctype html>")
        assert "DIAGRAM-MARKER" in html
        assert "Stub VI" in html
        # zoom group present
        assert 'id="zoomOut"' in html
        assert 'id="zoomFit"' in html
        assert 'id="zoomIn"' in html
        # the shared theme-control button + its script
        assert f'id="{THEME_CONTROL_BTN_ID}"' in html
        assert "lvkitDiagramTheme" in html
        # every placeholder fully substituted
        for marker in ("__TITLE__", "__SVG__", "__THEME_BTN__", "__THEME_SCRIPT__"):
            assert marker not in html


class TestBuildRenderViewerEndToEnd:
    def test_html_embeds_auto_svg_and_controls(self):
        _require_vi()
        graph, vi = _load(WD_BASE_VI)
        svg = render_vi(graph, vi, theme_mode="auto")
        assert svg is not None

        html = build_render_viewer(svg, title=WD_BASE_VI.stem)
        assert html.startswith("<!doctype html>")
        assert svg in html
        # the embedded SVG is the runtime-themable "auto" one
        assert ':root[data-theme="dark"]' in html
        assert f'id="{THEME_CONTROL_BTN_ID}"' in html
        assert 'id="zoomFit"' in html
        # DESIGN LAW: the toggle themes the DIAGRAM SURFACE — its --canvas
        # backdrop and the in-view panels drawn over it (the --d* properties-
        # popover tokens) — but NEVER the window-chrome palette (--bg/--panel).
        assert '[data-theme="dark"]{--bg' not in html
        assert ':root[data-theme="dark"]{--canvas:#1b1c1e}' in html
        assert ':root[data-theme="dark"]{--dpanel' in html

    def test_default_svg_render_is_byte_identical_to_light(self):
        """The ``--format svg`` (default) path renders with ``theme_mode='light'``
        and must stay byte-identical to the legacy default render — the
        determinism contract the html viewer must never disturb."""
        _require_vi()
        graph, vi = _load(WD_BASE_VI)
        default_svg = render_vi(graph, vi)
        light_svg = render_vi(graph, vi, theme_mode="light")
        assert default_svg is not None
        assert default_svg == light_svg
        assert "var(--lv-" not in light_svg
        assert "data-theme" not in light_svg

"""Tests for task #19 — VI properties + structure surfaced in the render
viewer, SVG-embedded (chrome panel + status chips).

Mirrors ``tests/test_render_viewer.py``'s patterns: a pure-unit test with a
stub SVG (no sample VI needed) proving the chip/panel markup + JS get wired
into ``build_render_viewer``'s output, plus an end-to-end test over the
JKI-VI-Tester corpus's ``VITester_Item_Init.vi`` (password-protected +
reentrant — the exact "notable properties" sample task #19 calls for) proving
the root ``<svg>`` actually carries the expected ``data-lv-properties``/
``data-lv-structure`` JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.render import render_vi
from lvkit.render.render_viewer import build_render_viewer

# Both copies in the JKI-VI-Tester sample corpus are password-protected +
# reentrant (same VI, project-linked twice) — pick whichever is present.
_VITESTER_ITEM_INIT_CANDIDATES = [
    Path(
        ".lvkit/cache/samples/JKI-VI-Tester/source/Built Project Integration"
        "/VITester_Item_Init.vi"
    ),
    Path(
        ".lvkit/cache/samples/JKI-VI-Tester/source/LabVIEW Project Plugin"
        "/VITester_Item_Init.vi"
    ),
]


def _find_vitester_item_init() -> Path | None:
    for p in _VITESTER_ITEM_INIT_CANDIDATES:
        if p.exists():
            return p
    return None


def _require_vitester_item_init() -> Path:
    vi = _find_vitester_item_init()
    if vi is None:
        pytest.skip("JKI-VI-Tester sample corpus not available")
    return vi


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), mode=LoadMode.NONE, layout=True)
    return graph, graph.resolve_vi_name(vi_path.name)


class TestSvgCarriesPropertiesData:
    """Part A: the root <svg> is the single carrier for VIProperties/
    VIStructure — both the viewer chrome AND a host that only sees the raw
    SVG (e.g. the VS Code extension) read from it."""

    def test_password_protected_reentrant_vi_embeds_expected_json(self):
        vi_path = _require_vitester_item_init()
        graph, vi = _load(vi_path)
        svg = render_vi(graph, vi)
        assert svg is not None

        assert "data-lv-properties=" in svg
        assert "data-lv-structure=" in svg

        # Pull the JSON back out of the attribute and parse it for real,
        # rather than substring-matching — proves it's valid, well-formed
        # JSON, not just a string that happens to contain the right text.
        props_json = _extract_attr(svg, "data-lv-properties")
        struct_json = _extract_attr(svg, "data-lv-structure")
        props = json.loads(props_json)
        struct = json.loads(struct_json)

        assert props["lock_state"] == "password_protected"
        assert props["execution"]["reentrant"] is True
        assert struct["is_broken"] is False
        assert struct["is_typedef"] is False

    def test_unloaded_vi_gets_all_default_properties_never_raises(self):
        """A VI the graph doesn't know about (or hasn't parsed the LVSR block
        for) degrades to the all-defaults VIContext — render must never raise
        just because properties/structure are unavailable."""
        vi_path = _require_vitester_item_init()
        graph, vi = _load(vi_path)
        svg = render_vi(graph, vi)
        assert svg is not None
        # Sanity: still valid JSON even for the common (mostly-default) case.
        json.loads(_extract_attr(svg, "data-lv-properties"))
        json.loads(_extract_attr(svg, "data-lv-structure"))


def _extract_attr(svg: str, attr: str) -> str:
    """Pull a single-quoted ``attr='...'`` value out of the root <svg> tag."""
    head = svg.split(">", 1)[0]
    marker = f"{attr}='"
    start = head.index(marker) + len(marker)
    end = head.index("'", start)
    return head[start:end]


class TestBuildRenderViewerPropertiesChrome:
    """Part B: chips + collapsible panel are wired into build_render_viewer's
    output — pure unit test, no sample VI needed (stub SVG mirrors
    test_render_viewer.py's TestBuildRenderViewerPureUnit)."""

    def _html(self) -> str:
        svg = (
            "<svg id='lv-stub' data-lv-properties='{\"lock_state\":"
            "\"password_protected\"}' data-lv-structure='{\"is_broken\":"
            "false}'>DIAGRAM-MARKER</svg>"
        )
        return build_render_viewer(svg, title="Stub VI")

    def test_chip_container_and_toggle_button_present(self):
        html = self._html()
        assert 'id="lvkitChips"' in html
        assert 'id="lvkitPropsBtn"' in html
        assert 'id="lvkitPropsPanel"' in html
        # Chrome, plain-text button — no NI-derived glyph.
        assert ">Properties<" in html

    def test_script_reads_dataset_not_re_deriving_state(self):
        """The chip/panel script must read straight off the embedded SVG's
        dataset (the single source of truth) — never recompute state some
        other way."""
        html = self._html()
        assert "dataset.lvProperties" in html
        assert "dataset.lvStructure" in html
        # The four high-signal chip conditions from the spec.
        assert '"locked"' in html
        assert '"password_protected"' in html
        assert "is_broken" in html
        assert "reentrant" in html
        assert "is_typedef" in html

    def test_panel_collapsed_by_default(self):
        html = self._html()
        assert '<div class="lvkit-props-panel" id="lvkitPropsPanel" hidden>' in html

    def test_chrome_css_follows_system_theme_not_data_theme(self):
        """DESIGN LAW: chips/panel are chrome — plain
        @media(prefers-color-scheme) tokens only, NEVER a rule keyed off
        [data-theme] (that toggle is diagram-only)."""
        html = self._html()
        assert ".lvkit-chip{" in html
        assert ".lvkit-props-panel{" in html
        # No properties-chrome selector reacts to data-theme.
        start = html.index(".lvkit-chips{")
        end = html.index("</style>", start)
        chrome_css = html[start:end]
        assert "data-theme" not in chrome_css

    def test_every_placeholder_substituted(self):
        html = self._html()
        for marker in ("__PROPERTIES_BTN__", "__PROPERTIES_PANEL__"):
            assert marker not in html


class TestBuildRenderViewerPropertiesEndToEnd:
    def test_real_password_protected_vi_html_carries_data_attrs(self):
        vi_path = _require_vitester_item_init()
        graph, vi = _load(vi_path)
        svg = render_vi(graph, vi, theme_mode="auto")
        assert svg is not None

        html = build_render_viewer(svg, title=vi)
        assert "password_protected" in html
        assert 'id="lvkitChips"' in html
        assert 'id="lvkitPropsBtn"' in html

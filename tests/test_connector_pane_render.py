"""Render tests for the connector-pane SVG (render/connector_pane.py)."""

from __future__ import annotations

from lvkit.models import FPTerminal, LVType
from lvkit.render.connector_pane import (
    PaneTerminal,
    pane_terminals,
    render_connector_pane,
    render_connector_pane_diff,
    render_connector_pane_help,
)
from lvkit.render.style import DEFAULT_THEME


def _err() -> LVType:
    return LVType(
        kind="cluster", underlying_type="Cluster", typedef_name="error in.ctl"
    )


def _refs_and_errors() -> list[PaneTerminal]:
    # The classic 4-2-2-4 error-cluster VI: error in/out (idx 8/0), refs (11/3).
    dbl = LVType(kind="primitive", underlying_type="Numeric")
    return [
        PaneTerminal(0, "error out", _err(), is_output=True, wiring_rule=2),
        PaneTerminal(3, "reference out", dbl, is_output=True, wiring_rule=2),
        PaneTerminal(8, "error in", _err(), is_output=False, wiring_rule=3),
        PaneTerminal(11, "reference in", dbl, is_output=False, wiring_rule=1),
    ]


def test_renders_svg_with_names_and_types():
    svg = render_connector_pane(4815, _refs_and_errors())
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "error in" in svg and "error out" in svg
    assert "reference in" in svg
    assert "Error" in svg  # the error cluster's type label


def test_error_cells_use_the_error_wire_color():
    svg = render_connector_pane(4815, _refs_and_errors())
    assert DEFAULT_THEME.wire_error in svg  # mustard, from the error cluster


def test_empty_slots_are_dashed():
    # 12-cell pattern, only 4 wired -> 8 empty cells, drawn dashed.
    svg = render_connector_pane(4815, _refs_and_errors())
    assert "stroke-dasharray" in svg


def test_ring_adds_highlight_stroke():
    plain = render_connector_pane(4815, _refs_and_errors())
    ringed = render_connector_pane(4815, _refs_and_errors(), ring=frozenset({0}))
    assert ringed.count(DEFAULT_THEME.coercion_dot) > plain.count(
        DEFAULT_THEME.coercion_dot
    )


def test_unknown_pattern_falls_back_but_still_renders_terminals():
    svg = render_connector_pane(999999, _refs_and_errors())
    assert svg.startswith("<svg")
    assert "error in" in svg
    assert "no pattern geometry" in svg


def test_no_pattern_id_falls_back():
    svg = render_connector_pane(None, _refs_and_errors())
    assert svg.startswith("<svg")
    assert "error in" in svg


def test_pane_terminals_adapter_maps_fields():
    ts = [
        FPTerminal(id="a", index=0, direction="output", name="error out",
                   wiring_rule=2, is_indicator=True),
        FPTerminal(id="b", index=8, direction="input", name="error in",
                   wiring_rule=3, is_indicator=False),
    ]
    pts = pane_terminals(ts)
    by_idx = {p.index: p for p in pts}
    assert by_idx[0].is_output is True and by_idx[0].wiring_rule == 2
    assert by_idx[8].is_output is False and by_idx[8].wiring_rule == 3
    assert by_idx[0].name == "error out"


def test_diff_rings_only_changed_terminals():
    dbl = LVType(kind="primitive", underlying_type="Numeric")
    before = [
        PaneTerminal(8, "error in", _err(), is_output=False),
        PaneTerminal(0, "error out", _err(), is_output=True),
        PaneTerminal(11, "removed me", dbl, is_output=False),
    ]
    after = [
        PaneTerminal(8, "error in", _err(), is_output=False),
        PaneTerminal(0, "error out", _err(), is_output=True),
        PaneTerminal(11, "added me", dbl, is_output=False),
    ]
    svg = render_connector_pane_diff(4815, before, 4815, after)
    assert "before" in svg and "after" in svg
    # exactly two rings: one on each side's changed terminal (idx 11)
    assert svg.count(DEFAULT_THEME.coercion_dot) == 2


def test_diff_type_change_rings_the_matched_terminal():
    before = [PaneTerminal(0, "x", LVType(kind="primitive",
              underlying_type="Boolean"), is_output=True)]
    after = [PaneTerminal(0, "x", LVType(kind="primitive",
             underlying_type="Numeric"), is_output=True)]
    svg = render_connector_pane_diff(4800, before, 4800, after)
    # a retype of the same-named terminal rings it on both sides
    assert svg.count(DEFAULT_THEME.coercion_dot) == 2


def test_diff_identical_panes_have_no_rings():
    terms = _refs_and_errors()
    svg = render_connector_pane_diff(4815, terms, 4815, list(terms))
    assert svg.count(DEFAULT_THEME.coercion_dot) == 0


def test_svg_escapes_special_chars_in_names():
    svg = render_connector_pane(
        4801, [PaneTerminal(0, "a<b>&c", None, is_output=True)]
    )
    assert "<b>" not in svg.replace("<svg", "")  # the name's <b> is escaped
    assert "&amp;c" in svg


def test_help_panel_emits_terminal_identity_handles():
    """Each terminal in the Context-Help panel wraps in a <g class="lv-pane-term"
    data-pane-term="{direction}:{name}"> whose value MATCHES the diff engine's
    connector-pane change uid suffix ("connector_pane:{direction}:{name}", see
    graph/diff.py::_diff_connector_pane) -- the join the diff viewer uses to ring
    + number a changed terminal. A quote in the name (a real default like ("""
    '""' """)) is attribute-escaped so it can't break the handle."""
    string_t = LVType(kind="primitive", underlying_type="String")
    terms = [
        PaneTerminal(8, "error in", _err(), is_output=False),
        PaneTerminal(0, "error out", _err(), is_output=True),
        PaneTerminal(11, 'msg ("")', string_t, is_output=False),
    ]
    svg = render_connector_pane_help(4815, terms, title="X")
    assert 'class="lv-pane-term"' in svg
    assert 'data-pane-term="input:error in"' in svg
    assert 'data-pane-term="output:error out"' in svg
    # the quote in the name escapes (matches the JSON-decoded change uid in-browser)
    assert 'data-pane-term="input:msg (&quot;&quot;)"' in svg

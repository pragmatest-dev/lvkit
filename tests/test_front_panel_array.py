"""Front-panel array control: drawn from the heap's own parts.

Hermetic -- synthetic parts shaped like a real array control's ``partsList``
(frame ``partID`` 9, index bezel 30 / index 8002, viewport 28, scrollbar 39,
caption 16), plus the numeric format spec and the parser's capture of hidden
flags, sub-parts, saved element values and format strings.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from lvkit.parser.models import ParsedFPControl, ParsedFPPart, ParsedFrontPanel
from lvkit.parser.vi import _parse_ddo
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls import resolve_glyph, value_bounds
from lvkit.render.front_panel.controls.number_format import format_number
from lvkit.render.style import DEFAULT_THEME

# (top, left, bottom, right), control-local. A 123x265 control whose caption
# strip (16-17px) and index bezel sit above the content: the value box is the
# union of the non-label parts, so it starts at y=16 and is 115 wide.
_HIDDEN = {"objFlags": "395578"}
_SHOWN = {"objFlags": "395570"}


def _index_part(props: dict[str, str]) -> ParsedFPPart:
    sub = [
        ParsedFPPart(10, "numLabel", (9, 27, 26, 48)),
        ParsedFPPart(2, "bigMultiCosm", (6, 7, 17, 22)),
        ParsedFPPart(3, "bigMultiCosm", (17, 7, 28, 22)),
    ]
    return ParsedFPPart(8002, "stdNum", (20, 2, 57, 62), props, sub)


def _array(
    index_props: dict[str, str],
    scrollbar_props: dict[str, str] | None = None,
    **kw,
) -> ParsedFPControl:
    parts = [
        ParsedFPPart(16, "label", (0, 0, 17, 123)),
        _index_part(index_props),
        ParsedFPPart(30, "cosm", (16, 0, 59, 65)),
        ParsedFPPart(28, "cosm", (25, 66, 255, 88 + 66)),
        ParsedFPPart(39, "cosm", (25, 154, 255, 171), scrollbar_props or {}),
        ParsedFPPart(9, "cosm", (18, 59, 265, 174)),
        ParsedFPPart(None, "stdNum", (25, 66, 48, 154), {"typeDesc": "TypeID(39)"}),
    ]
    return ParsedFPControl(
        uid="7",
        name="Steps",
        control_type="indArr",
        bounds=(0, 0, 265, 174),
        parts=parts,
        **kw,
    )


def _svg(ctrl: ParsedFPControl) -> str:
    fp = ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 400, 600))
    return render_front_panel_svg(fp)


def test_value_box_is_union_of_non_label_parts() -> None:
    """The array's drawn box excludes the caption strip: it starts at the top of
    the first non-label part (the index bezel, y=16) -- the same box a cluster
    hands a nested field -- and a top-level array is placed into it."""
    glyph = resolve_glyph(_array(_HIDDEN), DEFAULT_THEME)
    assert glyph.native_size == (174.0, 249.0)  # type: ignore[attr-defined]
    x1, y1, x2, y2 = value_bounds(glyph, (100.0, 50.0, 274.0, 315.0))
    assert (x1, y1, x2, y2) == (100.0, 66.0, 274.0, 315.0)


def test_hidden_index_draws_no_selector_shown_index_does() -> None:
    assert "lv-array-index" not in _svg(_array(_HIDDEN))
    assert "lv-array-index" in _svg(_array(_SHOWN))


def test_frame_is_the_frame_part_not_the_whole_box() -> None:
    """One frame rect the size of ``partID`` 9 (115 wide; the backend insets a
    stroked rect by half its width), not a rect over the whole 174-wide control
    that would also wrap the index area."""
    svg = _svg(_array(_HIDDEN))
    widths = {
        round(float(w))
        for w in re.findall(r'<rect [^>]*width="([\d.]+)"[^>]*stroke-width="1.0"', svg)
    }
    assert 114 in widths and not {173, 174} & widths


def test_saved_elements_draw_as_real_enabled_rows() -> None:
    ctrl = _array(_HIDDEN, element_values=["-40.0", "-30.0"], number_format="%#_g")
    svg = _svg(ctrl)
    assert ">-40<" in svg and ">-30<" in svg
    assert "lv-disabled-mask" in svg  # rows past the array's end stay disabled


def test_empty_array_washes_its_index_and_scrollbar() -> None:
    svg = _svg(_array(_SHOWN))
    assert svg.count("lv-disabled-mask") >= 3  # rows + index + scrollbar


def test_number_format_specs() -> None:
    assert format_number("3600.0", "%#_g") == "3600"
    assert format_number("1e-06", "%#_g") == "1E-6"
    assert format_number("0.75", "%#_g") == "0.75"
    assert format_number("-20.0", "%#_g") == "-20"
    assert format_number("10000", "%.0f") == "10000"
    assert format_number("2.0", "%.2f") == "2.00"


def test_number_format_leaves_unknown_specs_and_non_numbers_alone() -> None:
    assert format_number("3.0", "%d") == "3.0"
    assert format_number("abc", "%#_g") == "abc"
    assert format_number("3.0", None) == "3.0"


def _array_ddo() -> ET.Element:
    return ET.fromstring(
        '<ddo class="indArr" uid="1"><bounds>(0,0,100,200)</bounds>'
        "<objFlags>0</objFlags>"
        '<partsList elements="1"><SL__arrayElement class="stdNum" uid="2">'
        "<bounds>(18,-82,55,2)</bounds><objFlags>395578</objFlags>"
        "<partID>8002</partID>"
        '<partsList elements="1"><SL__arrayElement class="numLabel" uid="3">'
        "<bounds>(9,27,26,72)</bounds><objFlags>0</objFlags><partID>10</partID>"
        "</SL__arrayElement></partsList>"
        "</SL__arrayElement></partsList>"
        '<ddo class="stdNum" uid="4"><bounds>(25,7,48,88)</bounds>'
        "<objFlags>0</objFlags>"
        '<partsList elements="1"><SL__arrayElement class="numLabel" uid="5">'
        '<bounds>(3,3,20,78)</bounds><objFlags>0</objFlags><format>"%#_g"</format>'
        "</SL__arrayElement></partsList></ddo>"
        "</ddo>"
    )


def test_parser_keeps_hidden_flag_sub_parts_and_number_format() -> None:
    ctrl = _parse_ddo(_array_ddo(), "1", set())
    assert ctrl is not None
    index = next(p for p in ctrl.parts if p.part_id == 8002)
    assert index.props["objFlags"] == "395578"
    assert [p.part_id for p in index.parts] == [10]
    assert ctrl.number_format == "%#_g"


def test_parser_carries_every_saved_element_value() -> None:
    ctrl = _parse_ddo(
        _array_ddo(), "1", set(), None, None, ["-40.0", "-30.0", "-20.0"]
    )
    assert ctrl is not None
    assert ctrl.element_values == ["-40.0", "-30.0", "-20.0"]


def test_scrollbar_only_when_its_part_is_not_hidden() -> None:
    """The scrollbar is a per-control toggle (``partID`` 39, objFlags 0x8), the
    same as the index: hidden on some arrays in the reference, visible on
    others."""
    assert "lv-array-scrollbar" in _svg(_array(_HIDDEN))
    assert "lv-array-scrollbar" not in _svg(_array(_HIDDEN, {"objFlags": "3454"}))


def _numeric() -> ParsedFPControl:
    """An 85x55 numeric control: 17px caption strip, then one raised frame
    (partID 9) holding the spinner at the LEFT and a narrow readout."""
    parts = [
        ParsedFPPart(16, "label", (0, 0, 17, 79)),
        ParsedFPPart(10, "numLabel", (27, 27, 44, 73)),
        ParsedFPPart(2, "bigMultiCosm", (24, 7, 35, 22)),
        ParsedFPPart(3, "bigMultiCosm", (35, 7, 46, 22)),
        ParsedFPPart(119, "cosm", (25, 25, 46, 75)),
        ParsedFPPart(9, "cosm", (18, 0, 55, 85)),
    ]
    return ParsedFPControl(
        uid="9", name="Initial Time (S)", control_type="stdNum",
        bounds=(0, 0, 55, 85), parts=parts, default_value="3600.0",
        number_format="%#_g",
    )


def test_numeric_spinner_sits_left_of_a_narrow_readout() -> None:
    glyph = resolve_glyph(_numeric(), DEFAULT_THEME)
    assert glyph.native_size == (85.0, 37.0)  # type: ignore[attr-defined]
    spinner = glyph.spinner_local  # type: ignore[attr-defined]
    readout = glyph.readout_local  # type: ignore[attr-defined]
    assert spinner[2] <= readout[0]  # spinner entirely left of the readout
    assert readout[2] - readout[0] < 60  # narrow, not the whole control
    assert ">3600<" in _svg(_numeric())


def test_numeric_without_parts_is_a_plain_cell_with_no_spinner() -> None:
    bare = ParsedFPControl(
        uid="1", name="x", control_type="stdNum", bounds=(0, 0, 20, 80),
        default_value="5.0",
    )
    svg = _svg(bare)
    assert ">5.0<" in svg
    assert svg.count("<polygon") == 0

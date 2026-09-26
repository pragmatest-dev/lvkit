"""Frame colors: a cluster or array is drawn in the literal colors its frame
part (``partID`` 9) records; a default/system color falls back to the theme."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.models import ParsedFPControl, ParsedFPPart, ParsedFrontPanel
from lvkit.parser.utils import heap_color
from lvkit.parser.vi import _parse_fp_parts_list
from lvkit.render.front_panel import render_front_panel_svg


def test_literal_heap_color_is_rgb_and_a_default_is_not() -> None:
    assert heap_color("00646464") == "#646464"
    assert heap_color("00CCCCCC") == "#CCCCCC"
    assert heap_color("01000000") is None  # a default, not a color
    assert heap_color(None) is None
    assert heap_color("0064") is None
    assert heap_color("00ZZZZZZ") is None


def test_parser_records_a_parts_literal_colors() -> None:
    owner = ET.fromstring(
        "<ddo><partsList>"
        '<SL__arrayElement class="cosm"><partID>9</partID>'
        "<bounds>(18, 0, 265, 115)</bounds>"
        "<fgColor>00646464</fgColor><bgColor>00CCCCCC</bgColor>"
        "</SL__arrayElement>"
        '<SL__arrayElement class="label"><partID>16</partID>'
        "<bounds>(0, 0, 17, 60)</bounds>"
        "<fgColor>01000000</fgColor></SL__arrayElement>"
        "</partsList></ddo>"
    )
    frame, label = _parse_fp_parts_list(owner)
    assert (frame.fg_color, frame.bg_color) == ("#646464", "#CCCCCC")
    assert (label.fg_color, label.bg_color) == (None, None)


def _cluster_svg(frame: ParsedFPPart | None) -> str:
    ctrl = ParsedFPControl(
        uid="1", name="c", control_type="stdClust", bounds=(0, 0, 100, 100),
        label_visible=False, parts=[frame] if frame else [],
    )
    return render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 200, 200))
    )


def test_cluster_is_drawn_in_its_frame_colors() -> None:
    frame = ParsedFPPart(
        9, "cosm", (0, 0, 100, 100), {}, [], fg_color="#112233", bg_color="#445566"
    )
    svg = _cluster_svg(frame)
    assert 'fill="#445566"' in svg and 'stroke="#112233"' in svg


def test_cluster_without_recorded_colors_uses_the_theme() -> None:
    svg = _cluster_svg(ParsedFPPart(9, "cosm", (0, 0, 100, 100)))
    assert "#445566" not in svg and "#112233" not in svg

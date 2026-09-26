"""Array index displays: one per dimension, inside one raised bezel."""

from __future__ import annotations

from lvkit.parser.models import ParsedFPControl, ParsedFPPart, ParsedFrontPanel
from lvkit.render.front_panel import render_front_panel_svg

_SHOWN = {"objFlags": "530802"}
_HIDDEN = {"objFlags": "530810"}  # bit 0x8 set


def _index(top: int, props: dict[str, str]) -> ParsedFPPart:
    # 41x30 index display: spinner halves at the left, readout at the right.
    sub = [
        ParsedFPPart(10, "numLabel", (7, 17, 24, 37)),
        ParsedFPPart(3, "bigMultiCosm", (16, 0, 30, 14)),
        ParsedFPPart(2, "bigMultiCosm", (0, 0, 14, 14)),
        ParsedFPPart(9, "cosm", (3, 13, 28, 41)),
    ]
    return ParsedFPPart(8002, "stdNum", (top, 2, top + 30, 43), props, sub)


def _svg(indices: list[ParsedFPPart]) -> str:
    parts = [
        *indices,
        ParsedFPPart(30, "cosm", (14, 0, 14 + 30 * len(indices), 46)),
        ParsedFPPart(28, "cosm", (21, 47, 46, 101)),
        ParsedFPPart(9, "cosm", (17, 43, 50, 105)),
        ParsedFPPart(None, "stdNum", (21, 47, 43, 101), {"typeDesc": "TypeID(39)"}),
    ]
    ctrl = ParsedFPControl(
        uid="7", name="Grid", control_type="indArr", bounds=(0, 0, 80, 110),
        parts=parts, label_visible=False,
    )
    return render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 300, 300))
    )


def test_index_is_framed_by_its_own_frame_part_not_the_bezel() -> None:
    """The frame is the index's ``partID`` 9 sub-part (28x25 here); the larger
    bezel part (46x32) is not drawn."""
    svg = _svg([_index(14, _SHOWN)])
    assert 'width="27.0" height="24.0"' in svg and 'rx="3.0"' in svg
    assert 'width="45.0"' not in svg


def test_each_dimension_draws_its_own_index() -> None:
    one = _svg([_index(14, _SHOWN)])
    two = _svg([_index(14, _SHOWN), _index(44, _SHOWN)])
    spinner = 'rx="7.0"'  # a 14 px wide spinner
    assert one.count(spinner) == 1
    assert two.count(spinner) == 2


def test_only_the_first_dimension_is_the_live_target() -> None:
    two = _svg([_index(14, _SHOWN), _index(44, _SHOWN)])
    assert two.count('data-lv-action="next"') == 1


def test_a_hidden_dimension_draws_no_index() -> None:
    svg = _svg([_index(14, _HIDDEN), _index(44, _HIDDEN)])
    assert 'rx="7.0"' not in svg and 'rx="3.0"' not in svg

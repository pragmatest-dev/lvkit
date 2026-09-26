"""Label vs. caption: a control shows ONE of them, chosen by their own hidden
flags (objFlags bit 0x8). Measured on the sample corpus: label shown 55%, label
hidden with no caption 44%, caption shown with the label hidden 0.7%, both
shown never.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.layout import _field_label
from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel
from lvkit.parser.vi import _parse_ddo
from lvkit.render.front_panel import render_front_panel_svg

_SHOWN = 0
_HIDDEN = 8


def _part(part_id: int, flags: int, text: str, bounds: str) -> str:
    return (
        '<SL__arrayElement class="label" uid="9">'
        f"<objFlags>{flags}</objFlags><partID>{part_id}</partID>"
        f"<bounds>{bounds}</bounds>"
        f'<textRec class="textHair"><text>"{text}"</text></textRec>'
        "</SL__arrayElement>"
    )


def _ddo(label_flags: int | None, caption_flags: int | None) -> ET.Element:
    parts = ""
    if label_flags is not None:
        parts += _part(16, label_flags, "pin_name", "(0,0,17,60)")
    if caption_flags is not None:
        parts += _part(82, caption_flags, "Pin Name", "(2,4,19,64)")
    return ET.fromstring(
        '<ddo class="stdNum" uid="1"><bounds>(10,20,50,120)</bounds>'
        f"<objFlags>0</objFlags><partsList>{parts}</partsList></ddo>"
    )


def test_visible_label_is_the_shown_text() -> None:
    rect, text = _field_label(_ddo(_SHOWN, _HIDDEN))
    assert rect is not None and text is None  # None = draw the field's own name


def test_visible_caption_replaces_a_hidden_label() -> None:
    rect, text = _field_label(_ddo(_HIDDEN, _SHOWN))
    assert text == "Pin Name"
    # the caption's OWN rect, offset by the control's origin (left 20, top 10)
    assert rect == (24.0, 12.0, 84.0, 29.0)


def test_nothing_is_shown_when_both_are_hidden_or_absent() -> None:
    assert _field_label(_ddo(_HIDDEN, _HIDDEN)) == (None, None)
    assert _field_label(_ddo(_HIDDEN, None)) == (None, None)
    assert _field_label(_ddo(None, None)) == (None, None)


def test_parser_records_which_caption_text_is_visible() -> None:
    def flags(label: int, caption: int) -> tuple[bool, bool]:
        ctrl = _parse_ddo(_ddo(label, caption), "1", set())
        assert ctrl is not None
        return ctrl.label_visible, ctrl.caption_visible

    assert flags(_SHOWN, _HIDDEN) == (True, False)
    assert flags(_HIDDEN, _SHOWN) == (False, True)
    assert flags(_HIDDEN, _HIDDEN) == (False, False)


def _svg(**kw) -> str:
    ctrl = ParsedFPControl(
        uid="1", name="pin_name", control_type="stdNum",
        bounds=(30, 20, 60, 120), **kw,
    )
    return render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 400, 600))
    )


def test_top_level_control_draws_only_the_visible_text() -> None:
    assert ">pin_name<" in _svg(label_visible=True)
    hidden = _svg(label_visible=False)
    assert ">pin_name<" not in hidden
    shown = _svg(label_visible=False, caption="Pin Name", caption_visible=True)
    assert ">Pin Name<" in shown and ">pin_name<" not in shown



def test_top_level_label_draws_at_its_own_part_rect() -> None:
    """The label sits INSIDE the control's box at its recorded part rect, not
    above the box where it would cover whatever is over the control."""
    from lvkit.parser.models import ParsedFPPart

    ctrl = ParsedFPControl(
        uid="1", name="ring", control_type="stdRing", bounds=(288, 72, 327, 200),
        parts=[ParsedFPPart(16, "label", (0, 11, 15, 128))],
    )
    svg = render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 400, 600))
    )
    tag = svg.split(">ring<")[0].rsplit("<text", 1)[1]
    assert 'x="84.0"' in tag  # control left 72 + part left 11 + 1
    baseline = float(tag.split('y="')[1].split('"')[0])
    assert 288 <= baseline <= 303  # inside the label rect, not at 283

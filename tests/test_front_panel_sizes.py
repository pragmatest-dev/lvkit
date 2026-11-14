"""Front-panel sizing: a boolean draws in its button part, not the whole value
box (the union of button, shadow and divot), and labels use the front-panel
label size."""

from __future__ import annotations

import re

from lvkit.parser.models import ParsedFPControl, ParsedFPPart, ParsedFrontPanel
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.label import LABEL_SIZE


def _part(part_id: int, bounds: tuple[int, int, int, int]) -> ParsedFPPart:
    return ParsedFPPart(
        part_class="bigMultiCosm", part_id=part_id, bounds=bounds, props={}
    )


def _bool_svg() -> str:
    # (top, left, bottom, right): divot 60x31 around a 50x21 button, as in the
    # issue #101 boolean.
    ctrl = ParsedFPControl(
        uid="1", name="flag", control_type="stdBool", default_value="True",
        bounds=(10, 10, 41, 70), label_visible=False,
        parts=[_part(88, (0, 0, 31, 60)), _part(21, (5, 5, 26, 55))],
    )
    return render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 100, 100))
    )


def test_boolean_switch_draws_in_its_button_part() -> None:
    rects = re.findall(
        r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"[^>]*rx=',
        _bool_svg(),
    )
    switch = [tuple(map(float, r)) for r in rects if float(r[2]) < 60][0]
    # the 50x21 button inset by half its 1px stroke, not the 60x31 box
    assert (switch[2], switch[3]) == (49.0, 20.0)


def test_label_uses_the_front_panel_label_size() -> None:
    ctrl = ParsedFPControl(
        uid="1", name="pin_name", control_type="stdNum", bounds=(30, 20, 60, 120),
    )
    svg = render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 400, 600))
    )
    label_tag = svg.split(">pin_name<")[0].rsplit("<text", 1)[1]
    assert f'font-size="{LABEL_SIZE}"' in label_tag



def test_glyph_without_its_own_narrowing_gets_the_value_extent() -> None:
    """A ring's heap box includes its label strip (top 15 px) and the label's
    extra width; its glyph draws into the union of the non-label parts."""
    from lvkit.render.front_panel.controls import (
        control_value_bounds,
        resolve_glyph,
    )
    from lvkit.render.style import DEFAULT_THEME

    ctrl = ParsedFPControl(
        uid="1", name="ring", control_type="stdRing", bounds=(288, 72, 327, 200),
        parts=[
            ParsedFPPart(16, "label", (0, 11, 15, 128)),
            _part(9, (14, 11, 39, 61)),
            _part(12, (18, 15, 35, 57)),
            _part(10, (18, 63, 35, 79)),
            _part(2, (13, 0, 25, 12)),
        ],
    )
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    box = control_value_bounds(ctrl, glyph, (72.0, 288.0, 200.0, 327.0))
    assert box == (72.0, 301.0, 151.0, 327.0)  # not the 128x39 heap box

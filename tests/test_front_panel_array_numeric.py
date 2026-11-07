"""An array's index display and its numeric element are drawn by the numeric
drawing, from their own parts and picture ids (Modern here), greyed while the
array has no saved elements."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.parser.models import ParsedFPControl, ParsedFPPart, ParsedFrontPanel
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.array import ArrayControlGlyph, ElementRowGlyph
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.style import ControlStyle
from lvkit.render.style import DEFAULT_THEME
from tests.corpus_graphs import load_corpus_vi

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_MUI = (
    _SAMPLES
    / "measurement-plugin-labview/Source/Generator/Measurement Plug-In Template/UI"
    / "Measurement UI.vi"
)
_SHOWN = 0x932
_HIDDEN = 0x93A
_PILL_TOP = "#D1D1D1"  # the Modern spinner pill's gradient start


def _numeric_parts(arrows: int, readout_fill: str | None = None) -> list[ParsedFPPart]:
    """A Modern numeric's parts: readout, two arrow pills, frame."""
    return [
        ParsedFPPart(10, "numLabel", (7, 17, 24, 37), image_ids=(-239,),
                     bg_color=readout_fill, flags=0x40972),
        ParsedFPPart(3, "bigMultiCosm", (14, 0, 28, 14), image_ids=(-414,),
                     flags=arrows),
        ParsedFPPart(2, "bigMultiCosm", (0, 0, 14, 14), image_ids=(-413,),
                     flags=arrows),
        ParsedFPPart(9, "cosm", (3, 13, 28, 41), image_ids=(-412,)),
    ]


def _array(indicator: bool, values: list[object]) -> ParsedFPControl:
    index = ParsedFPPart(
        8002, "stdNum", (16, 0, 44, 41), parts=_numeric_parts(_SHOWN), flags=0xC0972
    )
    element = ParsedFPPart(
        None,
        "stdNum",
        (23, 45, 49, 110),
        parts=[
            ParsedFPPart(10, "numLabel", (5, 15, 22, 61), image_ids=(-239,)),
            ParsedFPPart(3, "bigMultiCosm", (14, 0, 26, 12), image_ids=(-414,),
                         flags=_HIDDEN if indicator else _SHOWN),
            ParsedFPPart(2, "bigMultiCosm", (0, 0, 12, 12), image_ids=(-413,),
                         flags=_HIDDEN if indicator else _SHOWN),
            ParsedFPPart(9, "cosm", (1, 11, 26, 65), image_ids=(-412,)),
        ],
        flags=393219 if indicator else 393218,
    )
    return ParsedFPControl(
        uid="7",
        name="Grid",
        control_type="indArr",
        bounds=(0, 0, 60, 120),
        parts=[
            index,
            ParsedFPPart(28, "cosm", (23, 45, 49, 110)),
            ParsedFPPart(9, "cosm", (19, 41, 53, 114)),
            element,
        ],
        label_visible=False,
        element_values=values,
    )


def _svg(ctrl: ParsedFPControl) -> str:
    return render_front_panel_svg(
        ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 300, 300)),
        window_frame=False,
    )


def test_the_index_is_the_modern_numeric_and_the_element_its_own_fill() -> None:
    control = _svg(_array(False, ["5"]))
    assert _PILL_TOP in control
    assert 'fill="#FAFAFA"' in control and 'fill="#D2D2D2"' not in control
    indicator = _svg(_array(True, ["5"]))
    assert _PILL_TOP in indicator
    assert 'fill="#D2D2D2"' in indicator


def test_an_empty_array_has_a_disabled_element_and_a_plain_index() -> None:
    control = _svg(_array(False, []))
    # the element's field and text are the disabled look; the index readout
    # beside it keeps the control's own fill
    assert 'fill="#FDFDFD"' in control
    assert 'fill="#FAFAFA"' in control
    assert 'fill="#A5A8A8"' in control
    # the element's spinner is the faded pill; the index's is the normal one
    assert 'fill="#B5B5B5"' in control
    assert 'fill="#3C3C3C"' in control
    assert "#E8E8E8" in control and _PILL_TOP in control
    assert 'fill="#CCCCCC"' not in control
    assert 'class="lv-disabled-mask"' not in control
    indicator = _svg(_array(True, []))
    assert 'fill="#EDEDED"' in indicator
    assert 'fill="#FAFAFA"' in indicator
    assert 'fill="#D2D2D2"' not in indicator


def test_the_elements_arrows_follow_their_own_hidden_bit() -> None:
    glyph = resolve_glyph(_array(False, ["5"]), DEFAULT_THEME)
    assert isinstance(glyph, ArrayControlGlyph)
    shown = glyph.default_element
    assert isinstance(shown, ElementRowGlyph)
    assert shown.glyph.style == ControlStyle.MODERN
    assert len(shown.glyph.spinner_halves_local) == 2
    assert not shown.glyph.is_indicator
    hidden = resolve_glyph(_array(True, ["5"]), DEFAULT_THEME)
    assert isinstance(hidden, ArrayControlGlyph)
    assert isinstance(hidden.default_element, ElementRowGlyph)
    assert hidden.default_element.glyph.spinner_halves_local == ()
    assert hidden.default_element.glyph.is_indicator


def test_the_first_index_is_the_live_target() -> None:
    svg = _svg(_array(False, ["5"]))
    assert svg.count('data-lv-action="next"') == 1
    assert svg.count('data-lv-action="prev"') == 1
    assert 'class="lv-array-index"' in svg


@pytest.mark.needs_samples
def test_measurement_ui_double_array_in_is_drawn_by_the_numeric_drawing() -> None:
    graph, key = load_corpus_vi(_MUI)
    front_panel = graph.get_vi_front_panel(key)
    assert front_panel is not None
    arrays = {
        c.name: resolve_glyph(c, DEFAULT_THEME)
        for c in front_panel.controls
        if c.name in ("double array in", "double array out")
    }
    in_glyph, out_glyph = arrays["double array in"], arrays["double array out"]
    assert isinstance(in_glyph, ArrayControlGlyph)
    assert isinstance(out_glyph, ArrayControlGlyph)
    index = in_glyph.indices[0].numeric
    assert index is not None and index.style == ControlStyle.MODERN
    assert index.disabled and not index.is_indicator
    element = in_glyph.default_element
    assert isinstance(element, ElementRowGlyph)
    assert element.glyph.style == ControlStyle.MODERN
    assert not element.glyph.is_indicator
    assert len(element.glyph.spinner_halves_local) == 2
    out_element = out_glyph.default_element
    assert isinstance(out_element, ElementRowGlyph)
    assert out_element.glyph.is_indicator
    assert out_element.glyph.spinner_halves_local == ()

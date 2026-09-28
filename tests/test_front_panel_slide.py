"""``stdSlide`` front-panel controls: a frame + a thin track along the
control's own long axis (read from its bounds, never assumed), with a thumb
positioned at the saved value's real fraction between the control's own
recorded ``StdNumMin``/``StdNumMax`` (parsed straight off the heap, no VCTP
needed) -- only when that position is actually computable; otherwise just
the frame and track, never a guessed position. Scale tick marks and an
optional digital-display sub-part are separate heap parts this does not
draw."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from lvkit.parser.models import ParsedFrontPanel
from lvkit.parser.vi import _std_num_bound, parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.base import control_value_bounds, find_part
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.slide import SlideControlGlyph
from lvkit.render.front_panel.controls.unknown import UnknownControlGlyph
from lvkit.render.style import DEFAULT_THEME

from .conftest import SAMPLES_ROOT
from .test_front_panel_render import _control

_GTR_MAIN_UI_VI = (
    SAMPLES_ROOT
    / "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner"
    / "Graphical Test Runner - Main UI - .vi"
)

_MASTER_ACQUISITION_VI = (
    SAMPLES_ROOT / "LabVIEW-DAQ/Fiber Photometry/MasterAquisitionFile_FP.vi"
)


def _elem(text: str, fmt: str | None = None) -> ET.Element:
    e = ET.Element("StdNumMin")
    e.text = text
    if fmt is not None:
        e.set("Format", fmt)
    return e


def test_std_num_bound_reads_the_leading_number_or_the_inf_sentinel() -> None:
    """A plain decimal with a parenthesised hex echo, and LabVIEW's own
    ``-inf``/``inf`` "no bound set" sentinel (both real corpus shapes)."""
    assert _std_num_bound(_elem("100 (0x4059000000000000)")) == 100.0
    assert _std_num_bound(_elem("0 (0x0000000000000000)")) == 0.0
    assert _std_num_bound(_elem("-inf (0xFFF0000000000000)")) == float("-inf")
    assert _std_num_bound(_elem("inf (0x7FF0000000000000)")) == float("inf")
    assert _std_num_bound(None) is None


def test_std_num_bound_decodes_a_raw_hex_bit_pattern_by_byte_width() -> None:
    """``Format="hex"`` -- a raw bit pattern with NO decimal echo at all (a
    real corpus shape distinct from the parenthesised-hex-ECHO case above):
    4 bytes -> a signed 32-bit integer (0x80000000 = INT32_MIN, a real
    corpus signed-32 control's own min); 8 bytes -> an IEEE-754 double (the
    real corpus -inf/inf sentinel's own bit pattern, this time with no
    decimal echo to fall back on). Must NOT run the raw hex text through
    plain float() -- every character in a hex bit pattern can also be a
    valid decimal digit, so that would silently return a wrong number."""
    assert _std_num_bound(_elem("80000000", fmt="hex")) == float(-(2**31))
    assert _std_num_bound(_elem("FFF0000000000000", fmt="hex")) == float("-inf")
    assert _std_num_bound(_elem("7FF0000000000000", fmt="hex")) == float("inf")
    assert _std_num_bound(_elem("zz", fmt="hex")) is None  # not valid hex
    assert _std_num_bound(_elem("00", fmt="hex")) is None  # unrecognized width


def test_a_horizontal_slide_places_its_thumb_by_real_fraction() -> None:
    """Orientation comes from the control's own box (wider than tall), never
    a heuristic; the thumb sits at (value-min)/(max-min)."""
    ctrl = _control(
        "Slide", "stdSlide", (0, 0, 30, 300),
        default_value="30", slide_min=0.0, slide_max=100.0,
    )
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, SlideControlGlyph)
    assert glyph.fraction == pytest.approx(0.3)


def test_a_vertical_slide_places_its_thumb_by_real_fraction() -> None:
    ctrl = _control(
        "Slide", "stdSlide", (0, 0, 300, 30),
        default_value="200", slide_min=0.0, slide_max=255.0,
    )
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, SlideControlGlyph)
    assert glyph.fraction == pytest.approx(200 / 255)


def _slide_fraction(ctrl: object) -> float | None:
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)  # type: ignore[arg-type]
    assert isinstance(glyph, SlideControlGlyph)
    return glyph.fraction


def test_a_slide_with_no_computable_position_draws_no_thumb() -> None:
    """No saved value, an unbounded (-inf/inf) range, or an inverted/empty
    range -- never a guessed midpoint."""
    no_value = _control(
        "S", "stdSlide", (0, 0, 30, 300), slide_min=0.0, slide_max=100.0
    )
    assert _slide_fraction(no_value) is None

    unbounded = _control(
        "S", "stdSlide", (0, 0, 30, 300),
        default_value="5", slide_min=float("-inf"), slide_max=float("inf"),
    )
    assert _slide_fraction(unbounded) is None

    no_range = _control("S", "stdSlide", (0, 0, 30, 300), default_value="5")
    assert _slide_fraction(no_range) is None

    # A structured (cluster-shaped) decode -- e.g. a two-thumb range slider --
    # is not a plain string; must not crash or fabricate a position.
    structured = _control(
        "S", "stdSlide", (0, 0, 30, 300), slide_min=0.0, slide_max=100.0
    )
    structured.default_value = {"field_0": 5.0, "field_1": 6.0}  # type: ignore[assignment]
    assert _slide_fraction(structured) is None


def test_a_slide_clamps_an_out_of_range_value() -> None:
    ctrl = _control(
        "S", "stdSlide", (0, 0, 30, 300),
        default_value="150", slide_min=0.0, slide_max=100.0,
    )
    assert _slide_fraction(ctrl) == 1.0


def _thumb_cy(svg: str) -> float:
    """The one drawn ``<circle>``'s own ``cy`` -- ``SlideControlGlyph`` draws
    exactly one, the thumb (the frame/track are ``<rect>``s)."""
    (cy,) = re.findall(r'<circle cx="[\d.-]+" cy="([\d.-]+)"', svg)
    return float(cy)


def test_a_vertical_slides_thumb_is_anchored_to_its_bottom_not_its_top() -> None:
    """Hermetic, end-to-end through the real SVG markup: a real vertical
    slider's origin is its BOTTOM (0 at the bottom, max at the top) -- a low
    value's thumb must land nearer the bottom edge (larger y) than a high
    value's (smaller y), an SVG y axis growing downward."""

    def render(value: str) -> str:
        fp = ParsedFrontPanel(
            controls=[
                _control(
                    "S", "stdSlide", (0, 0, 300, 30),
                    default_value=value, slide_min=0.0, slide_max=100.0,
                )
            ],
            panel_bounds=(0, 0, 100, 400),
        )
        return render_front_panel_svg(fp)

    low_cy = _thumb_cy(render("10"))
    high_cy = _thumb_cy(render("90"))
    assert low_cy > high_cy


@pytest.mark.needs_samples
@pytest.mark.skipif(not _GTR_MAIN_UI_VI.exists(), reason="sample absent")
def test_a_real_corpus_slide_control_renders_not_unknown() -> None:
    parsed = parse_vi(_GTR_MAIN_UI_VI)
    slide = next(c for c in parsed.front_panel.controls if c.control_type == "stdSlide")
    assert slide.slide_min == 0.0 and slide.slide_max == 100.0
    glyph = resolve_glyph(slide, DEFAULT_THEME)
    assert isinstance(glyph, SlideControlGlyph)
    assert not isinstance(glyph, UnknownControlGlyph)
    svg = render_front_panel_svg(parsed.front_panel, title=_GTR_MAIN_UI_VI.name)
    assert "<svg" in svg


@pytest.mark.needs_samples
@pytest.mark.skipif(not _MASTER_ACQUISITION_VI.exists(), reason="sample absent")
def test_a_real_corpus_slide_control_does_not_paint_over_its_own_label() -> None:
    """Real bug found by inspection: this VI's "Time Elapsed (Min)" slide has
    a HIDDEN digital-display sub-part (a disabled "show digital display"
    option, objFlags bit 0x8) recorded at a local top ABOVE the label's own
    bottom -- before value_extent() excluded hidden parts, this pulled the
    drawn glyph's own box up to overlap the label's rect, painting over the
    control's name."""
    parsed = parse_vi(_MASTER_ACQUISITION_VI)
    slide = next(
        c for c in parsed.front_panel.controls if c.name == "Time Elapsed (Min)"
    )
    label = find_part(slide, 16)
    assert label is not None
    label_bottom = float(label.bounds[2])  # (top, left, bottom, right)

    glyph = resolve_glyph(slide, DEFAULT_THEME)
    value_box = control_value_bounds(slide, glyph, (0.0, 0.0, 0.0, 0.0))
    assert value_box[1] >= label_bottom

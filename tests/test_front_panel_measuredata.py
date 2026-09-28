"""``stdMeasureData`` front-panel controls: a bordered box + the control's own
real ``measure_flavor`` (e.g. ``"DigitalWaveform"``) -- parsed from the VCTP
type descriptor's own ``Flavor``, the SAME field the block-diagram renderer
already keys a Timestamp's own color/glyph on. Real corpus data never
decomposes this into component fields (t0/dt/Y) at the type-model level, so
this never fakes one -- only the real flavor text, or a generic fallback name
when the flavor could not be resolved."""

from __future__ import annotations

import pytest

from lvkit.models import LVType, LVTypeKind
from lvkit.parser.vi import parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.measure_data import MeasureDataGlyph
from lvkit.render.front_panel.controls.resolve import _element, resolve_glyph
from lvkit.render.front_panel.controls.unknown import UnknownControlGlyph
from lvkit.render.style import DEFAULT_THEME

from .conftest import SAMPLES_ROOT
from .test_front_panel_render import _control

_STIM_VI = SAMPLES_ROOT / "LabVIEW-DAQ/Fiber Photometry/GenerateStimulusOutputs_FP.vi"


def _measure_data_type(flavor: str | None) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="MeasureData", measure_flavor=flavor
    )


def test_a_measure_data_control_shows_its_real_flavor() -> None:
    ctrl = _control(
        "Waveform", "stdMeasureData", (0, 0, 40, 100),
        lv_type=_measure_data_type("DigitalWaveform"),
    )
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, MeasureDataGlyph)
    assert glyph.flavor == "DigitalWaveform"


def test_a_measure_data_control_with_no_resolved_flavor_still_draws() -> None:
    """An unresolved VCTP (``lv_type`` is None) -- still a recognized bordered
    box, never UnknownControlGlyph, with a generic fallback name rather than
    a blank."""
    ctrl = _control("Waveform", "stdMeasureData", (0, 0, 40, 100))
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, MeasureDataGlyph)
    assert not isinstance(glyph, UnknownControlGlyph)
    assert glyph.flavor is None


def test_a_measure_data_control_actually_draws_its_flavor_text() -> None:
    """Hermetic, end-to-end through the real SVG markup."""
    from lvkit.parser.models import ParsedFrontPanel

    ctrl = _control(
        "Waveform", "stdMeasureData", (0, 0, 40, 100),
        lv_type=_measure_data_type("AnalogWaveform"),
    )
    fp = ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 150, 150))
    svg = render_front_panel_svg(fp)
    assert ">AnalogWaveform<" in svg


@pytest.mark.needs_samples
@pytest.mark.skipif(not _STIM_VI.exists(), reason="sample absent")
def test_a_real_corpus_measure_data_element_renders_not_unknown() -> None:
    """The real corpus shape: stdMeasureData never appears as a top-level
    control, only as an array's element (``GenerateStimulusOutputs_FP.vi``'s
    "Stimulus Digital Outputs", a real digital-waveform array)."""
    parsed = parse_vi(_STIM_VI)
    arr = next(
        c for c in parsed.front_panel.controls if c.name == "Stimulus Digital Outputs"
    )
    assert arr.element_lv_type is not None
    assert arr.element_lv_type.measure_flavor == "DigitalWaveform"

    glyph = _element(arr, DEFAULT_THEME)
    assert isinstance(glyph, MeasureDataGlyph)
    assert not isinstance(glyph, UnknownControlGlyph)
    assert glyph.flavor == "DigitalWaveform"

    svg = render_front_panel_svg(parsed.front_panel, title=_STIM_VI.name)
    assert "<svg" in svg

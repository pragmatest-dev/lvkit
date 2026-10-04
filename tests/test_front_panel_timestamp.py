"""``absTime`` (Timestamp) front-panel controls (issue #101 follow-up): a
cluster field whose heap ``class`` was previously dropped entirely by
``_direct_fields``'s class allowlist (see ``parser.fp_heap_type``), so it
never reached render at all. Once parsed, its value glyph distinguishes a
genuinely UNASSIGNED control (no ``DefaultData`` saved) from one whose real
saved value happens to decode to the epoch -- two different states LabVIEW
itself shows differently, confirmed directly by the maintainer."""

from __future__ import annotations

from lvkit.parser.models import ParsedFrontPanel
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.timestamp import (
    _UNASSIGNED_TEXT,
    TimestampControlGlyph,
    timestamp_control,
)
from lvkit.render.style import DEFAULT_THEME

from .test_front_panel_render import _control


def test_unassigned_control_shows_labviews_own_placeholder_not_the_epoch() -> None:
    """No ``DefaultData`` at all -- genuinely never assigned -- shows
    LabVIEW's own fixed placeholder text, never a decoded (real-looking but
    fabricated) epoch date."""
    ctrl = _control("Time Stamp", "absTime", (0, 0, 70, 90))
    glyph = timestamp_control(ctrl)
    assert isinstance(glyph, TimestampControlGlyph)
    assert glyph.value == _UNASSIGNED_TEXT


def test_a_real_saved_value_decodes_to_its_own_date_not_the_placeholder() -> None:
    """A real saved value -- even one that happens to decode to the LabVIEW
    epoch itself -- is a genuinely DIFFERENT state from "never assigned",
    and must show the real decoded date, not the unassigned placeholder."""
    ctrl = _control(
        "Time Stamp", "absTime", (0, 0, 70, 90), default_value="Timestamp(0.0)"
    )
    glyph = timestamp_control(ctrl)
    assert isinstance(glyph, TimestampControlGlyph)
    assert glyph.value != _UNASSIGNED_TEXT
    assert "1/1/1904" in glyph.value


def test_resolve_glyph_dispatches_abstime_to_the_timestamp_control() -> None:
    ctrl = _control("Time Stamp", "absTime", (0, 0, 70, 90))
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, TimestampControlGlyph)


def test_an_unassigned_timestamp_renders_end_to_end() -> None:
    """Hermetic, end-to-end through the real SVG markup -- the placeholder
    text actually reaches the page, never an empty gap (the original #101
    bug: this field was silently dropped by the parser before render ever
    saw it)."""
    fp = ParsedFrontPanel(
        controls=[_control("Time Stamp", "absTime", (0, 0, 70, 90))],
        panel_bounds=(0, 0, 100, 100),
    )
    svg = render_front_panel_svg(fp)
    assert "00:00:00.000 PM" in svg
    assert "MM/DD/YYYY" in svg

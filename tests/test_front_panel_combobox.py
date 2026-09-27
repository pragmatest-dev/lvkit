"""``stdComboBox`` front-panel controls: real corpus heap parts (a selLabel
part + dropdown-arrow decorations) draw the SAME value-cell-with-chevron
shape a ring/enum uses, so it reuses ``EnumControlGlyph``. It shares the
ring/enum ``multiLabel`` item-list convention when a developer set one; no
real corpus instance was found with one, and unlike a ring it can also hold
free-typed text with no fixed list at all, which ``EnumControlGlyph``
already falls back to showing literally.

Of 143 real corpus VI heaps containing the text "stdComboBox", only 9
actually parse into a top-level ``ParsedFPControl`` with
``control_type == "stdComboBox"`` -- traced one of the other ~94%: a
``<ddo class="stdComboBox">`` several levels deep inside a ``stdRefNum``-styled
Font control's OWN parts (its internal font-name text box), never surfaced
as its own ``ParsedFPControl`` by ``_parse_front_panel``'s fPDCO walk, and so
never reaching this dispatch at all -- the SAME nested-decoration pattern the
``stdTag`` work found (see ``test_front_panel_tag.py``)."""

from __future__ import annotations

import pytest

from lvkit.parser.vi import parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.enum_control import EnumControlGlyph
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.unknown import UnknownControlGlyph
from lvkit.render.glyph import ClusterConstantGlyph
from lvkit.render.style import DEFAULT_THEME

from .conftest import SAMPLES_ROOT
from .test_front_panel_render import _control

_MEASUREMENT_UI_VI = (
    SAMPLES_ROOT
    / "measurement-plugin-labview/Source/Example Measurements/VISA Measurement"
    / "VISA Measurement/Measurement UI.vi"
)


def test_a_combo_box_with_a_fixed_list_shows_the_selected_item() -> None:
    """Purely hypothetical: no real corpus stdComboBox with a fixed
    ``multiLabel`` item list was found (see module docstring) -- this only
    exercises the shared ``EnumControlGlyph`` index-lookup path that
    stdEnum/stdRing already use for real."""
    fp = _control(
        "Mode", "stdComboBox", (0, 0, 40, 100),
        default_value="1", enum_values=["Auto", "Manual"],
    )
    glyph = resolve_glyph(fp, DEFAULT_THEME)
    assert isinstance(glyph, EnumControlGlyph)
    assert glyph.enum_values == ("Auto", "Manual")


def test_a_combo_box_with_no_fixed_list_shows_its_free_typed_text() -> None:
    """Unlike a ring, a combo box's ``default_value`` need not be an index --
    ``EnumControlGlyph`` falls back to it as literal text when there is no
    item list to index into (real corpus data: no stdComboBox with a fixed
    list was found -- every real instance has none)."""
    fp = _control("Chan", "stdComboBox", (0, 0, 40, 100), default_value="Dev1/ai0")
    glyph = resolve_glyph(fp, DEFAULT_THEME)
    assert isinstance(glyph, EnumControlGlyph)
    assert glyph.enum_values == ()
    assert glyph.default_value == "Dev1/ai0"


def test_a_combo_box_cluster_field_resolves_through_the_same_generic_path() -> None:
    field = _control("Mode", "stdComboBox", (0, 0, 40, 20), default_value="Auto")
    cluster = _control("Config", "stdClust", (0, 0, 50, 50), children=[field])
    glyph = resolve_glyph(cluster, DEFAULT_THEME)
    assert isinstance(glyph, ClusterConstantGlyph)
    (_, field_glyph) = glyph.fields[0]
    assert isinstance(field_glyph, EnumControlGlyph)
    assert field_glyph.default_value == "Auto"


@pytest.mark.needs_samples
@pytest.mark.skipif(not _MEASUREMENT_UI_VI.exists(), reason="sample absent")
def test_a_real_corpus_combo_box_renders_not_unknown() -> None:
    """The real "pin name" control: a String-typed combo box with no saved
    default and no fixed list -- documenting what was actually found, and
    catching a regression if VCTP resolution ever changes for this control."""
    parsed = parse_vi(_MEASUREMENT_UI_VI)
    pin_name = next(
        c for c in parsed.front_panel.controls if c.control_type == "stdComboBox"
    )
    assert pin_name.lv_type is not None
    assert pin_name.lv_type.underlying_type == "String"
    assert pin_name.default_value is None
    assert pin_name.enum_values == []
    glyph = resolve_glyph(pin_name, DEFAULT_THEME)
    assert isinstance(glyph, EnumControlGlyph)
    assert not isinstance(glyph, UnknownControlGlyph)
    svg = render_front_panel_svg(parsed.front_panel, title=_MEASUREMENT_UI_VI.name)
    assert "<svg" in svg

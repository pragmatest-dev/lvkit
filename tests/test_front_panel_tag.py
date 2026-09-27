"""``stdTag`` front-panel controls: LabVIEW's I/O-name type (a DAQmx physical
channel/task name, a VISA resource name, ...) -- already classified into the
reference family by ``style.type_family`` (its wires draw LabVIEW's dark
reference teal), but the real corpus heap draws it as a value cell + dropdown
chevron (a ``selLabel`` part plus arrow decorations), the SAME shape a
ring/enum uses -- not a bare reference icon: unlike a Queue/Notifier, a Tag's
saved channel-name TEXT is the whole point of the control, so it reuses
``EnumControlGlyph`` for that shape (with no fixed item list, so it always
falls back to its own literal text). No new parser work: the control's own
resolved type already comes through the generic ``own_lv_type`` mechanism
``stdRefNum`` added.

Most real corpus "stdTag" XML text (~93% of occurrences) is actually an inert
``partID=8017`` decoration nested inside a ``stdRefNum``'s OWN parts, not a
top-level control of its own -- unrelated to ``control_type == "stdTag"`` and
never reached by this dispatch."""

from __future__ import annotations

import pytest

from lvkit.models import LVType, LVTypeKind
from lvkit.parser.vi import parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.enum_control import EnumControlGlyph
from lvkit.render.front_panel.controls.leaf import leaf_glyph
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.unknown import UnknownControlGlyph
from lvkit.render.glyph import ClusterConstantGlyph
from lvkit.render.style import DEFAULT_THEME, type_family

from .conftest import SAMPLES_ROOT
from .test_front_panel_render import _control

_DAQ_AO_SFP_VI = SAMPLES_ROOT / "lv-flex-channel-examples/DAQ AO SFP/Main.vi"

_TAG_TYPE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Tag")


def test_tag_is_already_classified_into_the_reference_family() -> None:
    """Pre-existing classification (``style.type_family``) -- not invented by
    this control's render support, and not what decides its SHAPE here (see
    the module docstring: the heap's own parts draw a ring-like value cell,
    not a reference icon)."""
    assert type_family(_TAG_TYPE) == "refnum"


def test_leaf_glyph_shows_the_saved_channel_name() -> None:
    """The channel/resource name is real user-facing data, unlike a refnum's
    held payload (never shown) -- it must actually render."""
    glyph = leaf_glyph("stdTag", "'SimAO/ao0:1'", [], DEFAULT_THEME)
    assert isinstance(glyph, EnumControlGlyph)
    assert glyph.default_value == "'SimAO/ao0:1'"
    assert glyph.enum_values == ()  # no fixed item list -- always literal text


def test_leaf_glyph_draws_a_tag_even_with_no_saved_value() -> None:
    glyph = leaf_glyph("stdTag", None, [], DEFAULT_THEME)
    assert isinstance(glyph, EnumControlGlyph)
    assert glyph.default_value is None


def test_a_tag_cluster_field_resolves_through_the_same_generic_path() -> None:
    """A Tag nested in a cluster field falls through the SAME generic
    ``leaf_glyph`` dispatch a top-level one does -- no Tag-specific cluster
    handling exists or is needed. (No real corpus example of this nesting
    exists yet -- see the module docstring -- so this is the only coverage.)"""
    field = _control("Chan", "stdTag", (0, 0, 40, 20), default_value="'Dev1/ai0'")
    cluster = _control("Config", "stdClust", (0, 0, 50, 50), children=[field])
    glyph = resolve_glyph(cluster, DEFAULT_THEME)
    assert isinstance(glyph, ClusterConstantGlyph)
    (_, field_glyph) = glyph.fields[0]
    assert isinstance(field_glyph, EnumControlGlyph)
    assert field_glyph.default_value == "'Dev1/ai0'"


@pytest.mark.needs_samples
@pytest.mark.skipif(not _DAQ_AO_SFP_VI.exists(), reason="sample absent")
def test_a_real_corpus_tag_control_renders_its_channel_name() -> None:
    """A real DAQmx physical-channel control: its saved channel name decodes
    (``'SimAO/ao0:1'``) through the SAME generic mechanism ``stdRefNum``
    added, and actually appears in the rendered SVG."""
    parsed = parse_vi(_DAQ_AO_SFP_VI)
    channels = next(
        c for c in parsed.front_panel.controls if c.control_type == "stdTag"
    )
    assert channels.lv_type is not None and channels.lv_type.underlying_type == "Tag"
    assert channels.default_value == "'SimAO/ao0:1'"
    glyph = resolve_glyph(channels, DEFAULT_THEME)
    assert isinstance(glyph, EnumControlGlyph)
    assert not isinstance(glyph, UnknownControlGlyph)
    svg = render_front_panel_svg(parsed.front_panel, title=_DAQ_AO_SFP_VI.name)
    assert "SimAO" in svg

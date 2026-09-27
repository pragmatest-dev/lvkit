"""``stdRefNum`` front-panel controls: the parser resolves the control's own
type (its ``ref_type``, and a registered payload's type) from the VI's VCTP,
in whichever of the two heap spots it lives; the render view draws the SAME
``RefnumGlyph`` a block-diagram refnum terminal uses."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.models import LVType, LVTypeKind
from lvkit.parser.models import ParsedBlockDiagram
from lvkit.parser.vi import _parse_cluster_fields, _parse_front_panel, parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.leaf import leaf_glyph
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.glyph import RefnumGlyph
from lvkit.render.style import DEFAULT_THEME

from .conftest import SAMPLES_ROOT

_EMPTY_BD = ParsedBlockDiagram(nodes=[], constants=[], wires=[])
_NOTIFIER_VI = (
    SAMPLES_ROOT
    / "ni-labview-icon-editor/resource/plugins/NIIconEditor/Class/Ants/GET"
    / "GET_Notifier.vi"
)

_QUEUE_STRING = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="Refnum",
    ref_type="QueueRef",
    element_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
)
_VI_SERVER_REF = LVType(
    kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="ViRef"
)


def _write_fp(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "vi_FPHb.xml"
    p.write_text(f"<root><paneHierarchy>{body}</paneHierarchy></root>")
    return p


def test_a_refnum_with_no_payload_resolves_via_the_fpdco_level_typedesc(
    tmp_path: Path,
) -> None:
    """The plain-refnum heap shape: ``<typeDesc>`` is a SIBLING of ``<ddo>``
    inside the ``fPDCO`` (verified against real corpus bytes -- a VI-Server
    reference control with no registered payload)."""
    fp_xml = _write_fp(
        tmp_path,
        '<SL__arrayElement class="fPDCO" uid="1">'
        "<typeDesc>TypeID(7)</typeDesc>"
        '<ddo class="stdRefNum" uid="2">'
        "<bounds>(0, 0, 40, 40)</bounds>"
        "</ddo></SL__arrayElement>",
    )
    fp = _parse_front_panel(fp_xml, _EMPTY_BD, type_map={7: _VI_SERVER_REF})
    (ctrl,) = fp.controls
    assert ctrl.control_type == "stdRefNum"
    assert ctrl.lv_type == _VI_SERVER_REF


def test_a_refnum_with_a_payload_resolves_via_its_own_ddo_child_typedesc(
    tmp_path: Path,
) -> None:
    """The typed-refnum heap shape: the OUTER ddo carries its OWN
    ``<typeDesc>`` as a child, positioned AFTER the nested payload ddo
    (verified against real corpus bytes -- a Queue of String control). No
    ``<typeDesc>`` sits at the ``fPDCO`` level in this shape."""
    fp_xml = _write_fp(
        tmp_path,
        '<SL__arrayElement class="fPDCO" uid="1">'
        '<ddo class="stdRefNum" uid="2">'
        '<ddo class="stdString" uid="3"><typeDesc>TypeID(9)</typeDesc></ddo>'
        "<typeDesc>TypeID(8)</typeDesc>"
        "<bounds>(0, 0, 40, 40)</bounds>"
        "</ddo></SL__arrayElement>",
    )
    fp = _parse_front_panel(fp_xml, _EMPTY_BD, type_map={8: _QUEUE_STRING})
    (ctrl,) = fp.controls
    assert ctrl.lv_type == _QUEUE_STRING


def test_an_array_of_refnums_gets_the_elements_type_from_the_arrays_own(
    tmp_path: Path,
) -> None:
    """An array element with no own payload gets its type from the ARRAY's
    OWN already-resolved type (VCTP decodes an array's element type as part
    of the array descriptor) -- there is no per-element ``<typeDesc>`` to find
    in this shape."""
    array_of_refs = LVType(
        kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=_VI_SERVER_REF
    )
    fp_xml = _write_fp(
        tmp_path,
        '<SL__arrayElement class="fPDCO" uid="1">'
        "<typeDesc>TypeID(5)</typeDesc>"
        '<ddo class="indArr" uid="2">'
        "<bounds>(0, 0, 40, 40)</bounds>"
        '<ddo class="stdRefNum" uid="3"><bounds>(0, 0, 20, 20)</bounds></ddo>'
        "</ddo></SL__arrayElement>",
    )
    fp = _parse_front_panel(fp_xml, _EMPTY_BD, type_map={5: array_of_refs})
    (ctrl,) = fp.controls
    assert ctrl.control_type == "indArr"
    assert ctrl.element_lv_type == _VI_SERVER_REF


def test_a_cluster_field_resolves_its_own_type_from_its_own_typedesc() -> None:
    """A field's own ``<typeDesc>`` (the SAME two-spot convention as a
    top-level/array-element control) is resolved and threaded into its own
    ``_parse_ddo`` call -- ``_parse_cluster_fields`` must do this itself since
    ``_parse_ddo`` never self-resolves. (Uses ``stdString``, not
    ``stdRefNum``: ``fp_heap_type._KNOWN_CLASSES`` doesn't yet recognize a
    ``stdRefNum`` field, so ``_direct_fields`` drops one before this wiring
    ever runs -- a separate, pre-existing gap. This proves the WIRING itself
    is correct and ready for whenever that gap is closed.)"""
    a_string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    cluster_ddo = ET.fromstring(
        "<ddo>"
        '<paneHierarchy class="pane"><zPlaneList elements="1">'
        '<SL__arrayElement class="stdString" uid="4">'
        "<typeDesc>TypeID(7)</typeDesc>"
        '<partsList elements="1"><SL__arrayElement class="label">'
        '<partID>16</partID><textRec class="textHair"><text>"Name"</text>'
        "</textRec></SL__arrayElement></partsList>"
        "</SL__arrayElement></zPlaneList></paneHierarchy>"
        '<ddoList elements="1"><SL__arrayElement uid="4" /></ddoList>'
        "</ddo>"
    )
    (field,) = _parse_cluster_fields(cluster_ddo, set(), type_map={7: a_string})
    assert field.lv_type == a_string


def test_an_array_elements_own_payload_resolves_via_its_own_ddo_child_typedesc(
    tmp_path: Path,
) -> None:
    """The array-element counterpart of the top-level with-payload test: an
    element that carries its OWN registered payload (a Queue-of-String
    element, not a plain unresolved-payload element) resolves from the
    ELEMENT's own child ``<typeDesc>``, never the array's ``element_type``."""
    array_of_queues = LVType(
        kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=_QUEUE_STRING
    )
    fp_xml = _write_fp(
        tmp_path,
        '<SL__arrayElement class="fPDCO" uid="1">'
        "<typeDesc>TypeID(5)</typeDesc>"
        '<ddo class="indArr" uid="2">'
        "<bounds>(0, 0, 40, 40)</bounds>"
        '<ddo class="stdRefNum" uid="3">'
        '<ddo class="stdString" uid="4"><typeDesc>TypeID(9)</typeDesc></ddo>'
        "<typeDesc>TypeID(8)</typeDesc>"
        "<bounds>(0, 0, 20, 20)</bounds>"
        "</ddo></ddo></SL__arrayElement>",
    )
    fp = _parse_front_panel(
        fp_xml, _EMPTY_BD, type_map={5: array_of_queues, 8: _QUEUE_STRING}
    )
    (ctrl,) = fp.controls
    assert ctrl.element_lv_type == _QUEUE_STRING


def test_a_doubly_nested_payload_never_leaks_through_as_the_outer_type(
    tmp_path: Path,
) -> None:
    """A payload that itself nests ANOTHER ddo (a Queue of Queues of String):
    the non-recursive ``find("typeDesc")`` must find the OUTER control's own
    typeDesc, never the inner payload's -- a recursive ``.//typeDesc`` search
    would silently pick up the wrong one."""
    inner_payload = _QUEUE_STRING  # the middle Queue-of-String
    outer_queue_of_queue = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="QueueRef",
        element_type=inner_payload,
    )
    fp_xml = _write_fp(
        tmp_path,
        '<SL__arrayElement class="fPDCO" uid="1">'
        '<ddo class="stdRefNum" uid="2">'  # the OUTER Queue-of-Queue
        '<ddo class="stdRefNum" uid="3">'  # the MIDDLE Queue-of-String payload
        '<ddo class="stdString" uid="4"><typeDesc>TypeID(20)</typeDesc></ddo>'
        "<typeDesc>TypeID(19)</typeDesc>"  # the middle's OWN type -- must be skipped
        "</ddo>"
        "<typeDesc>TypeID(21)</typeDesc>"  # the OUTER's own type -- must win
        "<bounds>(0, 0, 40, 40)</bounds>"
        "</ddo></SL__arrayElement>",
    )
    fp = _parse_front_panel(
        fp_xml, _EMPTY_BD, type_map={19: inner_payload, 21: outer_queue_of_queue}
    )
    (ctrl,) = fp.controls
    assert ctrl.lv_type == outer_queue_of_queue


def test_leaf_glyph_draws_a_refnum_by_its_real_kind() -> None:
    glyph = leaf_glyph("stdRefNum", None, [], DEFAULT_THEME, lv_type=_QUEUE_STRING)
    assert isinstance(glyph, RefnumGlyph)
    assert glyph.kind == "QueueRef"
    assert glyph.terminal is not None  # a registered payload draws a type badge


@pytest.mark.needs_samples
@pytest.mark.skipif(not _NOTIFIER_VI.exists(), reason="icon-editor sample absent")
def test_a_real_corpus_notifier_control_renders_as_a_refnum_not_unknown() -> None:
    """End-to-end against a real VI whose VCTP resolves (GET_Notifier.vi): the
    front panel's Notifier control gets its real ``ref_type`` and renders, no
    crash, never as ``UnknownControlGlyph``."""
    parsed = parse_vi(_NOTIFIER_VI)
    notifier = next(
        c for c in parsed.front_panel.controls if c.control_type == "stdRefNum"
    )
    assert notifier.lv_type is not None and notifier.lv_type.ref_type == "NotifierRef"
    glyph = resolve_glyph(notifier, DEFAULT_THEME)
    assert isinstance(glyph, RefnumGlyph)
    svg = render_front_panel_svg(parsed.front_panel, title=_NOTIFIER_VI.name)
    assert "<svg" in svg


def test_leaf_glyph_draws_a_generic_refnum_when_the_type_never_resolved() -> None:
    """``control_type == 'stdRefNum'`` alone already identifies the SHAPE, so
    an unresolved control (no VCTP) still gets the real frame -- never
    ``UnknownControlGlyph``."""
    glyph = leaf_glyph("stdRefNum", None, [], DEFAULT_THEME, lv_type=None)
    assert isinstance(glyph, RefnumGlyph)
    assert glyph.kind is None
    assert glyph.terminal is None

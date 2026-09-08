"""Tests for the parser module."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.parser import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedConnectorPaneSlot,
    ParsedConstant,
    ParsedDependencyRef,
    ParsedFPTerminal,
    ParsedLoopStructure,
    ParsedNode,
    ParsedTerminalInfo,
    ParsedWire,
    ParsedWiringRule,
    TunnelMapping,
    parse_connector_pane,
    parse_polymorphic_info,
    parse_vi,
    parse_vi_metadata,
)
from lvkit.parser.utils import clean_labview_string, strip_surrounding_quotes
from lvkit.parser.vi import (
    _decode_default_data,
    _decode_element,
    _process_element_terminals,
)

# === Model Dataclass Tests ===


def test_class_refnum_value_decodes_to_class_name_not_handle():
    """A CLASS/LVObject refnum's DataFill decodes to its CLASS NAME (from the
    resolved type), never the opaque handle. The on-disk value is a class-name
    descriptor: ``[4-byte class-count N][ N x [1-byte block-len][1-byte name-len]
    [name] ]`` — for a nested ``lib:class`` the block holds BOTH length-prefixed
    strings (block-len is self-inclusive). We consume the WHOLE descriptor so a
    following cluster field stays aligned: the old fixed-4-byte read left the
    name bytes in place, which a later array field misread as a huge length (a
    290M-element decode runaway). A PLAIN typed refnum (no classname) keeps its
    ``Refnum(<handle>)`` token.

    NOTE: the class's default-OBJECT data (its member instance) can follow the
    descriptor and is class-dependent; that trailing region is not yet consumed
    (see TASKS: class-refnum value under-consumed)."""
    from lvkit.models import LVType, LVTypeKind

    cls = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname="MeasurementLink Measurement Server.lvlib:MeasurementContext.lvclass",
    )
    raw = bytes.fromhex(
        "0000000145284d6561737572656d656e744c696e6b204d6561737572656d656e74"
        "205365727665722e6c766c69621a4d6561737572656d656e74436f6e746578742e"
        "6c76636c6173730000000000000000000000"
    )
    val, size = _decode_element(raw, cls)
    assert val == "MeasurementContext.lvclass"
    assert "Refnum(" not in val
    # count(4) + self-inclusive block(0x45=69) = the whole name descriptor.
    assert size == 73

    # A generic refnum (no classname) keeps the handle token.
    gen = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="Occurrence"
    )
    val2, _ = _decode_element((5).to_bytes(4, "big"), gen)
    assert val2 == "Refnum(5)"


def test_owning_libraries_from_libn_chain(tmp_path):
    """A VI is self-describing: its ``<LIBN>`` block records its ownership chain
    (owning .lvlib/.lvclass, outermost first). parse_vi_metadata captures the
    FULL chain into ``owning_libraries`` (a DISPLAY class-qualifier source),
    while ``library`` stays the outermost and ``qualified_name`` stays the BARE
    resolution key. Present even for an isolated .vi -- no class load, no
    filesystem."""
    from lvkit.parser.metadata import parse_vi_metadata

    xml = tmp_path / "m.xml"
    xml.write_text(
        "<RSRC>"
        '<LVSR><Section Name="Reserve Sessions.vi"/></LVSR>'
        "<LIBN><Section>"
        "<Library>NI Measurement Plug-In SDK.lvlib</Library>"
        "<Library>Measure Call Context.lvclass</Library>"
        "</Section></LIBN>"
        "</RSRC>"
    )
    m = parse_vi_metadata(xml)
    assert m["owning_libraries"] == [
        "NI Measurement Plug-In SDK.lvlib",
        "Measure Call Context.lvclass",
    ]
    assert m["library"] == "NI Measurement Plug-In SDK.lvlib"  # outermost, as before
    assert m["qualified_name"] == "Reserve Sessions.vi"  # bare resolution key


def test_fp_terminal_label_position_captured():
    """An fPTerm's on-diagram label carries its DEVELOPER-PLACED position as a
    direct ``<label>`` child's ``<bounds>``, relative to the terminal. It parses
    through the same LabVIEW ``(top, left, bottom, right)`` -> ``(x1, y1, x2, y2)``
    swap as every rect, so e.g. a raw ``(0, -60, 17, 0)`` is a label 60px to the
    LEFT of the terminal (left of an input), not 60px above. Captured so the
    renderer honors it instead of a fixed 'centered above' offset that collides
    when terminals are close."""
    from lvkit.parser.layout import _fp_label_box

    term = ET.fromstring(
        '<x class="fPTerm" uid="1">'
        "<bounds>(159, 1816, 175, 1848)</bounds>"
        '<label class="label" uid="2"><bounds>(0, -60, 17, 0)</bounds></label>'
        "</x>"
    )
    # (top=0,left=-60,bottom=17,right=0) -> (x1=-60, y1=0, x2=0, y2=17): LEFT.
    assert _fp_label_box(term) == (-60.0, 0.0, 0.0, 17.0)

    bare = ET.fromstring('<x class="fPTerm"><bounds>(0,0,10,10)</bounds></x>')
    assert _fp_label_box(bare) is None


# A synthetic cluster-constant ddo, structurally identical to the real
# corpus shape (verified against the "SMUI Template App Data" cluster in the
# Graphical Test Runner Main UI VI, ddo uid 13666): a `stdClust` ddo whose
# `paneHierarchy`/`zPlaneList` holds each field as its own std*-classed
# element, each with a `partsList` caption ("label" part) + value part(s).
# Field bounds are in a typedef-editor canvas scale UNRELATED to the pane's
# own tiny content-area frame (real corpus fact — see `_cluster_field_geoms`)
# — this fixture reproduces that mismatch (field coords in the thousands,
# pane/ddo bounds in the tens) rather than a toy same-scale layout, so the
# extent-normalize mapping is exercised the same way it is on real VIs.
# "note"'s caption sits to the LEFT of its value part (not the common
# caption-above placement); "inner" is a genuinely NESTED cluster field with
# its own paneHierarchy + 2 sub-fields ("a", "b").
_CLUSTER_CONST_FIXTURE = """
<ddo class="stdClust" uid="100">
  <bounds>(0, 0, 100, 50)</bounds>
  <paneHierarchy class="pane" uid="101">
    <bounds>(5, 5, 95, 45)</bounds>
    <zPlaneList elements="3">
      <SL__arrayElement class="stdNum" uid="200">
        <bounds>(2000, 1000, 2020, 1040)</bounds>
        <partsList elements="2">
          <SL__arrayElement class="label" uid="201">
            <objFlags>0</objFlags>
            <bounds>(-15, 0, 0, 40)</bounds>
            <textRec class="textHair"><text>"count"</text></textRec>
          </SL__arrayElement>
          <SL__arrayElement class="cosm" uid="202">
            <bounds>(0, 0, 20, 40)</bounds>
          </SL__arrayElement>
        </partsList>
      </SL__arrayElement>
      <SL__arrayElement class="stdString" uid="210">
        <bounds>(2050, 1000, 2085, 1050)</bounds>
        <partsList elements="2">
          <SL__arrayElement class="label" uid="211">
            <objFlags>0</objFlags>
            <bounds>(0, 0, 35, 15)</bounds>
            <textRec class="textHair"><text>"note"</text></textRec>
          </SL__arrayElement>
          <SL__arrayElement class="cosm" uid="212">
            <bounds>(0, 15, 35, 50)</bounds>
          </SL__arrayElement>
        </partsList>
      </SL__arrayElement>
      <SL__arrayElement class="stdClust" uid="220">
        <bounds>(2000, 1100, 2060, 1140)</bounds>
        <partsList elements="1">
          <SL__arrayElement class="label" uid="221">
            <objFlags>0</objFlags>
            <bounds>(-15, 0, 0, 40)</bounds>
            <textRec class="textHair"><text>"inner"</text></textRec>
          </SL__arrayElement>
        </partsList>
        <paneHierarchy class="pane" uid="222">
          <bounds>(2, 2, 58, 38)</bounds>
          <zPlaneList elements="2">
            <SL__arrayElement class="stdBool" uid="230">
              <bounds>(9000, 5000, 9020, 5030)</bounds>
              <partsList elements="2">
                <SL__arrayElement class="label" uid="231">
                  <objFlags>0</objFlags>
                  <bounds>(-15, 0, 0, 30)</bounds>
                  <textRec class="textHair"><text>"a"</text></textRec>
                </SL__arrayElement>
                <SL__arrayElement class="cosm" uid="232">
                  <bounds>(0, 0, 20, 30)</bounds>
                </SL__arrayElement>
              </partsList>
            </SL__arrayElement>
            <SL__arrayElement class="stdNum" uid="240">
              <bounds>(9030, 5000, 9060, 5030)</bounds>
              <partsList elements="2">
                <SL__arrayElement class="label" uid="241">
                  <objFlags>0</objFlags>
                  <bounds>(-15, 0, 0, 30)</bounds>
                  <textRec class="textHair"><text>"b"</text></textRec>
                </SL__arrayElement>
                <SL__arrayElement class="cosm" uid="242">
                  <bounds>(0, 0, 30, 30)</bounds>
                </SL__arrayElement>
              </partsList>
            </SL__arrayElement>
          </zPlaneList>
        </paneHierarchy>
      </SL__arrayElement>
    </zPlaneList>
  </paneHierarchy>
</ddo>
"""


def _inside(inner: tuple[float, float, float, float],
            outer: tuple[float, float, float, float]) -> bool:
    tol = 1e-6
    return (
        outer[0] - tol <= inner[0]
        and inner[2] <= outer[2] + tol
        and outer[1] - tol <= inner[1]
        and inner[3] <= outer[3] + tol
    )


def _fit_into(box, native_w, native_h):
    """Test helper mirroring ``ClusterConstantGlyph._draw_real_geometry``'s
    fit-scale: maps a (0, 0)-relative rect at ``(native_w, native_h)`` scale
    into a real ``box``, by a single uniform scale (never per-axis)."""
    bx1, by1, bx2, by2 = box
    scale = min((bx2 - bx1) / native_w, (by2 - by1) / native_h)

    def mapper(rect):
        x1, y1, x2, y2 = rect
        return (
            bx1 + x1 * scale, by1 + y1 * scale,
            bx1 + x2 * scale, by1 + y2 * scale,
        )

    return mapper


def test_cluster_field_geoms_maps_real_field_geometry():
    """``_cluster_field_geoms`` extracts a cluster's REAL geometry (issue #45
    reopened: the old fix synthesized a uniform-row layout instead of reading
    the heap's own field rects) — a ``ClusterGeom`` whose fields are relative
    to the cluster's own (0, 0) origin at its NATIVE size, fit into a real
    drawn box by the caller (a single uniform scale, never per-axis — see
    ``_fit_into``). Assert exactly what the render depends on: (a) fields
    come back in heap order, (b) every mapped field rect lies inside the
    cluster's drawn box, (c) value-box heights are NOT all equal (proves no
    uniform-row stretch), (d) a field whose caption sits left of its value
    keeps that relationship, and a genuinely nested cluster field's own
    sub-fields map inside ITS mapped box too (recursion doesn't escape the
    parent rect) — including through a SECOND fit-scale (the nested cluster's
    own native size differs from the sliver of the parent box it lands in)."""
    from lvkit.parser.layout import _cluster_field_geoms, _rect

    ddo = ET.fromstring(_CLUSTER_CONST_FIXTURE)
    cg = _cluster_field_geoms(ddo)
    assert cg is not None
    drawn_box = (-399.0, -201.0, -275.0, 830.0)  # an arbitrary REAL drawn box
    fit = _fit_into(drawn_box, cg.width, cg.height)
    mapped = {f.name: fit(f.value_rect) for f in cg.fields}

    # (a) heap order preserved.
    assert [f.name for f in cg.fields] == ["count", "note", "inner"]

    # (b) every field's value rect lies inside the drawn box.
    assert all(_inside(r, drawn_box) for r in mapped.values())

    # (c) heights vary — not a uniform-row stretch.
    heights = {round(r[3] - r[1], 6) for r in mapped.values()}
    assert len(heights) > 1

    # (d) "note"'s caption is to the LEFT of its value box.
    note = next(f for f in cg.fields if f.name == "note")
    assert note.label_rect is not None
    mapped_label = fit(note.label_rect)
    assert mapped_label[2] <= mapped[note.name][0] + 1e-6

    # "count"'s caption is ABOVE (not left of) its value box — both real
    # placements the heap can carry are represented, not just one.
    count = next(f for f in cg.fields if f.name == "count")
    assert count.label_rect is not None
    assert fit(count.label_rect)[3] <= mapped[count.name][1] + 1e-6

    # Nested cluster field "inner" recurses into its own full ClusterGeom (2
    # sub-fields), fit (its OWN native size, via a SECOND uniform scale) into
    # inner's own mapped rect — both land INSIDE it.
    inner = next(f for f in cg.fields if f.name == "inner")
    assert inner.nested is not None
    assert [nf.name for nf in inner.nested.fields] == ["a", "b"]
    inner_box = mapped["inner"]
    inner_fit = _fit_into(inner_box, inner.nested.width, inner.nested.height)
    assert all(
        _inside(inner_fit(nf.value_rect), inner_box) for nf in inner.nested.fields
    )

    # No paneHierarchy at all -> no geometry, not a crash.
    leaf = ET.fromstring(
        '<ddo class="stdNum" uid="5"><bounds>(0,0,10,10)</bounds></ddo>'
    )
    assert _cluster_field_geoms(leaf) is None
    # Sanity: fixture rects parse as expected (top,left,bottom,right -> x1,y1,x2,y2).
    assert _rect(leaf) == (0.0, 0.0, 10.0, 10.0)


def test_cluster_shape_resolves_direct_and_typedef_wrapped_clusters():
    """``_cluster_shape`` finds the ``stdClust`` element that defines a
    cluster's shape, whether ``el`` IS the cluster (``class="stdClust"``
    directly) or WRAPS one — a cluster used as a named ``.ctl`` typedef
    control has ddo ``class="typeDef"`` with the real ``stdClust`` embedded
    as one ``partsList`` PART (verified on the corpus: TestResult_Init's
    array-of-clusters element, ddo 569/573). Identified by carrying its own
    ``paneHierarchy`` — never by name/position — so an unrelated ``stdClust``-
    classed part with no pane (shouldn't exist in practice, but proves this
    isn't a blind first-match) is skipped."""
    from lvkit.parser.layout import _cluster_shape

    direct = ET.fromstring(
        '<ddo class="stdClust" uid="1"><bounds>(0,0,10,10)</bounds></ddo>'
    )
    assert _cluster_shape(direct) is direct

    wrapped = ET.fromstring(
        '<ddo class="typeDef" uid="2"><bounds>(0,0,10,10)</bounds>'
        '<partsList elements="2">'
        '<SL__arrayElement class="label" uid="3">'
        "<bounds>(0,0,5,5)</bounds></SL__arrayElement>"
        '<SL__arrayElement class="stdClust" uid="4"><bounds>(0,0,10,10)</bounds>'
        '<paneHierarchy class="pane" uid="5"><bounds>(1,1,9,9)</bounds>'
        '<zPlaneList elements="0"/></paneHierarchy>'
        "</SL__arrayElement>"
        "</partsList>"
        "</ddo>"
    )
    shape = _cluster_shape(wrapped)
    assert shape is not None
    assert shape.get("uid") == "4"

    not_a_cluster = ET.fromstring(
        '<ddo class="stdNum" uid="6"><bounds>(0,0,10,10)</bounds></ddo>'
    )
    assert _cluster_shape(not_a_cluster) is None
    assert _cluster_shape(None) is None

    # A typeDef with a stdClust PART that carries NO paneHierarchy (not a real
    # cluster shape — never seen on the corpus, but must not false-positive).
    no_pane = ET.fromstring(
        '<ddo class="typeDef" uid="7"><bounds>(0,0,10,10)</bounds>'
        '<partsList elements="1">'
        '<SL__arrayElement class="stdClust" uid="8"><bounds>(0,0,10,10)</bounds>'
        "</SL__arrayElement>"
        "</partsList>"
        "</ddo>"
    )
    assert _cluster_shape(no_pane) is None


def test_refnum_type_display_expanded_reads_multicosm_index():
    """``_refnum_type_display_expanded`` is the heap's OWN recorded
    expanded/compact bit for a data-typed ``stdRefNum`` — its
    ``partsList``'s ``multiCosm`` part's ``<index>`` (verified against 5 real
    heap instances: ``ResultChangedRef``/``SuiteChangedRef`` have
    ``<index>1</index>`` — EXPANDED; ``AbortEventRef``/``ExitEventReference``/
    ``TextStream`` omit ``<index>`` entirely — COMPACT). Never a size
    threshold or label match."""
    from lvkit.parser.layout import _refnum_type_display_expanded

    def _refnum(*, nested_ddo: bool, index: str | None) -> ET.Element:
        multicosm = '<SL__arrayElement class="multiCosm" uid="20">'
        if index is not None:
            multicosm += f"<index>{index}</index>"
        multicosm += "</SL__arrayElement>"
        inner = (
            '<ddo class="stdClust" uid="30"><bounds>(0,0,50,50)</bounds></ddo>'
            if nested_ddo
            else ""
        )
        xml = (
            '<ddo class="stdRefNum" uid="10"><bounds>(0,0,48,60)</bounds>'
            f"<partsList elements=\"1\">{multicosm}</partsList>{inner}</ddo>"
        )
        return ET.fromstring(xml)

    # EXPANDED: registered payload + multiCosm index=1.
    assert _refnum_type_display_expanded(
        _refnum(nested_ddo=True, index="1")
    )
    # COMPACT: registered payload, but no <index> element (LabVIEW's own
    # default, 0).
    assert not _refnum_type_display_expanded(
        _refnum(nested_ddo=True, index=None)
    )
    # COMPACT: registered payload with an explicit index=0.
    assert not _refnum_type_display_expanded(
        _refnum(nested_ddo=True, index="0")
    )
    # A plain untyped refnum (no nested <ddo> at all) is already compact —
    # nothing to distinguish, even if a stray index=1 were present.
    assert not _refnum_type_display_expanded(
        _refnum(nested_ddo=False, index="1")
    )
    # Not a refnum at all.
    not_refnum = ET.fromstring(
        '<ddo class="stdClust" uid="40"><bounds>(0,0,10,10)</bounds></ddo>'
    )
    assert not _refnum_type_display_expanded(not_refnum)


# A data-typed EXPANDED refnum field (mirrors GTR's real "ResultChangedRef":
# field raw bounds (521, 399, 727, 510) -> 111x206, nested payload <ddo>
# bounds (5, 31, 201, 106) relative to the field's own origin -> 75x196 at
# local offset (31, 5)), embedded in an outer cluster to also exercise
# ``_cluster_field_geoms``'s threading of ``ClusterFieldGeom.refnum_payload``.
_REFNUM_PAYLOAD_FIXTURE = """
<ddo class="stdClust" uid="600">
  <bounds>(0, 0, 206, 111)</bounds>
  <paneHierarchy class="pane" uid="601">
    <bounds>(2, 2, 204, 109)</bounds>
    <zPlaneList elements="1">
      <SL__arrayElement class="stdRefNum" uid="500">
        <bounds>(1000, 2000, 1206, 2111)</bounds>
        <partsList elements="2">
          <SL__arrayElement class="label" uid="501">
            <objFlags>0</objFlags>
            <bounds>(-15, 0, 0, 60)</bounds>
            <textRec class="textHair"><text>"ResultChangedRef"</text></textRec>
          </SL__arrayElement>
          <SL__arrayElement class="multiCosm" uid="502">
            <index>1</index>
          </SL__arrayElement>
        </partsList>
        <ddo class="stdClust" uid="510">
          <bounds>(5, 31, 201, 106)</bounds>
          <paneHierarchy class="pane" uid="511">
            <bounds>(2, 2, 193, 72)</bounds>
            <zPlaneList elements="2">
              <SL__arrayElement class="udClassDDO" uid="520">
                <bounds>(3000, 4000, 3048, 4048)</bounds>
                <partsList elements="1">
                  <SL__arrayElement class="label" uid="521">
                    <objFlags>8</objFlags>
                    <bounds>(-15, 0, 0, 40)</bounds>
                    <textRec class="textHair"><text>"test"</text></textRec>
                  </SL__arrayElement>
                </partsList>
              </SL__arrayElement>
              <SL__arrayElement class="stdNum" uid="530">
                <bounds>(3060, 4000, 3079, 4019)</bounds>
                <partsList elements="1">
                  <SL__arrayElement class="label" uid="531">
                    <objFlags>8</objFlags>
                    <bounds>(-15, 0, 0, 20)</bounds>
                    <textRec class="textHair"><text>"execution time"</text></textRec>
                  </SL__arrayElement>
                </partsList>
              </SL__arrayElement>
            </zPlaneList>
          </paneHierarchy>
        </ddo>
      </SL__arrayElement>
    </zPlaneList>
  </paneHierarchy>
</ddo>
"""


def test_refnum_payload_layout_extracts_real_offset_and_geometry():
    """``_refnum_payload_layout`` places an EXPANDED refnum's registered
    CLUSTER payload at the heap's own recorded sub-rect within the refnum's
    box (never a centered/invented placement) and recurses into the
    payload's own real per-field geometry via the SAME
    ``_cluster_field_geoms`` a genuine nested cluster field uses."""
    from lvkit.parser.layout import (
        _cluster_field_geoms,
        _cluster_shape,
        _refnum_payload_layout,
    )

    outer = ET.fromstring(_REFNUM_PAYLOAD_FIXTURE)
    refnum_el = outer.find(
        "paneHierarchy/zPlaneList/SL__arrayElement[@class='stdRefNum']"
    )
    assert refnum_el is not None

    payload = _refnum_payload_layout(refnum_el)
    assert payload is not None
    # offset = nested <ddo>'s local (31, 5, 106, 201) as fractions of the
    # refnum's own native (111, 206) box.
    ox1, oy1, ox2, oy2 = payload.offset
    assert ox1 == pytest.approx(31 / 111)
    assert oy1 == pytest.approx(5 / 206)
    assert ox2 == pytest.approx(106 / 111)
    assert oy2 == pytest.approx(201 / 206)
    assert payload.geom.width == pytest.approx(75.0)
    assert payload.geom.height == pytest.approx(196.0)
    assert [f.name for f in payload.geom.fields] == ["test", "execution time"]

    # Threaded through _cluster_field_geoms onto the FIELD's own
    # ClusterFieldGeom, exactly like refnum_expanded.
    outer_cg = _cluster_field_geoms(_cluster_shape(outer))
    assert outer_cg is not None
    field = next(f for f in outer_cg.fields if f.name == "ResultChangedRef")
    assert field.refnum_expanded is True
    assert field.refnum_payload is not None
    assert field.refnum_payload.offset == payload.offset
    assert [f.name for f in field.refnum_payload.geom.fields] == [
        "test", "execution time",
    ]

    # A COMPACT refnum (no <index>1</index>) still resolves no payload
    # placement — nothing to place.
    compact_xml = _REFNUM_PAYLOAD_FIXTURE.replace("<index>1</index>", "")
    compact_outer = ET.fromstring(compact_xml)
    compact_refnum = compact_outer.find(
        "paneHierarchy/zPlaneList/SL__arrayElement[@class='stdRefNum']"
    )
    assert compact_refnum is not None
    # _refnum_payload_layout itself doesn't gate on the expanded bit (that's
    # the CALLER's job) — it still resolves real geometry when a payload
    # <ddo> is present, compact or not.
    assert _refnum_payload_layout(compact_refnum) is not None

    # No nested <ddo> at all (a plain untyped refnum) -> no payload.
    plain = ET.fromstring(
        '<ddo class="stdRefNum" uid="700"><bounds>(0,0,48,36)</bounds></ddo>'
    )
    assert _refnum_payload_layout(plain) is None


# A synthetic array-of-clusters CONSTANT ddo, structurally identical to the
# real corpus shape (verified on TestResult_Init.vi's array constant, heap
# ddo uid 502: `class="indArr"` with a DIRECT `<ddo class="typeDef">` child
# — NOT inside its own `partsList` — wrapping the element's `stdClust`
# shape). The element cluster ("failure") has 2 fields: "test" (a leaf) and
# "error" (itself a NESTED cluster with 3 sub-fields), mirroring the real
# TestResult_Init.vi "failure" cluster exactly (uid 573 -> field "error"
# uid 594 -> status/code/source).
_ARRAY_OF_CLUSTER_FIXTURE = """
<ddo class="indArr" uid="502">
  <bounds>(197, 22, 313, 114)</bounds>
  <partsList elements="1">
    <SL__arrayElement class="label" uid="629">
      <bounds>(-17, 49, 0, 92)</bounds>
    </SL__arrayElement>
  </partsList>
  <ddo class="typeDef" uid="569">
    <bounds>(3, 35, 113, 89)</bounds>
    <partsList elements="1">
      <SL__arrayElement class="stdClust" uid="573">
        <bounds>(0, 0, 110, 54)</bounds>
        <paneHierarchy class="pane" uid="575">
          <bounds>(3, 3, 107, 51)</bounds>
          <zPlaneList elements="2">
            <SL__arrayElement class="udClassDDO" uid="588">
              <bounds>(-279, -396, -231, -348)</bounds>
              <partsList elements="1">
                <SL__arrayElement class="label" uid="590">
                  <objFlags>0</objFlags>
                  <bounds>(-17, 2, 0, 26)</bounds>
                  <textRec class="textHair"><text>"test"</text></textRec>
                </SL__arrayElement>
              </partsList>
            </SL__arrayElement>
            <SL__arrayElement class="stdClust" uid="594">
              <bounds>(-231, -396, -175, -374)</bounds>
              <partsList elements="1">
                <SL__arrayElement class="label" uid="627">
                  <objFlags>0</objFlags>
                  <bounds>(-17, 0, 0, 30)</bounds>
                  <textRec class="textHair"><text>"error"</text></textRec>
                </SL__arrayElement>
              </partsList>
              <paneHierarchy class="pane" uid="596">
                <bounds>(3, 3, 53, 19)</bounds>
                <zPlaneList elements="3">
                  <SL__arrayElement class="stdBool" uid="943">
                    <bounds>(-118, -3, -104, 13)</bounds>
                    <partsList elements="1">
                      <SL__arrayElement class="label" uid="944">
                        <objFlags>0</objFlags>
                        <bounds>(-17, 0, 0, 34)</bounds>
                        <textRec class="textHair"><text>"status"</text></textRec>
                      </SL__arrayElement>
                    </partsList>
                  </SL__arrayElement>
                  <SL__arrayElement class="stdNum" uid="949">
                    <bounds>(-104, -3, -85, 10)</bounds>
                    <partsList elements="1">
                      <SL__arrayElement class="label" uid="950">
                        <objFlags>0</objFlags>
                        <bounds>(-17, 0, 0, 29)</bounds>
                        <textRec class="textHair"><text>"code"</text></textRec>
                      </SL__arrayElement>
                    </partsList>
                  </SL__arrayElement>
                  <SL__arrayElement class="stdString" uid="959">
                    <bounds>(-87, -3, -68, 12)</bounds>
                    <partsList elements="1">
                      <SL__arrayElement class="label" uid="960">
                        <objFlags>0</objFlags>
                        <bounds>(-17, 0, 0, 19)</bounds>
                        <textRec class="textHair"><text>"source"</text></textRec>
                      </SL__arrayElement>
                    </partsList>
                  </SL__arrayElement>
                </zPlaneList>
              </paneHierarchy>
            </SL__arrayElement>
          </zPlaneList>
        </paneHierarchy>
      </SL__arrayElement>
    </partsList>
  </ddo>
</ddo>
"""


def test_array_element_cluster_geometry_typedef_wrapped():
    """The array-constant path: an ``indArr`` ddo's ELEMENT is its own DIRECT
    ``<ddo>`` child (not a ``partsList`` part) — verified on TestResult_Init's
    array-of-clusters constant. When that element resolves (via
    ``_cluster_shape``, through the ``typeDef`` wrapper) to a cluster,
    ``_cluster_field_geoms`` extracts its real geometry exactly like a
    top-level cluster constant, including a genuinely NESTED cluster FIELD
    (``error`` -> status/code/source) — the same recursion, reached through
    the array path instead of a plain cluster constant."""
    from lvkit.parser.layout import _cluster_field_geoms, _cluster_shape

    indarr = ET.fromstring(_ARRAY_OF_CLUSTER_FIXTURE)
    elem_ddo = indarr.find("ddo")
    assert elem_ddo is not None
    assert elem_ddo.get("class") == "typeDef"

    shape = _cluster_shape(elem_ddo)
    assert shape is not None
    assert shape.get("uid") == "573"

    cg = _cluster_field_geoms(shape)
    assert cg is not None
    assert [f.name for f in cg.fields] == ["test", "error"]

    error = next(f for f in cg.fields if f.name == "error")
    assert error.nested is not None
    assert [nf.name for nf in error.nested.fields] == ["status", "code", "source"]

    # Every field (and nested sub-field) lands inside the element's own
    # native box when fit at scale 1.0 (its own real size) — same containment
    # guarantee as a plain cluster constant.
    fit = _fit_into((0.0, 0.0, cg.width, cg.height), cg.width, cg.height)
    mapped = {f.name: fit(f.value_rect) for f in cg.fields}
    assert all(_inside(r, (0.0, 0.0, cg.width, cg.height)) for r in mapped.values())
    inner_fit = _fit_into(mapped["error"], error.nested.width, error.nested.height)
    assert all(
        _inside(inner_fit(nf.value_rect), mapped["error"]) for nf in error.nested.fields
    )


def test_fp_default_with_null_bytes_not_corrupted():
    """Task #78: an FP control's ``DefaultData`` is a length-prefixed binary
    blob whose null/control bytes are serialized as ``&#xNN;``. It must reach
    ``decode_xml_entities_to_bytes`` with those entities intact (quotes stripped
    only). The old path ran it through ``clean_labview_string`` first, which
    DELETES ``&#xNN;`` — dropping the string's 4-byte length prefix and
    corrupting every non-trivial default."""
    # LabVIEW string "hi": 4-byte big-endian length (2) + the bytes.
    serialized = '"&#x00;&#x00;&#x00;&#x02;hi"'
    assert (
        _decode_default_data(
            strip_surrounding_quotes(serialized),
            "stdString",
        )
        == '"hi"'
    )
    # Old path deletes the length prefix -> len < 4 -> value lost.
    assert (
        _decode_default_data(
            clean_labview_string(serialized),
            "stdString",
        )
        != '"hi"'
    )


class TestNode:
    """Tests for the ParsedNode dataclass."""

    def test_node_creation_minimal(self):
        """Test creating a ParsedNode with minimal fields."""
        node = ParsedNode(uid="123", node_type="prim")
        assert node.uid == "123"
        assert node.node_type == "prim"
        assert node.name is None

    def test_node_creation_full(self):
        """Test creating a ParsedNode with all fields."""
        node = ParsedNode(
            uid="456",
            node_type="iUse",
            name="MySubVI.vi",
        )
        assert node.uid == "456"
        assert node.node_type == "iUse"
        assert node.name == "MySubVI.vi"


class TestConstant:
    """Tests for the Constant dataclass."""

    def test_constant_creation_minimal(self):
        """Test creating a Constant with required fields."""
        const = ParsedConstant(uid="c1", type_desc="stdNum", value="3F800000")
        assert const.uid == "c1"
        assert const.type_desc == "stdNum"
        assert const.value == "3F800000"
        assert const.label is None

    def test_constant_creation_with_label(self):
        """Test creating a Constant with a label."""
        const = ParsedConstant(
            uid="c2", type_desc="stdString", value="48656C6C6F", label="greeting"
        )
        assert const.label == "greeting"


class TestWire:
    """Tests for the Wire dataclass."""

    def test_wire_creation(self):
        """Test creating a Wire."""
        wire = ParsedWire(uid="w1", from_term="t1", to_term="t2")
        assert wire.uid == "w1"
        assert wire.from_term == "t1"
        assert wire.to_term == "t2"


class TestFPTerminal:
    """Tests for the FPTerminal dataclass."""

    def test_fp_terminal_input(self):
        """Test creating an input (control) FP terminal."""
        fp = ParsedFPTerminal(
            uid="fp1", fp_dco_uid="dco1", name="Input Value", is_indicator=False
        )
        assert fp.uid == "fp1"
        assert fp.fp_dco_uid == "dco1"
        assert fp.name == "Input Value"
        assert fp.is_indicator is False

    def test_fp_terminal_output(self):
        """Test creating an output (indicator) FP terminal."""
        fp = ParsedFPTerminal(
            uid="fp2", fp_dco_uid="dco2", name="Output Value", is_indicator=True
        )
        assert fp.is_indicator is True


class TestTerminalInfo:
    """Tests for the ParsedTerminalInfo dataclass."""

    def test_terminal_info_input(self):
        """Test creating an input terminal info."""
        info = ParsedTerminalInfo(
            uid="t1",
            parent_uid="node1",
            index=0,
            is_output=False,
            name="x",
        )
        assert info.uid == "t1"
        assert info.parent_uid == "node1"
        assert info.index == 0
        assert info.is_output is False
        assert info.parsed_type is None
        assert info.name == "x"

    def test_terminal_info_output(self):
        """Test creating an output terminal info."""
        info = ParsedTerminalInfo(
            uid="t2",
            parent_uid="node1",
            index=1,
            is_output=True,
        )
        assert info.is_output is True


class TestWiringRule:
    """Tests for the ParsedWiringRule class."""

    def test_wiring_rule_values(self):
        """Test ParsedWiringRule constants."""
        assert ParsedWiringRule.INVALID == 0
        assert ParsedWiringRule.REQUIRED == 1
        assert ParsedWiringRule.RECOMMENDED == 2
        assert ParsedWiringRule.OPTIONAL == 3
        assert ParsedWiringRule.DYNAMIC_DISPATCH == 4


class TestTerminalInvertGating:
    """The DCO objFlags "Not" bit (0x00010000) means "invert this terminal"
    ONLY on Compound Arithmetic (cpdArith) nodes. Other primitives (e.g.
    Increment, primResID 1058) reuse the same bit for an unrelated purpose,
    so ``_process_element_terminals`` must only extract it for cpdArith
    elements and leave every other node class's terminals uninverted."""

    @staticmethod
    def _make_elem(elem_class: str, elem_uid: str, term_uid: str) -> ET.Element:
        # Bit 16 (0x00010000 == 65536) set in the terminal's DCO objFlags.
        xml = f"""
<SL__arrayElement class="{elem_class}" uid="{elem_uid}">
  <termList>
    <SL__arrayElement class="term" uid="{term_uid}">
      <dco uid="dco{term_uid}" class="stdNum">
        <parmIndex>0</parmIndex>
        <objFlags>65536</objFlags>
      </dco>
    </SL__arrayElement>
  </termList>
</SL__arrayElement>
"""
        return ET.fromstring(xml)

    def test_cpd_arith_terminal_is_inverted(self):
        """A cpdArith element's terminal with the bit set is inverted."""
        elem = self._make_elem("cpdArith", "e1", "t1")
        terminal_info: dict[str, ParsedTerminalInfo] = {}
        _process_element_terminals(elem, set(), set(), None, terminal_info, {})
        assert terminal_info["t1"].inverted is True

    def test_non_cpd_arith_terminal_is_not_inverted(self):
        """A non-cpdArith element (e.g. a plain primitive, like Increment)
        with the same bit set must NOT be marked inverted — this bit only
        means "Not" on Compound Arithmetic."""
        elem = self._make_elem("prim", "e2", "t2")
        terminal_info: dict[str, ParsedTerminalInfo] = {}
        _process_element_terminals(elem, set(), set(), None, terminal_info, {})
        assert terminal_info["t2"].inverted is False


class TestLoopTunnelInnerType:
    """A loop tunnel's two faces carry DIFFERENT types when it auto-indexes:
    the OUTER face is the array, the INNER face is the element. Both are
    explicit in the file — the OUTER type sits in the ``lpTun`` dco's own
    ``<typeDesc>`` (on the boundary ``<term>``), and the INNER type in that
    dco's nested ``<innerLpTunDCO>``'s ``<typeDesc>``. The inner face's own
    ``<term>`` (on the loop body) carries only a bare ``<dco uid=.../>``
    back-ref, so its type must be resolved by following that ref to the
    lpTun dco — NOT left None for the graph to guess by racing wires
    (nondeterministic under PYTHONHASHSEED; see _build_tunnel_inner_type_map)."""

    # An auto-indexing for-loop output tunnel: outer = [Boolean] (array),
    # inner = Boolean (element). The lpTun dco (uid d1) lives on the boundary
    # term; the inner term (on the body diagram, under a prim node) is a bare
    # <dco uid="d1"/> back-ref with no type of its own.
    _XML = """
<root>
  <SL__arrayElement class="forLoop" uid="loop1">
    <termList>
      <SL__arrayElement class="term" uid="outer1">
        <dco class="lpTun" uid="d1">
          <typeDesc>TypeID(1)</typeDesc>
          <innerLpTunDCO class="innerLpTun" uid="i1">
            <termList>
              <SL__arrayElement uid="inner1" />
              <SL__arrayElement uid="outer1" />
            </termList>
            <typeDesc>TypeID(2)</typeDesc>
          </innerLpTunDCO>
        </dco>
      </SL__arrayElement>
    </termList>
    <diagramList>
      <SL__arrayElement class="diag" uid="body1">
        <nodeList>
          <SL__arrayElement class="prim" uid="node1">
            <termList>
              <SL__arrayElement class="term" uid="inner1">
                <dco uid="d1" />
              </SL__arrayElement>
            </termList>
          </SL__arrayElement>
        </nodeList>
      </SL__arrayElement>
    </diagramList>
  </SL__arrayElement>
</root>
"""

    @staticmethod
    def _type_map():
        from lvkit.models import LVType, LVTypeKind

        return {
            1: LVType(
                kind=LVTypeKind.ARRAY,
                underlying_type="Array",
                element_type=LVType(
                    kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"
                ),
                dimensions=1,
            ),
            2: LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
        }

    def test_inner_type_map_keys_by_dco_uid(self):
        """The map indexes each lpTun dco uid -> its inner-face typeDesc text."""
        from lvkit.parser.vi import _build_tunnel_inner_type_map

        root = ET.fromstring(self._XML)
        assert _build_tunnel_inner_type_map(root) == {"d1": "TypeID(2)"}

    def test_inner_face_resolves_to_element_outer_to_array(self):
        """Outer face -> array; inner face (bare dco ref) -> element, resolved
        via the map — never None."""
        from lvkit.parser.vi import _build_tunnel_inner_type_map

        root = ET.fromstring(self._XML)
        inner_types = _build_tunnel_inner_type_map(root)
        type_map = self._type_map()
        terminal_info: dict[str, ParsedTerminalInfo] = {}

        loop = root.find(".//*[@uid='loop1']")
        node = root.find(".//*[@uid='node1']")
        assert loop is not None and node is not None
        # Boundary term (owns the full lpTun dco) -> outer array type.
        _process_element_terminals(
            loop,
            set(),
            set(),
            type_map,
            terminal_info,
            inner_types,
        )
        # Body term (bare dco back-ref) -> inner element type.
        _process_element_terminals(
            node,
            set(),
            set(),
            type_map,
            terminal_info,
            inner_types,
        )

        outer = terminal_info["outer1"].parsed_type
        inner = terminal_info["inner1"].parsed_type
        assert outer is not None and outer.kind == "array"
        assert inner is not None and inner.kind == "primitive"
        assert inner.type_name == "Boolean"


class TestCaseTunnelInnerType:
    """A case/select tunnel is PASS-THROUGH: unlike an lpTun it has no nested
    inner dco, so every face -- the boundary <term> that owns the dco, plus one
    bare-dco-ref <term> per frame -- shares the dco's own <typeDesc>. Reading it
    for the bare-ref inner faces stops the graph filling them by racing wires
    (which flipped a cluster's typedef identity across PYTHONHASHSEED)."""

    # A selTun with one outer boundary term (owns the dco, TypeID(7)) and two
    # per-frame inner faces (bare <dco uid="s1"/> refs, one per case frame).
    _XML = """
<root>
  <SL__arrayElement class="caseStruct" uid="case1">
    <termList>
      <SL__arrayElement class="term" uid="outerC">
        <dco class="selTun" uid="s1">
          <typeDesc>TypeID(7)</typeDesc>
          <termList elements="3">
            <SL__arrayElement uid="innerA" />
            <SL__arrayElement uid="innerB" />
            <SL__arrayElement uid="outerC" />
            </termList>
        </dco>
      </SL__arrayElement>
    </termList>
    <diagramList>
      <SL__arrayElement class="diag" uid="frameA">
        <nodeList>
          <SL__arrayElement class="prim" uid="nA">
            <termList>
              <SL__arrayElement class="term" uid="innerA">
                <dco uid="s1" />
                </SL__arrayElement>
              </termList>
          </SL__arrayElement>
        </nodeList>
      </SL__arrayElement>
    </diagramList>
  </SL__arrayElement>
</root>
"""

    def test_map_uses_dco_own_typedesc(self):
        """For a case tunnel the map value is the dco's OWN typeDesc (shared),
        not a nested inner dco's."""
        from lvkit.parser.vi import _build_tunnel_inner_type_map

        assert _build_tunnel_inner_type_map(ET.fromstring(self._XML)) == {
            "s1": "TypeID(7)"
        }

    def test_inner_face_resolves_to_shared_type(self):
        """The bare-ref inner face resolves to the tunnel's shared type -- never
        None (which would force the graph to race wires)."""
        from lvkit.models import LVType, LVTypeKind
        from lvkit.parser.vi import _build_tunnel_inner_type_map

        root = ET.fromstring(self._XML)
        inner_types = _build_tunnel_inner_type_map(root)
        type_map = {7: LVType(kind=LVTypeKind.CLUSTER, underlying_type="Cluster")}
        terminal_info: dict[str, ParsedTerminalInfo] = {}
        node = root.find(".//*[@uid='nA']")
        assert node is not None
        _process_element_terminals(
            node,
            set(),
            set(),
            type_map,
            terminal_info,
            inner_types,
        )
        inner = terminal_info["innerA"].parsed_type
        assert inner is not None and inner.kind == "cluster"


class TestCpdArithOperation:
    """The Compound Arithmetic operation lives in objFlags bits 16-18, NOT in
    dcoFiller (which is per-terminal invert/type data). Corpus-verified:
    0x80000=add, 0x90000=multiply, 0xA0000=and, 0xB0000=or, 0xC0000=xor.
    Unknown codes / a missing objFlags map to the 'unsupported' sentinel
    (the parser never raises; codegen is the layer that fails loud)."""

    def _op(self, objflags):
        import xml.etree.ElementTree as ET

        from lvkit.parser.node_types import CpdArithHandler

        of = "" if objflags is None else f"<objFlags>{objflags}</objFlags>"
        xml = f'<SL__arrayElement class="cpdArith" uid="1">{of}</SL__arrayElement>'
        return CpdArithHandler()._extract_operation(ET.fromstring(xml))

    def test_objflags_operation_codes(self):
        # Real objFlags values observed across the OpenG corpus.
        assert self._op(str(0x80000)) == "add"  # Trim / Reshape / MD5 FGHI
        assert self._op(str(0xA0000)) == "and"  # every "...Changed" detector
        assert self._op(str(0xB0000)) == "or"  # Create Dir if Non-Existant
        assert self._op(str(0xC0000)) == "xor"  # MD5 H function

    def test_multiply_enum_slot(self):
        # No corpus instance, but it occupies LabVIEW's enum slot (code 1).
        assert self._op(str(0x90000)) == "multiply"

    def test_dcofiller_does_not_select_op(self):
        # dcoFiller 256 co-occurs with add/and/or/xor -- objFlags is the source
        # of truth. Bit 19 (0x80000) is an always-set marker, masked out by &7.
        assert self._op(str(0xA0000)) == "and"  # not "add", despite old bug

    def test_unknown_or_missing_is_unsupported_sentinel_not_raise(self):
        # The parser must degrade gracefully, never raise; codegen fails loud.
        assert self._op(str(0xF0000)) == "unsupported"  # code 7
        assert self._op(None) == "unsupported"  # objFlags absent


class TestTunnelMapping:
    """Tests for the TunnelMapping dataclass."""

    def test_tunnel_with_paired_terminal(self):
        """Test tunnel with paired terminal (shift register)."""
        tunnel = TunnelMapping(
            outer_terminal_uid="outer5",
            inner_terminal_uid="inner5",
            tunnel_type="lSR",
            paired_terminal_uid="paired5",
        )
        assert tunnel.paired_terminal_uid == "paired5"


class TestLoopStructure:
    """Tests for the ParsedLoopStructure dataclass."""

    def test_while_loop_creation(self):
        """Test creating a while loop structure."""
        loop = ParsedLoopStructure(
            uid="loop1",
            loop_type="whileLoop",
            boundary_terminal_uids=["bt1", "bt2"],
            inner_diagram_uid="inner1",
            inner_node_uids=["n1", "n2"],
            stop_condition_terminal_uid="stop1",
        )
        assert loop.uid == "loop1"
        assert loop.loop_type == "whileLoop"
        assert len(loop.boundary_terminal_uids) == 2
        assert loop.stop_condition_terminal_uid == "stop1"

    def test_for_loop_creation(self):
        """Test creating a for loop structure."""
        loop = ParsedLoopStructure(
            uid="loop2",
            loop_type="forLoop",
            tunnels=[
                TunnelMapping(
                    outer_terminal_uid="o1",
                    inner_terminal_uid="i1",
                    tunnel_type="lpTun",
                ),
                TunnelMapping(
                    outer_terminal_uid="o2",
                    inner_terminal_uid="i2",
                    tunnel_type="lMax",
                ),
            ],
        )
        assert loop.loop_type == "forLoop"
        assert len(loop.tunnels) == 2


class TestConnectorPaneSlot:
    """Tests for the ParsedConnectorPaneSlot dataclass."""

    def test_slot_empty(self):
        """Test creating an empty slot."""
        slot = ParsedConnectorPaneSlot(index=0)
        assert slot.index == 0
        assert slot.fp_dco_uid is None

    def test_slot_connected(self):
        """Test creating a connected slot."""
        slot = ParsedConnectorPaneSlot(index=3, fp_dco_uid="dco123")
        assert slot.index == 3
        assert slot.fp_dco_uid == "dco123"


class TestConnectorPane:
    """Tests for the ParsedConnectorPane dataclass."""

    def test_connector_pane_creation(self):
        """Test creating a connector pane."""
        pane = ParsedConnectorPane(
            pattern_id=4,
            slots=[
                ParsedConnectorPaneSlot(index=0, fp_dco_uid="dco1"),
                ParsedConnectorPaneSlot(index=1),
                ParsedConnectorPaneSlot(index=2, fp_dco_uid="dco2"),
            ],
        )
        assert pane.pattern_id == 4
        assert len(pane.slots) == 3

    def test_get_connected_uids(self):
        """Test getting connected UIDs from connector pane."""
        pane = ParsedConnectorPane(
            pattern_id=4,
            slots=[
                ParsedConnectorPaneSlot(index=0, fp_dco_uid="dco1"),
                ParsedConnectorPaneSlot(index=1),  # Empty slot
                ParsedConnectorPaneSlot(index=2, fp_dco_uid="dco2"),
            ],
        )
        connected = pane.get_connected_uids()
        assert len(connected) == 2
        assert "dco1" in connected
        assert "dco2" in connected


class TestDependencyRef:
    """Tests for the ParsedDependencyRef dataclass."""

    def test_vilib_path_ref(self):
        """Test a vi.lib path reference."""
        ref = ParsedDependencyRef(
            name="File Exists.vi",
            path_tokens=["<vilib>", "Utility", "file.llb", "File Exists.vi"],
        )
        assert ref.name == "File Exists.vi"
        assert ref.path_tokens[0] == "<vilib>"
        assert ref.get_relative_path() == "Utility/file.llb/File Exists.vi"

    def test_userlib_path_ref(self):
        """Test a user.lib path reference."""
        ref = ParsedDependencyRef(
            name="MyHelper.vi",
            path_tokens=["<userlib>", "MyLib", "MyHelper.vi"],
        )
        assert ref.path_tokens[0] == "<userlib>"
        assert ref.get_relative_path() == "MyLib/MyHelper.vi"

    def test_local_path_ref(self):
        """Test a local path reference."""
        ref = ParsedDependencyRef(
            name="Local.vi",
            path_tokens=["SubFolder", "Local.vi"],
        )
        assert ref.path_tokens[0] not in ("<vilib>", "<userlib>")
        assert ref.get_relative_path() == "SubFolder/Local.vi"


class TestBlockDiagram:
    """Tests for the ParsedBlockDiagram dataclass."""

    def test_block_diagram_creation(self):
        """Test creating a ParsedBlockDiagram."""
        bd = ParsedBlockDiagram(
            nodes=[ParsedNode(uid="n1", node_type="prim")],
            constants=[ParsedConstant(uid="c1", type_desc="stdNum", value="0")],
            wires=[ParsedWire(uid="w1", from_term="t1", to_term="t2")],
        )
        assert len(bd.nodes) == 1
        assert len(bd.constants) == 1
        assert len(bd.wires) == 1
        assert bd.loops == []

    def test_get_node(self):
        """Test getting a node by UID."""
        node1 = ParsedNode(uid="n1", node_type="prim", name="Add")
        node2 = ParsedNode(uid="n2", node_type="iUse", name="SubVI.vi")
        bd = ParsedBlockDiagram(nodes=[node1, node2], constants=[], wires=[])

        found = bd.get_node("n1")
        assert found is not None
        assert found.name == "Add"

        not_found = bd.get_node("n99")
        assert not_found is None

    def test_get_parent_uid(self):
        """Test getting parent UID for a terminal."""
        bd = ParsedBlockDiagram(
            nodes=[],
            constants=[],
            wires=[],
            terminal_info={
                "t1": ParsedTerminalInfo(
                    uid="t1", parent_uid="node1", index=0, is_output=False
                ),
            },
        )
        assert bd.get_parent_uid("t1") == "node1"
        assert bd.get_parent_uid("t99") is None

    def test_get_loop(self):
        """Test getting a loop by UID."""
        loop = ParsedLoopStructure(uid="loop1", loop_type="whileLoop")
        bd = ParsedBlockDiagram(nodes=[], constants=[], wires=[], loops=[loop])

        found = bd.get_loop("loop1")
        assert found is not None
        assert found.loop_type == "whileLoop"

        not_found = bd.get_loop("loop99")
        assert not_found is None

    def test_get_tunnel_mapping(self):
        """Test getting tunnel mapping for a terminal."""
        tunnel = TunnelMapping(
            outer_terminal_uid="outer1",
            inner_terminal_uid="inner1",
            tunnel_type="lpTun",
        )
        loop = ParsedLoopStructure(uid="loop1", loop_type="forLoop", tunnels=[tunnel])
        bd = ParsedBlockDiagram(nodes=[], constants=[], wires=[], loops=[loop])

        # Find by outer terminal
        found_outer = bd.get_tunnel_mapping("outer1")
        assert found_outer is not None
        assert found_outer.tunnel_type == "lpTun"

        # Find by inner terminal
        found_inner = bd.get_tunnel_mapping("inner1")
        assert found_inner is not None
        assert found_inner.tunnel_type == "lpTun"

        # Not found
        not_found = bd.get_tunnel_mapping("other")
        assert not_found is None


# === XML Parsing Tests ===


class TestParseVI:
    """Tests for parse_vi function."""

    def test_parse_minimal_block_diagram(self, tmp_path: Path):
        """Test parsing a minimal block diagram XML."""
        xml_content = """<?xml version="1.0"?>
<root>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert bd is not None
        assert len(bd.nodes) == 0
        assert len(bd.constants) == 0
        assert len(bd.wires) == 0

    def test_parse_with_primitive(self, tmp_path: Path):
        """Test parsing a block diagram with a primitive node."""
        xml_content = """<?xml version="1.0"?>
<root>
    <node class="prim" uid="prim1">
        <primIndex>10</primIndex>
        <primResID>1419</primResID>
        <termList></termList>
    </node>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.nodes) == 1
        from lvkit.parser.node_types import PrimitiveNode

        node = bd.nodes[0]
        assert node.uid == "prim1"
        assert node.node_type == "prim"
        assert isinstance(node, PrimitiveNode)
        assert node.prim_index == 10
        assert node.prim_res_id == 1419

    def test_parse_with_subvi(self, tmp_path: Path):
        """Test parsing a block diagram with a SubVI node."""
        xml_content = """<?xml version="1.0"?>
<root>
    <node class="iUse" uid="subvi1">
        <label><textRec><text>"My Helper.vi"</text></textRec></label>
        <termList></termList>
    </node>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.nodes) == 1
        node = bd.nodes[0]
        assert node.uid == "subvi1"
        assert node.node_type == "iUse"
        assert node.name == "My Helper.vi"

    def test_parse_property_node_implicit_vs_explicit(self, tmp_path: Path):
        """A Property Node's ``bound_control_uid`` (task #51) comes ONLY from
        its own DIRECT ``<ddo>`` CHILD -- a sibling of ``<termList>``, never a
        part of it, never inferred from the ``<label>`` text. When present,
        ``_parse_block_diagram`` resolves ``bound_control_type`` from that
        uid's ddo in the FRONT-PANEL heap (never guessed, never done in
        ``render/``). Absent entirely -> both fields stay empty/None, exactly
        like the existing (unbound) propNode behavior."""
        bd_xml = """<?xml version="1.0"?>
<root>
    <node class="propNode" uid="900">
        <termList elements="3">
            <SL__arrayElement class="term" uid="901">
                <dco class="hGrowCItem" uid="911"><typeDesc>TypeID(1)</typeDesc></dco>
            </SL__arrayElement>
            <SL__arrayElement class="term" uid="902">
                <dco class="hGrowCItem" uid="912"><typeDesc>TypeID(2)</typeDesc></dco>
            </SL__arrayElement>
            <SL__arrayElement class="term" uid="903">
                <dco class="propItem" uid="913">
                    <typeDesc>TypeID(3)</typeDesc>
                    <propList elements="1">
                        <SL__arrayElement class="propItemInfo" uid="914">
                            <PropItemName>"Disabled"</PropItemName>
                            <PropItemCode>1</PropItemCode>
                        </SL__arrayElement>
                    </propList>
                </dco>
            </SL__arrayElement>
        </termList>
        <label class="label" uid="920">
            <textRec class="textHair"><text>"Abort"</text></textRec>
        </label>
        <dcoList elements="1"><SL__arrayElement uid="913" /></dcoList>
        <nodeName>"Bool"</nodeName>
        <oMId>0045</oMId>
        <ddo uid="950" />
    </node>
    <node class="propNode" uid="800">
        <termList elements="1">
            <SL__arrayElement class="term" uid="801">
                <dco class="propItem" uid="811">
                    <typeDesc>TypeID(4)</typeDesc>
                    <propList elements="1">
                        <SL__arrayElement class="propItemInfo" uid="812">
                            <PropItemName>"Name"</PropItemName>
                            <PropItemCode>500</PropItemCode>
                        </SL__arrayElement>
                    </propList>
                </dco>
            </SL__arrayElement>
        </termList>
        <dcoList elements="1"><SL__arrayElement uid="811" /></dcoList>
        <nodeName>"VI"</nodeName>
        <oMId>0007</oMId>
    </node>
    <signalList></signalList>
</root>"""
        fp_xml = """<?xml version="1.0"?>
<root>
    <ddo class="stdBool" uid="950">
        <bounds>(-2, 79, 38, 119)</bounds>
    </ddo>
</root>"""
        bd_file = tmp_path / "test_BDHb.xml"
        bd_file.write_text(bd_xml)
        fp_file = tmp_path / "test_FPHb.xml"
        fp_file.write_text(fp_xml)

        vi = parse_vi(bd_xml=bd_file, fp_xml=fp_file)
        nodes = {n.uid: n for n in vi.block_diagram.nodes}

        from lvkit.models import LVTypeKind
        from lvkit.parser.node_types import PropertyNode

        implicit = nodes["900"]
        assert isinstance(implicit, PropertyNode)
        assert implicit.label == "Abort"
        assert implicit.bound_control_uid == "950"
        assert implicit.bound_control_type is not None
        assert implicit.bound_control_type.kind == LVTypeKind.PRIMITIVE
        assert implicit.bound_control_type.underlying_type == "Boolean"

        explicit = nodes["800"]
        assert isinstance(explicit, PropertyNode)
        assert explicit.label is None
        assert explicit.bound_control_uid == ""
        assert explicit.bound_control_type is None

    def test_parse_event_reg_node_growable_rows(self, tmp_path: Path):
        """A Register-For-Events node (task #56, class="eventRegNode") gets
        its own display name from ``<nodeName>`` (the SAME field
        PropertyNode/InvokeNode already use -- NEVER a hard-coded
        "Register For Events" guess: every real corpus instance records
        "Reg Events"), and its GROWABLE event-source rows come from
        ``<dcoList>`` re-expressed as terminal uids (the same
        ``_dco_list_terminal_uids`` convention), fully data-driven -- a
        2-row node gets exactly 2 ``event_row_terminal_uids``, in heap
        order."""
        bd_xml = """<?xml version="1.0"?>
<root>
    <node class="eventRegNode" uid="700">
        <termList elements="6">
            <SL__arrayElement class="term" uid="701">
                <dco class="hGrowCItem" uid="711"><typeDesc>TypeID(1)</typeDesc></dco>
            </SL__arrayElement>
            <SL__arrayElement class="term" uid="702">
                <dco class="hGrowCItem" uid="712"><typeDesc>TypeID(2)</typeDesc></dco>
            </SL__arrayElement>
            <SL__arrayElement class="term" uid="703">
                <dco class="hGrowCItem" uid="713"><typeDesc>TypeID(3)</typeDesc></dco>
            </SL__arrayElement>
            <SL__arrayElement class="term" uid="704">
                <dco class="hGrowCItem" uid="714"><typeDesc>TypeID(4)</typeDesc></dco>
            </SL__arrayElement>
            <SL__arrayElement class="term" uid="705">
                <dco class="eventRegItem" uid="715">
                    <typeDesc>TypeID(5)</typeDesc>
                    <code>03E8</code>
                </dco>
            </SL__arrayElement>
            <SL__arrayElement class="term" uid="706">
                <dco class="eventRegItem" uid="716">
                    <typeDesc>TypeID(6)</typeDesc>
                    <code>03E8</code>
                </dco>
            </SL__arrayElement>
        </termList>
        <dcoList elements="2">
            <SL__arrayElement uid="715" />
            <SL__arrayElement uid="716" />
        </dcoList>
        <nodeName>"Reg Events"</nodeName>
        <oMId>0044</oMId>
    </node>
    <signalList></signalList>
</root>"""
        bd_file = tmp_path / "test_BDHb.xml"
        bd_file.write_text(bd_xml)

        vi = parse_vi(bd_xml=bd_file)
        nodes = {n.uid: n for n in vi.block_diagram.nodes}

        from lvkit.parser.node_types import EventRegNode

        node = nodes["700"]
        assert isinstance(node, EventRegNode)
        assert node.object_name == "Reg Events"
        assert node.event_row_terminal_uids == ["705", "706"]

    def test_parse_with_wires(self, tmp_path: Path):
        """Test parsing a block diagram with wires."""
        xml_content = """<?xml version="1.0"?>
<root>
    <signalList>
        <SL__arrayElement class="signal" uid="sig1">
            <termList>
                <SL__arrayElement uid="t1"/>
                <SL__arrayElement uid="t2"/>
            </termList>
        </SL__arrayElement>
    </signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.wires) == 1
        wire = bd.wires[0]
        assert wire.from_term == "t1"
        assert wire.to_term == "t2"

    def test_parse_with_multiway_wire(self, tmp_path: Path):
        """Test parsing a wire with multiple destinations."""
        xml_content = """<?xml version="1.0"?>
<root>
    <signalList>
        <SL__arrayElement class="signal" uid="sig1">
            <termList>
                <SL__arrayElement uid="source"/>
                <SL__arrayElement uid="dest1"/>
                <SL__arrayElement uid="dest2"/>
            </termList>
        </SL__arrayElement>
    </signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.wires) == 2
        assert bd.wires[0].from_term == "source"
        assert bd.wires[0].to_term == "dest1"
        assert bd.wires[1].from_term == "source"
        assert bd.wires[1].to_term == "dest2"

    def test_parse_with_constant(self, tmp_path: Path):
        """Test parsing a block diagram with a constant."""
        # Constants are found as terminals with dco[@class='bDConstDCO']
        xml_content = """<?xml version="1.0"?>
<root>
    <nodeList>
        <SL__arrayElement class="term" uid="const1">
            <dco class="bDConstDCO">
                <typeDesc>stdNum</typeDesc>
                <ConstValue>3F800000</ConstValue>
            </dco>
        </SL__arrayElement>
    </nodeList>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.constants) == 1
        const = bd.constants[0]
        assert const.uid == "const1"
        assert const.type_desc == "stdNum"
        assert const.value == "3F800000"

    def test_parse_constant_from_default_data(self, tmp_path: Path):
        """Real-world VIs carry a constant's value in ``DefaultData``, not
        ``ConstValue`` (task #59 finding — pylabview never emits
        ``ConstValue`` for a ``bDConstDCO`` in any corpus VI checked).
        ``DefaultData`` is a quoted string mixing literal printable bytes
        with ``&#xNN;``-entity-encoded non-printable ones (already
        unescaped once by XML parsing, so ``&amp;#x00;`` in the source
        arrives here as literal ``&#x00;``) — must decode to hex, not be
        used as hex text directly. Also covers the numeric display-format
        string (``%.0x`` = hex), which lives on the same DCO's
        ``ddo/partsList/numLabel/format``."""
        xml_content = """<?xml version="1.0"?>
<root>
    <nodeList>
        <SL__arrayElement class="term" uid="const1">
            <dco class="bDConstDCO">
                <typeDesc>TypeID(1)</typeDesc>
                <ddo class="stdNum" uid="1">
                    <partsList elements="1">
                        <SL__arrayElement class="numLabel" uid="2">
                            <format>"%.0x"</format>
                        </SL__arrayElement>
                    </partsList>
                </ddo>
                <DefaultData>"&amp;#x00;&amp;#x00;&amp;#x00;&amp;#x02;"</DefaultData>
            </dco>
        </SL__arrayElement>
    </nodeList>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.constants) == 1
        const = bd.constants[0]
        assert const.value == "00000002"
        assert const.display_format == "%.0x"

    def test_parse_with_fp_terminals(self, tmp_path: Path):
        """Test parsing a block diagram with front panel terminals."""
        xml_content = """<?xml version="1.0"?>
<root>
    <node class="fPTerm" uid="fp1">
        <dco uid="dco1"/>
        <label><textRec><text>"Input"</text></textRec></label>
    </node>
    <node class="fPTerm" uid="fp2">
        <dco uid="dco2"/>
        <label><textRec><text>"Output"</text></textRec></label>
    </node>
    <signalList>
        <SL__arrayElement class="signal" uid="sig1">
            <termList>
                <SL__arrayElement uid="t1"/>
                <SL__arrayElement uid="fp2"/>
            </termList>
        </SL__arrayElement>
    </signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.fp_terminals) == 2

        # fp1 has no incoming wire - it's an input
        fp1 = next(fp for fp in bd.fp_terminals if fp.uid == "fp1")
        assert fp1.is_indicator is False

        # fp2 has an incoming wire - it's an output
        fp2 = next(fp for fp in bd.fp_terminals if fp.uid == "fp2")
        assert fp2.is_indicator is True


class TestParseConnectorPane:
    """Tests for parse_connector_pane function."""

    def test_parse_connector_pane(self, tmp_path: Path):
        """Test parsing a connector pane from FP XML."""
        xml_content = """<?xml version="1.0"?>
<root>
    <conPane class="conPane">
        <conId>4</conId>
        <cons>
            <SL__arrayElement class="ConpaneConnection" index="0">
                <ConnectionDCO uid="dco1"/>
            </SL__arrayElement>
            <SL__arrayElement class="ConpaneConnection" index="2">
                <ConnectionDCO uid="dco2"/>
            </SL__arrayElement>
        </cons>
    </conPane>
</root>"""
        xml_file = tmp_path / "test_FPHb.xml"
        xml_file.write_text(xml_content)

        pane = parse_connector_pane(xml_file)
        assert pane is not None
        assert pane.pattern_id == 4
        assert len(pane.slots) == 2
        assert pane.slots[0].index == 0
        assert pane.slots[0].fp_dco_uid == "dco1"
        assert pane.slots[1].index == 2
        assert pane.slots[1].fp_dco_uid == "dco2"

    def test_parse_connector_pane_missing(self, tmp_path: Path):
        """Test parsing when no connector pane exists."""
        xml_content = """<?xml version="1.0"?>
<root></root>"""
        xml_file = tmp_path / "test_FPHb.xml"
        xml_file.write_text(xml_content)

        pane = parse_connector_pane(xml_file)
        assert pane is None


class TestParseSubviPaths:
    """Tests for VIVI-ref extraction via parse_vi(...).metadata.dependency_refs."""

    _MIN_BD = """<?xml version="1.0"?>
<root>
    <signalList></signalList>
</root>"""

    def test_parse_vilib_subvi(self, tmp_path: Path):
        """Test parsing a vi.lib SubVI reference."""
        main_xml_content = """<?xml version="1.0"?>
<root>
    <LIvi>
        <Section>
            <VIVI>
                <LinkSaveQualName><String>File Exists.vi</String></LinkSaveQualName>
                <LinkSavePathRef>
                    <String>&lt;vilib&gt;</String>
                    <String>Utility</String>
                    <String>file.llb</String>
                    <String>File Exists.vi</String>
                </LinkSavePathRef>
            </VIVI>
        </Section>
    </LIvi>
</root>"""
        bd_file = tmp_path / "test_BDHb.xml"
        bd_file.write_text(self._MIN_BD)
        main_file = tmp_path / "test.xml"
        main_file.write_text(main_xml_content)

        refs = parse_vi(bd_xml=bd_file, main_xml=main_file).metadata.dependency_refs
        assert len(refs) == 1
        ref = refs[0]
        assert ref.name == "File Exists.vi"
        assert ref.path_tokens[0] == "<vilib>"

    def test_parse_userlib_subvi(self, tmp_path: Path):
        """Test parsing a user.lib SubVI reference."""
        main_xml_content = """<?xml version="1.0"?>
<root>
    <LIvi>
        <Section>
            <VIVI>
                <LinkSaveQualName><String>MyHelper__ogtk.vi</String></LinkSaveQualName>
                <LinkSavePathRef>
                    <String>&lt;userlib&gt;</String>
                    <String>_OpenG.lib</String>
                    <String>MyHelper__ogtk.vi</String>
                </LinkSavePathRef>
            </VIVI>
        </Section>
    </LIvi>
</root>"""
        bd_file = tmp_path / "test_BDHb.xml"
        bd_file.write_text(self._MIN_BD)
        main_file = tmp_path / "test.xml"
        main_file.write_text(main_xml_content)

        refs = parse_vi(bd_xml=bd_file, main_xml=main_file).metadata.dependency_refs
        assert len(refs) == 1
        ref = refs[0]
        assert ref.name == "MyHelper__ogtk.vi"
        assert ref.path_tokens[0] == "<userlib>"


class TestParseViMetadata:
    """Tests for parse_vi_metadata function."""

    def test_parse_basic_metadata(self, tmp_path: Path):
        """Test parsing basic VI metadata."""
        xml_content = """<?xml version="1.0"?>
<root>
    <LVSR><Section Name="My Test VI.vi"/></LVSR>
    <LIBN><Section><Library>TestLib</Library></Section></LIBN>
    <LIvi><Section><LVIN Unk1="TestLib:My Test VI.vi"/></Section></LIvi>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        metadata = parse_vi_metadata(xml_file)
        assert metadata["name"] == "My Test VI.vi"
        assert metadata["library"] == "TestLib"
        assert metadata["qualified_name"] == "TestLib:My Test VI.vi"

    def test_parse_with_description(self, tmp_path: Path):
        """Test parsing VI with description."""
        xml_content = """<?xml version="1.0"?>
<root>
    <LVSR><Section Name="Test.vi"/></LVSR>
    <DSTM><Section><String>This is a test VI</String></Section></DSTM>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        metadata = parse_vi_metadata(xml_file)
        assert metadata["description"] == "This is a test VI"


class TestParsePolymorphicInfo:
    """Tests for parse_polymorphic_info function."""

    def test_non_polymorphic_vi(self, tmp_path: Path):
        """Test parsing a non-polymorphic VI."""
        xml_content = """<?xml version="1.0"?>
<root>
    <VCTP><Section><TypeDesc Type="Function"/></Section></VCTP>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        tree = ET.parse(xml_file)
        root = tree.getroot()

        info = parse_polymorphic_info(root)
        assert info["is_polymorphic"] is False
        assert info["variants"] == []
        assert info["selectors"] == []

    def test_polymorphic_vi(self, tmp_path: Path):
        """Test parsing a polymorphic VI."""
        xml_content = """<?xml version="1.0"?>
<root>
    <LVSR><Section><Execution2 AllowPolyTypeAdapt="1"/></Section></LVSR>
    <VCTP><Section><TypeDesc Type="PolyVI"/></Section></VCTP>
    <CPST><Section>
        <String>I8</String>
        <String>I16</String>
        <String>I32</String>
    </Section></CPST>
    <LIvi><Section>
        <VIVI><LinkSaveQualName><String>Add I8.vi</String></LinkSaveQualName></VIVI>
        <VIVI><LinkSaveQualName><String>Add I16.vi</String></LinkSaveQualName></VIVI>
        <VIVI><LinkSaveQualName><String>Add I32.vi</String></LinkSaveQualName></VIVI>
    </Section></LIvi>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        tree = ET.parse(xml_file)
        root = tree.getroot()

        info = parse_polymorphic_info(root)
        assert info["is_polymorphic"] is True
        assert len(info["selectors"]) == 3
        assert "I8" in info["selectors"]
        assert len(info["variants"]) == 3
        assert "Add I8.vi" in info["variants"]


# === Integration Tests with Real VIs ===


class TestRealVIParsing:
    """Integration tests using real VI files from samples."""

    @pytest.fixture
    def sample_vi_path(self) -> Path | None:
        """Get path to a sample VI if available."""
        path = Path(
            ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
            "Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi"
        )
        if path.exists():
            return path
        return None

    def test_parse_real_vi(self, sample_vi_path: Path | None):
        """Test parsing a real VI file."""
        if sample_vi_path is None:
            pytest.skip("Sample VI not available")

        from lvkit.extractor import extract_vi_xml

        bd_xml, fp_xml, main_xml = extract_vi_xml(sample_vi_path)

        # Parse VI
        vi = parse_vi(bd_xml=bd_xml)
        bd = vi.block_diagram
        assert bd is not None
        assert len(bd.nodes) > 0 or len(bd.constants) > 0 or len(bd.wires) > 0

        # Parse connector pane if available
        if fp_xml and fp_xml.exists():
            pane = parse_connector_pane(fp_xml)
            # May or may not have a connector pane
            if pane is not None:
                assert pane.pattern_id >= 0

        # Parse metadata if available
        if main_xml and main_xml.exists():
            metadata = parse_vi_metadata(main_xml)
            assert "name" in metadata or "qualified_name" in metadata


def test_every_registered_handler_is_reachable_by_extraction() -> None:
    """Guard against silently dropped nodes: every class with a registered
    node handler MUST also appear in OPERATION_NODE_CLASSES, which is what
    _extract_nodes iterates. A handler that is registered but not whitelisted
    is never reached, so those nodes vanish from the diagram (the aReplace /
    'Replace Array Subset' drop). This test fails loudly if the two lists
    diverge again.

    "commentNode" is the one intentional exception: class="commentNode" is
    used for BOTH a Disable structure (has subdiagrams) and, in principle, a
    plain free-text comment -- so it can't be bucketed unconditionally like
    every other OPERATION_NODE_CLASSES member (that would misparse a plain
    comment as a structure). _extract_nodes instead reaches it through a
    separate, gated pass keyed on
    parser.nodes.disable.is_disable_structure -- see that module's docstring
    and DisableStructureHandler's."""
    from lvkit.parser.constants import OPERATION_NODE_CLASSES
    from lvkit.parser.node_types import NODE_HANDLERS

    reachable = set(OPERATION_NODE_CLASSES) | {"commentNode"}
    unreachable = sorted(set(NODE_HANDLERS) - reachable)
    assert not unreachable, (
        f"Node handlers registered but not in OPERATION_NODE_CLASSES "
        f"(they will be silently dropped by _extract_nodes): {unreachable}"
    )


def test_every_dco_keyed_primitive_template_is_reachable() -> None:
    """Guard against the OTHER half of the drop class (#55): a
    primitives.json node_types entry can be wrong in TWO independent ways --
    (1) its XML class was never added to OPERATION_NODE_CLASSES (nodes are
    silently dropped by the parser, the previous test's job), or (2) it was
    added but its template ("python_code") encodes the wrong semantics
    (aInsert/aReshape both had this on top of (1) -- see #55). This test
    only guards (1), generalized: any node_types entry that documents real
    per-terminal ``dco_ref`` tags is, by construction, a genuine XML
    block-diagram node class keyed to DCO terminals (aBuild, aDelete,
    aIndx, aInit, aInsert, aReplace, aReshape, subset) -- as opposed to the
    other node_types entries (cpdArith, bundle, unbundle, arrayIdx,
    arrayRepl, strIdx, strRepl, strLen, concat, split), which are name-only
    stubs with no ``terminals`` list at all, used for primResID-keyed
    primitive display names rather than XML-class extraction. Every
    dco_ref-carrying entry's class name MUST be in OPERATION_NODE_CLASSES,
    or its node is silently dropped exactly like aInsert/aReshape were.
    """
    from lvkit._data import data_dir
    from lvkit.parser.constants import OPERATION_NODE_CLASSES

    with open(data_dir() / "primitives.json") as f:
        data = json.load(f)

    dco_keyed_classes = sorted(
        node_class
        for node_class, info in data.get("node_types", {}).items()
        if any("dco_ref" in t for t in info.get("terminals", []))
    )
    assert dco_keyed_classes, "sanity: expected some dco_ref-keyed entries"

    unreachable = sorted(set(dco_keyed_classes) - set(OPERATION_NODE_CLASSES))
    assert not unreachable, (
        "primitives.json node_types entries with real dco_ref terminals "
        f"but missing from OPERATION_NODE_CLASSES (silently dropped): "
        f"{unreachable}"
    )

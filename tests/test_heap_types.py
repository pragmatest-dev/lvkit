"""DTHP heap type pool -- the type source for a pre-LV9 diagram.

A pre-LV9 (LV8.x) VI has no ``VCTP``; its ``DTHP`` block carries the flat
TypeDesc list plus the ``TypeID -> TypeDesc`` table the block diagram and front
panel reference. Hermetic tests hand-build DTHP byte streams (pinning the binary
layout); the ``needs_samples`` tests prove end-to-end resolution on real
LabVIEW 8.2 VIs.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.models import LVTypeKind
from lvkit.parser.heap_types import HeapTypes, decode_heap_types
from lvkit.parser.node_types import _build_nmux_dco_maps
from lvkit.parser.td_pool import TdPool, read_td_list
from lvkit.parser.vi import _inline_dco_definitions


def _td(type_code: int, body: bytes, flags: int = 0x40) -> bytes:
    """One TypeDesc: u16 total_len (incl. these 2 bytes), u8 flags, u8 type."""
    return struct.pack(">HBB", 4 + len(body), flags, type_code) + body


def _pstr(s: str) -> bytes:
    return bytes([len(s)]) + s.encode("latin1")


def _dthp(tds: list[bytes], top: list[int]) -> bytes:
    """``u32 count`` + TDs, then ``u16 ntop`` + ``ntop x u16`` TD index."""
    return (
        struct.pack(">I", len(tds))
        + b"".join(tds)
        + struct.pack(f">H{len(top)}H", len(top), *top)
    )


def test_type_id_n_is_top_entry_n_minus_1():
    """Heap TypeID k (1-based) resolves to flat TypeDesc ``top[k-1]``; the same
    TD may serve several TypeIDs."""
    tds = [
        _td(0x03, _pstr("code")),  # [0] I32
        _td(0x30, b"\xff\xff\xff\xff" + _pstr("source")),  # [1] String
        _td(0x00, b"", flags=0),  # [2] Void
    ]
    types = decode_heap_types(_dthp(tds, [1, 0, 0, 2])).types
    assert types[1].type_descriptor() == "String"
    assert types[2].type_descriptor() == "I32"
    assert types[3].type_descriptor() == "I32"
    assert types[4].underlying_type == "Void"
    assert 0 not in types and 5 not in types


def test_cluster_array_and_typedef_enum_decode():
    """A cluster names its fields by earlier TD index; an array carries its
    dimension sizes before its element ref; a typedef embeds its inner TD."""
    enum_inner = (
        struct.pack(">H", 2) + _pstr("Off") + _pstr("On") + b"\x00" + _pstr("mode")
    )
    nested = struct.pack(">HBB", 4 + len(enum_inner), 0x40, 0x16) + enum_inner
    typedef_body = struct.pack(">II", 0, 1) + _pstr("Mode.ctl") + nested
    tds = [
        _td(0x21, _pstr("ok")),  # [0] Boolean
        _td(0x30, b"\xff\xff\xff\xff" + _pstr("name")),  # [1] String
        _td(0x50, struct.pack(">HHH", 2, 0, 1) + _pstr("pair")),  # [2] cluster
        _td(
            0x40,
            struct.pack(">H", 2) + b"\xff" * 8 + struct.pack(">H", 2),
            flags=0,
        ),  # [3] 2-D array of cluster [2]
        _td(0xF1, typedef_body, flags=0),  # [4] typedef enum
    ]
    types = decode_heap_types(_dthp(tds, [2, 3, 4])).types
    assert types[1].kind == LVTypeKind.CLUSTER
    assert [f.name for f in types[1].fields or []] == ["ok", "name"]
    assert types[2].kind == LVTypeKind.ARRAY
    assert types[2].dimensions == 2
    assert types[2].element_type is not None
    assert types[2].element_type.kind == LVTypeKind.CLUSTER
    assert types[3].kind == LVTypeKind.ENUM
    assert types[3].typedef_name == "Mode.ctl"
    assert list(types[3].values or {}) == ["Off", "On"]


def test_parametrized_refnum_carries_its_element_type():
    tds = [
        _td(0x30, b"\xff\xff\xff\xff" + _pstr("msg")),  # [0] String
        _td(0x70, struct.pack(">HHH", 18, 1, 0), flags=0),  # [1] Queue of [0]
    ]
    types = decode_heap_types(_dthp(tds, [1])).types
    assert types[1].type_descriptor() == "Queue refnum{String}"


def test_empty_dthp_yields_nothing_and_no_failure():
    assert decode_heap_types(struct.pack(">I", 0)) == HeapTypes()


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        (b"", "too short to hold a descriptor count"),
        (b"\xff\xff\xff\xff\x00\x0c", "the block ends inside descriptor 0"),
        (
            _dthp([_td(0x03, b"")], [0])[:-2] + b"\x00\x05",
            "names descriptor 5, which is not listed",
        ),
        (struct.pack(">I", 1) + _td(0x03, b""), "has no TypeID table"),
        (
            _dthp([_td(0x03, b"")], [0])[:-4] + b"\x00\x09\x00\x00",
            "lists 9 entries the block does not hold",
        ),
    ],
)
def test_malformed_dthp_says_why(block, reason):
    heap = decode_heap_types(block)
    assert heap.types == {}
    assert heap.failure is not None and reason in heap.failure


def test_a_descriptor_that_does_not_decode_is_an_unreadable_type():
    tds = [
        _td(0x03, _pstr("code")),  # [0] I32
        _td(0x50, struct.pack(">HH", 1, 5)),  # [1] cluster naming TD 5, not listed
    ]
    heap = decode_heap_types(_dthp(tds, [0, 1]))
    assert heap.failure is None
    assert heap.types[1].unreadable is None
    reason = heap.types[2].unreadable
    assert reason is not None and "refers to descriptor 5" in reason
    assert heap.types[2].type_descriptor().endswith(f"(unreadable: {reason})")


def test_a_label_is_read_exactly_when_the_flag_says_there_is_one():
    labelled = _td(0x03, _pstr("code"))
    unlabelled = _td(0x03, b"", flags=0)
    pool = TdPool(read_td_list(struct.pack(">I", 2) + labelled + unlabelled).tds)
    assert pool.names == ["code", None]


def test_a_flagged_label_that_is_missing_is_an_unreadable_type():
    types = decode_heap_types(_dthp([_td(0x03, b"")], [0])).types
    assert types[1].unreadable is not None


def test_structure_dco_definitions_are_inlined_into_bare_references():
    """A pre-LV9 diagram defines a by-name drawer (``dcoList``) and a case
    selector (``caseSelDCO``) on the structure; its terminals hold a bare
    ``<dco uid>``. Inlining gives every reader the one form -- so the drawer's
    field index and the selector's class are found on the terminal's ``<dco>``."""
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="nMux" uid="1">
            <dcoList elements="2">
              <SL__arrayElement class="nmxDCO" uid="10"><i>2</i></SL__arrayElement>
              <SL__arrayElement class="nmxDCO" uid="11" />
            </dcoList>
            <termList elements="2">
              <SL__arrayElement uid="100"><dco uid="10"/></SL__arrayElement>
              <SL__arrayElement uid="101"><dco uid="11"/></SL__arrayElement>
            </termList>
          </SL__arrayElement>
          <SL__arrayElement class="select" uid="2">
            <caseSelDCO class="caseSel" uid="20">
              <typeDesc>TypeID(7)</typeDesc>
            </caseSelDCO>
            <termList elements="1">
              <SL__arrayElement uid="200"><dco uid="20"/></SL__arrayElement>
            </termList>
          </SL__arrayElement>
        </root>
        """
    )
    _inline_dco_definitions(root)
    nmux = root.find("SL__arrayElement[@class='nMux']")
    assert nmux is not None
    _, field_index = _build_nmux_dco_maps(nmux, ["10", "11"], "i")
    # Drawer 11 has no <i>: LabVIEW omits it for field 0.
    assert field_index == {"10": 2, "11": 0}
    selector = root.find(".//SL__arrayElement[@uid='200']/dco")
    assert selector is not None and selector.get("class") == "caseSel"
    assert selector.findtext("typeDesc") == "TypeID(7)"


# --- end-to-end on real LabVIEW 8.2 VIs -------------------------------------

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_UTIL = _SAMPLES / "JKI-VI-Tester" / "source" / "Utilities"
_ECFEC = (
    _SAMPLES
    / "JKI-VI-Tester"
    / "source"
    / "Build Support"
    / "Package Builder Utilities"
    / "Error Cluster From Error Code__JKI_RIGHT_CLICK_BUILD_SUPPORT.vi"
)
_MASK = _UTIL / "Create Mask By Alpha - Advanced -.vi"
_HIERARCHY = _UTIL / "Read VI Hierarchy on Disk Prototype.vi"


def _parsed(vi_path: Path):
    from lvkit.parser.vi import parse_vi

    return parse_vi(vi_path)


def _unresolved(vi) -> int:
    """Diagram terminals whose type did not resolve."""
    return sum(
        1
        for t in vi.block_diagram.terminal_info.values()
        if t.parsed_type is None or t.parsed_type.type_name.startswith("TypeID(")
    )


@pytest.mark.needs_samples
@pytest.mark.skipif(not _ECFEC.exists(), reason="JKI-VI-Tester sample absent")
def test_lv82_heap_type_ids_resolve_to_real_types():
    from lvkit.extractor import extract_vi_xml
    from lvkit.parser.type_mapping import parse_type_map_rich

    _, _, main_xml = extract_vi_xml(_ECFEC)
    assert main_xml is not None
    type_map = parse_type_map_rich(main_xml)
    assert type_map[31].type_descriptor() == "[String]"  # `call chain`
    assert type_map[3].type_descriptor() == "Error"  # error cluster, by field names
    assert type_map[16].type_descriptor() == "TF"


@pytest.mark.needs_samples
@pytest.mark.skipif(not _MASK.exists(), reason="JKI-VI-Tester sample absent")
def test_lv82_diagram_arrays_have_elements_and_nmux_fields_resolve():
    vi = _parsed(_MASK)
    terms = vi.block_diagram.terminal_info.values()
    arrays = [t for t in terms if t.parsed_type and t.parsed_type.kind == "array"]
    assert arrays
    assert all(t.parsed_type and t.parsed_type.element_type for t in arrays)
    # The by-name unbundle's three drawers select image depth / image / Rectangle
    # (fields 1, 2, 5): distinct indices, not three copies of field 0.
    unbundle = next(
        n for n in vi.block_diagram.nodes if n.uid == "510"
    )  # Bundle/Unbundle By Name
    assert sorted(unbundle.dco_field_index.values()) == [1, 2, 5]  # type: ignore[attr-defined]


@pytest.mark.needs_samples
@pytest.mark.skipif(not _HIERARCHY.exists(), reason="JKI-VI-Tester sample absent")
def test_lv82_diagram_terminals_are_mostly_typed():
    """Before the heap pool decoded, every diagram terminal of a pre-LV9 VI was
    untyped; now only terminals the file itself leaves untyped remain."""
    vi = _parsed(_HIERARCHY)
    total = len(vi.block_diagram.terminal_info)
    assert _unresolved(vi) < total * 0.2, (_unresolved(vi), total)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _MASK.exists(), reason="JKI-VI-Tester sample absent")
def test_lv82_case_selector_is_found_and_named_in_lvnet():
    """A pre-LV9 case structure defines its selector on the structure, not on its
    border terminal; the lvnet header names the net driving it."""
    from lvkit.graph.core import InMemoryVIGraph
    from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
    from lvkit.load_mode import LoadMode

    graph = InMemoryVIGraph()
    graph.load_vi(str(_MASK), LoadMode.NONE, layout=False)
    vi_name = graph.resolve_vi_name(_MASK.name)
    text = render_lvnet(
        build_netlist_from_graph(graph, vi_name),
        display_name=graph.vi_display_name(vi_name),
        verbose=False,
    )
    assert "case ? :" not in text
    assert "case Bundle/Unbundle_By_Name_510::image depth :" in text

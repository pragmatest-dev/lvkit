"""Tests for event structure parsing — frame labels and border tunnels.

Mirrors test_case_parser.py's style (minimal synthetic XML), covering the
two things this parser slice is responsible for: (1) per-frame display
labels — faithful for the ONE frame LabVIEW last displayed, an honest
``"[N]"`` placeholder for every other frame (there is no per-frame label
table in the heap, unlike a case structure's dataspace SelectorTable) — and
(2) the four dco classes that carry data across the structure boundary,
all sharing the same per-frame-array tunnel shape.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.nodes.event import extract_event_structures


def _build_event_xml(
    struct_uid: str,
    *,
    num_frames: int = 2,
    d_idx: int | None = None,
    sel_string: str | None = None,
    tunnel_specs: list[tuple[str, str, list[str]]] | None = None,
    data_node_uids: list[str] | None = None,
    filter_node_uids: list[str] | None = None,
) -> ET.Element:
    """Build minimal XML for an event structure.

    Args:
        struct_uid: UID for the eventStruct element.
        num_frames: number of diagram frames to create.
        d_idx: heap ``dIdx`` (displayed-frame index), omitted if None.
        sel_string: raw ``selString/textRec/text`` content (heap wraps the
            whole thing in literal quote characters, e.g. ``'" [1] Timeout "'``).
        tunnel_specs: list of (outer_uid, dco_class, [inner_uid, ...]) — the
            eventStruct's own boundary terminals.
        data_node_uids: per-frame ``dataNodeList`` uids ("0" = none).
        filter_node_uids: per-frame ``filterNodeList`` uids ("0" = none).
    """
    root = ET.Element("root")
    es = ET.SubElement(root, "SL__arrayElement", attrib={
        "class": "eventStruct", "uid": struct_uid,
    })

    term_list = ET.SubElement(es, "termList")
    for outer_uid, dco_class, inner_uids in (tunnel_specs or []):
        term = ET.SubElement(term_list, "SL__arrayElement", attrib={
            "class": "term", "uid": outer_uid,
        })
        dco = ET.SubElement(term, "dco", attrib={"class": dco_class})
        dco_tl = ET.SubElement(dco, "termList")
        for iu in inner_uids:
            ET.SubElement(dco_tl, "SL__arrayElement", attrib={"uid": iu})
        # Outer face is the LAST element in the dco's own termList.
        ET.SubElement(dco_tl, "SL__arrayElement", attrib={"uid": outer_uid})

    diag_list = ET.SubElement(es, "diagramList")
    for i in range(num_frames):
        ET.SubElement(diag_list, "SL__arrayElement", attrib={
            "class": "diag", "uid": f"diag_{i}",
        })

    if d_idx is not None:
        ET.SubElement(es, "dIdx").text = str(d_idx)

    if sel_string is not None:
        sel = ET.SubElement(es, "selString")
        text_rec = ET.SubElement(sel, "textRec")
        ET.SubElement(text_rec, "text").text = sel_string

    if data_node_uids is not None:
        dnl = ET.SubElement(es, "dataNodeList")
        for u in data_node_uids:
            ET.SubElement(dnl, "SL__arrayElement", attrib={"uid": u})

    if filter_node_uids is not None:
        fnl = ET.SubElement(es, "filterNodeList")
        for u in filter_node_uids:
            ET.SubElement(fnl, "SL__arrayElement", attrib={"uid": u})

    return root


class TestFrameLabels:
    def test_frames_default_to_bracket_index_placeholder(self):
        """No dIdx/selString at all -- every frame gets an honest [N]."""
        root = _build_event_xml("es1", num_frames=3)
        es = extract_event_structures(root)[0]
        assert [f.event_label for f in es.frames] == ["[0]", "[1]", "[2]"]
        assert es.displayed_frame == 0

    def test_displayed_frame_gets_faithful_selstring_label(self):
        """LabVIEW's own bracketed rendering (heap wraps it in quote chars)
        is used verbatim for the displayed frame; every other frame keeps
        its placeholder."""
        root = _build_event_xml(
            "es1", num_frames=3, d_idx=1,
            sel_string='" [1] Timeout "',
        )
        es = extract_event_structures(root)[0]
        assert es.displayed_frame == 1
        assert es.frames[0].event_label == "[0]"
        assert es.frames[1].event_label == "[1] Timeout"
        assert es.frames[2].event_label == "[2]"

    def test_quoted_control_name_label_preserved(self):
        """A control-sourced event's label keeps its quoted control name
        AND the bracketed index, e.g. LabVIEW's own
        ``[3] "copyrights": Value Change`` (VI Tester About.vi, task #75)."""
        root = _build_event_xml(
            "es1", num_frames=4, d_idx=3,
            sel_string='" [3] "copyrights": Value Change "',
        )
        es = extract_event_structures(root)[0]
        assert es.frames[3].event_label == '[3] "copyrights": Value Change'

    def test_missing_didx_defaults_to_frame_0(self):
        """Heap omits dIdx when it's 0 (same convention as parmIndex/
        paramIdx elsewhere in this parser)."""
        root = _build_event_xml(
            "es1", num_frames=2, sel_string='" [0] Timeout "',
        )
        es = extract_event_structures(root)[0]
        assert es.displayed_frame == 0
        assert es.frames[0].event_label == "[0] Timeout"
        assert es.frames[1].event_label == "[1]"

    def test_out_of_range_didx_ignored(self):
        """A dIdx pointing past the frame count never crashes and never
        mislabels — displayed_frame is None, every frame keeps its
        placeholder."""
        root = _build_event_xml(
            "es1", num_frames=2, d_idx=5, sel_string='" [5] Bogus "',
        )
        es = extract_event_structures(root)[0]
        assert es.displayed_frame is None
        assert [f.event_label for f in es.frames] == ["[0]", "[1]"]


class TestBorderTunnels:
    def test_all_four_event_tunnel_dco_classes_extracted(self):
        """selTun, both eventDynDCO instances (registration in/out), and
        eventTimeOut all share the same per-frame-array shape -- N inner
        tunnels per outer, for N frames."""
        root = _build_event_xml(
            "es1", num_frames=3,
            tunnel_specs=[
                ("dyn_in", "eventDynDCO", ["dyn_in_f0", "dyn_in_f1", "dyn_in_f2"]),
                ("dyn_out", "eventDynDCO", ["dyn_out_f0", "dyn_out_f1", "dyn_out_f2"]),
                ("timeout", "eventTimeOut", ["to_f0", "to_f1", "to_f2"]),
                ("stop", "selTun", ["stop_f0", "stop_f1", "stop_f2"]),
            ],
        )
        es = extract_event_structures(root)[0]
        assert len(es.tunnels) == 12
        by_type: dict[str, list[str]] = {}
        for t in es.tunnels:
            by_type.setdefault(t.tunnel_type, []).append(t.inner_terminal_uid)
        assert by_type["eventDynDCO"] == [
            "dyn_in_f0", "dyn_in_f1", "dyn_in_f2",
            "dyn_out_f0", "dyn_out_f1", "dyn_out_f2",
        ]
        assert by_type["eventTimeOut"] == ["to_f0", "to_f1", "to_f2"]
        assert by_type["selTun"] == ["stop_f0", "stop_f1", "stop_f2"]
        assert all(t.outer_terminal_uid == "timeout"
                   for t in es.tunnels if t.tunnel_type == "eventTimeOut")

    def test_non_event_tunnel_dco_class_ignored(self):
        """A dco class this structure doesn't recognize as a boundary tunnel
        (e.g. a plain constant DCO) produces no tunnel."""
        root = _build_event_xml(
            "es1", num_frames=2,
            tunnel_specs=[("x", "bDConstDCO", ["x_f0", "x_f1"])],
        )
        es = extract_event_structures(root)[0]
        assert es.tunnels == []

    def test_no_tunnels_when_termlist_empty(self):
        root = _build_event_xml("es1", num_frames=2)
        es = extract_event_structures(root)[0]
        assert es.tunnels == []


class TestDataAndFilterNodeUids:
    def test_data_node_uid_folded_into_frame_inner_node_uids(self):
        root = _build_event_xml(
            "es1", num_frames=2,
            data_node_uids=["dn0", "dn1"],
        )
        es = extract_event_structures(root)[0]
        assert "dn0" in es.frames[0].inner_node_uids
        assert "dn1" in es.frames[1].inner_node_uids

    def test_zero_uid_means_no_node_for_that_frame(self):
        """Heap uid="0" marks "no data/filter node for this frame" -- must
        not be folded in as a real node reference."""
        root = _build_event_xml(
            "es1", num_frames=2,
            data_node_uids=["0", "dn1"],
            filter_node_uids=["fn0", "0"],
        )
        es = extract_event_structures(root)[0]
        assert es.frames[0].inner_node_uids == ["fn0"]
        assert es.frames[1].inner_node_uids == ["dn1"]

    def test_both_data_and_filter_node_on_same_frame(self):
        root = _build_event_xml(
            "es1", num_frames=1,
            data_node_uids=["dn0"],
            filter_node_uids=["fn0"],
        )
        es = extract_event_structures(root)[0]
        assert set(es.frames[0].inner_node_uids) == {"dn0", "fn0"}


class TestNoEventStructures:
    def test_no_eventstruct_elements_returns_empty(self):
        root = ET.Element("root")
        assert extract_event_structures(root) == []

    def test_eventstruct_without_diagrams_skipped(self):
        """An eventStruct with no diagramList/diag children is malformed --
        skip it rather than emit a frameless structure."""
        root = ET.Element("root")
        ET.SubElement(root, "SL__arrayElement", attrib={
            "class": "eventStruct", "uid": "es1",
        })
        assert extract_event_structures(root) == []

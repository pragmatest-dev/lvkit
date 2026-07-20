"""Tests for Diagram/Conditional Disable structure parsing.

A Disable structure is serialized as class="commentNode" -- the same class
a plain free-text comment might use -- distinguished by carrying subdiagrams
(a diagramList of >=1 diag children). See parser/nodes/disable.py.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.nodes.disable import (
    extract_disable_structures,
    is_disable_structure,
)


def _build_disable_xml(
    comment_uid: str,
    *,
    active_diag: str | None,
    active_label: str | None,
    frame_node_uids: list[list[str]],
    outer_term_uid: str | None = None,
    inner_term_uids: list[str] | None = None,
) -> ET.Element:
    """Build minimal XML for a Disable structure, mirroring the real heap
    shape (verified against ctm_head.vi node 1926 and 111 other corpus VIs):
    termList (own boundary terminals) / diagramList (direct diag children,
    each with a nodeList) / selString (active frame's label) / activeDiag.
    """
    root = ET.Element("root")
    comment = ET.SubElement(root, "SL__arrayElement", attrib={
        "class": "commentNode", "uid": comment_uid,
    })

    term_list = ET.SubElement(comment, "termList")
    if outer_term_uid is not None and inner_term_uids is not None:
        term = ET.SubElement(term_list, "SL__arrayElement", attrib={
            "class": "term", "uid": outer_term_uid,
        })
        dco = ET.SubElement(term, "dco", attrib={
            "class": "commentTun", "uid": f"dco_{outer_term_uid}",
        })
        dco_term_list = ET.SubElement(dco, "termList")
        for inner_uid in inner_term_uids:
            ET.SubElement(dco_term_list, "SL__arrayElement", attrib={
                "uid": inner_uid,
            })
        # Outer terminal is last in its own dco's termList (selTun-style).
        ET.SubElement(dco_term_list, "SL__arrayElement", attrib={
            "uid": outer_term_uid,
        })

    diag_list = ET.SubElement(comment, "diagramList")
    for i, node_uids in enumerate(frame_node_uids):
        diag = ET.SubElement(diag_list, "SL__arrayElement", attrib={
            "class": "diag", "uid": f"diag_{i}",
        })
        node_list = ET.SubElement(diag, "nodeList")
        for node_uid in node_uids:
            ET.SubElement(node_list, "SL__arrayElement", attrib={
                "uid": node_uid,
            })

    if active_label is not None:
        sel_string = ET.SubElement(comment, "selString", attrib={
            "class": "selLabel",
        })
        text_rec = ET.SubElement(sel_string, "textRec", attrib={
            "class": "textHair",
        })
        ET.SubElement(text_rec, "text").text = f'"{active_label}"'

    if active_diag is not None:
        ET.SubElement(comment, "activeDiag").text = active_diag

    return root


class TestIsDisableStructure:
    def test_true_for_structure_with_diag_children(self):
        root = _build_disable_xml(
            "100", active_diag="01", active_label=" Disabled ",
            frame_node_uids=[["200"], []],
        )
        comment = root.find(".//*[@class='commentNode']")
        assert comment is not None
        assert is_disable_structure(comment) is True

    def test_false_for_plain_comment_without_diagramlist(self):
        root = ET.Element("root")
        comment = ET.SubElement(root, "SL__arrayElement", attrib={
            "class": "commentNode", "uid": "999",
        })
        ET.SubElement(comment, "label")
        assert is_disable_structure(comment) is False

    def test_false_for_diagramlist_with_no_diag_children(self):
        root = ET.Element("root")
        comment = ET.SubElement(root, "SL__arrayElement", attrib={
            "class": "commentNode", "uid": "999",
        })
        ET.SubElement(comment, "diagramList")
        assert is_disable_structure(comment) is False


class TestExtractDisableStructures:
    def test_two_frame_enabled_disabled(self):
        """Diagram Disable Structure: frame 0 has the real code (node 200),
        frame 1 (index == activeDiag) is the empty 'Disabled' frame -- the
        currently-active label ('Disabled') is read from selString; frame 0
        gets the fixed complementary label ('Enabled'), a LabVIEW product
        invariant for the 2-frame case (not a guess)."""
        root = _build_disable_xml(
            "1926", active_diag="01", active_label=" Disabled ",
            frame_node_uids=[["1938", "190"], ["1945"]],
        )
        structures = extract_disable_structures(root)

        assert len(structures) == 1
        ds = structures[0]
        assert ds.uid == "1926"
        assert ds.active_frame == 1
        assert len(ds.frames) == 2

        enabled_frame, disabled_frame = ds.frames
        assert enabled_frame.selector_value == "Enabled"
        assert enabled_frame.is_default is False
        assert enabled_frame.inner_node_uids == ["1938", "190"]

        assert disabled_frame.selector_value == "Disabled"
        assert disabled_frame.is_default is True
        assert disabled_frame.inner_node_uids == ["1945"]

    def test_conditional_three_frame_only_active_gets_real_label(self):
        """A Conditional Disable Structure (3+ frames, e.g. per-symbol
        conditions) has no reliable per-frame label source beyond the
        active one -- non-active frames get an honest 'Frame N' placeholder
        rather than a guessed symbol name."""
        root = _build_disable_xml(
            "390", active_diag="02", active_label=" Default ",
            frame_node_uids=[["401"], ["416"], ["429"]],
        )
        structures = extract_disable_structures(root)

        assert len(structures) == 1
        ds = structures[0]
        assert ds.active_frame == 2
        labels = [f.selector_value for f in ds.frames]
        assert labels == ["Frame 0", "Frame 1", "Default"]
        assert ds.frames[2].is_default is True
        assert ds.frames[0].is_default is False
        assert ds.frames[1].is_default is False

    def test_own_boundary_tunnels_selTun_style(self):
        """The structure's own commentTun boundary terminal maps to one
        inner terminal per frame (positionally identical to a case's
        selTun): [frame0_inner, frame1_inner, ..., outer_self]."""
        root = _build_disable_xml(
            "1926", active_diag="01", active_label=" Disabled ",
            frame_node_uids=[["1938", "190"], ["1945"]],
            outer_term_uid="1962", inner_term_uids=["1960", "1961"],
        )
        structures = extract_disable_structures(root)

        assert len(structures) == 1
        tunnels = structures[0].tunnels
        assert len(tunnels) == 2
        assert {t.inner_terminal_uid for t in tunnels} == {"1960", "1961"}
        assert all(t.outer_terminal_uid == "1962" for t in tunnels)
        assert all(t.tunnel_type == "commentTun" for t in tunnels)

    def test_no_disable_structures_when_none_present(self):
        root = ET.Element("root")
        ET.SubElement(root, "SL__arrayElement", attrib={
            "class": "commentNode", "uid": "1",
        })
        assert extract_disable_structures(root) == []

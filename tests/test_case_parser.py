"""Tests for case structure parsing — selector value resolution by type."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvpy.parser.models import ParsedTerminalInfo, ParsedType
from lvpy.parser.nodes.case import extract_case_structures


def _build_case_xml(
    case_uid: str,
    selector_uid: str,
    select_ranges: list[tuple[int, int]],
    *,
    string_array: list[str] | None = None,
    default_diag: int | None = None,
    num_diags: int = 2,
) -> ET.Element:
    """Build minimal XML for a case structure.

    Args:
        case_uid: UID for the case structure element
        selector_uid: UID for the selector terminal
        select_ranges: list of (start, diagramIdx) for SelectRangeArray32
        string_array: hex-encoded strings for SelectStringArray
        default_diag: diagram index of the default case (None = no default)
        num_diags: number of diagram frames to create
    """
    root = ET.Element("root")
    case = ET.SubElement(root, "SL__arrayElement", attrib={
        "class": "select", "uid": case_uid,
    })

    # Selector terminal
    term_list = ET.SubElement(case, "termList")
    term = ET.SubElement(term_list, "SL__arrayElement", attrib={
        "class": "term", "uid": selector_uid,
    })
    ET.SubElement(term, "dco", attrib={"class": "cSelDCO"})

    # SelectRangeArray32
    sra = ET.SubElement(case, "SelectRangeArray32")
    for start, diag_idx in select_ranges:
        sr = ET.SubElement(sra, "SL__arrayElement", attrib={
            "class": "SelectorRange",
        })
        ET.SubElement(sr, "start").text = str(start)
        ET.SubElement(sr, "diagramIdx").text = str(diag_idx)

    # SelectStringArray
    if string_array is not None:
        ssa = ET.SubElement(case, "SelectStringArray")
        for hex_str in string_array:
            item = ET.SubElement(ssa, "SL__arrayElement")
            item.text = hex_str

    # SelectDefaultCase
    if default_diag is not None:
        ET.SubElement(case, "SelectDefaultCase").text = f"{default_diag:02X}"

    # Diagram frames
    diag_list = ET.SubElement(case, "diagramList")
    for i in range(num_diags):
        ET.SubElement(diag_list, "SL__arrayElement", attrib={
            "class": "diag", "uid": f"diag_{i}",
        })

    return root


def _make_terminal_info(
    uid: str, type_name: str,
) -> dict[str, ParsedTerminalInfo]:
    return {
        uid: ParsedTerminalInfo(
            uid=uid,
            parent_uid="parent",
            index=0,
            is_output=False,
            parsed_type=ParsedType(kind="primitive", type_name=type_name),
        ),
    }


class TestBooleanSelector:
    def test_boolean_maps_0_false_1_true(self):
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(0, 0), (1, 1)],
        )
        ti = _make_terminal_info("sel1", "Boolean")
        cases = extract_case_structures(root, ti)

        assert len(cases) == 1
        cs = cases[0]
        assert cs.selector_type == "boolean"
        assert cs.frames[0].selector_value == "False"
        assert cs.frames[1].selector_value == "True"

    def test_boolean_reversed_diag_order(self):
        """diagramIdx 0 = True (start=1), diagramIdx 1 = False (start=0)."""
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(1, 0), (0, 1)],
        )
        ti = _make_terminal_info("sel1", "Boolean")
        cases = extract_case_structures(root, ti)

        cs = cases[0]
        assert cs.frames[0].selector_value == "True"
        assert cs.frames[1].selector_value == "False"


class TestStringSelector:
    def test_string_uses_select_string_array(self):
        """String case should decode hex values from SelectStringArray."""
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(0, 0)],
            string_array=["54657374436173652E6C76636C617373"],  # TestCase.lvclass
            default_diag=1,
        )
        ti = _make_terminal_info("sel1", "String")
        cases = extract_case_structures(root, ti)

        cs = cases[0]
        assert cs.selector_type == "string"
        assert cs.frames[0].selector_value == "TestCase.lvclass"
        assert cs.frames[1].is_default is True
        assert cs.frames[1].selector_value == "Default"

    def test_string_multiple_values(self):
        """String case with multiple string labels."""
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(0, 0), (1, 1), (2, 2)],
            string_array=[
                "616C706861",   # alpha
                "62657461",     # beta
                "67616D6D61",   # gamma
            ],
            num_diags=3,
        )
        ti = _make_terminal_info("sel1", "String")
        cases = extract_case_structures(root, ti)

        cs = cases[0]
        assert cs.frames[0].selector_value == "alpha"
        assert cs.frames[1].selector_value == "beta"
        assert cs.frames[2].selector_value == "gamma"

    def test_string_without_terminal_info_falls_back(self):
        """Without terminal_info, string cases get raw integer values."""
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(0, 0)],
            string_array=["54657374436173652E6C76636C617373"],
            default_diag=1,
        )
        # No terminal_info — can't know it's a string selector
        cases = extract_case_structures(root, None)

        cs = cases[0]
        # Without type info, falls back to raw integer
        assert cs.frames[0].selector_value == "0"


class TestIntegerSelector:
    def test_integer_uses_raw_values(self):
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(0, 0), (1, 1), (2, 2)],
            num_diags=3,
        )
        ti = _make_terminal_info("sel1", "NumInt32")
        cases = extract_case_structures(root, ti)

        cs = cases[0]
        assert cs.selector_type == "integer"
        assert cs.frames[0].selector_value == "0"
        assert cs.frames[1].selector_value == "1"
        assert cs.frames[2].selector_value == "2"


class TestDefaultCase:
    def test_default_frame_marked(self):
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(0, 0)],
            default_diag=1,
        )
        ti = _make_terminal_info("sel1", "NumInt32")
        cases = extract_case_structures(root, ti)

        cs = cases[0]
        assert cs.frames[0].is_default is False
        assert cs.frames[1].is_default is True
        assert cs.frames[1].selector_value == "Default"

    def test_no_default_when_ff(self):
        """SelectDefaultCase=FF means no default frame."""
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(0, 0), (1, 1)],
        )
        # Manually set FF
        case_elem = root.find(".//*[@class='select']")
        assert case_elem is not None
        ET.SubElement(case_elem, "SelectDefaultCase").text = "FF"

        ti = _make_terminal_info("sel1", "Boolean")
        cases = extract_case_structures(root, ti)

        for frame in cases[0].frames:
            assert frame.is_default is False

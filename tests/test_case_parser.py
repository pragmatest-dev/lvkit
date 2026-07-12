"""Tests for case structure parsing — selector value resolution by type."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.models import ParsedTerminalInfo, ParsedType, SelectorTable
from lvkit.parser.nodes.case import (
    extract_case_structures,
    parse_selector_tables,
)


def _build_case_xml(
    case_uid: str,
    selector_uid: str,
    select_ranges: list[tuple[int, int]],
    *,
    string_array: list[str] | None = None,
    default_diag: int | None = None,
    num_diags: int = 2,
    sel_type_id: int | None = None,
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
    sel_dco = ET.SubElement(term, "dco", attrib={"class": "cSelDCO"})
    if sel_type_id is not None:
        ET.SubElement(sel_dco, "typeDesc").text = f"TypeID({sel_type_id})"

    # SelectRangeArray32. Each entry is (start, diagramIdx) — a single value —
    # or (start, end, diagramIdx) for a closed range.
    sra = ET.SubElement(case, "SelectRangeArray32")
    for entry in select_ranges:
        if len(entry) == 3:
            start, end, diag_idx = entry
        else:
            start, diag_idx = entry
            end = start
        sr = ET.SubElement(sra, "SL__arrayElement", attrib={
            "class": "SelectorRange",
        })
        ET.SubElement(sr, "start").text = str(start)
        ET.SubElement(sr, "end").text = str(end)
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

    def test_implicit_default_when_frame_has_no_range(self):
        """A non-boolean frame with NO SelectorRange is the implicit default
        (LabVIEW labels it "Default"), not a spurious "False". Regression for
        the boolean-fallback bug on enum/integer selectors."""
        # Two diagrams, but only diag 1 has an explicit range (value 9).
        root = _build_case_xml(
            "cs1", "sel1",
            select_ranges=[(9, 1)],
            num_diags=2,
        )
        ti = _make_terminal_info("sel1", "NumInt32")
        cases = extract_case_structures(root, ti)

        cs = cases[0]
        assert cs.frames[0].is_default is True
        assert cs.frames[0].selector_value == "Default"
        assert cs.frames[1].selector_value == "9"


class TestSelectorRanges:
    def test_single_range_and_multi_range_preserved(self):
        """selector_ranges carries start/end faithfully, including several
        ranges on one frame (e.g. ``1, 3, 5..8``)."""
        root = _build_case_xml(
            "cs1", "sel1",
            # diag 0: 0..1 ; diag 1: 9 ; diag 2: 2..8
            select_ranges=[(0, 1, 0), (9, 9, 1), (2, 8, 2)],
            num_diags=3,
        )
        ti = _make_terminal_info("sel1", "NumInt32")
        cs = extract_case_structures(root, ti)[0]

        f0 = cs.frames[0]
        assert [(r.start, r.end) for r in f0.selector_ranges] == [(0, 1)]
        assert f0.selector_ranges[0].is_single is False
        f1 = cs.frames[1]
        assert [(r.start, r.end) for r in f1.selector_ranges] == [(9, 9)]
        assert f1.selector_ranges[0].is_single is True
        f2 = cs.frames[2]
        assert [(r.start, r.end) for r in f2.selector_ranges] == [(2, 8)]

    def test_boolean_frames_have_no_ranges(self):
        """Booleans keep True/False in selector_value and carry no ranges."""
        root = _build_case_xml("cs1", "sel1", select_ranges=[(0, 0), (1, 1)])
        ti = _make_terminal_info("sel1", "Boolean")
        cs = extract_case_structures(root, ti)[0]
        assert all(f.selector_ranges == [] for f in cs.frames)


# ---------------------------------------------------------------------------
# Dataspace selector-value tables (#82): the real per-frame selector values
# live in the main *.xml DFDS, not the block-diagram heap.
# ---------------------------------------------------------------------------

def _ds_selector_table(
    type_id: int,
    displayed_frame: int,
    ranges: list[tuple[int, int, int]],
    strings: list[str] | None = None,
) -> ET.Element:
    """Build one dataspace <DataFill> with the selector-table cluster shape:
    {I32 displayed, I32 count, Array[range clusters], Array[String], trailer}.
    """
    df = ET.Element("DataFill", attrib={"TypeID": str(type_id)})
    cl = ET.SubElement(df, "Cluster")
    ET.SubElement(cl, "I32").text = str(displayed_frame)
    ET.SubElement(cl, "I32").text = str(len(ranges))
    range_arr = ET.SubElement(cl, "Array")
    ET.SubElement(range_arr, "dim").text = str(len(ranges))
    for start, end, diag in ranges:
        rc = ET.SubElement(range_arr, "Cluster")
        ET.SubElement(rc, "I32").text = str(start)
        ET.SubElement(rc, "I32").text = str(end)
        ET.SubElement(rc, "U8").text = "0"
        ET.SubElement(rc, "U8").text = "0"
        ET.SubElement(rc, "I16").text = str(diag)
    str_arr = ET.SubElement(cl, "Array")
    ET.SubElement(str_arr, "dim").text = str(len(strings or []))
    for s in strings or []:
        ET.SubElement(str_arr, "String").text = s
    trailer = ET.SubElement(cl, "Cluster")
    ET.SubElement(trailer, "I32").text = "0"
    ET.SubElement(trailer, "I32").text = "0"
    return df


def _ds_root(*datafills: ET.Element) -> ET.Element:
    root = ET.Element("root")
    for df in datafills:
        root.append(df)
    return root


class TestParseSelectorTables:
    def test_decode_string_and_numeric_tables(self):
        root = _ds_root(
            _ds_selector_table(33, 4, [(0, 0, 0), (2, 4, 1)],
                               strings=["bmp", "jpe", "jpeg", "jpg", "png"]),
            _ds_selector_table(35, 2, [(2, 3, 0), (5, 7, 1)]),
        )
        tables = parse_selector_tables(root)
        assert [t.type_id for t in tables] == [33, 35]
        assert tables[0].has_strings and tables[0].displayed_frame == 4
        assert tables[0].ranges == [(0, 0, 0), (2, 4, 1)]
        assert tables[1].strings == [] and tables[1].ranges == [(2, 3, 0), (5, 7, 1)]

    def test_dedupe_identical_pairs_keeps_lowest_type_id(self):
        """pylabview emits each default twice (edit + run copy)."""
        def t(tid: int) -> ET.Element:
            return _ds_selector_table(tid, 0, [(0, 0, 0), (1, 1, 1)])
        tables = parse_selector_tables(_ds_root(t(31), t(70)))
        assert [t.type_id for t in tables] == [31]

    def test_ignores_non_selector_datafills(self):
        other = ET.Element("DataFill", attrib={"TypeID": "9"})
        ET.SubElement(other, "I32").text = "42"
        tables = parse_selector_tables(_ds_root(
            other, _ds_selector_table(33, 0, [(0, 0, 0), (1, 1, 1)])))
        assert [t.type_id for t in tables] == [33]


class TestApplySelectorTables:
    def test_string_multi_value_frame_and_displayed(self):
        # Extension case: bmp -> f0 ; jpe/jpeg/jpg -> f1 ; gif -> f2 ; default f3
        root = _build_case_xml(
            "cs1", "sel1", select_ranges=[], num_diags=4, sel_type_id=42,
        )
        tables = [_ds_selector_table_obj(
            33, 3,
            [(0, 0, 0), (2, 2, 1), (3, 3, 1), (4, 4, 1), (1, 1, 2)],
            ["bmp", "gif", "jpe", "jpeg", "jpg"])]
        ti = _make_terminal_info("sel1", "String")
        cs = extract_case_structures(root, ti, tables)[0]
        assert cs.displayed_frame == 3
        assert cs.frames[0].selector_strings == ["bmp"]
        assert cs.frames[1].selector_strings == ["jpe", "jpeg", "jpg"]
        assert cs.frames[1].selector_value == "jpe"
        assert cs.frames[2].selector_strings == ["gif"]
        assert cs.frames[3].is_default and cs.frames[3].selector_value == "Default"

    def test_integer_ranges_applied(self):
        root = _build_case_xml(
            "cs1", "sel1", select_ranges=[], num_diags=3, sel_type_id=50,
        )
        tables = [_ds_selector_table_obj(35, 2, [(2, 3, 0), (5, 7, 1)])]
        ti = _make_terminal_info("sel1", "NumInt32")
        cs = extract_case_structures(root, ti, tables)[0]
        assert [(r.start, r.end) for r in cs.frames[0].selector_ranges] == [(2, 3)]
        assert cs.frames[0].selector_value == "2..3"
        assert [(r.start, r.end) for r in cs.frames[1].selector_ranges] == [(5, 7)]
        assert cs.frames[2].is_default

    def test_kind_mismatch_aborts_application(self):
        """A string table must not be applied to an integer case."""
        root = _build_case_xml(
            "cs1", "sel1", select_ranges=[], num_diags=2, sel_type_id=42,
        )
        tables = [_ds_selector_table_obj(33, 0, [(0, 0, 0), (1, 1, 1)],
                                         ["a", "b"])]
        ti = _make_terminal_info("sel1", "NumInt32")
        cs = extract_case_structures(root, ti, tables)[0]
        # No string values applied; falls back to frame-index placeholders.
        assert cs.frames[0].selector_strings == []
        assert cs.displayed_frame is None

    def test_boolean_case_excluded_keeps_true_false(self):
        """A boolean case stores no table; adding one (count mismatch) must not
        touch its True/False frames."""
        root = _build_case_xml(
            "cs1", "sel1", select_ranges=[(0, 0), (1, 1)], sel_type_id=42,
        )
        tables = [_ds_selector_table_obj(33, 0, [(0, 0, 0), (1, 1, 1)])]
        ti = _make_terminal_info("sel1", "Boolean")
        cs = extract_case_structures(root, ti, tables)[0]
        assert cs.frames[0].selector_value == "False"
        assert cs.frames[1].selector_value == "True"


def _ds_selector_table_obj(
    type_id: int,
    displayed: int,
    ranges: list[tuple[int, int, int]],
    strings: list[str] | None = None,
) -> SelectorTable:
    return SelectorTable(type_id=type_id, displayed_frame=displayed,
                         ranges=list(ranges), strings=list(strings or []))

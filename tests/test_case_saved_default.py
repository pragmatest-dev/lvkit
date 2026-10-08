"""Case defaults are stored diagram indices, independent of selector values."""

from __future__ import annotations

import pytest

from lvkit.parser.nodes.case import extract_case_structures
from tests.test_case_parser import (
    _build_case_xml,
    _ds_selector_table_obj,
    _make_terminal_info,
)


@pytest.mark.parametrize("type_name", ["NumInt32", "String", "Enum"])
@pytest.mark.parametrize("default_diag", [None, 0, 1])
@pytest.mark.parametrize("with_table", [False, True])
def test_default_index_survives_literal_ranges_and_tables(
    type_name, default_diag, with_table
):
    strings = ["alpha", "beta"] if type_name == "String" else None
    root = _build_case_xml(
        "case",
        "selector",
        select_ranges=[(0, 0), (1, 1)],
        string_array=[s.encode().hex() for s in strings] if strings else None,
        default_diag=default_diag,
        sel_type_id=42,
    )
    tables = (
        [_ds_selector_table_obj(33, 1, [(0, 0, 0), (1, 1, 1)], strings)]
        if with_table
        else None
    )
    case = extract_case_structures(
        root, _make_terminal_info("selector", type_name), tables
    )[0]

    expected_default = 0 if default_diag is None else default_diag
    assert [f.is_default for f in case.frames] == [
        i == expected_default for i in range(2)
    ]
    assert [f.selector_value for f in case.frames] == (strings or ["0", "1"])
    if type_name != "String":
        assert [[(r.start, r.end) for r in f.selector_ranges] for f in case.frames] == [
            [(0, 0)],
            [(1, 1)],
        ]
    if with_table:
        assert case.displayed_frame == 1


@pytest.mark.parametrize("type_name", ["NumInt32", "String", "Enum"])
@pytest.mark.parametrize("with_table", [False, True])
def test_ff_keeps_fully_covered_frames_without_a_default(type_name, with_table):
    strings = ["alpha", "beta"] if type_name == "String" else None
    root = _build_case_xml(
        "case",
        "selector",
        select_ranges=[(0, 0), (1, 1)],
        string_array=[s.encode().hex() for s in strings] if strings else None,
        default_diag=255,
        sel_type_id=42,
    )
    tables = (
        [_ds_selector_table_obj(33, 0, [(0, 0, 0), (1, 1, 1)], strings)]
        if with_table
        else None
    )
    case = extract_case_structures(
        root, _make_terminal_info("selector", type_name), tables
    )[0]
    assert not any(f.is_default for f in case.frames)
    assert [f.selector_value for f in case.frames] == (strings or ["0", "1"])


@pytest.mark.parametrize("default_diag", [None, 255])
@pytest.mark.parametrize("with_table", [False, True])
def test_boolean_frames_keep_their_finite_domain(default_diag, with_table):
    root = _build_case_xml(
        "case",
        "selector",
        select_ranges=[(0, 0), (1, 1)],
        default_diag=default_diag,
        sel_type_id=42,
    )
    tables = (
        [_ds_selector_table_obj(33, 0, [(0, 0, 0), (1, 1, 1)])] if with_table else None
    )
    case = extract_case_structures(
        root, _make_terminal_info("selector", "Boolean"), tables
    )[0]
    assert not any(f.is_default for f in case.frames)
    assert [f.selector_value for f in case.frames] == ["False", "True"]


def test_default_retains_several_closed_selector_ranges():
    root = _build_case_xml(
        "case",
        "selector",
        select_ranges=[(0, 3, 0), (8, 9, 0), (4, 7, 1)],
    )
    case = extract_case_structures(root, _make_terminal_info("selector", "NumInt32"))[0]
    assert [f.is_default for f in case.frames] == [True, False]
    assert [(r.start, r.end) for r in case.frames[0].selector_ranges] == [
        (0, 3),
        (8, 9),
    ]


def test_zero_default_does_not_depend_on_displayed_frame():
    root = _build_case_xml(
        "case", "selector", select_ranges=[(0, 0), (1, 1)], sel_type_id=42
    )
    tables = [_ds_selector_table_obj(33, 1, [(0, 0, 0), (1, 1, 1)])]
    case = extract_case_structures(
        root, _make_terminal_info("selector", "NumInt32"), tables
    )[0]
    assert case.displayed_frame == 1
    assert [f.is_default for f in case.frames] == [True, False]

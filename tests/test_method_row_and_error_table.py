"""An Invoke Node's method row has no input terminal (#183), and a table-based
error case reads No Error / Error with no default (#187)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lvkit.models import CaseFrame, LVType, LVTypeKind
from lvkit.parser.models import (
    ParsedCaseStructure,
    ParsedTerminalInfo,
    SelectorKind,
    SelectorTable,
)
from lvkit.parser.nodes.case import _apply_one_table
from lvkit.parser.vi import _process_element_terminals
from tests.conftest import SAMPLES_ROOT


def _invoke_node(terms: list[tuple[str, int]]) -> ET.Element:
    """An Invoke Node whose terminals each carry their own invokeItem DCO."""
    items = "".join(
        f"""
    <SL__arrayElement class="term" uid="{uid}">
      <objFlags>0</objFlags>
      <dco class="invokeItem" uid="{uid}0">
        <objFlags>{flags}</objFlags>
        <typeDesc>TypeID(0)</typeDesc>
      </dco>
    </SL__arrayElement>"""
        for uid, flags in terms
    )
    return ET.fromstring(
        f"""
<SL__arrayElement class="invokeNode" uid="10">
  <termList elements="{len(terms)}">{items}
  </termList>
</SL__arrayElement>"""
    )


def _terminals(elem: ET.Element, sinks: set[str]) -> dict[str, ParsedTerminalInfo]:
    terminals: dict[str, ParsedTerminalInfo] = {}
    refnum = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
    _process_element_terminals(elem, set(), sinks, {0: refnum}, terminals, {})
    return terminals


def test_the_method_row_keeps_only_its_return_value_output() -> None:
    elem = _invoke_node(
        [
            ("21", 0x10001),  # method row, output half (return value)
            ("22", 0x10000),  # method row, input half: no terminal on the node
            ("23", 0x0),  # a parameter input
            ("24", 0x1),  # a parameter output
        ]
    )
    terminals = _terminals(elem, sinks=set())
    assert sorted(terminals) == ["21", "23", "24"]
    assert terminals["21"].is_output
    assert not terminals["23"].is_output
    assert terminals["24"].is_output


def test_a_wired_terminal_is_always_kept() -> None:
    elem = _invoke_node([("22", 0x10000)])
    assert sorted(_terminals(elem, sinks={"22"})) == ["22"]


def _error_case(n_frames: int) -> ParsedCaseStructure:
    return ParsedCaseStructure(
        uid="1",
        frames=[CaseFrame(selector_value=str(i)) for i in range(n_frames)],
        selector_type=SelectorKind.ERROR,
    )


def test_a_table_error_case_reads_no_error_then_error_without_a_default() -> None:
    case = _error_case(2)
    # LabVIEW 9: the table gives frame 0 the range holding status False; frame 1
    # has no range of its own.
    table = SelectorTable(type_id=0, displayed_frame=0, ranges=[(-2147483648, 0, 0)])
    _apply_one_table(case, table)
    no_error, error = case.frames
    assert no_error.is_no_error and not no_error.is_default
    assert not error.is_no_error and not error.is_default


@pytest.mark.needs_samples
def test_gif_read_unpack_error_case_reads_no_error_and_error() -> None:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.describe import describe_vi

    vi = (
        SAMPLES_ROOT
        / "OpenG/extracted/File Group 0/user.lib/_OpenG.lib/picture/picture.llb"
        / "GIF.Read.Unpack__ogtk.vi"
    )
    if not vi.exists():
        pytest.skip("corpus VI not present")
    graph = InMemoryVIGraph()
    graph.load_vi(vi)
    text = describe_vi(graph, graph.resolve_vi_name(vi.name))
    assert 'Frame "No Error":' in text
    assert 'Frame "Error":' in text
    assert "-2147483648" not in text

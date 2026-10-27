"""Shared Variable nodes (class="sharedVariable").

No Shared Variable exists in the sample corpus; the XML here is built from a real
node excerpt of a LabVIEW 2025 VI: a ``termList`` of three terminals whose
``terminal_ID`` is 0 (absent in the heap -- the default), 1 and 2, and a hex
``<name4>`` holding the variable name. Terminal 0 is the value: an output on a read
node, an input on a write node.
"""

# ruff: noqa: E501  (the XML fixture lines mirror the heap's one-line elements)
from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import SharedVariableNode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.parser.node_types import (
    SharedVariableAccess,
    parse_node,
)
from lvkit.parser.node_types import (
    SharedVariableNode as ParsedSharedVariableNode,
)

from .test_lvnet_identity import _assert_graph_identity
from .test_lvnet_roundtrip import _first_mismatch


def _node(
    uid: int,
    name: str,
    *,
    write: bool,
    encoding: str = "ascii",
    extra_terminal_id: int | None = None,
) -> str:
    """One ``sharedVariable`` node: value terminal (output when reading), error in
    and error out; ``extra_terminal_id`` adds a fourth terminal with that ID."""
    label = f'".\\Shared.lvlib\\{name}"'
    value_flags = "" if write else "<objFlags>1</objFlags>"
    extra = ""
    if extra_terminal_id is not None:
        extra = (
            f'<SL__arrayElement class="term" uid="{uid + 9}">'
            f'<dco class="sharedVariableDCO" uid="{uid + 10}">'
            f"<objFlags>1</objFlags><typeDesc>TypeID(207)</typeDesc>"
            f"<terminal_ID>{extra_terminal_id:02X}</terminal_ID></dco>"
            "</SL__arrayElement>"
        )
    return f"""
<SL__arrayElement class="sharedVariable" uid="{uid}">
  <termList elements="3">
    <SL__arrayElement class="term" uid="{uid + 1}">
      <dco class="sharedVariableDCO" uid="{uid + 2}">{value_flags}<typeDesc>TypeID(204)</typeDesc></dco>
    </SL__arrayElement>
    <SL__arrayElement class="term" uid="{uid + 4}">
      <dco class="sharedVariableDCO" uid="{uid + 5}"><typeDesc>TypeID(205)</typeDesc><terminal_ID>01</terminal_ID></dco>
    </SL__arrayElement>
    <SL__arrayElement class="term" uid="{uid + 7}">
      <dco class="sharedVariableDCO" uid="{uid + 8}"><objFlags>1</objFlags><typeDesc>TypeID(206)</typeDesc><terminal_ID>02</terminal_ID></dco>
    </SL__arrayElement>
    {extra}
  </termList>
  <label class="label" uid="{uid + 20}">
    <partID>16</partID>
    <textRec class="textHair"><text>{label}</text></textRec>
  </label>
  <name4>{name.encode(encoding).hex().upper()}</name4>
  <SharedVariableCustomRule />
</SL__arrayElement>"""


def _bd(*nodes: str) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
        "<zPlaneList>" + "".join(nodes) + "</zPlaneList></root></SL__rootObject>"
    )


def _parse(xml: str):
    import xml.etree.ElementTree as ET

    return parse_node(ET.fromstring(xml))


def _load(tmp_path: Path, *nodes: str) -> tuple[InMemoryVIGraph, str]:
    bd_xml = tmp_path / "Shared_BDHb.xml"
    bd_xml.write_text(_bd(*nodes), encoding="utf-8")
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    return graph, graph.list_vis()[0]


def _shared(graph: InMemoryVIGraph, vi: str) -> dict[str, SharedVariableNode]:
    return {
        n.id.rsplit("::", 1)[-1]: n
        for n in graph.iter_nodes(vi)
        if isinstance(n, SharedVariableNode)
    }


def test_parser_reads_the_variable_name_and_access() -> None:
    read = _parse(_node(100, "Measurement Computing TC-32_Out", write=False))
    write = _parse(_node(200, "Measurement Computing TC-32_Out", write=True))
    assert isinstance(read, ParsedSharedVariableNode)
    assert read.variable_name == "Measurement Computing TC-32_Out"
    assert read.access is SharedVariableAccess.READ
    assert write.access is SharedVariableAccess.WRITE  # type: ignore[attr-defined]
    # the heap omits terminal_ID for the default 0
    assert sorted(read.terminal_ids.values()) == [0, 1, 2]


def test_variable_name_is_not_taken_from_the_label_text() -> None:
    node = _parse(_node(100, "Out", write=False))
    assert isinstance(node, ParsedSharedVariableNode)
    assert node.variable_name == "Out"
    assert node.label is not None and "Shared.lvlib" in node.label


def test_non_ascii_name_is_decoded_with_the_labview_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lvkit.text_encoding.labview_text_encoding", lambda *_: "cp1252"
    )
    node = _parse(_node(100, "Température_Out", write=False, encoding="cp1252"))
    assert isinstance(node, ParsedSharedVariableNode)
    assert node.variable_name == "Température_Out"


def test_a_node_without_a_name_is_an_error() -> None:
    xml = _node(100, "Out", write=False).replace("<name4>4F7574</name4>", "")
    with pytest.raises(ValueError, match="no <name4>"):
        _parse(xml)


def test_graph_node_names_terminals_by_terminal_id(tmp_path: Path) -> None:
    graph, vi = _load(
        tmp_path,
        _node(100, "TC-32_Out", write=False),
        _node(200, "TC-32_Out", write=True),
    )
    nodes = _shared(graph, vi)
    read, write = nodes["100"], nodes["200"]
    assert (read.variable_name, read.is_write) == ("TC-32_Out", False)
    assert (write.variable_name, write.is_write) == ("TC-32_Out", True)
    assert [(t.index, t.name, t.direction) for t in read.terminals] == [
        (0, "Variable", "output"),
        (1, "error in (no error)", "input"),
        (2, "error out", "output"),
    ]
    assert [(t.index, t.name, t.direction) for t in write.terminals] == [
        (0, "Variable", "input"),
        (1, "error in (no error)", "input"),
        (2, "error out", "output"),
    ]


def test_an_unconfirmed_terminal_id_keeps_only_its_number(tmp_path: Path) -> None:
    graph, vi = _load(
        tmp_path, _node(100, "TC-32_Out", write=False, extra_terminal_id=5)
    )
    (node,) = _shared(graph, vi).values()
    extra = next(t for t in node.terminals if t.index == 5)
    assert extra.name is None


def test_describe_text_and_json(tmp_path: Path) -> None:
    graph, vi = _load(
        tmp_path,
        _node(100, "TC-32_Out", write=False),
        _node(200, "TC-32_Out", write=True),
    )
    text = describe_vi(graph, vi)
    # no link-table record names these nodes' library
    assert "Shared Variable `TC-32_Out` read" in text
    assert "Shared Variable `TC-32_Out` write" in text

    body = netlist_to_dict(build_netlist_from_graph(graph, vi))["body"]
    by_uid = {item["uid"]: item for item in body}
    assert by_uid["100"]["kind"] == "shared_variable"
    assert by_uid["100"]["variable"] == "TC-32_Out"
    assert by_uid["100"]["access"] == "read"
    assert by_uid["200"]["access"] == "write"


def test_lvnet_renders_and_round_trips(tmp_path: Path) -> None:
    graph, vi = _load(
        tmp_path,
        _node(100, "TC-32_Out", write=False),
        _node(200, "TC-32 : Out = 1", write=True),
        _node(300, "TC-32_Out", write=False, extra_terminal_id=5),
    )
    module = build_netlist_from_graph(graph, vi)
    text = render_lvnet(module, display_name="Shared.vi", verbose=True)
    assert "shared-variable TC-32_Out_100 : TC-32_Out : read" in text
    assert 'shared-variable TC-32_:_Out_=_1_200 : "TC-32 : Out = 1" : write' in text
    assert "out  Variable" in text and "out  error out" in text
    assert "out  5 " in text  # the unconfirmed terminal ID keeps its number

    parsed = parse_lvnet(text)
    assert _first_mismatch(netlist_signature(module), netlist_signature(parsed)) is None
    rebuilt = reconstruct_module(parsed)
    _assert_graph_identity(module, rebuilt, "Shared.vi")
    assert render_lvnet(rebuilt, display_name="Shared.vi", verbose=True) == text


def test_codegen_names_the_variable_whose_library_is_unknown(
    tmp_path: Path,
) -> None:
    graph, vi = _load(tmp_path, _node(100, "TC-32_Out", write=False))
    with pytest.raises(ConversionGap, match="Shared Variable 'TC-32_Out' cannot be"):
        build_module(graph.get_vi_context(vi), vi, graph=graph)

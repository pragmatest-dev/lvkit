"""A signal lists its terminals with the source usually first; when a terminal's
own DCO declares it an input and another one declares it an output, the output
is the source."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.vi import _extract_wires


def _term(uid: int, dco_class: str, flags: int | None) -> str:
    flag_xml = f"<objFlags>{flags}</objFlags>" if flags is not None else ""
    return (
        f'<SL__arrayElement class="term" uid="{uid}"><dco class="{dco_class}" '
        f'uid="{uid + 1}">{flag_xml}<typeDesc>TypeID(1)</typeDesc></dco>'
        "</SL__arrayElement>"
    )


def _signal(uid: int, terms: list[int]) -> str:
    items = "".join(f'<SL__arrayElement uid="{t}" />' for t in terms)
    return (
        f'<SL__arrayElement class="signal" uid="{uid}"><termList elements='
        f'"{len(terms)}">{items}</termList></SL__arrayElement>'
    )


def _wires(*signals: str) -> list[tuple[str, str]]:
    terms = (
        _term(10, "parm", None)  # an input
        + _term(20, "nmxDCO", 1)  # an output
        + _term(30, "parm", 1)  # an output
        + _term(40, "iUseDCO", 512)  # an input
        + _term(50, "iUseDCO", 513)  # an output
    )
    root = ET.fromstring(
        f"<root>{terms}<signalList>{''.join(signals)}</signalList></root>"
    )
    return [(w.from_term, w.to_term) for w in _extract_wires(root)]


def test_the_output_is_the_source_when_the_signal_lists_the_input_first() -> None:
    assert _wires(_signal(1, [10, 20])) == [("20", "10")]
    assert _wires(_signal(1, [40, 50])) == [("50", "40")]


def test_a_signal_listing_the_output_first_keeps_its_order() -> None:
    assert _wires(_signal(1, [20, 10])) == [("20", "10")]


def test_the_other_sinks_of_a_flipped_signal_keep_their_order() -> None:
    assert _wires(_signal(1, [10, 40, 30])) == [("30", "10"), ("30", "40")]


def test_a_signal_with_no_declared_output_keeps_its_order() -> None:
    assert _wires(_signal(1, [10, 40])) == [("10", "40")]
    assert _wires(_signal(1, [99, 20])) == [("99", "20")]


_AND_BD = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">
<zPlaneList elements="0" /><nodeList elements="2">
<SL__arrayElement class="prim" uid="10"><primResID>1061</primResID>
<termList elements="3">
<SL__arrayElement class="term" uid="11"><dco class="parm" uid="12">
<objFlags>1</objFlags><typeDesc>TypeID(0)</typeDesc></dco></SL__arrayElement>
<SL__arrayElement class="term" uid="13"><objFlags>32768</objFlags>
<dco class="parm" uid="14"><typeDesc>TypeID(0)</typeDesc><parmIndex>1</parmIndex>
</dco></SL__arrayElement>
<SL__arrayElement class="term" uid="15"><objFlags>32768</objFlags>
<dco class="parm" uid="16"><objFlags>65536</objFlags><typeDesc>TypeID(0)</typeDesc>
<parmIndex>2</parmIndex></dco></SL__arrayElement>
</termList></SL__arrayElement>
<SL__arrayElement class="nMux" uid="20"><termList elements="2">
<SL__arrayElement class="term" uid="21"><dco class="nmxDCO" uid="22">
<typeDesc>TypeID(0)</typeDesc></dco></SL__arrayElement>
<SL__arrayElement class="term" uid="23"><dco class="nmxDCO" uid="24">
<objFlags>1</objFlags><typeDesc>TypeID(0)</typeDesc></dco></SL__arrayElement>
</termList><dcoList elements="1"><SL__arrayElement uid="24" /></dcoList>
<dcoAgg uid="22" /></SL__arrayElement>
</nodeList><signalList elements="1">
<SL__arrayElement class="signal" uid="30"><termList elements="2">
<SL__arrayElement uid="13" /><SL__arrayElement uid="23" /></termList>
</SL__arrayElement></signalList></root></SL__rootObject>
"""


def test_a_signal_listing_the_input_first_gives_the_node_its_inputs(tmp_path) -> None:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.models import PrimitiveNode

    bd_xml = tmp_path / "Sig_BDHb.xml"
    bd_xml.write_text(_AND_BD)
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    name = graph.list_vis()[0]
    and_node = next(
        n
        for n in graph.iter_nodes(name)
        if isinstance(n, PrimitiveNode) and n.prim_id == 1061
    )
    assert [(t.index, t.direction) for t in and_node.terminals] == [
        (0, "output"),
        (1, "input"),
        (2, "input"),
    ]
    (edge,) = graph.incoming_edges(f"{name}::13")
    assert edge.terminal_id == f"{name}::23"

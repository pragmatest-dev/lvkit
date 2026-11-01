"""A constant the block diagram holds no value for is a gap."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.models import ValueNotStored
from tests.native_vi import generate, scalar
from tests.test_match_pattern import _OUTPUT_WIRES, _OUTPUTS, STRING, _node

I32 = scalar("NumInt32")


def test_constant_without_a_stored_value_is_one_gap_and_the_rest_generates() -> None:
    gaps: list[Gap] = []
    generated = generate(
        {"s": STRING},
        _OUTPUTS,
        [_node()],
        [("fp:s", "mp:0"), ("c1", "mp:1"), *_OUTPUT_WIRES],
        constants={"c1": (ValueNotStored(), STRING)},
        gaps=gaps,
    )
    (gap,) = gaps
    assert gap.reason == (
        "The constant (uid c1) holds a value the VI stores in its default data "
        "(DFDS), which lvkit does not decode yet; code generation has no value to "
        "write for it."
    )
    body = generated.source[generated.source.index("def native_test") :]
    assert body.splitlines()[1].startswith(
        "    raise NotImplementedError('The constant"
    )
    assert "pattern_match = re.search(" in body
    with pytest.raises(NotImplementedError, match="uid c1"):
        generated.function("abc")


def _term(uid: int, index: int, flags: str = "") -> str:
    return (
        f'<SL__arrayElement class="term" uid="{uid}">{flags}'
        f'<dco class="parm" uid="{uid + 1}"><parmIndex>{index}</parmIndex></dco>'
        "</SL__arrayElement>"
    )


_CONSTANT = (
    '<SL__arrayElement class="sRN" uid="20"><termList elements="1">'
    '<SL__arrayElement class="term" uid="21"><objFlags>1</objFlags>'
    '<dco class="bDConstDCO" uid="22"><typeDesc>TypeID(1)</typeDesc></dco>'
    "</SL__arrayElement></termList></SL__arrayElement>"
)
_BD_XML = (
    "<?xml version='1.0' encoding='utf-8'?>"
    '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
    '<zPlaneList elements="0" /><nodeList elements="2">'
    '<SL__arrayElement class="prim" uid="10"><primResID>1535</primResID>'
    f'<termList elements="2">{_term(11, 1)}{_term(13, 3, "<objFlags>1</objFlags>")}'
    f"</termList></SL__arrayElement>{_CONSTANT}</nodeList>"
    '<signalList elements="1"><SL__arrayElement class="signal" uid="30">'
    '<termList elements="2"><SL__arrayElement uid="21" />'
    '<SL__arrayElement uid="11" /></termList></SL__arrayElement>'
    "</signalList></root></SL__rootObject>"
)


def test_describe_and_lvnet_show_the_constant_as_not_decoded(tmp_path: Path) -> None:
    bd_xml = tmp_path / "C_BDHb.xml"
    bd_xml.write_text(_BD_XML)
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    name = graph.list_vis()[0]
    assert (
        "## Constants\n"
        "  (unnamed): unknown = <value stored in the VI's default data, not decoded>\n"
    ) in describe_vi(graph, name)
    assert render_lvnet(build_netlist_from_graph(graph, name)).endswith(
        '      in   1 : ? = "<value stored in the VI\'s default data, not decoded>"\n'
        "      in   3 : ? default"
    )

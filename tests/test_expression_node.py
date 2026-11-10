"""Expression Node (class="exprNode"): a one-variable formula.

Each case is built as a minimal block diagram (or graph node) and checked in
every view that shows the node, and the generated Python is run.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import FormulaNode
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.parser.expression_text import read_text_record
from lvkit.parser.node_types import ExpressionNode
from lvkit.parser.vi import parse_vi
from lvkit.render import render_vi
from tests.native_vi import Generated, array, generate, result_values, scalar

_TERMS = (
    '<termList elements="2">'
    '<SL__arrayElement class="term" uid="11">'
    '<dco class="bDDCO" uid="12"><termBounds>(100, 100, 108, 117)</termBounds>'
    "</dco></SL__arrayElement>"
    '<SL__arrayElement class="term" uid="13"><objFlags>4096</objFlags>'
    '<dco class="bDDCO" uid="14"><objFlags>1</objFlags>'
    "<termBounds>(100, 190, 108, 207)</termBounds>"
    "</dco></SL__arrayElement></termList>"
)


def _bd_xml(node_class: str, label_children: str, extra: str = "") -> str:
    label_tag = "textNodeLabel" if node_class == "exprNode" else "formula"
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
        '<zPlaneList elements="0" /><nodeList elements="1">'
        f'<SL__arrayElement class="{node_class}" uid="10">{_TERMS}'
        "<bounds>(100, 100, 117, 207)</bounds>"
        f'<{label_tag} class="label" uid="20">{label_children}</{label_tag}>'
        f"{extra}</SL__arrayElement></nodeList>"
        '<signalList elements="0" /></root></SL__rootObject>'
    )


def _inline_text(text: str) -> str:
    return f'<textRec class="textHair"><text>"{text}"</text></textRec>'


def _write(tmp_path: Path, xml: str) -> Path:
    bd_xml = tmp_path / "Expr_BDHb.xml"
    bd_xml.write_text(xml)
    return bd_xml


def _text_record(text: str) -> bytes:
    raw = text.encode("latin-1")
    return b"\x00" * 72 + struct.pack(">I", len(raw)) + raw


def _node(script: str | None, lv_type=None) -> FormulaNode:
    lv_type = lv_type or scalar("NumFloat64")
    return FormulaNode(
        id="expr",
        vi_path="Native Test.vi",
        name="Expression Node",
        node_type="exprNode",
        script=script,
        terminals=[
            Terminal(id="expr:0", index=0, direction="input", lv_type=lv_type),
            Terminal(id="expr:1", index=1, direction="output", lv_type=lv_type),
        ],
    )


def _run(script: str | None, lv_type) -> Generated:
    return generate(
        {"in": lv_type},
        {"out": lv_type},
        [_node(script, lv_type)],
        [("fp:in", "expr:0"), ("expr:1", "fp:out")],
    )


def _call(script: str, lv_type, value):
    (result,) = result_values(_run(script, lv_type).function(**{"in_": value}))
    return result


# --- the formula text -------------------------------------------------------


def test_parser_reads_the_formula_text(tmp_path: Path) -> None:
    bd_xml = _write(tmp_path, _bd_xml("exprNode", _inline_text("2*x+3")))
    nodes = parse_vi(bd_xml=bd_xml).block_diagram.nodes
    (node,) = nodes
    assert isinstance(node, ExpressionNode)
    assert node.script == "2*x+3"
    assert node.text_record is None


def test_formula_text_is_trimmed(tmp_path: Path) -> None:
    bd_xml = _write(tmp_path, _bd_xml("exprNode", _inline_text(" h ")))
    (node,) = parse_vi(bd_xml=bd_xml).block_diagram.nodes
    assert isinstance(node, ExpressionNode)
    assert node.script == "h"


def test_text_record_formula_is_read_from_the_sidecar(tmp_path: Path) -> None:
    (tmp_path / "Expr_TRec40.bin").write_bytes(_text_record("(x/pi)*180.0"))
    bd_xml = _write(tmp_path, _bd_xml("exprNode", "<textRec>28</textRec>"))
    (node,) = parse_vi(bd_xml=bd_xml).block_diagram.nodes
    assert isinstance(node, ExpressionNode)
    assert node.text_record == 0x28
    assert node.script == "(x/pi)*180.0"


def test_text_record_without_sidecar_has_no_script(tmp_path: Path) -> None:
    bd_xml = _write(tmp_path, _bd_xml("exprNode", "<textRec>28</textRec>"))
    (node,) = parse_vi(bd_xml=bd_xml).block_diagram.nodes
    assert isinstance(node, ExpressionNode)
    assert node.script is None


def test_text_record_with_wrong_length_is_not_read(tmp_path: Path) -> None:
    bd_xml = tmp_path / "Expr_BDHb.xml"
    (tmp_path / "Expr_TRec3.bin").write_bytes(_text_record("x+1") + b"extra")
    assert read_text_record(bd_xml, 3) is None
    (tmp_path / "Expr_TRec4.bin").write_bytes(b"\x00" * 10)
    assert read_text_record(bd_xml, 4) is None
    (tmp_path / "Expr_TRec5.bin").write_bytes(_text_record("x+1"))
    assert read_text_record(bd_xml, 5) == "x+1"


# --- every view -------------------------------------------------------------


def test_every_view_shows_the_formula(tmp_path: Path) -> None:
    bd_xml = _write(tmp_path, _bd_xml("exprNode", _inline_text("2*x+3")))
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml, layout=True)
    vi_name = graph.list_vis()[0]

    assert describe_vi(graph, vi_name).endswith(
        "## Operations\nExpression Node `2*x+3`"
    )
    assert render_lvnet(build_netlist_from_graph(graph, vi_name)).endswith(
        "    formula-node Expression_Node_10 : Expression Node\n"
        "      in   0 : ? default\n"
        "      out  1 : ?\n"
        "      # TODO(lvnet): formula-node script rendering needs the `script` "
        "field plumbed onto the model first (md §17 item 6)"
    )
    svg = render_vi(graph, vi_name, interactive=False)
    assert svg is not None
    assert ">2*x+3</text>" in svg


def test_formula_node_views_are_unchanged(tmp_path: Path) -> None:
    bd_xml = _write(
        tmp_path,
        _bd_xml(
            "fBox",
            '<text>"y = x + 1;"</text>',
            '<formula><text>"y = x;"</text></formula>',
        ),
    )
    (node,) = parse_vi(bd_xml=bd_xml).block_diagram.nodes
    assert not isinstance(node, ExpressionNode)
    assert node.node_type == "fBox"
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    vi_name = graph.list_vis()[0]
    assert describe_vi(graph, vi_name).endswith(
        "## Operations\nFormula Node [Formula Node]"
    )


# --- generated Python, run --------------------------------------------------


def test_double_expression() -> None:
    assert _call("2*x+3", scalar("NumFloat64"), 4.0) == 11.0


def test_integer_input_gives_an_integer_result() -> None:
    result = _call("2*x+3", scalar("NumInt32"), 4)
    assert result == 11
    assert isinstance(result, int)


def test_integer_result_rounds_to_nearest_even_and_wraps() -> None:
    assert _call("x/2", scalar("NumInt32"), 5) == 2
    assert _call("x/2", scalar("NumInt32"), 7) == 4
    assert _call("x*2", scalar("NumInt8"), 100) == -56


def test_function_call_and_constant() -> None:
    assert _call("sin(x)", scalar("NumFloat64"), 0.0) == 0.0
    assert _call("(x/pi)*180.0", scalar("NumFloat64"), math.pi) == pytest.approx(180.0)
    assert _call("sqrt(x)", scalar("NumFloat64"), 9.0) == 3.0


def test_any_variable_name() -> None:
    assert _call("h+10", scalar("NumFloat64"), 1.5) == 11.5


def test_array_input_applies_the_expression_to_each_element() -> None:
    assert _call("2*x+3", array("NumFloat64"), [1.0, 2.0, 3.0]) == [5.0, 7.0, 9.0]
    assert _call("x+1", array("NumInt32"), [1, 2]) == [2, 3]
    assert _call("x+1", array("NumInt32"), []) == []


def test_two_dimensional_array_input() -> None:
    value = [[1.0, 2.0], [3.0]]
    assert _call("x*2", array("NumFloat64", 2), value) == [[2.0, 4.0], [6.0]]


def test_expression_function_is_a_module_level_function() -> None:
    source = _run("2*x+3", scalar("NumFloat64")).source
    assert "def _expression_native_test_expr(*, x):" in source
    assert "lvkit" not in source


def test_text_record_gap_raises_where_the_node_runs() -> None:
    with pytest.raises(NotImplementedError, match="has no formula text"):
        _run(None, scalar("NumFloat64")).function(in_=1.0)


def test_two_variables_is_a_gap() -> None:
    with pytest.raises(NotImplementedError, match="more than one variable"):
        _run("x+y", scalar("NumFloat64")).function(in_=1.0)


def test_unknown_function_is_a_gap() -> None:
    with pytest.raises(NotImplementedError, match="mystery"):
        _run("mystery(x)", scalar("NumFloat64")).function(in_=1.0)


def test_cluster_input_is_a_gap() -> None:
    cluster = LVType(kind=LVTypeKind.CLUSTER)
    with pytest.raises(NotImplementedError, match="only a number or an array"):
        _run("x+1", cluster).function(in_=None)

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
from lvkit.graph.lvnet_parse import netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import FormulaNode
from lvkit.graph.netlist import build_netlist_from_graph, netlist_to_dict
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
from lvkit.parser.expression_text import read_text_record
from lvkit.parser.node_types import ExpressionNode
from lvkit.parser.vi import parse_vi
from lvkit.render import render_vi
from tests.native_vi import (
    Generated,
    array,
    generate,
    node,
    result_values,
    scalar,
)

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
        '    expression-node Expression_Node_10 : "2*x+3"\n'
        "      in   0 : ? default\n"
        "      out  1 : ?"
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


# --- the formula in lvnet and JSON ------------------------------------------

_FORMULA_SCRIPT = 'a = "q";\nb = x\t;'


def _netlist(tmp_path: Path, xml: str):
    graph = InMemoryVIGraph()
    graph.load_vi(_write(tmp_path, xml), layout=True)
    return build_netlist_from_graph(graph, graph.list_vis()[0])


def _body_lines(text: str) -> str:
    """The node's declaration and terminal block (after the ``vi`` line,
    ``block-diagram :`` header included)."""
    return "\n".join(text.splitlines()[1:])


def _assert_round_trips(module, text: str, verbose: bool) -> None:
    parsed = parse_lvnet(text)
    assert netlist_signature(parsed) == netlist_signature(module)
    assert render_lvnet(reconstruct_module(parsed), verbose=verbose) == text


@pytest.mark.parametrize("verbose", [False, True])
def test_formula_node_script_is_one_quoted_literal(
    tmp_path: Path, verbose: bool
) -> None:
    module = _netlist(tmp_path, _bd_xml("fBox", f'<text>"{_FORMULA_SCRIPT}"</text>'))
    text = render_lvnet(module, verbose=verbose)
    assert _body_lines(text) == (
        "  block-diagram :\n"
        '    formula-node Formula_Node_10 : "a = \\"q\\";\\nb = x\\t;"\n'
        "      in   0 : ? default\n"
        "      out  1 : ?"
    )
    _assert_round_trips(module, text, verbose)


@pytest.mark.parametrize("verbose", [False, True])
def test_expression_node_formula_is_one_quoted_literal(
    tmp_path: Path, verbose: bool
) -> None:
    module = _netlist(tmp_path, _bd_xml("exprNode", _inline_text("2*x+3")))
    text = render_lvnet(module, verbose=verbose)
    assert _body_lines(text) == (
        "  block-diagram :\n"
        '    expression-node Expression_Node_10 : "2*x+3"\n'
        "      in   0 : ? default\n"
        "      out  1 : ?"
    )
    _assert_round_trips(module, text, verbose)


@pytest.mark.parametrize("verbose", [False, True])
def test_expression_node_without_text_has_no_component(
    tmp_path: Path, verbose: bool
) -> None:
    module = _netlist(tmp_path, _bd_xml("exprNode", "<textRec>28</textRec>"))
    text = render_lvnet(module, verbose=verbose)
    assert _body_lines(text) == (
        "  block-diagram :\n"
        "    expression-node Expression_Node_10\n"
        "      in   0 : ? default\n"
        "      out  1 : ?"
    )
    _assert_round_trips(module, text, verbose)


def test_json_has_the_script_of_formula_and_expression_nodes(
    tmp_path: Path,
) -> None:
    def instance(xml: str, name: str) -> dict:
        (item,) = netlist_to_dict(_netlist(tmp_path / name, xml))["body"]
        return item

    (tmp_path / "f").mkdir()
    (tmp_path / "e").mkdir()
    (tmp_path / "n").mkdir()
    formula = instance(_bd_xml("fBox", f'<text>"{_FORMULA_SCRIPT}"</text>'), "f")
    assert formula["name"] == "Formula Node"
    assert formula["script"] == _FORMULA_SCRIPT
    expression = instance(_bd_xml("exprNode", _inline_text("2*x+3")), "e")
    assert expression["name"] == "Expression Node"
    assert expression["script"] == "2*x+3"
    no_text = instance(_bd_xml("exprNode", "<textRec>28</textRec>"), "n")
    assert no_text["script"] is None


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


def _body(script: str, lv_type) -> str:
    """The generated lines of the VI function that belong to the node."""
    lines = _run(script, lv_type).source.splitlines()
    return "\n".join(line.strip() for line in lines if line.strip().startswith("expr"))


def test_expression_is_written_inline() -> None:
    source = _run("2*x+3", scalar("NumFloat64")).source
    assert "def _expression" not in source
    assert "_result" not in source
    assert "lvkit" not in source
    assert _body("2*x+3", scalar("NumFloat64")) == "expr = 2 * in_ + 3"


def test_integer_expression_rounds_and_wraps_inline() -> None:
    assert _body("2*x+3", scalar("NumUInt16")) == (
        "expr = ctypes.c_uint16(round(2 * in_ + 3)).value"
    )


def test_array_expression_is_a_comprehension() -> None:
    assert _body("2*x+3", array("NumUInt16")) == (
        "expr = [ctypes.c_uint16(round(2 * x + 3)).value for x in in_]"
    )
    assert _body("x*2", array("NumFloat64", 2)) == (
        "expr = [[x * 2 for x in x_0] for x_0 in in_]"
    )


def test_loop_variable_is_a_safe_name() -> None:
    assert "for round_ in in_]" in _body("round+1", array("NumFloat64"))
    assert _call("round+1", array("NumFloat64"), [1.0]) == [2.0]


def test_guarded_function_runs_inline_and_in_a_comprehension() -> None:
    assert _call("sqrt(x)", scalar("NumFloat64"), 16.0) == 4.0
    assert math.isnan(_call("sqrt(x)", scalar("NumFloat64"), -1.0))
    values = _call("sqrt(x)", array("NumFloat64"), [4.0, -1.0, 9.0])
    assert values[0] == 2.0 and math.isnan(values[1]) and values[2] == 3.0
    assert "_t1 :=" in _body("sqrt(x)", array("NumFloat64"))


def test_computed_input_is_bound_once() -> None:
    float64 = scalar("NumFloat64")
    generated = generate(
        {},
        {"out": float64},
        [_node("x*x+x", float64)],
        [("c", "expr:0"), ("expr:1", "fp:out")],
        constants={"c": (4.0, float64)},
    )
    body = "\n".join(
        line.strip()
        for line in generated.source.splitlines()
        if line.strip().startswith("expr")
    )
    assert body == ("expr_input = 4.0\nexpr = expr_input * expr_input + expr_input")
    assert result_values(generated.function())[0] == 20.0


def test_named_input_is_not_rebound() -> None:
    assert _body("x*x+x", scalar("NumFloat64")) == "expr = in_ * in_ + in_"


def test_unbundled_input_is_written_in_place() -> None:
    """An Unbundle output read straight from a control (``point[1]``) is not
    bound to a new name first; the expression reads it in place."""
    float64 = scalar("NumFloat64")
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="x", type=float64),
            ClusterField(name="y", type=float64),
        ],
    )
    unbundle = node("ub", None, [], node_type="nMux")
    unbundle.terminals = [
        Terminal(
            id="ub:0", index=0, direction="input", nmux_role="agg", lv_type=cluster
        ),
        Terminal(
            id="ub:1",
            index=1,
            direction="output",
            nmux_role="list",
            nmux_field_index=1,
            lv_type=float64,
        ),
    ]
    generated = generate(
        {"point": cluster},
        {"out": float64},
        [unbundle, _node("2*x+3", float64)],
        [("fp:point", "ub:0"), ("ub:1", "expr:0"), ("expr:1", "fp:out")],
    )
    lines = [line.strip() for line in generated.source.splitlines()]
    assert not any(line.startswith("expr_input") for line in lines)
    assert "expr = 2 * point[1] + 3" in lines
    assert result_values(generated.function((1.0, 4.0)))[0] == 11.0


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

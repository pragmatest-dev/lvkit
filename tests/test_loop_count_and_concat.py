"""For Loop count conversion, concatenating output tunnels and shift-register
variable names: each case generates a loop from a tiny diagram and runs or
inspects the code."""

from __future__ import annotations

import ast
from types import SimpleNamespace

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import loop
from lvkit.codegen.nodes.subvi import _adapted_value
from lvkit.graph.models import PrimitiveNode, Wire
from lvkit.models import LVType, LVTypeKind, Terminal, Tunnel, TunnelMode
from tests.helpers import register_nodes
from tests.test_loop_codegen import _mk_loop, _output_tunnel, _run_statements


def _number(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def _for_loop_over(count_expr: str, count_type: str, mode=TunnelMode.INDEXING):
    """A For loop whose count is the expression ``count_expr`` (a source node's
    output of type ``count_type``); its output tunnel collects ``[i, i]``."""
    data_flow = [
        Wire.from_terminals(from_terminal_id="n_src", to_terminal_id="n_outer"),
        Wire.from_terminals(from_terminal_id="val_src", to_terminal_id="out_inner"),
    ]
    ctx = CodeGenContext.from_wires(data_flow)
    source = PrimitiveNode(
        vi_path="test.vi",
        id="src",
        name="Source",
        prim_id=1,
        terminals=[
            Terminal(
                id="n_src",
                index=0,
                direction="output",
                lv_type=_number(count_type),
            )
        ],
    )
    register_nodes(ctx.graph, [source])
    ctx.graph._term_to_node["n_src"] = "src"  # the typed node owns the terminal
    ctx.bind("n_src", count_expr)
    ctx.bind("val_src", "[i, i]")
    loop_op = _mk_loop(
        ctx,
        loop_type="forLoop",
        tunnels=[
            Tunnel(
                outer_terminal_uid="n_outer",
                inner_terminal_uid="n_inner",
                tunnel_type="lMax",
            )
        ],
        terminals=[*_output_tunnel("out_outer", "out_inner", mode)],
    )
    fragment = loop.generate(loop_op, ctx)
    result = _run_statements(fragment.statements)
    return result[fragment.bindings["out_outer"]]


def test_a_float_count_is_rounded_to_nearest_ties_to_even():
    assert len(_for_loop_over("2.5", "NumFloat64")) == 2
    assert len(_for_loop_over("3.5", "NumFloat64")) == 4
    assert len(_for_loop_over("0.3 * 10", "NumFloat64")) == 3
    assert len(_for_loop_over("-1.2", "NumFloat64")) == 0


def test_an_integer_count_is_used_as_it_is():
    assert len(_for_loop_over("3", "NumInt32")) == 3


def test_a_concatenating_tunnel_joins_each_iterations_array():
    assert _for_loop_over("3", "NumInt32", TunnelMode.CONCATENATING) == [
        0,
        0,
        1,
        1,
        2,
        2,
    ]


def test_an_indexing_tunnel_still_stacks_the_arrays():
    assert _for_loop_over("3", "NumInt32", TunnelMode.INDEXING) == [
        [0, 0],
        [1, 1],
        [2, 2],
    ]


def _call_output(own: LVType | None, declared: LVType | None):
    term = Terminal(id="t", index=2, direction="output", lv_type=own)
    indicator = (
        Terminal(id="d", index=2, direction="output", lv_type=declared)
        if declared is not None
        else None
    )
    graph = SimpleNamespace(callee_pane_output=lambda _tid: indicator)
    return term, SimpleNamespace(graph=graph)


def test_an_adapt_to_type_output_reads_the_value_of_the_returned_variant():
    term, ctx = _call_output(LVType(kind=LVTypeKind.CLUSTER), _number("Variant"))
    assert _adapted_value(term, ctx) == ".value"


def test_a_variant_output_and_a_typed_output_are_left_alone():
    variant = _number("Variant")
    cluster = LVType(kind=LVTypeKind.CLUSTER)
    assert _adapted_value(*_call_output(variant, variant)) == ""
    assert _adapted_value(*_call_output(cluster, cluster)) == ""
    assert _adapted_value(*_call_output(cluster, None)) == ""


def test_a_shift_register_variable_is_not_taken_by_a_node_output():
    ctx = CodeGenContext.from_wires(
        [Wire.from_terminals(from_terminal_id="seed", to_terminal_id="lsr_outer")]
    )
    ctx.bind("seed", "0")
    loop_op = _mk_loop(
        ctx,
        loop_type="forLoop",
        tunnels=[
            Tunnel(
                outer_terminal_uid="lsr_outer",
                inner_terminal_uid="lsr_inner",
                tunnel_type="lSR",
            ),
            Tunnel(
                outer_terminal_uid="rsr_outer",
                inner_terminal_uid="rsr_inner",
                tunnel_type="rSR",
            ),
        ],
    )
    fragment = loop.generate(loop_op, ctx)
    shift_var = next(
        s.targets[0].id
        for s in fragment.statements
        if isinstance(s, ast.Assign) and isinstance(s.targets[0], ast.Name)
    )
    assert ctx.make_output_var(shift_var, "other_node") != shift_var

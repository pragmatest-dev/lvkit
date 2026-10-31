"""Gaps: what code generation cannot convert becomes a warning and a
``raise NotImplementedError(<reason>)`` where the node would run."""

from __future__ import annotations

import ast
import builtins

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.gaps import Gap, catch_gap, gap_fragment
from lvkit.codegen.nodes import generate as generate_node
from lvkit.codegen.nodes.primitive import raise_unknown
from lvkit.codegen.nodes.subvi import _raise_vilib_resolution
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import (
    CaseStructureNode,
    LoopNode,
    PrimitiveNode,
    VIContext,
    VINode,
    Wire,
)
from lvkit.models import (
    CaseFrame,
    FPTerminal,
    LVType,
    LVTypeKind,
    Terminal,
    TunnelMode,
    TunnelTerminal,
)
from lvkit.primitive_resolver import PrimitiveResolutionNeeded
from lvkit.vilib_resolver import ResolutionContext, VILibResolutionNeeded
from tests.helpers import build_graph
from tests.test_loop_codegen import _mk_loop, _output_tunnel

# ---------------------------------------------------------------------------
# Diagnostics the gap errors carry
# ---------------------------------------------------------------------------


def test_primitive_resolution_needed_carries_qualified_vi_name() -> None:
    exc = PrimitiveResolutionNeeded(
        prim_id=9999,
        prim_name="Imaginary Primitive",
        terminals=[{"index": 0, "direction": "in", "name": "x", "type": "DBL"}],
        vi_name="Foo.vi",
        qualified_vi_name="MyLib.lvlib:Bar.lvclass:Foo.vi",
    )
    assert exc.qualified_vi_name == "MyLib.lvlib:Bar.lvclass:Foo.vi"
    assert "MyLib.lvlib:Bar.lvclass:Foo.vi" in str(exc)
    assert ".lvkit/primitives.json" in str(exc)
    assert "data/primitives.json" in str(exc)
    assert exc.summary() == "primitive 9999 (Imaginary Primitive) is not known"
    assert isinstance(exc, ConversionGap)


def test_resolution_context_carries_qualified_path() -> None:
    ctx = ResolutionContext(
        caller_vi="Caller.vi",
        caller_qualified_name="MyProj.lvlib:Caller.vi",
        qualified_path="<vilib>/Utility/error.llb/Some VI.vi",
        wire_types=["idx_0 (in, Boolean, wired)"],
    )
    exc = VILibResolutionNeeded("Some VI.vi", context=ctx)
    msg = str(exc)
    assert "<vilib>/Utility/error.llb/Some VI.vi" in msg
    assert "MyProj.lvlib:Caller.vi" in msg
    assert ".lvkit/vilib/" in msg
    assert "data/vilib/" in msg
    assert exc.summary() == "the vi.lib VI 'Some VI.vi' has no terminal mapping"


def test_vinode_qualified_path_field_exists() -> None:
    node = VINode(
        id="vi_uid_42",
        vi_path="Caller.vi",
        name="Foo.vi",
        node_type="iUse",
        terminals=[],
        qualified_path="<vilib>/Foo/bar.vi",
    )
    assert node.qualified_path == "<vilib>/Foo/bar.vi"


def test_unknown_primitive_and_vilib_raise_their_own_gap() -> None:
    node = PrimitiveNode(
        id="p", vi_path="t.vi", name="Mystery", terminals=[], prim_id=99999
    )
    ctx = CodeGenContext(vi_name="Caller.vi", qualified_vi_name="Lib:Caller.vi")
    with pytest.raises(PrimitiveResolutionNeeded) as primitive:
        raise_unknown(node, 99999, ctx)
    assert primitive.value.prim_id == "99999"
    assert primitive.value.qualified_vi_name == "Lib:Caller.vi"

    call = VINode(
        id="s", vi_path="t.vi", name="Imaginary VI.vi", terminals=[], node_type="iUse"
    )
    with pytest.raises(VILibResolutionNeeded) as vilib:
        _raise_vilib_resolution(call, ctx, vilib_vi=None)
    assert vilib.value.vi_name == "Imaginary VI.vi"


# ---------------------------------------------------------------------------
# The fragment of a gap node
# ---------------------------------------------------------------------------


def _lv(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def _fp(id: str, direction: str, name: str, lv_type: str) -> FPTerminal:
    return FPTerminal(
        id=id,
        index=0,
        direction=direction,
        name=name,
        is_public=True,
        is_indicator=direction == "output",
        lv_type=_lv(lv_type),
    )


def test_a_gap_node_binds_only_its_outer_outputs_and_skips_error_clusters() -> None:
    error = LVType(kind=LVTypeKind.CLUSTER, classname="errorcluster")
    node = LoopNode(
        id="loop",
        vi_path="t.vi",
        name="Loop",
        node_type="whileLoop",
        loop_type="whileLoop",
        terminals=[
            TunnelTerminal(
                id="out_outer",
                index=0,
                direction="output",
                boundary="outer",
                paired_id="out_inner",
                tunnel_type="lpTun",
                name="total",
                lv_type=_lv("NumInt32"),
            ),
            TunnelTerminal(
                id="in_inner",
                index=1,
                direction="output",
                boundary="inner",
                paired_id="in_outer",
                tunnel_type="lpTun",
                name="seed",
                lv_type=_lv("NumInt32"),
            ),
            Terminal(
                id="err_out",
                index=2,
                direction="output",
                name="error out",
                lv_type=error,
            ),
        ],
    )
    ctx = CodeGenContext(vi_name="t.vi")
    fragment = gap_fragment(node, ctx, "because")
    assert fragment.bindings == {"out_outer": "total"}
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    assert ast.unparse(module) == "total = None\nraise NotImplementedError('because')"


# ---------------------------------------------------------------------------
# Whole VIs, generated and run
# ---------------------------------------------------------------------------


def _mystery(parent: str | None = None, frame: str | None = None) -> PrimitiveNode:
    return PrimitiveNode(
        id="mystery",
        vi_path="Gap.vi",
        name="Mystery",
        prim_id=99999,
        parent=parent,
        frame=frame,
        terminals=[
            Terminal(id="m_in", index=1, direction="input", lv_type=_lv("NumInt32")),
            Terminal(
                id="m_out",
                index=0,
                direction="output",
                name="result",
                lv_type=_lv("NumInt32"),
            ),
        ],
    )


def _build(context: VIContext, nodes: list) -> tuple[str, list[Gap]]:
    graph = build_graph(context, context.name, nodes)
    gaps: list[Gap] = []
    return build_module(context, context.name, graph=graph, gaps=gaps), gaps


def _with_the_raise_shown_as(source: str, marker: str) -> list[str]:
    """The lines of ``source`` with the one gap raise (a message of several lines
    in one source line) replaced by ``marker``, after checking what it says."""
    lines = source.splitlines()
    (index,) = [
        i for i, line in enumerate(lines) if "raise NotImplementedError(" in line
    ]
    raised = lines[index].strip()
    assert raised.startswith(
        "raise NotImplementedError('primitive 99999 (Mystery) is not known\\n"
        "gap kind: unknown_primitive\\nVI: Gap.vi\\nnode: Mystery\\n"
    )
    assert "Full connector pane" in raised
    assert "index=0 direction=output type=NumInt32 wired name=result" in raised
    assert "Fix: add primitive 99999 to .lvkit/primitives.json" in raised
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    lines[index] = indent + marker
    return lines


def _run(source: str, function: str, *args):
    namespace: dict = {}
    exec(compile(source, "gap_test", "exec"), namespace)  # noqa: S102
    return namespace[function](*args)


def test_an_unknown_primitive_at_the_top_raises_and_the_code_after_it_stays() -> None:
    context = VIContext(
        name="Gap.vi",
        inputs=[_fp("fp:x", "input", "x", "NumInt32")],
        outputs=[_fp("fp:y", "output", "y", "NumInt32")],
        data_flow=[
            Wire.from_terminals(from_terminal_id="fp:x", to_terminal_id="m_in"),
            Wire.from_terminals(from_terminal_id="m_out", to_terminal_id="fp:y"),
        ],
    )
    source, gaps = _build(context, [_mystery()])
    assert _with_the_raise_shown_as(source, "<raise>") == [
        "from __future__ import annotations",
        "from typing import Any, NamedTuple",
        "",
        "class GapResult(NamedTuple):",
        "    y: int",
        "",
        "def gap(x: int=0) -> GapResult:",
        "    result = None",
        "    <raise>",
        "    return GapResult(y=result)",
    ]
    (gap,) = gaps
    assert (gap.vi, gap.node_id, gap.node_name) == ("Gap.vi", "mystery", "Mystery")
    assert (gap.kind, gap.identifier, gap.name) == (
        "unknown_primitive",
        "99999",
        "Mystery",
    )
    with pytest.raises(NotImplementedError, match=r"primitive 99999 \(Mystery\)"):
        _run(source, "gap", 1)


def test_an_unknown_primitive_in_a_loop_raises_in_the_loop() -> None:
    loop = LoopNode(
        id="loop1",
        vi_path="Gap.vi",
        name="While Loop",
        node_type="whileLoop",
        loop_type="whileLoop",
        stop_condition_terminal="cond",
        terminals=[
            TunnelTerminal(
                id="lp_out",
                index=0,
                direction="output",
                boundary="outer",
                tunnel_type="lMax",
                paired_id="lp_in",
                lv_type=_lv("NumInt32"),
            ),
            TunnelTerminal(
                id="lp_in",
                index=1,
                direction="input",
                boundary="inner",
                tunnel_type="lMax",
                paired_id="lp_out",
                lv_type=_lv("NumInt32"),
            ),
            Terminal(id="cond", index=2, direction="input", lv_type=_lv("Boolean")),
        ],
    )
    context = VIContext(
        name="Gap.vi",
        inputs=[_fp("fp:x", "input", "x", "NumInt32")],
        outputs=[_fp("fp:y", "output", "y", "NumInt32")],
        data_flow=[
            Wire.from_terminals(from_terminal_id="m_out", to_terminal_id="lp_in"),
            Wire.from_terminals(from_terminal_id="lp_out", to_terminal_id="fp:y"),
        ],
    )
    source, gaps = _build(context, [loop, _mystery(parent="loop1")])
    assert _with_the_raise_shown_as(source, "<raise>")[6:] == [
        "def gap(x: int=0) -> GapResult:",
        "    while True:",
        "        result = None",
        "        <raise>",
        "        break",
        "    return GapResult(y=None)",
    ]
    assert [g.node_name for g in gaps] == ["Mystery"]
    with pytest.raises(NotImplementedError, match="Mystery"):
        _run(source, "gap", 1)


def test_an_unknown_primitive_in_a_case_raises_in_its_frame_only() -> None:
    case = CaseStructureNode(
        id="case1",
        vi_path="Gap.vi",
        name="Case Structure",
        node_type="caseStruct",
        terminals=[
            Terminal(id="sel", index=0, direction="input", lv_type=_lv("Boolean")),
            TunnelTerminal(
                id="outer",
                index=1,
                direction="output",
                boundary="outer",
                tunnel_type="csTun",
                paired_id="inner_true",
                lv_type=_lv("NumInt32"),
            ),
            TunnelTerminal(
                id="inner_true",
                index=2,
                direction="input",
                boundary="inner",
                tunnel_type="csTun",
                paired_id="outer",
                frame="True",
                lv_type=_lv("NumInt32"),
            ),
        ],
        selector_terminal="sel",
        frames=[
            CaseFrame(selector_value="True", boolean_value=True),
            CaseFrame(selector_value="False", boolean_value=False),
        ],
    )
    context = VIContext(
        name="Gap.vi",
        inputs=[_fp("fp:flag", "input", "flag", "Boolean")],
        outputs=[_fp("fp:y", "output", "y", "NumInt32")],
        data_flow=[
            Wire.from_terminals(from_terminal_id="fp:flag", to_terminal_id="sel"),
            Wire.from_terminals(from_terminal_id="m_out", to_terminal_id="inner_true"),
            Wire.from_terminals(from_terminal_id="outer", to_terminal_id="fp:y"),
        ],
    )
    source, gaps = _build(context, [case, _mystery(parent="case1", frame="True")])
    assert _with_the_raise_shown_as(source, "<raise>")[6:] == [
        "def gap(flag: bool=False) -> GapResult:",
        "    case_output = 0",
        "    if flag:",
        "        result = None",
        "        <raise>",
        "        case_output = result",
        "    return GapResult(y=case_output)",
    ]
    assert len(gaps) == 1
    assert _run(source, "gap", False).y == 0
    with pytest.raises(NotImplementedError, match="Mystery"):
        _run(source, "gap", True)


def test_a_gap_of_a_structure_replaces_the_gaps_inside_it() -> None:
    """The structure cannot be converted, so the code of its nodes is dropped and
    so are their warnings: the warnings match the raises."""
    ctx = CodeGenContext.from_wires(
        [Wire.from_terminals(from_terminal_id="iter", to_terminal_id="out_inner")]
    )
    loop = _mk_loop(
        ctx,
        id="loop1",
        loop_type="forLoop",
        terminals=[
            *_output_tunnel(
                "out_outer", "out_inner", TunnelMode.INDEXING, condition="cond"
            ),
            Terminal(id="iter", index=10, direction="output"),
            Terminal(id="cond", index=11, direction="input"),
        ],
        inner_nodes=[_mystery()],
    )
    ctx.vi_name = "t.vi"
    fragment = generate_node(loop, ctx)
    assert [(g.node_id, g.reason) for g in ctx.gaps] == [
        ("loop1", "Conditional tunnel's condition terminal is not wired")
    ]
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    assert ast.unparse(module) == (
        "out_0 = None\n"
        "raise NotImplementedError(\"Conditional tunnel's condition terminal is not "
        'wired\\ngap kind: unsupported_node\\nVI: t.vi\\nnode: Loop")'
    )


def test_a_gap_takes_back_what_the_node_added_to_the_module() -> None:
    node = PrimitiveNode(id="p", vi_path="t.vi", name="Call", terminals=[])
    ctx = CodeGenContext(vi_name="t.vi")
    ctx.add_import("import before")
    with catch_gap(node, ctx) as guard:
        ctx.add_import("import ctypes")
        ctx.module_declarations.declare(("library", "x"), "_lib", lambda n: f"{n} = 1")
        ctx.formula_helpers.append(ast.parse("x = 1").body[0])
        ctx.module_globals["g"] = ast.parse("g = 0").body[0]
        ctx.flat_functions[("k",)] = "f"
        ctx.data_files = {"a.bin": b"1"}
        raise ConversionGap("no way", node)
    assert guard.fragment is not None
    assert ctx.imports == {"import before"}
    assert ctx.module_declarations.statements == []
    assert ctx.formula_helpers == [] and ctx.module_globals == {}
    assert ctx.flat_functions == {}
    assert [g.reason for g in ctx.gaps] == ["no way"]


def test_an_error_that_is_not_a_conversion_gap_is_not_caught() -> None:
    node = PrimitiveNode(id="p", vi_path="t.vi", name="Call", terminals=[])
    ctx = CodeGenContext(vi_name="t.vi")
    with pytest.raises(ValueError, match="a bug"):  # noqa: PT012
        with catch_gap(node, ctx):
            raise ValueError("a bug")
    assert ctx.gaps == []


def test_the_raise_is_a_plain_builtin() -> None:
    node = PrimitiveNode(id="p", vi_path="t.vi", name="Call", terminals=[])
    fragment = gap_fragment(node, CodeGenContext(vi_name="t.vi"), "no way")
    assert fragment.imports == set()
    raised = fragment.statements[-1]
    assert isinstance(raised, ast.Raise)
    assert isinstance(raised.exc, ast.Call)
    assert isinstance(raised.exc.func, ast.Name)
    assert hasattr(builtins, raised.exc.func.id)


def test_a_result_variable_named_from_a_receiver_expression_is_a_plain_name() -> None:
    """An Invoke Node called on ``items[i]`` (a loop's indexed element) names its
    result variable from a valid identifier, not from the expression."""
    from lvkit.codegen.nodes import invoke_node
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.models import WireEnd
    from tests.helpers import make_node

    graph = InMemoryVIGraph()
    for tid, nid in (("ref_t", "src_ref"), ("out_sink", "sink")):
        graph._graph.add_node(nid, node=make_node(nid, [tid]))
        graph._term_to_node[tid] = nid
    graph._graph.add_node("invoke_1", node=make_node("invoke_1", ["ref_in", "out_t"]))
    for tid in ("ref_in", "out_t"):
        graph._term_to_node[tid] = "invoke_1"
    for src, dst, src_nid, dst_nid in (
        ("ref_t", "ref_in", "src_ref", "invoke_1"),
        ("out_t", "out_sink", "invoke_1", "sink"),
    ):
        graph._graph.add_edge(
            src_nid,
            dst_nid,
            source=WireEnd(terminal_id=src, node_id=src_nid),
            dest=WireEnd(terminal_id=dst, node_id=dst_nid),
        )
    ctx = CodeGenContext(graph=graph)
    ctx.bind("ref_t", "items[i]")
    node = PrimitiveNode(
        id="invoke_1",
        vi_path="t.vi",
        name="Invoke",
        node_type="invokeNode",
        method_name="Get Value",
        terminals=[
            Terminal(id="ref_in", index=0, direction="input"),
            Terminal(id="out_t", index=1, direction="output"),
        ],
    )
    fragment = invoke_node.generate(node, ctx)
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    assert ast.unparse(module) == "itemsi_get_value_result = items[i].get_value()"


def test_a_fail_fast_log_stops_at_the_gap_it_records_and_a_rollback_does_not() -> None:
    from lvkit.codegen.gaps import GapLog
    from lvkit.graph import InMemoryVIGraph
    from lvkit.run_report import RunSettings, RunStopped

    node = PrimitiveNode(id="p", vi_path="t.vi", name="Call", terminals=[])
    graph = InMemoryVIGraph(RunSettings(fail_fast=True))
    ctx = CodeGenContext(vi_name="t.vi", gaps=GapLog(graph))
    with pytest.raises(RunStopped) as stop:
        with catch_gap(node, ctx):
            raise ConversionGap("no way", node)
    assert stop.value.problem.reason == "no way"
    assert [g.reason for g in ctx.gaps] == ["no way"]
    # Taking a snapshot of a log that already holds a gap records nothing.
    with catch_gap(node, CodeGenContext(vi_name="t.vi", gaps=ctx.gaps)):
        pass

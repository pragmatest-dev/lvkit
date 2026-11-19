"""An output variable never takes the name of one of the function's parameters:
the control is still read after the node that would have overwritten it."""

from __future__ import annotations

from lvkit.codegen.builder import function_body
from lvkit.codegen.context import CodeGenContext
from lvkit.graph.models import VIContext
from lvkit.models import FPTerminal
from tests.helpers import build_graph
from tests.native_vi import generate, node, scalar

DBL = scalar("NumFloat64")


def _increment_twice_read(control: str):
    """Increment's output is ``x1`` (``x+1``), read by two indicators so it is a
    variable; a control may be labelled the same and is read again after."""
    return generate(
        {control: DBL},
        {"a": DBL, "b": DBL, "c": DBL},
        [node("inc", 1057, [(0, "output", DBL), (1, "input", DBL)])],
        [
            (f"fp:{control}", "inc:1"),
            ("inc:0", "fp:a"),
            ("inc:0", "fp:b"),
            (f"fp:{control}", "fp:c"),
        ],
    )


def test_a_primitive_output_named_like_a_control_leaves_the_control_alone() -> None:
    generated = _increment_twice_read("x1")
    result = generated.function(5.0)  # type: ignore[operator]
    assert (result.a, result.b, result.c) == (6.0, 6.0, 5.0)
    assert "x1_2 = x1 + 1" in generated.source


def test_an_output_with_a_distinct_name_keeps_its_name() -> None:
    generated = _increment_twice_read("x")
    result = generated.function(5.0)  # type: ignore[operator]
    assert (result.a, result.b, result.c) == (6.0, 6.0, 5.0)
    assert "x1 = x + 1" in generated.source


def _body_context(controls: list[str]) -> CodeGenContext:
    """The context of a function with these controls, once its body is written."""
    inputs = [
        FPTerminal(
            id=f"fp:{name}",
            index=i,
            direction="input",
            wiring_rule=1,
            name=name,
            is_public=True,
            is_indicator=False,
            lv_type=DBL,
        )
        for i, name in enumerate(controls)
    ]
    context = VIContext(name="Native Test.vi", inputs=inputs)
    graph = build_graph(context, context.name, [])
    ctx = CodeGenContext.from_vi_context(
        context, graph=graph, vi_name=context.name, module_stem="native_test"
    )
    function_body(context, context.name, ctx)
    return ctx


def test_a_called_vi_output_and_a_structure_tunnel_never_take_a_parameter_name() -> (
    None
):
    """A called VI's output (``make_output_var``) and a structure output
    tunnel's merge variable (``_allocate_var``) are claimed from one set."""
    ctx = _body_context(["fill_result", "total"])
    assert ctx.make_output_var("fill result", "call") == "fill_result_2"
    assert ctx._allocate_var("total") == "total_2"  # noqa: SLF001
    assert ctx.make_output_var("other", "call") == "other"

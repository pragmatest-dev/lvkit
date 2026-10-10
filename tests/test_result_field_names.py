"""Executable result fields for legal LabVIEW output labels."""

from __future__ import annotations

import pytest

from lvkit.codegen.builder import build_module, build_result_class
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes.subvi import _build_output_bindings
from lvkit.graph.models import Constant, VIContext, VINode, Wire
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from tests.helpers import build_graph


@pytest.mark.parametrize("label", ["_status", "status", "class", "2nd result"])
def test_caller_reads_the_callee_result_field(label: str):
    """A caller reads a SubVI output by the same field name the callee's
    NamedTuple declares."""
    callee = VIContext(name="Callee.vi", outputs=[output(label)])
    result_class = build_result_class(callee)
    assert result_class is not None
    declared = result_class.body[0].target.id  # type: ignore[attr-defined]

    node = VINode(
        id="op:1",
        vi_path="Caller.vi",
        name="Callee.vi",
        terminals=[Terminal(id="t:out", index=0, direction="output", name=label)],
    )
    bindings = _build_output_bindings(node, "callee_result", None, CodeGenContext())
    assert bindings["t:out"] == f"callee_result.{declared}"


def output(label: str, index: int = 0) -> FPTerminal:
    return FPTerminal(
        id=f"out:{index}",
        index=index,
        direction="output",
        name=label,
        is_indicator=True,
        is_public=True,
        lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
    )


@pytest.mark.parametrize(
    "label,field",
    [
        ("_status", "output_status"),
        ("__status", "output__status"),
        ("_", "output_"),
        ("_0", "output_0"),
        (" _Status", "output__status"),
        ("-status", "output_status"),
        ("_class", "output_class"),
        ("_asdict", "output_asdict"),
        ("status", "status"),
        ("Output Value", "output_value"),
        ("2nd result", "var_2nd_result"),
        ("class", "class_"),
        ("sum", "sum_"),
        ("", "output"),
        ("!", "var"),
    ],
)
def test_result_field_and_return_agree(label: str, field: str):
    terminal = output(label)
    context = VIContext(
        name="Result Field.vi",
        outputs=[terminal],
        constants=[Constant(id="const:1", value=42.0)],
        data_flow=[
            Wire.from_terminals(
                from_terminal_id="const:1",
                to_terminal_id=terminal.id,
                from_parent_kind="constant",
                to_parent_kind="vi",
            )
        ],
    )
    graph = build_graph(context, context.name, [])
    source = build_module(context, context.name, graph=graph)
    namespace = {}
    exec(compile(source, context.name, "exec"), namespace)
    result = namespace["result_field"]()
    assert result._fields == (field,)
    assert result._asdict() == {field: 42.0}


@pytest.mark.parametrize(
    "labels",
    [
        ("_status", "output_status"),
        ("-status", "_status"),
        ("A B", "A-B"),
        ("", "output"),
        ("!", "?"),
    ],
)
def test_colliding_fields_fail_during_generation(labels: tuple[str, str]):
    context = VIContext(
        name="Collision.vi",
        outputs=[output(label, index) for index, label in enumerate(labels)],
    )
    with pytest.raises(ValueError, match="Output labels collide"):
        build_module(context, context.name)


def test_error_cluster_is_excluded_before_collision_check():
    regular = output("error out")
    regular.lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    error = output("error out", 1)
    error.lv_type = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name=name) for name in ("status", "code", "source")],
    )
    context = VIContext(name="Error Output.vi", outputs=[regular, error])
    result_class = build_result_class(context)
    assert result_class is not None
    assert len(result_class.body) == 1

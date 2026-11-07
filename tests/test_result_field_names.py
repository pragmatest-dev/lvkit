"""Executable result fields for legal LabVIEW output labels."""

from __future__ import annotations

import dataclasses

import pytest

from lvkit.codegen.builder import build_module, build_result_class
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes.subvi import _build_output_bindings
from lvkit.graph.models import Constant, VIContext, VINode, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from tests.helpers import build_graph, error_cluster_type, exec_module


@pytest.mark.parametrize("label", ["_status", "status", "class", "2nd result"])
def test_caller_reads_the_callee_result_field(label: str):
    """A caller reads a SubVI output by the same field name the callee's
    result class declares."""
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


def field_names(result: object) -> tuple[str, ...]:
    return tuple(f.name for f in dataclasses.fields(result))  # type: ignore[arg-type]


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
    assert field_names(result) == (field,)
    assert dataclasses.asdict(result) == {field: 42.0}


@pytest.mark.parametrize(
    "labels,fields",
    [
        (("_status", "output_status"), ("output_status", "output_status_2")),
        (("-status", "_status"), ("output_status", "output_status_2")),
        (("A B", "A-B"), ("a_b", "a_b_2")),
        (("", "output"), ("output", "output_2")),
        (("!", "?"), ("var", "var_2")),
    ],
)
def test_colliding_labels_get_distinct_fields(
    labels: tuple[str, str], fields: tuple[str, str]
):
    context = VIContext(
        name="Collision.vi",
        outputs=[output(label, index) for index, label in enumerate(labels)],
    )
    source = build_module(context, context.name)
    namespace = exec_module(source, context.name).__dict__
    assert field_names(namespace["collision"]()) == fields


def test_an_error_cluster_output_is_a_field_like_any_other():
    regular = output("error out")
    regular.lv_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    error = output("error out", 1)
    error.lv_type = error_cluster_type()
    context = VIContext(name="Error Output.vi", outputs=[regular, error])
    result_class = build_result_class(context)
    assert result_class is not None
    fields = [stmt.target.id for stmt in result_class.body]  # type: ignore[attr-defined]
    assert fields == ["error_out", "error_out_2"]

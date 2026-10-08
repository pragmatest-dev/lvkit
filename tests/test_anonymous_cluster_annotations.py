"""Generated anonymous cluster annotations match their tuple representation."""

from __future__ import annotations

import ast
import inspect
from typing import get_type_hints

import pytest

from lvkit.codegen.builder import build_args, build_module, build_result_class
from lvkit.graph.models import VIContext, Wire
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind
from tests.helpers import build_graph

DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
PAIR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[ClusterField("first", DBL), ClusterField("second", DBL)],
)


def terminal(lv_type, *, output=False):
    return FPTerminal(
        id="fp:result" if output else "fp:value",
        index=0,
        direction="output" if output else "input",
        name="result" if output else "value",
        is_public=True,
        is_indicator=output,
        lv_type=lv_type,
    )


@pytest.mark.parametrize(
    "lv_type,value",
    [
        (LVType(kind=LVTypeKind.CLUSTER), (1.0,)),
        (LVType(kind=LVTypeKind.CLUSTER, fields=[]), ()),
        (LVType(kind=LVTypeKind.CLUSTER, fields=[ClusterField("first", DBL)]), (1.5,)),
        (PAIR, (1.0, 42.0)),
        (
            LVType(
                kind=LVTypeKind.CLUSTER,
                fields=[
                    ClusterField("number", DBL),
                    ClusterField("flag", BOOL),
                    ClusterField("text", STRING),
                ],
            ),
            (1.0, True, "sample"),
        ),
        (
            LVType(
                kind=LVTypeKind.CLUSTER,
                fields=[ClusterField("number", DBL), ClusterField("pair", PAIR)],
            ),
            (1.0, (2.0, 3.0)),
        ),
    ],
)
def test_generated_cluster_annotations_match_explicit_tuple_values(lv_type, value):
    argument, result = terminal(lv_type), terminal(lv_type, output=True)
    context = VIContext(
        name="Cluster Annotation.vi",
        inputs=[argument],
        outputs=[result],
        data_flow=[
            Wire.from_terminals(from_terminal_id=argument.id, to_terminal_id=result.id)
        ],
    )
    graph = build_graph(context, context.name, [])
    source = build_module(context, context.name, graph=graph)
    namespace = {}
    exec(compile(source, context.name, "exec"), namespace)
    function = namespace["cluster_annotation"]
    actual = function(value).result
    assert actual == value and isinstance(actual, tuple)
    annotations = get_type_hints(function, globalns=namespace)
    assert annotations["value"] == tuple | None
    assert get_type_hints(annotations["return"], globalns=namespace)["result"] is tuple
    assert inspect.signature(function).parameters["value"].default is None


@pytest.mark.parametrize(
    "lv_type,input_annotation,result_annotation",
    [
        (LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"), "int", "int"),
        (DBL, "float", "float"),
        (BOOL, "bool", "bool"),
        (STRING, "str", "str"),
        (LVType(kind=LVTypeKind.ENUM, underlying_type="NumUInt16"), "int", "int"),
        (
            LVType(kind=LVTypeKind.CLUSTER, typedef_name="Settings.ctl"),
            "Settings",
            "Settings",
        ),
        (
            LVType(kind=LVTypeKind.CLUSTER, classname="Device.lvclass"),
            "dict[str, Any]",
            "dict[str, Any]",
        ),
        (LVType(kind=LVTypeKind.CLASS, classname="Device.lvclass"), "Any", "Any"),
        (
            LVType(kind=LVTypeKind.ARRAY, element_type=DBL, dimensions=2),
            "list[list[float]] | None",
            "list[list[float]]",
        ),
        (
            LVType(kind=LVTypeKind.ARRAY, element_type=PAIR, dimensions=1),
            "list[tuple] | None",
            "list[tuple]",
        ),
        (None, "Any", "Any"),
    ],
)
def test_other_annotation_contracts_are_preserved(
    lv_type, input_annotation, result_annotation
):
    arguments = build_args([terminal(lv_type)])
    assert ast.unparse(arguments.args[0].annotation) == input_annotation
    context = VIContext(name="Control.vi", outputs=[terminal(lv_type, output=True)])
    result_class = build_result_class(context)
    assert ast.unparse(result_class.body[0].annotation) == result_annotation


def test_shared_type_model_renders_anonymous_cluster_as_tuple():
    assert PAIR.to_python() == "tuple"

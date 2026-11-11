"""Cluster To Array (primResID 1169).

NI, "Cluster To Array Function" (labview-api-ref, ``functions/cluster-to-array.html``):
"Converts a cluster of elements of the same data type to a 1D array of elements of
the same data type. ... The order of the elements in array is the same as the
cluster order of the elements." An unwired cluster is the default of its type.

A cluster is a tuple, or a ``SimpleNamespace`` when it is a typedef (the forms
cluster constants take, ``type_default.type_default``).
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import (
    build_assign,
    field_attribute_names,
    parse_expr,
)
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...type_default import type_default
from . import register_op_fragment
from ._cluster_node import cluster_type
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable


@register_op_fragment("CLUSTER_TO_ARRAY")
def cluster_to_array(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    in_type = cluster_type(node, "input")
    cluster = input_expression(ctx, node, 1)
    if cluster is None:
        cluster = ast.unparse(type_default(ctx, in_type))
    statements: list[ast.stmt] = []
    if in_type.typedef_name:
        names = field_attribute_names(f.name for f in in_type.fields or [])
        name = string_variable(ctx, node, cluster, statements, base="cluster")
        elements = "[" + ", ".join(f"{name}.{field}" for field in names) + "]"
    else:
        elements = f"list({cluster})"
    out = terminal(node, "output", 0)
    assert out is not None
    var = ctx.make_output_var("array", node.id, terminal_id=out.id)
    statements.append(build_assign(var, parse_expr(elements)))
    return CodeFragment(statements=statements, bindings={out.id: var})

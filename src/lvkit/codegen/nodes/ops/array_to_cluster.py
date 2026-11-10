"""Array To Cluster (primResID 1168).

NI, "Array To Cluster Function" (labview-api-ref, ``functions/array-to-cluster.html``):
"Converts a 1D array to a cluster of elements of the same type as the array
elements. ... set the number of elements in the cluster. The default is nine. ...
Each element in cluster is the same as the corresponding element in array. The
cluster order matches the order of the elements in the array." The node stores
its size as the number of fields of its output cluster. NI's page does not say
what an array longer or shorter than the cluster does; the book "LabVIEW
Graphical Programming" says a longer array is truncated to the cluster size and
a shorter one fills the extra cluster values with the default of the element
type, which is what is written here.

A cluster is a tuple, or a ``SimpleNamespace`` when it is a typedef (the forms
cluster constants take, ``ast_utils.type_default_expr``).
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import (
    build_assign,
    field_attribute_names,
    parse_expr,
    type_default_expr,
)
from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._cluster_node import cluster_type
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable


@register_op_fragment("ARRAY_TO_CLUSTER")
def array_to_cluster(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    out_type = cluster_type(node, "output")
    fields = out_type.fields or []
    size = len(fields)
    statements: list[ast.stmt] = []
    array = string_variable(
        ctx, node, input_expression(ctx, node, 1) or "[]", statements, base="array"
    )
    default = ast.unparse(type_default_expr(fields[0].type))
    cluster = (
        f"(*{array}[:{size}], *({default} for _ in range({size} - len({array}))))"
    )
    if out_type.typedef_name:
        names = field_attribute_names(f.name for f in fields)
        cluster = f"SimpleNamespace(**dict(zip({tuple(names)!r}, {cluster})))"
    out = terminal(node, "output", 0)
    assert out is not None
    var = ctx.make_output_var("cluster", node.id, terminal_id=out.id)
    statements.append(build_assign(var, parse_expr(cluster)))
    return CodeFragment(statements=statements, bindings={out.id: var})

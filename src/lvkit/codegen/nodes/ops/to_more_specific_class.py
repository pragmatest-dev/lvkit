"""To More Specific Class (primResID 8016).

NI, "To More Specific Class Function" (labview-api-ref,
``functions/to-more-specific-class.html``): "If the target class is incompatible
with the reference, the function returns an error and specific class reference
is Not A Refnum." For a LabVIEW class wire the target is the class the
``target class`` input's type names at generation time; the cast is an
``isinstance`` check against its generated Python class, and a failure is error
1448 (NI, LabVIEW Object-Oriented Programming Error Codes: "Bad type cast. LabVIEW
cannot treat the run-time value of this LabVIEW class as an instance of the
given LabVIEW class.") through the catalog entry's ``errors`` (``native_failure``),
the output then its type's default, ``None``. A VI Server reference cast passes
the reference through.

Terminals (from the primitives entry):
  in 2 = target class, 3 = reference; out 0 = specific class reference
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable

# NI, LabVIEW Object-Oriented Programming Error Codes, 1448.
BAD_TYPE_CAST = (
    "Bad type cast. LabVIEW cannot treat the run-time value of this LabVIEW class "
    "as an instance of the given LabVIEW class."
)


@register_op_fragment("TO_MORE_SPECIFIC_CLASS")
def to_more_specific_class(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    output = terminal(node, "output", 0)
    target = terminal(node, "input", 2)
    statements: list[ast.stmt] = []
    reference = string_variable(
        ctx, node, input_expression(ctx, node, 3) or "None", statements
    )
    target_type = target.lv_type if target is not None else None
    if target_type is not None and target_type.ref_type == "UDClassInst":
        cls = ctx.class_expression(target_type, "To More Specific Class's target")
        statements.extend(
            ast.parse(
                f"if not isinstance({reference}, {cls}):\n"
                f"    raise TypeError({BAD_TYPE_CAST!r})"
            ).body
        )
    bindings: dict[str, str] = {}
    if output is not None:
        # A variable of its own, so a failed cast gives it its default.
        specific = ctx.make_output_var(
            "specific_class_reference", node.id, terminal_id=output.id
        )
        statements.append(build_assign(specific, parse_expr(reference)))
        bindings[output.id] = specific
    return CodeFragment(statements=statements, bindings=bindings)

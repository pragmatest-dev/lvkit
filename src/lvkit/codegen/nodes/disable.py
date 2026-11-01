"""Code generator for Diagram, Conditional and Type Specialization Disable
structures."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import DisableStructureNode
from lvkit.models import CaseFrame

from ..ast_utils import build_assign, default_value_expr
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment


def generate(node: DisableStructureNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate the subdiagram LabVIEW compiles: the one the structure is saved
    with as enabled (``active_frame``). The other subdiagrams are not part of the
    compiled VI, so they produce no code.

    A tunnel carries a value into or out of the structure through the enabled
    subdiagram: an input tunnel's inner terminal takes the outer value, and an
    output tunnel's outer terminal takes the value the enabled subdiagram sends.
    """
    frames = ctx.frame_children(node)
    if node.active_frame is None or not 0 <= node.active_frame < len(frames):
        raise ConversionGap(
            "Disable structure has no readable enabled subdiagram "
            f"(active_frame={node.active_frame})",
            node,
        )
    frame, operations = frames[node.active_frame]
    if not isinstance(frame, CaseFrame):
        raise CodeGenError("Disable structure frame is not a case frame", node)

    outer_by_id = {t.id: t for t in node.terminals}
    bindings: dict[str, str] = {}
    for tunnel in ctx.tunnels(node):
        outer = outer_by_id[tunnel.outer_terminal_uid]
        if outer.direction == "input":
            outer_var = ctx.resolve(tunnel.outer_terminal_uid)
            if outer_var:
                ctx.bind(tunnel.inner_terminal_uid, outer_var)

    statements: list[ast.stmt] = ctx.generate_body(operations)
    for stmt in statements:
        ast.fix_missing_locations(stmt)

    for tunnel in ctx.tunnels(node):
        outer = outer_by_id[tunnel.outer_terminal_uid]
        inner = outer_by_id[tunnel.inner_terminal_uid]
        if (
            outer.direction == "output"
            and getattr(inner, "frame", None) == frame.selector_value
        ):
            inner_var = ctx.resolve(tunnel.inner_terminal_uid)
            if inner_var:
                bindings[tunnel.outer_terminal_uid] = inner_var
            else:
                # The enabled subdiagram leaves the output tunnel unwired: it
                # sends the type's default, as an unwired case output tunnel does.
                default = ctx.make_output_var(
                    "tunnel_default", tunnel.outer_terminal_uid
                )
                statements.append(
                    build_assign(default, default_value_expr(outer.lv_type))
                )
                bindings[tunnel.outer_terminal_uid] = default

    return CodeFragment(statements=statements, bindings=bindings)

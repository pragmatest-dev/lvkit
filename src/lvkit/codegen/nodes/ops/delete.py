"""Delete (primResIDs 8056 and 1417).

NI, "Delete Function" (labview-api-ref, ``functions/delete.html``): deletes the
file or directory ``path`` names; a directory that is not empty only when
``entire hierarchy`` is TRUE. "If path is empty (default), the function displays
a dialog box", so an unwired ``path`` is a gap; "If you specify an empty or
relative path, this function returns an error", error 1430 (NI, General LabVIEW
Error Codes: "The path is empty or relative. You must use an absolute path.").
``confirm`` TRUE shows a dialog: a gap unless it is unwired or a constant FALSE.
The failures of the operation are the node's error out through the catalog
entry's ``errors`` (``native_failure``): 1430, then 7, 8 and 6 as Copy's are.
``cancelled`` is FALSE (no dialog is shown); ``deleted path`` is ``path``.

Terminals (from the primitives entries; 9 and 10 from the callers' wiring, see
``labview-binary-format.md``, "Delete"):
  8056: in 7 = prompt, 9 = confirm, 10 = entire hierarchy, 11 = path;
        out 1 = cancelled, 3 = deleted path
  1417: in 3 = path; out 1 = deleted path (no hierarchy or confirm input)
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, literal, string_variable

# NI, General LabVIEW Error Codes, 1430.
EMPTY_OR_RELATIVE = "The path is empty or relative. You must use an absolute path."

# Input and output terminal indices of each Delete primitive.
_PANES = {
    8056: {"path": 11, "confirm": 9, "hierarchy": 10, "deleted": 3},
    1417: {"path": 3, "deleted": 1},
}


@register_op_fragment("DELETE")
def delete(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    pane = _PANES[node.prim_id or 0]
    path_text = input_expression(ctx, node, pane["path"])
    if path_text is None:
        raise ConversionGap("Delete with no path shows a file dialog", node)
    confirm = (
        input_expression(ctx, node, pane["confirm"]) if "confirm" in pane else None
    )
    if confirm is not None and literal(confirm) is not False:
        raise ConversionGap("Delete with confirm TRUE shows a dialog", node)
    hierarchy = (
        input_expression(ctx, node, pane["hierarchy"]) if "hierarchy" in pane else None
    )
    statements: list[ast.stmt] = []
    path = string_variable(ctx, node, path_text, statements)
    removal = "if os.path.isdir(PATH):\n    os.rmdir(PATH)\nelse:\n    os.remove(PATH)"
    if hierarchy is not None and literal(hierarchy) is not False:
        whole = f"shutil.rmtree({path})"
        ctx.imports.add("import shutil")
        removal = (
            whole
            if literal(hierarchy) is True
            else f"if {hierarchy}:\n    {whole}\nel" + removal
        )
    source = (
        f"if {path} is None or not os.path.isabs({path}):\n"
        f"    raise ValueError({EMPTY_OR_RELATIVE!r})\n"
        + removal.replace("PATH", path)
    )
    statements.extend(ast.parse(source).body)
    bindings: dict[str, str] = {}
    deleted = terminal(node, "output", pane["deleted"])
    if deleted is not None:
        # A variable of its own, so a failed delete gives it its default.
        var = ctx.make_output_var("deleted_path", node.id, terminal_id=deleted.id)
        statements.append(build_assign(var, parse_expr(path)))
        bindings[deleted.id] = var
    cancelled = terminal(node, "output", 1) if node.prim_id == 8056 else None
    if cancelled is not None:
        var = ctx.make_output_var("cancelled", node.id, terminal_id=cancelled.id)
        statements.append(build_assign(var, parse_expr("False")))
        bindings[cancelled.id] = var
    return CodeFragment(statements=statements, bindings=bindings)

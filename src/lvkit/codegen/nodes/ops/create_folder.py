"""Create Folder (primResID 8055).

NI, "Create Folder Function" (labview-api-ref, ``functions/create-folder.html``):
"Creates the appropriate folder specified by path. This function creates all
folders that do not already exist in the specified path. If a file or folder
already exists at the specified location, the function returns an error instead of
overwriting the existing file or folder."

* An unwired ``path`` shows a folder dialog: a gap.
* "If you specify an empty or relative path, this function returns an error": error
  1430 (NI, General LabVIEW Error Codes), as for Delete.
* ``os.makedirs`` creates the missing folders and raises ``FileExistsError`` for
  an existing file or folder, which is error 10, "Duplicate path" (NI, General
  LabVIEW Error Codes), as for Copy and Move.
* "``created path`` specifies the new location of the folder. If the operation is
  unsuccessful, this function sets created path to <Not A Path>": the path, and
  on a failure the Path type's default, the empty path, as for Delete.
* "``cancelled`` is TRUE if you cancel the file dialog box. Otherwise, cancelled
  is FALSE, even if this function returns an error": FALSE, there being no dialog.

The failures are the node's error out through the catalog entry's ``errors``
(``native_failure``).

Terminals (from the primitives entry): in 7 = prompt, 11 = path; out 1 = cancelled,
3 = created path.
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
from ._path_checks import absolute_path_check
from ._regex_node import input_expression, string_variable


@register_op_fragment("CREATE_FOLDER")
def create_folder(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    path_text = input_expression(ctx, node, 11)
    if path_text is None:
        raise ConversionGap("Create Folder with no path shows a folder dialog", node)
    statements: list[ast.stmt] = []
    path = string_variable(ctx, node, path_text, statements, "path")
    statements.extend(
        ast.parse(absolute_path_check(path) + f"os.makedirs({path})").body
    )
    bindings: dict[str, str] = {}
    created = terminal(node, "output", 3)
    if created is not None:
        # A variable of its own, so a failed create gives it its default.
        var = ctx.make_output_var("created_path", node.id, terminal_id=created.id)
        statements.append(build_assign(var, parse_expr(path)))
        bindings[created.id] = var
    cancelled = terminal(node, "output", 1)
    if cancelled is not None:
        var = ctx.make_output_var("cancelled", node.id, terminal_id=cancelled.id)
        statements.append(build_assign(var, parse_expr("False")))
        bindings[cancelled.id] = var
    return CodeFragment(statements=statements, bindings=bindings)

"""Set File Size (primResID 8074) and Set Permissions (primResID 8076).

NI, "Set File Size Function" (labview-api-ref, ``functions/set-file-size.html``):
"Sets the file size of file by setting the end-of-file marker to size bytes from
the beginning of the file." ``file`` is a refnum (a Python file object) or a path;
"The default is to close the file if it is referenced by a file path": a path has
no file object for ``refnum out`` to give, so it is a gap. ``file.truncate(size)``
sets the end of the file, past the old end too.

NI, "Set Permissions Function" (``functions/set-permissions.html``) and "Setting
Permissions": ``permissions`` is the 9 Linux permission bits (user, group, others;
read, write, execute): ``os.chmod``. On Windows "You can use permissions to change
only write permissions for users (bit 7)": what ``os.chmod`` does there too.
"If new owner is an empty string, the VI does not set the owner", likewise the
group: ``shutil.chown``. "(Windows) This function ignores new owner and new group";
"(macOS) If path refers to a file, this function ignores new owner and new
group". "If you do not specify new owner, new group, or permissions, this function
returns the current settings unchanged": an unwired ``permissions`` leaves them.
``path out`` is ``path``.

Terminals (from the primitives entries): Set File Size: in 6 = size, 7 = file; out
3 = refnum out. Set Permissions: in 7 = permissions, 9 = new group, 10 = new
owner, 11 = path; out 3 = path out.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op, register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable


@register_op("SET_FILE_SIZE")
def set_file_size(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    file = terminal(node, "input", 7)
    if file is None or file.lv_type is None or file.lv_type.underlying_type != "Refnum":
        raise ConversionGap(
            "Set File Size with a file path: it closes the file, so there is no "
            "refnum out; only a file refnum has code generation",
            node,
        )
    return {"refnum_out": "(in_7.truncate(in_6), in_7)[1]"}


@register_op_fragment("SET_PERMISSIONS")
def set_permissions(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    statements: list[ast.stmt] = []
    path_text = input_expression(ctx, node, 11)
    if path_text is None:
        raise ConversionGap("Set Permissions has no path wired", node)
    path = string_variable(ctx, node, path_text, statements, "path")
    permissions = input_expression(ctx, node, 7)
    owner = input_expression(ctx, node, 10)
    group = input_expression(ctx, node, 9)
    lines: list[str] = []
    if permissions is not None:
        ctx.add_import("import os")
        lines.append(f"os.chmod({path}, {permissions})")
    if owner is not None or group is not None:
        ctx.add_import("import os")
        ctx.add_import("import shutil")
        ctx.add_import("import sys")
        names = [part or "''" for part in (owner, group)]
        lines.append(
            "if os.name == 'posix' and not (sys.platform == 'darwin' and "
            f"os.path.isfile({path})) and ({names[0]} or {names[1]}):\n"
            f"    shutil.chown({path}, {names[0]} or None, {names[1]} or None)"
        )
    for line in lines:
        statements.extend(ast.parse(line).body)
    out = terminal(node, "output", 3)
    bindings = {out.id: path} if out is not None and ctx.is_wired(out.id) else {}
    return CodeFragment(statements=statements, bindings=bindings)

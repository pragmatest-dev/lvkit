"""Write to Text File (primResID 8080).

NI, "Write to Text File Function" (labview-api-ref,
``functions/write-to-text-file.html``): "Writes a string or an array of strings as
lines to a file."

* ``file``: "file can be a refnum or absolute file path. If you wire a path to the
  file input, the function opens or creates the file before writing to it and
  replaces any previous file contents. If you wire a file refnum to the file
  input, writing begins at the current file position." Unwired, the function
  "display[s] a file dialog box": a gap. "If you specify an empty or relative path,
  this function returns an error": error 1430 (NI, General LabVIEW Error Codes),
  and a path with a null character error 1, as for Delete.
* ``refnum out``: "The default is to close the file if it is referenced by a file
  path ... If file is a refnum or if you wire refnum out to another function,
  LabVIEW assumes that the file is still in use until you close it": a path whose
  ``refnum out`` is wired leaves the file open and gives it; a refnum is given
  back.
* ``text``: "a string or an array of strings". "This function appends
  platform-dependent, end-of-line (EOL) characters to the elements of an array even
  if you right-click the function and remove the checkmark next to the Convert EOL
  shortcut menu item": an array element is followed by ``os.linesep``; a string
  has its line feeds written as ``os.linesep`` when Convert EOL is on and as they
  are when it is off. The option is the node's own ``objFlags`` bit 0x2000000
  (``docs/_internal/design/labview-binary-format.md``, "Write to Text File").
* ``cancelled`` is FALSE (no dialog is shown).

A file is a Python file object; one opened for bytes is written the text's
encoding, one opened for text the text itself. The failures are the node's error out
through the catalog entry's ``errors`` (``native_failure``).

Terminals (from the primitives entry): in 7 = prompt, 10 = text, 11 = file; out
1 = cancelled, 3 = refnum out.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._path_checks import absolute_path_check
from ._regex_node import input_expression, is_wired_output, string_variable

# The node's own ``objFlags`` bit that is set when Convert EOL is on.
CONVERT_EOL = 0x2000000


def _text_data(node: PrimitiveNode, text: str) -> str:
    """The characters written for ``text``: an array's elements each followed by
    the platform's EOL, a string with its line feeds converted when Convert EOL
    is on."""
    term = terminal(node, "input", 10)
    lv_type = term.lv_type if term is not None else None
    if lv_type is not None and lv_type.kind == LVTypeKind.ARRAY:
        return f"''.join(line + os.linesep for line in {text})"
    if node.obj_flags & CONVERT_EOL:
        return f"{text}.replace('\\n', os.linesep)"
    return text


@register_op_fragment("WRITE_TO_TEXT_FILE")
def write_to_text_file(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    file_text = input_expression(ctx, node, 11)
    if file_text is None:
        raise ConversionGap(
            "Write to Text File with no file shows a file dialog", node
        )
    file_term = terminal(node, "input", 11)
    file_type = file_term.lv_type if file_term is not None else None
    is_path = file_type is not None and file_type.underlying_type == "Path"
    is_refnum = file_type is not None and file_type.underlying_type == "Refnum"
    if not (is_path or is_refnum):
        raise ConversionGap(
            "Write to Text File writes to a path or a file refnum only", node
        )
    text = input_expression(ctx, node, 10) or "''"
    statements: list[ast.stmt] = []
    text = string_variable(ctx, node, text, statements, "text")
    data = _text_data(node, text)
    refnum_out = terminal(node, "output", 3)
    bindings: dict[str, str] = {}
    if is_path:
        path = string_variable(ctx, node, file_text, statements, "path")
        statements.extend(ast.parse(absolute_path_check(path)).body)
        if is_wired_output(ctx, node, 3):
            assert refnum_out is not None
            file = ctx.make_output_var("refnum_out", node.id, terminal_id=refnum_out.id)
            source = f"{file} = open({path}, 'w', newline='')\n{file}.write({data})"
            bindings[refnum_out.id] = file
        else:
            handle = ctx.make_output_var("file_handle", node.id)
            source = (
                f"with open({path}, 'w', newline='') as {handle}:\n"
                f"    {handle}.write({data})"
            )
        statements.extend(ast.parse(source).body)
    else:
        file = string_variable(ctx, node, file_text, statements, "file")
        ctx.add_import("import io")
        written = ctx.make_output_var("text_data", node.id)
        statements.append(build_assign(written, parse_expr(data)))
        statements.extend(
            ast.parse(
                f"{file}.write({written} if isinstance({file}, io.TextIOBase) "
                f"else {written}.encode())"
            ).body
        )
        if refnum_out is not None:
            bindings[refnum_out.id] = file
    cancelled = terminal(node, "output", 1)
    if cancelled is not None:
        var = ctx.make_output_var("cancelled", node.id, terminal_id=cancelled.id)
        statements.append(build_assign(var, parse_expr("False")))
        bindings[cancelled.id] = var
    return CodeFragment(statements=statements, bindings=bindings)

"""Write to Binary File (primResID 8078).

NI, "Write to Binary File Function" (labview-api-ref,
``functions/write-to-binary-file.html``): ``data`` "can be any data type" and is
written flattened, the way Flatten To String writes it (``codegen/flat.py``), in
the ``byte order`` (0, 1, 2; default big-endian). ``prepend array or string size?``
(default TRUE) "controls only the top-level data size information. Arrays and
strings in hierarchical data types such as clusters always include size
information."

* ``file``: "If you wire a path ... the function opens or creates the file before
  writing to it and replaces any previous file contents. If you wire a file refnum
  ..., writing begins at the current file position." Unwired, the function
  displays a file dialog box: a gap. "If you specify an empty or relative path,
  this function returns an error" (the failures of the catalog entry's
  ``errors``).
* A path is closed after the write unless ``refnum out`` is wired, which leaves
  the file open and gives it; a refnum is given back.
* ``cancelled`` is FALSE (no dialog is shown).

Terminals (from the primitives entry): in 5 = prepend array or string size, 9 =
byte order, 10 = data, 11 = file; out 1 = cancelled, 3 = refnum out.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import (
    BYTE_ORDER_PREFIX,
    Functions,
    UnsupportedLayout,
    layout_of,
    render_parts,
)
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import (
    add_flat_imports,
    constant_input,
    terminal,
    unsupported_layout_error,
)
from ._path_checks import absolute_path_check
from ._regex_node import input_expression, is_wired_output, string_variable


@register_op_fragment("WRITE_TO_BINARY_FILE")
def write_to_binary_file(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    file_text = input_expression(ctx, node, 11)
    if file_text is None:
        raise ConversionGap(
            "Write to Binary File with no file shows a file dialog", node
        )
    file_term = terminal(node, "input", 11)
    file_type = file_term.lv_type if file_term is not None else None
    is_path = file_type is not None and file_type.underlying_type == "Path"
    is_refnum = file_type is not None and file_type.underlying_type == "Refnum"
    if not (is_path or is_refnum):
        raise ConversionGap(
            "Write to Binary File writes to a path or a file refnum only", node
        )
    data_term = terminal(node, "input", 10)
    value = (input_expression(ctx, node, 10)) or "None"
    prefix = bool(constant_input(ctx, node, 5, True))
    order = BYTE_ORDER_PREFIX[int(constant_input(ctx, node, 9, 0))]
    try:
        layout = layout_of(data_term.lv_type if data_term else None)
    except UnsupportedLayout as error:
        raise unsupported_layout_error(node, error) from error
    statements: list[ast.stmt] = []
    flattened = render_parts(
        layout.flatten(value, prefix, 0, Functions(ctx, order)), order
    )
    written = ctx.make_output_var("bytes_written", node.id)
    statements.append(build_assign(written, parse_expr(flattened)))
    refnum_out = terminal(node, "output", 3)
    bindings: dict[str, str] = {}
    if is_path:
        path = string_variable(ctx, node, file_text, statements, "path")
        statements.extend(ast.parse(absolute_path_check(path)).body)
        if is_wired_output(ctx, node, 3):
            assert refnum_out is not None
            file = ctx.make_output_var("refnum_out", node.id, terminal_id=refnum_out.id)
            source = f"{file} = open({path}, 'wb')\n{file}.write({written})"
            bindings[refnum_out.id] = file
        else:
            handle = ctx.make_output_var("file_handle", node.id)
            source = (
                f"with open({path}, 'wb') as {handle}:\n    {handle}.write({written})"
            )
        statements.extend(ast.parse(source).body)
    else:
        file = string_variable(ctx, node, file_text, statements, "file")
        statements.extend(ast.parse(f"{file}.write({written})").body)
        if refnum_out is not None:
            bindings[refnum_out.id] = file
    cancelled = terminal(node, "output", 1)
    if cancelled is not None and is_wired_output(ctx, node, 1):
        var = ctx.make_output_var("cancelled", node.id, terminal_id=cancelled.id)
        statements.append(build_assign(var, parse_expr("False")))
        bindings[cancelled.id] = var
    add_flat_imports(ctx, statements)
    return CodeFragment(statements=statements, bindings=bindings)

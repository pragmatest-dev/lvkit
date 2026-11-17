"""Open/Create/Replace File (primResID 8050).

NI, "Open/Create/Replace File Function" (labview-api-ref,
``functions/open-create-replace-file.html``): "Opens an existing file, creates a
new file, or replaces an existing file".

* ``operation`` (default 0): "0 open (default)--Opens an existing file. Error 7
  occurs if the file cannot be found. 1 replace--Replaces an existing file by
  opening the file and setting its end of file to 0. 2 create--Creates a new
  file. Error 10 occurs if the file already exists. 3 open or create--Opens an
  existing file or creates a new file if one does not exist. 4 replace or
  create--Creates a new file or replaces a file if it exists. 5 replace or create
  with confirmation--... if you give permission."
* ``access`` (default 0): "0 read/write, 1 read-only, 2 write-only".
* ``file path``: "If you do not wire file path, the function displays a dialog
  box": a gap (as for Delete and Write to Text File: a dialog has no Python
  meaning). "If you specify an empty or relative path, this function returns an
  error": 1430, and a path with a null character 1 (``absolute_path_check``).
* ``disable buffering`` (default FALSE): "(macOS and Linux) LabVIEW ignores this
  input", so TRUE is an unbuffered Python file (``buffering=0``).
* ``cancelled`` is FALSE (no dialog is shown). Operation 5 asks for permission to
  replace in a dialog: a gap when the operation is that constant, and refused at
  run time naming the value when a wired value is 5.

The Python file is opened for the access (``'r+b'``, ``'rb'``, ``'wb'``: the
wrapper refuses the other direction, which the Read and Write nodes report as
error 8, "File permission error", as NI's forum reports for a write to a file
opened read-only) with the file's creation flags given to ``os.open`` through
``opener``, so the operation is exactly NI's: open and replace of a missing file
fail with ``FileNotFoundError`` (7), create of an existing one with
``FileExistsError`` (10). Replace with read-only access (truncating a file
opened for reading) is not something NI documents, so it is refused: a gap for
constants, a ``ValueError`` naming both values at run time.

Terminals (from the primitives entry): in 6 = disable buffering, 7 = prompt,
9 = access, 10 = operation, 11 = file path; out 1 = cancelled, 3 = refnum out.
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
from ._regex_node import input_expression, is_wired_output, literal, string_variable

# Per operation 0..4: the creation flags given to os.open; per access 0..2: the
# Python mode and the access flag.
_OPERATION_FLAGS = (
    "0",
    "os.O_TRUNC",
    "os.O_CREAT | os.O_EXCL",
    "os.O_CREAT",
    "os.O_CREAT | os.O_TRUNC",
)
_MODES = ("'r+b'", "'rb'", "'wb'")
_ACCESS_FLAGS = ("os.O_RDWR", "os.O_RDONLY", "os.O_WRONLY")
_REPLACING = (1, 4)
_READ_ONLY = 1
_WITH_CONFIRMATION = 5
_BINARY = "getattr(os, 'O_BINARY', 0)"


def _constant(text: str | None, default: int, what: str, node: PrimitiveNode) -> int:
    """The value of an unwired (``default``) or constant input; -1 for a wired
    run-time value."""
    if text is None:
        return default
    value = literal(text)
    if value is None:
        return -1
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConversionGap(f"Open/Create/Replace File {what} is {value!r}", node)
    return value


def _name_or_literal(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    text: str,
    statements: list[ast.stmt],
    base: str,
) -> str:
    """``text`` as it is when it is a literal, else as a name."""
    if literal(text) is not None:
        return text
    return string_variable(ctx, node, text, statements, base)


@register_op_fragment("OPEN_CREATE_REPLACE_FILE")
def open_create_replace_file(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    path_text = input_expression(ctx, node, 11)
    if path_text is None:
        raise ConversionGap(
            "Open/Create/Replace File with no file path shows a file dialog", node
        )
    operation_text = input_expression(ctx, node, 10)
    access_text = input_expression(ctx, node, 9)
    buffering_text = input_expression(ctx, node, 6)
    operation = _constant(operation_text, 0, "operation", node)
    access = _constant(access_text, 0, "access", node)
    if operation == _WITH_CONFIRMATION:
        raise ConversionGap(
            "Open/Create/Replace File replace or create with confirmation "
            "shows a dialog",
            node,
        )
    if operation != -1 and not 0 <= operation < len(_OPERATION_FLAGS):
        raise ConversionGap(f"Open/Create/Replace File operation {operation}", node)
    if access != -1 and not 0 <= access < len(_MODES):
        raise ConversionGap(f"Open/Create/Replace File access {access}", node)
    if access == _READ_ONLY and operation in _REPLACING:
        raise ConversionGap(
            f"Open/Create/Replace File operation {operation} with read-only "
            "access: NI does not document replacing a file opened for reading",
            node,
        )
    statements: list[ast.stmt] = []
    path = string_variable(ctx, node, path_text, statements, "path")
    statements.extend(ast.parse(absolute_path_check(path)).body)
    flags = ctx.make_output_var("open_flags", node.id)
    if operation != -1 and access != -1:
        mode = _MODES[access]
        flag_text = " | ".join(
            part
            for part in (_OPERATION_FLAGS[operation], _ACCESS_FLAGS[access])
            if part != "0"
        )
    else:
        operation_var = _name_or_literal(
            ctx, node, operation_text or "0", statements, "operation"
        )
        access_var = _name_or_literal(
            ctx, node, access_text or "0", statements, "access"
        )
        statements.extend(
            ast.parse(
                f"if {operation_var} not in range({len(_OPERATION_FLAGS)}) or "
                f"{access_var} not in range({len(_MODES)}) or "
                f"({access_var} == {_READ_ONLY} and "
                f"{operation_var} in {_REPLACING}):\n"
                "    raise ValueError(f'Open/Create/Replace File: operation "
                f"{{{operation_var}!r}} with access {{{access_var}!r}} is not "
                "supported')"
            ).body
        )
        mode = f"({', '.join(_MODES)})[{access_var}]"
        flag_text = (
            f"({', '.join(_OPERATION_FLAGS)})[{operation_var}] | "
            f"({', '.join(_ACCESS_FLAGS)})[{access_var}]"
        )
    statements.append(build_assign(flags, parse_expr(f"{flag_text} | {_BINARY}")))
    buffering = "-1"
    if buffering_text is not None and literal(buffering_text) is not False:
        buffering = (
            "0"
            if literal(buffering_text) is True
            else f"(0 if {buffering_text} else -1)"
        )
    refnum_out = terminal(node, "output", 3)
    bindings: dict[str, str] = {}
    open_call = (
        f"open({path}, {mode}, buffering={buffering}, "
        f"opener=lambda name, _flags: os.open(name, {flags}))"
    )
    if refnum_out is not None and is_wired_output(ctx, node, 3):
        file = ctx.make_output_var("refnum_out", node.id, terminal_id=refnum_out.id)
        statements.append(build_assign(file, parse_expr(open_call)))
        bindings[refnum_out.id] = file
    else:
        statements.append(ast.Expr(value=parse_expr(open_call)))
    cancelled = terminal(node, "output", 1)
    if cancelled is not None:
        var = ctx.make_output_var("cancelled", node.id, terminal_id=cancelled.id)
        statements.append(build_assign(var, parse_expr("False")))
        bindings[cancelled.id] = var
    return CodeFragment(statements=statements, bindings=bindings)

"""Read from Binary File (primResID 8051).

NI, "Read from Binary File Function" (labview-api-ref,
``functions/read-from-binary-file.html``): "The function interprets the data
starting at the current file position to be count instances of data type. If the
type is an array, string, or cluster containing an array or string, the function
assumes that each instance of that data type contains size information."

* ``data type`` unwired: ``count`` is in bytes and ``data`` is those bytes as a
  string, with no size ahead of it. NI's page: "count is the number of data
  elements to read. Data elements can be bytes or instances of data type"; the
  book "LabVIEW for Everyone" (Advanced File IO, flylib.com/books/en/3.352.1.175/1/):
  "If you wire the data type input (recommended), then the count input assumes
  you're telling it to count data points (elements), not bytes ... If you do not
  wire the data type input, then the count input assumes you're telling it how many
  bytes to read"; the forum answer that the default type is a string (forums.ni.com
  reading-bytes-from-a-binary-file-directly-what-format-specifier/td-p/3903138).
  OpenG's Compare File (Binary) reads its blocks this way. ``count`` unwired is one
  byte; -1 is every byte to the end of the file; fewer bytes left than ``count``
  keeps the bytes read and gives error 4, as NI says of elements.
* ``data type`` wired: the type of one instance. It is read the way Unflatten From
  String reads a value, with the ``struct`` calls for that type
  (``codegen/flat.py``), big-endian, native or little-endian by ``byte order``
  (0, 1, 2; default big-endian), every string or array carrying its size.
* ``count``: "By default, the function returns a single data element": unwired,
  ``data`` is one value of the type. Wired, ``data`` is an array of ``count``
  values (a list); "If count is -1, the function reads the entire file starting
  from the current file position"; "If count is less than -1, the function
  returns an error". A cluster wired to ``count`` gives a multidimensional array:
  a gap.
* "if it reaches the end of the file, it returns all the complete data elements
  read thus far and an end-of-file error": the list keeps the elements read and
  the node's error out is error 4 (NI, General LabVIEW Error Codes: "End of file
  encountered").
* ``file``: "can be a refnum or absolute file path. If it is a path, this function
  opens the file ... The default is to display a file dialog box": unwired is a
  gap. The file is read from its current position and left just after what was
  read. A path is closed after the read unless ``refnum out`` is wired.
* ``cancelled`` is FALSE (no dialog is shown).

Terminals (from the primitives entry): in 5 = data type, 9 = byte order, 10 =
count, 11 = file; out 1 = cancelled, 2 = data, 3 = refnum out.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import BYTE_ORDER_PREFIX, Functions, UnsupportedLayout, layout_of
from ...fragment import CodeFragment
from ...type_default import type_default
from . import register_op_fragment
from ._flat_node import (
    add_flat_imports,
    constant_input,
    terminal,
    unsupported_layout_error,
)
from ._path_checks import absolute_path_check
from ._regex_node import input_expression, is_wired_output, string_variable


@dataclass(frozen=True)
class _Names:
    """The generated variables the read statements use."""

    file: str
    value: str
    data: str
    end: str
    start: str


def _indent(source: str, spaces: int) -> str:
    pad = " " * spaces
    return "".join(pad + line for line in source.splitlines(keepends=True))


def _read_one(n: _Names, size: int | None, reader: str, default: str) -> str:
    """Statements that read one value of the type into ``n.value``, which holds
    the type's default until it is read."""
    amount = "" if size is None else str(size)
    ends = "End of file encountered: the file ends inside the data type"
    return (
        f"{n.value} = {default}\n"
        f"{n.data} = {n.file}.read({amount})\n"
        "try:\n"
        f"    {n.value}, {n.end} = {reader}({n.data}, 0)\n"
        "except struct.error as _short:\n"
        f"    raise EOFError({ends!r}) from _short\n"
        f"if {n.end} > len({n.data}):\n"
        f"    raise EOFError({ends!r})\n"
    )


def _read_many(n: _Names, size: int | None, reader: str, count: str) -> str:
    """Statements that read ``count`` values (every one with -1) into the list
    ``n.value``."""
    amount = "" if size is None else f"{count} * {size} if {count} >= 0 else -1"
    ends = f"'End of file encountered after ' + str(len({n.value})) + ' elements'"
    return (
        f"{n.value} = []\n"
        f"if {count} < -1:\n"
        f"    raise ValueError('count is ' + str({count}) + "
        "'; it must be -1 or more')\n"
        f"{n.data} = {n.file}.read({amount})\n"
        f"{n.end} = 0\n"
        f"while {n.end} < len({n.data}) if {count} < 0 else "
        f"len({n.value}) < {count}:\n"
        "    try:\n"
        f"        _item, {n.end} = {reader}({n.data}, {n.end})\n"
        "    except struct.error as _short:\n"
        f"        raise EOFError({ends}) from _short\n"
        f"    if {n.end} > len({n.data}):\n"
        f"        raise EOFError({ends})\n"
        f"    {n.value}.append(_item)\n"
    )


def _read_bytes(n: _Names, count: str) -> str:
    """Statements that read ``count`` bytes (every one with -1) into the string
    ``n.value``, which keeps the bytes read when the file ends before ``count``."""
    return (
        f"{n.value} = ''\n"
        f"if {count} < -1:\n"
        f"    raise ValueError('count is ' + str({count}) + "
        "'; it must be -1 or more')\n"
        f"{n.data} = {n.file}.read({count} if {count} >= 0 else -1)\n"
        f"{n.value} = {n.data}.decode('latin-1')\n"
        f"{n.end} = len({n.data})\n"
        f"if {count} >= 0 and {n.end} < {count}:\n"
        "    raise EOFError('End of file encountered after ' + "
        f"str({n.end}) + ' bytes')\n"
    )


@register_op_fragment("READ_FROM_BINARY_FILE")
def read_from_binary_file(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    file_text = input_expression(ctx, node, 11)
    if file_text is None:
        raise ConversionGap(
            "Read from Binary File with no file shows a file dialog", node
        )
    file_term = terminal(node, "input", 11)
    file_type = file_term.lv_type if file_term is not None else None
    is_path = file_type is not None and file_type.underlying_type == "Path"
    is_refnum = file_type is not None and file_type.underlying_type == "Refnum"
    if not (is_path or is_refnum):
        raise ConversionGap(
            "Read from Binary File reads a path or a file refnum only", node
        )
    type_term = terminal(node, "input", 5)
    if type_term is None or type_term.lv_type is None:
        raise ConversionGap("Read from Binary File has no data type", node)
    count_term = terminal(node, "input", 10)
    count = input_expression(ctx, node, 10)
    if (
        count is not None
        and count_term is not None
        and count_term.lv_type is not None
        and count_term.lv_type.kind == LVTypeKind.CLUSTER
    ):
        raise ConversionGap(
            "Read from Binary File with a cluster of counts returns a "
            "multidimensional array",
            node,
        )
    order = BYTE_ORDER_PREFIX[int(constant_input(ctx, node, 9, 0))]
    typed = input_expression(ctx, node, 5) is not None
    reader, size = "", None
    if typed:
        try:
            layout = layout_of(type_term.lv_type)
        except UnsupportedLayout as error:
            raise unsupported_layout_error(node, error) from error
        reader = Functions(ctx, order).reader(layout, True)
        size = layout.fixed_size()

    statements: list[ast.stmt] = []
    data_term = terminal(node, "output", 2)
    refnum_out = terminal(node, "output", 3)
    keep_open = refnum_out is not None and ctx.is_wired(refnum_out.id)
    count_var = (
        string_variable(ctx, node, count, statements, "count")
        if count is not None
        else None
    )
    if is_path:
        path = string_variable(ctx, node, file_text, statements, "path")
        statements.extend(ast.parse(absolute_path_check(path)).body)
        file = ctx.make_output_var("file_handle", node.id)
    else:
        file = string_variable(ctx, node, file_text, statements, "file")
    names = _Names(
        file=file,
        value=ctx.make_output_var(
            "data",
            node.id,
            terminal_id=data_term.id if data_term is not None else None,
        ),
        data=ctx.make_output_var("bytes_read", node.id),
        end=ctx.make_output_var("end", node.id),
        start=ctx.make_output_var("file_start", node.id),
    )
    default = ast.unparse(type_default(ctx, type_term.lv_type))
    if not typed:
        reading = _read_bytes(names, count_var or "1")
    elif count_var is None:
        reading = _read_one(names, size, reader, default)
    else:
        reading = _read_many(names, size, reader, count_var)
    reading = (
        f"{names.start} = {file}.tell()\n"
        + reading
        + f"{file}.seek({names.start} + {names.end})\n"
    )
    if is_path and not keep_open:
        source = f"with open({path}, 'rb') as {file}:\n" + _indent(reading, 4)
    elif is_path:
        source = f"{file} = open({path}, 'rb')\n" + reading
    else:
        source = reading
    statements.extend(ast.parse(source).body)
    bindings: dict[str, str] = {}
    if data_term is not None:
        bindings[data_term.id] = names.value
    if refnum_out is not None:
        bindings[refnum_out.id] = file
    cancelled = terminal(node, "output", 1)
    if cancelled is not None and is_wired_output(ctx, node, 1):
        var = ctx.make_output_var("cancelled", node.id, terminal_id=cancelled.id)
        statements.append(build_assign(var, parse_expr("False")))
        bindings[cancelled.id] = var
    add_flat_imports(ctx, statements)
    return CodeFragment(statements=statements, bindings=bindings)

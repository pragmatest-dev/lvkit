"""TDMS Open (primResIDs 2012 and 24112), written with npTDMS.

NI, "TDMS Open Function" (labview-api-ref, ``functions/tdms-open.html``): opens
a .tdms file for reading or writing, or creates one, by ``operation``: 0 open
(to write to), 1 open or create, 2 create or replace, 3 create, 4 open
(read-only). npTDMS (https://nptdms.readthedocs.io): ``TdmsWriter(path, mode,
version, index_file)`` writes a file whose ``version`` is 4712 or 4713 (the
format versions NI calls 1.0 and 2.0) and ``TdmsFile.open(path)`` reads one.

The ``tdms file out`` refnum is the pair ``(writer, path)``, the npTDMS writer
(or the opened ``TdmsFile`` when read-only) and the path ``TDMS Close`` returns.

* a file created gets the extension .tdms when its path has another ("If you use
  this function to create a new file, the file extension ... must be .tdms.
  Otherwise, this function automatically appends .tdms");
* ``operation`` 0 on a file that is not there is a missing file; 3 on one that is
  there is an existing file; 0 and 1 append to a file that is there, in the
  format version it has ("If you use this function to update an existing .tdms
  file, LabVIEW writes data in the byte order of that file");
* ``create index file?`` is the writer's ``index_file``; NI: "LabVIEW ignores
  this input if you open a .tdms file that already has a .tdms_index file", and
  an index is not started for a file that has none;
* ``byte order`` is valid only for a file the function creates, and npTDMS
  writes little-endian only: anything but the default 2 (little-endian) on an
  operation that can create a file is a gap;
* ``disable buffering?`` only chooses between two ways of caching the transfer
  (NI: "can speed up data transfers in certain situations"), never what is
  written, so Python's file buffering stands for both values.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, literal
from ._tdms import NPTDMS_IMPORT, OPEN_PANES, OpenPane

OPEN, OPEN_OR_CREATE, CREATE_OR_REPLACE, CREATE, READ_ONLY = range(5)
LITTLE_ENDIAN = 2
# NI's TDMS Open: file format version 0 is 1.0 and 1 is 2.0 (the default); the
# format versions the files carry are 4712 and 4713.
VERSION_1_0 = 4712
DEFAULT_VERSION_INDEX = 1


def _enum(
    ctx: CodeGenContext, node: PrimitiveNode, index: int, default: int, what: str
) -> int:
    """The value of the enum input ``index``; ``default`` when unwired. A value
    computed when the code runs is a gap."""
    text = input_expression(ctx, node, index)
    if text is None:
        return default
    value = literal(text)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ConversionGap(
        f"TDMS Open with a {what} computed when the code runs: it picks the "
        "writer it is built with",
        node,
    )


def _index_file(ctx: CodeGenContext, node: PrimitiveNode, pane: OpenPane) -> str:
    """The expression for ``create index file?``: NI's default TRUE when
    unwired."""
    return input_expression(ctx, node, pane.create_index_file) or "True"


@register_op_fragment("TDMS_OPEN")
def tdms_open(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    pane = OPEN_PANES[node.prim_id or 0]
    path_text = input_expression(ctx, node, pane.file_path)
    if path_text is None:
        raise ConversionGap("TDMS Open with no file path", node)
    operation = _enum(ctx, node, pane.operation, OPEN, "operation")
    if operation not in range(5):
        raise ConversionGap(f"TDMS Open with the operation {operation}", node)
    byte_order = _enum(ctx, node, pane.byte_order, LITTLE_ENDIAN, "byte order")
    creates = operation in (OPEN_OR_CREATE, CREATE_OR_REPLACE, CREATE)
    if creates and byte_order != LITTLE_ENDIAN:
        raise ConversionGap(
            "TDMS Open with a byte order other than little-endian: npTDMS "
            "writes little-endian only",
            node,
        )
    version = _version(ctx, node, pane)

    path = ctx.make_output_var("path", node.id)
    out = terminal(node, "output", pane.refnum_out)
    ref = ctx.make_output_var(
        "tdms_file", node.id, terminal_id=out.id if out is not None else None
    )
    lines = [f"{path} = Path({path_text})"]
    if operation == READ_ONLY:
        lines.append(f"{ref} = (nptdms.TdmsFile.open(str({path})), {path})")
    else:
        lines += _writer_lines(ctx, node, pane, operation, version, path, ref)
    statements = ast.parse("\n".join(lines)).body
    ctx.add_import("from pathlib import Path")
    ctx.add_import(NPTDMS_IMPORT)
    if "os." in "\n".join(lines):
        ctx.add_import("import os")
    bindings = {out.id: ref} if out is not None else {}
    return CodeFragment(statements=statements, bindings=bindings)


def _version(ctx: CodeGenContext, node: PrimitiveNode, pane: OpenPane) -> str:
    """The expression for the format version number a file is created in."""
    text = input_expression(ctx, node, pane.file_format_version)
    if text is None:
        return str(VERSION_1_0 + DEFAULT_VERSION_INDEX)
    value = literal(text)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(VERSION_1_0 + value)
    return f"{VERSION_1_0} + {text}"


def _writer_lines(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    pane: OpenPane,
    operation: int,
    version: str,
    path: str,
    ref: str,
) -> list[str]:
    """The statements that open ``path`` for writing in the mode ``operation``
    names and bind the refnum ``ref``."""
    index = _index_file(ctx, node, pane)
    writer = ctx.make_output_var("writer", node.id)
    lines: list[str] = []
    if operation in (OPEN_OR_CREATE, CREATE_OR_REPLACE, CREATE):
        # The extension is added to a file the function creates; an existing
        # file opened or created keeps its own name.
        has_other_extension = f"{path}.suffix.lower() != '.tdms'"
        if operation == OPEN_OR_CREATE:
            has_other_extension += f" and not {path}.exists()"
        lines += [
            f"if {has_other_extension}:",
            f"    {path} = {path}.with_name({path}.name + '.tdms')",
        ]
    if operation == OPEN:
        lines += [
            f"if not {path}.exists():",
            f"    raise FileNotFoundError(str({path}))",
        ]
    if operation in (OPEN, OPEN_OR_CREATE):
        existed = ctx.make_output_var("existed", node.id)
        number = ctx.make_output_var("version", node.id)
        has_index = f"os.path.exists(str({path}) + '_index')"
        index_file = has_index if operation == OPEN else f"not {existed} or {has_index}"
        if index != "True":
            index_file = f"({index}) and ({index_file})"
        lines += [
            f"{existed} = {path}.exists()",
            f"{number} = (nptdms.TdmsFile.read_metadata(str({path})).tdms_version "
            f"if {existed} else None) or {version}",
            f"{writer} = nptdms.TdmsWriter(str({path}), 'a', "
            f"version={number}, index_file={index_file})",
        ]
    else:
        mode = "w" if operation == CREATE_OR_REPLACE else "x"
        lines.append(
            f"{writer} = nptdms.TdmsWriter(str({path}), '{mode}', "
            f"version={version}, index_file={index})"
        )
    lines += [f"{writer}.open()", f"{ref} = ({writer}, {path})"]
    return lines

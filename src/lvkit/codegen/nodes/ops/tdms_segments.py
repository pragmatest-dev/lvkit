"""TDMS Write, TDMS Set Properties and TDMS Close (primResIDs 2010, 2014, 2013 and
the VI-Scripting numbers 24110, 24114, 24113), written with npTDMS on the refnum
TDMS Open makes: the pair ``(writer, path)``.

NI, "TDMS Write Function" (``functions/tdms-write.html``): streams ``data`` to
the channels ``channel name(s) in`` names, in the group ``group name in`` (the
default is "Untitled"); unwired channel names are named "Untitled", "Untitled1",
"Untitled2"...; ``group name out`` and ``channel name(s) out`` return the names
written. npTDMS: ``writer.write_segment([ChannelObject(group, channel, data)])``.
The data of one channel is a 1D array of numbers, Booleans or strings; a 1D
array is one channel whatever the ``data layout``, which only says how a 2D
array's rows and columns are the channels, so a 2D array is a gap, as are the
data types that need a timestamp (a waveform, dynamic data, a digital table).

NI, "TDMS Set Properties Function" (``functions/tdms-set-properties.html``): "If
you wire the group name and channel name inputs with values, the function writes
the properties at the channel level. Wiring only the group name input with a
value writes the properties at the channel group level. If both ... contain no
values, the properties become file-specific." npTDMS writes a segment of
``RootObject``, ``GroupObject`` or ``ChannelObject`` with ``properties``.

NI, "TDMS Close Function" (``functions/tdms-close.html``): closes the file the
refnum names and returns its path; "This node runs normally even if an error
occurred before this node runs", so a refnum a failed TDMS Open never made is
closed as nothing.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import is_boolean_type_name, is_string_type_name
from lvkit.models import LVTypeKind, Terminal

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable
from ._tdms import (
    CLOSE_PANES,
    NPTDMS_IMPORT,
    SET_PROPERTIES_PANES,
    WRITE_PANES,
    SetPropertiesPane,
    WritePane,
)

UNTITLED = "'Untitled'"
# The numpy type of a channel of each LabVIEW numeric type: npTDMS picks the
# smallest integer type that holds the values of a list, so the type is written.
NUMPY_TYPES = {
    "NumInt8": "int8",
    "NumInt16": "int16",
    "NumInt32": "int32",
    "NumInt64": "int64",
    "NumUInt8": "uint8",
    "NumUInt16": "uint16",
    "NumUInt32": "uint32",
    "NumUInt64": "uint64",
    "NumFloat32": "float32",
    "NumFloat64": "float64",
}


def _refnum(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    index: int,
    statements: list[ast.stmt],
    what: str,
) -> str:
    """The name of the refnum input ``index``."""
    text = input_expression(ctx, node, index)
    if text is None:
        raise ConversionGap(f"{what} with no tdms file wired", node)
    return string_variable(ctx, node, text, statements, "tdms_file")


def _output(node: PrimitiveNode, index: int) -> Terminal | None:
    return terminal(node, "output", index)


def _bind(
    bindings: dict[str, str], node: PrimitiveNode, index: int, value: str
) -> None:
    out = _output(node, index)
    if out is not None:
        bindings[out.id] = value


def _is_array(term: Terminal | None) -> bool:
    lv_type = term.lv_type if term is not None else None
    return lv_type is not None and lv_type.kind == LVTypeKind.ARRAY


WAVEFORM_GAP = (
    "TDMS Write of a waveform is not converted: the waveform's start time is "
    "the channel's wf_start_time, and lvkit generates no datetime (the value "
    "npTDMS writes) for a timestamp"
)


def _channel_data(node: PrimitiveNode, term: Terminal | None, data: str) -> str:
    """The expression for the data of the one channel a TDMS Write writes."""
    lv_type = term.lv_type if term is not None else None
    if lv_type is None:
        raise ConversionGap("TDMS Write of data of an unknown type", node)
    element = lv_type.element_type if lv_type.kind == LVTypeKind.ARRAY else lv_type
    if element is not None and element.underlying_type == "MeasureData":
        raise ConversionGap(WAVEFORM_GAP, node)
    if lv_type.kind != LVTypeKind.ARRAY:
        raise ConversionGap(
            f"TDMS Write of {term.type_label() if term else 'that type'} is not "
            "converted: only a 1D array is",
            node,
        )
    if (lv_type.dimensions or 1) != 1:
        raise ConversionGap(
            "TDMS Write of a 2D array is not converted: NI's page does not say "
            "whether the rows or the columns are the channels of each data layout",
            node,
        )
    name = element.underlying_type if element else None
    if name in NUMPY_TYPES:
        return f"numpy.array({data}, dtype=numpy.{NUMPY_TYPES[name]})"
    if is_boolean_type_name(name):
        return f"numpy.array({data}, dtype=numpy.bool_)"
    if is_string_type_name(name):
        return data
    raise ConversionGap(
        f"TDMS Write of a 1D array of {name} is not converted: npTDMS has no "
        "channel type for it",
        node,
    )


@register_op_fragment("TDMS_WRITE")
def tdms_write(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    pane: WritePane = WRITE_PANES[node.prim_id or 0]
    statements: list[ast.stmt] = []
    ref = _refnum(ctx, node, pane.refnum, statements, "TDMS Write")
    data_text = input_expression(ctx, node, pane.data)
    if data_text is None:
        raise ConversionGap("TDMS Write with no data wired", node)
    data = _channel_data(node, terminal(node, "input", pane.data), data_text)
    group_text = input_expression(ctx, node, pane.group_name)
    group = string_variable(ctx, node, group_text or UNTITLED, statements, "group")
    names_text = input_expression(ctx, node, pane.channel_names)
    channel = ctx.make_output_var("channel", node.id)
    if names_text is None:
        channel_value = UNTITLED
    elif _is_array(terminal(node, "input", pane.channel_names)):
        names = string_variable(ctx, node, names_text, statements, "channel_names")
        channel_value = f"{names}[0] if {names} else {UNTITLED}"
    else:
        channel_value = names_text
    source = (
        f"{channel} = {channel_value}\n"
        f"{ref}[0].write_segment([nptdms.ChannelObject({group}, {channel}, {data})])"
    )
    statements.extend(ast.parse(source).body)
    ctx.add_import(NPTDMS_IMPORT)
    if "numpy." in data:
        ctx.add_import("import numpy")
    bindings: dict[str, str] = {}
    _bind(bindings, node, pane.refnum_out, ref)
    _bind(bindings, node, pane.group_name_out, group)
    _bind(bindings, node, pane.channel_names_out, f"[{channel}]")
    return CodeFragment(statements=statements, bindings=bindings)


def _properties_object(
    node: PrimitiveNode,
    group: str | None,
    channel: str | None,
    properties: str,
    target: str,
) -> list[str]:
    """The statements that bind ``target`` to the object the properties are
    written on: the channel's when a channel name has a value, else the group's
    when a group name has, else the file's."""
    if channel is not None and group is None:
        raise ConversionGap(
            "TDMS Set Properties with a channel name and no group name: NI's "
            "page says a channel name needs a group name too",
            node,
        )
    branches: list[tuple[str, str]] = []
    if channel is not None:
        branches.append(
            (channel, f"nptdms.ChannelObject({group}, {channel}, None, {properties})")
        )
    if group is not None:
        branches.append((group, f"nptdms.GroupObject({group}, {properties})"))
    root = f"nptdms.RootObject({properties})"
    if not branches:
        return [f"{target} = {root}"]
    lines: list[str] = []
    for i, (condition, made) in enumerate(branches):
        keyword = "if" if i == 0 else "elif"
        lines += [f"{keyword} {condition}:", f"    {target} = {made}"]
    return [*lines, "else:", f"    {target} = {root}"]


@register_op_fragment("TDMS_SET_PROPERTIES")
def tdms_set_properties(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    pane: SetPropertiesPane = SET_PROPERTIES_PANES[node.prim_id or 0]
    statements: list[ast.stmt] = []
    ref = _refnum(ctx, node, pane.refnum, statements, "TDMS Set Properties")
    group_text = input_expression(ctx, node, pane.group_name)
    channel_text = input_expression(ctx, node, pane.channel_name)
    group = (
        string_variable(ctx, node, group_text, statements, "group")
        if group_text
        else None
    )
    channel = (
        string_variable(ctx, node, channel_text, statements, "channel")
        if channel_text
        else None
    )
    names = input_expression(ctx, node, pane.property_names)
    values = input_expression(ctx, node, pane.property_values)
    properties = f"dict(zip({names}, {values}))" if names and values else "{}"
    object_var = ctx.make_output_var("segment_object", node.id)
    lines = _properties_object(node, group, channel, properties, object_var)
    source = "\n".join(lines) + f"\n{ref}[0].write_segment([{object_var}])"
    statements.extend(ast.parse(source).body)
    ctx.add_import(NPTDMS_IMPORT)
    bindings: dict[str, str] = {}
    _bind(bindings, node, pane.refnum_out, ref)
    _bind(bindings, node, pane.group_name_out, group or "''")
    _bind(bindings, node, pane.channel_name_out, channel or "''")
    return CodeFragment(statements=statements, bindings=bindings)


@register_op_fragment("TDMS_CLOSE")
def tdms_close(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    pane = CLOSE_PANES[node.prim_id or 0]
    statements: list[ast.stmt] = []
    ref = _refnum(ctx, node, pane.refnum, statements, "TDMS Close")
    source = f"if {ref} is not None:\n    {ref}[0].close()"
    bindings: dict[str, str] = {}
    out = _output(node, pane.file_path_out)
    if out is not None and ctx.is_wired(out.id):
        path = ctx.make_output_var("file_path", node.id, terminal_id=out.id)
        source += f"\n{path} = {ref}[1] if {ref} is not None else Path('')"
        ctx.add_import("from pathlib import Path")
        bindings[out.id] = path
    statements.extend(ast.parse(source).body)
    return CodeFragment(statements=statements, bindings=bindings)

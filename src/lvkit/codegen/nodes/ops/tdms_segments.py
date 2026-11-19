"""TDMS Write, TDMS Set Properties and TDMS Close (primResIDs 2010, 2014, 2013 and
the VI-Scripting numbers 24110, 24114, 24113), written with npTDMS on the refnum
TDMS Open makes: the pair ``(writer, path)``.

NI, "TDMS Write Function" (``functions/tdms-write.html``): streams ``data`` to
the channels ``channel name(s) in`` names, in the group ``group name in`` (the
default is "Untitled"); unwired channel names are named "Untitled", "Untitled1",
"Untitled2"...; ``group name out`` and ``channel name(s) out`` return the names
written. npTDMS: ``writer.write_segment([ChannelObject(group, channel, data)])``.
A 1D or 2D array of numbers, Booleans, strings or timestamps is written by
``tdms_channels``. An analog waveform, or a 1D array of them, is written by
``tdms_waveform``; dynamic data, a digital waveform and a digital table are gaps.

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
from lvkit.models import LVTypeKind, Terminal

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, literal, string_variable
from ._tdms import (
    CLOSE_PANES,
    NPTDMS_IMPORT,
    SET_PROPERTIES_PANES,
    WRITE_PANES,
    SetPropertiesPane,
    WritePane,
)
from .tdms_channels import (
    channel_data,
    interleaved_gap_source,
    rows_write_source,
)
from .tdms_waveform import IMPORTS as WAVEFORM_IMPORTS
from .tdms_waveform import waveform_data, waveform_write_source

UNTITLED = "'Untitled'"


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


@register_op_fragment("TDMS_WRITE")
def tdms_write(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    pane: WritePane = WRITE_PANES[node.prim_id or 0]
    statements: list[ast.stmt] = []
    ref = _refnum(ctx, node, pane.refnum, statements, "TDMS Write")
    data_text = input_expression(ctx, node, pane.data)
    if data_text is None:
        raise ConversionGap("TDMS Write with no data wired", node)
    data_term = terminal(node, "input", pane.data)
    waveforms = waveform_data(node, data_term)
    group_text = input_expression(ctx, node, pane.group_name)
    group = string_variable(ctx, node, group_text or UNTITLED, statements, "group")
    names_text = input_expression(ctx, node, pane.channel_names)
    channel = ctx.make_output_var("channel", node.id)
    if waveforms is not None:
        return _write_waveforms(
            ctx, node, pane, statements, (ref, group, channel), data_text, waveforms
        )
    array = channel_data(node, data_term)
    layout = input_expression(ctx, node, pane.data_layout)
    bindings: dict[str, str] = {}
    _bind(bindings, node, pane.refnum_out, ref)
    _bind(bindings, node, pane.group_name_out, group)
    if array.dimensions == 2:
        channels = ctx.make_output_var("channels", node.id)
        source = rows_write_source(
            ref,
            group,
            data_text,
            layout or "0",
            array.convert,
            names_text,
            _is_array(terminal(node, "input", pane.channel_names)),
            channels,
            channel,
        )
        _bind(bindings, node, pane.channel_names_out, channels)
    else:
        if layout is not None and literal(layout) == 1:
            raise ConversionGap(
                "TDMS Write of a 1D array with an interleaved data layout is not "
                "converted: NI does not say how many channels it holds",
                node,
            )
        if layout is not None and literal(layout) is None:
            statements.extend(ast.parse(interleaved_gap_source(layout)).body)
        if names_text is None:
            channel_value = UNTITLED
        elif _is_array(terminal(node, "input", pane.channel_names)):
            names = string_variable(ctx, node, names_text, statements, "channel_names")
            channel_value = f"{names}[0] if {names} else {UNTITLED}"
        else:
            channel_value = names_text
        data = array.convert.format(data_text)
        source = (
            f"{channel} = {channel_value}\n"
            f"{ref}[2].add(({group}, {channel}))\n"
            f"{ref}[0].write_segment("
            f"[nptdms.ChannelObject({group}, {channel}, {data})])"
        )
        _bind(bindings, node, pane.channel_names_out, f"[{channel}]")
    statements.extend(ast.parse(source).body)
    for needed in (NPTDMS_IMPORT, *array.imports):
        ctx.add_import(needed)
    return CodeFragment(statements=statements, bindings=bindings)


def _write_waveforms(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    pane: WritePane,
    statements: list[ast.stmt],
    names: tuple[str, str, str],
    data_text: str,
    is_array: bool,
) -> CodeFragment:
    """TDMS Write of analog waveforms: one channel each (``tdms_waveform``)."""
    ref, group, channel = names
    names_text = input_expression(ctx, node, pane.channel_names)
    names_is_array = _is_array(terminal(node, "input", pane.channel_names))
    channels = ctx.make_output_var("channels", node.id)
    source = waveform_write_source(
        ref, group, data_text, is_array, names_text, names_is_array, channels, channel
    )
    statements.extend(ast.parse(source).body)
    for needed in (NPTDMS_IMPORT, *WAVEFORM_IMPORTS):
        ctx.add_import(needed)
    bindings: dict[str, str] = {}
    _bind(bindings, node, pane.refnum_out, ref)
    _bind(bindings, node, pane.group_name_out, group)
    _bind(bindings, node, pane.channel_names_out, channels)
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

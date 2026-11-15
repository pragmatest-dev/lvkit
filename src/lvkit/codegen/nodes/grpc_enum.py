"""The two gRPC enum conversion VIs of the LabVIEW gRPC library (``Get LV Enum
Value from Proto Value`` and ``Get Proto Enum Value from LV Value``).

The library registers each enum as a list of ``name=number`` items (Register
Enum Metadata) and converts by position: the LabVIEW enum's value is the index
of the item in that list, the proto value is the item's number; a value the list
does not hold is the library's error -2003 (``enum_metadata.h``,
``grpc_interop.cc``). The list is the proto enum's values in declaration order,
which protobuf's ``EnumTypeWrapper.values()`` returns too, so the conversion is
``Enum.values().index(proto)`` (the first item of an alias, as the library
keeps) and ``Enum.values()[lv]``, on the Python class of the enum the call names.
The enum name is a constant of the call; ``proto_enums`` gives its class. A name
that is not a constant, or not in the table, is a gap.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import TerminalDirection
from lvkit.proto_enums import find_proto_enum
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..output_binding import bind_output
from .grpc_pane import bind_passthrough, constant_text, index_of, wired

_ENUM_NAME = "enum_name"


def _generate(
    node: VINode,
    ctx: CodeGenContext,
    entry: VIEntry,
    value_in: str,
    value_out: str,
    convert: str,
) -> CodeFragment:
    name = constant_text(wired(node, entry, _ENUM_NAME, ctx))
    if name is None:
        raise ConversionGap(
            f"{entry.name}: the enum name is not a constant, so the Python "
            "class of the proto enum is not known when the code is generated",
            node,
        )
    proto_enum = find_proto_enum(name)
    if proto_enum is None:
        raise ConversionGap(
            f"{entry.name}: the proto enum {name!r} is not in lvkit's table of "
            "protobuf enums with a Python package (data/grpc/proto_enums.json)",
            node,
        )
    value = wired(node, entry, value_in, ctx)
    if value is None:
        raise ConversionGap(f"{entry.name} has no value wired to convert", node)
    ctx.require_packages([proto_enum.distribution])
    values = f"{proto_enum.expression()}.values()"
    expression = convert.format(values=values, value=value)

    fragment = CodeFragment(imports={proto_enum.import_statement()})
    out_index = index_of(entry, value_out, TerminalDirection.OUTPUT)
    for term in node.terminals:
        if (
            term.direction == TerminalDirection.OUTPUT
            and term.index == out_index
            and ctx.is_wired(term.id)
        ):
            fragment.extend(
                bind_output(node, term.id, value_out, parse_expr(expression), ctx)
            )
    bind_passthrough(node, entry, ctx, fragment.bindings)
    return fragment


def proto_to_lv(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Get LV Enum Value from Proto Value``: the index of the proto value."""
    return _generate(
        node,
        ctx,
        entry,
        "proto_enum_value",
        "labview_enum_value",
        "{values}.index({value})",
    )


def lv_to_proto(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Get Proto Enum Value from LV Value``: the number at the LabVIEW value."""
    return _generate(
        node,
        ctx,
        entry,
        "labview_enum_value",
        "proto_enum_value",
        "{values}[{value}]",
    )

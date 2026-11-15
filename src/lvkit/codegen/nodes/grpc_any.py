"""The gRPC library's message-packing VIs (``PackToAny``, ``UnpackFromAny``,
``Cluster to gRPC LabVIEW Message``, ``Register Reflection Descriptors``) written
on ``protobuf``'s message classes.

``PackToAny`` makes the message named by ``Message Type`` from the cluster and
gives the ``Any`` cluster: ``Type Url`` is ``"/"`` and the message's full proto
name, ``Bytes`` the message serialized (``any_support.cc``, ``PackToAny`` and
``PackToBuffer``; the full name is the type URL the library's own ``.proto``
parser records, ``proto_parser.cc``). The cluster is read into the message the way
``Client Unary Call`` reads its request (``json_format.ParseDict`` on the plain
form, ``grpc_client.request_message``), and the ``Any`` cluster is the pane's own
cluster of ``Type Url`` and ``Bytes``, because the callers read both by name.

``UnpackFromAny`` parses ``Bytes`` as the named message and copies it into the
``Data Type`` cluster. The library does not read ``Type Url`` (``UnpackFromAny``
calls ``UnpackFromBuffer`` on ``Bytes`` alone, ``any_support.cc``); bytes that do
not parse are error -2 (``ParseFromString`` failing), which the catalog entry's
``errors`` field writes as ``DecodeError``. The cluster is read from the message the
way ``Client Unary Call`` reads its response (``grpc_client.read_response``).

The message is named by a constant of the call, as in the client calls: the Python
class is chosen when the code is generated (``grpc_client.message_class``).

``Cluster to gRPC LabVIEW Message`` makes the library's description of the
messages and enums of a cluster's type, which ``Register Message Metadata`` and
``Register Enum Metadata`` give to the library's C++ side; the ``_pb2`` classes
are that description in Python, so the two arrays are empty. ``Register
Reflection Descriptors`` hands the library the serialized file descriptors its
server's reflection service answers with (``DeserializeReflectionInfo``,
``lv_proto_server_reflection_plugin.cc``): there is no server in the generated
code, so it passes its id on.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import LVType, LVTypeKind, TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..plain_values import (
    PlainErrors,
    PlainOptions,
    UnsupportedPlainType,
    from_plain,
    plain_fields,
    to_plain,
)
from .grpc_client import (
    PROTOBUF,
    message_class,
    read_response,
    request_message,
)
from .grpc_pane import bind_passthrough, output_of, terminal_of

_ANY_ERRORS = PlainErrors(
    type_mismatch="TypeError",
    element_missing="KeyError",
    extra_items="ValueError",
    out_of_range="OverflowError",
    dimensions="ValueError",
    ragged="ValueError",
)


def _any_keys(any_type: LVType | None, ctx: CodeGenContext, node: VINode) -> tuple:
    """The plain keys of the ``Any`` cluster's ``Type Url`` (the string) and
    ``Bytes`` (the array of bytes), found by their types."""
    if any_type is None:
        raise ConversionGap("the type of the Any cluster is not recorded", node)
    try:
        fields = plain_fields(any_type, ctx)
    except UnsupportedPlainType as error:
        raise ConversionGap(f"cannot read the Any cluster: {error}", node) from error
    urls = [k for k, t in fields if t.underlying_type == "String"]
    byte_arrays = [
        k
        for k, t in fields
        if t.kind == LVTypeKind.ARRAY
        and t.element_type is not None
        and t.element_type.underlying_type == "NumUInt8"
    ]
    if len(fields) != 2 or len(urls) != 1 or len(byte_arrays) != 1:
        raise ConversionGap(
            "the Any cluster is not a cluster of a string (Type Url) and an "
            "array of bytes (Bytes)",
            node,
        )
    return urls[0], byte_arrays[0]


def pack_to_any(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``PackToAny``: the ``Any`` cluster of the message made from the data."""
    message = message_class(node, ctx, entry, "message_type", "Message Type")
    fragment = CodeFragment()
    bind_passthrough(node, entry, ctx, fragment.bindings)
    out = output_of(node, entry, "any")
    if out is None or not ctx.is_wired(out.id):
        return fragment
    url_key, bytes_key = _any_keys(out.lv_type, ctx, node)
    assert out.lv_type is not None
    statements: list[ast.stmt] = []
    built = request_message(node, ctx, entry, message, statements, param="data")
    packed = ctx.make_output_var("packed", node.id)
    variable = ctx.make_output_var("any_value", node.id, terminal_id=out.id)
    plain = (
        f"{{{url_key!r}: {'/' + message.proto_name!r}, "
        f"{bytes_key!r}: list({built}.SerializeToString())}}"
    )
    statements.append(build_assign(packed, parse_expr(plain)))
    options = PlainOptions(null_to_default="False", strict="True", errors=_ANY_ERRORS)
    statements.extend(
        from_plain(packed, out.lv_type, "None", variable, ctx, node.id, options)
    )
    fragment.statements = statements
    fragment.bindings[out.id] = variable
    fragment.imports.update(
        {"from google.protobuf import json_format", message.import_statement()}
    )
    ctx.require_packages([PROTOBUF, message.distribution])
    return fragment


def unpack_from_any(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``UnpackFromAny``: the cluster read from the message in ``Bytes``."""
    message = message_class(node, ctx, entry, "message_type", "Message Type")
    fragment = CodeFragment()
    bind_passthrough(node, entry, ctx, fragment.bindings)
    out = output_of(node, entry, "unpacked")
    if out is None or not ctx.is_wired(out.id):
        return fragment
    any_term = terminal_of(node, entry, "any", TerminalDirection.INPUT)
    if any_term is None or not ctx.is_wired(any_term.id):
        raise ConversionGap("no Any is wired", node)
    _, bytes_key = _any_keys(any_term.lv_type, ctx, node)
    assert any_term.lv_type is not None
    try:
        plain_any = to_plain(
            ctx.wired_input(any_term.id, node) or "None", any_term.lv_type, ctx
        )
    except UnsupportedPlainType as error:
        raise ConversionGap(f"cannot read the Any cluster: {error}", node) from error
    parsed = ctx.make_output_var("message", node.id)
    statements = [
        build_assign(parsed, parse_expr(f"{message.expression()}()")),
        ast.Expr(
            value=parse_expr(
                f"{parsed}.ParseFromString(bytes({plain_any}[{bytes_key!r}]))"
            )
        ),
    ]
    fragment.statements = statements
    fragment.extend(
        read_response(
            node,
            ctx,
            entry,
            parsed,
            out.id,
            type_param="data_type",
            type_label="Data Type",
        )
    )
    fragment.imports.update(
        {"from google.protobuf import json_format", message.import_statement()}
    )
    ctx.require_packages([PROTOBUF, message.distribution])
    return fragment


def cluster_to_message_metadata(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """``Cluster to gRPC LabVIEW Message``: the two metadata arrays are empty."""
    fragment = CodeFragment()
    for param in ("message_metadata", "enum_metadata"):
        out = output_of(node, entry, param)
        if out is not None and ctx.is_wired(out.id):
            variable = ctx.make_output_var(param, node.id, terminal_id=out.id)
            fragment.statements.append(build_assign(variable, parse_expr("[]")))
            fragment.bindings[out.id] = variable
    return fragment

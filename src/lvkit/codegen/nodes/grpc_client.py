"""The client side of the LabVIEW gRPC library (``Create Client``, ``Close
Client``, ``Client Unary Call``) and the VIs that only register message and enum
metadata, written on Python's own ``grpcio`` and ``protobuf``.

The library's client is a channel: ``CreateClient2`` makes one from the address,
with SSL credentials made from the root certificate file when ``certificate
path`` is not empty, else without credentials, and no limit on a message's size
(``grpc_client.cc``, ``LabVIEWgRPCClient::Connect``). The client id is the
channel and Close Client closes it.

``Client Unary Call`` calls the method named by ``Method Name`` with the request
message and reads the response message. Both message names and the method name
are constants of the call, so the generated code names the Python classes of the
messages (``proto_messages``, the classes of NI's ``_pb2`` packages) and the
method. LabVIEW passes the request as a cluster and the response as a cluster of
the wired ``Response Type``; the request is its plain form (``plain_values``)
read into the request message by ``json_format.ParseDict``, and the response
message is ``json_format.MessageToDict`` read into the type (a field the
response leaves out keeps the type's default; a 64-bit integer is text in
protobuf's JSON, and is read as the integer). ``timeout (ms)`` above 0 is the
call's deadline and anything else no deadline (``ClientUnaryCall2``); a failed
call is the error ``-(1000 + the gRPC status code)`` with the status's message
(``CompleteClientUnaryCall2``), which the catalog entry's ``errors`` field
writes.

``Register Message Metadata``, ``Register Enum Metadata`` and
``CompleteMetadataRegistration`` tell the library's client which messages and
enums exist; the ``_pb2`` import does that in Python, so they only pass their
id on.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import Terminal, TerminalDirection
from lvkit.proto_messages import ProtoMessage, find_proto_message
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..plain_values import (
    PlainErrors,
    PlainOptions,
    UnsupportedPlainType,
    from_plain,
    to_plain,
)
from .grpc_pane import bind_passthrough, constant_text, terminal_of, wired

GRPCIO = "grpcio"
PROTOBUF = "protobuf"
# ``Connect`` sets no limit on the size of a message in either direction.
_CHANNEL_OPTIONS = (
    "[('grpc.max_receive_message_length', -1), ('grpc.max_send_message_length', -1)]"
)
# A response that does not fit the Response Type, read from protobuf's JSON.
_RESPONSE_ERRORS = PlainErrors(
    type_mismatch="TypeError",
    element_missing="KeyError",
    extra_items="ValueError",
    out_of_range="OverflowError",
    dimensions="ValueError",
    ragged="ValueError",
)


def _out(node: VINode, entry: VIEntry, param: str) -> Terminal | None:
    return terminal_of(node, entry, param, TerminalDirection.OUTPUT)


def create_client(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Create Client``: the channel to ``address``."""
    out = _out(node, entry, "grpcid_out")
    if out is None or not ctx.is_wired(out.id):
        return CodeFragment.empty()
    address = wired(node, entry, "address", ctx)
    if address is None:
        raise ConversionGap(f"{entry.name} has no address wired", node)
    certificate = wired(node, entry, "certificate_path", ctx)
    insecure = f"grpc.insecure_channel({address}, options={_CHANNEL_OPTIONS})"
    if certificate is None or constant_text(certificate) == "":
        channel = insecure
    else:
        channel = (
            f"grpc.secure_channel({address}, grpc.ssl_channel_credentials("
            f"root_certificates=pathlib.Path({certificate}).read_bytes()), "
            f"options={_CHANNEL_OPTIONS}) if {certificate} else {insecure}"
        )
        ctx.add_import("import pathlib")
    variable = ctx.make_output_var("channel", node.id, terminal_id=out.id)
    ctx.add_import("import grpc")
    ctx.require_packages([GRPCIO])
    return CodeFragment(
        statements=[build_assign(variable, parse_expr(channel))],
        bindings={out.id: variable},
    )


def close_client(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Close Client``: closes the channel."""
    client = wired(node, entry, "grpcid_in", ctx)
    if client is None:
        raise ConversionGap(f"{entry.name} has no client wired", node)
    return CodeFragment(
        statements=[ast.Expr(value=parse_expr(f"{client}.close()"))],
    )


def metadata_registration(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """The metadata registration VIs: the id passes on."""
    fragment = CodeFragment()
    bind_passthrough(node, entry, ctx, fragment.bindings)
    return fragment


def _message(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, param: str, label: str
) -> ProtoMessage:
    name = constant_text(wired(node, entry, param, ctx))
    if name is None:
        raise ConversionGap(
            f"{entry.name}: the {label} is not a constant, so the Python class of "
            "the message is not known when the code is generated",
            node,
        )
    message = find_proto_message(name)
    if message is None:
        raise ConversionGap(
            f"{entry.name}: the message {name!r} is not in lvkit's table of "
            "protobuf messages with a Python package "
            "(data/grpc/proto_messages.json); generate Python from your .proto "
            "file with grpcio-tools",
            node,
        )
    return message


def _timeout(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> str:
    text = wired(node, entry, "timeout_ms", ctx)
    if text is None:
        return "None"
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return f"({text} / 1000 if {text} > 0 else None)"
    return repr(value / 1000) if value > 0 else "None"


def _context_guard(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> list[ast.stmt]:
    """The statement that refuses a client context: the call's ``contextId`` is
    0 when it has none, and Python's call has no context to give."""
    text = wired(node, entry, "contextid", ctx)
    if text is None:
        return []
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        value = None
    if value == 0:
        return []
    if value is not None:
        raise ConversionGap(
            f"{entry.name}: a client context (contextId {value!r}) has no Python "
            "equivalent on a grpcio call",
            node,
        )
    prefix = f"{entry.name}: a client context (contextId "
    suffix = ") has no Python equivalent on a grpcio call"
    return ast.parse(
        f"if {text}:\n"
        f"    raise NotImplementedError({prefix!r} + repr({text}) + {suffix!r})\n"
    ).body


def unary_call(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Client Unary Call``: the call of the method on the channel."""
    method = constant_text(wired(node, entry, "method_name", ctx))
    if method is None:
        raise ConversionGap(
            f"{entry.name}: the method name is not a constant, so the call cannot "
            "be written when the code is generated",
            node,
        )
    request = _message(node, ctx, entry, "request_message_name", "request message name")
    response = _message(
        node, ctx, entry, "response_message_name", "response message name"
    )
    client = wired(node, entry, "grpcid_in", ctx)
    if client is None:
        raise ConversionGap(f"{entry.name} has no client wired", node)
    statements = _context_guard(node, ctx, entry)

    request_term = terminal_of(node, entry, "request_data", TerminalDirection.INPUT)
    plain = "{}"
    if request_term is not None and ctx.is_wired(request_term.id):
        data = ctx.make_output_var("request_data", node.id)
        statements.append(
            build_assign(
                data, parse_expr(ctx.wired_input(request_term.id, node) or "None")
            )
        )
        try:
            assert request_term.lv_type is not None, "the request type is recorded"
            plain = to_plain(data, request_term.lv_type, ctx)
        except UnsupportedPlainType as error:
            raise ConversionGap(
                f"{entry.name} does not handle {error} in the request data: "
                "protobuf's JSON takes Booleans, numbers, strings and arrays or "
                "clusters of them",
                node,
            ) from error

    reply = ctx.make_output_var("response", node.id)
    call = (
        f"{client}.unary_unary({method!r}, "
        f"request_serializer={request.expression()}.SerializeToString, "
        f"response_deserializer={response.expression()}.FromString)("
        f"json_format.ParseDict({plain}, {request.expression()}()), "
        f"timeout={_timeout(node, ctx, entry)})"
    )
    statements.append(build_assign(reply, parse_expr(call)))
    fragment = CodeFragment(statements=statements)
    fragment.imports.update(
        {
            "import grpc",
            "from google.protobuf import json_format",
            request.import_statement(),
            response.import_statement(),
        }
    )
    ctx.require_packages(
        [GRPCIO, PROTOBUF, request.distribution, response.distribution]
    )
    bind_passthrough(node, entry, ctx, fragment.bindings)

    out = _out(node, entry, "response_data")
    if out is not None and ctx.is_wired(out.id):
        fragment.extend(_read_response(node, ctx, entry, reply, out.id))
    return fragment


def _read_response(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, reply: str, out_id: str
) -> CodeFragment:
    type_term = terminal_of(node, entry, "response_type", TerminalDirection.INPUT)
    out = next(t for t in node.terminals if t.id == out_id)
    lv_type = (type_term.lv_type if type_term else None) or out.lv_type
    if lv_type is None or type_term is None or not ctx.is_wired(type_term.id):
        raise ConversionGap(f"{entry.name} needs the Response Type wired", node)
    plain = ctx.make_output_var("response_dict", node.id)
    default = ctx.make_output_var("response_type", node.id)
    variable = ctx.make_output_var("response_data", node.id, terminal_id=out_id)
    options = PlainOptions(
        null_to_default="True",
        strict="False",
        errors=_RESPONSE_ERRORS,
        missing_to_default=True,
        numbers_as_text=True,
    )
    statements: list[ast.stmt] = [
        build_assign(
            plain,
            parse_expr(
                f"json_format.MessageToDict({reply}, "
                "preserving_proto_field_name=True, use_integers_for_enums=True)"
            ),
        ),
        build_assign(
            default, parse_expr(ctx.wired_input(type_term.id, node) or "None")
        ),
    ]
    try:
        statements.extend(
            from_plain(plain, lv_type, default, variable, ctx, node.id, options)
        )
    except UnsupportedPlainType as error:
        raise ConversionGap(
            f"{entry.name} does not handle {error} in the response type: "
            "protobuf's JSON gives Booleans, numbers, strings and arrays or "
            "clusters of them",
            node,
        ) from error
    return CodeFragment(statements=statements, bindings={out_id: variable})

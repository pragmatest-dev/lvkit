"""The server side of the LabVIEW gRPC library (``Create Server``, ``Start Server``,
``Stop Server``, ``Create And Register Server Event``, ``Read Call Request``,
``Write Call Response``, ``Complete Call``, ``Set Call Status`` and ``Is Call
Cancelled``), written on the package's ``grpc_server`` module
(``grpc_server_module``), which holds the server's rules with the library's C++
each follows.

The server's gRPC id is the module's ``Server`` and a call's is its ``Call``. A
call arrives as the registered user event, whose data is a cluster that holds the
call; a VI's Event Structure handles it and reads, writes and completes by that
call.

- ``Create Server`` is ``Server()`` (``LVCreateServer``, ``grpc_interop.cc:145``).
- ``Start Server`` is ``server.start(address, certificate, key)`` and returns the
  listening port (``LVStartServer`` and ``LVGetServerListeningPort``,
  ``grpc_interop.cc:161-190``); the two files are the path's text, as the VI's
  Path To String gives the DLL. The case on the result in the VI's own diagram
  makes -1 the error -8001.
- ``Stop Server`` is ``server.stop()`` (``LVStopServer``,
  ``grpc_interop.cc:194-210``).
- ``Create And Register Server Event`` makes the user event (an empty list of
  queues, as Create User Event does) and registers it with the method name, the
  wrap that makes the event's data from the call, and the Python classes of the
  request and response messages (``RegisterServerEvent``,
  ``grpc_interop.cc:328-342``). The message names are constants of the call, so
  the classes are known when the code is generated, as for the client
  (``grpc_client.message_class``); a name the table of protobuf messages does not
  list is a gap. The wrap is the type of the wired event data, built with the call
  in its first field, the gRPC id (``gRPCId Cluster``), and the type's defaults in
  the rest. The generic method event (``Create And Register Generic Method Server
  Event``, ``RegisterGenericMethodServerEvent``) is not converted: its catalog
  entry does not exist, so a call to it is a gap.
- ``Read Call Request`` is ``call.read()``, the next request message, read into the
  cluster of ``Request Type`` as the client reads a response (``GetRequestData``,
  ``grpc_interop.cc:380-397``); ``Write Call Response`` is ``call.write(message)``
  with the response cluster read into the call's response class as the client
  reads a request (``SetResponseData``, ``grpc_interop.cc:401-418``).
- ``Complete Call`` is ``call.complete()`` (``CloseServerEvent``,
  ``grpc_interop.cc:422-438``), ``Set Call Status`` is ``call.set_status(code,
  details)`` (``SetCallStatus``, ``grpc_interop.cc:450-463``) and ``Is Call
  Cancelled`` is ``call.is_cancelled()`` (``IsCancelled``,
  ``grpc_interop.cc:467-479``).

Every VI here runs whatever error comes in on its error in: the library's diagrams
call the DLL and merge the error that comes in with the call's own.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import LVType, LVTypeKind, TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import build_assign, cluster_default_expr, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..output_binding import bind_output
from ..type_default import type_default
from .grpc_client import (
    GRPCIO,
    PROTOBUF,
    message_class,
    message_from_input,
    read_response,
)
from .grpc_pane import bind_passthrough, output_of, terminal_of, wired

_SERVER_NAMES = "Server"
_JSON_FORMAT = "from google.protobuf import json_format"


def _server(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> str:
    server = wired(node, entry, "grpcid_in", ctx)
    if server is None:
        raise ConversionGap("no server is wired", node)
    return server


def _call(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> str:
    call = wired(node, entry, "grpcid_in", ctx)
    if call is None:
        raise ConversionGap("no call ID is wired", node)
    return call


def _path_text(node: VINode, ctx: CodeGenContext, entry: VIEntry, param: str) -> str:
    """The text of a path input: what Path To String gives, empty when unwired."""
    path = wired(node, entry, param, ctx)
    return "''" if path is None else f"str({path})"


def create_server(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Create Server``: a new server."""
    out = output_of(node, entry, "grpcid_out")
    if out is None or not ctx.is_wired(out.id):
        return CodeFragment.empty()
    variable = ctx.make_output_var("server", node.id, terminal_id=out.id)
    ctx.import_grpc_server(_SERVER_NAMES)
    ctx.require_packages([GRPCIO])
    return CodeFragment(
        statements=[build_assign(variable, parse_expr("Server()"))],
        bindings={out.id: variable},
    )


def start_server(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Start Server``: the server listening; ``Listening Port`` is the port."""
    server = _server(node, ctx, entry)
    address = wired(node, entry, "server_address", ctx) or "''"
    certificate = _path_text(node, ctx, entry, "server_certificate_file")
    key = _path_text(node, ctx, entry, "server_key_file")
    port = ctx.make_output_var("port", node.id)
    fragment = CodeFragment(
        statements=[
            build_assign(
                port, parse_expr(f"{server}.start({address}, {certificate}, {key})")
            )
        ]
    )
    ctx.require_packages([GRPCIO])
    bind_passthrough(node, entry, ctx, fragment.bindings)
    out = output_of(node, entry, "listening_port")
    if out is not None and ctx.is_wired(out.id):
        fragment.extend(
            bind_output(node, out.id, "listening_port", parse_expr(port), ctx)
        )
    return fragment


def stop_server(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Stop Server``: stops the server and cancels its calls."""
    server = _server(node, ctx, entry)
    return CodeFragment(statements=[ast.Expr(value=parse_expr(f"{server}.stop()"))])


def _wrap(
    node: VINode, ctx: CodeGenContext, lv_type: LVType | None, call: str
) -> ast.expr:
    """The data of the user event for the call ``call``: the cluster ``lv_type``
    with the call in its first field."""
    if lv_type is None or lv_type.kind not in (
        LVTypeKind.CLUSTER,
        LVTypeKind.TYPEDEF_REF,
    ):
        raise ConversionGap(
            "the data of the user event is not a cluster that holds the gRPC id",
            node,
        )
    first = [True]

    def field(field_type: LVType | None) -> ast.expr:
        if first[0]:
            first[0] = False
            return ast.Name(id=call, ctx=ast.Load())
        return type_default(ctx, field_type)

    data = cluster_default_expr(lv_type, field)
    if isinstance(data, ast.Constant):
        raise ConversionGap(
            "the data of the user event is not a cluster that holds the gRPC id",
            node,
        )
    return data


def register_server_event(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """``Create And Register Server Event``: the user event, registered."""
    server = _server(node, ctx, entry)
    method = wired(node, entry, "method_name", ctx)
    if method is None:
        raise ConversionGap("no method name is wired", node)
    request = message_class(
        node, ctx, entry, "request_metadata_name", "request metadata name"
    )
    response = message_class(
        node, ctx, entry, "response_metadata_name", "response metadata name"
    )
    event_in = terminal_of(node, entry, "event_in", TerminalDirection.INPUT)
    wrap = _wrap(node, ctx, event_in.lv_type if event_in else None, "call")
    out = output_of(node, entry, "event")
    wired_out = out is not None and ctx.is_wired(out.id)
    event = ctx.make_output_var(
        "user_event", node.id, terminal_id=out.id if wired_out and out else None
    )
    registration = (
        f"{server}.register({method}, {event}, lambda call: {ast.unparse(wrap)}, "
        f"{request.expression()}, {response.expression()})"
    )
    fragment = CodeFragment(
        statements=[
            build_assign(event, ast.List(elts=[], ctx=ast.Load())),
            ast.Expr(value=parse_expr(registration)),
        ]
    )
    fragment.imports.update({request.import_statement(), response.import_statement()})
    ctx.require_packages(
        [GRPCIO, PROTOBUF, request.distribution, response.distribution]
    )
    bind_passthrough(node, entry, ctx, fragment.bindings)
    if wired_out and out is not None:
        fragment.bindings[out.id] = event
    return fragment


def read_call_request(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """``Read Call Request``: the next request message, as ``Request Type``."""
    call = _call(node, ctx, entry)
    message = ctx.make_output_var("message", node.id)
    fragment = CodeFragment(
        statements=[build_assign(message, parse_expr(f"{call}.read()"))]
    )
    fragment.imports.add(_JSON_FORMAT)
    ctx.require_packages([GRPCIO, PROTOBUF])
    bind_passthrough(node, entry, ctx, fragment.bindings)
    out = output_of(node, entry, "request_data")
    if out is not None and ctx.is_wired(out.id):
        fragment.extend(
            read_response(
                node,
                ctx,
                entry,
                message,
                out.id,
                type_param="request_type",
                type_label="Request Type",
            )
        )
    return fragment


def write_call_response(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """``Write Call Response``: one response message, read from ``Response Data``
    into the response class the method was registered with."""
    call = _call(node, ctx, entry)
    statements: list[ast.stmt] = []
    message = message_from_input(
        node, ctx, entry, f"{call}.response_class", statements, "response_data"
    )
    statements.append(ast.Expr(value=parse_expr(f"{call}.write({message})")))
    fragment = CodeFragment(statements=statements)
    fragment.imports.add(_JSON_FORMAT)
    ctx.require_packages([GRPCIO, PROTOBUF])
    bind_passthrough(node, entry, ctx, fragment.bindings)
    return fragment


def complete_call(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Complete Call``: ends the call with its status."""
    call = _call(node, ctx, entry)
    return CodeFragment(statements=[ast.Expr(value=parse_expr(f"{call}.complete()"))])


def set_call_status(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Set Call Status``: the status the call ends with."""
    call = _call(node, ctx, entry)
    code = wired(node, entry, "status_code", ctx) or "0"
    details = wired(node, entry, "details", ctx) or "''"
    fragment = CodeFragment(
        statements=[
            ast.Expr(value=parse_expr(f"{call}.set_status(int({code}), {details})"))
        ]
    )
    bind_passthrough(node, entry, ctx, fragment.bindings)
    return fragment


def is_call_cancelled(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """``Is Call Cancelled``: whether the call ended before it was completed."""
    call = _call(node, ctx, entry)
    fragment = CodeFragment()
    bind_passthrough(node, entry, ctx, fragment.bindings)
    out = output_of(node, entry, "is_cancelled")
    if out is not None and ctx.is_wired(out.id):
        fragment.extend(
            bind_output(
                node, out.id, "is_cancelled", parse_expr(f"{call}.is_cancelled()"), ctx
            )
        )
    return fragment

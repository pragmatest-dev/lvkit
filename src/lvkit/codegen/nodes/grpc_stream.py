"""The streaming calls of the LabVIEW gRPC library's client (``Client Begin
Server Streaming Call``, ``Client Read From Stream``, ``Client Complete Streaming
Call``, ``Client Cancel Call``), written on the ``grpcio`` call object.

The call ID is the call: ``channel.unary_stream(...)(request)`` starts the call
and returns an iterator that is also the call (``grpc.Call``), the way
``ClientBeginServerStreamingCall`` makes a ``ServerStreamingClientCall`` and
registers it as the call ID (``grpc_client.cc``). The request message, the
deadline from ``timeout (ms)`` and the refused client context are those of the
unary call (``grpc_client``).

``Client Read From Stream`` is ``ClientBeginReadFromStream`` followed by
``ClientCompleteReadFromStream``: ``reader->Read`` returns false when the stream
has ended or failed, ``Success`` is that Boolean, the response cluster is copied
only when it is true, and no error is reported by the read (the status comes from
``Client Complete Streaming Call``). ``next(call)`` raises ``StopIteration`` at
the end and ``grpc.RpcError`` for a failed or cancelled call, both of which are
``Success`` False; a read that gets no message leaves ``Response Data`` as the
``Response Type``.

``Client Complete Streaming Call`` is ``ClientCompleteStreamingCall``:
``call->Finish()`` (``_reader->Finish()``, which waits for the end of the call)
and, when the status is not OK, the error ``-(1000 + the status code)`` with the
status's message: ``call.code()`` and ``call.details()``, which the catalog
entry's ``errors`` field writes from the ``RpcError`` the generated code raises.
With ``Cancel All Reads (F)`` True the VI calls ``ClientCancelCall`` instead (its
diagram's True frame). ``Client Cancel Call`` is ``ClientCancelCall``:
``call->Cancel()`` (``ClientContext::TryCancel``), whose status is the call's
default one, so it reports no error. ``errorDetails`` is never filled by the
library (the ``errorDetailsCluster`` branch of each function is empty), so it is
the cluster's default.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..type_default import type_default
from .grpc_client import (
    GRPCIO,
    PROTOBUF,
    context_guard,
    deadline,
    message_class,
    read_response,
    request_message,
)
from .grpc_pane import bind_passthrough, constant_text, output_of, wired


def _call(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> str:
    call = wired(node, entry, "grpcid_in", ctx)
    if call is None:
        raise ConversionGap("no call ID is wired", node)
    return call


def _error_details(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, fragment: CodeFragment
) -> None:
    """Bind ``errorDetails`` to its cluster's default."""
    out = output_of(node, entry, "error_details")
    if out is None or not ctx.is_wired(out.id):
        return
    variable = ctx.make_output_var("error_details", node.id, terminal_id=out.id)
    fragment.statements.append(build_assign(variable, type_default(ctx, out.lv_type)))
    fragment.bindings[out.id] = variable


def begin_server_streaming_call(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """``Client Begin Server Streaming Call``: the call of the method on the
    channel, started."""
    method = constant_text(wired(node, entry, "method_name", ctx))
    if method is None:
        raise ConversionGap(
            "the method name is not a constant, so the call cannot "
            "be written when the code is generated",
            node,
        )
    request = message_class(
        node, ctx, entry, "request_message_name", "request message name"
    )
    response = message_class(
        node, ctx, entry, "response_message_name", "response message name"
    )
    client = wired(node, entry, "grpcid_in", ctx)
    if client is None:
        raise ConversionGap("no client is wired", node)
    statements = context_guard(node, ctx, entry)
    message = request_message(node, ctx, entry, request, statements)
    out = output_of(node, entry, "call_id_out")
    wired_out = out is not None and ctx.is_wired(out.id)
    variable = ctx.make_output_var(
        "call", node.id, terminal_id=out.id if wired_out and out else None
    )
    call = (
        f"{client}.unary_stream({method!r}, "
        f"request_serializer={request.expression()}.SerializeToString, "
        f"response_deserializer={response.expression()}.FromString)("
        f"{message}, timeout={deadline(node, ctx, entry)})"
    )
    statements.append(build_assign(variable, parse_expr(call)))
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
    if wired_out and out is not None:
        fragment.bindings[out.id] = variable
    return fragment


def read_from_stream(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Client Read From Stream``: the next message of the call."""
    call = _call(node, ctx, entry)
    message = ctx.make_output_var("message", node.id)
    fragment = CodeFragment(
        statements=ast.parse(
            "try:\n"
            f"    {message} = next({call})\n"
            "except (StopIteration, grpc.RpcError):\n"
            f"    {message} = None\n"
        ).body
    )
    fragment.imports.update({"import grpc", "from google.protobuf import json_format"})
    ctx.require_packages([GRPCIO, PROTOBUF])
    bind_passthrough(node, entry, ctx, fragment.bindings)
    success = output_of(node, entry, "success")
    if success is not None and ctx.is_wired(success.id):
        variable = ctx.make_output_var("success", node.id, terminal_id=success.id)
        fragment.statements.append(
            build_assign(variable, parse_expr(f"{message} is not None"))
        )
        fragment.bindings[success.id] = variable
    out = output_of(node, entry, "response_data")
    if out is not None and ctx.is_wired(out.id):
        fragment.extend(read_response(node, ctx, entry, message, out.id, optional=True))
    return fragment


def complete_streaming_call(
    node: VINode, ctx: CodeGenContext, entry: VIEntry
) -> CodeFragment:
    """``Client Complete Streaming Call``: the call's status, or its cancel."""
    call = _call(node, ctx, entry)
    cancel = wired(node, entry, "cancel_all_reads", ctx)
    check = f"if {call}.code() != grpc.StatusCode.OK:\n    raise {call}\n"
    if cancel is None or cancel == "False":
        source = check
    elif cancel == "True":
        source = f"{call}.cancel()\n"
    else:
        source = f"if {cancel}:\n    {call}.cancel()\nel{check}"
    fragment = CodeFragment()
    _error_details(node, ctx, entry, fragment)
    fragment.statements.extend(ast.parse(source).body)
    fragment.imports.add("import grpc")
    ctx.require_packages([GRPCIO])
    return fragment


def cancel_call(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """``Client Cancel Call``: the call's cancel."""
    call = _call(node, ctx, entry)
    fragment = CodeFragment(statements=[ast.Expr(value=parse_expr(f"{call}.cancel()"))])
    _error_details(node, ctx, entry, fragment)
    bind_passthrough(node, entry, ctx, fragment.bindings)
    ctx.require_packages([GRPCIO])
    return fragment

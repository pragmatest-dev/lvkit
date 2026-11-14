"""TCP Open Connection (1715), Write (1714), Read (1716) and Close (1713).

A connection is a ``socket.socket``. NI ("TCP Open Connection Function",
"TCP Write Function", "TCP Read Function", labview-api-ref ``functions/``):

* Open: ``address`` is "in IP dot notation or it can be a hostname"; "If you do not
  specify an address, LabVIEW establishes a connection to the local computer"
  (``localhost``). ``timeout ms`` defaults to 60000, -1 waits indefinitely;
  ``local port`` defaults to 0, the operating system's choice. "If you wire a value
  of 0 [to the remote port], LabVIEW returns an error": error 54, whose NI text
  names that case. A service name in place of a port number is looked up by NI's
  Service Locator, which Python has no equivalent of: a gap.
* Close: closes the socket; "abort is reserved for future use".
* Write and Read: ``timeout ms`` defaults to 25000, -1 waits indefinitely; the
  bytes written or read so far are returned with the error. ``codegen/
  tcp_functions.py`` has the two functions written into the generated module.
  Read's CRLF mode (2) has no NI text for what a timeout returns: a gap.

The failures are the node's error out through the catalog entry's ``errors``
(``native_failure``).

Terminals (from the primitives entries): Open: in 6 = local port, 9 = timeout ms,
10 = remote port, 11 = address; out 3 = connection ID. Write: in 9 = timeout ms,
10 = data in, 11 = connection ID; out 2 = bytes written, 3 = connection ID out.
Close: in 11 = connection ID; out 3 = connection ID out.
Read: in 7 = mode, 9 = timeout ms, 10 = bytes to read, 11 = connection ID; out 2 =
data out, 3 = connection ID out.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import is_string_type_name

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...module_function import function_source
from ...tcp_functions import tcp_read, tcp_write
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, literal, string_variable

TCP_READ_CRLF_MODE = 2


def _input(ctx: CodeGenContext, node: PrimitiveNode, index: int, default: str) -> str:
    """The expression feeding input ``index``, else ``default``."""
    return input_expression(ctx, node, index) or default


def _connection(
    ctx: CodeGenContext, node: PrimitiveNode, statements: list[ast.stmt]
) -> str:
    text = input_expression(ctx, node, 11)
    if text is None:
        raise ConversionGap(f"{node.name} has no connection ID wired", node)
    return string_variable(ctx, node, text, statements, "connection")


def _declare(ctx: CodeGenContext, function: object) -> str:
    name = function.__name__  # type: ignore[attr-defined]
    ctx.add_import("import socket")
    ctx.add_import("import time")
    return ctx.module_declarations.declare(
        ("helper", name),
        name,
        lambda declared: function_source(function, declared),  # type: ignore[arg-type]
    )


def _out_name(
    ctx: CodeGenContext, node: PrimitiveNode, index: int, base: str
) -> tuple[str | None, str | None]:
    """The terminal id of output ``index`` and the variable it is bound to, when
    something reads it."""
    term = terminal(node, "output", index)
    if term is None or not ctx.is_wired(term.id):
        return None, None
    return term.id, ctx.make_output_var(base, node.id, terminal_id=term.id)


def _pass_connection(
    ctx: CodeGenContext, node: PrimitiveNode, connection: str
) -> dict[str, str]:
    out = terminal(node, "output", 3)
    if out is not None and ctx.is_wired(out.id):
        return {out.id: connection}
    return {}


@register_op_fragment("TCP_OPEN")
def tcp_open(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    port = terminal(node, "input", 10)
    if port is not None and port.lv_type is not None:
        if is_string_type_name(port.lv_type.underlying_type):
            raise ConversionGap(
                "TCP Open Connection with a service name: NI's Service Locator has "
                "no Python equivalent",
                node,
            )
    statements: list[ast.stmt] = []
    address = input_expression(ctx, node, 11)
    port_text = input_expression(ctx, node, 10)
    if port_text is None:
        raise ConversionGap("TCP Open Connection has no remote port wired", node)
    port_name = string_variable(ctx, node, port_text, statements, "port")
    timeout = _input(ctx, node, 9, "60000")
    local_port = _input(ctx, node, 6, "0")
    seconds = _seconds(timeout)
    statements.extend(
        ast.parse(
            f"if not {port_name}:\n"
            "    raise socket.gaierror('the remote port or service name is 0')\n"
        ).body
    )
    ctx.add_import("import socket")
    connection_id, variable = _out_name(ctx, node, 3, "connection")
    variable = variable or ctx.make_output_var("connection", node.id)
    where = f"({address} or 'localhost')" if address else "'localhost'"
    statements.append(
        build_assign(
            variable,
            parse_expr(
                f"socket.create_connection(({where}, {port_name}), {seconds}, "
                f"source_address=('', {local_port}))"
            ),
        )
    )
    bindings = {connection_id: variable} if connection_id else {}
    return CodeFragment(statements=statements, bindings=bindings)


def _seconds(milliseconds: str) -> str:
    """The ``socket`` timeout, in seconds, of a ``timeout ms`` expression: -1 (or
    less) is no timeout."""
    value = literal(milliseconds)
    if isinstance(value, int) and not isinstance(value, bool):
        return "None" if value < 0 else repr(value / 1000)
    return f"({milliseconds} / 1000 if {milliseconds} >= 0 else None)"


@register_op_fragment("TCP_WRITE")
def tcp_write_node(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    statements: list[ast.stmt] = []
    connection = _connection(ctx, node, statements)
    data = input_expression(ctx, node, 10)
    if data is None:
        raise ConversionGap("TCP Write has no data in wired", node)
    helper = _declare(ctx, tcp_write)
    payload = ctx.make_output_var("payload", node.id)
    sent = ctx.make_output_var("sent", node.id)
    statements.append(build_assign(payload, parse_expr(f"{data}.encode('latin-1')")))
    statements.append(build_assign(sent, parse_expr("bytearray()")))
    written_id, written = _out_name(ctx, node, 2, "bytes_written")
    written = written or ctx.make_output_var("bytes_written", node.id)
    timeout = _input(ctx, node, 9, "25000")
    statements.extend(
        ast.parse(
            f"try:\n    {helper}({connection}, {payload}, {timeout}, {sent})\n"
            f"finally:\n    {written} = len({sent})\n"
        ).body
    )
    bindings = _pass_connection(ctx, node, connection)
    if written_id:
        bindings[written_id] = written
    return CodeFragment(statements=statements, bindings=bindings)


@register_op_fragment("TCP_READ")
def tcp_read_node(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    mode = _input(ctx, node, 7, "0")
    mode_value = literal(mode)
    if mode_value == TCP_READ_CRLF_MODE or mode_value is None:
        raise ConversionGap(
            "TCP Read in CRLF mode, or in a mode set at run time: NI does not say "
            "what a CRLF read that times out returns",
            node,
        )
    statements: list[ast.stmt] = []
    connection = _connection(ctx, node, statements)
    count = input_expression(ctx, node, 10)
    if count is None:
        raise ConversionGap("TCP Read has no bytes to read wired", node)
    helper = _declare(ctx, tcp_read)
    received = ctx.make_output_var("received", node.id)
    statements.append(build_assign(received, parse_expr("bytearray()")))
    data_id, data = _out_name(ctx, node, 2, "data_out")
    data = data or ctx.make_output_var("data_out", node.id)
    timeout = _input(ctx, node, 9, "25000")
    statements.extend(
        ast.parse(
            f"try:\n    {helper}({connection}, {count}, {mode}, {timeout}, "
            f"{received})\n"
            f"finally:\n    {data} = {received}.decode('latin-1')\n"
        ).body
    )
    bindings = _pass_connection(ctx, node, connection)
    if data_id:
        bindings[data_id] = data
    return CodeFragment(statements=statements, bindings=bindings)


@register_op_fragment("TCP_CLOSE")
def tcp_close(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    """``connection.close()``. ``abort`` is "reserved for future use" (NI)."""
    statements: list[ast.stmt] = []
    connection = _connection(ctx, node, statements)
    statements.append(ast.Expr(value=parse_expr(f"{connection}.close()")))
    return CodeFragment(
        statements=statements, bindings=_pass_connection(ctx, node, connection)
    )

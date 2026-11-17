"""Code generator for Scan From String (scanf) nodes.

The node's inputs are the format string (index 0), the input string (1), the
initial scan location (3), the error in (5) and a ``default`` for each output
(index 7 and every second one after); its outputs are the remaining string (2),
the offset past the scan (4), the error out (6) and the scanned values (index 8
and every second one after). The rules for the text are in
``codegen/labview_scan.py``: the format string, written on the diagram or
LabVIEW's default for the outputs' types, is turned into one ``re`` pattern while
generating, and each output is its group's text converted to the output's type.
A format string wired from a value known only at run time is translated by the
same code when the code runs: ``scan_text`` of the package's ``scan`` module
(``codegen/scan_module.py``, whose source is copied from ``labview_scan.py``).

A scan that stops before an output leaves that output at its default and its
error out the "Scan failed" error 85, whose source NI gives as
``Scan From String (arg n)`` for the first output that was not scanned.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..error_codes import describe_error_code
from ..error_flow import own_error_terminals
from ..errors_module import LABVIEW_ERROR_NAME
from ..fragment import CodeFragment
from ..labview_scan import (
    ScanPlan,
    default_format,
    field_count,
    scan_pieces,
    scan_plan,
)
from ..output_binding import bind_output
from ..scan_module import SCAN_FUNCTION_NAME
from ..type_default import type_default
from .ops._numeric import FormatKind, format_kind
from .printf import literal_text

_FORMAT_INDEX = 0
_INPUT_INDEX = 1
_REMAINING_INDEX = 2
_INITIAL_INDEX = 3
_OFFSET_INDEX = 4
_FIRST_DEFAULT_INDEX = 7
_FIRST_OUTPUT_INDEX = 8
# NI, General LabVIEW Error Codes: 85 "Scan failed. The input string does not
# contain data in the expected format."
_SCAN_FAILED = 85


def _input(node: PrimitiveNode, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == index),
        None,
    )


def _scanned_outputs(node: PrimitiveNode) -> list[Terminal]:
    """The terminals that take the scanned values, in order."""
    return sorted(
        (
            t
            for t in node.terminals
            if t.direction == "output"
            and t.index >= _FIRST_OUTPUT_INDEX
            and (t.index - _FIRST_OUTPUT_INDEX) % 2 == 0
        ),
        key=lambda t: t.index,
    )


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a scanf (Scan From String) node."""
    scanned = _scanned_outputs(node)
    if not scanned:
        return CodeFragment.empty()

    def wired(index: int) -> str | None:
        terminal = _input(node, index)
        if terminal is None or not ctx.is_wired(terminal.id):
            return None
        return ctx.wired_input(terminal.id, node) or "None"

    kinds = [format_kind(t.lv_type) for t in scanned]
    format_value = wired(_FORMAT_INDEX)
    format_text = _format_text(node, format_value, kinds)
    plan: ScanPlan | None = None
    if format_text is not None:
        pieces = scan_pieces(format_text)
        if field_count(pieces) != len(scanned):
            raise ConversionGap(
                f"The format string of Scan From String has {field_count(pieces)} "
                f"specifiers and the node has {len(scanned)} outputs",
                node,
            )
        try:
            plan = scan_plan(pieces, kinds)
        except ValueError as reason:
            raise ConversionGap(str(reason), node) from None
        ctx.add_import("import re")
        for statement_import in plan.imports:
            ctx.add_import(statement_import)
    else:
        ctx.import_scan()
    fragment = CodeFragment()
    text = parse_expr(wired(_INPUT_INDEX) or "''")
    if not isinstance(text, ast.Name | ast.Constant):
        name = ctx.make_output_var("scan_input", node.id)
        fragment.statements.append(build_assign(name, text))
        text = ast.Name(id=name, ctx=ast.Load())
    initial = wired(_INITIAL_INDEX)
    scan = ctx.make_output_var("scan", node.id)
    start = (
        ast.Subscript(
            value=text,
            slice=ast.Slice(lower=parse_expr(initial)),
            ctx=ast.Load(),
        )
        if initial is not None
        else text
    )
    if plan is not None:
        scan_call = ast.Call(
            func=parse_expr("re.match"),
            args=[ast.Constant(value=plan.pattern), start],
            keywords=[],
        )
    else:
        scan_call = ast.Call(
            func=parse_expr(SCAN_FUNCTION_NAME),
            args=[
                parse_expr(format_value or "''"),
                start,
                parse_expr(repr(tuple(tuple(kind) for kind in kinds))),
            ],
            keywords=[],
        )
    fragment.statements.append(build_assign(scan, scan_call))
    end = f"{scan}.end()" if initial is None else f"{initial} + {scan}.end()"
    _bind_positions(node, ctx, fragment, ast.unparse(text), end)
    for position, terminal in enumerate(scanned):
        if not ctx.is_wired(terminal.id):
            continue
        group = f"{scan}[{position + 1}]"
        default = wired(_FIRST_DEFAULT_INDEX + 2 * position)
        default_expr = (
            parse_expr(default)
            if default is not None
            else type_default(ctx, terminal.lv_type)
        )
        if plan is None:
            value: ast.expr = parse_expr(
                f"{scan}.value({position}, {ast.unparse(default_expr)})"
            )
        else:
            value = ast.IfExp(
                test=parse_expr(f"{group} is not None"),
                body=parse_expr(
                    plan.values[position].format(g=group, d=ast.unparse(default_expr))
                ),
                orelse=default_expr,
            )
        fragment.extend(
            bind_output(node, terminal.id, terminal.name or "scanned", value, ctx)
        )
    _report_failure(node, ctx, fragment, scan, len(scanned))
    return fragment


def _format_text(
    node: PrimitiveNode, format_value: str | None, kinds: list[FormatKind]
) -> str | None:
    """The format string: the one written on the diagram, else LabVIEW's default
    for the outputs' types; None for one wired from a value known only when the
    code runs."""
    if format_value is None:
        try:
            return default_format(kinds)
        except ValueError as reason:
            raise ConversionGap(str(reason), node) from None
    return literal_text(format_value)


def _bind_positions(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    fragment: CodeFragment,
    text: str,
    end: str,
) -> None:
    """The offset past the scan and the remaining string, when wired."""
    for index, expr, name in (
        (_OFFSET_INDEX, end, "offset_past_scan"),
        (_REMAINING_INDEX, f"{text}[{end}:]", "remaining_string"),
    ):
        terminal = next(
            (t for t in node.terminals if t.direction == "output" and t.index == index),
            None,
        )
        if terminal is not None and ctx.is_wired(terminal.id):
            fragment.extend(
                bind_output(
                    node, terminal.id, terminal.name or name, parse_expr(expr), ctx
                )
            )


def _report_failure(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    fragment: CodeFragment,
    scan: str,
    count: int,
) -> None:
    """The node's own error when the scan stopped before an output, when its
    error out is wired."""
    _, error_outs = own_error_terminals(node, ctx)
    if not any(ctx.is_wired(term.id) for term in error_outs):
        return
    ctx.import_labview_error()
    reached = f"({scan}.lastindex or 0)"
    description = describe_error_code(_SCAN_FAILED) or ""
    error = ctx.make_output_var("own_error", node.id)
    fragment.statements.append(
        build_assign(
            error,
            parse_expr(
                f"{LABVIEW_ERROR_NAME}(code={_SCAN_FAILED}, source="
                f'f"Scan From String (arg {{{reached} + 1}})", '
                f"message={description!r}) if {reached} < {count} else None"
            ),
        )
    )
    fragment.own_error = error

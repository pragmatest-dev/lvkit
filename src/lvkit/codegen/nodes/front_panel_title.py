"""Front Panel Window:Title (FP.Title), the property of the VI class that is the
string in the title bar, on the VI's own front panel window.

The Property Node's reference is unwired (the default VI object, the VI holding
the node) or wired from "This VI" (``InMemoryVIGraph.acts_on_own_window_title``).
A write is ``live_io.set_window_title(title)``, the way FP.Open is
``live_io.open_panel`` (``front_panel_window.py``), and the function keeps the
title in a local variable (``entry_title``) for its later reads. A read gives the
title last written in this call of the function. Before any write LabVIEW gives
the VI's saved window title (NI: the property is similar to the Window title
option of the Window Appearance page of the VI Properties dialog box, whose
"Same as VI Name" check box makes it the VI's name); the VI file's parsed blocks
record neither that check box nor a custom title string, so a VI that reads the
title and never writes it is a gap, and a VI that writes it somewhere reads
``None`` before its first write, which is refused when the code runs.

The Title of another VI's reference stays a gap (``property_node``)."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals

from ..ast_utils import LIVE_IO, parse_stmt
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from . import ctrl_val

_TITLE = "window_title"


def entry_title(ctx: CodeGenContext) -> list[ast.stmt]:
    """``window_title = None`` at entry of a VI that writes the Title of its own
    window anywhere: the variable every write sets and every read reads."""
    if ctx.graph is None or ctx.vi_name is None:
        return []
    if not ctx.graph.writes_a_window_title(ctx.vi_name):
        return []
    ctx.window_title_var = ctx.claim_name(_TITLE)
    return [parse_stmt(f"{ctx.window_title_var} = None")]


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The writes and reads of the rows, in order."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    for _prop, term in correlate_property_terminals(
        node.properties or [], node.terminals, node.property_value_terminal_ids
    ):
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        if term.direction == "input":
            if not ctx.is_wired(term.id):
                raise ConversionGap(
                    "Front Panel Window:Title: the value written is not wired", node
                )
            value = ctx.wired_input(term.id, node)
            if value is None or ctx.window_title_var is None:
                raise CodeGenError("Property write value is unresolved", node)
            statements.append(parse_stmt(f"{ctx.window_title_var} = {value}"))
            statements.append(
                parse_stmt(f"{LIVE_IO}.set_window_title({ctx.window_title_var})")
            )
        elif term.direction == "output":
            if ctx.window_title_var is None:
                raise ConversionGap(
                    "Front Panel Window:Title is read and the VI never writes it: "
                    "the title it holds then is the VI's saved window title, which "
                    "the VI file does not record",
                    node,
                )
            var = ctx.make_output_var(_TITLE, term.id, term.id)
            statements.append(
                parse_stmt(
                    f"if {ctx.window_title_var} is None:\n"
                    "    raise NotImplementedError(\n"
                    '        f"Front Panel Window:Title is read before any write and '
                    "the VI file does not record the VI's saved window title "
                    f'(got {{{ctx.window_title_var}!r}})"\n'
                    "    )"
                )
            )
            statements.append(parse_stmt(f"{var} = {ctx.window_title_var}"))
            bindings[term.id] = var
        else:
            raise CodeGenError("Property value direction is unresolved", node)
    for term in node.terminals:
        if term.id in node.property_value_terminal_ids or term.is_error_cluster:
            continue
        if term.direction == "output" and ctx.is_wired(term.id):
            reference_out = ctrl_val.terminal_at(node, "output", ctrl_val.REFERENCE_OUT)
            if reference_out is not term:
                raise ConversionGap("Property flow-through output is unresolved", node)
            reference_in = ctrl_val.terminal_at(node, "input", ctrl_val.REFERENCE_IN)
            wired = (
                ctx.wired_input(reference_in.id, node)
                if reference_in is not None and ctx.is_wired(reference_in.id)
                else None
            )
            bindings[term.id] = wired or ctx.this_vi_expression()
    return CodeFragment(statements=statements, bindings=bindings)

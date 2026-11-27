"""The Property Node rows on the VI's own front panel window: Front Panel
Window:Title (``front_panel_title``), Window Bounds (``front_panel_bounds``) and
the rows of the window the panel holds (``window_rows``: State, Open, Is
Frontmost, Panel Bounds, Origin, Monitor, Behavior, Custom Title, Edit Mode On
Open).

The node's reference is unwired (the default VI object) or wired from "This VI"
(``InMemoryVIGraph.acts_on_own_window_properties``). Title and Window Bounds read
or write the attribute of the VI's own function (``function.window_title``,
``function.window_bounds``), and a write also tells ``live_io``, the way FP.Open is
``live_io.open_panel`` (``front_panel_window.py``): ``set_window_title(title)`` and
``set_window_bounds(bounds)``. The rows of the window the panel holds read and
write ``panel_of(function)``, and a write also tells ``live_io`` what the window
must do (``window_rows``). A function called without a ``live_io`` that shows
windows does nothing more and goes on. The same rows on another VI's reference only
read and set that function's attribute or panel (``vi_reference.vi_property_read``,
``property_node``): the window of that VI belongs to its own run, whose ``live_io``
the caller does not hold. A row of the panel's window fails with the panel's own
error (a State written on a window that is not open): the node's error out."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals

from ..ast_utils import LIVE_IO, parse_stmt
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from . import ctrl_val, front_panel_bounds, front_panel_title, window_rows
from .ctrl_val import report_panel_error

_ROW_NAMES = {True: "Window Bounds", False: "Title"}


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The writes and reads of the rows on the VI's own window, in order."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    this_vi = ctx.this_vi_expression()
    panel_rows = False
    for prop, term in correlate_property_terminals(
        node.properties or [], node.terminals, node.property_value_terminal_ids
    ):
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        row = window_rows.window_row(prop)
        if row is not None:
            panel_rows = True
            target = window_rows.Target(this_vi, True, term.lv_type, node, ctx)
            if term.direction == "input":
                if not ctx.is_wired(term.id):
                    raise ConversionGap(
                        f"Front Panel Window:{row.name}: the value written is "
                        "not wired",
                        node,
                    )
                value = ctx.wired_input(term.id, node)
                if value is None:
                    raise CodeGenError("Property write value is unresolved", node)
                statements.extend(window_rows.write(row, target, value))
            elif term.direction == "output":
                var = ctx.make_output_var(
                    row.name.lower().replace(" ", "_"), term.id, term.id
                )
                statements.append(
                    ast.Assign(
                        targets=[ast.Name(id=var, ctx=ast.Store())],
                        value=window_rows.read(row, target),
                    )
                )
                bindings[term.id] = var
            else:
                raise CodeGenError("Property value direction is unresolved", node)
            continue
        bounds = front_panel_bounds.is_bounds_row(prop)
        attribute = (
            front_panel_bounds.BOUNDS_ATTRIBUTE
            if bounds
            else front_panel_title.TITLE_ATTRIBUTE
        )
        if term.direction == "input":
            if not ctx.is_wired(term.id):
                raise ConversionGap(
                    f"Front Panel Window:{_ROW_NAMES[bounds]}: the value written is "
                    "not wired",
                    node,
                )
            value = ctx.wired_input(term.id, node)
            if value is None:
                raise CodeGenError("Property write value is unresolved", node)
            if bounds:
                statements.extend(
                    front_panel_bounds.write(this_vi, value, term.lv_type, node)
                )
                call = "set_window_bounds"
            else:
                statements.append(front_panel_title.write(this_vi, value))
                call = "set_window_title"
            statements.append(parse_stmt(f"{LIVE_IO}.{call}({this_vi}.{attribute})"))
        elif term.direction == "output":
            var = ctx.make_output_var(attribute, term.id, term.id)
            expression = (
                front_panel_bounds.read(this_vi, term.lv_type, node, ctx)
                if bounds
                else front_panel_title.read(this_vi)
            )
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var, ctx=ast.Store())], value=expression
                )
            )
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
            bindings[term.id] = wired or this_vi
    if panel_rows:
        return report_panel_error(node, ctx, statements, bindings)
    return CodeFragment(statements=statements, bindings=bindings)

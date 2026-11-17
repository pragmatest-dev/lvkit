"""Front Panel Window:Title (FP.Title), the property of the VI class that is the
string in the title bar.

The title belongs to the VI, so each generated function carries it as
``function.window_title``, set right after its ``def`` beside ``function.panel``
(``window_title_statement``): the VI's custom Window title (the ``<TITL>`` block of
its file, ``WindowProps.window_title``), else the VI's name, which is what the
Window Appearance page of the VI Properties dialog box gives while "Same as VI
name" is checked (NI: the Window title option, "Uses the VI name for the title of
the VI window"; the name includes the ``.vi``, which NI's Idea Exchange post "Remove
file extension for default window title" gives as the default). A read of the
property on a VI reference is the attribute of the function the reference holds
and a write sets it (``read`` / ``write``).

On the VI's own window (the Property Node's reference unwired or wired from "This
VI": ``InMemoryVIGraph.acts_on_own_window_title``) the node does the same through
the VI's own function, and a write also calls ``live_io.set_window_title(title)``,
the way FP.Open is ``live_io.open_panel`` (``front_panel_window.py``). A write on
another VI's reference only sets its attribute: the window of that VI belongs to
its own run, whose ``live_io`` the caller does not hold."""

from __future__ import annotations

import ast
from pathlib import Path

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals
from lvkit.graph.vi_reference import VI_FP_TITLE_PROPERTY_CODE
from lvkit.models import PropertyDef

from ..ast_utils import LIVE_IO, parse_expr, parse_stmt
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from . import ctrl_val

TITLE_ATTRIBUTE = "window_title"


def is_title_row(prop: PropertyDef) -> bool:
    """Whether the property row is Front Panel Window:Title, by its code (the
    older name Front Panel.Title records the same code)."""
    return len(prop.segments) == 1 and (
        prop.segments[0].code == VI_FP_TITLE_PROPERTY_CODE
    )


def read(reference: str) -> ast.expr:
    """The title of the VI whose function is ``reference``."""
    return parse_expr(f"{reference}.{TITLE_ATTRIBUTE}")


def write(reference: str, value: str) -> ast.stmt:
    """Set the title of the VI whose function is ``reference``."""
    return parse_stmt(f"{reference}.{TITLE_ATTRIBUTE} = {value}")


def window_title_statement(ctx: CodeGenContext, key: str, target: str) -> ast.stmt:
    """``target.window_title = "<title>"`` for the VI ``key``: its custom Window
    title, else the name of the VI."""
    title = None
    name = Path(key).name
    if ctx.graph is not None:
        title = ctx.graph.get_vi_properties(key).window.window_title
        name = ctx.graph.vi_display_name(key).rsplit(":", 1)[-1]
    return ast.Assign(
        targets=[
            ast.Attribute(
                value=parse_expr(target), attr=TITLE_ATTRIBUTE, ctx=ast.Store()
            )
        ],
        value=ast.Constant(value=name if title is None else title),
    )


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The writes and reads of the rows on the VI's own window, in order."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    this_vi = ctx.this_vi_expression()
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
            if value is None:
                raise CodeGenError("Property write value is unresolved", node)
            statements.append(write(this_vi, value))
            statements.append(
                parse_stmt(f"{LIVE_IO}.set_window_title({this_vi}.{TITLE_ATTRIBUTE})")
            )
        elif term.direction == "output":
            var = ctx.make_output_var(TITLE_ATTRIBUTE, term.id, term.id)
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var, ctx=ast.Store())], value=read(this_vi)
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
    return CodeFragment(statements=statements, bindings=bindings)

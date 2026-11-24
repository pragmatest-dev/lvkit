"""The methods of a Tree control: Edit Tree Items, Navigate Tree, Open/Close and Custom
Item Symbols.

Each is a method of the control's ``TreeItems`` (``codegen/tree_items_module.py``, in
the package's ``panels.py``), the object that holds a tree's items, tags and open
state: ``as_control(reference).tree.add_item(...)``. The method is told by its code and
its parameters by ``tree_methods`` of ``control_properties.json`` (NI's page of each is
its ``doc_url``); the Invoke Node's terminals are the reference at 0, the error at 2,
the result at 5 and parameter ``k`` at input ``6 + 2k``, as the corpus's nodes record
them. A parameter that is not wired takes the method's own default, so a call names the
wired parameters only.

The node acts on the tree its reference holds; a node bound to a Tree control of its
own VI acts on that control of the VI's panel; a node with no reference (the default
Tree object) acts on a tree of its own that holds no control. An error of the tree
(an item tag that is not one, NI's error 1136) is the node's own error.

Point To Row Column converts a pixel of the control's own drawing, which exists only in
a view: it stays a gap."""

from __future__ import annotations

import ast

from lvkit.control_properties import POINT_TO_ROW_COLUMN_CODE, tree_method
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..panels_module import (
    AS_CONTROL_NAME,
    PANEL_ERROR_NAME,
    PANEL_OF_NAME,
)
from ..tree_items_module import TREE_ITEMS_NAME
from . import ctrl_val

_RESULT = 5
_FIRST_PARAMETER = 6


def is_tree_method(node: PrimitiveNode) -> bool:
    """Whether ``node`` is an Invoke Node of a method of a Tree control."""
    return (
        node.method_code == POINT_TO_ROW_COLUMN_CODE
        or tree_method(node.method_code) is not None
    )


def _tree(node: PrimitiveNode, ctx: CodeGenContext) -> tuple[str, str | None]:
    """The expression of the ``TreeItems`` the node acts on, and the expression of
    the reference to give the node's reference output (None when it has none)."""
    reference_in = ctrl_val.terminal_at(node, "input", ctrl_val.REFERENCE_IN)
    if reference_in is not None and ctx.is_wired(reference_in.id):
        reference = ctx.wired_input(reference_in.id, node)
        if reference is not None:
            ctx.import_panels(f"{AS_CONTROL_NAME}, {PANEL_ERROR_NAME}")
            return f"{AS_CONTROL_NAME}({reference}).tree", reference
    bound = ctx.graph.bound_terminal(node) if ctx.graph is not None else None
    if bound is not None:
        name = ctx.panel_names.get(bound.id)
        if name is None:
            raise ConversionGap(
                f"{node.method_name}: the control {bound.name!r} is not on the "
                "panel of the VI",
                node,
            )
        ctx.import_panels(f"{PANEL_OF_NAME}, {PANEL_ERROR_NAME}")
        panel = f"{PANEL_OF_NAME}({ctx.this_vi_expression()})"
        return f"{panel}.control_of({name!r}).tree", None
    ctx.import_panels(f"{TREE_ITEMS_NAME}, {PANEL_ERROR_NAME}")
    return f"{TREE_ITEMS_NAME}()", None


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The call of the method on the tree the node acts on."""
    if node.method_code == POINT_TO_ROW_COLUMN_CODE:
        raise ConversionGap(
            "Point To Row Column converts a pixel coordinate of the Tree control's "
            "own drawing to an item, which exists only in a view",
            node,
        )
    method = tree_method(node.method_code)
    assert method is not None
    tree, reference = _tree(node, ctx)
    arguments: list[str] = []
    for position, (name, _) in enumerate(method.params):
        terminal = ctrl_val.terminal_at(node, "input", _FIRST_PARAMETER + 2 * position)
        if terminal is not None and ctx.is_wired(terminal.id):
            value = ctx.wired_input(terminal.id, node)
            if value is not None:
                arguments.append(f"{name}={value}")
    call = f"{tree}.{method.member}({', '.join(arguments)})"
    bindings: dict[str, str] = {}
    reference_out = ctrl_val.terminal_at(node, "output", ctrl_val.REFERENCE_OUT)
    if reference_out is not None and reference is not None:
        bindings[reference_out.id] = reference
    result = ctrl_val.terminal_at(node, "output", _RESULT)
    statement: ast.stmt
    if method.returns and result is not None and ctx.is_wired(result.id):
        variable = ctx.make_output_var(method.member, node.id, result.id)
        statement = build_assign(variable, parse_expr(call))
        bindings[result.id] = variable
    else:
        statement = ast.Expr(value=parse_expr(call))
    return ctrl_val.report_panel_error(node, ctx, [statement], bindings)

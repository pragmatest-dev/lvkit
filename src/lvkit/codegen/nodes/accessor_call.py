"""Code generator for a call of a property accessor VI of a LabVIEW class.

The accessor is the getter or the setter of a property of its class
(``class_attributes``): the call reads ``obj.name`` or writes ``obj.name =
value``. Call Parent of a property's read VI is ``super().name``, of its write VI
``Parent.name.fset(self, value)`` with ``Parent`` the class the calling class
descends from. A private property is reached only from its class's own VIs."""

from __future__ import annotations

import ast

from lvkit.class_property import PropertyRole
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import Terminal

from ..ast_utils import param_default_expr, parse_expr, to_var_name
from ..class_attributes import Accessor, AccessorPane
from ..class_model import base_import, check_private_access, python_class_name
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver
from ..value_copy import owned_input


def generate(
    node: VINode, ctx: CodeGenContext, accessor: Accessor, pane: AccessorPane
) -> CodeFragment:
    """A call of a property accessor VI: ``obj.name`` read, or ``obj.name = value``
    written, where ``name`` is the accessor's property."""
    assert ctx.graph is not None
    class_key = ctx.graph.owning_class_key(accessor.key)
    assert class_key is not None
    check_private_access(ctx.graph, accessor.python_name, class_key, ctx.vi_name)
    terminals = ctx.enriched_terminals(node)

    def terminal(direction: str, index: int | None) -> Terminal | None:
        if index is None:
            return None
        return next(
            (t for t in terminals if t.direction == direction and t.index == index),
            None,
        )

    prelude: list[ast.stmt] = []
    receiver_term = terminal("input", pane.class_in)
    is_setter = accessor.role is PropertyRole.SETTER
    receiver = (
        owned_input(ctx, receiver_term, node, prelude)
        if receiver_term is not None and is_setter
        else ctx.wired_input(receiver_term.id, node)
        if receiver_term is not None
        else None
    )
    if receiver is None:
        raise ConversionGap(
            f"The class wire of the call of property '{accessor.property_name}' "
            "is not wired",
            node,
        )
    call_parent = node.node_type == "callParentDynIUse"
    target = ast.Attribute(
        value=_super(node, ctx, receiver) if call_parent else parse_expr(receiver),
        attr=accessor.python_name,
        ctx=ast.Load(),
    )
    bindings: dict[str, str] = {}
    class_out = terminal("output", pane.class_out)
    if class_out is not None:
        bindings[class_out.id] = receiver

    if is_setter:
        value_term = terminal("input", pane.value_in)
        value = ctx.wired_input(value_term.id, node) if value_term else None
        value_expr = (
            parse_expr(value)
            if value is not None
            else _accessor_default(ctx, node, pane)
        )
        if call_parent:
            stmt: ast.stmt = ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(
                        value=ast.Attribute(
                            value=_parent_class(ctx, node),
                            attr=accessor.python_name,
                            ctx=ast.Load(),
                        ),
                        attr="fset",
                        ctx=ast.Load(),
                    ),
                    args=[parse_expr(receiver), value_expr],
                    keywords=[],
                )
            )
        else:
            target.ctx = ast.Store()
            stmt = ast.Assign(targets=[target], value=value_expr)
        return CodeFragment(statements=[*prelude, stmt], bindings=bindings)

    value_out = terminal("output", pane.value_out)
    if value_out is None:
        return CodeFragment(statements=[ast.Expr(value=target)], bindings=bindings)
    var = ctx.make_output_var(
        to_var_name(accessor.property_name), node.id, value_out.id
    )
    bindings[value_out.id] = var
    stmt = ast.Assign(targets=[ast.Name(id=var, ctx=ast.Store())], value=target)
    return CodeFragment(statements=[stmt], bindings=bindings)


def _super(node: VINode, ctx: CodeGenContext, receiver: str) -> ast.expr:
    """``super()``, or the parent's view of the wired object when it is not
    ``self``."""
    if receiver == "self" or ctx.graph is None or ctx.vi_name is None:
        return parse_expr("super()")
    class_key = ctx.graph.owning_class_key(ctx.vi_name)
    if class_key is None:
        raise CodeGenError("Call Parent is made from a method of a class", node)
    return parse_expr(f"super({python_class_name(class_key)}, {receiver})")


def _accessor_default(
    ctx: CodeGenContext, node: VINode, pane: AccessorPane
) -> ast.expr:
    """The value a setter's unwired value input takes: the default of the
    accessor VI's own control."""
    assert ctx.graph is not None and node.callee_key is not None
    inputs, _, _, _ = ctx.graph.vi_interface(node.callee_key)
    control = next((t for t in inputs if t.index == pane.value_in), None)
    return param_default_expr(control) if control is not None else ast.Constant(None)


def _parent_class(ctx: CodeGenContext, node: VINode) -> ast.expr:
    """The class the calling VI's class descends from, imported."""
    assert ctx.graph is not None and ctx.vi_name is not None
    class_key = ctx.graph.owning_class_key(ctx.vi_name)
    if class_key is None or not isinstance(ctx.import_resolver, ImportResolver):
        raise CodeGenError(
            "Call Parent of a property is made from a method of a class", node
        )
    base = base_import(ctx.graph, class_key, ctx.import_resolver)
    if base is None:
        raise CodeGenError("Call Parent of a property in a class with no parent", node)
    ctx.add_import(base.statement)
    return ast.Name(id=base.name, ctx=ast.Load())

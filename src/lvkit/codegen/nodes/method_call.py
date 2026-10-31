"""Code generator for a call of a VI that is a method of a LabVIEW class.

A class is one Python class and its member VIs are its methods
(``class_model``), so a call is the method call Python has:

* a dynamic-dispatch call, and a static call that takes the class wire, is
  ``receiver.method(...)`` (Python's own lookup is the dispatch);
* Call Parent is ``super().method(...)``;
* a static call that takes no class wire is ``Class.method(...)``.

Arguments go by keyword. The results are read the way the shape of the method
says (``MethodShape``): nothing, the one value, or the fields of its
``NamedTuple``; the class output is the receiver, which the method changed in
place, unless the shape returns it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.graph.models import VINode
from lvkit.models import Terminal

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..class_model import MethodKind, MethodShape, class_import, python_class_name
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver
from ..value_copy import is_by_value_object, owned_input


@dataclass
class MethodCall:
    """One call of a method: the keyword arguments, the variable the result is
    assigned to (None when the method returns nothing), the node's outputs read
    from it and the statements to run first (copies of shared objects)."""

    keywords: list[ast.keyword]
    result_var: str | None
    bindings: dict[str, str]
    prelude: list[ast.stmt]
    receiver: str | None


def generate(node: VINode, ctx: CodeGenContext, shape: MethodShape) -> CodeFragment:
    """Generate the call of the method ``shape`` from ``node``."""
    call = build_call(node, ctx, shape, receiver_keyword=None)
    if node.node_type == "callParentDynIUse":
        target: ast.expr = _super(node, ctx, call.receiver)
    elif call.receiver is not None:
        target = parse_expr(call.receiver)
    else:
        target = _class_name(shape, ctx)
    invoke = ast.Call(
        func=ast.Attribute(value=target, attr=shape.name, ctx=ast.Load()),
        args=[],
        keywords=call.keywords,
    )
    statement: ast.stmt = (
        ast.Assign(
            targets=[ast.Name(id=call.result_var, ctx=ast.Store())], value=invoke
        )
        if call.result_var is not None
        else ast.Expr(value=invoke)
    )
    return CodeFragment(statements=[*call.prelude, statement], bindings=call.bindings)


def build_call(
    node: VINode,
    ctx: CodeGenContext,
    shape: MethodShape,
    receiver_keyword: str | None,
) -> MethodCall:
    """The arguments and results of the call of ``shape`` from ``node``. The
    object the method works on is a keyword argument named ``receiver_keyword``
    when that is given (a call by reference), else the object the call is made
    on (``receiver``)."""
    assert ctx.graph is not None
    prelude: list[ast.stmt] = []
    terminals = ctx.enriched_terminals(node)
    receiver_term = _terminal(terminals, "input", shape.receiver_in)
    receiver: str | None = None
    if shape.kind is not MethodKind.STATIC:
        # Call Parent works on the object it is called from, never on a copy.
        receiver = (
            None
            if receiver_term is None
            else ctx.wired_input(receiver_term.id, node)
            if node.node_type == "callParentDynIUse"
            else owned_input(ctx, receiver_term, node, prelude)
        )
        if receiver is None and shape.kind is MethodKind.INSTANCE:
            # A static VI whose class input is unwired works on the class's
            # default object.
            receiver = ctx.make_output_var(
                f"{to_var_name(shape.name.lstrip('_'))}_object", node.id
            )
            prelude.append(
                build_assign(
                    receiver,
                    ast.Call(func=_class_name(shape, ctx), args=[], keywords=[]),
                )
            )
        if receiver is None:
            raise CodeGenError(
                f"The call of the dynamic-dispatch method {shape.name!r} has no "
                "object wired to its class input, so there is no object to "
                "dispatch on",
                node,
            )

    keywords: list[ast.keyword] = []
    if receiver_keyword is not None and receiver is not None:
        keywords.append(ast.keyword(arg=receiver_keyword, value=parse_expr(receiver)))
    for term in terminals:
        if term.direction != "input" or term.is_error_cluster:
            continue
        if term.index == shape.receiver_in or term.index not in shape.parameters:
            continue
        value = (
            owned_input(ctx, term, node, prelude)
            if is_by_value_object(term.lv_type)
            else ctx.wired_input(term.id, node)
        )
        if value is not None:
            keywords.append(
                ast.keyword(arg=shape.parameters[term.index], value=parse_expr(value))
            )

    bindings: dict[str, str] = {}
    result_var: str | None = None
    if shape.results:
        result_var = ctx.make_output_var(f"{shape.name.lstrip('_')}_result", node.id)
    for term in terminals:
        if term.direction != "output" or term.is_error_cluster:
            continue
        if term.index in shape.results and result_var is not None:
            bindings[term.id] = (
                result_var
                if len(shape.results) == 1
                else f"{result_var}.{shape.result_fields[term.index]}"
            )
        elif term.index == shape.receiver_out and receiver is not None:
            bindings[term.id] = receiver
    return MethodCall(keywords, result_var, bindings, prelude, receiver)


def _terminal(
    terminals: list[Terminal], direction: str, index: int | None
) -> Terminal | None:
    if index is None:
        return None
    return next(
        (t for t in terminals if t.direction == direction and t.index == index), None
    )


def _super(node: VINode, ctx: CodeGenContext, receiver: str | None) -> ast.expr:
    """``super()`` for Call Parent; when the wired object is not ``self`` (the
    wire was copied), the parent's view of that object."""
    if receiver in (None, "self") or ctx.graph is None or ctx.vi_name is None:
        return parse_expr("super()")
    class_key = ctx.graph.owning_class_key(ctx.vi_name)
    if class_key is None:
        raise CodeGenError("Call Parent is made from a method of a class", node)
    return parse_expr(f"super({python_class_name(class_key)}, {receiver})")


def _class_name(shape: MethodShape, ctx: CodeGenContext) -> ast.expr:
    """The class a static method is called on, imported."""
    assert ctx.graph is not None
    if not isinstance(ctx.import_resolver, ImportResolver):
        raise CodeGenError(
            "A static method is called on its class, which has no module"
        )
    imported = class_import(ctx.graph, shape.class_key, ctx.import_resolver)
    ctx.add_import(imported.statement)
    return ast.Name(id=imported.name, ctx=ast.Load())

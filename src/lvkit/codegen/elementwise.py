"""Rewrite a scalar numeric expression into element-wise helper calls.

Used for primitives flagged ``elementwise`` in primitives.json when at least
one operand is an array. The scalar template (e.g. ``a - b``) is parsed to an
AST and its arithmetic/comparison/unary operators are replaced with calls to
``lvkit.runtime.lv`` helpers, which broadcast over lists. Scalar uses are left
untouched (the helpers are scalar-safe, but we only apply this when an array
operand is actually present, keeping scalar codegen clean).
"""

from __future__ import annotations

import ast

LV_IMPORT = "from lvkit.runtime import lv as _lv"

_BINOP = {
    ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "truediv",
    ast.FloorDiv: "floordiv", ast.Mod: "mod", ast.Pow: "pow_",
}
_CMP = {
    ast.Gt: "gt", ast.Lt: "lt", ast.GtE: "ge", ast.LtE: "le",
    ast.Eq: "eq", ast.NotEq: "ne",
}


def _call(fn: str, args: list[ast.expr]) -> ast.Call:
    return ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="_lv", ctx=ast.Load()), attr=fn, ctx=ast.Load()
        ),
        args=args,
        keywords=[],
    )


class _Arrayify(ast.NodeTransformer):
    used: bool = False

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        fn = _BINOP.get(type(node.op))
        if fn:
            self.used = True
            return _call(fn, [node.left, node.right])
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, ast.USub):
            self.used = True
            return _call("neg", [node.operand])
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if len(node.ops) == 1:
            fn = _CMP.get(type(node.ops[0]))
            if fn:
                self.used = True
                return _call(fn, [node.left, node.comparators[0]])
        return node


def arrayify(expr: ast.expr) -> tuple[ast.expr, bool]:
    """Return (rewritten_expr, used_helper). ``used_helper`` is True when any
    operator was rewritten (so the caller adds the import)."""
    t = _Arrayify()
    new = t.visit(expr)
    ast.fix_missing_locations(new)
    return new, t.used


def _is_array_valued(node: ast.expr, array_vars: frozenset[str]) -> bool:
    """An operator's operand carries an array if it names a known array
    variable or is already an ``_lv.*`` broadcast call (which returns an array
    when its input was one). A subscript (``a[i]``) is an element — scalar."""
    if isinstance(node, ast.Name):
        return node.id in array_vars
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_lv"):
        return True
    return False


class _ModuleArrayify(ast.NodeTransformer):
    """Arrayify operators whose operands are array-valued, anywhere in the
    tree. Catches expressions that were inlined past the per-node hook."""

    def __init__(self, array_vars: frozenset[str]):
        self.array_vars = array_vars
        self.used = False

    def _arr(self, n: ast.expr) -> bool:
        return _is_array_valued(n, self.array_vars)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        fn = _BINOP.get(type(node.op))
        if fn and (self._arr(node.left) or self._arr(node.right)):
            self.used = True
            return _call(fn, [node.left, node.right])
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, ast.USub) and self._arr(node.operand):
            self.used = True
            return _call("neg", [node.operand])
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if len(node.ops) == 1 and (self._arr(node.left)
                                   or self._arr(node.comparators[0])):
            fn = _CMP.get(type(node.ops[0]))
            if fn:
                self.used = True
                return _call(fn, [node.left, node.comparators[0]])
        return node


def arrayify_module(body: list[ast.stmt], array_vars: frozenset[str]) -> bool:
    """Rewrite operators over array-valued operands across a whole function
    body (post-codegen, so inlined single-use expressions are covered).
    Returns True if anything was rewritten."""
    if not array_vars:
        return False
    t = _ModuleArrayify(array_vars)
    for stmt in body:
        t.visit(stmt)
    ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    return t.used

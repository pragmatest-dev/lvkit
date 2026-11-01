"""Element-wise arithmetic over arrays, written as list comprehensions.

LabVIEW's numeric functions are polymorphic: an operator applied to an array
(or to a scalar and an array) works on every element. Python's operators do not,
so when an operand of an operator, comparison or conversion is an array, the
expression is rewritten into a comprehension over the arrays, one nesting level
per array dimension::

    a - b            ->  [_e0 - _e1 for _e0, _e1 in zip(a, b)]
    a - 1            ->  [_e0 - 1 for _e0 in a]
    int(round(m))    ->  [[int(round(_e1)) for _e1 in _e0] for _e0 in m]

An operand is an array when the graph types its wire as one; the rank (the
dimension count) of each operand comes from that type. A connected chain of
operators becomes one comprehension, not one per operator. A scalar operand stays
as it is inside the comprehension.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

# Operators and conversion calls that apply to every element of an array.
_UNARY_CALLS = frozenset({"int", "float", "bool", "round", "abs"})
_UNARY_OPS = (ast.USub, ast.UAdd, ast.Not, ast.Invert)
_COMPARE_OPS = (ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq)
_BINARY_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.BitAnd,
    ast.BitOr,
    ast.BitXor,
    ast.LShift,
    ast.RShift,
)


def _is_list_shaped(node: ast.expr) -> bool:
    """True for an expression that is a LIST by construction: a sliced subscript
    (``x[a:b]``), a list literal (``[x]``), or a ``+`` of such. Build Array and
    Replace Array Subset join these with ``+`` to CONCATENATE; a scalar operand
    is never list-shaped, so ``5.0 + arr[:3]`` (a broadcast) is not mistaken for a
    concat."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        return True
    if isinstance(node, ast.List):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_list_shaped(node.left) and _is_list_shaped(node.right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return isinstance(node.left, ast.List) or isinstance(node.right, ast.List)
    return False


def _unary_call(node: ast.AST) -> bool:
    """A conversion of one value (``int(x)``, ``round(x)``, …) or a ``math``
    function of its arguments (``math.log10(x)``, ``math.copysign(a, b)``)."""
    if not (
        isinstance(node, ast.Call)
        and node.args
        and not node.keywords
        and not any(isinstance(a, ast.Starred) for a in node.args)
    ):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _UNARY_CALLS and len(node.args) == 1
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "math"
    )


def _fixed_width_value(node: ast.AST) -> ast.Call | None:
    """The ``ctypes.c_int32(x)`` call of a ``ctypes.c_int32(x).value`` fixed-width
    integer conversion, else None."""
    if not (
        isinstance(node, ast.Attribute)
        and node.attr == "value"
        and isinstance(node.value, ast.Call)
        and len(node.value.args) == 1
    ):
        return None
    func = node.value.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "ctypes"
    ):
        return node.value
    return None


class _Ranks:
    """The dimension count of an expression: from the wire types of the operands
    (``known``, keyed by the operand's source text), from the array variables of
    the function (``variables``), and from the shape of the expression itself."""

    def __init__(self, known: Mapping[str, int], variables: Mapping[str, int]) -> None:
        self.known = known
        self.variables = variables
        self.loop_variables: dict[str, int] = {}
        # Whether ``a if c else b`` applies per element (a Select over arrays
        # would not, so only an expression written for it turns this on).
        self.conditionals = False

    def __call__(self, node: ast.expr) -> int:
        text = ast.unparse(node)
        if text in self.known:
            return self.known[text]
        if isinstance(node, ast.Name):
            if node.id in self.loop_variables:
                return self.loop_variables[node.id]
            return self.variables.get(node.id, 0)
        if isinstance(node, ast.Subscript):
            inner = self(node.value)
            if isinstance(node.slice, ast.Slice):
                return inner
            return max(inner - 1, 0)
        if isinstance(node, ast.ListComp):
            return 1 + self(node.elt)
        if isinstance(node, ast.BinOp):
            return max(self(node.left), self(node.right))
        if isinstance(node, ast.UnaryOp):
            return self(node.operand)
        if isinstance(node, ast.Compare):
            return max(self(node.left), *(self(c) for c in node.comparators))
        if isinstance(node, ast.IfExp):
            return max(self(node.test), self(node.body), self(node.orelse))
        if _unary_call(node):
            assert isinstance(node, ast.Call)
            return max(self(a) for a in node.args)
        conversion = _fixed_width_value(node)
        if conversion is not None:
            return self(conversion.args[0])
        return 0


def _applies_per_element(
    node: ast.expr, ranks: _Ranks, keep_list_concat: bool
) -> bool:
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BINARY_OPS):
            return False
        if (
            keep_list_concat
            and isinstance(node.op, ast.Add)
            and (
                isinstance(node.left, ast.List)
                or isinstance(node.right, ast.List)
                or (_is_list_shaped(node.left) and _is_list_shaped(node.right))
            )
        ):
            return False
        return True
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, _UNARY_OPS)
    if isinstance(node, ast.Compare):
        return len(node.ops) == 1 and isinstance(node.ops[0], _COMPARE_OPS)
    if isinstance(node, ast.IfExp):
        return ranks.conditionals
    return _unary_call(node) or _fixed_width_value(node) is not None


def _operands(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp):
        return [node.left, node.right]
    if isinstance(node, ast.UnaryOp):
        return [node.operand]
    if isinstance(node, ast.Compare):
        return [node.left, node.comparators[0]]
    if isinstance(node, ast.IfExp):
        return [node.test, node.body, node.orelse]
    conversion = _fixed_width_value(node)
    if conversion is not None:
        return list(conversion.args)
    assert isinstance(node, ast.Call)
    return list(node.args)


def _array_leaves(
    node: ast.expr, ranks: _Ranks, keep_list_concat: bool, out: list[ast.expr]
) -> None:
    """The array-valued operands at the edge of a connected chain of
    per-element operators, in evaluation order."""
    for operand in _operands(node):
        if ranks(operand) == 0:
            continue
        if _applies_per_element(operand, ranks, keep_list_concat):
            _array_leaves(operand, ranks, keep_list_concat, out)
        else:
            out.append(operand)


class _ReplaceLeaves(ast.NodeTransformer):
    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    def visit(self, node: ast.AST) -> ast.AST:
        if isinstance(node, ast.expr):
            name = self.names.get(ast.unparse(node))
            if name is not None:
                return ast.Name(id=name, ctx=ast.Load())
        return super().visit(node)


def _fresh_names(count: int, taken: set[str]) -> list[str]:
    names: list[str] = []
    serial = 0
    while len(names) < count:
        candidate = f"_e{serial}"
        serial += 1
        if candidate not in taken:
            names.append(candidate)
            taken.add(candidate)
    return names


def _lift_children(
    node: ast.AST, ranks: _Ranks, keep_list_concat: bool, taken: set[str]
) -> bool:
    changed = False
    for field_name, value in ast.iter_fields(node):
        if isinstance(value, ast.expr):
            new, did = _lift(value, ranks, keep_list_concat, taken)
            if did:
                setattr(node, field_name, new)
                changed = True
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, ast.expr):
                    new, did = _lift(item, ranks, keep_list_concat, taken)
                    if did:
                        value[i] = new
                        changed = True
                elif isinstance(item, (ast.comprehension, ast.keyword)):
                    changed = (
                        _lift_children(item, ranks, keep_list_concat, taken) or changed
                    )
    return changed


def _lift(
    node: ast.expr, ranks: _Ranks, keep_list_concat: bool, taken: set[str]
) -> tuple[ast.expr, bool]:
    """``node`` with every per-element operator chain over arrays turned into a
    comprehension; whether anything changed."""
    if _applies_per_element(node, ranks, keep_list_concat) and ranks(node) > 0:
        leaves: list[ast.expr] = []
        _array_leaves(node, ranks, keep_list_concat, leaves)
        unique: dict[str, ast.expr] = {}
        for leaf in leaves:
            lifted, _ = _lift(leaf, ranks, keep_list_concat, taken)
            unique.setdefault(ast.unparse(leaf), lifted)
        if unique:
            names = dict(zip(unique, _fresh_names(len(unique), taken)))
            inner_node = _ReplaceLeaves(names).visit(node)
            assert isinstance(inner_node, ast.expr)
            for text, leaf in unique.items():
                ranks.loop_variables[names[text]] = max(ranks(leaf) - 1, 0)
            inner, _ = _lift(inner_node, ranks, keep_list_concat, taken)
            targets: list[ast.expr] = [
                ast.Name(id=n, ctx=ast.Store()) for n in names.values()
            ]
            sources = list(unique.values())
            target: ast.expr
            iterable: ast.expr
            if len(sources) == 1:
                target, iterable = targets[0], sources[0]
            else:
                target = ast.Tuple(elts=targets, ctx=ast.Store())
                iterable = ast.Call(
                    func=ast.Name(id="zip", ctx=ast.Load()),
                    args=sources,
                    keywords=[],
                )
            comprehension = ast.comprehension(
                target=target, iter=iterable, ifs=[], is_async=0
            )
            return ast.ListComp(elt=inner, generators=[comprehension]), True
    return node, _lift_children(node, ranks, keep_list_concat, taken)


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def arrayify(
    expr: ast.expr, operand_ranks: Mapping[str, int], conditionals: bool = False
) -> tuple[ast.expr, bool]:
    """Rewrite one primitive's expression so its operators apply per element.
    ``operand_ranks`` maps the source text of each array operand to its
    dimension count. ``conditionals`` also applies ``a if c else b`` per element.
    Returns (expression, whether anything was rewritten)."""
    ranks = _Ranks(operand_ranks, {})
    ranks.conditionals = conditionals
    new, changed = _lift(expr, ranks, False, _names_in(expr))
    ast.fix_missing_locations(new)
    return new, changed


class _ModuleLift(ast.NodeTransformer):
    def __init__(self, array_variables: Mapping[str, int]) -> None:
        self.ranks = _Ranks({}, array_variables)
        self.taken = set(array_variables)
        self.changed = False

    def visit(self, node: ast.AST) -> ast.AST:
        if isinstance(node, ast.expr):
            self.ranks.loop_variables.clear()
            new, did = _lift(node, self.ranks, True, self.taken | _names_in(node))
            self.changed = self.changed or did
            return new
        return self.generic_visit(node)


def arrayify_module(body: list[ast.stmt], array_variables: Mapping[str, int]) -> bool:
    """Rewrite operators over array variables across a whole function body
    (after generation, so expressions inlined from other nodes are covered).
    Returns True if anything was rewritten."""
    if not array_variables:
        return False
    lifter = _ModuleLift(array_variables)
    for stmt in body:
        lifter.visit(stmt)
    ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    return lifter.changed

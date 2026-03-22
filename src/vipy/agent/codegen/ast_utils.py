"""AST building utilities for code generation."""

from __future__ import annotations

import ast
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_expr(template: str) -> ast.expr:
    """Parse a string template into an AST expression.

    Args:
        template: Python expression as string

    Returns:
        AST expression node
    """
    try:
        tree = ast.parse(template, mode="eval")
        return tree.body
    except SyntaxError as e:
        logger.warning("Invalid expression template: %r — %s", template, e)
        return ast.Constant(value=None)


def parse_stmt(template: str) -> ast.stmt:
    """Parse a string template into an AST statement.

    Args:
        template: Python statement as string

    Returns:
        AST statement node
    """
    try:
        tree = ast.parse(template, mode="exec")
        if tree.body:
            return tree.body[0]
        return ast.Pass()
    except SyntaxError as e:
        logger.warning("Invalid statement template: %r — %s", template, e)
        return ast.Expr(value=ast.Constant(value=None))


def build_assign(target: str, value: ast.expr) -> ast.Assign:
    """Build an assignment statement: target = value."""
    return ast.Assign(
        targets=[ast.Name(id=target, ctx=ast.Store())],
        value=value,
    )


def build_assign_from_str(target: str, value_expr: str) -> ast.Assign:
    """Build an assignment from string expression."""
    return build_assign(target, parse_expr(value_expr))


def build_multi_assign(targets: list[str], value: ast.expr) -> ast.Assign:
    """Build a tuple unpacking assignment: a, b, c = value."""
    if len(targets) == 1:
        return build_assign(targets[0], value)

    return ast.Assign(
        targets=[
            ast.Tuple(
                elts=[ast.Name(id=t, ctx=ast.Store()) for t in targets],
                ctx=ast.Store(),
            )
        ],
        value=value,
    )


def build_call(
    func: str, args: list[str], keywords: dict[str, str] | None = None
) -> ast.Call:
    """Build a function call: func(args..., **keywords)."""
    return ast.Call(
        func=ast.Name(id=func, ctx=ast.Load()),
        args=[ast.Name(id=a, ctx=ast.Load()) for a in args],
        keywords=[
            ast.keyword(arg=k, value=ast.Name(id=v, ctx=ast.Load()))
            for k, v in (keywords or {}).items()
        ],
    )


def build_method_call(obj: str, method: str, args: list[str]) -> ast.Call:
    """Build a method call: obj.method(args...)."""
    return ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=obj, ctx=ast.Load()),
            attr=method,
            ctx=ast.Load(),
        ),
        args=[ast.Name(id=a, ctx=ast.Load()) for a in args],
        keywords=[],
    )


def build_attr_access(obj: str, attr: str) -> ast.Attribute:
    """Build attribute access: obj.attr."""
    return ast.Attribute(
        value=ast.Name(id=obj, ctx=ast.Load()),
        attr=attr,
        ctx=ast.Load(),
    )


def build_name(name: str) -> ast.Name:
    """Build a name reference."""
    return ast.Name(id=name, ctx=ast.Load())


def build_constant(value: Any) -> ast.Constant:
    """Build a constant value."""
    return ast.Constant(value=value)


def substitute_names(node: ast.expr, mapping: dict[str, str]) -> ast.expr:
    """Replace Name nodes with mapped variable names.

    Args:
        node: AST expression to transform
        mapping: name → replacement mapping

    Returns:
        Transformed AST expression
    """
    class NameReplacer(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.AST:
            name_lower = node.id.lower()
            # Try exact match first
            if node.id in mapping:
                return ast.Name(id=mapping[node.id], ctx=node.ctx)
            # Try case-insensitive match
            for key, value in mapping.items():
                if key.lower() == name_lower:
                    return ast.Name(id=value, ctx=node.ctx)
            return node

    return NameReplacer().visit(node)


def substitute_in_template(template: str, mapping: dict[str, str]) -> ast.expr:
    """Parse template and substitute variable names.

    Args:
        template: Python expression template
        mapping: variable name → replacement value

    Returns:
        AST expression with substitutions applied
    """
    expr = parse_expr(template)
    return substitute_names(expr, mapping)


def build_return(values: list[str], result_class: str | None = None) -> ast.Return:
    """Build a return statement.

    Args:
        values: Variable names to return
        result_class: If provided, wraps in result_class(var=value, ...)

    Returns:
        Return statement AST
    """
    if not values:
        return ast.Return(value=None)

    if len(values) == 1 and not result_class:
        return ast.Return(value=ast.Name(id=values[0], ctx=ast.Load()))

    if result_class:
        # Return ResultClass(field1=val1, field2=val2, ...)
        return ast.Return(
            value=ast.Call(
                func=ast.Name(id=result_class, ctx=ast.Load()),
                args=[],
                keywords=[
                    ast.keyword(arg=v, value=ast.Name(id=v, ctx=ast.Load()))
                    for v in values
                ],
            )
        )

    # Return tuple
    return ast.Return(
        value=ast.Tuple(
            elts=[ast.Name(id=v, ctx=ast.Load()) for v in values],
            ctx=ast.Load(),
        )
    )


def to_var_name(name: str) -> str:
    """Convert a name to a valid Python variable name."""
    if not name:
        return "var"

    result = name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    if result and not result[0].isalpha() and result[0] != "_":
        result = "var_" + result
    if not result:
        result = "var"
    # Handle Python keywords
    import keyword
    if keyword.iskeyword(result):
        result = result + "_"
    return result


def to_function_name(vi_name: str) -> str:
    """Convert VI name to Python function name."""
    # Remove .vi extension
    name = vi_name.replace(".vi", "").replace(".VI", "")
    return to_var_name(name)


def to_module_name(vi_name: str) -> str:
    """Convert VI name to Python module name (strips library prefix).

    Examples:
        "Get Settings Path.vi" -> "get_settings_path"
        "GraphicalTestRunner.lvlib:Run.vi" -> "run"
    """
    # Strip library prefix
    if ":" in vi_name:
        vi_name = vi_name.split(":")[-1]
    vi_name = vi_name.replace(".vi", "").replace(".VI", "")
    result = vi_name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    return result or "module"

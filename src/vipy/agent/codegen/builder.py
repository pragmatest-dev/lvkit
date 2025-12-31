"""Main code generation builder - traversal and module assembly."""

from __future__ import annotations

import ast
from typing import Any

from .ast_utils import to_function_name, to_var_name
from .context import CodeGenContext
from .nodes import get_codegen


def build_module(
    vi_context: dict[str, Any],
    vi_name: str,
    vi_context_lookup: Any = None,
) -> str:
    """Build complete Python module from VI context.

    Args:
        vi_context: VI context dict with operations, inputs, outputs, etc.
        vi_name: Name of the VI (used for function name)
        vi_context_lookup: Optional callable (vi_name) -> context for looking up
                          callee VI parameter names

    Returns:
        Python source code as string
    """
    # Initialize context with inputs and constants
    ctx = CodeGenContext.from_vi_context(vi_context)
    ctx.vi_context_lookup = vi_context_lookup

    # Generate function body
    body = generate_body(vi_context.get("operations", []), ctx)

    # Add return statement
    return_stmt = build_return_stmt(vi_context, ctx)
    if return_stmt:
        body.append(return_stmt)

    # Build module structure
    module = build_module_ast(vi_context, vi_name, body, ctx)

    # Fix locations and unparse
    ast.fix_missing_locations(module)
    return ast.unparse(module)


def generate_body(
    operations: list[dict[str, Any]], ctx: CodeGenContext
) -> list[ast.stmt]:
    """Generate function body statements from operations.

    Args:
        operations: List of operation nodes
        ctx: Code generation context

    Returns:
        List of AST statements
    """
    statements: list[ast.stmt] = []

    # Filter to top-level operations (not inside loops)
    top_level = [op for op in operations if not op.get("parent_loop")]

    # Topologically sort operations
    sorted_ops = topological_sort(top_level, ctx)

    for node in sorted_ops:
        codegen = get_codegen(node)
        fragment = codegen.generate(node, ctx)

        statements.extend(fragment.statements)
        ctx.merge(fragment.bindings)
        ctx.imports.update(fragment.imports)

    return statements


def topological_sort(operations: list[dict], ctx: CodeGenContext) -> list[dict]:
    """Sort operations by data dependencies.

    An operation can execute when all its input wires have data.
    """
    if not operations:
        return []

    op_by_id = {op.get("id"): op for op in operations}
    dependencies: dict[str, set[str]] = {op.get("id"): set() for op in operations}

    # Build output terminal → operation mapping
    output_to_op: dict[str, str] = {}
    for op in operations:
        op_id = op.get("id")
        for term in op.get("terminals", []):
            if term.get("direction") == "output":
                output_to_op[term.get("id")] = op_id

    # Build dependencies from data flow
    for op in operations:
        op_id = op.get("id")
        for term in op.get("terminals", []):
            if term.get("direction") != "input":
                continue
            term_id = term.get("id")
            # Look up source in flow map
            if term_id in ctx._flow_map:
                src_term = ctx._flow_map[term_id]["src_terminal"]
                if src_term in output_to_op:
                    dep_op_id = output_to_op[src_term]
                    if dep_op_id != op_id and dep_op_id in dependencies:
                        dependencies[op_id].add(dep_op_id)

    # Kahn's algorithm
    result = []
    ready = [op_id for op_id, deps in dependencies.items() if not deps]
    remaining = {op_id: set(deps) for op_id, deps in dependencies.items() if deps}

    while ready:
        op_id = ready.pop(0)
        if op_id in op_by_id:
            result.append(op_by_id[op_id])

        # Update remaining dependencies
        to_remove = []
        for other_id, deps in remaining.items():
            deps.discard(op_id)
            if not deps:
                ready.append(other_id)
                to_remove.append(other_id)
        for r in to_remove:
            del remaining[r]

    # Add any remaining (circular dependencies)
    for op_id in remaining:
        if op_id in op_by_id:
            result.append(op_by_id[op_id])

    return result


def build_return_stmt(
    vi_context: dict[str, Any], ctx: CodeGenContext
) -> ast.Return | None:
    """Build return statement for function.

    Returns NamedTuple with output values resolved from context.
    """
    outputs = vi_context.get("outputs", [])
    if not outputs:
        return None

    result_class = build_result_class_name(vi_context.get("name", "VI"))

    # Resolve output values
    keywords = []
    for out in outputs:
        out_id = out.get("id")
        out_name = out.get("name", "output")
        var_name = to_var_name(out_name)

        # Try to resolve from context
        value = ctx.resolve(out_id)
        if value:
            value_ast = ast.Name(id=value, ctx=ast.Load())
        else:
            value_ast = ast.Constant(value=None)

        keywords.append(ast.keyword(arg=var_name, value=value_ast))

    return ast.Return(
        value=ast.Call(
            func=ast.Name(id=result_class, ctx=ast.Load()),
            args=[],
            keywords=keywords,
        )
    )


def build_module_ast(
    vi_context: dict[str, Any],
    vi_name: str,
    body: list[ast.stmt],
    ctx: CodeGenContext,
) -> ast.Module:
    """Build complete module AST."""
    module_body: list[ast.stmt] = []

    # Imports
    module_body.extend(build_imports(vi_context, ctx))

    # Result class
    result_class = build_result_class(vi_context)
    if result_class:
        module_body.append(result_class)

    # Function definition
    func_def = build_function_def(vi_context, vi_name, body)
    module_body.append(func_def)

    return ast.Module(body=module_body, type_ignores=[])


def build_imports(vi_context: dict[str, Any], ctx: CodeGenContext) -> list[ast.stmt]:
    """Build import statements."""
    imports: list[ast.stmt] = []

    # Standard imports
    imports.append(
        ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations", asname=None)],
            level=0,
        )
    )

    # Common imports
    common = ["from pathlib import Path", "from typing import Any, NamedTuple"]
    for imp in common:
        try:
            tree = ast.parse(imp)
            imports.extend(tree.body)
        except SyntaxError:
            pass

    # Context-accumulated imports
    for imp in sorted(ctx.imports):
        try:
            tree = ast.parse(imp)
            imports.extend(tree.body)
        except SyntaxError:
            pass

    return imports


def build_result_class(vi_context: dict[str, Any]) -> ast.ClassDef | None:
    """Build NamedTuple result class."""
    outputs = vi_context.get("outputs", [])
    if not outputs:
        return None

    class_name = build_result_class_name(vi_context.get("name", "VI"))

    # Build fields
    fields = []
    for out in outputs:
        name = to_var_name(out.get("name", "output"))
        type_hint = out.get("type", "Any")
        if isinstance(type_hint, dict):
            type_hint = type_hint.get("python_type", "Any")
        fields.append((name, type_hint))

    # Build class body with type annotations
    class_body = []
    for name, type_hint in fields:
        ann = ast.AnnAssign(
            target=ast.Name(id=name, ctx=ast.Store()),
            annotation=ast.Name(id=type_hint, ctx=ast.Load()),
            simple=1,
        )
        class_body.append(ann)

    if not class_body:
        class_body = [ast.Pass()]

    return ast.ClassDef(
        name=class_name,
        bases=[ast.Name(id="NamedTuple", ctx=ast.Load())],
        keywords=[],
        body=class_body,
        decorator_list=[],
    )


def build_function_def(
    vi_context: dict[str, Any], vi_name: str, body: list[ast.stmt]
) -> ast.FunctionDef:
    """Build function definition."""
    func_name = to_function_name(vi_name)

    # Build arguments
    args = build_args(vi_context.get("inputs", []))

    # Build return annotation
    returns = None
    if vi_context.get("outputs"):
        result_class = build_result_class_name(vi_name)
        returns = ast.Name(id=result_class, ctx=ast.Load())

    # Ensure non-empty body
    if not body:
        body = [ast.Pass()]

    return ast.FunctionDef(
        name=func_name,
        args=args,
        body=body,
        decorator_list=[],
        returns=returns,
    )


def build_args(inputs: list[dict[str, Any]]) -> ast.arguments:
    """Build function arguments from inputs."""
    args = []
    defaults = []

    for inp in inputs:
        name = to_var_name(inp.get("name", "input"))
        type_hint = inp.get("type", "Any")
        if isinstance(type_hint, dict):
            type_hint = type_hint.get("python_type", "Any")

        arg = ast.arg(
            arg=name,
            annotation=ast.Name(id=type_hint, ctx=ast.Load()),
        )
        args.append(arg)
        defaults.append(ast.Constant(value=None))  # Default to None

    return ast.arguments(
        posonlyargs=[],
        args=args,
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=defaults,
    )


def build_result_class_name(vi_name: str) -> str:
    """Build result class name from VI name."""
    base = to_function_name(vi_name)
    # Convert to CamelCase
    parts = base.split("_")
    camel = "".join(p.capitalize() for p in parts if p)
    return f"{camel}Result"

"""Main code generation builder - traversal and module assembly."""

from __future__ import annotations

import ast
from collections import deque
from typing import Any

from vipy.graph_types import FPTerminalNode, Operation

from .ast_optimizer import optimize_module
from .ast_utils import parse_expr, to_function_name, to_var_name
from .context import CodeGenContext
from .error_handler import (
    build_held_error_check,
    build_held_error_init,
    build_labview_error_import,
    needs_error_handling,
)
from .nodes import get_codegen


def build_module(
    vi_context: dict[str, Any],
    vi_name: str,
    vi_context_lookup: Any = None,
    import_resolver: Any = None,
    has_parallel_branches: bool | None = None,
) -> str:
    """Build complete Python module from VI context.

    Args:
        vi_context: VI context dict with operations, inputs, outputs, etc.
        vi_name: Name of the VI (used for function name)
        vi_context_lookup: Optional callable (vi_name) -> context for looking up
                          callee VI parameter names
        import_resolver: Optional callable (subvi_name) -> import statement string
        has_parallel_branches: If True, enable held error model for parallel
                              branch error handling. If None, reads from vi_context.

    Returns:
        Python source code as string
    """
    # Initialize context with inputs and constants
    ctx = CodeGenContext.from_vi_context(vi_context)
    ctx.vi_context_lookup = vi_context_lookup
    ctx.import_resolver = import_resolver
    ctx.vi_name = vi_name

    # Determine if we need error handling infrastructure
    # Read from vi_context if not explicitly passed
    if has_parallel_branches is None:
        has_parallel_branches = vi_context.get("has_parallel_branches", False)
    use_error_handling = needs_error_handling(has_parallel_branches, vi_context)
    ctx.use_held_error_model = use_error_handling

    # Generate function body
    body: list[ast.stmt] = []

    # Add held error initialization if needed
    if use_error_handling:
        body.append(build_held_error_init())

    # Generate operation code
    body.extend(generate_body(vi_context.get("operations", []), ctx))

    # Add held error check before return if we have error handling
    if use_error_handling:
        body.append(build_held_error_check())

    # Add return statement
    return_stmt = build_return_stmt(vi_context, ctx)
    if return_stmt:
        body.append(return_stmt)

    # Build module structure
    module = build_module_ast(vi_context, vi_name, body, ctx, use_error_handling)

    # Optimize AST (duplicate imports, dead code)
    module = optimize_module(module)

    # Fix locations and unparse
    ast.fix_missing_locations(module)
    return ast.unparse(module)


def generate_body(
    operations: list[Operation], ctx: CodeGenContext
) -> list[ast.stmt]:
    """Generate function body statements from operations.

    Args:
        operations: List of Operation nodes
        ctx: Code generation context

    Returns:
        List of AST statements
    """
    statements: list[ast.stmt] = []

    # All operations passed here are top-level (inner loop ops are in inner_nodes)
    top_level = operations

    # Topologically sort operations
    sorted_ops = topological_sort(top_level, ctx)

    for node in sorted_ops:
        codegen = get_codegen(node)
        fragment = codegen.generate(node, ctx)

        statements.extend(fragment.statements)
        ctx.merge(fragment.bindings)
        ctx.imports.update(fragment.imports)

    return statements


def topological_sort(
    operations: list[Operation], ctx: CodeGenContext
) -> list[Operation]:
    """Sort operations by data dependencies.

    An operation can execute when all its input wires have data.
    """
    if not operations:
        return []

    op_by_id = {op.id: op for op in operations}
    dependencies: dict[str, set[str]] = {op.id: set() for op in operations}

    # Build output terminal → operation mapping
    output_to_op: dict[str, str] = {}
    for op in operations:
        for term in op.terminals:
            if term.direction == "output":
                output_to_op[term.id] = op.id

    # Build dependencies from data flow
    for op in operations:
        for term in op.terminals:
            if term.direction != "input":
                continue
            # Look up source in flow map
            if term.id in ctx._flow_map:
                src_term = ctx._flow_map[term.id]["src_terminal"]
                if src_term in output_to_op:
                    dep_op_id = output_to_op[src_term]
                    if dep_op_id != op.id and dep_op_id in dependencies:
                        dependencies[op.id].add(dep_op_id)

    # Kahn's algorithm with deque for O(1) popleft
    result: list[Operation] = []
    ready: deque[str] = deque(
        op_id for op_id, deps in dependencies.items() if not deps
    )
    remaining = {op_id: set(deps) for op_id, deps in dependencies.items() if deps}

    while ready:
        op_id = ready.popleft()
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
        out_id = out.id
        out_name = out.name or "output"
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
    use_error_handling: bool = False,
) -> ast.Module:
    """Build complete module AST."""
    module_body: list[ast.stmt] = []

    # Imports
    module_body.extend(build_imports(vi_context, ctx, use_error_handling))

    # Result class
    result_class = build_result_class(vi_context)
    if result_class:
        module_body.append(result_class)

    # Function definition
    func_def = build_function_def(vi_context, vi_name, body)
    module_body.append(func_def)

    return ast.Module(body=module_body, type_ignores=[])


def build_imports(
    vi_context: dict[str, Any],
    ctx: CodeGenContext,
    use_error_handling: bool = False,
) -> list[ast.stmt]:
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

    # Add LabVIEWError import if using error handling
    if use_error_handling:
        imports.append(build_labview_error_import())

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
        name = to_var_name(out.name or "output")
        # Use LVType for Python type hints if available
        if out.lv_type:
            type_hint = out.lv_type.to_python()
        else:
            type_hint = out.type or "Any"
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


def build_args(inputs: list[FPTerminalNode]) -> ast.arguments:
    """Build function arguments from inputs.

    Wiring rules:
    - 0 = unknown (treat as required)
    - 1 = required (no default)
    - 2 = recommended (has default)
    - 3 = optional (has default)
    """
    args = []
    defaults = []

    for inp in inputs:
        name = to_var_name(inp.name or "input")
        # Use LVType for Python type hints if available
        if inp.lv_type:
            type_hint = inp.lv_type.to_python()
        else:
            type_hint = inp.type or "Any"

        # wiring_rule >= 2 means recommended or optional
        is_optional = inp.wiring_rule >= 2

        arg = ast.arg(
            arg=name,
            annotation=parse_expr(type_hint),
        )
        args.append(arg)

        if is_optional:
            defaults.append(_get_default_for_type(inp.lv_type))

    return ast.arguments(
        posonlyargs=[],
        args=args,
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=defaults,
    )


def _get_default_for_type(lv_type) -> ast.expr:
    """Get appropriate default value for a LabVIEW type."""
    if lv_type is None:
        return ast.Constant(value=None)

    kind = lv_type.kind
    underlying = lv_type.underlying_type

    if kind == "array":
        return ast.List(elts=[], ctx=ast.Load())
    elif kind == "primitive":
        if underlying == "Path":
            return ast.Call(
                func=ast.Name(id="Path", ctx=ast.Load()),
                args=[],
                keywords=[],
            )
        elif underlying == "String":
            return ast.Constant(value="")
        elif underlying == "Boolean":
            return ast.Constant(value=False)
        elif underlying in ("NumInt8", "NumInt16", "NumInt32", "NumInt64",
                           "NumUInt8", "NumUInt16", "NumUInt32", "NumUInt64"):
            return ast.Constant(value=0)
        elif underlying in ("NumFloat32", "NumFloat64"):
            return ast.Constant(value=0.0)
    elif kind == "cluster":
        return ast.Dict(keys=[], values=[])
    elif kind in ("enum", "ring"):
        return ast.Constant(value=0)

    return ast.Constant(value=None)


def build_result_class_name(vi_name: str) -> str:
    """Build result class name from VI name."""
    base = to_function_name(vi_name)
    # Convert to CamelCase
    parts = base.split("_")
    camel = "".join(p.capitalize() for p in parts if p)
    return f"{camel}Result"

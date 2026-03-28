"""Main code generation builder - traversal and module assembly."""

from __future__ import annotations

import ast
import warnings
from collections import deque
from typing import TYPE_CHECKING, Any

from vipy.graph_types import Operation, Terminal, VIContext

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

if TYPE_CHECKING:
    from vipy.memory_graph import InMemoryVIGraph


def build_module(
    vi_context: VIContext,
    vi_name: str,
    vi_context_lookup: Any = None,
    import_resolver: Any = None,
    has_parallel_branches: bool | None = None,
    graph: InMemoryVIGraph | None = None,
) -> str:
    """Build complete Python module from VI context.

    Args:
        vi_context: VIContext with operations, inputs, outputs, etc.
        vi_name: Name of the VI (used for function name)
        vi_context_lookup: Deprecated, ignored.
        import_resolver: Optional callable (subvi_name) -> import statement string
        has_parallel_branches: If True, enable held error model for parallel
                              branch error handling. If None, reads from vi_context.
        graph: The nx.MultiDiGraph. resolve() walks this directly.

    Returns:
        Python source code as string
    """
    # Initialize context with inputs and constants
    ctx = CodeGenContext.from_vi_context(vi_context, graph=graph)  # InMemoryVIGraph
    ctx.import_resolver = import_resolver
    ctx.vi_name = vi_name

    # Determine if we need error handling infrastructure
    # Read from vi_context if not explicitly passed
    if has_parallel_branches is None:
        has_parallel_branches = vi_context.has_parallel_branches
    use_error_handling = needs_error_handling(has_parallel_branches, vi_context)
    ctx.use_held_error_model = use_error_handling

    # Generate function body
    body: list[ast.stmt] = []

    # Add held error initialization if needed
    if use_error_handling:
        body.append(build_held_error_init())

    # Generate operation code
    body.extend(generate_body(vi_context.operations, ctx))

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

    Uses tiered topological sort to identify parallel groups. Single-op
    tiers emit sequential code. Multi-op tiers are wrapped in
    concurrent.futures.ThreadPoolExecutor.

    Args:
        operations: List of Operation nodes
        ctx: Code generation context

    Returns:
        List of AST statements
    """
    # Register self as the body generator for recursive calls
    # (case frames, loop bodies) without circular imports.
    ctx._body_generator = generate_body

    statements: list[ast.stmt] = []

    # All operations passed here are top-level (inner loop ops are in inner_nodes)
    tiers = topological_sort_tiered(operations, ctx)

    for tier in tiers:
        if len(tier) == 1:
            # Single-op tier — emit as plain statement (no executor overhead)
            node = tier[0]
            codegen = get_codegen(node)
            fragment = codegen.generate(node, ctx)
            statements.extend(fragment.statements)
            ctx.merge(fragment.bindings)
            ctx.imports.update(fragment.imports)
        else:
            # Multi-op tier — wrap in ThreadPoolExecutor
            statements.extend(_generate_parallel_tier(tier, ctx))

    return statements


def _generate_parallel_tier(
    tier: list[Operation], ctx: CodeGenContext
) -> list[ast.stmt]:
    """Generate ThreadPoolExecutor block for parallel operations.

    Each branch function returns its produced values as a tuple.
    Futures capture the returns, and .result() unpacks them into
    the outer scope after the executor block completes.

    LabVIEW semantics:
    - By-value data: branches get copies, return new values via future
    - By-reference data (classes, refnums): shared, mutations visible
      across branches via closure capture
    - Shift registers: loop-scoped state, accessible to all branches
    """
    ctx.imports.add("import concurrent.futures")

    # Generate fragments for each op
    fragments = []
    for node in tier:
        codegen = get_codegen(node)
        fragment = codegen.generate(node, ctx)
        fragments.append(fragment)
        ctx.imports.update(fragment.imports)

    # Build function defs, submit calls, and future captures
    inner_stmts: list[ast.stmt] = []
    # Statements to emit AFTER the with block (future.result() unpacking)
    post_stmts: list[ast.stmt] = []
    # Track which fragments produce bindings that need returning
    branch_info: list[tuple[str, list[str]]] = []  # (future_var, [bound_var_names])

    for fragment in fragments:
        stmts = fragment.statements
        if not stmts:
            continue

        func_name = f"_branch_{ctx._branch_counter}"
        future_var = f"_f{ctx._branch_counter}"
        ctx._branch_counter += 1

        # Collect unique variable names this branch produces.
        # Skip constants/literals/keywords — only actual assignable
        # variable names need returning from the branch.
        bound_vars = list(dict.fromkeys(
            v for v in fragment.bindings.values()
            if v and v.isidentifier()
            and v not in ("None", "True", "False")
            and "." not in v
        ))

        body = list(stmts)

        # Add return statement if branch produces bindings
        if bound_vars:
            if len(bound_vars) == 1:
                return_value = ast.Name(id=bound_vars[0], ctx=ast.Load())
            else:
                return_value = ast.Tuple(
                    elts=[ast.Name(id=v, ctx=ast.Load()) for v in bound_vars],
                    ctx=ast.Load(),
                )
            body.append(ast.Return(value=return_value))

        func_def = ast.FunctionDef(
            name=func_name,
            args=ast.arguments(
                posonlyargs=[], args=[], vararg=None,
                kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
            ),
            body=body,
            decorator_list=[],
            returns=None,
            type_params=[],
        )
        inner_stmts.append(func_def)

        if bound_vars:
            # _fN = _executor.submit(_branch_N)
            inner_stmts.append(ast.Assign(
                targets=[ast.Name(id=future_var, ctx=ast.Store())],
                value=_build_submit(ast.Name(id=func_name, ctx=ast.Load())),
            ))
            branch_info.append((future_var, bound_vars))
        else:
            # Fire-and-forget: _executor.submit(_branch_N)
            inner_stmts.append(ast.Expr(
                value=_build_submit(ast.Name(id=func_name, ctx=ast.Load())),
            ))

    if not inner_stmts:
        return []

    # Build the with statement
    with_stmt = ast.With(
        items=[
            ast.withitem(
                context_expr=ast.Call(
                    func=ast.Attribute(
                        value=ast.Attribute(
                            value=ast.Name(id="concurrent", ctx=ast.Load()),
                            attr="futures",
                            ctx=ast.Load(),
                        ),
                        attr="ThreadPoolExecutor",
                        ctx=ast.Load(),
                    ),
                    args=[],
                    keywords=[],
                ),
                optional_vars=ast.Name(id="_executor", ctx=ast.Store()),
            )
        ],
        body=inner_stmts,
    )

    # Build future.result() unpacking after the with block
    for future_var, bound_vars in branch_info:
        result_expr = ast.Call(
            func=ast.Attribute(
                value=ast.Name(id=future_var, ctx=ast.Load()),
                attr="result",
                ctx=ast.Load(),
            ),
            args=[], keywords=[],
        )
        if len(bound_vars) == 1:
            target = ast.Name(id=bound_vars[0], ctx=ast.Store())
        else:
            target = ast.Tuple(
                elts=[ast.Name(id=v, ctx=ast.Store()) for v in bound_vars],
                ctx=ast.Store(),
            )
        post_stmts.append(ast.Assign(targets=[target], value=result_expr))

    ast.fix_missing_locations(with_stmt)
    for stmt in post_stmts:
        ast.fix_missing_locations(stmt)

    # Merge bindings (terminal→variable mappings for downstream resolution)
    for fragment in fragments:
        ctx.merge(fragment.bindings)

    return [with_stmt] + post_stmts


def _build_submit(callable_expr: ast.expr) -> ast.Call:
    """Build _executor.submit(<callable_expr>) AST node."""
    return ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="_executor", ctx=ast.Load()),
            attr="submit",
            ctx=ast.Load(),
        ),
        args=[callable_expr],
        keywords=[],
    )


def topological_sort_tiered(
    operations: list[Operation], ctx: CodeGenContext
) -> list[list[Operation]]:
    """Sort operations by data dependencies, returning parallel tiers.

    Returns a list of tiers. Each tier contains operations that have no
    data dependencies between them and can execute concurrently. Tiers
    must execute sequentially (each tier depends on prior tiers).
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

    # Build dependencies from data flow.
    # Trace through infrastructure nodes (sRN shift registers, tunnels)
    # to find the actual producing operation. Without this, operations
    # connected through sRN appear independent and land in the same
    # parallel tier, causing unresolved references.
    for op in operations:
        for term in op.terminals:
            if term.direction != "input":
                continue
            # Trace through graph edges to find producing operation
            visited: set[str] = set()
            current = term.id
            while current not in visited:
                visited.add(current)
                source = ctx.get_source(current)
                if not source:
                    break
                src_term = source.src_terminal
                if src_term in output_to_op:
                    dep_op_id = output_to_op[src_term]
                    if dep_op_id != op.id and dep_op_id in dependencies:
                        dependencies[op.id].add(dep_op_id)
                    break
                # Structure tunnels: source terminal's parent node may
                # be an operation even if the terminal isn't an "output".
                src_parent = source.src_parent_id
                if src_parent in op_by_id and src_parent != op.id:
                    dependencies[op.id].add(src_parent)
                    break
                current = src_term

    # Tiered Kahn's algorithm — drain all ready ops per iteration
    tiers: list[list[Operation]] = []
    ready: deque[str] = deque(
        op_id for op_id, deps in dependencies.items() if not deps
    )
    remaining = {op_id: set(deps) for op_id, deps in dependencies.items() if deps}

    while ready:
        # Drain all currently-ready ops into one parallel tier
        tier_ids = list(ready)
        ready.clear()
        tier = [op_by_id[oid] for oid in tier_ids if oid in op_by_id]
        if tier:
            tiers.append(tier)

        # Update remaining dependencies — newly ready ops go into next tier
        for completed_id in tier_ids:
            to_remove = []
            for other_id, deps in remaining.items():
                deps.discard(completed_id)
                if not deps:
                    ready.append(other_id)
                    to_remove.append(other_id)
            for r in to_remove:
                del remaining[r]

    # Add any remaining (circular dependencies) as a final tier
    circular = [op_by_id[oid] for oid in remaining if oid in op_by_id]
    if circular:
        tiers.append(circular)

    return tiers


def build_return_stmt(
    vi_context: VIContext, ctx: CodeGenContext
) -> ast.Return | None:
    """Build return statement for function.

    Returns NamedTuple with output values resolved from context.
    Skips error cluster outputs - Python uses exceptions instead.
    """
    outputs = vi_context.outputs
    if not outputs:
        return None

    result_class = build_result_class_name(vi_context.name)

    # Resolve output values, skipping error clusters
    keywords = []
    for out in outputs:
        if out.is_error_cluster:
            continue

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

    # If all outputs were error clusters, return None
    if not keywords:
        return None

    return ast.Return(
        value=ast.Call(
            func=ast.Name(id=result_class, ctx=ast.Load()),
            args=[],
            keywords=keywords,
        )
    )


def build_module_ast(
    vi_context: VIContext,
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
    vi_context: VIContext,
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
            warnings.warn(f"Skipping unparseable import: {imp!r}", stacklevel=2)

    return imports


def build_result_class(vi_context: VIContext) -> ast.ClassDef | None:
    """Build NamedTuple result class.

    Skips error cluster outputs - Python uses exceptions instead.
    """
    outputs = vi_context.outputs
    if not outputs:
        return None

    class_name = build_result_class_name(vi_context.name)

    # Build fields, skipping error clusters
    fields = []
    for out in outputs:
        if out.is_error_cluster:
            continue

        name = to_var_name(out.name or "output")
        type_hint = out.python_type()
        fields.append((name, type_hint))

    # If all outputs were error clusters, no result class needed
    if not fields:
        return None

    # Build class body with type annotations
    class_body = []
    for name, type_hint in fields:
        ann = ast.AnnAssign(
            target=ast.Name(id=name, ctx=ast.Store()),
            annotation=parse_expr(type_hint),
            simple=1,
        )
        class_body.append(ann)

    if not class_body:
        class_body: list[ast.stmt] = [ast.Pass()]

    return ast.ClassDef(
        name=class_name,
        bases=[ast.Name(id="NamedTuple", ctx=ast.Load())],
        keywords=[],
        body=class_body,
        decorator_list=[],
        type_params=[],
    )


def build_function_def(
    vi_context: VIContext, vi_name: str, body: list[ast.stmt]
) -> ast.FunctionDef:
    """Build function definition."""
    func_name = to_function_name(vi_name)

    # Build arguments
    args = build_args(vi_context.inputs)

    # Build return annotation
    returns = None
    if vi_context.outputs:
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
        type_params=[],
    )


def build_args(inputs: list[Terminal]) -> ast.arguments:
    """Build function arguments from inputs.

    Skips error cluster inputs - Python uses exceptions instead.

    Wiring rules:
    - 0 = unknown (treat as required)
    - 1 = required (no default)
    - 2 = recommended (has default)
    - 3 = optional (has default)
    """
    args = []
    defaults = []

    for inp in inputs:
        if inp.is_error_cluster:
            continue

        name = to_var_name(inp.name or "input")
        type_hint = inp.python_type()

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
    """Get appropriate default value for a LabVIEW type.

    Note: Uses None for mutable types (list, dict) to avoid Python's mutable
    default argument anti-pattern. Callers should handle None appropriately.
    """
    if lv_type is None:
        return ast.Constant(value=None)

    kind = lv_type.kind
    underlying = lv_type.underlying_type

    # Use None for mutable types to avoid mutable default argument anti-pattern
    if kind == "array":
        return ast.Constant(value=None)
    elif kind == "primitive":
        if underlying == "Path":
            return ast.Constant(value=None)
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
        return ast.Constant(value=None)
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

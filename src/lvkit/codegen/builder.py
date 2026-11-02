"""Main code generation builder - traversal and module assembly."""

from __future__ import annotations

import ast
from collections import deque
from collections.abc import Callable

import networkx as nx

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import AnyGraphNode, VIContext, VINode
from lvkit.models import LVTypeKind, Terminal, TypeNamer

from .ast_optimizer import optimize_module
from .ast_utils import (
    build_assign,
    build_result_class_name,
    build_type_checking_block,
    interface_names,
    param_default_expr,
    parse_expr,
    to_function_name,
)
from .context import CodeGenContext
from .elementwise import arrayify_module
from .error_handler import (
    build_held_error_check,
    build_held_error_init,
    build_merge_try_except,
    needs_error_handling,
)
from .error_path import ErrorRole, classify_error_node
from .error_wires import generate_error_handler
from .errors import CodeGenError
from .front_panel import front_panel_docstring, off_pane_locals
from .gaps import Gap, catch_gap, vi_body
from .global_module import build_global_module
from .import_resolver import ImportResolver
from .local_indicators import LocalIndicators
from .nodes import generate as generate_node
from .references import REFERENCE_KINDS, generated_nodes


def build_module(
    vi_context: VIContext,
    vi_name: str,
    import_resolver: ImportResolver | None = None,
    graph: InMemoryVIGraph | None = None,
    data_files: dict[str, bytes] | None = None,
    gaps: list[Gap] | None = None,
) -> str:
    """Build complete Python module from VI context.

    Args:
        vi_context: VIContext with inputs, outputs, etc.
        vi_name: Name of the VI (used for function name)
        import_resolver: Optional callable (subvi_name) -> import statement string
        graph: The nx.MultiDiGraph. resolve() walks this directly.
        data_files: When a dict, every data file the module reads at run time (an
            array too large to write into the module, a library of a Call Library
            Function Node) is added to it by its path from the module's folder; the
            caller writes each there. Without it, a VI that needs
            one fails with an error saying so.
        gaps: When a list, every gap found in the VI (see ``gaps.py``) is added
            to it; the caller prints them as warnings.

    Returns:
        Python source code as string
    """
    # Canonicalize the VI reference to its graph key (source path) ONCE, so every
    # downstream graph lookup (child_nodes, top_level_nodes, var_name_in_use,
    # clear_var_names) keys on the path directly instead of each re-resolving a
    # bare name -- and var_name_in_use no longer silently falls back to scanning
    # the whole graph when handed a bare filename. A no-op when already a key.
    if graph is not None:
        vi_name = graph.resolve_vi_name(vi_name)
        # Clear any var_name scratch a prior build left on the graph's terminals
        # so this build is idempotent (see InMemoryVIGraph.clear_var_names).
        # No-op on a freshly loaded graph.
        graph.clear_var_names(vi_name)

    # A Global VI is its controls: a module of state, not a function.
    if graph is not None and vi_context.properties.is_global_vi:
        vi_node = graph.get_graph_node(vi_name)
        assert isinstance(vi_node, VINode)
        return build_global_module(vi_node, import_resolver, data_files, gaps)

    # Initialize context with inputs and constants
    ctx = CodeGenContext.from_vi_context(
        vi_context,
        graph=graph,
        import_resolver=import_resolver,
        data_files=data_files,
        module_stem=to_function_name(vi_context.name),
        vi_name=vi_name,
        gaps=gaps,
    )
    ctx.function_name = to_function_name(vi_context.name)
    ctx.qualified_vi_name = vi_context.qualified_name

    body = vi_body(ctx, vi_name, lambda: function_body(vi_context, vi_name, ctx))

    # Build module structure
    module = build_module_ast(vi_context, vi_name, body, ctx)

    # Optimize AST (duplicate imports, dead code)
    module = optimize_module(module)

    # Fix locations and unparse
    ast.fix_missing_locations(module)
    return ast.unparse(module)


def function_body(
    vi_context: VIContext,
    vi_name: str,
    ctx: CodeGenContext,
    parameter_names: dict[str, str] | None = None,
    return_stmt: Callable[[], ast.stmt | None] | None = None,
) -> list[ast.stmt]:
    """The statements of the VI's function. ``parameter_names`` (terminal id to
    name) and ``return_stmt`` are given by a caller that names the parameters or
    returns the results itself (a method of a class)."""
    for term in annotated_terminals(vi_context):
        ctx.import_types(term.lv_type, at_run_time=False)

    # Top-level diagram nodes in dataflow order. Emitters walk structure
    # contents from here via ``ctx.child_nodes`` / ``ctx.frame_children``.
    top_nodes: list[AnyGraphNode] = (
        generated_nodes(ctx.graph.top_level_nodes(vi_name, extra_kinds=REFERENCE_KINDS))
        if ctx.graph is not None
        else []
    )

    # The held error model is on when Merge Errors is anywhere in the VI.
    use_error_handling = needs_error_handling(top_nodes, ctx)
    ctx.use_held_error_model = use_error_handling

    # What the function evaluates when it is entered and cannot be written
    # raises first.
    body: list[ast.stmt] = []
    names = (
        parameter_names
        or interface_names(vi_context.inputs, vi_context.off_pane_inputs, []).parameters
    )
    docstring = front_panel_docstring(vi_context, names, ctx.type_namer())
    if docstring is not None:
        body.append(docstring)
    body.extend(ctx.entry_raises)

    # A control that is not on the connector pane is a local variable set to its
    # saved default.
    body.extend(off_pane_locals(vi_context.off_pane_inputs, names))

    # Normalize unwired array inputs: they default to None (a mutable [] default
    # would be shared across calls), so map None -> [] up front, matching
    # LabVIEW's "unwired array input == empty array". `x or []` also folds an
    # already-empty list to [] harmlessly.
    parameters = [*vi_context.inputs, *vi_context.off_pane_inputs]
    body.extend(build_array_input_normalization(parameters, names))

    if use_error_handling:
        ctx.import_labview_error()
        body.append(build_held_error_init())

    # An indicator a Local Variable reads is a variable set to its default here.
    ctx.local_indicators, indicator_defaults = LocalIndicators.declare(ctx, vi_name)
    body.extend(indicator_defaults)

    # Generate operation code
    body.extend(generate_body(top_nodes, ctx))

    # Raise the first held error where the branches have merged
    if use_error_handling:
        body.append(build_held_error_check())

    # Add return statement
    final = (
        return_stmt() if return_stmt is not None else build_return_stmt(vi_context, ctx)
    )
    if final:
        body.append(final)

    # Final pass: write numeric operators over array-valued operands that were
    # inlined (single-use) into a consumer as comprehensions.
    arrayify_module(body, ctx.array_vars)
    return body


def generate_body(
    operations: list[AnyGraphNode], ctx: CodeGenContext
) -> list[ast.stmt]:
    """Generate function body statements from operations.

    Uses tiered topological sort to identify parallel groups. Single-op tiers
    emit sequential code. Under the held error model a multi-op tier is wrapped
    in concurrent.futures.ThreadPoolExecutor, each branch holding its error;
    otherwise independent nodes are emitted sequentially in dataflow order.

    Statements are tagged with the operation IDs that produced them so the
    nodes that handle an error (Clear Errors, an error case structure, ...) can
    wrap the statements that produce the error they handle in a ``try``.

    Args:
        operations: List of graph nodes to generate code for
        ctx: Code generation context

    Returns:
        List of AST statements
    """
    # Register self as the body generator for recursive calls
    # (case frames, loop bodies) without circular imports.
    ctx._body_generator = generate_body

    # Tagged statements: (set of op IDs, statement)
    tagged: list[tuple[set[str], ast.stmt]] = []

    # All operations passed here are top-level (nested structure contents
    # are fetched separately via ctx.child_nodes)
    tiers = topological_sort_tiered(operations, ctx)

    for tier in tiers:
        roles = {op.id: classify_error_node(op) for op in tier}
        handlers = [
            op for op in tier if roles[op.id] not in (ErrorRole.NONE, ErrorRole.MERGE)
        ]
        handler_ids = {op.id for op in handlers}
        remaining = [op for op in tier if op.id not in handler_ids]

        # A ThreadPoolExecutor parallel tier is used ONLY when the held error
        # model needs per-branch error holding. Otherwise independent nodes are
        # emitted SEQUENTIALLY in dataflow order: equivalent for by-value
        # LabVIEW dataflow (independent branches carry no shared mutable
        # state), far more idiomatic Python, and free of the per-branch return
        # wiring that dropped cross-branch inlined references.
        if ctx.use_held_error_model and len(remaining) > 1:
            tier_ids = {op.id for op in remaining}
            for stmt in _generate_parallel_tier(remaining, ctx):
                tagged.append((tier_ids, stmt))
            for op in remaining:
                for stmt in ctx.local_indicators.assignments_after(op, ctx):
                    tagged.append((tier_ids, stmt))
        else:
            for node in remaining:
                fragment = generate_node(node, ctx)
                for s in fragment.statements:
                    tagged.append(({node.id}, s))
                ctx.merge(fragment.bindings)
                ctx.imports.update(fragment.imports)
                for s in ctx.local_indicators.assignments_after(node, ctx):
                    tagged.append(({node.id}, s))

        # A handler wraps the statements that produce the error it handles,
        # which are all in earlier tiers or earlier in this one.
        for op in handlers:
            with catch_gap(op, ctx) as guard:
                handled = generate_error_handler(
                    op, roles[op.id], operations, ctx, tagged
                )
            if guard.fragment is not None:
                fragment = guard.fragment
            elif handled:
                continue
            else:
                fragment = generate_node(op, ctx)
            for s in fragment.statements:
                tagged.append(({op.id}, s))
            ctx.merge(fragment.bindings)
            ctx.imports.update(fragment.imports)
            for s in ctx.local_indicators.assignments_after(op, ctx):
                tagged.append(({op.id}, s))

    return [stmt for _, stmt in tagged]


def _generate_parallel_tier(
    tier: list[AnyGraphNode], ctx: CodeGenContext
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
        fragment = generate_node(node, ctx)
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
        # Include both direct identifiers (e.g., "result") and root
        # variables from dotted bindings (e.g., "result" from
        # "result.field") when the root was assigned in this branch.
        assigned_in_branch = _extract_assigned_names(stmts)
        raw_vars: list[str] = []
        for v in fragment.bindings.values():
            if not v or v in ("None", "True", "False"):
                continue
            root = v.split(".")[0] if "." in v else v
            if root.isidentifier() and root in assigned_in_branch:
                raw_vars.append(root)
        bound_vars = list(dict.fromkeys(raw_vars))

        body = list(stmts)

        # An indicator variable the branch assigns belongs to the function, not
        # to the branch.
        indicator_vars = set(ctx.local_indicators.variables.values())
        shared = sorted(assigned_in_branch & indicator_vars)
        if shared:
            body.insert(0, ast.Nonlocal(names=shared))

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
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=body,
            decorator_list=[],
            returns=None,
        )
        inner_stmts.append(func_def)

        # _fN = _executor.submit(_branch_N); every branch is awaited after the
        # block so an error it raises reaches the caller.
        inner_stmts.append(
            ast.Assign(
                targets=[ast.Name(id=future_var, ctx=ast.Store())],
                value=_build_submit(ast.Name(id=func_name, ctx=ast.Load())),
            )
        )
        branch_info.append((future_var, bound_vars))

    # Always merge bindings — even when all ops produce zero statements.
    # Passthrough primitives (e.g., Variant To Data, inlined Subtract)
    # generate no code but carry critical bindings that downstream
    # tiers depend on for input resolution.
    for fragment in fragments:
        ctx.merge(fragment.bindings)

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

    # Build future.result() unpacking after the with block.
    # When held-error model is active, wrap each .result() call in
    # try/except so exceptions are captured and the first error is
    # raised at the merge point (LabVIEW Merge Errors semantics).
    for future_var, bound_vars in branch_info:
        result_expr = ast.Call(
            func=ast.Attribute(
                value=ast.Name(id=future_var, ctx=ast.Load()),
                attr="result",
                ctx=ast.Load(),
            ),
            args=[],
            keywords=[],
        )
        result_stmt: ast.stmt
        if not bound_vars:
            result_stmt = ast.Expr(value=result_expr)
        elif len(bound_vars) == 1:
            target = ast.Name(id=bound_vars[0], ctx=ast.Store())
            result_stmt = ast.Assign(targets=[target], value=result_expr)
        else:
            target = ast.Tuple(
                elts=[ast.Name(id=v, ctx=ast.Store()) for v in bound_vars],
                ctx=ast.Store(),
            )
            result_stmt = ast.Assign(targets=[target], value=result_expr)

        if ctx.use_held_error_model:
            # Wrap in try/except: _held_error = _held_error or e
            result_var = bound_vars[0] if len(bound_vars) == 1 else None
            post_stmts.append(build_merge_try_except(result_stmt, result_var))
        else:
            post_stmts.append(result_stmt)

    ast.fix_missing_locations(with_stmt)
    for stmt in post_stmts:
        ast.fix_missing_locations(stmt)

    return [with_stmt] + post_stmts


class _AssignedNameVisitor(ast.NodeVisitor):
    """Collects Name targets of Assign/AnnAssign (each name of an unpacking
    target too) anywhere in a statement tree, including inside nested blocks
    (while/for/if/with/try) -- e.g. a shift-register update assigned inside a
    while loop's body. Does NOT
    descend into nested function/lambda defs, which introduce their own
    scope (a name assigned there is not a name of the enclosing branch)."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def _add_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.names.add(target.id)
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._add_target(element)
        elif isinstance(target, ast.Starred):
            self._add_target(target.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._add_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.names.add(node.target.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # New scope -- do not descend.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass  # New scope -- do not descend.

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass  # New scope -- do not descend.


def _extract_assigned_names(stmts: list[ast.stmt]) -> set[str]:
    """Extract variable names assigned anywhere in a list of statements.

    Walks into nested blocks (while/for/if/with/try) since a value bound
    by a node's CodeFragment may be assigned inside a loop body it
    generated (e.g. a shift-register update) rather than as a top-level
    statement of the fragment.
    """
    visitor = _AssignedNameVisitor()
    for stmt in stmts:
        visitor.visit(stmt)
    return visitor.names


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
    operations: list[AnyGraphNode], ctx: CodeGenContext
) -> list[list[AnyGraphNode]]:
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
            # Trace through graph edges to find producing operation. The trace
            # stays in the terminal's own VI: a subVI call is connected to the
            # called VI's terminals, and what feeds those belongs to other VIs.
            graph = ctx.graph
            home = graph.terminal_vi(term.id) if graph else None
            visited: set[str] = set()
            current = term.id
            while current not in visited:
                visited.add(current)
                source = ctx.get_source(current)
                if not source:
                    break
                src_term = source.src_terminal
                # The VI's own control is where a value starts: a wire that
                # also reaches the control (a local variable write) is not a
                # dependency of the nodes reading it.
                if source.src_parent_id == ctx.vi_name:
                    break
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
                if graph is None or graph.terminal_vi(src_term) != home:
                    break
                current = src_term

    # Tiered Kahn's algorithm — drain all ready ops per iteration
    tiers: list[list[AnyGraphNode]] = []
    ready: deque[str] = deque(op_id for op_id, deps in dependencies.items() if not deps)
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
        _record_cycles(circular, remaining, op_by_id, ctx)

    return tiers


def _record_cycles(
    circular: list[AnyGraphNode],
    remaining: dict[str, set[str]],
    op_by_id: dict[str, AnyGraphNode],
    ctx: CodeGenContext,
) -> None:
    """Record, for each node on a cycle of the wires at this structure level, why
    it cannot be ordered. A node that only waits on a cycle is not on one."""
    wires = nx.DiGraph()
    for op_id, deps in remaining.items():
        for dep in deps:
            wires.add_edge(dep, op_id)
    for component in nx.strongly_connected_components(wires):
        if len(component) < 2:
            continue
        names = sorted(
            op_by_id[op_id].display_name for op_id in component if op_id in op_by_id
        )
        reason = (
            "The wires between "
            + ", ".join(repr(name) for name in names)
            + " read as a cycle, which a saved, runnable VI cannot have; lvkit is "
            "likely misreading a terminal's direction here"
        )
        for op_id in component:
            ctx.circular_nodes[op_id] = reason


def build_return_stmt(vi_context: VIContext, ctx: CodeGenContext) -> ast.Return | None:
    """Build return statement for function.

    Returns the NamedTuple of the connector-pane outputs, resolved from context.
    Skips error cluster outputs - Python uses exceptions instead.
    """
    outputs = vi_context.outputs
    if not outputs:
        return None

    result_class = build_result_class_name(vi_context.name)
    field_names = interface_names(
        vi_context.inputs, vi_context.off_pane_inputs, outputs
    ).fields

    # Resolve output values, skipping error clusters
    vi_node = (
        ctx.graph.get_graph_node(vi_context.name) if ctx.graph is not None else None
    )
    keywords = []
    for out in outputs:
        if out.is_error_cluster:
            continue

        out_id = out.id
        var_name = field_names[out_id]

        # Try to resolve from context. resolve() returns an EXPRESSION string
        # (often compound, e.g. "low_000 + product" from an inlined output), so
        # parse it into a real AST — a bare ast.Name(id=expr) is malformed and
        # hides the variable loads from later passes (dead-code elimination then
        # deletes the assignments the return depends on).
        value = (
            ctx.wired_input(out_id, vi_node)
            if vi_node is not None
            else ctx.resolve(out_id)
        )
        if value:
            value_ast: ast.expr = parse_expr(value)
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
) -> ast.Module:
    """Build complete module AST."""
    module_body: list[ast.stmt] = []

    # Result class
    spell = ctx.type_namer()
    result_class = build_result_class(vi_context, spell)
    if result_class:
        module_body.append(result_class)

    # Module-level helper functions generated for Formula Nodes.
    module_body.extend(ctx.formula_helpers)

    # Module-level declarations of nodes: ctypes libraries, prototypes,
    # structures and helper functions.
    module_body.extend(ctx.module_declarations.statements)

    # Module-level globals for persistent per-call-site state
    # (uninitialized shift registers, First Call?). Sorted by name for
    # deterministic output — dict iteration order is insertion order, but
    # sorting removes any dependence on traversal order across the graph.
    for name in sorted(ctx.module_globals):
        module_body.append(ctx.module_globals[name])

    # Function definition
    func_def = build_function_def(vi_context, vi_name, body, spell)
    module_body.append(func_def)

    # ``os`` is imported when a path expression in the module spells its volume
    # for the running OS. A bare ``os`` name can only be that module: generated
    # variable and function names never take a module's name (``to_var_name``).
    if any(
        isinstance(node, ast.Name) and node.id == "os"
        for stmt in module_body
        for node in ast.walk(stmt)
    ):
        ctx.add_import("import os")

    imports = build_imports(vi_context, ctx)
    return ast.Module(body=[*imports, *module_body], type_ignores=[])


def build_imports(
    vi_context: VIContext,
    ctx: CodeGenContext,
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

    # Common imports (hardcoded — no try/except needed)
    common = ["from pathlib import Path", "from typing import Any, NamedTuple"]
    for imp in common:
        tree = ast.parse(imp)
        imports.extend(tree.body)

    # Context-accumulated imports
    for imp in sorted(ctx.imports):
        try:
            tree = ast.parse(imp)
            imports.extend(tree.body)
        except SyntaxError as e:
            raise CodeGenError(f"Unparseable import: {imp!r} — {e}") from e

    imports.extend(build_type_checking_block(ctx.annotation_imports))
    return imports


def annotated_terminals(vi_context: VIContext) -> list[Terminal]:
    """The terminals whose types the module annotates: the parameters and the
    result fields. An error cluster is an exception, so it is neither."""
    return [
        term
        for term in (
            *vi_context.inputs,
            *vi_context.off_pane_inputs,
            *vi_context.outputs,
        )
        if not term.is_error_cluster
    ]


def has_result_fields(vi_context: VIContext) -> bool:
    """True when the VI returns a value: some connector-pane indicator is not an
    error cluster (an error is an exception, so it has no result field)."""
    return any(not out.is_error_cluster for out in vi_context.outputs)


def build_result_class(
    vi_context: VIContext, spell: TypeNamer | None = None
) -> ast.ClassDef | None:
    """Build NamedTuple result class.

    Skips error cluster outputs - Python uses exceptions instead.
    """
    if not has_result_fields(vi_context):
        return None
    outputs = vi_context.outputs

    class_name = build_result_class_name(vi_context.name)

    field_names = interface_names(
        vi_context.inputs, vi_context.off_pane_inputs, outputs
    ).fields

    # Build fields, skipping error clusters
    fields = []
    for out in outputs:
        if out.is_error_cluster:
            continue

        name = field_names[out.id]
        type_hint = out.python_type(spell)
        fields.append((name, type_hint))

    if len({name for name, _ in fields}) != len(fields):
        raise ValueError("Output labels collide after Python field normalization")

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
    )


def build_function_def(
    vi_context: VIContext,
    vi_name: str,
    body: list[ast.stmt],
    spell: TypeNamer | None = None,
) -> ast.FunctionDef:
    """Build function definition."""
    # Name the function by the VI's DISPLAY name, not the raw vi_name (which in
    # single-VI generation is a filesystem path → a giant path-mangled name).
    # Callers import/call it as to_function_name(node.name) == the display name,
    # and the result class + return use vi_context.name too, so this keeps the
    # def, the return annotation, the call sites, and the imports all consistent.
    func_name = to_function_name(vi_context.name)

    # Build arguments
    args = build_args(vi_context.inputs, spell=spell)

    # Build return annotation
    returns = None
    if has_result_fields(vi_context):
        result_class = build_result_class_name(vi_context.name)
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


def build_args(
    inputs: list[Terminal],
    names: dict[str, str] | None = None,
    spell: TypeNamer | None = None,
) -> ast.arguments:
    """The parameters of a VI's function: its connector-pane ``inputs`` only.

    A Required input has no default and is positional; a Recommended or Optional
    one is keyword-only with the control's saved default (what the input holds
    when a caller leaves it unwired). Each group keeps connector-pane order.
    Front-panel controls that are not on the connector pane are not parameters
    (``off_pane_locals``).

    ``names`` maps terminal id -> parameter name (``interface_names``); when
    omitted it is computed over ``inputs``. Error clusters are skipped: errors are
    exceptions in Python.
    """
    if names is None:
        names = interface_names(inputs, [], []).parameters

    def argument(term: Terminal) -> ast.arg:
        # Arrays and anonymous clusters use a conservative None default.
        type_hint = term.python_type(spell)
        if _is_array_input(term) or _is_anonymous_cluster(term):
            type_hint = f"{type_hint} | None"
        return ast.arg(arg=names[term.id], annotation=parse_expr(type_hint))

    parameters = [t for t in inputs if not t.is_error_cluster]
    required = [t for t in parameters if is_required(t, "input")]
    optional = [t for t in parameters if not is_required(t, "input")]
    return ast.arguments(
        posonlyargs=[],
        args=[argument(t) for t in required],
        vararg=None,
        kwonlyargs=[argument(t) for t in optional],
        kw_defaults=[param_default_expr(t) for t in optional],
        kwarg=None,
        defaults=[],
    )


def _is_array_input(inp: Terminal) -> bool:
    """True for a non-error array/list-typed input parameter."""
    return (
        not inp.is_error_cluster
        and inp.lv_type is not None
        and inp.lv_type.kind == LVTypeKind.ARRAY
    )


def build_array_input_normalization(
    inputs: list[Terminal], names: dict[str, str]
) -> list[ast.stmt]:
    """`name = name or []` for each array input, so an unwired (None-defaulted)
    array behaves as LabVIEW's empty array instead of crashing on iteration."""
    stmts: list[ast.stmt] = []
    for inp in inputs:
        if not _is_array_input(inp):
            continue
        name = names[inp.id]
        stmts.append(build_assign(name, parse_expr(f"{name} or []")))
    return stmts


def _is_anonymous_cluster(term: Terminal) -> bool:
    lv_type = term.lv_type
    return (
        lv_type is not None
        and lv_type.kind == LVTypeKind.CLUSTER
        and not lv_type.typedef_name
        and not lv_type.classname
    )

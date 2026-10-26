"""Main code generation builder - traversal and module assembly."""

from __future__ import annotations

import ast
import warnings
from collections import deque

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import AnyGraphNode, VIContext, VINode
from lvkit.models import LVTypeKind, Terminal, TypeNamer

from .ast_optimizer import optimize_module
from .ast_utils import (
    build_assign,
    build_type_checking_block,
    param_default_expr,
    parameter_names,
    parse_expr,
    result_field_names,
    to_function_name,
)
from .context import CodeGenContext
from .elementwise import LV_IMPORT, arrayify_module
from .error_path import ErrorRole, classify_error_node
from .error_wires import generate_error_handler
from .global_module import build_global_module
from .import_resolver import ImportResolver
from .nodes import generate as generate_node


def build_module(
    vi_context: VIContext,
    vi_name: str,
    import_resolver: ImportResolver | None = None,
    graph: InMemoryVIGraph | None = None,
    soft_unresolved: bool = False,
    unresolved_sink: list[Exception] | None = None,
    data_files: dict[str, bytes] | None = None,
) -> str:
    """Build complete Python module from VI context.

    Args:
        vi_context: VIContext with inputs, outputs, etc.
        vi_name: Name of the VI (used for function name)
        import_resolver: Optional callable (subvi_name) -> import statement string
        graph: The nx.MultiDiGraph. resolve() walks this directly.
        soft_unresolved: When True, unknown primitives / vi.lib VIs are
            emitted as inline `raise PrimitiveResolutionNeeded(...)` /
            `raise VILibResolutionNeeded(...)` statements instead of
            failing the build. Generated Python is syntactically valid;
            running it raises the same exception that hard mode would
            raise at codegen time.
        unresolved_sink: When a list, each resolution gap (unknown primitive /
            unmapped vi.lib VI) is appended to it as the constructed exception.
            `lvkit unresolved` uses this to batch-collect every gap in one pass.
        data_files: When a dict, every data file the module reads at run time (an
            array too large to write into the module) is added to it by file name;
            the caller writes each beside the module. Without it, a VI that needs
            one fails with an error saying so.

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
        return build_global_module(vi_node, import_resolver, data_files)

    # Initialize context with inputs and constants
    ctx = CodeGenContext.from_vi_context(
        vi_context,
        graph=graph,
        import_resolver=import_resolver,
        data_files=data_files,
        module_stem=to_function_name(vi_context.name),
    )
    ctx.vi_name = vi_name
    ctx.qualified_vi_name = vi_context.qualified_name
    ctx.soft_unresolved = soft_unresolved
    ctx.unresolved_sink = unresolved_sink
    for term in annotated_terminals(vi_context):
        ctx.import_types(term.lv_type, at_run_time=False)

    # Top-level diagram nodes in dataflow order. Emitters walk structure
    # contents from here via ``ctx.child_nodes`` / ``ctx.frame_children``.
    top_nodes: list[AnyGraphNode] = (
        ctx.graph.top_level_nodes(vi_name) if ctx.graph is not None else []
    )

    # Generate function body
    body: list[ast.stmt] = []

    # Normalize unwired array inputs: they default to None (a mutable [] default
    # would be shared across calls), so map None -> [] up front, matching
    # LabVIEW's "unwired array input == empty array". `x or []` also folds an
    # already-empty list to [] harmlessly.
    parameters = [*vi_context.inputs, *vi_context.off_pane_inputs]
    body.extend(
        build_array_input_normalization(parameters, parameter_names(parameters))
    )

    # Generate operation code
    body.extend(generate_body(top_nodes, ctx))

    # Final pass: broadcast numeric operators over array-valued operands that
    # were inlined (single-use) past the per-node element-wise hook.
    if arrayify_module(body, frozenset(ctx.array_vars)):
        ctx.imports.add(LV_IMPORT)

    # Add return statement
    return_stmt = build_return_stmt(vi_context, ctx)
    if return_stmt:
        body.append(return_stmt)

    # Build module structure
    module = build_module_ast(vi_context, vi_name, body, ctx)

    # Optimize AST (duplicate imports, dead code)
    module = optimize_module(module)

    # Fix locations and unparse
    ast.fix_missing_locations(module)
    return ast.unparse(module)


def generate_body(
    operations: list[AnyGraphNode], ctx: CodeGenContext
) -> list[ast.stmt]:
    """Generate function body statements from operations.

    Operations are emitted sequentially in dataflow order; independent nodes
    carry no shared state, and an exception raised by one propagates.

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

    def emit(node: AnyGraphNode) -> None:
        fragment = generate_node(node, ctx)
        for s in fragment.statements:
            tagged.append(({node.id}, s))
        ctx.merge(fragment.bindings)
        ctx.imports.update(fragment.imports)

    for tier in tiers:
        roles = {op.id: classify_error_node(op, ctx) for op in tier}
        handlers = [
            op for op in tier if roles[op.id] not in (ErrorRole.NONE, ErrorRole.MERGE)
        ]
        handler_ids = {op.id for op in handlers}
        for node in tier:
            if node.id not in handler_ids:
                emit(node)
        # A handler wraps the statements that produce the error it handles,
        # which are all in earlier tiers or earlier in this one.
        for op in handlers:
            if not generate_error_handler(op, roles[op.id], operations, ctx, tagged):
                emit(op)

    return [stmt for _, stmt in tagged]


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

    return tiers


def build_return_stmt(vi_context: VIContext, ctx: CodeGenContext) -> ast.Return | None:
    """Build return statement for function.

    Returns NamedTuple with output values resolved from context.
    Skips error cluster outputs - Python uses exceptions instead.
    """
    outputs = [*vi_context.outputs, *vi_context.off_pane_outputs]
    if not outputs:
        return None

    result_class = build_result_class_name(vi_context.name)
    field_names = result_field_names(vi_context.outputs, vi_context.off_pane_outputs)

    # Resolve output values, skipping error clusters
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
        value = ctx.resolve(out_id)
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

    # Imports
    module_body.extend(build_imports(vi_context, ctx))

    # Result class
    spell = ctx.type_namer()
    result_class = build_result_class(vi_context, spell)
    if result_class:
        module_body.append(result_class)

    # Module-level helper functions generated for Formula Nodes.
    module_body.extend(ctx.formula_helpers)

    # Module-level globals for persistent per-call-site state
    # (uninitialized shift registers, First Call?). Sorted by name for
    # deterministic output — dict iteration order is insertion order, but
    # sorting removes any dependence on traversal order across the graph.
    for name in sorted(ctx.module_globals):
        module_body.append(ctx.module_globals[name])

    # Function definition
    func_def = build_function_def(vi_context, vi_name, body, spell)
    module_body.append(func_def)

    return ast.Module(body=module_body, type_ignores=[])


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
        except SyntaxError:
            warnings.warn(f"Skipping unparseable import: {imp!r}", stacklevel=2)

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
            *vi_context.off_pane_outputs,
        )
        if not term.is_error_cluster
    ]


def has_result_fields(vi_context: VIContext) -> bool:
    """True when the VI returns a value: some indicator is not an error cluster
    (an error is an exception, so it has no result field)."""
    return any(
        not out.is_error_cluster
        for out in (*vi_context.outputs, *vi_context.off_pane_outputs)
    )


def build_result_class(
    vi_context: VIContext, spell: TypeNamer | None = None
) -> ast.ClassDef | None:
    """Build NamedTuple result class.

    Skips error cluster outputs - Python uses exceptions instead.
    """
    if not has_result_fields(vi_context):
        return None
    outputs = [*vi_context.outputs, *vi_context.off_pane_outputs]

    class_name = build_result_class_name(vi_context.name)

    field_names = result_field_names(vi_context.outputs, vi_context.off_pane_outputs)

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
    args = build_args(vi_context.inputs, vi_context.off_pane_inputs, spell=spell)

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
    off_pane_inputs: list[Terminal] | None = None,
    names: dict[str, str] | None = None,
    spell: TypeNamer | None = None,
) -> ast.arguments:
    """Build function arguments from inputs.

    ``off_pane_inputs`` (front-panel controls that are not on the connector
    pane) become keyword-only parameters after the connector-pane ones, each
    defaulting to the control's saved default -- the value such a control holds
    when LabVIEW calls the VI as a subVI.

    ``names`` maps terminal id -> parameter name (``parameter_names`` over every
    control of the VI); when omitted it is computed over ``inputs`` and
    ``off_pane_inputs``.

    Skips error cluster inputs - Python uses exceptions instead.

    Every input gets a default, in connector-pane order. In LabVIEW any input
    terminal may be left unwired and then takes its own default value, so every
    generated parameter is optional-with-default — which also sidesteps Python's
    "non-default argument follows default argument" rule without reordering the
    parameters (reordering would desync positional callers, e.g. class-method
    dynamic dispatch, which pass arguments in connector-pane order). The default
    is the terminal's real connector-pane default when known, else the type
    default (e.g. 0 / '' / None).
    """
    args = []
    defaults = []
    if names is None:
        names = parameter_names([*inputs, *(off_pane_inputs or [])])

    for inp in inputs:
        if inp.is_error_cluster:
            continue

        # Arrays and anonymous clusters use a conservative None default.
        type_hint = inp.python_type(spell)
        if _is_array_input(inp) or _is_anonymous_cluster(inp):
            type_hint = f"{type_hint} | None"
        args.append(
            ast.arg(
                arg=names[inp.id],
                annotation=parse_expr(type_hint),
            )
        )
        defaults.append(param_default_expr(inp))

    kwonly_args = []
    kw_defaults: list[ast.expr | None] = []
    for ctl in off_pane_inputs or []:
        if ctl.is_error_cluster:
            continue
        type_hint = ctl.python_type(spell)
        if _is_array_input(ctl) or _is_anonymous_cluster(ctl):
            type_hint = f"{type_hint} | None"
        kwonly_args.append(
            ast.arg(
                arg=names[ctl.id],
                annotation=parse_expr(type_hint),
            )
        )
        kw_defaults.append(param_default_expr(ctl))

    return ast.arguments(
        posonlyargs=[],
        args=args,
        vararg=None,
        kwonlyargs=kwonly_args,
        kw_defaults=kw_defaults,
        kwarg=None,
        defaults=defaults,
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


def build_result_class_name(vi_name: str) -> str:
    """Build result class name from VI name."""
    base = to_function_name(vi_name)
    # Convert to CamelCase
    parts = base.split("_")
    camel = "".join(p.capitalize() for p in parts if p)
    return f"{camel}Result"

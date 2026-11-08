"""Main code generation builder - traversal and module assembly."""

from __future__ import annotations

import ast
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

import networkx as nx

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import AnyGraphNode, VIContext, VINode
from lvkit.models import LVTypeKind, Terminal, TypeNamer

from .ast_optimizer import optimize_module
from .ast_utils import (
    RESULT_CLASS_IMPORT,
    build_assign,
    build_result_class_name,
    build_type_checking_block,
    control_default_expr,
    implicit_imports,
    interface_names,
    parameter_default_expr,
    parse_expr,
    result_class_def,
    terminal_annotation,
    to_function_name,
    with_statements_after_docstring,
)
from .context import CodeGenContext
from .elementwise import arrayify_module
from .error_flow import share_guards
from .errors import CodeGenError
from .front_panel import front_panel_docstring, off_pane_locals
from .gaps import Gap, vi_body
from .global_module import build_global_module
from .import_cycles import imports_inside, in_import_cycle
from .import_resolver import ImportResolver
from .local_indicators import LocalIndicators
from .nodes import generate as generate_node
from .references import REFERENCE_KINDS, generated_nodes

if TYPE_CHECKING:
    from .class_builder.shared_state import ModuleParts


def build_module(
    vi_context: VIContext,
    vi_name: str,
    import_resolver: ImportResolver | None = None,
    graph: InMemoryVIGraph | None = None,
    data_files: dict[str, bytes] | None = None,
    gaps: list[Gap] | None = None,
    shared: ModuleParts | None = None,
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
        shared: The module-level parts of a module that holds several VIs (the
            variants of a polymorphic VI): their helpers, declarations and
            globals are named against one another, never twice.

    Returns:
        Python source code as string
    """
    if graph is not None:
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
    if shared is not None:
        shared.share_with(ctx)

    body = vi_body(ctx, vi_name, lambda: function_body(vi_context, vi_name, ctx))
    if graph is not None and in_import_cycle(graph, vi_name):
        body, ctx.imports = imports_inside(body, ctx.imports)

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
        if term.is_error_cluster:
            ctx.import_labview_error(at_run_time=False)

    # Top-level diagram nodes in dataflow order. Emitters walk structure
    # contents from here via ``ctx.child_nodes`` / ``ctx.frame_children``.
    top_nodes: list[AnyGraphNode] = (
        generated_nodes(ctx.graph.top_level_nodes(vi_name, extra_kinds=REFERENCE_KINDS))
        if ctx.graph is not None
        else []
    )

    # What the function evaluates when it is entered and cannot be written
    # raises first.
    body: list[ast.stmt] = []
    names = (
        parameter_names
        or interface_names(vi_context.inputs, vi_context.off_pane_inputs, []).parameters
    )
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

    # The indicators held in variables start here (``LocalIndicators``).
    ctx.local_indicators, indicator_starts = LocalIndicators.declare(ctx, vi_name)
    body.extend(indicator_starts)

    # Generate operation code
    body.extend(generate_body(top_nodes, ctx))
    body.extend(ctx.local_indicators.statements_at_end())

    # The docstring opens the function; it names the Variant parameters the body
    # turned out to read as lookup tables.
    docstring = front_panel_docstring(
        vi_context, names, ctx.type_namer(), sorted(ctx.lookup_parameters)
    )
    if docstring is not None:
        body.insert(0, docstring)

    # Add return statement
    final = (
        return_stmt() if return_stmt is not None else build_return_stmt(vi_context, ctx)
    )
    if final:
        body.append(final)

    # Final pass: write numeric operators over array-valued operands that were
    # inlined (single-use) into a consumer as comprehensions.
    arrayify_module(body, ctx.array_vars)
    # The imports the function makes inside its body (``inside_imports``).
    return with_statements_after_docstring(
        [stmt for imp in sorted(ctx.inside_imports) for stmt in ast.parse(imp).body],
        body,
    )


def generate_body(
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    frame: tuple[str, str | int | None] | None = None,
) -> list[ast.stmt]:
    """The statements of ``operations`` (the nodes of one diagram), in dataflow
    order: each tier of the dependency sort, its nodes one after another. Nodes
    of one tier do not depend on each other, so any order is LabVIEW's; the
    nodes skipped on one error share one ``if`` (``error_flow.share_guards``).
    The diagram of a structure ``frame`` (the structure and its frame key)
    starts with the writes of the indicators its inside faces are wired to.

    Args:
        operations: List of graph nodes to generate code for
        ctx: Code generation context
        frame: The structure frame the diagram is, ``None`` for the VI's own

    Returns:
        List of AST statements
    """
    # Register self as the body generator for recursive calls
    # (case frames, loop bodies) without circular imports.
    ctx._body_generator = generate_body

    statements: list[ast.stmt] = []
    if frame is not None:
        statements.extend(ctx.local_indicators.writes_in_frame(frame, ctx))
    for tier in topological_sort_tiered(operations, ctx):
        for node in tier:
            fragment = generate_node(node, ctx)
            statements.extend(fragment.statements)
            ctx.merge(fragment.bindings)
            ctx.imports.update(fragment.imports)
            statements.extend(ctx.local_indicators.assignments_after(node, ctx))
    return share_guards(statements)


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


def output_value(
    out: Terminal, vi_node: AnyGraphNode | None, ctx: CodeGenContext
) -> ast.expr:
    """The value a connector-pane output returns: the value wired to its
    indicator; for an indicator wired to nothing, the indicator's default (its
    saved default, else its type's); ``None`` for a wire whose value a gap
    leaves unknown."""
    # resolve() returns an EXPRESSION string (often compound, e.g.
    # "low_000 + product" from an inlined output), so parse it into a real AST —
    # a bare ast.Name(id=expr) is malformed and hides the variable loads from
    # later passes (dead-code elimination then deletes the assignments the
    # return depends on).
    value = (
        ctx.wired_input(out.id, vi_node) if vi_node is not None else ctx.resolve(out.id)
    )
    if value:
        return parse_expr(value)
    if ctx.is_wired(out.id):
        return ast.Constant(value=None)
    return control_default_expr(out)


def build_return_stmt(vi_context: VIContext, ctx: CodeGenContext) -> ast.Return | None:
    """The return of the VI's function: its result class built from the value
    each connector-pane output returns (``output_value``); nothing when the pane
    has no output."""
    outputs = vi_context.outputs
    if not outputs:
        return None

    result_class = build_result_class_name(vi_context.name)
    field_names = interface_names(
        vi_context.inputs, vi_context.off_pane_inputs, outputs
    ).fields

    vi_node = (
        ctx.graph.get_graph_node(vi_context.name) if ctx.graph is not None else None
    )
    keywords = [
        ast.keyword(arg=field_names[out.id], value=output_value(out, vi_node, ctx))
        for out in outputs
    ]

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
        module_body.extend(ctx.module_globals[name])

    # Function definition
    func_def = build_function_def(vi_context, vi_name, body, spell)
    module_body.append(func_def)

    for statement in implicit_imports(module_body):
        ctx.add_import(statement)

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
    common = [RESULT_CLASS_IMPORT, "from pathlib import Path", "from typing import Any"]
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

    imports.extend(build_type_checking_block(ctx.annotation_imports - ctx.imports))
    return imports


def annotated_terminals(vi_context: VIContext) -> list[Terminal]:
    """The terminals whose types the module annotates: the parameters, the
    off-pane controls and the result fields."""
    return [*vi_context.inputs, *vi_context.off_pane_inputs, *vi_context.outputs]


def build_result_class(
    vi_context: VIContext, spell: TypeNamer | None = None
) -> ast.ClassDef | None:
    """The result class of the VI's function, one field per connector-pane
    output; None when the pane has no output."""
    if not vi_context.outputs:
        return None
    field_names = interface_names(
        vi_context.inputs, vi_context.off_pane_inputs, vi_context.outputs
    ).fields
    return result_class_def(
        build_result_class_name(vi_context.name),
        [
            (field_names[out.id], parse_expr(terminal_annotation(out, spell)))
            for out in vi_context.outputs
        ],
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
    if vi_context.outputs:
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
    omitted it is computed over ``inputs``. An error in is a parameter like any
    other, annotated ``LabVIEWError | None``.
    """
    if names is None:
        names = interface_names(inputs, [], []).parameters

    def argument(term: Terminal) -> ast.arg:
        # Arrays and anonymous clusters use a conservative None default.
        type_hint = terminal_annotation(term, spell)
        if _is_array_input(term) or _is_anonymous_cluster(term):
            type_hint = f"{type_hint} | None"
        return ast.arg(arg=names[term.id], annotation=parse_expr(type_hint))

    required = [t for t in inputs if is_required(t, "input")]
    optional = [t for t in inputs if not is_required(t, "input")]
    return ast.arguments(
        posonlyargs=[],
        args=[argument(t) for t in required],
        vararg=None,
        kwonlyargs=[argument(t) for t in optional],
        kw_defaults=[parameter_default_expr(t) for t in optional],
        kwarg=None,
        defaults=[],
    )


def _is_array_input(inp: Terminal) -> bool:
    """True for an array/list-typed input parameter."""
    return inp.lv_type is not None and inp.lv_type.kind == LVTypeKind.ARRAY


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
        and not term.is_error_cluster
    )

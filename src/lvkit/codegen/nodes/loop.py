"""Code generator for loop structures (while, for)."""

from __future__ import annotations

import ast

from lvkit.models import LoopOperation, LVType, Operation, Tunnel

from ..ast_utils import (
    build_assign,
    default_value_expr,
    parse_expr,
    sanitize_state_var_suffix,
    to_var_name,
)
from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: LoopOperation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a loop structure."""
    loop_type = node.loop_type or "whileLoop"
    tunnels = node.tunnels
    inner_nodes = node.inner_nodes

    pre_loop_stmts: list[ast.stmt] = []
    bindings: dict[str, str] = {}

    # Create child context for loop interior (increment depth for nested loops)
    inner_ctx = ctx.child(increment_loop_depth=True)

    # Track shift register variable names for update statements
    shift_reg_vars: dict[str, str] = {}  # lSR outer_terminal -> var_name
    # (global_name, lsr_outer_term, fallback_shift_var) for uninitialized
    # SRs -- written back to the module global after the loop so the next
    # VI call sees them. The final value used is looked up after step 5
    # (falls back to fallback_shift_var if the paired rSR never got a
    # binding, i.e. the SR is truly never written to).
    uninitialized_sr_writebacks: list[tuple[str, str, str]] = []

    # Terminal lookup for uninitialized shift registers: the outer tunnel
    # terminal carries its own lv_type (from the connector-pane XML) even
    # when nothing is wired in, so we don't need to trace data flow to
    # find a default value.
    term_by_id = {t.id: t for t in node.terminals}

    # 1. Process INPUT tunnels (lSR, lpTun)
    for tunnel in tunnels:
        tunnel_type = tunnel.tunnel_type
        outer_term = tunnel.outer_terminal_uid
        inner_term = tunnel.inner_terminal_uid

        if not outer_term or not inner_term:
            continue

        if tunnel_type == "lSR":
            # Shift register: init variable from outer, bind inner
            outer_var = ctx.resolve(outer_term)
            if outer_var:
                shift_var = _unique_shift_var_name(_make_var_name(tunnel, ctx), ctx)
                pre_loop_stmts.append(
                    build_assign(shift_var, parse_expr(outer_var))
                )
                inner_ctx.bind(inner_term, shift_var)
                shift_reg_vars[outer_term] = shift_var
            else:
                # Uninitialized shift register: nothing wired into the
                # left terminal from outside the loop. In LabVIEW this is
                # the LV2/functional-global idiom -- the value PERSISTS
                # ACROSS CALLS to the VI. Lower to a module-level global,
                # seeded to the SR's type default, that this call site
                # reads at loop entry and writes back after the loop.
                global_name = (
                    f"_lv_state_{sanitize_state_var_suffix(outer_term)}"
                )
                sr_lv_type = None
                outer_sr_term = term_by_id.get(outer_term)
                inner_sr_term = term_by_id.get(inner_term)
                if outer_sr_term is not None:
                    sr_lv_type = outer_sr_term.lv_type
                elif inner_sr_term is not None:
                    sr_lv_type = inner_sr_term.lv_type
                ctx.add_module_global(global_name, default_value_expr(sr_lv_type))

                shift_var = _unique_shift_var_name(_make_var_name(tunnel, ctx), ctx)
                pre_loop_stmts.append(ast.Global(names=[global_name]))
                pre_loop_stmts.append(
                    build_assign(shift_var, ast.Name(id=global_name, ctx=ast.Load()))
                )
                inner_ctx.bind(inner_term, shift_var)
                shift_reg_vars[outer_term] = shift_var
                # Resolved to the paired rSR's FINAL bound variable once
                # step 5 runs (falls back to shift_var, unmodified, if
                # this SR is never written to -- see step 5's docstring
                # for why the rSR update does not mutate shift_var itself).
                uninitialized_sr_writebacks.append((global_name, outer_term, shift_var))

        elif tunnel_type == "lpTun":
            # Check if input tunnel (outer has a source)
            outer_var = ctx.resolve(outer_term)
            if outer_var:
                # While loops: pass the whole value through
                # For loops: defer binding until we know enumerate vs range
                if loop_type != "forLoop":
                    inner_ctx.bind(inner_term, outer_var)
                # For forLoop lpTun, binding happens after we determine loop style

    # 2. Process OUTPUT tunnels - set up accumulators for lMax and lpTun (For loops)
    # Note: lMax can be either:
    # - The N terminal (iteration count) - outer has input, inner has no input
    # - Auto-indexed output (accumulator) - inner receives values from loop body
    #
    # For For loops, lpTun OUTPUT tunnels are also auto-indexed by default
    # (they accumulate into arrays, not just return last value)
    accum_tunnels: list[tuple[Tunnel, str]] = []  # (tunnel, accum_var)
    n_terminal_var: str | None = None  # For loop count

    for tunnel in tunnels:
        tunnel_type = tunnel.tunnel_type
        outer_term = tunnel.outer_terminal_uid
        inner_term = tunnel.inner_terminal_uid

        if not outer_term or not inner_term:
            continue

        if tunnel_type == "lMax":
            # Check if something flows INTO the inner terminal (accumulator)
            # vs nothing flows in (N terminal for iteration count)
            inner_has_source = _has_incoming_flow(inner_term, ctx)

            if inner_has_source:
                # Accumulator: init empty list before loop
                accum_var = _make_var_name(tunnel, ctx)
                pre_loop_stmts.append(
                    build_assign(accum_var, ast.List(elts=[], ctx=ast.Load()))
                )
                accum_tunnels.append((tunnel, accum_var))
                bindings[outer_term] = accum_var
            else:
                # N terminal: try to get count from outer terminal
                outer_var = ctx.resolve(outer_term)
                if outer_var:
                    # Check if array type - use len(), otherwise direct
                    lv_type = _get_terminal_type(outer_term, ctx)
                    if lv_type and lv_type.kind == "array":
                        n_terminal_var = f"len({outer_var})"
                    else:
                        # Integer or unknown - use directly
                        n_terminal_var = outer_var

        elif tunnel_type == "lpTun" and loop_type == "forLoop":
            # In For loops, lpTun OUTPUT tunnels are auto-indexed (accumulate)
            # Distinguish input vs output:
            # - INPUT tunnel: outer terminal has external source (resolvable)
            # - OUTPUT tunnel: outer terminal has NO external source
            outer_var = ctx.resolve(outer_term)

            if not outer_var:
                # No external source to outer -> this is an OUTPUT tunnel
                # Treat as accumulator - use pluralized name to avoid
                # conflict with inner iteration variable
                base_name = _make_var_name(tunnel, ctx)
                accum_var = _pluralize(base_name)
                pre_loop_stmts.append(
                    build_assign(accum_var, ast.List(elts=[], ctx=ast.Load()))
                )
                accum_tunnels.append((tunnel, accum_var))
                bindings[outer_term] = accum_var

    # 3. For forLoops: bind lpTun inner terminals based on loop style
    #    Must happen BEFORE generating inner statements
    if loop_type == "forLoop":
        # Separate lpTun inputs into arrays and scalars
        # (outer_var, inner_term, outer_term)
        lpTun_array_inputs: list[tuple[str, str, str]] = []
        lpTun_scalar_inputs: list[tuple[str, str]] = []  # (outer_var, inner_term)

        for tunnel in tunnels:
            if tunnel.tunnel_type == "lpTun":
                outer_var = ctx.resolve(tunnel.outer_terminal_uid)
                # Resolved value must be a valid Python identifier
                # (constants like '\x12' from the graph are not iterable names)
                if outer_var and not outer_var.isidentifier():
                    outer_var = to_var_name(outer_var) or "items"
                if outer_var and tunnel.inner_terminal_uid:
                    outer_term = tunnel.outer_terminal_uid
                    inner_term = tunnel.inner_terminal_uid
                    lv_type = _get_terminal_type(outer_term, ctx)
                    # Treat as array if type is array OR unknown (backward compat)
                    # Only treat as scalar if type is known and NOT an array
                    is_array = lv_type is None or lv_type.kind == "array"
                    if is_array:
                        lpTun_array_inputs.append(
                            (outer_var, inner_term, outer_term)
                        )
                    else:
                        # Known scalar type: pass through directly (no indexing)
                        lpTun_scalar_inputs.append((outer_var, inner_term))

        # Bind scalar inputs directly - same value each iteration
        for outer_var, inner_term in lpTun_scalar_inputs:
            inner_ctx.bind(inner_term, outer_var)

        # Decide: enumerate (single array, no N) vs indexed access for arrays
        depth = ctx.loop_depth
        idx_var = "ijklmn"[depth] if depth < 6 else f"idx_{depth}"

        if len(lpTun_array_inputs) == 1 and not n_terminal_var:
            # Single array, no N terminal: use enumerate, bind to singular form
            outer_var, inner_term, _ = lpTun_array_inputs[0]
            item_var = _singularize(outer_var, inner_ctx)
            inner_ctx.bind(inner_term, item_var)
        else:
            # Multiple arrays or N terminal: use indexed access
            for outer_var, inner_term, _ in lpTun_array_inputs:
                inner_ctx.bind(inner_term, f"{outer_var}[{idx_var}]")

    # 4. Generate inner node code
    inner_stmts = _generate_inner(inner_nodes, inner_ctx)

    # 4. Add accumulator appends for lMax at end of loop body
    for tunnel, accum_var in accum_tunnels:
        inner_term = tunnel.inner_terminal_uid
        inner_val = inner_ctx.resolve(inner_term)
        if inner_val and inner_val != accum_var:
            # accum_var.append(inner_val)
            inner_stmts.append(
                ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(id=accum_var, ctx=ast.Load()),
                            attr="append",
                            ctx=ast.Load(),
                        ),
                        args=[parse_expr(inner_val)],
                        keywords=[],
                    )
                )
            )

    # 5. Handle shift register updates (rSR) at end of loop body
    #
    # Pair each rSR with its lSR by ORDER of appearance (first lSR <-> first
    # rSR, second <-> second, ...): the graph-based tunnel reconstruction
    # (graph/operations.py::_tunnels_from_terminals) never populates
    # Tunnel.paired_terminal_uid -- only the legacy parser path
    # (parser/nodes/loop.py::_pair_shift_registers) does, using this exact
    # same positional heuristic (LabVIEW lists shift-register tunnels in
    # matching left/right order). Without this, rSR-to-lSR correlation was
    # silently unreachable and the update statement below never fired for
    # ANY shift register (init'd or not) -- a real value written into rSR
    # would just sit in its own freshly-named variable, never written back
    # into the lSR's local, so the next loop *iteration* (not call) would
    # keep reading the stale initial value.
    lsr_outers_ordered = [
        t.outer_terminal_uid for t in tunnels if t.tunnel_type == "lSR"
    ]
    rsr_outers_ordered = [
        t.outer_terminal_uid for t in tunnels if t.tunnel_type == "rSR"
    ]
    rsr_to_lsr_outer = dict(zip(rsr_outers_ordered, lsr_outers_ordered, strict=False))

    # Feedback writes (lSR local <- rSR's new value) are emitted AFTER every
    # updated_var assignment, so cross-register reads in the same iteration
    # still see the previous values -- LabVIEW updates all shift registers
    # together at the iteration boundary.
    sr_feedbacks: list[tuple[str, str]] = []  # (lsr_var, updated_var)
    for tunnel in tunnels:
        tunnel_type = tunnel.tunnel_type
        outer_term = tunnel.outer_terminal_uid
        inner_term = tunnel.inner_terminal_uid

        if tunnel_type != "rSR" or not outer_term or not inner_term:
            continue

        # Find paired lSR to get the shift variable name
        rsr_shift_var: str | None = None
        lsr_outer = rsr_to_lsr_outer.get(outer_term)
        if lsr_outer and lsr_outer in shift_reg_vars:
            rsr_shift_var = shift_reg_vars[lsr_outer]

        if rsr_shift_var:
            inner_val = inner_ctx.resolve(inner_term)
            if inner_val and inner_val != rsr_shift_var:
                # Write the new value into a DISTINCT variable rather than
                # mutating rsr_shift_var (the lSR-bound local) in place.
                updated_var = ctx.make_output_var(
                    f"{rsr_shift_var}_updated", node.id, terminal_id=outer_term,
                )
                inner_stmts.append(build_assign(updated_var, parse_expr(inner_val)))
                bindings[outer_term] = updated_var
                # Feed the new value back into the lSR local for the NEXT
                # iteration ONLY when it is genuine accumulation -- i.e. the
                # new value is computed FROM the SR's current value
                # (`counter = counter + 1`). When the new value is INDEPENDENT
                # of the SR (a functional global storing an external input,
                # e.g. the OpenG "Changed?" family: `state_new = u16`), we must
                # NOT overwrite: rsr_shift_var still holds the PRE-update value
                # that a separate branch consumes after the loop (the
                # `old != new` comparison). Overwriting only where there is real
                # feedback keeps those branch consumers correct without needing
                # a per-iteration snapshot.
                if _expr_references(inner_val, rsr_shift_var):
                    sr_feedbacks.append((rsr_shift_var, updated_var))
            else:
                bindings[outer_term] = rsr_shift_var

    for lsr_var, updated_var in sr_feedbacks:
        inner_stmts.append(
            build_assign(lsr_var, ast.Name(id=updated_var, ctx=ast.Load()))
        )

    # Resolve uninitialized-SR writebacks now that step 5 has run: use the
    # paired rSR's final bound value (the fresh "updated" variable above)
    # if one exists, else fall back to the unmodified seed (the SR is
    # never written to -- persists the same value forever).
    lsr_to_rsr_outer = {v: k for k, v in rsr_to_lsr_outer.items()}
    resolved_sr_writebacks: list[tuple[str, str]] = []
    for global_name, lsr_outer_term, fallback_var in uninitialized_sr_writebacks:
        rsr_outer_term = lsr_to_rsr_outer.get(lsr_outer_term)
        final_var = (
            bindings.get(rsr_outer_term, fallback_var)
            if rsr_outer_term
            else fallback_var
        )
        resolved_sr_writebacks.append((global_name, final_var))

    # 6. Build the loop
    stop_condition_var: str | None = None
    loop_ast: ast.While | ast.For
    if loop_type == "whileLoop":
        loop_ast, stop_condition_var = _build_while_loop(
            node, inner_stmts, inner_ctx
        )
    else:
        loop_ast = _build_for_loop(
            node, inner_stmts, inner_ctx, tunnels, n_terminal_var
        )

    # 7. Handle lpTun outputs (last value)
    for tunnel in tunnels:
        tunnel_type = tunnel.tunnel_type
        outer_term = tunnel.outer_terminal_uid
        inner_term = tunnel.inner_terminal_uid

        if tunnel_type != "lpTun" or not outer_term or not inner_term:
            continue

        # Only if not already bound (it might be an input tunnel)
        if outer_term not in bindings:
            inner_val = inner_ctx.resolve(inner_term)
            if inner_val:
                bindings[outer_term] = inner_val

    # Add initialization for while loop stop condition
    # (condition is computed inside loop, so we init to False before)
    # Skip if the variable is already a function parameter or if the
    # resolved value is an expression (not a valid assignment target)
    if stop_condition_var:
        param_names = {to_var_name(inp.name or "") for inp in ctx.vi_inputs}
        is_identifier = stop_condition_var.isidentifier()
        if is_identifier and stop_condition_var not in param_names:
            pre_loop_stmts.append(
                build_assign(stop_condition_var, ast.Constant(value=False))
            )

    # Write uninitialized-SR locals back to their module globals so the
    # persisted value is visible on the VI's next call.
    writeback_stmts: list[ast.stmt] = [
        build_assign(global_name, ast.Name(id=final_var, ctx=ast.Load()))
        for global_name, final_var in resolved_sr_writebacks
    ]

    all_stmts = pre_loop_stmts + [loop_ast] + writeback_stmts
    return CodeFragment(
        statements=all_stmts,
        bindings=bindings,
        imports=inner_ctx.imports,
    )

def _expr_references(expr_str: str, var: str) -> bool:
    """True if the Python expression ``expr_str`` reads the name ``var``.

    Used to detect true shift-register accumulation: the rSR's new value is
    computed FROM the SR's current value (so it must be fed back for the next
    iteration), versus an independent value (which must not overwrite the lSR
    local a separate branch still reads). AST-based so ``state`` does not match
    ``state2``."""
    try:
        tree = ast.parse(expr_str, mode="eval")
    except SyntaxError:
        return False
    return any(
        isinstance(n, ast.Name) and n.id == var for n in ast.walk(tree)
    )


def _make_var_name(tunnel: Tunnel, ctx: CodeGenContext | None = None) -> str:
    """Generate a variable name from tunnel info.

    Priority:
    1. Use outer source terminal name (if available from context flow)
    2. Use downstream destination name (if source is unnamed)
    3. Semantic inference based on tunnel type and data type
    4. Fall back to generic names
    """
    outer = tunnel.outer_terminal_uid

    # Try to get name from the source feeding this tunnel
    if ctx:
        source_name = _get_source_terminal_name(outer, ctx)
        if source_name:
            return to_var_name(source_name)

        # Try downstream: where does this value ultimately go?
        dest_name = _get_dest_terminal_name(outer, ctx)
        if dest_name:
            return to_var_name(dest_name)

    # Semantic naming based on tunnel type
    if tunnel.tunnel_type == "lSR":
        # Shift registers: use semantic names based on common patterns
        # Try to infer from the data type or use generic names
        lv_type = _get_terminal_type(outer, ctx) if ctx else None
        if lv_type:
            if lv_type.kind == "string":
                return "accumulated_str"
            elif lv_type.kind == "array":
                return "collected"
            elif lv_type.kind in ("int", "float", "numeric"):
                return "counter"
        # Generic fallback for shift registers
        return "state"

    elif tunnel.tunnel_type == "lMax":
        # Accumulators build up arrays
        return "results"

    # Fall back to generic tunnel name
    return "value"

def _unique_shift_var_name(base_name: str, ctx: CodeGenContext) -> str:
    """Disambiguate a shift-register variable name against ones already
    bound in this VI.

    _make_var_name() falls back to a generic name ("state", "counter", ...)
    whenever it has no naming hint -- a loop with TWO such shift registers
    (e.g. two independent uninitialized SRs, as in the OpenG "Periodic
    Trigger" idiom) would otherwise both get the SAME Python variable,
    silently clobbering one with the other's seed value. Mirrors
    _singularize()'s conflict-resolution pattern.
    """
    if not ctx.var_name_in_use(base_name):
        return base_name
    suffix = 2
    candidate = f"{base_name}_{suffix}"
    while ctx.var_name_in_use(candidate):
        suffix += 1
        candidate = f"{base_name}_{suffix}"
    return candidate

def _pluralize(var_name: str) -> str:
    """Convert a variable name to plural form for accumulator naming.

    Examples:
        stripped_path -> stripped_paths
        name -> names
        entry -> entries
        box -> boxes

    This ensures accumulator variable names don't conflict with
    inner iteration variables derived from the same source.
    """
    base = var_name.lower()

    # If already plural-looking, add _list suffix
    if base.endswith("s") and not base.endswith("ss"):
        return f"{var_name}_list"

    # Common plural transformations
    if base.endswith("y") and len(base) > 1 and base[-2] not in "aeiou":
        return base[:-1] + "ies"  # entry -> entries
    if base.endswith(("s", "x", "ch", "sh")):
        return base + "es"  # box -> boxes
    return base + "s"  # name -> names

def _singularize(array_var: str, ctx: CodeGenContext) -> str:
    """Derive singular item name from array variable name.

    Examples:
        methods -> method
        items -> item
        values -> value
        data -> datum (or data_item)
        array -> element

    For nested loops with name conflicts, appends a number.
    """
    base = array_var.lower()

    # Common plural -> singular transformations
    if base.endswith("ies"):
        singular = base[:-3] + "y"  # entries -> entry
    elif base.endswith("ses") or base.endswith("xes") or base.endswith("ches"):
        singular = base[:-2]  # boxes -> box, matches -> match
    elif base.endswith("s") and len(base) > 1:
        singular = base[:-1]  # methods -> method
    elif base == "data":
        singular = "datum"
    elif base == "array":
        singular = "element"
    else:
        singular = base + "_item"

    # Check for conflicts with existing bindings
    candidate = singular
    suffix = 2
    while ctx.var_name_in_use(candidate):
        candidate = f"{singular}_{suffix}"
        suffix += 1

    return candidate

def _get_source_terminal_name(
    terminal_uid: str, ctx: CodeGenContext
) -> str | None:
    """Get the name of the source feeding a terminal.

    Traces back through data flow to find a named source (FP control, constant).
    """
    flow_info = ctx.get_source(terminal_uid)
    if not flow_info:
        return None

    if flow_info.src_parent_name:
        return flow_info.src_parent_name

    # Recurse to trace further back
    if flow_info.src_terminal and flow_info.src_terminal != terminal_uid:
        return _get_source_terminal_name(flow_info.src_terminal, ctx)

    return None

def _get_dest_terminal_name(
    terminal_uid: str, ctx: CodeGenContext, visited: set[str] | None = None
) -> str | None:
    """Get the name of a destination this terminal flows to.

    Traces forward through data flow to find a named destination:
    - FP indicator names
    - SubVI input names (from dest_parent_name in flow data)

    Used when source has no name (e.g., unnamed constant).

    Handles tunnel pass-through: when data flows to a tunnel inner terminal,
    traces through the tunnel outer's other destinations.
    """
    if visited is None:
        visited = set()
    if terminal_uid in visited:
        return None
    visited.add(terminal_uid)

    dest_list = ctx.get_destinations(terminal_uid)
    for dest_info in dest_list:
        # Check if it's a named indicator (output)
        if (
            dest_info.dest_parent_name
            and "Indicator" in dest_info.dest_parent_labels
        ):
            return dest_info.dest_parent_name

        # Check if it flows to a SubVI input - use SubVI name as hint
        if (
            "SubVI" in dest_info.dest_parent_labels
            and dest_info.dest_parent_name
        ):
            return dest_info.dest_parent_name

        # Recurse through tunnels/connections
        if dest_info.dest_terminal:
            found = _get_dest_terminal_name(
                dest_info.dest_terminal, ctx, visited
            )
            if found:
                return found

    # If no forward flow found, check source terminal's other destinations
    source = ctx.get_source(terminal_uid)
    if source:
        if source.src_terminal not in visited:
            found = _get_dest_terminal_name(source.src_terminal, ctx, visited)
            if found:
                return found

    return None

def _get_terminal_type(
    terminal_uid: str,
    ctx: CodeGenContext,
    visited: set[str] | None = None,
) -> LVType | None:
    """Get the LVType for a terminal by tracing back to its source.

    Traces through data flow to find the source FP terminal's lv_type.
    This allows us to distinguish scalar vs array inputs to loops.

    Args:
        terminal_uid: Terminal ID to look up type for
        ctx: Code generation context with vi_inputs and flow maps
        visited: Set of already visited terminals (for cycle detection)

    Returns:
        LVType if found, None otherwise
    """
    if visited is None:
        visited = set()
    if terminal_uid in visited:
        return None  # Cycle detection
    visited.add(terminal_uid)

    # Check if terminal is directly an FP input
    for inp in ctx.vi_inputs:
        if inp.id == terminal_uid:
            return inp.lv_type

    # Trace through data flow
    flow_info = ctx.get_source(terminal_uid)
    if flow_info:
        # Check if source parent is an FP input
        for inp in ctx.vi_inputs:
            if inp.id == flow_info.src_parent_id:
                return inp.lv_type

        # Recurse to trace further
        if flow_info.src_terminal and flow_info.src_terminal != terminal_uid:
            return _get_terminal_type(flow_info.src_terminal, ctx, visited)

    return None

def _generate_inner(
    inner_nodes: list[Operation], ctx: CodeGenContext
) -> list[ast.stmt]:
    """Generate code for inner loop nodes."""
    return ctx.generate_body(inner_nodes)

def _build_while_loop(
    node: LoopOperation, body: list[ast.stmt], ctx: CodeGenContext
) -> tuple[ast.While, str | None]:
    """Build a while loop AST node.

    LabVIEW while loops have do-while semantics: the body ALWAYS runs at
    least once, then the stop condition is tested at the END of each
    iteration. We model this directly with Python's do-while idiom:

        while True:
            <body>
            if <stop_expr>:
                break

    The conditional terminal also has a polarity that LabVIEW lets the user
    toggle (right-click "Stop if True" vs "Continue if True"):
    - Stop-if-True (default, ``stop_condition_inverted`` False): break when
      the resolved condition is True -> ``if cond: break``.
    - Continue-if-True (``stop_condition_inverted`` True): the loop keeps
      running while the condition is True, so it breaks when the condition
      is False -> ``if not cond: break``.

    Returns:
        Tuple of (While AST node, stop condition variable name or None)
    """
    # Ensure non-empty body
    if not body:
        body = [ast.Pass()]

    # Get stop condition from the lTst terminal
    stop_terminal = node.stop_condition_terminal

    if stop_terminal:
        # Resolve the stop condition variable from the loop body
        # The cpdArith or other operation computes this inside the loop
        stop_condition = ctx.resolve(stop_terminal)
        if stop_condition:
            cond_expr = parse_expr(stop_condition)
            if node.stop_condition_inverted:
                cond_expr = ast.UnaryOp(op=ast.Not(), operand=cond_expr)
            body = [
                *body,
                ast.If(test=cond_expr, body=[ast.Break()], orelse=[]),
            ]
            return ast.While(
                test=ast.Constant(value=True),
                body=body,
                orelse=[],
            ), stop_condition

    # Fallback: no stop condition found, use break to prevent infinite loop.
    # Do-while with no condition still runs the body exactly once.
    body = [*body, ast.Break()]
    return ast.While(
        test=ast.Constant(value=True),
        body=body,
        orelse=[],
    ), None

def _build_for_loop(
    node: LoopOperation,
    body: list[ast.stmt],
    ctx: CodeGenContext,
    tunnels: list[Tunnel],
    n_terminal_var: str | None = None,
) -> ast.For:
    """Build a for loop AST node.

    For loops can iterate:
    1. Over a range (N terminal via lMax input)
    2. Over array elements (autoindexing via lpTun input)

    LabVIEW behavior with multiple auto-indexing inputs:
    - Iterates min(len(arr1), len(arr2), ..., N) times
    - Each array is accessed by index

    Args:
        n_terminal_var: Explicit count source (e.g., count or "len(array)")
    """
    # Ensure non-empty body
    if not body:
        body = [ast.Pass()]

    # A For loop may carry an OPTIONAL conditional terminal (LabVIEW 2012+:
    # "For Loop with conditional terminal") — the same lTst stop as a while
    # loop, tested at the END of each iteration. If present, append the guarded
    # break so the loop can exit before N (Stop-if-True -> ``if cond: break``,
    # Continue-if-True -> ``if not cond: break``), mirroring _build_while_loop.
    # Placed AFTER the body's auto-indexed accumulator appends, so the stopping
    # iteration still contributes its output — LabVIEW stops after it completes.
    stop_terminal = node.stop_condition_terminal
    if stop_terminal:
        stop_condition = ctx.resolve(stop_terminal)
        if stop_condition:
            cond_expr = parse_expr(stop_condition)
            if node.stop_condition_inverted:
                cond_expr = ast.UnaryOp(op=ast.Not(), operand=cond_expr)
            body = [*body, ast.If(test=cond_expr, body=[ast.Break()], orelse=[])]

    # Find ALL auto-indexing array inputs
    autoindex_arrays = _find_all_autoindex_arrays(tunnels, ctx)

    # Get index variable for this loop depth (i, j, k, ...)
    # Use depth-1 because ctx was already incremented for loop interior
    depth = max(0, ctx.loop_depth - 1)
    idx_var = "ijklmn"[depth] if depth < 6 else f"idx_{depth}"

    # Single array, no N terminal: use enumerate for both index and item
    # Note: inner terminal already bound in generate()
    if len(autoindex_arrays) == 1 and not n_terminal_var:
        array_var, inner_term = autoindex_arrays[0]
        # Get item_var from binding (set in generate())
        item_var = ctx.resolve(inner_term) or "item"
        return ast.For(
            target=ast.Tuple(
                elts=[
                    ast.Name(id=idx_var, ctx=ast.Store()),
                    ast.Name(id=item_var, ctx=ast.Store()),
                ],
                ctx=ast.Store(),
            ),
            iter=ast.Call(
                func=ast.Name(id="enumerate", ctx=ast.Load()),
                args=[ast.Name(id=array_var, ctx=ast.Load())],
                keywords=[],
            ),
            body=body,
            orelse=[],
        )

    # Multiple arrays or N terminal: use indexed access with min()
    if autoindex_arrays or n_terminal_var:
        # Build min() arguments: len(arr1), len(arr2), ..., N
        min_args: list[ast.expr] = []

        for array_var, inner_term in autoindex_arrays:
            # Add len(array) to min args
            # Note: inner terminal already bound to array[idx] in generate()
            min_args.append(
                ast.Call(
                    func=ast.Name(id="len", ctx=ast.Load()),
                    args=[parse_expr(array_var)],
                    keywords=[],
                )
            )

        if n_terminal_var:
            min_args.append(parse_expr(n_terminal_var))

        # Build range argument
        if len(min_args) == 1:
            range_arg = min_args[0]
        else:
            range_arg = ast.Call(
                func=ast.Name(id="min", ctx=ast.Load()),
                args=min_args,
                keywords=[],
            )

        return ast.For(
            target=ast.Name(id=idx_var, ctx=ast.Store()),
            iter=ast.Call(
                func=ast.Name(id="range", ctx=ast.Load()),
                args=[range_arg],
                keywords=[],
            ),
            body=body,
            orelse=[],
        )

    # Absolute fallback
    return ast.For(
        target=ast.Name(id=idx_var, ctx=ast.Store()),
        iter=ast.Call(
            func=ast.Name(id="range", ctx=ast.Load()),
            args=[ast.Constant(value=10)],
            keywords=[],
        ),
        body=body,
        orelse=[],
    )

def _find_all_autoindex_arrays(
    tunnels: list[Tunnel], ctx: CodeGenContext
) -> list[tuple[str, str]]:
    """Find array inputs for autoindexing (excludes scalar inputs).

    LabVIEW For loops with multiple auto-indexing inputs iterate
    min(len(arr1), len(arr2), ...) times.

    In LabVIEW, lpTun inputs to For loops are auto-indexed IF they are arrays.
    Scalar inputs pass through unchanged (same value each iteration).

    Returns list of (array_var, inner_terminal_uid) tuples.
    Only includes inputs with array type - scalar inputs are excluded.
    """
    results: list[tuple[str, str]] = []

    for tunnel in tunnels:
        tunnel_type = tunnel.tunnel_type
        outer_term = tunnel.outer_terminal_uid
        inner_term = tunnel.inner_terminal_uid

        # In For loops, lpTun inputs are auto-indexed if array OR type unknown
        # Only exclude if type is KNOWN and NOT an array (scalar)
        if tunnel_type == "lpTun" and outer_term and inner_term:
            outer_var = ctx.resolve(outer_term)
            if outer_var:
                lv_type = _get_terminal_type(outer_term, ctx)
                # Treat as array if type is array OR unknown (backward compat)
                if lv_type is None or lv_type.kind == "array":
                    results.append((outer_var, inner_term))

    return results

def _has_incoming_flow(terminal_uid: str, ctx: CodeGenContext) -> bool:
    """Check if a terminal has incoming data flow from the loop body.

    Used to distinguish:
    - lMax as N terminal (no incoming flow to inner terminal)
    - lMax as accumulator (has incoming flow from loop body)

    Structural edges (tunnel outer→inner on the same structure node)
    are NOT data flow — they're the tunnel's own pass-through.
    """
    if ctx.graph is None:
        return False
    node_id = ctx.graph._term_to_node.get(terminal_uid)
    for src in ctx.graph.incoming_edges(terminal_uid):
        if src.node_id != node_id:
            return True
    return False


# Comparison operator inversions for cleaner negation
_INVERT_CMPOP: dict[type, type] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.LtE: ast.Gt,
    ast.Gt: ast.LtE,
    ast.GtE: ast.Lt,
}


def _negate_condition(expr: ast.expr) -> ast.expr:
    """Negate a condition expression, simplifying where possible.

    Produces cleaner Python by:
    - Unwrapping double negation: not (not x) -> x
    - Inverting comparisons: not (x < y) -> x >= y
    - Applying De Morgan's law: not (a and b) -> (not a) or (not b)

    Args:
        expr: AST expression to negate

    Returns:
        Negated expression, simplified where possible
    """
    # Double negation: not (not x) -> x
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return expr.operand

    # Invert comparison: not (x < y) -> x >= y
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1:
        op_type = type(expr.ops[0])
        if op_type in _INVERT_CMPOP:
            return ast.Compare(
                left=expr.left,
                ops=[_INVERT_CMPOP[op_type]()],
                comparators=expr.comparators,
            )

    # Default: wrap in not
    return ast.UnaryOp(op=ast.Not(), operand=expr)

"""Code generator for loop structures (while, for)."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import Operation, Tunnel

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..condition_builder import build_condition_expr
from ..fragment import CodeFragment
from .base import NodeCodeGen, get_codegen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class LoopCodeGen(NodeCodeGen):
    """Generate code for LabVIEW loop structures.

    Handles:
    - whileLoop: while True: ... (with break condition)
    - forLoop: for i in range(n): ... or for item in array: ...

    Tunnel types:
    - lSR (left shift register): Input, persists value across iterations
    - rSR (right shift register): Output, returns final shift register value
    - lpTun (loop tunnel): Pass-through, last value on output
    - lMax (accumulator): Output, builds array from all iterations
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
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
                    shift_var = self._make_var_name(tunnel, ctx)
                    pre_loop_stmts.append(
                        build_assign(shift_var, parse_expr(outer_var))
                    )
                    inner_ctx.bind(inner_term, shift_var)
                    shift_reg_vars[outer_term] = shift_var

            elif tunnel_type == "lpTun":
                # Check if input tunnel (outer has a source)
                outer_var = ctx.resolve(outer_term)
                if outer_var:
                    # While loops: pass the whole value through
                    # For loops: defer binding until we know enumerate vs range
                    if loop_type != "forLoop":
                        inner_ctx.bind(inner_term, outer_var)
                    # For forLoop lpTun, binding happens after we determine loop style

        # 2. Process OUTPUT tunnels - set up accumulators for lMax
        # Note: lMax can be either:
        # - The N terminal (iteration count) - outer has input, inner has no input
        # - Auto-indexed output (accumulator) - inner receives values from loop body
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
                inner_has_source = self._has_incoming_flow(inner_term, ctx)

                if inner_has_source:
                    # Accumulator: init empty list before loop
                    accum_var = self._make_var_name(tunnel, ctx)
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
                        lv_type = self._get_terminal_type(outer_term, ctx)
                        if lv_type and lv_type.kind == "array":
                            n_terminal_var = f"len({outer_var})"
                        else:
                            # Integer or unknown - use directly
                            n_terminal_var = outer_var

        # 3. For forLoops: bind lpTun inner terminals based on loop style
        #    Must happen BEFORE generating inner statements
        if loop_type == "forLoop":
            # Collect all lpTun input tunnels
            lpTun_inputs: list[tuple[str, str, str]] = []  # (outer_var, inner_term, outer_term)
            for tunnel in tunnels:
                if tunnel.tunnel_type == "lpTun":
                    outer_var = ctx.resolve(tunnel.outer_terminal_uid)
                    if outer_var and tunnel.inner_terminal_uid:
                        lpTun_inputs.append((outer_var, tunnel.inner_terminal_uid, tunnel.outer_terminal_uid))

            # Decide: enumerate (single array, no N) vs indexed access
            depth = ctx.loop_depth
            idx_var = "ijklmn"[depth] if depth < 6 else f"idx_{depth}"

            if len(lpTun_inputs) == 1 and not n_terminal_var:
                # Single array, no N terminal: use enumerate, bind to singular form
                outer_var, inner_term, _ = lpTun_inputs[0]
                item_var = self._singularize(outer_var, inner_ctx)
                inner_ctx.bind(inner_term, item_var)
            else:
                # Multiple arrays or N terminal: use indexed access
                for outer_var, inner_term, _ in lpTun_inputs:
                    inner_ctx.bind(inner_term, f"{outer_var}[{idx_var}]")

        # 4. Generate inner node code
        inner_stmts = self._generate_inner(inner_nodes, inner_ctx)

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
        for tunnel in tunnels:
            tunnel_type = tunnel.tunnel_type
            outer_term = tunnel.outer_terminal_uid
            inner_term = tunnel.inner_terminal_uid
            paired_uid = tunnel.paired_terminal_uid

            if tunnel_type != "rSR" or not outer_term or not inner_term:
                continue

            # Find paired lSR to get the shift variable name
            rsr_shift_var: str | None = None
            if paired_uid:
                # paired_uid is the lSR's DCO uid, need to find matching lSR tunnel
                for t in tunnels:
                    if t.tunnel_type == "lSR":
                        lsr_outer = t.outer_terminal_uid
                        if lsr_outer in shift_reg_vars:
                            rsr_shift_var = shift_reg_vars[lsr_outer]
                            break

            if rsr_shift_var:
                inner_val = inner_ctx.resolve(inner_term)
                if inner_val and inner_val != rsr_shift_var:
                    # Update shift register: rsr_shift_var = new_value
                    inner_stmts.append(
                        build_assign(rsr_shift_var, parse_expr(inner_val))
                    )
                bindings[outer_term] = rsr_shift_var

        # 6. Build the loop
        stop_condition_var: str | None = None
        loop_ast: ast.While | ast.For
        if loop_type == "whileLoop":
            loop_ast, stop_condition_var = self._build_while_loop(
                node, inner_stmts, inner_ctx
            )
        else:
            loop_ast = self._build_for_loop(
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
        if stop_condition_var:
            pre_loop_stmts.append(
                build_assign(stop_condition_var, ast.Constant(value=False))
            )

        all_stmts = pre_loop_stmts + [loop_ast]
        return CodeFragment(
            statements=all_stmts,
            bindings=bindings,
            imports=inner_ctx.imports,
        )

    def _make_var_name(self, tunnel: Tunnel, ctx: CodeGenContext | None = None) -> str:
        """Generate a variable name from tunnel info.

        Priority:
        1. Use outer source terminal name (if available from context flow)
        2. Semantic inference (counter, index, accumulator)
        3. Fall back to UID-based naming
        """
        outer = tunnel.outer_terminal_uid

        # Try to get name from the source feeding this tunnel
        if ctx:
            source_name = self._get_source_terminal_name(outer, ctx)
            if source_name:
                return to_var_name(source_name)

        # Semantic naming based on tunnel type
        if tunnel.tunnel_type == "lSR":
            # Shift registers are often counters or accumulators
            # Check if paired with numeric operations
            return f"shift_{tunnel.outer_terminal_uid[-4:]}"
        elif tunnel.tunnel_type == "lMax":
            return f"accum_{tunnel.outer_terminal_uid[-4:]}"

        # Fall back to UID-based
        uid_suffix = outer.split(":")[-1] if ":" in outer else outer[-4:]
        return to_var_name(uid_suffix)

    def _singularize(self, array_var: str, ctx: CodeGenContext) -> str:
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
        while candidate in ctx.bindings.values():
            candidate = f"{singular}_{suffix}"
            suffix += 1

        return candidate

    def _get_source_terminal_name(
        self, terminal_uid: str, ctx: CodeGenContext
    ) -> str | None:
        """Get the name of the source feeding a terminal.

        Traces back through data flow to find a named source (FP control, constant).
        """
        flow_info = ctx._flow_map.get(terminal_uid)
        if not flow_info:
            return None

        src_parent_name: str | None = flow_info.get("src_parent_name")
        if src_parent_name:
            return src_parent_name

        # Recurse to trace further back
        src_terminal = flow_info.get("src_terminal")
        if src_terminal and src_terminal != terminal_uid:
            return self._get_source_terminal_name(src_terminal, ctx)

        return None

    def _generate_inner(
        self, inner_nodes: list[Operation], ctx: CodeGenContext
    ) -> list[ast.stmt]:
        """Generate code for inner loop nodes."""
        statements = []

        for node in inner_nodes:
            codegen = get_codegen(node)
            fragment = codegen.generate(node, ctx)

            statements.extend(fragment.statements)
            ctx.merge(fragment.bindings)
            ctx.imports.update(fragment.imports)

        return statements

    def _build_while_loop(
        self, node: Operation, body: list[ast.stmt], ctx: CodeGenContext
    ) -> tuple[ast.While, str | None]:
        """Build a while loop AST node.

        LabVIEW while loops run until stop condition is True.
        We generate: while not <condition>: ...

        Priority for stop condition:
        1. Try to build compound expression (e.g., counter < 10) from inner operations
        2. Fall back to simple variable reference
        3. Fallback: while True with break

        Returns:
            Tuple of (While AST node, stop condition variable name or None)
        """
        # Ensure non-empty body
        if not body:
            body = [ast.Pass()]

        # Get stop condition from the lTst terminal
        stop_terminal = node.stop_condition_terminal

        if stop_terminal:
            # First, try to build a compound condition expression
            # This handles cases like: counter < 10, i >= array_size
            cond_expr = build_condition_expr(
                stop_terminal, ctx, node.inner_nodes
            )

            if cond_expr:
                # LabVIEW stops when condition is True
                # Python: while not <condition>
                # But simplify double negation: not (not x) -> x
                test_expr = _negate_condition(cond_expr)
                return ast.While(
                    test=test_expr,
                    body=body,
                    orelse=[],
                ), None  # No pre-init needed for compound expression

            # Fall back to simple variable reference
            stop_condition = ctx.resolve(stop_terminal)
            if stop_condition:
                # LabVIEW stops when condition is True
                # Python: while not stop_condition
                # The condition is computed inside the loop, so we return:
                # 1. An initialization statement (condition = False) - done by caller
                # 2. The while loop
                return ast.While(
                    test=ast.UnaryOp(
                        op=ast.Not(),
                        operand=parse_expr(stop_condition),
                    ),
                    body=body,
                    orelse=[],
                ), stop_condition

        # Fallback: no stop condition found, use break to prevent infinite loop
        body.append(ast.Break())
        return ast.While(
            test=ast.Constant(value=True),
            body=body,
            orelse=[],
        ), None

    def _build_for_loop(
        self,
        node: Operation,
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

        # Find ALL auto-indexing array inputs
        autoindex_arrays = self._find_all_autoindex_arrays(tunnels, ctx)

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
        self, tunnels: list[Tunnel], ctx: CodeGenContext
    ) -> list[tuple[str, str]]:
        """Find ALL array inputs for autoindexing.

        LabVIEW For loops with multiple auto-indexing inputs iterate
        min(len(arr1), len(arr2), ...) times.

        In LabVIEW, lpTun inputs to For loops ARE auto-indexed arrays by default.
        The tunnel type itself indicates auto-indexing behavior.

        Returns list of (array_var, inner_terminal_uid) tuples.
        """
        results: list[tuple[str, str]] = []

        for tunnel in tunnels:
            tunnel_type = tunnel.tunnel_type
            outer_term = tunnel.outer_terminal_uid
            inner_term = tunnel.inner_terminal_uid

            # In For loops, lpTun inputs are auto-indexed arrays
            if tunnel_type == "lpTun" and outer_term and inner_term:
                outer_var = ctx.resolve(outer_term)
                if outer_var:
                    results.append((outer_var, inner_term))

        return results

    def _has_incoming_flow(self, terminal_uid: str, ctx: CodeGenContext) -> bool:
        """Check if a terminal has any incoming data flow.

        Used to distinguish:
        - lMax as N terminal (no incoming flow to inner terminal)
        - lMax as accumulator (has incoming flow from loop body)
        """
        # Check if this terminal is the destination of any flow
        return terminal_uid in ctx._flow_map


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

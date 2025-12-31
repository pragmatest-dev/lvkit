"""Code generator for loop structures (while, for)."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from ..ast_utils import build_assign, parse_expr, to_var_name
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

    def generate(self, node: dict[str, Any], ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a loop structure."""
        loop_type = node.get("loop_type", "whileLoop")
        tunnels = node.get("tunnels", [])
        inner_nodes = node.get("inner_nodes", [])

        pre_loop_stmts: list[ast.stmt] = []
        bindings: dict[str, str] = {}

        # Create child context for loop interior
        inner_ctx = ctx.child()

        # Track shift register variable names for update statements
        shift_reg_vars: dict[str, str] = {}  # lSR outer_terminal -> var_name

        # 1. Process INPUT tunnels (lSR, lpTun)
        for tunnel in tunnels:
            tunnel_type = tunnel.get("tunnel_type")
            outer_term = tunnel.get("outer_terminal_uid")
            inner_term = tunnel.get("inner_terminal_uid")

            if not outer_term or not inner_term:
                continue

            if tunnel_type == "lSR":
                # Shift register: init variable from outer, bind inner
                outer_var = ctx.resolve(outer_term)
                if outer_var:
                    shift_var = f"shift_{self._make_var_name(tunnel)}"
                    pre_loop_stmts.append(
                        build_assign(shift_var, parse_expr(outer_var))
                    )
                    inner_ctx.bind(inner_term, shift_var)
                    shift_reg_vars[outer_term] = shift_var

            elif tunnel_type == "lpTun":
                # Check if input tunnel (outer has a source)
                outer_var = ctx.resolve(outer_term)
                if outer_var:
                    # For loops: auto-index using array[i]
                    # While loops: pass the whole value through
                    if loop_type == "forLoop":
                        inner_ctx.bind(inner_term, f"{outer_var}[i]")
                    else:
                        inner_ctx.bind(inner_term, outer_var)

        # 2. Process OUTPUT tunnels - set up accumulators for lMax
        # Note: lMax can be either:
        # - The N terminal (iteration count) - outer has input, inner has no input
        # - Auto-indexed output (accumulator) - inner receives values from loop body
        accum_tunnels: list[tuple[dict, str]] = []  # (tunnel, accum_var)
        n_terminal_var: str | None = None  # For loop count

        for tunnel in tunnels:
            tunnel_type = tunnel.get("tunnel_type")
            outer_term = tunnel.get("outer_terminal_uid")
            inner_term = tunnel.get("inner_terminal_uid")

            if not outer_term or not inner_term:
                continue

            if tunnel_type == "lMax":
                # Check if something flows INTO the inner terminal (accumulator)
                # vs nothing flows in (N terminal for iteration count)
                inner_has_source = self._has_incoming_flow(inner_term, ctx)

                if inner_has_source:
                    # Accumulator: init empty list before loop
                    accum_var = f"accum_{self._make_var_name(tunnel)}"
                    pre_loop_stmts.append(
                        build_assign(accum_var, ast.List(elts=[], ctx=ast.Load()))
                    )
                    accum_tunnels.append((tunnel, accum_var))
                    bindings[outer_term] = accum_var
                else:
                    # N terminal: try to get count from outer terminal
                    outer_var = ctx.resolve(outer_term)
                    if outer_var:
                        # len() if array, direct use if integer
                        n_terminal_var = f"len({outer_var})"

        # 3. Generate inner node code
        inner_stmts = self._generate_inner(inner_nodes, inner_ctx)

        # 4. Add accumulator appends for lMax at end of loop body
        for tunnel, accum_var in accum_tunnels:
            inner_term = tunnel.get("inner_terminal_uid")
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
            tunnel_type = tunnel.get("tunnel_type")
            outer_term = tunnel.get("outer_terminal_uid")
            inner_term = tunnel.get("inner_terminal_uid")
            paired_uid = tunnel.get("paired_terminal_uid")

            if tunnel_type != "rSR" or not outer_term or not inner_term:
                continue

            # Find paired lSR to get the shift variable name
            shift_var = None
            if paired_uid:
                # paired_uid is the lSR's DCO uid, need to find matching lSR tunnel
                for t in tunnels:
                    if t.get("tunnel_type") == "lSR":
                        lsr_outer = t.get("outer_terminal_uid")
                        if lsr_outer in shift_reg_vars:
                            shift_var = shift_reg_vars[lsr_outer]
                            break

            if shift_var:
                inner_val = inner_ctx.resolve(inner_term)
                if inner_val and inner_val != shift_var:
                    # Update shift register: shift_var = new_value
                    inner_stmts.append(
                        build_assign(shift_var, parse_expr(inner_val))
                    )
                bindings[outer_term] = shift_var

        # 6. Build the loop
        stop_condition_var = None
        if loop_type == "whileLoop":
            loop_ast, stop_condition_var = self._build_while_loop(node, inner_stmts, inner_ctx)
        else:
            loop_ast = self._build_for_loop(
                node, inner_stmts, inner_ctx, tunnels, n_terminal_var
            )

        # 7. Handle lpTun outputs (last value)
        for tunnel in tunnels:
            tunnel_type = tunnel.get("tunnel_type")
            outer_term = tunnel.get("outer_terminal_uid")
            inner_term = tunnel.get("inner_terminal_uid")

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

    def _make_var_name(self, tunnel: dict) -> str:
        """Generate a variable name from tunnel info."""
        outer = tunnel.get("outer_terminal_uid", "")
        # Use last part of UID for uniqueness
        uid_suffix = outer.split(":")[-1] if ":" in outer else outer[-4:]
        return to_var_name(uid_suffix)

    def _generate_inner(
        self, inner_nodes: list[dict], ctx: CodeGenContext
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
        self, node: dict, body: list[ast.stmt], ctx: CodeGenContext
    ) -> ast.While:
        """Build a while loop AST node.

        LabVIEW while loops run until stop condition is True.
        We generate: <condition> = False; while not <condition>: ... <condition> = ...

        If no stop condition can be resolved, falls back to while True with break.
        """
        # Ensure non-empty body
        if not body:
            body = [ast.Pass()]

        # Get stop condition from the lTst terminal
        stop_terminal = node.get("stop_condition_terminal")
        stop_condition = None

        if stop_terminal:
            # Resolve what value flows into the stop terminal
            stop_condition = ctx.resolve(stop_terminal)

        if stop_condition:
            # LabVIEW stops when condition is True
            # Python: while not stop_condition
            # The condition is computed inside the loop, so we return:
            # 1. An initialization statement (condition = False)
            # 2. The while loop
            return ast.While(
                test=ast.UnaryOp(
                    op=ast.Not(),
                    operand=parse_expr(stop_condition),
                ),
                body=body,
                orelse=[],
            ), stop_condition
        else:
            # Fallback: no stop condition found, use break to prevent infinite loop
            body.append(ast.Break())
            return ast.While(
                test=ast.Constant(value=True),
                body=body,
                orelse=[],
            ), None

    def _build_for_loop(
        self,
        node: dict,
        body: list[ast.stmt],
        ctx: CodeGenContext,
        tunnels: list[dict],
        n_terminal_var: str | None = None,
    ) -> ast.For:
        """Build a for loop AST node.

        For loops can iterate:
        1. Over a range (N terminal via lMax input)
        2. Over array elements (autoindexing via lpTun input)

        Args:
            n_terminal_var: Explicit count source (e.g., "len(array)")
        """
        # Ensure non-empty body
        if not body:
            body = [ast.Pass()]

        # Check for array autoindexing first (using first lpTun input as array)
        array_info = self._find_autoindex_array(tunnels, ctx)

        if array_info:
            array_var, inner_term = array_info
            item_var = "item"
            # Bind inner terminal to item variable
            ctx.bind(inner_term, item_var)

            return ast.For(
                target=ast.Name(id=item_var, ctx=ast.Store()),
                iter=ast.Name(id=array_var, ctx=ast.Load()),
                body=body,
                orelse=[],
            )

        # Use explicit N terminal count if provided
        if n_terminal_var:
            return ast.For(
                target=ast.Name(id="i", ctx=ast.Store()),
                iter=ast.Call(
                    func=ast.Name(id="range", ctx=ast.Load()),
                    args=[parse_expr(n_terminal_var)],
                    keywords=[],
                ),
                body=body,
                orelse=[],
            )

        # Try to find count from first lpTun input's length
        for tunnel in tunnels:
            if tunnel.get("tunnel_type") == "lpTun":
                outer_var = ctx.resolve(tunnel.get("outer_terminal_uid"))
                if outer_var:
                    # Use len(first_input) as iteration count
                    return ast.For(
                        target=ast.Name(id="i", ctx=ast.Store()),
                        iter=ast.Call(
                            func=ast.Name(id="range", ctx=ast.Load()),
                            args=[
                                ast.Call(
                                    func=ast.Name(id="len", ctx=ast.Load()),
                                    args=[parse_expr(outer_var)],
                                    keywords=[],
                                )
                            ],
                            keywords=[],
                        ),
                        body=body,
                        orelse=[],
                    )

        # Absolute fallback
        return ast.For(
            target=ast.Name(id="i", ctx=ast.Store()),
            iter=ast.Call(
                func=ast.Name(id="range", ctx=ast.Load()),
                args=[ast.Constant(value=10)],
                keywords=[],
            ),
            body=body,
            orelse=[],
        )

    def _find_count_source(
        self, tunnels: list[dict], ctx: CodeGenContext
    ) -> str | None:
        """Find the iteration count source from tunnels.

        Looks for lMax tunnel with input wired to it (N terminal).
        """
        for tunnel in tunnels:
            tunnel_type = tunnel.get("tunnel_type")
            outer_term = tunnel.get("outer_terminal_uid")

            # lMax on input side provides count (it's the N terminal)
            # Note: lMax is typically output for accumulation, but for
            # the iteration count it receives a value
            if tunnel_type == "lMax" and outer_term:
                source = ctx.resolve(outer_term)
                if source:
                    return source

        return None

    def _find_autoindex_array(
        self, tunnels: list[dict], ctx: CodeGenContext
    ) -> tuple[str, str] | None:
        """Find an array input for autoindexing.

        Returns (array_var, inner_terminal_uid) if found.
        """
        for tunnel in tunnels:
            tunnel_type = tunnel.get("tunnel_type")
            outer_term = tunnel.get("outer_terminal_uid")
            inner_term = tunnel.get("inner_terminal_uid")

            if tunnel_type == "lpTun" and outer_term and inner_term:
                outer_var = ctx.resolve(outer_term)
                if outer_var:
                    # TODO: Check if this is actually an array type
                    # For now, assume any lpTun input could be autoindexed
                    # This would need type info to be accurate
                    pass

        return None  # Conservative: don't autoindex without type info

    def _has_incoming_flow(self, terminal_uid: str, ctx: CodeGenContext) -> bool:
        """Check if a terminal has any incoming data flow.

        Used to distinguish:
        - lMax as N terminal (no incoming flow to inner terminal)
        - lMax as accumulator (has incoming flow from loop body)
        """
        # Check if this terminal is the destination of any flow
        return terminal_uid in ctx._flow_map

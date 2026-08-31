"""Code generator for flat sequence structures."""

from __future__ import annotations

import ast

from lvkit.graph.models import SequenceNode

from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: SequenceNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate sequential code for each frame in order.

    Flat sequences enforce execution order: frame 0 runs first,
    then frame 1, etc. In Python, this is just sequential code.

    Within each frame, independent operations are parallel (handled
    by generate_body's tiered topological sort).
    """
    if not node.frames:
        return CodeFragment.empty()

    all_statements: list[ast.stmt] = []
    all_bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    tunnels = ctx.tunnels(node)

    # Bind input tunnels: outer values available inside frames
    for tunnel in tunnels:
        if tunnel.tunnel_type in ("seqTun", "flatSeqTun"):
            outer_var = ctx.resolve(tunnel.outer_terminal_uid)
            if outer_var:
                ctx.bind(tunnel.inner_terminal_uid, outer_var)

    # Generate code for each frame sequentially
    for _frame, ops in ctx.frame_children(node):
        frame_stmts = ctx.generate_body(ops)
        for stmt in frame_stmts:
            ast.fix_missing_locations(stmt)
        all_statements.extend(frame_stmts)
        all_imports.update(ctx.imports)

        # Propagate tunnel bindings between frames
        for tunnel in tunnels:
            inner_var = ctx.resolve(tunnel.inner_terminal_uid)
            if inner_var:
                ctx.bind(tunnel.outer_terminal_uid, inner_var)
        for tunnel in tunnels:
            outer_var = ctx.resolve(tunnel.outer_terminal_uid)
            if outer_var:
                ctx.bind(tunnel.inner_terminal_uid, outer_var)

    # Bind output tunnels: inner values available outside
    for tunnel in tunnels:
        if tunnel.tunnel_type in ("seqTun", "flatSeqTun"):
            inner_var = ctx.resolve(tunnel.inner_terminal_uid)
            if inner_var:
                all_bindings[tunnel.outer_terminal_uid] = inner_var

    return CodeFragment(
        statements=all_statements,
        bindings=all_bindings,
        imports=all_imports,
    )

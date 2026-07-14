"""Code generator for the First Call? primitive (primResID 1083).

`First Call?` returns True only the first time its call site executes
after the generated module is imported ("after you click Run" in
LabVIEW terms), False on every execution after that -- stateful per call
site, not a constant. It's a generic ``class="prim"`` XML node
distinguished only by primResID (0 inputs, 1 Boolean output named
``first_call`` in primitives.json) -- dispatched by primResID in
nodes/__init__.py before the node_type registry, same as queue_ops.

Lowering mirrors the LV2/functional-global idiom used for uninitialized
shift registers (see codegen/nodes/loop.py): a module-level boolean flag,
seeded True, is read into a local at the call site and then flipped to
False -- so only the very first call after import observes True.
"""

from __future__ import annotations

import ast

from lvkit.models import PrimitiveOperation

from ..ast_utils import sanitize_state_var_suffix
from ..context import CodeGenContext
from ..fragment import CodeFragment

FIRST_CALL_PRIM_ID = 1083


def generate(node: PrimitiveOperation, ctx: CodeGenContext) -> CodeFragment:
    """Lower a First Call? call site to a module-global-backed flag read."""
    flag_name = f"_lv_first_call_{sanitize_state_var_suffix(node.id)}"
    ctx.add_module_global(flag_name, ast.Constant(value=True))

    statements: list[ast.stmt] = [ast.Global(names=[flag_name])]
    bindings: dict[str, str] = {}

    out_term = next(
        (t for t in node.terminals if t.direction == "output"), None,
    )
    if out_term is not None:
        var_name = ctx.make_output_var(
            "first_call", node.id, terminal_id=out_term.id,
        )
        statements.append(
            ast.Assign(
                targets=[ast.Name(id=var_name, ctx=ast.Store())],
                value=ast.Name(id=flag_name, ctx=ast.Load()),
            )
        )
        bindings[out_term.id] = var_name

    # Flip the flag -- every call after this one sees False. Runs
    # unconditionally: the node fires whenever the block diagram reaches
    # it, regardless of whether the output happens to be wired.
    statements.append(
        ast.Assign(
            targets=[ast.Name(id=flag_name, ctx=ast.Store())],
            value=ast.Constant(value=False),
        )
    )

    return CodeFragment(statements=statements, bindings=bindings)

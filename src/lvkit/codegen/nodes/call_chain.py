"""Code generator for the Call Chain function (primResID 1999), and the
``vi_name`` every generated VI function carries for it.

NI: "Returns the chain of callers from the current VI to the top-level VI. Element 0
of the call chain array contains the name of the lowest VI in the call chain.
Subsequent elements are callers of the lower VIs in the call chain. The last element
of the call chain array is the name of the top-level VI"
(functions/call-chain.html). A VI of a library or a class is named by its qualified
name (``Lib.lvlib:Class.lvclass:Method.vi``; NI community threads on a Call Chain
error 7 show ``AB_Build.lvclass:Build.vi``): ``InMemoryVIGraph.vi_display_name``.

Each function carries its name as ``function.vi_name``, set right after its ``def``
beside ``function.window_title`` (``vi_name_statement``), and the node is
``panels.call_chain()``, which reads Python's own stack (``panels_module``).
"""

from __future__ import annotations

import ast
from pathlib import Path

from lvkit.graph.models import PrimitiveNode

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..panels_module import CALL_CHAIN_NAME

CALL_CHAIN_PRIM_ID = 1999
VI_NAME_ATTRIBUTE = "vi_name"


def vi_name_statement(ctx: CodeGenContext, key: str, target: str) -> ast.stmt:
    """``target.vi_name = "<qualified name>"`` for the VI ``key``."""
    name = (
        ctx.graph.vi_display_name(key) if ctx.graph is not None else Path(key).name
    )
    return ast.Assign(
        targets=[
            ast.Attribute(
                value=parse_expr(target), attr=VI_NAME_ATTRIBUTE, ctx=ast.Store()
            )
        ],
        value=ast.Constant(value=name),
    )


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """``call_chain()`` bound to the call chain output."""
    out_term = next((t for t in node.terminals if t.direction == "output"), None)
    if out_term is None:
        return CodeFragment.empty()
    ctx.import_panels(CALL_CHAIN_NAME)
    var_name = ctx.make_output_var("chain", node.id, terminal_id=out_term.id)
    return CodeFragment(
        statements=[
            ast.Assign(
                targets=[ast.Name(id=var_name, ctx=ast.Store())],
                value=parse_expr(f"{CALL_CHAIN_NAME}()"),
            )
        ],
        bindings={out_term.id: var_name},
    )

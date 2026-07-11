"""Code generator for LabVIEW Queue Operations primitives.

Covers Obtain Queue (9108), Enqueue Element (9111), Enqueue Element At
Opposite End (9129), Dequeue Element (9113), and Get Queue Status (9109).

These are generic `<Prim class="prim">` XML nodes distinguished only by
`primResID` (verified against real samples — see phaseB_findings.md), so
dispatch happens by primResID in `nodes/__init__.py` rather than by
`node_type` (unlike aInit/aReplace/nMux, which have distinct XML classes).

Terminals are resolved by the connector-pane INDEX recorded in
primitives.json (CLAUDE.md's terminal-resolution workflow — never by
name), and each op emits a call into the small stdlib-only runtime in
`lvkit.labview_queue`. Error cluster terminals (error_in / error_out) are
intentionally left unwired here, matching every other primitive handler in
this package — error propagation is handled by the graph-level error
model (`codegen/error_handler.py`), not per-primitive.
"""

from __future__ import annotations

import ast

from lvkit.models import PrimitiveOperation, Terminal

from ..ast_utils import build_multi_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment

OBTAIN_QUEUE = 9108
GET_QUEUE_STATUS = 9109
ENQUEUE_ELEMENT = 9111
DEQUEUE_ELEMENT = 9113
ENQUEUE_AT_OPPOSITE_END = 9129

QUEUE_PRIM_IDS = frozenset(
    {OBTAIN_QUEUE, GET_QUEUE_STATUS, ENQUEUE_ELEMENT, DEQUEUE_ELEMENT,
     ENQUEUE_AT_OPPOSITE_END},
)

_QUEUE_IMPORT = (
    "from lvkit.labview_queue import ("
    "dequeue_element, enqueue_element, enqueue_element_at_opposite_end, "
    "get_queue_status, obtain_queue)"
)


def generate(node: PrimitiveOperation, ctx: CodeGenContext) -> CodeFragment:
    """Dispatch a queue primitive to its handler by primResID."""
    by_index: dict[int, Terminal] = {t.index: t for t in node.terminals}
    prim_id = node.primResID

    if prim_id == OBTAIN_QUEUE:
        return _generate_obtain_queue(node, by_index, ctx)
    if prim_id == ENQUEUE_ELEMENT:
        return _generate_enqueue(node, by_index, ctx, "enqueue_element")
    if prim_id == ENQUEUE_AT_OPPOSITE_END:
        return _generate_enqueue(
            node, by_index, ctx, "enqueue_element_at_opposite_end",
        )
    if prim_id == DEQUEUE_ELEMENT:
        return _generate_dequeue(node, by_index, ctx)
    if prim_id == GET_QUEUE_STATUS:
        return _generate_status(node, by_index, ctx)

    raise ValueError(
        f"queue_ops.generate() called for unsupported prim_id {prim_id!r}; "
        "the dispatch guard in codegen/nodes/__init__.py should only route "
        "QUEUE_PRIM_IDS here",
    )


def _resolve_input(term: Terminal | None, ctx: CodeGenContext, default: str) -> str:
    """Resolve an input terminal's variable name, or a literal default."""
    if term is None:
        return default
    value = ctx.resolve(term.id)
    return value if value else default


def _emit_call(
    node: PrimitiveOperation,
    ctx: CodeGenContext,
    func_name: str,
    arg_exprs: list[str],
    outputs: list[tuple[Terminal | None, str]],
) -> tuple[list[ast.stmt], dict[str, str]]:
    """Build `var1, var2 = func_name(args...)` for present output terminals.

    `outputs` is (terminal-or-None, descriptive base name) pairs, in the
    same order — and same LENGTH — as the runtime function's return
    tuple; call sites always pass one entry per value the runtime
    function returns. A None terminal means that connector-pane slot
    isn't in this node's JSON definition (e.g. `created` for Obtain
    Queue) — its slot is still unpacked (into a throwaway name) since
    the runtime unconditionally returns the full tuple; only dropping
    the *assignment* would misalign a partial-arity unpack against the
    real return value.
    """
    call = ast.Call(
        func=ast.Name(id=func_name, ctx=ast.Load()),
        args=[parse_expr(e) for e in arg_exprs],
        keywords=[],
    )
    if not outputs:
        return [ast.Expr(value=call)], {}

    var_names: list[str] = []
    bindings: dict[str, str] = {}
    for i, (term, base) in enumerate(outputs):
        if term is None:
            var_names.append(f"_unused_{i}")
            continue
        var_name = ctx.make_output_var(base, node.id, terminal_id=term.id)
        bindings[term.id] = var_name
        var_names.append(var_name)

    stmt = build_multi_assign(var_names, call)
    return [stmt], bindings


def _generate_obtain_queue(
    node: PrimitiveOperation, by_index: dict[int, Terminal], ctx: CodeGenContext,
) -> CodeFragment:
    """Obtain Queue(name, element_data_type, max_queue_size) -> handle.

    `element_data_type` (index 1) is a type-carrying terminal only —
    Python is dynamically typed, so it has no runtime effect and is not
    resolved.
    """
    name_val = _resolve_input(by_index.get(0), ctx, "''")
    create_val = _resolve_input(by_index.get(2), ctx, "True")
    max_size_val = _resolve_input(by_index.get(4), ctx, "-1")

    stmts, bindings = _emit_call(
        node, ctx, "obtain_queue",
        [name_val, max_size_val, create_val],
        [(by_index.get(8), "queue"), (by_index.get(10), "created")],
    )
    return CodeFragment(statements=stmts, bindings=bindings, imports={_QUEUE_IMPORT})


def _generate_enqueue(
    node: PrimitiveOperation,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
    func_name: str,
) -> CodeFragment:
    """Enqueue Element / Enqueue Element At Opposite End(queue, element,
    timeout_ms) -> timed_out. `queue_out` is a passthrough of `queue`
    (NI docs: "queue out returns the reference to the queue unchanged").
    """
    queue_val = _resolve_input(by_index.get(0), ctx, "None")
    element_val = _resolve_input(by_index.get(1), ctx, "None")
    timeout_val = _resolve_input(by_index.get(2), ctx, "-1")

    bindings: dict[str, str] = {}
    queue_out_term = by_index.get(8)
    if queue_out_term is not None:
        bindings[queue_out_term.id] = queue_val

    stmts, call_bindings = _emit_call(
        node, ctx, func_name,
        [queue_val, element_val, timeout_val],
        [(by_index.get(10), "timed_out")],
    )
    bindings.update(call_bindings)
    return CodeFragment(statements=stmts, bindings=bindings, imports={_QUEUE_IMPORT})


def _generate_dequeue(
    node: PrimitiveOperation, by_index: dict[int, Terminal], ctx: CodeGenContext,
) -> CodeFragment:
    """Dequeue Element(queue, timeout_ms) -> (element, timed_out).
    `queue_out` is a passthrough of `queue`.
    """
    queue_val = _resolve_input(by_index.get(0), ctx, "None")
    timeout_val = _resolve_input(by_index.get(2), ctx, "-1")

    bindings: dict[str, str] = {}
    queue_out_term = by_index.get(8)
    if queue_out_term is not None:
        bindings[queue_out_term.id] = queue_val

    stmts, call_bindings = _emit_call(
        node, ctx, "dequeue_element",
        [queue_val, timeout_val],
        [(by_index.get(9), "element"), (by_index.get(10), "timed_out")],
    )
    bindings.update(call_bindings)
    return CodeFragment(statements=stmts, bindings=bindings, imports={_QUEUE_IMPORT})


def _generate_status(
    node: PrimitiveOperation, by_index: dict[int, Terminal], ctx: CodeGenContext,
) -> CodeFragment:
    """Get Queue Status(queue, return_elements) -> (name, elements).

    primitives.json only carries `name` (idx 8) and `elements` (idx 9) as
    observed outputs — no separate pending-count terminal was wired in any
    sample this was resolved against, so "pending count" is `len(elements)`
    (requires `return_elements=True`; NI docs default is False).
    """
    queue_val = _resolve_input(by_index.get(0), ctx, "None")
    return_elements_val = _resolve_input(by_index.get(2), ctx, "False")

    stmts, bindings = _emit_call(
        node, ctx, "get_queue_status",
        [queue_val, return_elements_val],
        [(by_index.get(8), "queue_name"), (by_index.get(9), "queue_elements")],
    )
    return CodeFragment(statements=stmts, bindings=bindings, imports={_QUEUE_IMPORT})

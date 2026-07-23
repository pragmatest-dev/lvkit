"""Code generator for LabVIEW Queue Operations primitives.

Covers Obtain Queue (9108), Enqueue Element (9111), Enqueue Element At
Opposite End (9129), Dequeue Element (9113), Release Queue (9109), and Get
Queue Status (9110).

Get Queue Status's 8-output pane has four same-typed I32 outputs (max size,
#pending remove, #pending insert, #elements) that type alone can't tell apart;
they were resolved from the NI connector-pane IMAGE (types by wire colour +
top-to-bottom layout) plus two corpus wiring anchors (#elements feeds "<=0?" in
test Queue Size is Zero.vi; error out -> the VI's error in) and termBounds
geometry -- see _generate_get_queue_status. NOTE: 9109 was historically (and
wrongly) mapped as "Get Queue Status" -- it is actually Release Queue.

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
ENQUEUE_ELEMENT = 9111
DEQUEUE_ELEMENT = 9113
ENQUEUE_AT_OPPOSITE_END = 9129
RELEASE_QUEUE = 9109
GET_QUEUE_STATUS = 9110

QUEUE_PRIM_IDS = frozenset(
    {
        OBTAIN_QUEUE, ENQUEUE_ELEMENT, DEQUEUE_ELEMENT, ENQUEUE_AT_OPPOSITE_END,
        RELEASE_QUEUE, GET_QUEUE_STATUS,
    },
)

_QUEUE_IMPORT = (
    "from lvkit.labview_queue import ("
    "dequeue_element, enqueue_element, enqueue_element_at_opposite_end, "
    "get_queue_status, obtain_queue, release_queue)"
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
    if prim_id == RELEASE_QUEUE:
        return _generate_release_queue(node, by_index, ctx)
    if prim_id == GET_QUEUE_STATUS:
        return _generate_get_queue_status(node, by_index, ctx)

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


def _generate_release_queue(
    node: PrimitiveOperation, by_index: dict[int, Terminal], ctx: CodeGenContext,
) -> CodeFragment:
    """Release Queue(queue, force destroy?) -> (queue name, remaining elements).

    Observed pane (NI-confirmed): in idx0=queue, idx2=force destroy? (F),
    idx3=error in; out idx8=queue name (String), idx9=remaining elements
    (Array, adapts to the queue subtype), idx11=error out. The queue-name
    output is the queue's own ``.name`` (it survives the destroy), bound as an
    expression rather than a runtime return; error in/out flow through the
    general error machinery like the other queue ops.
    """
    queue_val = _resolve_input(by_index.get(0), ctx, "None")
    force_val = _resolve_input(by_index.get(2), ctx, "False")

    bindings: dict[str, str] = {}
    name_term = by_index.get(8)
    if name_term is not None:
        bindings[name_term.id] = f"{queue_val}.name"

    stmts, call_bindings = _emit_call(
        node, ctx, "release_queue",
        [queue_val, force_val],
        [(by_index.get(9), "remaining_elements")],
    )
    bindings.update(call_bindings)
    return CodeFragment(statements=stmts, bindings=bindings, imports={_QUEUE_IMPORT})


def _generate_get_queue_status(
    node: PrimitiveOperation, by_index: dict[int, Terminal], ctx: CodeGenContext,
) -> CodeFragment:
    """Get Queue Status(queue, return elements?) -> a QueueStatus dataclass.

    Pane resolved from the corpus + NI's connector-pane image (types by wire
    colour) + two wiring anchors (idx7 feeds "<=0?"; idx11 -> VI error in):
      in  idx0=queue, idx1=return elements?, idx3=error in
      out idx4=max queue size, idx5=elements, idx6=queue name,
          idx7=# elements in queue, idx8=queue out (passthrough),
          idx9=# pending remove, idx10=# pending insert, idx11=error out
    The runtime returns ONE QueueStatus; each consumed output binds to a field
    of it (or the queue passthrough). error in/out flow through the general
    error model like the other queue ops.
    """
    queue_val = _resolve_input(by_index.get(0), ctx, "None")
    return_elems_val = _resolve_input(by_index.get(1), ctx, "False")

    bindings: dict[str, str] = {}
    queue_out = by_index.get(8)
    if queue_out is not None:  # queue out is a passthrough of the input queue
        bindings[queue_out.id] = queue_val

    field_by_index = {
        4: "max_size", 5: "elements", 6: "name", 7: "n_elements",
        9: "pending_remove", 10: "pending_insert",
    }
    wanted = {i: by_index[i] for i in field_by_index if i in by_index}
    if not wanted:
        # get_queue_status is a side-effect-free read; if nothing but the queue
        # passthrough / error is consumed, emit no call.
        return CodeFragment(statements=[], bindings=bindings, imports={_QUEUE_IMPORT})

    status_var = ctx.make_output_var("queue_status", node.id, terminal_id=node.id)
    call = ast.Call(
        func=ast.Name(id="get_queue_status", ctx=ast.Load()),
        args=[parse_expr(queue_val), parse_expr(return_elems_val)],
        keywords=[],
    )
    stmt = build_multi_assign([status_var], call)
    for i, term in wanted.items():
        bindings[term.id] = f"{status_var}.{field_by_index[i]}"
    return CodeFragment(statements=[stmt], bindings=bindings, imports={_QUEUE_IMPORT})

"""Code generator for the Queue Operations primitives.

Covers Obtain Queue (9108), Release Queue (9109), Get Queue Status (9110),
Enqueue Element (9111), Preview Queue Element (9112), Dequeue Element (9113),
Flush Queue (9114), and Enqueue Element At Opposite End (9129).

A queue is a ``queue.Queue``. An Obtain Queue with no name is a ``Queue`` made
at that point. One with a constant name refers to the one queue of that name in
the package's ``queues`` module (see ``queue_module``); one whose name is
computed at run time is an entry of that module's ``by_name`` dict. A timeout
is the ``timeout`` of ``put`` / ``get``,
and a queue that times out raises ``queue.Full`` / ``queue.Empty``; when the
operation's ``timed out?`` output is used, the call is wrapped in ``try`` /
``except`` and the output is a local ``bool``. Release Queue shuts the queue
down, which needs Python 3.13.

Get Queue Status's pane has four same-typed I32 outputs (max size, #pending
remove, #pending insert, #elements) that type alone can't tell apart; they were
resolved from the NI connector-pane IMAGE (types by wire colour + top-to-bottom
layout) plus two corpus wiring anchors (#elements feeds "<=0?" in test Queue Size
is Zero.vi; error out -> the VI's error in) and termBounds geometry.

These are generic ``<Prim class="prim">`` XML nodes distinguished only by
``primResID``, so dispatch happens by primResID in ``nodes/__init__.py`` rather
than by ``node_type``. Terminals are resolved by the connector-pane INDEX
recorded in primitives.json, never by name. Error cluster terminals are left
unwired here: errors are Python exceptions.
"""

from __future__ import annotations

import ast
from enum import IntEnum

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ..ast_utils import default_value_expr, parse_expr
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver, ModuleImport
from ..queue_module import PackageQueues, QueueUse


class QueueOp(IntEnum):
    """Queue-operation ``primResID`` codes. ``IntEnum`` because the code IS the
    raw int carried on ``PrimitiveNode.prim_id`` -- a member compares equal to it
    and lives in the ``QUEUE_PRIM_IDS`` membership set."""

    OBTAIN = 9108
    RELEASE = 9109
    GET_STATUS = 9110
    ENQUEUE = 9111
    PREVIEW = 9112
    DEQUEUE = 9113
    FLUSH = 9114
    ENQUEUE_OPPOSITE_END = 9129


# Membership set for the nodes/__init__ dispatcher; an int primResID matches a
# member (IntEnum hashes/compares as its int).
QUEUE_PRIM_IDS = frozenset(QueueOp)

_QUEUE_IMPORT = "from queue import Empty, Full, Queue"


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Dispatch a queue primitive to its handler by primResID."""
    by_index: dict[int, Terminal] = {t.index: t for t in node.terminals}
    prim_id = node.prim_id

    if prim_id == QueueOp.OBTAIN:
        return _generate_obtain_queue(node, by_index, ctx)
    if prim_id == QueueOp.ENQUEUE:
        return _generate_enqueue(node, by_index, ctx)
    if prim_id == QueueOp.ENQUEUE_OPPOSITE_END:
        return _generate_enqueue_opposite_end(node, by_index, ctx)
    if prim_id == QueueOp.DEQUEUE:
        return _generate_dequeue(node, by_index, ctx)
    if prim_id == QueueOp.PREVIEW:
        return _generate_preview(node, by_index, ctx)
    if prim_id == QueueOp.FLUSH:
        return _generate_flush(node, by_index, ctx)
    if prim_id == QueueOp.RELEASE:
        return _generate_release_queue(node, by_index, ctx)
    if prim_id == QueueOp.GET_STATUS:
        return _generate_get_queue_status(node, by_index, ctx)

    raise ValueError(
        f"queue_ops.generate() called for unsupported prim_id {prim_id!r}; "
        "the dispatch guard in codegen/nodes/__init__.py should only route "
        "QUEUE_PRIM_IDS here",
    )


def _input(
    term: Terminal | None, node: PrimitiveNode, ctx: CodeGenContext
) -> str | None:
    """The expression an input terminal carries, or None when it is unwired."""
    if term is None:
        return None
    value = ctx.wired_input(term.id, node)
    return value if value else None


def _queue_input(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> str:
    queue = _input(by_index.get(0), node, ctx)
    if queue is None:
        raise ConversionGap(f"{node.name} has no queue wired", node)
    return queue


def _consumed(term: Terminal | None, ctx: CodeGenContext) -> Terminal | None:
    """``term`` when something reads it, else None."""
    if term is not None and not term.is_error_cluster and ctx.is_wired(term.id):
        return term
    return None


def _constant(expression: str) -> object:
    """The value of a constant ``expression``; None when it is not a constant."""
    try:
        return ast.literal_eval(expression)
    except (ValueError, SyntaxError):
        return None


def _timeout_seconds(milliseconds: str | None) -> str | None:
    """The ``timeout`` argument for a wait of ``milliseconds`` (an expression):
    None blocks without limit, which is an unwired timeout and any negative one."""
    if milliseconds is None:
        return None
    value = _constant(milliseconds)
    if value is None:
        return f"None if {milliseconds} < 0 else {milliseconds} / 1000"
    if isinstance(value, (int, float)):
        return None if value < 0 else repr(value / 1000)
    raise ConversionGap(f"Queue timeout {milliseconds} is not a number")


def _call(queue: str, method: str, args: list[str], timeout: str | None) -> ast.expr:
    """``queue.method(args, timeout=...)``, with no ``timeout`` to block."""
    call = f"{queue}.{method}({', '.join(args)})"
    if timeout is not None:
        call = f"{queue}.{method}({', '.join([*args, f'timeout={timeout}'])})"
    return parse_expr(call)


def _assign(name: str, value: ast.expr) -> ast.stmt:
    return ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=value)


def _flag(name: str, value: bool) -> ast.stmt:
    return _assign(name, ast.Constant(value=value))


def _try_timeout(
    attempt: list[ast.stmt], exception: str, on_timeout: list[ast.stmt]
) -> ast.stmt:
    return ast.Try(
        body=attempt,
        handlers=[
            ast.ExceptHandler(
                type=ast.Name(id=exception, ctx=ast.Load()),
                name=None,
                body=on_timeout,
            )
        ],
        orelse=[],
        finalbody=[],
    )


def _passthrough(
    by_index: dict[int, Terminal], index: int, queue: str
) -> dict[str, str]:
    """The ``queue out`` output is the queue that went in."""
    term = by_index.get(index)
    return {term.id: queue} if term is not None else {}


def _obtain_queue_use(
    node: PrimitiveNode, ctx: CodeGenContext
) -> tuple[QueueUse, ModuleImport | None]:
    """How the Obtain Queue ``node`` names its queue, and the import of the
    package's ``queues`` module. A package answers from the query made over all
    its VIs; a VI generated alone is asked about itself, and has no module to
    hold a named queue."""
    resolver = ctx.import_resolver
    if isinstance(resolver, ImportResolver) and resolver.has_package_modules:
        use, module = resolver.queue_use(node)
        return use, module
    if ctx.graph is None:
        raise CodeGenError("An Obtain Queue needs the VI graph", node)
    use = PackageQueues.from_uses(
        ctx.graph.obtain_queue_uses([ctx.vi_name or ""])
    ).use_of(node.id, node)
    if use.error is not None:
        raise ConversionGap(use.error, node)
    if use.computed or use.attribute is not None:
        raise CodeGenError(
            f"Queue '{use.name}' is named, and there is no generated package to "
            "define it in",
            node,
        )
    return use, None


def _generate_obtain_queue(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
) -> CodeFragment:
    """Obtain Queue(name, element_data_type, create_if_not_found?, max_queue_size)
    -> queue.

    ``element_data_type`` (index 1) only carries a type. A constant name is the
    queue of that name in the ``queues`` module, a name computed at run time is
    its entry in that module's ``by_name`` dict, and no name is a new ``Queue``.
    Which of the three it is comes from the package's queries."""
    use, module = _obtain_queue_use(node, ctx)
    name_text = _input(by_index.get(0), node, ctx)
    max_size_text = _input(by_index.get(4), node, ctx)
    size = _constant(max_size_text) if max_size_text is not None else -1
    bound = str(max(size, 0)) if isinstance(size, int) else f"max({max_size_text}, 0)"

    queue_term = _consumed(by_index.get(8), ctx)
    created_term = _consumed(by_index.get(10), ctx)
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    imports: set[str] = set()
    created_var = (
        ctx.make_output_var("created", node.id, created_term.id)
        if created_term is not None
        else None
    )
    if created_term is not None and created_var is not None:
        bindings[created_term.id] = created_var
    registry = f"{module.alias}.by_name" if module is not None else ""
    if module is not None and use.computed and name_text is not None:
        imports.update({module.statement, _QUEUE_IMPORT})
        if created_var is not None:
            statements.append(
                _assign(created_var, parse_expr(f"{name_text} not in {registry}"))
            )
        value = parse_expr(
            f"{registry}.setdefault({name_text}, Queue(maxsize={bound}))"
        )
    elif module is not None and use.attribute is not None:
        imports.add(module.statement)
        value = parse_expr(f"{module.alias}.{use.attribute}")
        if created_var is not None:
            obtained = f"{module.alias}.obtained"
            statements.append(
                _assign(created_var, parse_expr(f"{use.name!r} not in {obtained}"))
            )
            statements.append(
                ast.Expr(value=parse_expr(f"{obtained}.add({use.name!r})"))
            )
    else:
        value = parse_expr(f"Queue(maxsize={bound})")
        imports.add(_QUEUE_IMPORT)
        if created_var is not None:
            statements.append(_flag(created_var, True))
    if queue_term is not None:
        queue_var = ctx.make_output_var("queue", node.id, queue_term.id)
        statements.append(_assign(queue_var, value))
        bindings[queue_term.id] = queue_var
    elif use.computed:
        statements.append(ast.Expr(value=value))
    return CodeFragment(statements=statements, bindings=bindings, imports=imports)


def _generate_enqueue(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
) -> CodeFragment:
    """Enqueue Element(queue, element, timeout_ms) -> timed out?. A full queue
    waits for room; past the timeout ``put`` raises ``queue.Full``."""
    queue = _queue_input(node, by_index, ctx)
    element = _input(by_index.get(1), node, ctx) or "None"
    timeout = _timeout_seconds(_input(by_index.get(2), node, ctx))
    bindings = _passthrough(by_index, 8, queue)
    put = ast.Expr(value=_call(queue, "put", [element], timeout))
    timed_out = _consumed(by_index.get(10), ctx)
    if timed_out is None:
        return CodeFragment(statements=[put], bindings=bindings)
    flag = ctx.make_output_var("timed_out", node.id, timed_out.id)
    bindings[timed_out.id] = flag
    attempt = _try_timeout([put, _flag(flag, False)], "Full", [_flag(flag, True)])
    return CodeFragment(
        statements=[attempt], bindings=bindings, imports={_QUEUE_IMPORT}
    )


def _generate_enqueue_opposite_end(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
) -> CodeFragment:
    """Enqueue Element At Opposite End(queue, element, timeout_ms) -> timed out?.
    ``queue.Queue`` only puts at the back, so the element goes on the front of
    its ``deque`` under the queue's lock, waking one waiting ``get``."""
    queue = _queue_input(node, by_index, ctx)
    element = _input(by_index.get(1), node, ctx) or "None"
    bindings = _passthrough(by_index, 8, queue)
    front = ast.parse(
        f"with {queue}.mutex:\n"
        f"    {queue}.queue.appendleft({element})\n"
        f"    {queue}.unfinished_tasks += 1\n"
        f"    {queue}.not_empty.notify()\n"
    ).body
    statements: list[ast.stmt] = list(front)
    timed_out = _consumed(by_index.get(10), ctx)
    if timed_out is not None:
        flag = ctx.make_output_var("timed_out", node.id, timed_out.id)
        statements.append(_flag(flag, False))
        bindings[timed_out.id] = flag
    return CodeFragment(statements=statements, bindings=bindings)


def _generate_dequeue(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
) -> CodeFragment:
    """Dequeue Element(queue, timeout_ms) -> (element, timed out?). An empty
    queue waits; past the timeout ``get`` raises ``queue.Empty``, or, when
    ``timed out?`` is used, the element is the type's default and the flag
    is true."""
    queue = _queue_input(node, by_index, ctx)
    timeout = _timeout_seconds(_input(by_index.get(2), node, ctx))
    bindings = _passthrough(by_index, 8, queue)
    get = _call(queue, "get", [], timeout)
    element_term = _consumed(by_index.get(9), ctx)
    timed_out = _consumed(by_index.get(10), ctx)
    if timed_out is None:
        if element_term is None:
            return CodeFragment(statements=[ast.Expr(value=get)], bindings=bindings)
        element = ctx.make_output_var("element", node.id, element_term.id)
        bindings[element_term.id] = element
        return CodeFragment(statements=[_assign(element, get)], bindings=bindings)
    flag = ctx.make_output_var("timed_out", node.id, timed_out.id)
    bindings[timed_out.id] = flag
    attempt: list[ast.stmt] = []
    on_timeout: list[ast.stmt] = []
    if element_term is None:
        attempt.append(ast.Expr(value=get))
    else:
        element = ctx.make_output_var("element", node.id, element_term.id)
        bindings[element_term.id] = element
        attempt.append(_assign(element, get))
        on_timeout.append(_assign(element, default_value_expr(element_term.lv_type)))
    attempt.append(_flag(flag, False))
    on_timeout.append(_flag(flag, True))
    return CodeFragment(
        statements=[_try_timeout(attempt, "Empty", on_timeout)],
        bindings=bindings,
        imports={_QUEUE_IMPORT},
    )


def _generate_preview(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
) -> CodeFragment:
    """Preview Queue Element(queue, timeout_ms) -> (element, timed out?): the
    front element, left in the queue. Under the queue's lock, the condition
    ``not_empty`` waits until the queue holds an element or the timeout passes."""
    queue = _queue_input(node, by_index, ctx)
    timeout = _timeout_seconds(_input(by_index.get(2), node, ctx))
    bindings = _passthrough(by_index, 8, queue)
    element_term = _consumed(by_index.get(9), ctx)
    timed_out = _consumed(by_index.get(10), ctx)
    element = (
        ctx.make_output_var("element", node.id, element_term.id)
        if element_term is not None
        else "_element"
    )
    flag = (
        ctx.make_output_var("timed_out", node.id, timed_out.id)
        if timed_out is not None
        else None
    )
    default = default_value_expr(element_term.lv_type if element_term else None)
    wait = f"{queue}.not_empty.wait_for(lambda: {queue}.queue"
    wait += ")" if timeout is None else f", {timeout})"
    peek = f"{queue}.queue[0] if {queue}.queue else {ast.unparse(default)}"
    statements = ast.parse(
        f"with {queue}.not_empty:\n"
        f"    {'found' if flag else '_'} = {wait}\n"
        f"    {element} = {peek}\n" + (f"    {flag} = not found\n" if flag else "")
    ).body
    if element_term is not None:
        bindings[element_term.id] = element
    if timed_out is not None and flag is not None:
        bindings[timed_out.id] = flag
    return CodeFragment(statements=statements, bindings=bindings)


def _drain(queue: str, into: str) -> list[ast.stmt]:
    """``into`` is the list of every element taken out of ``queue``, front first."""
    return ast.parse(
        f"{into} = []\n"
        "while True:\n"
        "    try:\n"
        f"        {into}.append({queue}.get_nowait())\n"
        "    except Empty:\n"
        "        break\n"
    ).body


def _generate_flush(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
) -> CodeFragment:
    """Flush Queue(queue) -> remaining elements: take every element out."""
    queue = _queue_input(node, by_index, ctx)
    bindings = _passthrough(by_index, 8, queue)
    remaining = _consumed(by_index.get(9), ctx)
    name = (
        ctx.make_output_var("remaining_elements", node.id, remaining.id)
        if remaining is not None
        else "_flushed"
    )
    if remaining is not None:
        bindings[remaining.id] = name
    return CodeFragment(
        statements=_drain(queue, name), bindings=bindings, imports={_QUEUE_IMPORT}
    )


def _generate_release_queue(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
) -> CodeFragment:
    """Release Queue(queue, force destroy?) -> (queue name, remaining elements).

    Observed pane (NI-confirmed): in idx0=queue, idx2=force destroy? (F),
    idx3=error in; out idx8=queue name (String), idx9=remaining elements,
    idx11=error out. Releasing shuts the queue down (Python 3.13+): ``shutdown()``
    stops further puts and ends a waiting ``get`` once the queue is empty;
    ``shutdown(immediate=True)``, for a true ``force destroy?``, also drops what
    is left and ends every waiting ``get`` and ``put`` with ``queue.ShutDown``.
    The remaining elements, when used, are drained out first. A queue has no
    name of its own, so a used ``queue name`` stops generation."""
    queue = _queue_input(node, by_index, ctx)
    if _consumed(by_index.get(8), ctx) is not None:
        raise ConversionGap(
            "Release Queue uses its 'queue name' output; a queue.Queue has no name",
            node,
        )
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    remaining = _consumed(by_index.get(9), ctx)
    if remaining is not None:
        name = ctx.make_output_var("remaining_elements", node.id, remaining.id)
        statements.extend(_drain(queue, name))
        bindings[remaining.id] = name
    force = _input(by_index.get(2), node, ctx)
    call = f"{queue}.shutdown()"
    if force is not None and _constant(force) is not False:
        call = f"{queue}.shutdown(immediate={force})"
    statements.append(ast.Expr(value=parse_expr(call)))
    return CodeFragment(
        statements=statements, bindings=bindings, imports={_QUEUE_IMPORT}
    )


def _generate_get_queue_status(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
) -> CodeFragment:
    """Get Queue Status(queue, return elements?) -> sizes and elements.

    Pane resolved from the corpus + NI's connector-pane image (types by wire
    colour) + two wiring anchors (idx7 feeds "<=0?"; idx11 -> VI error in):
      in  idx0=queue, idx1=return elements?, idx3=error in
      out idx4=max queue size, idx5=elements, idx6=queue name,
          idx7=# elements in queue, idx8=queue out (passthrough),
          idx9=# pending remove, idx10=# pending insert, idx11=error out
    ``queue.Queue`` has the maximum size (0 is unbounded, -1 here), the count
    and, under its lock, the elements; it has no name and does not count the
    threads waiting on it, so a used ``queue name``, ``# pending remove`` or
    ``# pending insert`` stops generation."""
    queue = _queue_input(node, by_index, ctx)
    bindings = _passthrough(by_index, 8, queue)
    statements: list[ast.stmt] = []
    for index, what in (
        (6, "queue name"),
        (9, "# pending remove"),
        (10, "# pending insert"),
    ):
        if _consumed(by_index.get(index), ctx) is not None:
            raise ConversionGap(
                f"Get Queue Status uses its '{what}' output, which queue.Queue "
                "does not provide",
                node,
            )
    expressions = {4: f"{queue}.maxsize or -1", 7: f"{queue}.qsize()"}
    for index, base in ((4, "max_queue_size"), (7, "elements_in_queue")):
        term = _consumed(by_index.get(index), ctx)
        if term is not None:
            var = ctx.make_output_var(base, node.id, term.id)
            statements.append(_assign(var, parse_expr(expressions[index])))
            bindings[term.id] = var
    elements = _consumed(by_index.get(5), ctx)
    if elements is not None:
        var = ctx.make_output_var("elements", node.id, elements.id)
        statements.extend(
            ast.parse(f"with {queue}.mutex:\n    {var} = list({queue}.queue)\n").body
        )
        bindings[elements.id] = var
    return CodeFragment(statements=statements, bindings=bindings)

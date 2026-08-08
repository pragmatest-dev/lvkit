"""Tests for Queue Operations codegen (Obtain/Enqueue/Dequeue/Release/Status).

Mirrors the pattern in tests/test_array_ops_codegen.py: build
PrimitiveOperation + CodeGenContext by hand for each node in a small
chain, generate each fragment, then COMPILE AND EXECUTE the combined
statements and assert on the real runtime output (queue contents,
timed_out flags, ordering) -- not just that the code parses.
"""

from __future__ import annotations

import ast

import pytest

import lvkit.labview_queue as labview_queue
from lvkit.codegen.nodes import queue_ops
from lvkit.labview_queue import (
    dequeue_element,
    enqueue_element,
    enqueue_element_at_opposite_end,
    get_queue_status,
    obtain_queue,
    release_queue,
)
from lvkit.models import PrimitiveOperation, Terminal
from tests.helpers import make_ctx


@pytest.fixture(autouse=True)
def _clear_named_queue_registry():
    """Named queues live in a process-global registry (matches LabVIEW's
    process-wide named-queue semantics) -- isolate each test from it."""
    labview_queue._QUEUE_REGISTRY.clear()
    yield
    labview_queue._QUEUE_REGISTRY.clear()

_RUNTIME_GLOBALS = {
    "obtain_queue": obtain_queue,
    "enqueue_element": enqueue_element,
    "enqueue_element_at_opposite_end": enqueue_element_at_opposite_end,
    "dequeue_element": dequeue_element,
    "release_queue": release_queue,
    "get_queue_status": get_queue_status,
}


def _compile_and_run(statements: list, local_vars: dict) -> dict:
    """Compile statements and execute, returning resulting locals.

    Supplies the real runtime functions as globals since real generated
    modules hoist ``fragment.imports`` ("from lvkit.labview_queue import
    ...") to the module header -- here we exec just the fragments'
    statements directly, in isolation.
    """
    module = ast.Module(body=statements, type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, "<test>", "exec")
    exec(code, dict(_RUNTIME_GLOBALS), local_vars)
    return local_vars


def _terminal(tid: str, index: int, direction: str) -> Terminal:
    return Terminal(id=tid, index=index, direction=direction)


def _obtain_op(
    node_id: str = "obtain",
    *,
    with_name: bool = False,
    with_create: bool = False,
    with_max: bool = False,
    with_created: bool = False,
) -> PrimitiveOperation:
    terminals = []
    if with_name:
        terminals.append(_terminal(f"{node_id}.name", 0, "input"))
    if with_create:
        terminals.append(_terminal(f"{node_id}.create", 2, "input"))
    if with_max:
        terminals.append(_terminal(f"{node_id}.max", 4, "input"))
    terminals.append(_terminal(f"{node_id}.queue_out", 8, "output"))
    if with_created:
        terminals.append(_terminal(f"{node_id}.created", 10, "output"))
    return PrimitiveOperation(
        id=node_id,
        name="Obtain Queue",
        kind="primitive",
        node_type="prim",
        primResID=9108,
        terminals=terminals,
    )


def _enqueue_op(
    node_id: str, *, opposite_end: bool = False, with_timed_out: bool = True,
) -> PrimitiveOperation:
    terminals = [
        _terminal(f"{node_id}.queue", 0, "input"),
        _terminal(f"{node_id}.element", 1, "input"),
        _terminal(f"{node_id}.timeout_ms", 2, "input"),
        _terminal(f"{node_id}.queue_out", 8, "output"),
    ]
    if with_timed_out:
        terminals.append(_terminal(f"{node_id}.timed_out", 10, "output"))
    return PrimitiveOperation(
        id=node_id,
        name="Enqueue Element At Opposite End" if opposite_end else "Enqueue Element",
        kind="primitive",
        node_type="prim",
        primResID=9129 if opposite_end else 9111,
        terminals=terminals,
    )


def _dequeue_op(node_id: str = "dequeue") -> PrimitiveOperation:
    terminals = [
        _terminal(f"{node_id}.queue", 0, "input"),
        _terminal(f"{node_id}.timeout_ms", 2, "input"),
        _terminal(f"{node_id}.queue_out", 8, "output"),
        _terminal(f"{node_id}.element", 9, "output"),
        _terminal(f"{node_id}.timed_out", 10, "output"),
    ]
    return PrimitiveOperation(
        id=node_id,
        name="Dequeue Element",
        kind="primitive",
        node_type="prim",
        primResID=9113,
        terminals=terminals,
    )


def _release_op(node_id: str = "release", *, force: bool = False) -> PrimitiveOperation:
    """Release Queue pane (observed): in idx0=queue, idx2=force destroy?;
    out idx8=queue name, idx9=remaining elements (error terminals omitted, as
    in the other builders)."""
    terminals = [_terminal(f"{node_id}.queue", 0, "input")]
    if force:
        terminals.append(_terminal(f"{node_id}.force", 2, "input"))
    terminals += [
        _terminal(f"{node_id}.queue_name", 8, "output"),
        _terminal(f"{node_id}.remaining", 9, "output"),
    ]
    return PrimitiveOperation(
        id=node_id, name="Release Queue", kind="primitive",
        node_type="prim", primResID=9109, terminals=terminals,
    )


class TestDispatch:
    """queue_ops.generate() is reachable via the primResID dispatch guard
    in nodes/__init__.py, not via node_type (all ops share the generic
    ``class="prim"`` XML node_type)."""

    def test_dispatch_guard_covers_all_prim_ids(self):
        from lvkit.codegen.nodes import generate as generate_node

        for prim_id, op in (
            (9108, _obtain_op()),
            (9111, _enqueue_op("enq")),
            (9129, _enqueue_op("enq", opposite_end=True)),
            (9113, _dequeue_op()),
            (9109, _release_op()),
            (9110, _status_op()),
        ):
            assert op.primResID == prim_id
            ctx = make_ctx(*(t.id for t in op.terminals))
            fragment = generate_node(op, ctx)
            assert fragment.statements, f"prim {prim_id} produced no statements"
            assert queue_ops._QUEUE_IMPORT in fragment.imports


class TestObtainEnqueueDequeueRoundTrip:
    """Obtain -> Enqueue -> Dequeue preserves the element and threads the
    queue reference through `queue_out` passthroughs."""

    def test_round_trip_preserves_element(self):
        obtain = _obtain_op()
        enqueue = _enqueue_op("enqueue")
        dequeue = _dequeue_op()

        ids = [t.id for op in (obtain, enqueue, dequeue) for t in op.terminals]
        ctx = make_ctx(*ids)

        obtain_frag = queue_ops.generate(obtain, ctx)
        queue_var = obtain_frag.bindings["obtain.queue_out"]

        ctx.bind("enqueue.queue", queue_var)
        ctx.bind("enqueue.element", "payload")
        enqueue_frag = queue_ops.generate(enqueue, ctx)

        ctx.bind("dequeue.queue", enqueue_frag.bindings["enqueue.queue_out"])
        dequeue_frag = queue_ops.generate(dequeue, ctx)

        statements = (
            obtain_frag.statements + enqueue_frag.statements + dequeue_frag.statements
        )
        result = _compile_and_run(statements, {"payload": {"x": 1, "y": 2}})

        assert result[dequeue_frag.bindings["dequeue.element"]] == {"x": 1, "y": 2}
        assert result[dequeue_frag.bindings["dequeue.timed_out"]] is False
        assert result[enqueue_frag.bindings["enqueue.timed_out"]] is False

    def test_queue_out_is_a_passthrough_not_a_new_statement(self):
        """NI docs: 'queue out returns the reference to the queue
        unchanged' -- codegen should bind it directly to the input queue
        variable rather than emitting an assignment."""
        obtain = _obtain_op()
        enqueue = _enqueue_op("enqueue")

        ids = [t.id for op in (obtain, enqueue) for t in op.terminals]
        ctx = make_ctx(*ids)

        obtain_frag = queue_ops.generate(obtain, ctx)
        queue_var = obtain_frag.bindings["obtain.queue_out"]

        ctx.bind("enqueue.queue", queue_var)
        ctx.bind("enqueue.element", "1")
        enqueue_frag = queue_ops.generate(enqueue, ctx)

        assert enqueue_frag.bindings["enqueue.queue_out"] == queue_var
        # Only the enqueue_element(...) call itself, no extra "queue_out = queue" line.
        assert len(enqueue_frag.statements) == 1


class TestEnqueueTimeout:
    """timeout_ms=0 on a full bounded queue is a single non-blocking
    attempt -- it must report timed_out=True rather than block."""

    def test_nonblocking_enqueue_on_full_queue_times_out(self):
        obtain = _obtain_op(with_max=True)
        enq1 = _enqueue_op("enq1")
        enq2 = _enqueue_op("enq2")

        ops = (obtain, enq1, enq2)
        ids = [t.id for op in ops for t in op.terminals]
        ctx = make_ctx(*ids)

        ctx.bind("obtain.max", "1")
        obtain_frag = queue_ops.generate(obtain, ctx)
        queue_var = obtain_frag.bindings["obtain.queue_out"]

        # First enqueue fills the bounded (max_size=1) queue.
        ctx.bind("enq1.queue", queue_var)
        ctx.bind("enq1.element", "'first'")
        ctx.bind("enq1.timeout_ms", "-1")
        enq1_frag = queue_ops.generate(enq1, ctx)

        # Second enqueue: non-blocking (timeout_ms=0) on the now-full queue.
        ctx.bind("enq2.queue", enq1_frag.bindings["enq1.queue_out"])
        ctx.bind("enq2.element", "'second'")
        ctx.bind("enq2.timeout_ms", "0")
        enq2_frag = queue_ops.generate(enq2, ctx)

        statements = (
            obtain_frag.statements + enq1_frag.statements + enq2_frag.statements
        )
        result = _compile_and_run(statements, {})

        assert result[enq1_frag.bindings["enq1.timed_out"]] is False
        assert result[enq2_frag.bindings["enq2.timed_out"]] is True


class TestEnqueueAtOppositeEnd:
    """Enqueue Element At Opposite End inserts at the FRONT -- the next
    Dequeue Element must return it first, even though a normal Enqueue
    Element happened earlier."""

    def test_opposite_end_element_dequeued_first(self):
        obtain = _obtain_op()
        enq_back = _enqueue_op("back")
        enq_front = _enqueue_op("front", opposite_end=True)
        dequeue = _dequeue_op()

        ops = (obtain, enq_back, enq_front, dequeue)
        ids = [t.id for op in ops for t in op.terminals]
        ctx = make_ctx(*ids)

        obtain_frag = queue_ops.generate(obtain, ctx)
        queue_var = obtain_frag.bindings["obtain.queue_out"]

        ctx.bind("back.queue", queue_var)
        ctx.bind("back.element", "'a'")
        back_frag = queue_ops.generate(enq_back, ctx)

        ctx.bind("front.queue", back_frag.bindings["back.queue_out"])
        ctx.bind("front.element", "'b'")
        front_frag = queue_ops.generate(enq_front, ctx)

        ctx.bind("dequeue.queue", front_frag.bindings["front.queue_out"])
        dequeue_frag = queue_ops.generate(dequeue, ctx)

        statements = (
            obtain_frag.statements
            + back_frag.statements
            + front_frag.statements
            + dequeue_frag.statements
        )
        result = _compile_and_run(statements, {})

        assert result[dequeue_frag.bindings["dequeue.element"]] == "b"


class TestObtainQueueNamedRegistry:
    """A non-empty name shares ONE queue instance across Obtain Queue
    calls -- unnamed (empty-string) queues never share."""

    def test_same_name_shares_one_queue_instance(self):
        obtain1 = _obtain_op("obtain1", with_name=True, with_created=True)
        obtain2 = _obtain_op("obtain2", with_name=True, with_created=True)

        ids = [t.id for op in (obtain1, obtain2) for t in op.terminals]
        ctx = make_ctx(*ids)

        ctx.bind("obtain1.name", "'shared'")
        frag1 = queue_ops.generate(obtain1, ctx)

        ctx.bind("obtain2.name", "'shared'")
        frag2 = queue_ops.generate(obtain2, ctx)

        result = _compile_and_run(frag1.statements + frag2.statements, {})

        q1 = result[frag1.bindings["obtain1.queue_out"]]
        q2 = result[frag2.bindings["obtain2.queue_out"]]
        assert q1 is q2
        assert result[frag1.bindings["obtain1.created"]] is True
        assert result[frag2.bindings["obtain2.created"]] is False

    def test_empty_name_creates_distinct_queues(self):
        obtain1 = _obtain_op("obtain1")
        obtain2 = _obtain_op("obtain2")

        ids = [t.id for op in (obtain1, obtain2) for t in op.terminals]
        ctx = make_ctx(*ids)

        frag1 = queue_ops.generate(obtain1, ctx)
        frag2 = queue_ops.generate(obtain2, ctx)

        result = _compile_and_run(frag1.statements + frag2.statements, {})

        q1 = result[frag1.bindings["obtain1.queue_out"]]
        q2 = result[frag2.bindings["obtain2.queue_out"]]
        assert q1 is not q2


class TestReleaseQueue:
    """Release Queue (9109) codegen: force-destroy returns the remaining
    elements, and the queue-name output is bound to the queue's .name."""

    def test_force_destroy_returns_remaining_elements(self):
        obtain = _obtain_op()
        enqueue = _enqueue_op("enqueue")
        release = _release_op("release", force=True)
        ids = [t.id for op in (obtain, enqueue, release) for t in op.terminals]
        ctx = make_ctx(*ids)

        obtain_frag = queue_ops.generate(obtain, ctx)
        ctx.bind("enqueue.queue", obtain_frag.bindings["obtain.queue_out"])
        ctx.bind("enqueue.element", "payload")
        enqueue_frag = queue_ops.generate(enqueue, ctx)

        ctx.bind("release.queue", enqueue_frag.bindings["enqueue.queue_out"])
        ctx.bind("release.force", "True")
        release_frag = queue_ops.generate(release, ctx)

        statements = (
            obtain_frag.statements + enqueue_frag.statements + release_frag.statements
        )
        result = _compile_and_run(statements, {"payload": 42})
        assert result[release_frag.bindings["release.remaining"]] == [42]

    def test_queue_name_output_is_the_queue_name_attribute(self):
        release = _release_op("release")
        ctx = make_ctx(*(t.id for t in release.terminals))
        ctx.bind("release.queue", "q")
        frag = queue_ops.generate(release, ctx)
        # queue name (idx8) is bound as the queue's .name, not a runtime return
        assert frag.bindings["release.queue_name"] == "q.name"


def _status_op(node_id: str = "status") -> PrimitiveOperation:
    """Get Queue Status pane (resolved via the NI connector-pane image + wiring
    anchors + geometry): in idx0=queue; out idx4=max size, idx5=elements,
    idx6=name, idx7=# elements, idx8=queue out, idx9=# pending remove,
    idx10=# pending insert (error terminals omitted, as in the other builders)."""
    return PrimitiveOperation(
        id=node_id, name="Get Queue Status", kind="primitive",
        node_type="prim", primResID=9110,
        terminals=[
            _terminal(f"{node_id}.queue", 0, "input"),
            _terminal(f"{node_id}.max_size", 4, "output"),
            _terminal(f"{node_id}.elements", 5, "output"),
            _terminal(f"{node_id}.name", 6, "output"),
            _terminal(f"{node_id}.n_elements", 7, "output"),
            _terminal(f"{node_id}.queue_out", 8, "output"),
            _terminal(f"{node_id}.pending_remove", 9, "output"),
            _terminal(f"{node_id}.pending_insert", 10, "output"),
        ],
    )


class TestGetQueueStatus:
    """Get Queue Status (9110): each output binds to a field of the runtime's
    QueueStatus, per the connector-pane-image-resolved index map."""

    def test_status_reports_count_max_and_name(self):
        obtain = _obtain_op(with_name=True, with_max=True)
        enq1, enq2 = _enqueue_op("e1"), _enqueue_op("e2")
        status = _status_op("status")
        ids = [t.id for op in (obtain, enq1, enq2, status) for t in op.terminals]
        ctx = make_ctx(*ids)
        ctx.bind("obtain.name", "'q'")
        ctx.bind("obtain.max", "5")

        of = queue_ops.generate(obtain, ctx)
        qv = of.bindings["obtain.queue_out"]
        ctx.bind("e1.queue", qv)
        ctx.bind("e1.element", "'a'")
        e1f = queue_ops.generate(enq1, ctx)
        ctx.bind("e2.queue", e1f.bindings["e1.queue_out"])
        ctx.bind("e2.element", "'b'")
        e2f = queue_ops.generate(enq2, ctx)
        ctx.bind("status.queue", e2f.bindings["e2.queue_out"])
        sf = queue_ops.generate(status, ctx)

        statements = of.statements + e1f.statements + e2f.statements + sf.statements
        result = _compile_and_run(statements, {})

        # the outputs bind to <status_var>.<field>; recover the QueueStatus itself
        status_var = sf.bindings["status.n_elements"].rsplit(".", 1)[0]
        st = result[status_var]
        assert st.n_elements == 2
        assert st.max_size == 5
        assert st.name == "q"
        assert st.pending_remove == 0 and st.pending_insert == 0

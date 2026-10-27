"""Queue Operations generate plain ``queue.Queue`` code.

Each test builds a small VI (queue primitives, constants, controls, indicators),
generates its module, asserts the exact text of the queue statements, and runs
the module. A queue obtained by a constant name is defined once, in the
package's ``queues`` module, and shared by every VI that names it.
"""

# ruff: noqa: E501  (the pane tables list one primitive per line)
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors import CodeGenError
from lvkit.codegen.nodes import queue_ops
from lvkit.codegen.package_modules import PackageModules
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import Constant, PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.pipeline import create_import_resolver
from tests.helpers import build_graph

INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
TEXT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
FLAG = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
REF = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")

# kind -> (primResID, name, input roles by connector-pane index, output roles).
PANES = {
    "obtain": (9108, "Obtain Queue", {"name": 0, "max": 4}, {"queue_out": 8, "created": 10}),
    "enqueue": (9111, "Enqueue Element", {"queue": 0, "element": 1, "timeout": 2}, {"queue_out": 8, "timed_out": 10}),
    "opposite": (9129, "Enqueue Element At Opposite End", {"queue": 0, "element": 1, "timeout": 2}, {"queue_out": 8, "timed_out": 10}),
    "dequeue": (9113, "Dequeue Element", {"queue": 0, "timeout": 2}, {"queue_out": 8, "element": 9, "timed_out": 10}),
    "preview": (9112, "Preview Queue Element", {"queue": 0, "timeout": 2}, {"queue_out": 8, "element": 9, "timed_out": 10}),
    "flush": (9114, "Flush Queue", {"queue": 0}, {"queue_out": 8, "remaining": 9}),
    "release": (9109, "Release Queue", {"queue": 0, "force": 2}, {"name": 8, "remaining": 9}),
    "status": (
        9110,
        "Get Queue Status",
        {"queue": 0, "return": 1},
        {"max": 4, "elements": 5, "name": 6, "count": 7, "queue_out": 8,
         "pending_remove": 9, "pending_insert": 10},
    ),
}  # fmt: skip


def op(kind: str, node_id: str, vi_path: str) -> PrimitiveNode:
    """A queue primitive with every pane terminal of ``kind``, ids
    ``<node_id>:<role>``."""
    prim_id, name, inputs, outputs = PANES[kind]
    terminals = [
        Terminal(
            id=f"{node_id}:{role}",
            index=index,
            direction=direction,
            lv_type=_type_of(role),
        )
        for direction, roles in (("input", inputs), ("output", outputs))
        for role, index in roles.items()
    ]
    return PrimitiveNode(
        id=node_id,
        vi_path=vi_path,
        name=name,
        node_type="prim",
        prim_id=prim_id,
        terminals=terminals,
    )


def _type_of(role: str) -> LVType:
    if role in ("queue", "queue_out"):
        return REF
    if role in ("timed_out", "created", "force", "return"):
        return FLAG
    if role == "name":
        return TEXT
    return INT


def wire(src: str, dst: str) -> Wire:
    return Wire.from_terminals(from_terminal_id=src, to_terminal_id=dst)


class Vi:
    """A VI under construction: controls, indicators, constants, nodes, wires."""

    def __init__(self, name: str = "Queue Test.vi") -> None:
        self.name = name
        self.nodes: list[PrimitiveNode] = []
        self.inputs: list[FPTerminal] = []
        self.outputs: list[FPTerminal] = []
        self.constants: list[Constant] = []
        self.wires: list[Wire] = []

    def control(self, name: str, lv_type: LVType = INT) -> str:
        tid = f"in:{name}"
        self.inputs.append(self._fp(tid, len(self.inputs), name, "input", lv_type))
        return tid

    def indicator(self, name: str, source: str, lv_type: LVType = INT) -> None:
        tid = f"out:{name}"
        self.outputs.append(self._fp(tid, len(self.outputs), name, "output", lv_type))
        self.wires.append(wire(source, tid))

    @staticmethod
    def _fp(tid: str, index: int, name: str, direction: str, lv_type: LVType):
        return FPTerminal(
            id=tid,
            index=index,
            direction=direction,
            name=name,
            is_public=True,
            is_indicator=direction == "output",
            lv_type=lv_type,
        )

    def constant(self, value: object, lv_type: LVType, target: str) -> None:
        cid = f"const:{len(self.constants)}"
        self.constants.append(Constant(id=cid, value=value, lv_type=lv_type))
        self.wires.append(wire(cid, target))

    def add(self, kind: str, node_id: str) -> PrimitiveNode:
        node = op(kind, node_id, self.name)
        self.nodes.append(node)
        return node

    def register(self, graph: InMemoryVIGraph | None = None):
        """The VI's context and the graph holding it (``graph`` when given). Node
        and terminal ids are made unique to this VI, as they are in a real graph."""

        slug = "".join(c for c in self.name.lower() if c.isalnum())

        def own(identifier: str) -> str:
            return identifier if graph is None else f"{slug}_{identifier}"

        nodes = [
            node.model_copy(
                update={
                    "id": own(node.id),
                    "terminals": [
                        t.model_copy(update={"id": own(t.id)}) for t in node.terminals
                    ],
                }
            )
            for node in self.nodes
        ]
        context = VIContext(
            name=self.name,
            inputs=[t.model_copy(update={"id": own(t.id)}) for t in self.inputs],
            outputs=[t.model_copy(update={"id": own(t.id)}) for t in self.outputs],
            constants=[c.model_copy(update={"id": own(c.id)}) for c in self.constants],
            data_flow=[
                Wire.from_terminals(
                    from_terminal_id=own(w.from_terminal_id),
                    to_terminal_id=own(w.to_terminal_id),
                )
                for w in self.wires
            ],
        )
        return context, build_graph(context, self.name, nodes, graph)

    def source(self, resolver=None, graph: InMemoryVIGraph | None = None) -> str:
        context, graph = self.register(graph)
        return build_module(context, self.name, import_resolver=resolver, graph=graph)


def package_of(vis: list[Vi]):
    """One graph holding all of ``vis`` and the package's shared modules, queried
    from it."""
    graph = InMemoryVIGraph()
    for vi in vis:
        vi.register(graph)
    return graph, PackageModules.from_graph(graph, [vi.name for vi in vis])


def run(source: str, function: str):
    namespace: dict = {}
    exec(compile(source, "queue_test", "exec"), namespace)  # noqa: S102
    return namespace[function]


def test_prim_ids_are_the_dispatch_set() -> None:
    assert {int(p) for p in queue_ops.QUEUE_PRIM_IDS} == {
        pane[0] for pane in PANES.values()
    }


def local_roundtrip() -> Vi:
    vi = Vi("Local Queue.vi")
    item = vi.control("item")
    vi.add("obtain", "o")
    vi.add("enqueue", "e")
    vi.add("dequeue", "d1")
    vi.add("dequeue", "d2")
    vi.wires += [
        wire("o:queue_out", "e:queue"),
        wire(item, "e:element"),
        wire("e:queue_out", "d1:queue"),
        wire("d1:queue_out", "d2:queue"),
    ]
    vi.constant(-1, INT, "e:timeout")
    vi.constant(0, INT, "d1:timeout")
    vi.constant(0, INT, "d2:timeout")
    vi.indicator("first", "d1:element")
    vi.indicator("first_timed_out", "d1:timed_out", FLAG)
    vi.indicator("second", "d2:element")
    vi.indicator("second_timed_out", "d2:timed_out", FLAG)
    return vi


def test_unnamed_queue_is_a_local_queue_and_empty_get_times_out() -> None:
    source = local_roundtrip().source()
    assert "lvkit" not in source
    assert "from queue import Empty, Queue\n" in source
    assert "queue = Queue(maxsize=0)\n" in source
    assert "queue.put(item)\n" in source
    assert (
        "    try:\n"
        "        element = queue.get(timeout=0.0)\n"
        "        timed_out = False\n"
        "    except Empty:\n"
        "        element = 0\n"
        "        timed_out = True\n"
    ) in source
    result = run(source, "local_queue")(7)
    assert (result.first, result.first_timed_out) == (7, False)
    assert (result.second, result.second_timed_out) == (0, True)


def test_a_timeout_that_is_not_used_raises_empty() -> None:
    vi = Vi()
    vi.add("obtain", "o")
    vi.add("dequeue", "d")
    vi.wires.append(wire("o:queue_out", "d:queue"))
    vi.constant(0, INT, "d:timeout")
    vi.indicator("element", "d:element")
    source = vi.source()
    assert "element = queue.get(timeout=0.0)\n" in source
    with pytest.raises(queue_empty()):
        run(source, "queue_test")()


def queue_empty():
    import queue

    return queue.Empty


def test_a_wired_timeout_is_milliseconds_and_negative_waits_for_ever() -> None:
    vi = Vi()
    wait = vi.control("wait")
    item = vi.control("item")
    vi.add("obtain", "o")
    vi.add("enqueue", "e")
    vi.wires += [wire("o:queue_out", "e:queue"), wire(item, "e:element")]
    vi.wires.append(wire(wait, "e:timeout"))
    vi.indicator("full", "e:timed_out", FLAG)
    source = vi.source()
    assert "queue.put(item, timeout=None if wait < 0 else wait / 1000)" in source


def test_a_full_queue_times_out_on_enqueue() -> None:
    vi = Vi()
    item = vi.control("item")
    vi.add("obtain", "o")
    vi.add("enqueue", "e1")
    vi.add("enqueue", "e2")
    vi.constant(1, INT, "o:max")
    vi.wires += [
        wire("o:queue_out", "e1:queue"),
        wire(item, "e1:element"),
        wire("e1:queue_out", "e2:queue"),
        wire(item, "e2:element"),
    ]
    vi.constant(0, INT, "e1:timeout")
    vi.constant(0, INT, "e2:timeout")
    vi.indicator("first_full", "e1:timed_out", FLAG)
    vi.indicator("second_full", "e2:timed_out", FLAG)
    source = vi.source()
    assert "queue = Queue(maxsize=1)\n" in source
    assert "    except Full:\n" in source
    result = run(source, "queue_test")(3)
    assert (result.first_full, result.second_full) == (False, True)


def test_enqueue_at_opposite_end_goes_to_the_front() -> None:
    vi = Vi()
    a = vi.control("a")
    b = vi.control("b")
    vi.add("obtain", "o")
    vi.add("enqueue", "e")
    vi.add("opposite", "f")
    vi.add("dequeue", "d1")
    vi.add("dequeue", "d2")
    vi.wires += [
        wire("o:queue_out", "e:queue"),
        wire(a, "e:element"),
        wire("e:queue_out", "f:queue"),
        wire(b, "f:element"),
        wire("f:queue_out", "d1:queue"),
        wire("d1:queue_out", "d2:queue"),
    ]
    for target in ("e:timeout", "f:timeout", "d1:timeout", "d2:timeout"):
        vi.constant(-1, INT, target)
    vi.indicator("first", "d1:element")
    vi.indicator("second", "d2:element")
    source = vi.source()
    assert (
        "    with queue.mutex:\n"
        "        queue.queue.appendleft(b)\n"
        "        queue.unfinished_tasks += 1\n"
        "        queue.not_empty.notify()\n"
    ) in source
    result = run(source, "queue_test")(1, 2)
    assert (result.first, result.second) == (2, 1)


def test_flush_status_and_release() -> None:
    vi = Vi()
    a = vi.control("a")
    b = vi.control("b")
    vi.add("obtain", "o")
    vi.add("enqueue", "e1")
    vi.add("enqueue", "e2")
    vi.add("status", "s")
    vi.add("flush", "f")
    vi.add("release", "r")
    vi.wires += [
        wire("o:queue_out", "e1:queue"),
        wire(a, "e1:element"),
        wire("e1:queue_out", "e2:queue"),
        wire(b, "e2:element"),
        wire("e2:queue_out", "s:queue"),
        wire("s:queue_out", "f:queue"),
        wire("f:queue_out", "r:queue"),
    ]
    vi.constant(-1, INT, "e1:timeout")
    vi.constant(-1, INT, "e2:timeout")
    vi.indicator("count", "s:count")
    vi.indicator("capacity", "s:max")
    vi.indicator("flushed", "f:remaining", LVType(kind=LVTypeKind.ARRAY))
    vi.indicator("left", "r:remaining", LVType(kind=LVTypeKind.ARRAY))
    source = vi.source()
    assert "elements_in_queue = queue.qsize()\n" in source
    assert "max_queue_size = queue.maxsize or -1\n" in source
    result = run(source, "queue_test")(4, 5)
    assert result.count == 2
    assert result.capacity == -1
    assert result.flushed == [4, 5]
    assert result.left == []


def test_preview_leaves_the_element_and_times_out_when_empty() -> None:
    vi = Vi()
    a = vi.control("a")
    vi.add("obtain", "o")
    vi.add("preview", "p0")
    vi.add("enqueue", "e")
    vi.add("preview", "p1")
    vi.add("dequeue", "d")
    vi.wires += [
        wire("o:queue_out", "p0:queue"),
        wire("p0:queue_out", "e:queue"),
        wire(a, "e:element"),
        wire("e:queue_out", "p1:queue"),
        wire("p1:queue_out", "d:queue"),
    ]
    for target in ("p0:timeout", "p1:timeout", "d:timeout"):
        vi.constant(0, INT, target)
    vi.constant(-1, INT, "e:timeout")
    vi.indicator("early_timed_out", "p0:timed_out", FLAG)
    vi.indicator("peeked", "p1:element")
    vi.indicator("taken", "d:element")
    result = run(vi.source(), "queue_test")(9)
    assert result.early_timed_out is True
    assert (result.peeked, result.taken) == (9, 9)


def test_preview_waits_for_an_element_until_its_timeout() -> None:
    vi = Vi()
    vi.add("obtain", "o")
    vi.add("preview", "p")
    vi.wires.append(wire("o:queue_out", "p:queue"))
    vi.constant(50, INT, "p:timeout")
    vi.indicator("peeked", "p:element")
    vi.indicator("timed_out", "p:timed_out", FLAG)
    source = vi.source()
    assert "queue.not_empty.wait_for(lambda: queue.queue, 0.05)" in source
    result = run(source, "queue_test")()
    assert (result.peeked, result.timed_out) == (0, True)


def test_queue_name_and_waiting_counts_have_no_equivalent() -> None:
    for role in ("name", "pending_remove", "pending_insert"):
        vi = Vi()
        vi.add("obtain", "o")
        vi.add("status", "s")
        vi.wires.append(wire("o:queue_out", "s:queue"))
        vi.indicator("value", f"s:{role}", TEXT if role == "name" else INT)
        with pytest.raises(CodeGenError, match="Get Queue Status uses its"):
            vi.source()


def package(tmp_path: Path, vis: dict[str, Vi], reverse: bool = False) -> Path:
    """Generate ``vis`` ({module: VI}) into a package sharing one ``queues``
    module, and return the package directory. ``reverse`` gives the VIs to the
    package in the opposite order."""
    root = tmp_path / "pkg"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    items = list(vis.items())
    if reverse:
        items.reverse()
    graph, modules = package_of([vi for _, vi in items])
    paths = {vi.name: root / f"{module}.py" for module, vi in items}
    for module, vi in items:
        resolver = create_import_resolver(
            "pkg", root, paths, root / f"{module}.py", package_modules=modules
        )
        (root / f"{module}.py").write_text(vi.source(resolver, graph))
    assert [p.name for p in modules.write(root)] == ["queues.py"]
    return root


def named_producer_and_consumer() -> dict[str, Vi]:
    producer = Vi("Producer.vi")
    item = producer.control("item")
    producer.add("obtain", "o")
    producer.add("enqueue", "e")
    producer.wires += [wire("o:queue_out", "e:queue"), wire(item, "e:element")]
    producer.constant("Work Items", TEXT, "o:name")
    producer.constant(-1, INT, "e:timeout")
    consumer = Vi("Consumer.vi")
    consumer.add("obtain", "o")
    consumer.add("dequeue", "d")
    consumer.wires.append(wire("o:queue_out", "d:queue"))
    consumer.constant("Work Items", TEXT, "o:name")
    consumer.constant(0, INT, "d:timeout")
    consumer.constant(False, FLAG, "o:max")  # unused pane slot, see below
    consumer.constants.pop()
    consumer.wires.pop()
    consumer.indicator("element", "d:element")
    consumer.indicator("timed_out", "d:timed_out", FLAG)
    return {"producer": producer, "consumer": consumer}


@pytest.fixture
def imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    yield lambda name: importlib.import_module(name)
    for name in [m for m in sys.modules if m == "pkg" or m.startswith("pkg.")]:
        del sys.modules[name]


def test_a_named_queue_is_one_queue_in_the_queues_module(
    tmp_path: Path, imported
) -> None:
    root = package(tmp_path, named_producer_and_consumer())
    assert (root / "queues.py").read_text() == (
        '"""Named queues shared by the package. Releasing a queue shuts it down '
        '(queue.ShutDown), which needs Python 3.13 or later."""\n'
        "from __future__ import annotations\n"
        "from queue import Queue\n"
        "work_items: Queue = Queue(maxsize=0)"
    )
    producer_source = (root / "producer.py").read_text()
    assert "from . import queues as shared_queues\n" in producer_source
    assert "queue = shared_queues.work_items\n" in producer_source
    assert "lvkit" not in producer_source + (root / "consumer.py").read_text()
    produce = imported("pkg.producer").producer
    consume = imported("pkg.consumer").consumer
    produce(11)
    taken = consume()
    assert (taken.element, taken.timed_out) == (11, False)
    empty = consume()
    assert (empty.element, empty.timed_out) == (0, True)
    assert imported("pkg.queues").work_items.qsize() == 0


def obtain_named(name: str, module_name: str, size: int | None = None) -> Vi:
    vi = Vi(module_name)
    vi.add("obtain", "o")
    vi.constant(name, TEXT, "o:name")
    if size is not None:
        vi.constant(size, INT, "o:max")
    vi.indicator("queue", "o:queue_out", REF)
    return vi


def test_names_that_make_one_attribute_get_distinct_attributes_in_sorted_order(
    tmp_path: Path,
) -> None:
    first = obtain_named("work-items", "A.vi")
    second = obtain_named("Work Items", "B.vi")
    _, modules = package_of([first, second])
    assert modules.queues.attributes == {
        "Work Items": "work_items",
        "work-items": "work_items_2",
    }
    _, reversed_modules = package_of([second, first])
    assert reversed_modules.queues.attributes == modules.queues.attributes


def test_a_named_queue_with_two_sizes_stops_generation_of_its_nodes() -> None:
    a = obtain_named("Work Items", "A.vi", 0)
    b = obtain_named("Work Items", "B.vi", 5)
    graph, modules = package_of([a, b])
    resolver = create_import_resolver(
        "pkg", Path("/x"), {}, Path("/x/a.py"), package_modules=modules
    )
    with pytest.raises(CodeGenError, match="Work Items"):
        a.source(resolver, graph)


def test_the_shared_modules_do_not_depend_on_the_order_of_the_vis(
    tmp_path: Path,
) -> None:
    def written(order_reversed: bool, folder: Path) -> dict[str, str]:
        vis = {
            "producer": computed_name_vi("Producer.vi", "enqueue"),
            "consumer": computed_name_vi("Consumer.vi", "dequeue"),
            "z_queue": obtain_named("Zeta", "Z.vi"),
            "a_queue": obtain_named("Alpha", "A.vi", 3),
            "created": created_vi("Work Items", "Created.vi"),
        }
        root = package(folder, vis, reverse=order_reversed)
        return {f.name: f.read_text() for f in root.glob("queues.py")}

    forward = written(False, tmp_path / "forward")
    backward = written(True, tmp_path / "backward")
    assert forward == backward
    assert forward["queues.py"].index("alpha") < forward["queues.py"].index("zeta")


def computed_name_vi(name: str, role: str) -> Vi:
    """A VI whose queue name is its control ``label``: ``enqueue`` puts ``item``,
    ``dequeue`` returns what it gets."""
    vi = Vi(name)
    label = vi.control("label", TEXT)
    vi.add("obtain", "o")
    vi.wires.append(wire(label, "o:name"))
    if role == "enqueue":
        item = vi.control("item")
        vi.add("enqueue", "e")
        vi.wires += [wire("o:queue_out", "e:queue"), wire(item, "e:element")]
        vi.constant(-1, INT, "e:timeout")
    else:
        vi.add("dequeue", "d")
        vi.wires.append(wire("o:queue_out", "d:queue"))
        vi.constant(0, INT, "d:timeout")
        vi.indicator("element", "d:element")
        vi.indicator("timed_out", "d:timed_out", FLAG)
    return vi


def test_a_name_computed_at_run_time_is_an_entry_of_by_name(
    tmp_path: Path, imported
) -> None:
    root = package(
        tmp_path,
        {
            "producer": computed_name_vi("Producer.vi", "enqueue"),
            "consumer": computed_name_vi("Consumer.vi", "dequeue"),
        },
    )
    assert (
        (root / "queues.py")
        .read_text()
        .endswith("from queue import Queue\nby_name: dict[str, Queue] = {}")
    )
    producer_source = (root / "producer.py").read_text()
    assert (
        "queue = shared_queues.by_name.setdefault(label, Queue(maxsize=0))\n"
        in producer_source
    )
    assert "lvkit" not in producer_source
    produce = imported("pkg.producer").producer
    consume = imported("pkg.consumer").consumer
    produce("jobs", 4)
    produce("other", 8)
    got = consume("jobs")
    assert (got.element, got.timed_out) == (4, False)
    assert consume("jobs").timed_out is True
    assert consume("other").element == 8


def test_a_named_queue_needs_a_package() -> None:
    vi = Vi()
    vi.add("obtain", "o")
    vi.constant("Work Items", TEXT, "o:name")
    vi.indicator("queue", "o:queue_out", REF)
    with pytest.raises(CodeGenError, match="no generated package"):
        vi.source()


def created_vi(constant_name: str | None, name: str = "Creator.vi") -> Vi:
    """A VI that obtains a queue (by the constant name, else by its control
    ``label``) and returns the ``created?`` output."""
    vi = Vi(name)
    vi.add("obtain", "o")
    if constant_name is None:
        vi.wires.append(wire(vi.control("label", TEXT), "o:name"))
    else:
        vi.constant(constant_name, TEXT, "o:name")
    vi.indicator("created", "o:created", FLAG)
    return vi


def test_created_is_true_only_the_first_time_a_name_is_obtained(
    tmp_path: Path, imported
) -> None:
    root = package(
        tmp_path,
        {
            "by_constant": created_vi("Work Items", "ByConstant.vi"),
            "by_label": created_vi(None, "ByLabel.vi"),
        },
    )
    source = (root / "by_constant.py").read_text()
    assert "created = 'Work Items' not in shared_queues.obtained\n" in source
    assert "shared_queues.obtained.add('Work Items')" in source
    assert (
        "created = label not in shared_queues.by_name\n"
        in (root / "by_label.py").read_text()
    )
    constant = imported("pkg.by_constant").byconstant
    computed = imported("pkg.by_label").bylabel
    assert [constant().created, constant().created] == [True, False]
    assert [computed("a").created, computed("a").created, computed("b").created] == [
        True,
        False,
        True,
    ]


needs_shutdown = pytest.mark.skipif(
    sys.version_info < (3, 13), reason="queue.shutdown needs Python 3.13"
)


def release_vi(force: bool | None) -> Vi:
    vi = Vi("Closer.vi")
    vi.add("obtain", "o")
    vi.add("release", "r")
    vi.wires.append(wire("o:queue_out", "r:queue"))
    vi.constant("Work Items", TEXT, "o:name")
    if force is not None:
        vi.constant("True" if force else "False", FLAG, "r:force")
    return vi


def test_release_shuts_the_queue_down_and_force_destroy_does_it_immediately() -> None:

    def generated(force: bool | None) -> str:
        vi = release_vi(force)
        graph, modules = package_of([vi])
        resolver = create_import_resolver(
            "pkg", Path("/x"), {}, Path("/x/c.py"), package_modules=modules
        )
        return vi.source(resolver, graph)

    assert generated(None).endswith("queue.shutdown()")
    assert generated(False).endswith("queue.shutdown()")
    assert generated(True).endswith("queue.shutdown(immediate=True)")


@needs_shutdown
def test_releasing_the_queue_ends_a_consumer_waiting_on_it(
    tmp_path: Path, imported
) -> None:
    import queue
    import threading

    consumer = Vi("Consumer.vi")
    consumer.add("obtain", "o")
    consumer.add("dequeue", "d")
    consumer.wires.append(wire("o:queue_out", "d:queue"))
    consumer.constant("Work Items", TEXT, "o:name")
    consumer.constant(-1, INT, "d:timeout")
    consumer.indicator("element", "d:element")
    root = package(
        tmp_path,
        {"consumer": consumer, "closer": release_vi(True)},
    )
    assert "shutdown(immediate=True)" in (root / "closer.py").read_text()
    consume = imported("pkg.consumer").consumer
    close = imported("pkg.closer").closer
    outcome: list[BaseException] = []

    def wait() -> None:
        try:
            consume()
        except BaseException as exc:  # noqa: BLE001
            outcome.append(exc)

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    thread.join(timeout=0.3)
    assert thread.is_alive()
    close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert [type(e) for e in outcome] == [queue.ShutDown]

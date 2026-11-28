"""The functions written from the instances saved in callers' files
(``instance_functions.py``): copies with one body share one function, the
functions of a ``.vim`` number in the order of their first caller's path, and a
shared function that holds state and runs from several calls is a problem of the
run. Tiny inputs: a real Feedback Node diagram for what holds state, and a graph
that answers only what ``InstanceFunctions`` asks."""

from __future__ import annotations

from lvkit.codegen import build_module
from lvkit.codegen.class_builder.shared_state import ModuleParts
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VINode
from lvkit.instance_functions import (
    InstanceFunctions,
    InstanceHome,
    normalised_body,
    state_problem,
)
from lvkit.load_mode import LoadMode
from lvkit.run_report import ProblemKind
from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.test_feedback_node import ADD, NAME, _array


class _Graph:
    """What ``InstanceFunctions`` asks of the graph: the VI that holds each copy
    and the calls of each holder."""

    def __init__(self, holders: dict[str, str], calls: dict[str, list[VINode]]):
        self._holders = holders
        self._calls = calls

    def instance_holder(self, key: str) -> str | None:
        return self._holders.get(key)

    def holding_file(self, key: str) -> str | None:
        return self._holders.get(key)

    def iter_nodes(self, key: str) -> list[VINode]:
        return self._calls.get(key, [])

    def key_label(self, key: str) -> str:
        return key.rsplit("/", 1)[-1]


def _call(holder: str, copy: str, name: str) -> VINode:
    return VINode(
        id=f"{holder}::{name}",
        vi_path=holder,
        name=name,
        callee_key=copy,
        runs_saved_instance=True,
    )


def _functions(
    bodies: dict[str, tuple[str, tuple[str, ...]]],
) -> tuple[InstanceFunctions, InstanceHome]:
    """Copies ``b/guid.vi``, ``a/guid.vi``, ... held by VIs of the same names,
    each calling ``Add.vim`` and trial-built to ``bodies``."""
    copies = tuple(sorted(f"{holder}/{holder}.vi" for holder in bodies))
    home = InstanceHome("Add.vim", copies, "add", None)
    graph = _Graph(
        {copy: copy.split("/")[0] for copy in copies},
        {
            copy.split("/")[0]: [_call(copy.split("/")[0], copy, f"Add {i}")]
            for i, copy in enumerate(copies)
        },
    )

    def trial(copy: str, function_name: str) -> tuple[str, tuple[str, ...]]:
        return bodies[copy.split("/")[0]]

    functions = InstanceFunctions(
        graph,  # type: ignore[arg-type]
        [home],
        lambda holder: holder,
        trial,
    )
    return functions, home


def test_copies_with_one_body_share_one_function() -> None:
    functions, home = _functions(
        {"a": ("same", ()), "b": ("same", ()), "c": ("other", ())}
    )

    assert [(f.name, f.copies) for f in functions.functions(home)] == [
        ("add", ("a/a.vi", "b/b.vi")),
        ("add_2", ("c/c.vi",)),
    ]
    assert functions.function_name("b/b.vi") == "add"
    assert functions.function_name("c/c.vi") == "add_2"
    assert functions.function_name("not a copy") is None


def test_functions_number_in_the_order_of_their_first_callers_path() -> None:
    # "z" is first by key, but the holder paths sort "a" first.
    functions, home = _functions({"z": ("z body", ()), "a": ("a body", ())})

    assert [(f.name, f.key) for f in functions.functions(home)] == [
        ("add", "a/a.vi"),
        ("add_2", "z/z.vi"),
    ]


def test_a_shared_function_with_state_called_from_two_places_is_a_problem() -> None:
    functions, home = _functions(
        {
            "a": ("same", ("a Feedback Node",)),
            "b": ("same", ("a Feedback Node",)),
        }
    )
    [function] = functions.functions(home)
    sites = functions.call_sites(function)

    assert function.state == ("a Feedback Node",)
    assert sites == ["a: Add 0", "b: Add 1"]
    problem = state_problem(
        functions._graph,  # type: ignore[arg-type]  # noqa: SLF001
        "Add.vim",
        "Add.vim",
        function,
        sites,
    )
    assert problem.kind is ProblemKind.GAP
    assert problem.reason == (
        "Add.vim holds state (a Feedback Node) and is called from 2 places; "
        "LabVIEW gives each call site its own copy of that state (a malleable "
        "VI runs as a preallocated clone), but the generated function add "
        "shares one, so the callers share the value. "
        "Calls: a: Add 0; b: Add 1"
    )


def test_a_function_without_state_has_none_to_warn_of() -> None:
    functions, home = _functions({"a": ("same", ()), "b": ("same", ())})

    assert [f.state for f in functions.functions(home)] == [()]


def test_a_body_is_the_function_without_its_window_and_panel_lines() -> None:
    code = (
        "def add(x):\n"
        "    return 'copy 8c9053c6-7367-45ea-a096-853d532a0fb8'\n"
        "add.window_title = 'a'\n"
        "add.panel = Panel(add)\n"
        "other.x = 1\n"
    )

    assert normalised_body(code, "add", ["(x)"]) == (
        "def add<copy>:\n    return 'copy <copy>'\nother.x = 1"
    )


def test_a_feedback_node_is_what_holds_state_in_a_built_function(
    project: Project,  # noqa: F811
) -> None:
    """The holder names a real diagram's state in LabVIEW's words, and an
    initializer on the loop border (a shift register in effect) holds none."""
    vi = Vi(NAME)
    values_type = vi.add_type(_array(I32))
    stops_type = vi.add_type(_array(BOOL))
    values = vi.control("values", values_type, REQUIRED)
    stops = vi.control("stops", stops_type, REQUIRED)
    total = vi.indicator("total", I32)
    loop = vi.loop(
        "whileLoop",
        tunnels=[(I32, True)],
        indexing_inputs=[(values_type, I32), (stops_type, BOOL)],
    )
    out, value_in, stop_in = loop.tunnels
    with vi.inside(loop.scopes[0]):
        fb = vi.feedback_node(I32, initializer=False, link=vi.next_uid())
        add = vi.primitive(
            ADD,
            [("sum", 0, I32, True), ("y", 1, I32, False), ("x", 2, I32, False)],
        )
    vi.wire(values, value_in.outer)
    vi.wire(stops, stop_in.outer)
    vi.wire(stop_in.faces[0], loop.faces[0])
    vi.wire(fb["read"], add["x"])
    vi.wire(value_in.faces[0], add["y"])
    vi.wire(add["sum"], fb["write"], out.faces[0])
    vi.wire(out.outer, total)
    project.add(vi)
    graph = InMemoryVIGraph()
    key = graph.load_vi(project.root / NAME, LoadMode.MINIMAL)
    assert key is not None

    parts = ModuleParts(module_stem="fb")
    build_module(graph.get_vi_context(key), key, graph=graph, shared=parts)

    assert sorted(set(parts.state_holders.values())) == ["a Feedback Node"]
    assert set(parts.state_holders) == set(parts.globals)

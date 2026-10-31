"""A LabVIEW class generates as one Python class whose methods are its VIs.

Each case is a handmade project of classes (``tests/class_graphs.py``) generated
into a package that is imported and run: the dispatch, the by-value copies, the
attributes and properties, and the gaps are checked on what the generated code
does, and every VI is described in every text view the graph has. A private data
field is ``self.__<field>``, which Python stores as ``_<Class>__<field>``."""

from __future__ import annotations

import copy
import queue
from pathlib import Path

import pytest

from lvkit.class_property import PropertyRole
from lvkit.codegen.class_attributes import ClassAttributes
from lvkit.codegen.class_model import MethodKind, class_model, member_shape
from lvkit.codegen.gaps import Gap
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import (
    CaseFrame,
    CaseStructureNode,
    GraphNode,
    LoopNode,
    VIContext,
    VINode,
)
from lvkit.graph.netlist import render_lvnet
from lvkit.graph.netlist_build import build_netlist_from_graph
from lvkit.graph.op_walk import stamp_nmux_lane_names
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal, TunnelTerminal
from tests.class_graphs import (
    INT,
    Project,
    Recipe,
    add,
    call,
    class_type,
    mux,
    pane,
    wire,
)

BASE, CHILD = class_type("Base"), class_type("Child")
COUNT = ClusterField(name="Count", type=INT)
EXTRA = ClusterField(name="Extra", type=INT)
OFFSET = ClusterField(name="Offset", type=INT)


# --- recipes ---------------------------------------------------------------------


def bump(owner: LVType, field_index: int, by_pane: bool) -> Recipe:
    """A method that adds its ``by`` input to a field of its class (``add_n``)."""
    assert by_pane

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "in", owner, "Self", 0),
                    pane(vi, "input", "n", INT, "n", 2),
                ],
                outputs=[pane(vi, "output", "out", owner, "Self out", 1)],
                data_flow=[
                    wire(vi, "in", "get.agg"),
                    wire(vi, "in", "set.agg"),
                    wire(vi, "get.field", "sum.x"),
                    wire(vi, "n", "sum.y"),
                    wire(vi, "sum.sum", "set.field"),
                    wire(vi, "set.agg_out", "out"),
                ],
            ),
            [
                mux(vi, "get", False, owner, INT, field_index),
                add(vi, "sum"),
                mux(vi, "set", True, owner, INT, field_index),
            ],
        )

    return build


def calls_with(
    owner: LVType, callee: str, node_type: str, then_bump: bool = False
) -> Recipe:
    """A method that calls ``callee`` with its ``by`` input (as Call Parent when
    ``node_type`` says), then, with ``then_bump``, adds ``by`` to the first field
    of its own class."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        callee_type = BASE if node_type == "callParentDynIUse" else owner
        nodes: list[GraphNode] = [
            call(
                vi,
                "c",
                callee,
                node_type,
                [("self", 0, callee_type), ("by", 2, INT)],
                [("self out", 1, callee_type)],
            )
        ]
        wires = [wire(vi, "in", "c.self"), wire(vi, "n", "c.by")]
        if then_bump:
            nodes += [
                mux(vi, "get", False, owner, INT, 0),
                add(vi, "sum"),
                mux(vi, "set", True, owner, INT, 0),
            ]
            wires += [
                wire(vi, "c.self out", "get.agg"),
                wire(vi, "c.self out", "set.agg"),
                wire(vi, "get.field", "sum.x"),
                wire(vi, "n", "sum.y"),
                wire(vi, "sum.sum", "set.field"),
                wire(vi, "set.agg_out", "out"),
            ]
        else:
            wires.append(wire(vi, "c.self out", "out"))
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "in", owner, "Self", 0),
                    pane(vi, "input", "n", INT, "n", 2),
                ],
                outputs=[pane(vi, "output", "out", owner, "Self out", 1)],
                data_flow=wires,
            ),
            nodes,
        )

    return build


def info(owner: LVType) -> Recipe:
    """Two results: the Count and twice the Count."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 0)],
                outputs=[
                    pane(vi, "output", "out", owner, "Self out", 1),
                    pane(vi, "output", "a", INT, "a", 2),
                    pane(vi, "output", "b", INT, "b", 3),
                ],
                data_flow=[
                    wire(vi, "in", "get.agg"),
                    wire(vi, "in", "out"),
                    wire(vi, "get.field", "a"),
                    wire(vi, "get.field", "sum.x"),
                    wire(vi, "get.field", "sum.y"),
                    wire(vi, "sum.sum", "b"),
                ],
            ),
            [mux(vi, "get", False, owner, INT, 0), add(vi, "sum")],
        )

    return build


def info_swapped(owner: LVType, callee: str) -> Recipe:
    """The results of the parent's, the other way round."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 0)],
                outputs=[
                    pane(vi, "output", "out", owner, "Self out", 1),
                    pane(vi, "output", "a", INT, "a", 2),
                    pane(vi, "output", "b", INT, "b", 3),
                ],
                data_flow=[
                    wire(vi, "in", "c.self"),
                    wire(vi, "c.self out", "out"),
                    wire(vi, "c.a", "b"),
                    wire(vi, "c.b", "a"),
                ],
            ),
            [
                call(
                    vi,
                    "c",
                    callee,
                    "callParentDynIUse",
                    [("self", 0, BASE)],
                    [("self out", 1, BASE), ("a", 2, INT), ("b", 3, INT)],
                )
            ],
        )

    return build


def reader(owner: LVType, field_index: int = 0) -> Recipe:
    """A getter that only moves one field."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 0)],
                outputs=[
                    pane(vi, "output", "out", owner, "Self out", 1),
                    pane(vi, "output", "value", INT, "value", 2),
                ],
                data_flow=[
                    wire(vi, "in", "get.agg"),
                    wire(vi, "in", "out"),
                    wire(vi, "get.field", "value"),
                ],
            ),
            [mux(vi, "get", False, owner, INT, field_index)],
        )

    return build


def writer(owner: LVType, field_index: int = 0, hands_back: bool = True) -> Recipe:
    """A setter that only moves one field; with ``hands_back`` False its class
    output is another object it is given (``other``), not the changed one."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        inputs = [
            pane(vi, "input", "in", owner, "Self", 0),
            pane(vi, "input", "value", INT, "value", 2),
        ]
        if not hands_back:
            inputs.append(pane(vi, "input", "other", owner, "Other", 3))
        return (
            VIContext(
                name=vi,
                inputs=inputs,
                outputs=[pane(vi, "output", "out", owner, "Self out", 1)],
                data_flow=[
                    wire(vi, "in", "set.agg"),
                    wire(vi, "value", "set.field"),
                    wire(vi, "set.agg_out" if hands_back else "other", "out"),
                ],
            ),
            [mux(vi, "set", True, owner, INT, field_index)],
        )

    return build


def parent_reader(callee: str) -> Recipe:
    """A getter of the child: the parent's value plus the child's Offset."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", CHILD, "Self", 0)],
                outputs=[
                    pane(vi, "output", "out", CHILD, "Self out", 1),
                    pane(vi, "output", "value", INT, "value", 2),
                ],
                data_flow=[
                    wire(vi, "in", "c.self"),
                    wire(vi, "c.self out", "get.agg"),
                    wire(vi, "c.self out", "out"),
                    wire(vi, "c.value", "sum.x"),
                    wire(vi, "get.field", "sum.y"),
                    wire(vi, "sum.sum", "value"),
                ],
            ),
            [
                call(
                    vi,
                    "c",
                    callee,
                    "callParentDynIUse",
                    [("self", 0, BASE)],
                    [("self out", 1, BASE), ("value", 2, INT)],
                ),
                mux(vi, "get", False, CHILD, INT, 0),
                add(vi, "sum"),
            ],
        )

    return build


def parent_writer(callee: str) -> Recipe:
    """A setter of the child that passes the value to the parent's."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "in", CHILD, "Self", 0),
                    pane(vi, "input", "value", INT, "value", 2),
                ],
                outputs=[pane(vi, "output", "out", CHILD, "Self out", 1)],
                data_flow=[
                    wire(vi, "in", "c.self"),
                    wire(vi, "value", "c.value"),
                    wire(vi, "c.self out", "out"),
                ],
            ),
            [
                call(
                    vi,
                    "c",
                    callee,
                    "callParentDynIUse",
                    [("self", 0, BASE), ("value", 2, INT)],
                    [("self out", 1, BASE)],
                )
            ],
        )

    return build


def static_make(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A static VI that takes no class wire: its ``seed``, as its ``value``."""
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "seed", INT, "seed", 0)],
            outputs=[pane(vi, "output", "value", INT, "value", 1)],
            data_flow=[wire(vi, "seed", "value")],
        ),
        [],
    )


def passthrough(owner: LVType) -> Recipe:
    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 0)],
                outputs=[pane(vi, "output", "out", owner, "Self out", 1)],
                data_flow=[wire(vi, "in", "out")],
            ),
            [],
        )

    return build


def attributes_of(project: Project, cls: str = "Base") -> ClassAttributes:
    return class_model(project.graph, project.classes[cls]).attributes


# --- every view ------------------------------------------------------------------


def see_every_view(project: Project) -> dict[str, str]:
    """Describe each VI of the project as text, as lvnet and as JSON; the text of
    each view by VI."""
    views: dict[str, str] = {}
    stamp_nmux_lane_names(project.graph)
    for name, key in project.vis.items():
        netlist = build_netlist_from_graph(project.graph, key)
        views[name] = "\n".join(
            [
                describe_vi(project.graph, key),
                render_lvnet(netlist),
                netlist.model_dump_json()
                if hasattr(netlist, "model_dump_json")
                else "",
            ]
        )
    return views


# --- 1, 2, 7: a parent and a child that overrides with Call Parent ---------------


def inheritance_project(tmp_path: Path) -> Project:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [EXTRA], parent="Base")
    add_key = project.key_of("Base", "Add")
    inc_key = project.key_of("Base", "Increment")
    project.add_method("Base", "Add", bump(BASE, 0, True), "private", static=True)
    project.add_method("Base", "Increment", calls_with(BASE, add_key, "iUse"), "public")
    project.add_method(
        "Child",
        "Increment",
        calls_with(CHILD, inc_key, "callParentDynIUse", then_bump=True),
        "public",
    )
    return project


def test_a_child_class_inherits_and_overrides_with_call_parent(
    tmp_path: Path,
) -> None:
    project = inheritance_project(tmp_path)
    package = project.generate()
    assert package.gaps == []
    base_source, child_source = package.sources["base"], package.sources["child"]
    assert "lvkit" not in base_source + child_source
    assert "class Base:" in base_source and "class Child(Base):" in child_source
    assert "def __add(self, n" in base_source
    assert "self.__add(n=n)" in base_source
    assert "super().increment(n=n)" in child_source
    # The members have no module of their own.
    assert sorted(path.stem for path in package.root.glob("*.py")) == [
        "__init__",
        "base",
        "child",
    ]
    with package.use() as module:
        base, child = module("base").Base, module("child").Child
        assert [c.__name__ for c in child.__mro__] == ["Child", "Base", "object"]
        obj = child()
        # The child's own field is its first field: a Bundle By Name on its wire
        # reads and writes `Extra`, never the parent's `Count`.
        assert sorted(vars(obj)) == ["_Base__count", "_Child__extra"]
        obj.increment(n=2)
        assert (obj._Base__count, obj._Child__extra) == (2, 2)
        parent = base()
        parent.increment(n=5)
        assert parent._Base__count == 5
        # The private helper is the parent's own: it runs on a child object too.
        obj._Base__add(n=1)
        assert obj._Base__count == 3


def test_the_class_methods_have_one_shape_each(tmp_path: Path) -> None:
    project = inheritance_project(tmp_path)
    graph = project.graph
    add_shape = member_shape(graph, project.vis["Base.Add"])
    inc = member_shape(graph, project.vis["Base.Increment"])
    child_inc = member_shape(graph, project.vis["Child.Increment"])
    assert add_shape is not None and inc is not None and child_inc is not None
    assert (add_shape.kind, add_shape.name) == (MethodKind.INSTANCE, "__add")
    assert (inc.kind, inc.name) == (MethodKind.DYNAMIC, "increment")
    assert add_shape.results == () and inc.returns_receiver is False
    assert child_inc.root_key == inc.key
    assert child_inc.parameters == inc.parameters == {2: "n"}


def test_every_view_names_the_childs_own_field(tmp_path: Path) -> None:
    project = inheritance_project(tmp_path)
    views = see_every_view(project)
    # `Child.Increment` bundles/unbundles field 0 of the child's own data.
    assert views["Child.Increment"].count("Extra") >= 2
    assert "Count" not in views["Child.Increment"]
    assert "Count" in views["Base.Add"] and "Extra" not in views["Base.Add"]
    own = project.graph.class_own_fields(project.classes["Child"])
    assert [f.name for f in own] == ["Extra"]


# --- 3: a dispatch with two results ----------------------------------------------


def test_a_dispatch_with_two_results_reads_one_shape_from_either_class(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [EXTRA], parent="Base")
    info_key = project.key_of("Base", "Info")
    project.add_method("Base", "Info", info(BASE))
    project.add_method("Child", "Info", info_swapped(CHILD, info_key))

    def probe(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "obj", BASE, "obj", 0)],
                outputs=[
                    pane(vi, "output", "a", INT, "a", 1),
                    pane(vi, "output", "b", INT, "b", 2),
                ],
                data_flow=[
                    wire(vi, "obj", "c.self"),
                    wire(vi, "c.a", "a"),
                    wire(vi, "c.b", "b"),
                ],
            ),
            [
                call(
                    vi,
                    "c",
                    info_key,
                    "dynIUse",
                    [("self", 0, BASE)],
                    [("self out", 1, BASE), ("a", 2, INT), ("b", 3, INT)],
                )
            ],
        )

    project.add_vi("Probe", probe)
    package = project.generate()
    assert package.gaps == []
    assert "class InfoResult(NamedTuple)" in package.sources["base"]
    assert "from .base import InfoResult" in package.sources["child"]
    assert "obj.info()" in package.sources["probe"]
    with package.use() as module:
        base, child = module("base").Base(), module("child").Child()
        base._Base__count = child._Base__count = 3
        assert tuple(module("probe").probe(obj=base)) == (3, 6)
        assert tuple(module("probe").probe(obj=child)) == (6, 3)
        assert isinstance(child.info(), module("base").InfoResult)


# --- 4: a property overridden by a child -----------------------------------------


def accessor_project(tmp_path: Path) -> Project:
    """A parent and a child that each have a field ``Count``; the child's property
    ``Count`` overrides the parent's: its getter adds its own Count to the
    parent's (Call Parent), its setter passes the value to the parent's."""
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [COUNT], parent="Base")
    read_key = project.key_of("Base", "Read Count")
    write_key = project.key_of("Base", "Write Count")
    project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    project.add_method("Base", "Write Count", writer(BASE), property_name="Count")
    project.add_method(
        "Child", "Read Count", parent_reader(read_key), property_name="Count"
    )
    project.add_method(
        "Child", "Write Count", parent_writer(write_key), property_name="Count"
    )
    return project


def test_a_child_property_of_the_same_long_name_overrides_the_parents(
    tmp_path: Path,
) -> None:
    project = accessor_project(tmp_path)
    parent = attributes_of(project)
    assert parent.fields == ["__count"]
    assert {a.python_name for a in parent.accessors.values()} == {"count"}
    package = project.generate()
    assert package.gaps == []
    base_source, child_source = package.sources["base"], package.sources["child"]
    assert "@property\n    def count(self)" in base_source
    assert "@count.setter\n    def count(self, value" in base_source
    assert "return self.__count" in base_source
    # The child's property starts from the parent's and reads its own field.
    assert "@Base.count.getter\n    def count(self)" in child_source
    assert "@count.setter\n    def count(self, value" in child_source
    assert "super().count" in child_source
    assert "Base.count.fset(self, value)" in child_source
    assert "self.__count" in child_source
    with package.use() as module:
        child, base = module("child").Child(), module("base").Base()
        child.count = 5
        child._Child__count = 3
        assert sorted(vars(child)) == ["_Base__count", "_Child__count"]
        assert child._Base__count == 5 and child.count == 8
        base.count = 2
        assert base.count == 2 and base._Base__count == 2


def test_a_child_that_overrides_only_the_getter_keeps_the_parents_setter(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [OFFSET], parent="Base")
    project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    project.add_method("Base", "Write Count", writer(BASE), property_name="Count")
    project.add_method("Child", "Read Total", reader(CHILD), property_name="Count")
    package = project.generate()
    assert package.gaps == []
    assert "@Base.count.getter" in package.sources["child"]
    with package.use() as module:
        obj = module("child").Child()
        obj.count = 4
        obj._Child__offset = 9
        # The child's getter reads its own Offset; the parent's setter is kept.
        assert (obj.count, obj._Base__count) == (9, 4)
        assert module("base").Base().count == 0


# --- 5: a fork, then a change ----------------------------------------------------


def fork_project(tmp_path: Path, forked: bool) -> Project:
    project = Project(tmp_path)
    project.add_class("Counter", [COUNT])
    counter = class_type("Counter")
    bump_key = project.key_of("Counter", "Bump")
    project.add_method("Counter", "Bump", bump(counter, 0, True), "public", True)

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        wires = [wire(vi, "obj", "c.self"), wire(vi, "c.self out", "out")]
        nodes: list[GraphNode] = [
            call(
                vi,
                "c",
                bump_key,
                "iUse",
                [("self", 0, counter), ("n", 2, INT)],
                [("self out", 1, counter)],
            )
        ]
        outputs = [pane(vi, "output", "out", counter, "out", 1)]
        if forked:
            nodes.append(mux(vi, "get", False, counter, INT, 0))
            wires += [wire(vi, "obj", "get.agg"), wire(vi, "get.field", "seen")]
            outputs.append(pane(vi, "output", "seen", INT, "seen", 2))
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "obj", counter, "obj", 0)],
                outputs=outputs,
                data_flow=wires,
            ),
            nodes,
        )

    project.add_method("Counter", "Run", build, static=True)
    return project


def test_a_value_read_by_another_branch_is_copied_before_it_is_changed(
    tmp_path: Path,
) -> None:
    package = fork_project(tmp_path, forked=True).generate()
    assert "copy.deepcopy(self)" in package.sources["counter"]
    with package.use() as module:
        original = module("counter").Counter()
        original._Counter__count = 3
        result = original.run()
        assert original._Counter__count == 3
        assert result.seen == 3
        assert result.out is not original and result.out._Counter__count == 3


def test_a_value_nothing_else_reads_is_changed_where_it_is(tmp_path: Path) -> None:
    package = fork_project(tmp_path, forked=False).generate()
    assert "deepcopy" not in package.sources["counter"]
    with package.use() as module:
        original = module("counter").Counter()
        # The object is changed in place, so the method returns nothing.
        assert original.run() is None


def test_a_named_cluster_is_copied_when_it_forks(tmp_path: Path) -> None:
    point = LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Point.ctl",
        fields=[ClusterField(name="x", type=INT), ClusterField(name="y", type=INT)],
    )
    project = Project(tmp_path)

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "data", point, "data", 0),
                    pane(vi, "input", "x", INT, "x", 1),
                ],
                outputs=[
                    pane(vi, "output", "out", point, "out", 2),
                    pane(vi, "output", "y", INT, "y", 3),
                ],
                data_flow=[
                    wire(vi, "data", "set.agg"),
                    wire(vi, "data", "get.agg"),
                    wire(vi, "x", "set.field"),
                    wire(vi, "set.agg_out", "out"),
                    wire(vi, "get.field", "y"),
                ],
            ),
            [
                mux(vi, "set", True, point, INT, 0),
                mux(vi, "get", False, point, INT, 1),
            ],
        )

    project.add_vi("Edit", build)
    package = project.generate()
    assert "copy.deepcopy(data)" in package.sources["edit"]
    with package.use() as module:
        from types import SimpleNamespace

        data = SimpleNamespace(x=1, y=2)
        result = module("edit").edit(data=data, x=9)
        assert (data.x, data.y) == (1, 2)
        assert (result.out.x, result.y) == (9, 2)


def test_a_value_entering_a_loop_through_an_input_tunnel_is_shared() -> None:
    from lvkit.graph import InMemoryVIGraph
    from tests.helpers import build_graph

    vi = "loop"
    outer = TunnelTerminal(
        id="loop:outer",
        index=0,
        direction="input",
        boundary="outer",
        tunnel_type="lpTun",
        paired_id="loop:inner",
    )
    inner = TunnelTerminal(
        id="loop:inner",
        index=0,
        direction="output",
        boundary="inner",
        tunnel_type="lpTun",
        paired_id="loop:outer",
    )
    structure = LoopNode(
        id="loop:for",
        vi_path=vi,
        node_type="forLoop",
        loop_type="forLoop",
        terminals=[outer, inner],
    )
    changer = mux(vi, "set", True, BASE, INT, 0)
    context = VIContext(
        name=vi,
        inputs=[pane(vi, "input", "obj", BASE, "obj", 0)],
        outputs=[],
        data_flow=[
            wire(vi, "obj", "outer"),
            wire(vi, "outer", "inner"),
            wire(vi, "inner", "set.agg"),
        ],
    )
    graph = build_graph(context, vi, [structure, changer], graph=InMemoryVIGraph())
    assert graph.value_is_shared("loop:set.agg")
    # The same object wired straight to the node is not read by anything else.
    plain = VIContext(
        name="plain",
        inputs=[pane("plain", "input", "obj", BASE, "obj", 0)],
        outputs=[],
        data_flow=[wire("plain", "obj", "set.agg")],
    )
    graph = build_graph(
        plain, "plain", [mux("plain", "set", True, BASE, INT, 0)], graph=graph
    )
    assert not graph.value_is_shared("plain:set.agg")


# --- 6: a class that holds a refnum ----------------------------------------------


def test_a_class_holding_a_refnum_copies_everything_but_the_refnum(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    handle = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="Queue"
    )
    items = LVType(kind=LVTypeKind.ARRAY, element_type=INT, dimensions=1)
    project.add_class(
        "Holder",
        [
            ClusterField(name="Queue", type=handle),
            ClusterField(name="Items", type=items),
        ],
    )
    package = project.generate()
    assert "def __deepcopy__" in package.sources["holder"]
    with package.use() as module:
        holder = module("holder").Holder()
        holder._Holder__queue = queue.Queue()
        holder._Holder__items = [1, 2]
        clone = copy.deepcopy(holder)
        assert clone._Holder__queue is holder._Holder__queue
        assert clone._Holder__items == [1, 2]
        assert clone._Holder__items is not holder._Holder__items
        assert type(clone) is type(holder)


# --- 8: a parent that was not found ----------------------------------------------


def test_a_missing_parent_leaves_the_class_importable_and_its_code_raising(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Orphan", [COUNT], parent="Missing", parent_found=False)
    project.add_method("Orphan", "Passthrough", passthrough(class_type("Orphan")))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    reasons = [g.reason for g in gaps]
    assert len(reasons) == 1
    assert "parent class Missing of Orphan.lvclass was not found" in reasons[0]
    assert str(tmp_path) not in reasons[0]
    with package.use() as module:
        orphan = module("orphan").Orphan
        assert orphan.__bases__ == (object,)
        with pytest.raises(NotImplementedError, match="parent class Missing"):
            orphan()
        with pytest.raises(NotImplementedError, match="parent class Missing"):
            orphan.passthrough(None)


# --- 9: what an accessor is ---------------------------------------------------------


def test_a_setter_that_hands_back_another_object_is_a_method(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method(
        "Base", "Write Count", writer(BASE, hands_back=False), property_name="Count"
    )
    assert attributes_of(project).accessors == {}
    assert project.graph.get_method_access(project.vis["Base.Write Count"]) is not None


def test_a_getter_and_a_setter_over_one_field_are_one_property(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    project.add_method("Base", "Write Count", writer(BASE), property_name="Count")
    attributes = attributes_of(project)
    assert attributes.fields == ["__count"]
    assert {(a.role, a.python_name) for a in attributes.accessors.values()} == {
        (PropertyRole.GETTER, "count"),
        (PropertyRole.SETTER, "count"),
    }


# --- 10: a static member with no class input -------------------------------------


def test_a_static_member_with_no_class_input_is_a_static_method(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    make_key = project.add_method("Base", "Make", static_make, static=True)

    def use(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "x", INT, "x", 0)],
                outputs=[pane(vi, "output", "y", INT, "y", 1)],
                data_flow=[wire(vi, "x", "c.seed"), wire(vi, "c.value", "y")],
            ),
            [call(vi, "c", make_key, "iUse", [("seed", 0, INT)], [("value", 1, INT)])],
        )

    project.add_vi("Use", use)
    package = project.generate()
    assert "@staticmethod\n    def make(seed" in package.sources["base"]
    assert "Base.make(seed=x)" in package.sources["use"]
    assert "from .base import Base" in package.sources["use"]
    with package.use() as module:
        assert module("use").use(x=4).y == 4
        assert module("base").Base.make(seed=7) == 7


# --- 11: a method named like a property -------------------------------------------


def test_a_method_with_the_name_of_a_property_is_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    project.add_method("Base", "Write Count", writer(BASE), property_name="Count")
    method = project.add_method("Base", "count", passthrough(BASE))

    def use(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "obj", BASE, "obj", 0)],
                outputs=[],
                data_flow=[wire(vi, "obj", "c.self")],
            ),
            [
                call(
                    vi,
                    "c",
                    method,
                    "dynIUse",
                    [("self", 0, BASE)],
                    [("self out", 1, BASE)],
                )
            ],
        )

    project.add_vi("Use", use)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    reasons = [g.reason for g in gaps]
    assert len(reasons) == 2 and all("both 'count' in Python" in r for r in reasons)
    # The method is not defined: the name is the property's.
    assert package.sources["base"].count("def count(") == 2
    with package.use() as module:
        base = module("base").Base()
        assert base.count == 0
        with pytest.raises(NotImplementedError, match="both 'count'"):
            module("use").use(obj=base)


def test_the_python_of_a_class_calls_nothing_in_lvkit(tmp_path: Path) -> None:
    package = inheritance_project(tmp_path).generate()
    for source in package.sources.values():
        assert "lvkit" not in source


# --- the fixes found in review ---------------------------------------------------


def _unwired_dispatch(owner: LVType, callee: str) -> Recipe:
    """A method that calls ``callee`` by dynamic dispatch with nothing wired to
    the object."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", owner, "Self", 0)],
                outputs=[pane(vi, "output", "out", owner, "Self out", 1)],
                data_flow=[wire(vi, "in", "out")],
            ),
            [
                call(
                    vi,
                    "c",
                    callee,
                    "dynIUse",
                    [("self", 0, owner)],
                    [("self out", 1, owner)],
                )
            ],
        )

    return build


def test_a_method_that_cannot_be_written_is_that_vis_gap_not_the_classs(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    info_key = project.key_of("Base", "Info")
    project.add_method("Base", "Info", info(BASE))
    project.add_method("Base", "Broken", _unwired_dispatch(BASE, info_key))
    project.add_method("Base", "NoObject", static_make)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    reasons = sorted(g.reason for g in gaps)
    assert len(reasons) == 2
    assert any("no object wired" in r for r in reasons)
    assert any("no input of its class Base" in r for r in reasons)
    with package.use() as module:
        base = module("base").Base()
        base._Base__count = 4
        assert tuple(base.info()) == (4, 8)
        with pytest.raises(NotImplementedError, match="no object wired"):
            base.broken()
        with pytest.raises(NotImplementedError, match="no input of its class"):
            module("base").Base.noobject()


def _structure_project(reader_first: bool):
    """A structure that two tunnels carry one object into: a Bundle By Name, and
    an Unbundle By Name that, with ``reader_first``, its value feeds."""
    from lvkit.graph import InMemoryVIGraph
    from tests.helpers import build_graph

    vi = "s"

    def tunnel(tag: str, direction: str, boundary: str) -> TunnelTerminal:
        return TunnelTerminal(
            id=f"s:{tag}",
            index=0,
            direction=direction,
            boundary=boundary,
            tunnel_type="csTun",
            paired_id=f"s:{tag[:-1]}{'i' if tag.endswith('o') else 'o'}",
        )

    structure = CaseStructureNode(
        id="s:case",
        vi_path=vi,
        node_type="select",
        terminals=[
            tunnel("1o", "input", "outer"),
            tunnel("1i", "output", "inner"),
            tunnel("2o", "input", "outer"),
            tunnel("2i", "output", "inner"),
        ],
        frames=[CaseFrame(selector_value="True")],
    )
    changer = mux(vi, "set", True, BASE, INT, 0)
    reader = mux(vi, "get", False, BASE, INT, 0)
    for node in (changer, reader):
        node.parent = "s:case"
        node.frame = "True"
    wires = [
        wire(vi, "obj", "1o"),
        wire(vi, "1o", "1i"),
        wire(vi, "1i", "set.agg"),
        wire(vi, "obj", "2o"),
        wire(vi, "2o", "2i"),
        wire(vi, "2i", "get.agg"),
    ]
    nodes = [structure, changer, reader]
    if reader_first:
        nodes.append(add(vi, "sum"))
        wires += [wire(vi, "get.field", "sum.x"), wire(vi, "sum.sum", "set.field")]
    context = VIContext(
        name=vi,
        inputs=[pane(vi, "input", "obj", BASE, "obj", 0)],
        outputs=[],
        data_flow=wires,
    )
    return build_graph(context, vi, nodes, graph=InMemoryVIGraph())


def test_a_reader_inside_the_same_structure_makes_the_value_shared() -> None:
    assert _structure_project(reader_first=False).value_is_shared("s:set.agg")


def test_a_reader_inside_the_structure_that_runs_first_does_not() -> None:
    assert not _structure_project(reader_first=True).value_is_shared("s:set.agg")


def test_an_unbundled_object_is_copied_before_it_is_changed(tmp_path: Path) -> None:
    counter, holder = class_type("Counter"), class_type("Holder")
    project = Project(tmp_path)
    project.add_class("Counter", [COUNT])
    project.add_class("Holder", [ClusterField(name="Inner", type=counter)])
    put = project.add_method("Counter", "Put", writer(counter), static=True)

    def take(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "holder", holder, "holder", 0),
                    pane(vi, "input", "v", INT, "v", 1),
                ],
                outputs=[pane(vi, "output", "out", counter, "out", 2)],
                data_flow=[
                    wire(vi, "holder", "get.agg"),
                    wire(vi, "get.field", "c.self"),
                    wire(vi, "v", "c.value"),
                    wire(vi, "c.self out", "out"),
                ],
            ),
            [
                mux(vi, "get", False, holder, counter, 0),
                call(
                    vi,
                    "c",
                    put,
                    "iUse",
                    [("self", 0, counter), ("value", 2, INT)],
                    [("self out", 1, counter)],
                ),
            ],
        )

    project.add_method("Holder", "Take", take, static=True)
    package = project.generate()
    assert package.gaps == []
    assert "copy.deepcopy(self.__inner)" in package.sources["holder"]
    with package.use() as module:
        holding = module("holder").Holder()
        holding._Holder__inner = module("counter").Counter()
        result = holding.take(v=5)
        assert holding._Holder__inner._Counter__count == 0
        assert result._Counter__count == 5 and result is not holding._Holder__inner


def test_an_object_entering_a_loop_is_copied_in_each_iteration(
    tmp_path: Path,
) -> None:
    from lvkit.models import Tunnel, TunnelMode
    from tests.helpers import tunnel_terminals

    counter = class_type("Counter")
    project = Project(tmp_path)
    project.add_class("Counter", [COUNT])

    def run(vi: str) -> tuple[VIContext, list[GraphNode]]:
        tunnels = [
            Tunnel(
                outer_terminal_uid=f"{vi}:n_o",
                inner_terminal_uid=f"{vi}:n_i",
                tunnel_type="lMax",
            ),
            Tunnel(
                outer_terminal_uid=f"{vi}:o_o",
                inner_terminal_uid=f"{vi}:o_i",
                tunnel_type="lpTun",
                mode=TunnelMode.PASSTHROUGH,
            ),
            Tunnel(
                outer_terminal_uid=f"{vi}:v_o",
                inner_terminal_uid=f"{vi}:v_i",
                tunnel_type="lpTun",
                mode=TunnelMode.PASSTHROUGH,
            ),
        ]
        terminals = tunnel_terminals(tunnels)
        terminals += [
            TunnelTerminal(
                id=f"{vi}:r_i",
                index=20,
                direction="input",
                boundary="inner",
                tunnel_type="lpTun",
                paired_id=f"{vi}:r_o",
                mode=TunnelMode.LAST_VALUE,
                lv_type=counter,
            ),
            TunnelTerminal(
                id=f"{vi}:r_o",
                index=21,
                direction="output",
                boundary="outer",
                tunnel_type="lpTun",
                paired_id=f"{vi}:r_i",
                mode=TunnelMode.LAST_VALUE,
                lv_type=counter,
            ),
        ]
        for t in terminals:
            if t.id.endswith(("o_o", "o_i")):
                t.lv_type = counter
            elif t.id.endswith(("v_o", "v_i", "n_o", "n_i")):
                t.lv_type = INT
        loop = LoopNode(
            id=f"{vi}:for",
            vi_path=vi,
            node_type="forLoop",
            loop_type="forLoop",
            terminals=terminals,
        )
        changer = mux(vi, "set", True, counter, INT, 0)
        changer.parent = loop.id
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "obj", counter, "obj", 0),
                    pane(vi, "input", "v", INT, "v", 1),
                    pane(vi, "input", "n", INT, "n", 2),
                ],
                outputs=[pane(vi, "output", "out", counter, "out", 3)],
                data_flow=[
                    wire(vi, "n", "n_o"),
                    wire(vi, "obj", "o_o"),
                    wire(vi, "o_o", "o_i"),
                    wire(vi, "o_i", "set.agg"),
                    wire(vi, "v", "v_o"),
                    wire(vi, "v_o", "v_i"),
                    wire(vi, "v_i", "set.field"),
                    wire(vi, "set.agg_out", "r_i"),
                    wire(vi, "r_i", "r_o"),
                    wire(vi, "r_o", "out"),
                ],
            ),
            [loop, changer],
        )

    project.add_method("Counter", "Run", run, static=True)
    package = project.generate()
    assert package.gaps == []
    assert "copy.deepcopy(" in package.sources["counter"]
    with package.use() as module:
        original = module("counter").Counter()
        result = original.run(v=7, n=2)
        assert original._Counter__count == 0
        assert result._Counter__count == 7


def test_a_free_vi_that_holds_a_class_makes_the_class_import_lazily(
    tmp_path: Path,
) -> None:
    from lvkit.codegen.class_model import lazy_imports

    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    method = project.add_method("Base", "Probe", passthrough(BASE))
    free = project.add_vi("Helper", passthrough(BASE))
    graph = project.graph
    graph._dep_graph.add_edge(method, free)  # noqa: SLF001
    assert not lazy_imports(graph, project.classes["Base"])
    # The free VI names the class only for a class constant.
    graph._dep_graph.add_edge(free, project.classes["Base"])  # noqa: SLF001
    from lvkit.codegen import class_model

    class_model._STATES.pop(graph, None)  # noqa: SLF001
    assert lazy_imports(graph, project.classes["Base"])


def test_a_member_in_a_subfolder_is_found_from_the_folder_of_its_module() -> None:
    from lvkit.codegen.nodes.vi_reference import _path_from

    assert (
        _path_from(object(), "/p/Cls/private/X.vi", "/p/Cls/Cls.lvclass")
        == "private/X.vi"
    )
    assert (
        _path_from(object(), "/p/Other/Y.vi", "/p/Cls/Cls.lvclass") == "../Other/Y.vi"
    )


def test_call_parent_of_a_forked_object_works_on_a_copy(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [EXTRA], parent="Base")
    base_key = project.key_of("Base", "Probe")

    def base_probe(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "in", BASE, "Self", 0),
                    pane(vi, "input", "n", INT, "n", 2),
                ],
                outputs=[
                    pane(vi, "output", "out", BASE, "Self out", 1),
                    pane(vi, "output", "seen", INT, "seen", 3),
                ],
                data_flow=[
                    wire(vi, "in", "get.agg"),
                    wire(vi, "in", "out"),
                    wire(vi, "get.field", "seen"),
                ],
            ),
            [mux(vi, "get", False, BASE, INT, 0)],
        )

    def child_probe(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "in", CHILD, "Self", 0),
                    pane(vi, "input", "n", INT, "n", 2),
                ],
                outputs=[
                    pane(vi, "output", "out", CHILD, "Self out", 1),
                    pane(vi, "output", "seen", INT, "seen", 3),
                ],
                data_flow=[
                    wire(vi, "in", "c.self"),
                    wire(vi, "n", "c.n"),
                    wire(vi, "c.self out", "out"),
                    wire(vi, "in", "get.agg"),
                    wire(vi, "get.field", "seen"),
                ],
            ),
            [
                call(
                    vi,
                    "c",
                    base_key,
                    "callParentDynIUse",
                    [("self", 0, BASE), ("n", 2, INT)],
                    [("self out", 1, BASE), ("seen", 3, INT)],
                ),
                mux(vi, "get", False, CHILD, INT, 0),
            ],
        )

    project.add_method("Base", "Probe", base_probe)
    project.add_method("Child", "Probe", child_probe)
    package = project.generate()
    child_source = package.sources["child"]
    assert "copy.deepcopy(self)" in child_source
    assert "super(Child, " in child_source
    with package.use() as module:
        child = module("child").Child()
        result = child.probe(n=1)
        assert result.seen == 0


# --- the private data of each class is its own -------------------------------------


GRAND = class_type("Grand")


def three_levels(tmp_path: Path) -> Project:
    """Base, Child and Grand each have a field ``Count`` and a method that adds to
    it; Base has the property ``Count``, which Child overrides (adding its own
    Count to the parent's) and Grand overrides again."""
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [COUNT], parent="Base")
    project.add_class("Grand", [COUNT], parent="Child")
    project.add_method("Base", "Bump Base", bump(BASE, 0, True))
    project.add_method("Child", "Bump Child", bump(CHILD, 0, True))
    project.add_method("Grand", "Bump Grand", bump(GRAND, 0, True))
    read_key = project.key_of("Base", "Read Count")
    project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    project.add_method("Base", "Write Count", writer(BASE), property_name="Count")
    child_read = project.key_of("Child", "Read Count")
    project.add_method(
        "Child", "Read Count", parent_reader(read_key), property_name="Count"
    )

    def grand_reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
        context, nodes = parent_reader(child_read)(vi)
        for node in nodes:
            for t in node.terminals:
                if t.lv_type == CHILD:
                    t.lv_type = GRAND
        for t in [*context.inputs, *context.outputs]:
            if t.lv_type == CHILD:
                t.lv_type = GRAND
        return context, nodes

    project.add_method("Grand", "Read Count", grand_reader, property_name="Count")
    return project


def test_a_parent_and_a_child_field_of_one_name_are_two_values(
    tmp_path: Path,
) -> None:
    package = three_levels(tmp_path).generate()
    assert package.gaps == []
    with package.use() as module:
        obj = module("grand").Grand()
        assert sorted(vars(obj)) == ["_Base__count", "_Child__count", "_Grand__count"]
        obj.bump_base(n=1)
        obj.bump_child(n=10)
        obj.bump_grand(n=100)
        # Each class's method sees its own class's field.
        assert (obj._Base__count, obj._Child__count, obj._Grand__count) == (
            1,
            10,
            100,
        )
        # Each override reads its own field and the parent's through Call Parent.
        assert obj.count == 111
        child = module("child").Child()
        child.bump_child(n=4)
        assert (child.count, child._Base__count) == (4, 0)


def test_overriding_a_read_only_property_keeps_it_read_only(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [COUNT], parent="Base")
    read_key = project.key_of("Base", "Read Count")
    project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    project.add_method(
        "Child", "Read Count", parent_reader(read_key), property_name="Count"
    )
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        obj = module("child").Child()
        obj._Child__count = 2
        assert obj.count == 2
        with pytest.raises(AttributeError):
            obj.count = 1


def test_a_child_method_reads_a_parent_property_never_the_parents_field(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [COUNT], parent="Base")
    read_key = project.add_method(
        "Base", "Read Count", reader(BASE), property_name="Count"
    )
    project.add_method("Base", "Write Count", writer(BASE), property_name="Count")

    def peek(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", CHILD, "Self", 0)],
                outputs=[
                    pane(vi, "output", "out", CHILD, "Self out", 1),
                    pane(vi, "output", "seen", INT, "seen", 2),
                ],
                data_flow=[
                    wire(vi, "in", "c.self"),
                    wire(vi, "c.self out", "out"),
                    wire(vi, "c.value", "seen"),
                ],
            ),
            [
                call(
                    vi,
                    "c",
                    read_key,
                    "dynIUse",
                    [("self", 0, BASE)],
                    [("self out", 1, BASE), ("value", 2, INT)],
                )
            ],
        )

    project.add_method("Child", "Parent Count", peek)
    package = project.generate()
    assert package.gaps == []
    child_source = package.sources["child"]
    assert "self.count" in child_source and "_Base__" not in child_source
    with package.use() as module:
        obj = module("child").Child()
        obj.count = 7
        obj._Child__count = 1
        assert obj.parent_count() == 7


# --- Call By Reference of a member VI -----------------------------------------------


def _by_reference(callee: str, owner: LVType) -> Recipe:
    """A VI of no class that calls ``callee`` by a Call By Reference node whose VI
    is chosen in its setup, on its ``obj``, with its ``n``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = call(
            vi,
            "c",
            callee,
            "callByRefNode",
            [("self", 0, owner), ("n", 2, INT)],
            [("self out", 1, owner)],
        )
        node.qualified_name = Path(callee).name
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "obj", owner, "obj", 0),
                    pane(vi, "input", "n", INT, "n", 1),
                ],
                outputs=[pane(vi, "output", "out", owner, "out", 2)],
                data_flow=[
                    wire(vi, "obj", "c.self"),
                    wire(vi, "n", "c.n"),
                    wire(vi, "c.self out", "out"),
                ],
            ),
            [node],
        )

    return build


def test_a_call_by_reference_of_a_static_member_calls_the_method(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    add_key = project.add_method("Base", "Add", bump(BASE, 0, True), static=True)
    project.add_vi("Caller", _by_reference(add_key, BASE))
    package = project.generate()
    assert package.gaps == []
    assert "Base.add(self=obj, n=n)" in package.sources["caller"]
    with package.use() as module:
        obj = module("base").Base()
        assert module("caller").caller(obj=obj, n=3).out is obj
        assert obj._Base__count == 3


def test_a_call_by_reference_of_a_dynamic_member_is_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    add_key = project.add_method("Base", "Add", bump(BASE, 0, True))
    project.add_vi("Caller", _by_reference(add_key, BASE))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    (gap,) = gaps
    assert "Call By Reference does not support dynamic member VIs" in gap.reason
    assert "dynamic-terminal-on-call-by-reference" in gap.reason
    with package.use() as module:
        with pytest.raises(NotImplementedError, match="dynamic member VIs"):
            module("caller").caller(obj=module("base").Base(), n=1)


def test_a_reference_to_a_property_read_vi_is_its_getter(tmp_path: Path) -> None:
    from lvkit.codegen.class_model import member_reference, not_a_method
    from lvkit.pipeline import create_import_resolver

    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    read = project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    write = project.add_method(
        "Base", "Write Count", writer(BASE), property_name="Count"
    )
    graph = project.graph
    assert not_a_method(graph, read) is None
    out = tmp_path / "pkg"
    paths = {project.classes["Base"]: out / "base.py"}
    resolver = create_import_resolver("pkg", out, paths, out / "x.py", graph=graph)
    for key, side in ((read, "fget"), (write, "fset")):
        shape = member_shape(graph, key)
        assert shape is not None
        expression, _ = member_reference(graph, shape, resolver, None)
        assert expression == f"Base.count.{side}"


def test_only_a_private_member_is_spelled_with_two_underscores() -> None:
    from lvkit.codegen.class_attributes import is_private, member_name

    assert member_name("_helper", "protected") == "_helper"
    assert member_name("__x", "public") == "x"
    assert member_name("_class", "public") == "class_"
    assert member_name("count__", "private") == "__count"
    assert [is_private(member_name("_x", s)) for s in ("public", "protected")] == [
        False,
        False,
    ]


def test_a_member_named_like_a_method_lvkit_writes_is_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "share_refnums", passthrough(BASE), "protected")
    gaps: list[Gap] = []
    package = project.generate(gaps)
    (gap,) = gaps
    assert "'_share_refnums'" in gap.reason
    assert "def _share_refnums" not in package.sources["base"]


# --- a reference value that may hold a method of a class -----------------------------


def _refnum(slots: list[tuple[int, bool, LVType, str]]) -> LVType:
    """A strictly typed VI refnum whose connector pane has ``slots``."""
    from lvkit.models import PaneSlot

    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="LVObjCtl",
        element_type=LVType(
            kind=LVTypeKind.PRIMITIVE,
            underlying_type="Function",
            pane=tuple(PaneSlot(i, out, t, name) for i, out, t, name in slots),
        ),
    )


def _call_held(slots: list[tuple[int, bool, LVType, str]]) -> Recipe:
    """A VI of no class that calls the VI its ``ref`` control holds: each input
    slot of the pane is wired from a control of the slot's name, each output to
    an indicator."""
    reference = _refnum(slots)

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        terminals = [
            Terminal(
                id=f"{vi}:c.ref", index=-1, direction="input", lv_type=reference
            ),
            *(
                Terminal(
                    id=f"{vi}:c.{name}",
                    index=i,
                    direction="output" if out else "input",
                    lv_type=t,
                    name=name,
                )
                for i, out, t, name in slots
            ),
        ]
        node = VINode(
            id=f"{vi}:c",
            vi_path=vi,
            name="Call By Reference",
            node_type="callByRefNode",
            terminals=terminals,
        )
        inputs = [pane(vi, "input", "ref", reference, "ref", 0)]
        outputs = []
        wires = [wire(vi, "ref", "c.ref")]
        for n, (_, out, t, name) in enumerate(slots, start=1):
            if out:
                outputs.append(pane(vi, "output", f"o_{name}", t, name, n))
                wires.append(wire(vi, f"c.{name}", f"o_{name}"))
            else:
                inputs.append(pane(vi, "input", f"i_{name}", t, name, n))
                wires.append(wire(vi, f"i_{name}", f"c.{name}"))
        return (
            VIContext(name=vi, inputs=inputs, outputs=outputs, data_flow=wires),
            [node],
        )

    return build


def _function(slots: list[tuple[int, bool, LVType, str]]) -> Recipe:
    """A VI with the pane ``slots`` whose outputs are its first input."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        ins = [s for s in slots if not s[1]]
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", n, t, n, i) for i, _, t, n in ins],
                outputs=[pane(vi, "output", n, t, n, i) for i, o, t, n in slots if o],
                data_flow=[
                    wire(vi, ins[0][3], n) for _, o, _, n in slots if o and ins
                ],
            ),
            [],
        )

    return build


SEED = [(0, False, INT, "seed"), (1, True, INT, "value")]


def test_a_reference_value_to_an_instance_member_calls_it_with_self(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Bump", bump(BASE, 0, True), static=True)
    slots = [
        (0, False, BASE, "Base in"),
        (1, True, BASE, "Base out"),
        (2, False, INT, "n"),
    ]
    project.add_vi("Caller", _call_held(slots))
    package = project.generate()
    assert package.gaps == []
    # The class input is the method's `self`, not the pane's label.
    assert "ref(self=base_in, n=n)" in package.sources["caller"]
    with package.use() as module:
        base = module("base").Base
        obj = base()
        result = module("caller").caller(ref=base.bump, base_in=obj, n=4)
        assert obj._Base__count == 4 and result.base_out is obj


def test_a_reference_value_to_a_static_member_returns_its_one_value(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Make", static_make, static=True)
    project.add_vi("Caller", _call_held(SEED))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        result = module("caller").caller(ref=module("base").Base.make, seed=6)
        assert result.value == 6


def test_a_reference_value_to_a_plain_vi_reads_its_result_fields(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Twice", _function(SEED))
    project.add_vi("Caller", _call_held(SEED))
    package = project.generate()
    assert package.gaps == []
    assert ".value" in package.sources["caller"]
    with package.use() as module:
        assert module("caller").caller(ref=module("twice").twice, seed=3).value == 3


def test_a_member_and_a_vi_called_differently_by_one_pane_are_a_gap(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Make", static_make, static=True)
    project.add_vi("Twice", _function(SEED))
    project.add_vi("Caller", _call_held(SEED))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    (gap,) = gaps
    assert "Base.lvclass:Make.vi" in gap.reason and "Twice.vi" in gap.reason
    assert "not all called the same way" in gap.reason
    with package.use() as module:
        with pytest.raises(NotImplementedError, match="not all called the same"):
            module("caller").caller(ref=module("twice").twice, seed=1)


def test_a_member_and_a_vi_called_the_same_way_share_the_call(
    tmp_path: Path,
) -> None:
    # Two outputs: both return a NamedTuple with the same fields.
    slots = [(0, False, INT, "seed"), (1, True, INT, "a"), (2, True, INT, "b")]
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Pair", _function(slots), static=True)
    project.add_vi("Pair2", _function(slots))
    project.add_vi("Caller", _call_held(slots))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        caller = module("caller").caller
        assert tuple(caller(ref=module("base").Base.pair, seed=2)) == (2, 2)
        assert tuple(caller(ref=module("pair2").pair2, seed=5)) == (5, 5)


def test_a_member_and_a_vi_with_no_outputs_share_the_call(tmp_path: Path) -> None:
    slots = [(0, False, INT, "seed")]
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Take", _function(slots), static=True)
    project.add_vi("Take2", _function(slots))
    project.add_vi("Caller", _call_held(slots))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        caller = module("caller").caller
        assert caller(ref=module("base").Base.take, seed=1) is None
        assert caller(ref=module("take2").take2, seed=1) is None


def test_an_error_cluster_output_is_no_result_for_either(tmp_path: Path) -> None:
    error = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(
                name="status",
                type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
            ),
            ClusterField(name="code", type=INT),
            ClusterField(
                name="source",
                type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
            ),
        ],
    )
    slots = [(0, False, INT, "seed"), (1, True, error, "error out")]

    def quiet(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "seed", INT, "seed", 0)],
                outputs=[pane(vi, "output", "err", error, "error out", 1)],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Quiet", quiet, static=True)
    project.add_vi("Quiet2", quiet)
    project.add_vi("Caller", _call_held(slots))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        caller = module("caller").caller
        assert caller(ref=module("base").Base.quiet, seed=1) is None
        assert caller(ref=module("quiet2").quiet2, seed=1) is None


def test_a_pane_typed_with_a_class_that_is_not_loaded_is_a_vis_call(
    tmp_path: Path,
) -> None:
    ghost = class_type("Ghost")
    slots = [(0, False, ghost, "obj"), (1, True, INT, "value")]
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Make", static_make, static=True)
    project.add_vi("Caller", _call_held(slots))
    package = project.generate()
    assert package.gaps == []
    assert "ref(obj=obj)" in package.sources["caller"]
    assert ".value" in package.sources["caller"]


def test_a_call_by_reference_of_a_property_read_and_write_vi_runs(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    read = project.add_method(
        "Base", "Read Count", reader(BASE), static=True, property_name="Count"
    )
    write = project.add_method(
        "Base", "Write Count", writer(BASE), static=True, property_name="Count"
    )

    def use(vi: str) -> tuple[VIContext, list[GraphNode]]:
        get = call(
            vi,
            "g",
            read,
            "callByRefNode",
            [("self", 0, BASE)],
            [("self out", 1, BASE), ("value", 2, INT)],
        )
        put = call(
            vi,
            "p",
            write,
            "callByRefNode",
            [("self", 0, BASE), ("value", 2, INT)],
            [("self out", 1, BASE)],
        )
        get.qualified_name, put.qualified_name = "Read Count.vi", "Write Count.vi"
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "obj", BASE, "obj", 0),
                    pane(vi, "input", "v", INT, "v", 1),
                ],
                outputs=[pane(vi, "output", "seen", INT, "seen", 2)],
                data_flow=[
                    wire(vi, "obj", "p.self"),
                    wire(vi, "v", "p.value"),
                    wire(vi, "p.self out", "g.self"),
                    wire(vi, "g.value", "seen"),
                ],
            ),
            [put, get],
        )

    project.add_vi("Use", use)
    package = project.generate()
    assert package.gaps == []
    source = package.sources["use"]
    assert "Base.count.fset(self=obj, value=v)" in source
    assert "Base.count.fget(self=obj)" in source
    with package.use() as module:
        obj = module("base").Base()
        assert module("use").use(obj=obj, v=8).seen == 8
        assert obj._Base__count == 8


def test_a_private_member_of_another_class_is_no_candidate(tmp_path: Path) -> None:
    # Only its own class's VIs call a private VI, so a VI of no class holding a
    # reference with its pane holds the plain VI.
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Make", static_make, "private", static=True)
    project.add_vi("Twice", _function(SEED))
    project.add_vi("Caller", _call_held(SEED))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        assert module("caller").caller(ref=module("twice").twice, seed=3).value == 3

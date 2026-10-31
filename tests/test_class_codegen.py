"""A LabVIEW class generates as one Python class whose methods are its VIs.

Each case is a handmade project of classes (``tests/class_graphs.py``) generated
into a package that is imported and run: the dispatch, the by-value copies, the
attributes and properties, and the gaps are checked on what the generated code
does, and every VI is described in every text view the graph has."""

from __future__ import annotations

import copy
import queue
from pathlib import Path

import pytest

from lvkit.class_property import PropertyRole
from lvkit.codegen.class_attributes import AccessorKind, class_attributes
from lvkit.codegen.class_model import MethodKind, member_shape
from lvkit.codegen.gaps import Gap
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import (
    CaseFrame,
    CaseStructureNode,
    Constant,
    GraphNode,
    LoopNode,
    VIContext,
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
    assert "def _add(self, n" in base_source
    assert "self._add(n=n)" in base_source
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
        assert sorted(vars(obj)) == ["_count", "_extra"]
        obj.increment(n=2)
        assert (obj._count, obj._extra) == (2, 2)
        parent = base()
        parent.increment(n=5)
        assert parent._count == 5
        # A private helper works from a child object.
        obj._add(n=1)
        assert obj._count == 3


def test_the_class_methods_have_one_shape_each(tmp_path: Path) -> None:
    project = inheritance_project(tmp_path)
    graph = project.graph
    add_shape = member_shape(graph, project.vis["Base.Add"])
    inc = member_shape(graph, project.vis["Base.Increment"])
    child_inc = member_shape(graph, project.vis["Child.Increment"])
    assert add_shape is not None and inc is not None and child_inc is not None
    assert (add_shape.kind, add_shape.name) == (MethodKind.INSTANCE, "_add")
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
        base._count = child._count = 3
        assert tuple(module("probe").probe(obj=base)) == (3, 6)
        assert tuple(module("probe").probe(obj=child)) == (6, 3)
        assert isinstance(child.info(), module("base").InfoResult)


# --- 4: an accessor overridden by a child ----------------------------------------


def accessor_project(tmp_path: Path) -> Project:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [OFFSET], parent="Base")
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


def test_an_overridden_accessor_is_a_property_in_the_parent_too(
    tmp_path: Path,
) -> None:
    project = accessor_project(tmp_path)
    parent = class_attributes(project.graph, project.classes["Base"])
    assert {a.kind for a in parent.accessors.values()} == {AccessorKind.PROPERTY}
    assert parent.fields == ["_count"]
    package = project.generate()
    assert package.gaps == []
    assert "@property" in package.sources["base"]
    assert "@count.setter" in package.sources["base"]
    assert "super().count" in package.sources["child"]
    assert "Base.count.fset(self, value)" in package.sources["child"]
    with package.use() as module:
        child, base = module("child").Child(), module("base").Base()
        child.count = 5
        child._offset = 3
        assert child._count == 5 and child.count == 8
        base.count = 2
        assert base.count == 2 and base._count == 2


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

    project.add_vi("Run", build)
    return project


def test_a_value_read_by_another_branch_is_copied_before_it_is_changed(
    tmp_path: Path,
) -> None:
    package = fork_project(tmp_path, forked=True).generate()
    source = package.sources["run"]
    assert "copy.deepcopy(obj)" in source
    with package.use() as module:
        original = module("counter").Counter()
        original._count = 3
        result = module("run").run(obj=original)
        assert original._count == 3
        assert result.seen == 3
        assert result.out is not original and result.out._count == 3


def test_a_value_nothing_else_reads_is_changed_where_it_is(tmp_path: Path) -> None:
    package = fork_project(tmp_path, forked=False).generate()
    assert "deepcopy" not in package.sources["run"]
    with package.use() as module:
        original = module("counter").Counter()
        result = module("run").run(obj=original)
        assert result.out is original


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
        holder._queue = queue.Queue()
        holder._items = [1, 2]
        clone = copy.deepcopy(holder)
        assert clone._queue is holder._queue
        assert clone._items == [1, 2] and clone._items is not holder._items
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


# --- 9: what makes an accessor only move its field -------------------------------


def case_around(selector_is_error: bool) -> Recipe:
    """A getter of Count with a case structure beside it, selected by the error
    cluster in or by a constant."""
    error_type = LVType(
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
    boolean = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        selector = Terminal(
            id=f"{vi}:case.sel",
            index=0,
            direction="input",
            lv_type=error_type if selector_is_error else boolean,
        )
        structure = CaseStructureNode(
            id=f"{vi}:case",
            vi_path=vi,
            node_type="select",
            selector_terminal=selector.id,
            terminals=[selector],
            frames=[
                CaseFrame(selector_value="False", is_no_error=True),
                CaseFrame(selector_value="True"),
            ],
        )
        inputs = [pane(vi, "input", "in", BASE, "Self", 0)]
        constants: list[Constant] = []
        if selector_is_error:
            inputs.append(pane(vi, "input", "err", error_type, "error in", 3))
            source = "err"
        else:
            constants.append(Constant(id=f"{vi}:flag", value="True", lv_type=boolean))
            source = "flag"
        return (
            VIContext(
                name=vi,
                inputs=inputs,
                outputs=[
                    pane(vi, "output", "out", BASE, "Self out", 1),
                    pane(vi, "output", "value", INT, "value", 2),
                ],
                constants=constants,
                data_flow=[
                    wire(vi, source, "case.sel"),
                    wire(vi, "in", "get.agg"),
                    wire(vi, "in", "out"),
                    wire(vi, "get.field", "value"),
                ],
            ),
            [structure, mux(vi, "get", False, BASE, INT, 0)],
        )

    return build


def kinds_of(project: Project, cls: str = "Base") -> set[AccessorKind]:
    return {
        a.kind
        for a in class_attributes(
            project.graph, project.classes[cls]
        ).accessors.values()
    }


def test_an_accessor_beside_an_error_case_structure_is_still_the_field(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Read Count", case_around(True), property_name="Count")
    assert kinds_of(project) == {AccessorKind.ATTRIBUTE}


def test_an_accessor_with_a_case_not_selected_by_the_error_cluster_is_a_property(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Read Count", case_around(False), property_name="Count")
    assert kinds_of(project) == {AccessorKind.PROPERTY}


def test_a_setter_that_hands_back_another_object_is_a_method(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method(
        "Base", "Write Count", writer(BASE, hands_back=False), property_name="Count"
    )
    assert class_attributes(project.graph, project.classes["Base"]).accessors == {}
    assert project.graph.get_method_access(project.vis["Base.Write Count"]) is not None


def test_a_getter_and_a_setter_over_one_field_are_the_attribute(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    project.add_method("Base", "Write Count", writer(BASE), property_name="Count")
    attributes = class_attributes(project.graph, project.classes["Base"])
    assert attributes.fields == ["count"]
    roles = {a.role for a in attributes.accessors.values()}
    assert roles == {PropertyRole.GETTER, PropertyRole.SETTER}


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


# --- 11: a method named like an attribute ----------------------------------------


def test_a_method_with_the_name_of_an_attribute_is_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Read Count", reader(BASE), property_name="Count")
    project.add_method("Base", "Write Count", writer(BASE), property_name="Count")
    project.add_method("Base", "count", passthrough(BASE))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    (gap,) = gaps
    assert "both 'count' in Python" in gap.reason
    with package.use() as module:
        base = module("base").Base
        assert base().count == 0
        with pytest.raises(NotImplementedError, match="both 'count'"):
            base.count(base())


def test_the_python_of_a_class_calls_nothing_in_lvkit(tmp_path: Path) -> None:
    package = inheritance_project(tmp_path).generate()
    for source in package.sources.values():
        assert "lvkit" not in source

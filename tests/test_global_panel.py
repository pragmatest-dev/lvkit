"""A Global VI has a front panel: its module's ``panel`` is a ``PanelValues`` over
the module's own names, the very values global variable nodes read and write, and
a reference to a Global VI is its module. Each case is a handmade project whose
generated package is imported and run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit.codegen.global_module import global_attribute_names
from lvkit.graph.models import (
    ConstantNode,
    GlobalVariableNode,
    GlobalVIRef,
    GraphNode,
    PrimitiveNode,
    VIContext,
    VINode,
    VIProperties,
)
from lvkit.models import (
    FPTerminal,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
    VIKind,
)
from lvkit.parser.models import ParsedFrontPanel
from tests.class_graphs import INT, Project, Recipe, pane, wire
from tests.test_run_vi import ERROR, VI_REF, invoke, runner_on_error_chain

STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")


def _control(vi: str, uid: str, name: str, lv_type: LVType, default: str) -> FPTerminal:
    return FPTerminal(
        id=f"{vi}:{uid}",
        index=None,
        direction="output",
        name=name,
        lv_type=lv_type,
        default_value=default,
        is_public=False,
        fp_dco_uid=uid,
    )


def global_vi(*controls: tuple[str, LVType, str]) -> Recipe:
    """A Global VI: its controls (name, type, saved default) and no diagram."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                off_pane_inputs=[
                    _control(vi, f"d{i}", name, lv_type, default)
                    for i, (name, lv_type, default) in enumerate(controls)
                ],
            ),
            [],
        )

    return build


def add_global(project: Project, name: str, *controls: tuple[str, LVType, str]) -> str:
    key = project.add_vi(name, global_vi(*controls))
    project.graph._vi_properties[key] = VIProperties(  # noqa: SLF001
        vi_type=VIKind.GLOBAL.label
    )
    vi_node = project.graph.get_graph_node(key)
    assert isinstance(vi_node, VINode)
    vi_node.front_panel = ParsedFrontPanel(
        controls=[],
        panel_bounds=(0, 0, 0, 0),
        dco_order=[t.fp_dco_uid for t in vi_node.terminals],
    )
    return key


def global_access(
    global_key: str, name: str, control: str, index: int, write: bool
) -> Recipe:
    """A VI that writes 42 to (``write``) or returns the global variable
    ``control`` of the Global VI at ``global_key``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = GlobalVariableNode(
            id=f"{vi}:g",
            vi_path=vi,
            name=control,
            terminals=[
                Terminal(
                    id=f"{vi}:g.t",
                    index=0,
                    direction="input" if write else "output",
                    lv_type=INT,
                )
            ],
            global_vi=GlobalVIRef(
                qualified_name=f"{name}.vi", path_tokens=(f"{name}.vi",), key=global_key
            ),
            control_name=control,
            param_idx=index,
            is_write=write,
        )
        nodes: list[GraphNode] = [node]
        if write:
            nodes.append(
                ConstantNode(
                    id=f"{vi}:c",
                    vi_path=vi,
                    name="c",
                    value="42",
                    lv_type=INT,
                    terminals=[
                        Terminal(id=f"{vi}:c", index=0, direction="output", lv_type=INT)
                    ],
                )
            )
            return VIContext(name=vi, data_flow=[wire(vi, "c", "g.t")]), nodes
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "out", INT, "out", 0)],
                data_flow=[wire(vi, "g.t", "out")],
            ),
            nodes,
        )

    return build


def two_controls_project(tmp_path: Path) -> tuple[Project, str]:
    project = Project(tmp_path)
    key = add_global(project, "Data", ("Count", INT, "5"), ("Total", INT, "9"))
    for tag, write in (("Writer", True), ("Reader", False)):
        caller = project.add_vi(tag, global_access(key, "Data", "Count", 0, write))
        project.graph._dep_graph.add_edge(caller, key)  # noqa: SLF001
    return project, key


def test_the_global_module_holds_a_panel_over_its_own_names(
    tmp_path: Path,
) -> None:
    project, _ = two_controls_project(tmp_path)
    package = project.generate()
    assert package.gaps == []
    assert package.sources["data"] == (
        "from __future__ import annotations\n"
        "from .panels import PanelValues\n"
        "from .variants import LV_I32\n"
        "panel = PanelValues(controls={'count': 'Count', 'total': 'Total'}, "
        "types={'count': LV_I32, 'total': LV_I32}, storage=globals())\n"
        "count: int = 5\n"
        "total: int = 9"
    )


def test_a_global_variable_write_is_seen_by_the_panel_and_set_by_the_panel_is_read(
    tmp_path: Path,
) -> None:
    project, _ = two_controls_project(tmp_path)
    package = project.generate()
    with package.use() as module:
        panel = module("data").panel
        assert module("panels").panel_of(module("data")) is panel
        assert module("reader").reader().out == 5
        assert panel.get("Count") == 5
        module("writer").writer()
        assert module("data").count == 42
        assert panel.get("Count") == 42
        panel.set("Count", 7)
        assert module("data").count == 7
        assert module("reader").reader().out == 7
        assert panel.get_all(True) == [("Count", 7), ("Total", 9)]
        assert panel.get_all() == []


def test_a_panel_value_the_views_are_told_of(tmp_path: Path) -> None:
    project, _ = two_controls_project(tmp_path)
    package = project.generate()
    with package.use() as module:
        told: list[tuple[str, object]] = []

        class View(module("panels").PanelView):
            def control_changed(self, name: str, value: object) -> None:
                told.append((name, value))

        panel = module("data").panel
        panel.attach(View())
        panel.set("Total", 8)
        assert told == [("total", 8)]
        assert module("data").total == 8


def test_an_unknown_label_is_error_1(tmp_path: Path) -> None:
    project, _ = two_controls_project(tmp_path)
    package = project.generate()
    with package.use() as module:
        with pytest.raises(Exception, match='Error 1: "Nope" not found'):
            module("data").panel.get("Nope")


def test_run_vi_on_a_global_vi_is_error_1035_on_the_nodes_error_out(
    tmp_path: Path,
) -> None:
    project, key = two_controls_project(tmp_path)
    runner = project.add_vi("RunIt", runner_on_error_chain(key))
    project.graph._dep_graph.add_edge(runner, key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    assert "from . import data as data_global" in package.sources["runit"]
    with package.use() as module:
        error = module("runit").runit().error_out
        assert (error.code, error.message) == (
            1035,
            "Operation is invalid for this type of VI.",
        )
        assert module("data").count == 5


def exec_state_reader(target_key: str) -> Recipe:
    """A VI that reads Exec.State of a Static VI Reference to ``target_key``,
    the Property Node's error out wired to the VI's ``error out``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        reference = ConstantNode(
            id=f"{vi}:ref",
            vi_path=vi,
            name="Target.vi",
            value="Target.vi",
            lv_type=VI_REF,
            referenced_vi_key=target_key,
            terminals=[
                Terminal(id=f"{vi}:ref", index=0, direction="output", lv_type=VI_REF)
            ],
        )
        node = PrimitiveNode(
            id=f"{vi}:prop",
            vi_path=vi,
            name="Property Node",
            node_type="propNode",
            object_name="VI",
            terminals=[
                Terminal(
                    id=f"{vi}:prop.ref", index=0, direction="input", lv_type=VI_REF
                ),
                Terminal(
                    id=f"{vi}:prop.err_out", index=3, direction="output", lv_type=ERROR
                ),
                Terminal(
                    id=f"{vi}:prop.value", index=4, direction="output", lv_type=INT
                ),
            ],
            properties=[
                PropertyDef(segments=[PropertySegment(name="Exec.State", code=557)])
            ],
            property_value_terminal_ids=[f"{vi}:prop.value"],
        )
        context = VIContext(
            name=vi,
            outputs=[
                pane(vi, "output", "state", INT, "state", 0),
                pane(vi, "output", "err_out", ERROR, "error out", 1),
            ],
            data_flow=[
                wire(vi, "ref", "prop.ref"),
                wire(vi, "prop.value", "state"),
                wire(vi, "prop.err_out", "err_out"),
            ],
        )
        return context, [reference, node]

    return build


def test_exec_state_of_a_global_vi_is_error_1035_on_the_nodes_error_out(
    tmp_path: Path,
) -> None:
    """A Global VI never runs: reading its Exec.State is error 1035, as Run VI on
    it is, reported on the Property Node's error out (no Python exception)."""
    project, key = two_controls_project(tmp_path)
    reader = project.add_vi("Reader", exec_state_reader(key))
    project.graph._dep_graph.add_edge(reader, key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        result = module("reader").reader()
        assert result.error_out.code == 1035
        assert result.state == 0


def test_a_reference_to_a_global_vi_is_its_module_for_ctrl_val(tmp_path: Path) -> None:
    project, key = two_controls_project(tmp_path)

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        reference = ConstantNode(
            id=f"{vi}:ref",
            vi_path=vi,
            name="Data.vi",
            value="Data.vi",
            lv_type=VI_REF,
            referenced_vi_key=key,
            terminals=[
                Terminal(id=f"{vi}:ref", index=0, direction="output", lv_type=VI_REF)
            ],
        )
        node = invoke(vi, "set", "Set Control Value", 1051)
        return (
            VIContext(
                name=vi,
                data_flow=[wire(vi, "ref", "set.ref")],
            ),
            [reference, node],
        )

    caller = project.add_vi("Setter", build)
    project.graph._dep_graph.add_edge(caller, key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    assert "panel_of(data_global).set_variant(" in package.sources["setter"]
    with package.use() as module:
        module("setter").setter()


def test_a_control_labelled_panel_does_not_take_the_modules_panel_name(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    key = add_global(project, "Data", ("Panel", INT, "1"))
    package = project.generate()
    source = package.sources["data"]
    assert "panel_2: int = 1" in source
    assert "panel = PanelValues(controls={'panel_2': 'Panel'}" in source
    node = project.graph.get_graph_node(key)
    assert list(global_attribute_names(node.terminals).values()) == ["panel_2"]
    with package.use() as module:
        assert module("data").panel.get("Panel") == 1
        module("data").panel.set("Panel", 3)
        assert module("data").panel_2 == 3


def test_a_function_vis_panel_is_still_a_panel(tmp_path: Path) -> None:
    from tests.test_run_vi import project_with

    package = project_with(tmp_path, None).generate()
    with package.use() as module:
        panels = module("panels")
        panel = module("target").target.panel
        assert isinstance(panel, panels.Panel)
        assert isinstance(panel, panels.PanelValues)
        assert panels.panel_of(module("target").target) is panel
        with pytest.raises(Exception, match="Error 1026"):
            panels.panel_of(SimpleNamespace(panel=object()))

"""Call Chain (primResID 1999, ``codegen/nodes/call_chain.py``): every generated VI
function carries its LabVIEW qualified name as ``function.vi_name`` and the node is
``panels.call_chain()``, the names of the VI functions on Python's own stack with the
VI the node is in first and the top-level VI last. Each case is a handmade project
whose generated package is imported and run."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import LVType, LVTypeKind, Terminal
from tests.class_graphs import Project, Recipe, call, pane, wire

STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
NAMES = LVType(kind=LVTypeKind.ARRAY, element_type=STRING)


def chain_node(vi: str) -> PrimitiveNode:
    return PrimitiveNode(
        id=f"{vi}:cc",
        vi_path=vi,
        name="Call Chain",
        node_type="prim",
        prim_id=1999,
        terminals=[
            Terminal(
                id=f"{vi}:cc.chain", index=0, direction="output", lv_type=NAMES
            )
        ],
    )


def probe() -> Recipe:
    """A VI whose ``chain`` output is its own Call Chain."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "chain", NAMES, "chain", 0)],
                data_flow=[wire(vi, "cc.chain", "chain")],
            ),
            [chain_node(vi)],
        )

    return build


def forwards(callee: str) -> Recipe:
    """A VI whose ``chain`` output is the ``chain`` of the VI ``callee`` it calls."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "chain", NAMES, "chain", 0)],
                data_flow=[wire(vi, "c.chain", "chain")],
            ),
            [call(vi, "c", callee, "iUse", [], [("chain", 0, NAMES)])],
        )

    return build


def test_each_vi_function_carries_its_qualified_name(tmp_path: Path) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Probe", probe())
    project.graph._vi_display_names[key] = "Lib.lvlib:Probe.vi"  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    assert "lvkit" not in package.sources["probe"]
    with package.use() as module:
        assert module("probe").probe.vi_name == "Lib.lvlib:Probe.vi"


def test_a_vi_that_calls_itself_down_a_chain_lists_every_caller(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    c = project.add_vi("C", probe())
    b = project.add_vi("B", forwards(c))
    project.graph._vi_display_names[b] = "Lib.lvlib:B.vi"  # noqa: SLF001
    a = project.add_vi("A", forwards(b))
    project.graph._dep_graph.add_edge(a, b)  # noqa: SLF001
    project.graph._dep_graph.add_edge(b, c)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        assert module("a").a().chain == ["C.vi", "Lib.lvlib:B.vi", "A.vi"]
        # Called from Python alone, the chain is the VI itself.
        assert module("c").c().chain == ["C.vi"]


def test_helper_frames_that_are_not_vis_are_skipped(tmp_path: Path) -> None:
    project = Project(tmp_path)
    c = project.add_vi("C", probe())
    b = project.add_vi("B", forwards(c))
    project.graph._dep_graph.add_edge(b, c)  # noqa: SLF001
    package = project.generate()
    with package.use() as module:
        b_function = module("b").b

        def helper() -> list[str]:
            return [name for name in b_function().chain]

        assert [n for n in helper()] == ["C.vi", "B.vi"]


def test_a_method_is_named_by_its_class(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Cls", [])
    method = project.add_method("Cls", "Probe", probe(), static=True)
    project.add_vi("Top", forwards(method))
    project.graph._dep_graph.add_edge(project.vis["Top"], method)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        assert module("top").top().chain == ["Cls.lvclass:Probe.vi", "Top.vi"]


def test_a_vi_in_another_thread_ends_at_its_top_vi(tmp_path: Path) -> None:
    project = Project(tmp_path)
    c = project.add_vi("C", probe())
    b = project.add_vi("B", forwards(c))
    project.graph._dep_graph.add_edge(b, c)  # noqa: SLF001
    package = project.generate()
    with package.use() as module:
        b_function = module("b").b
        results: list[Any] = []
        thread = threading.Thread(target=lambda: results.append(b_function()))
        thread.start()
        thread.join()
        assert results[0].chain == ["C.vi", "B.vi"]

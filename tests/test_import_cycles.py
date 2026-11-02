"""Generated modules that import each other (``codegen/import_cycles.py``): a VI
module on an import cycle imports the other generated modules inside its
function; one on no cycle imports them at module level."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.models import Constant, GraphNode, PrimitiveNode, VIContext
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.path_value import format_path
from tests.class_graphs import Project, Recipe, call, pane, wire
from tests.vi_reference_diagrams import this_vi_type

PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
REF = this_vi_type()


def opener(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """An Open VI Reference of the constant path ``Caller.vi`` (beside this VI);
    the reference is the output ``ref``."""
    node = PrimitiveNode(
        id=f"{vi}:open",
        vi_path=vi,
        name="Open VI Reference",
        node_type="prim",
        prim_id=8010,
        terminals=[
            Terminal(id=f"{vi}:open.path", index=10, direction="input", lv_type=PATH),
            Terminal(id=f"{vi}:open.ref", index=3, direction="output", lv_type=REF),
        ],
    )
    return (
        VIContext(
            name=vi,
            outputs=[pane(vi, "output", "ref", REF, "ref", 0)],
            constants=[
                Constant(
                    id=f"{vi}:path", value=format_path(1, ["Caller.vi"]), lv_type=PATH
                )
            ],
            data_flow=[wire(vi, "path", "open.path"), wire(vi, "open.ref", "ref")],
        ),
        [node],
    )


def calling(callee: str) -> Recipe:
    """A VI that calls ``callee`` and returns its output ``ref``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "ref", REF, "ref", 0)],
                data_flow=[wire(vi, "o.ref", "ref")],
            ),
            [call(vi, "o", callee, "iUse", [], [("ref", 0, REF)])],
        )

    return build


def test_vis_that_import_each_other_import_inside_their_functions(
    tmp_path: Path,
) -> None:
    """``Caller`` calls ``Opener``, which opens a reference to ``Caller``: the two
    modules import each other inside their functions. ``Plain`` calls ``Opener``
    and is on no cycle, so its import stays at module level."""
    project = Project(tmp_path)
    opener_key = project.add_vi("Opener", opener)
    caller_key = project.add_vi("Caller", calling(opener_key))
    plain_key = project.add_vi("Plain", calling(opener_key))
    for key in (caller_key, plain_key):
        project.graph._dep_graph.add_edge(key, opener_key)  # noqa: SLF001
    assert project.graph.opened_vi_edges() == [(opener_key, caller_key)]
    package = project.generate()
    assert package.gaps == []
    assert "def opener() -> OpenerResult:\n    from .caller import caller\n" in (
        package.sources["opener"]
    )
    assert "def caller() -> CallerResult:\n    from .opener import opener\n" in (
        package.sources["caller"]
    )
    assert package.sources["plain"].startswith(
        "from __future__ import annotations\nfrom typing import Any, NamedTuple\n"
        "from .opener import opener\n"
    )
    with package.use() as module:
        caller = module("caller")
        assert caller.caller().ref is caller.caller
        assert module("plain").plain().ref is caller.caller

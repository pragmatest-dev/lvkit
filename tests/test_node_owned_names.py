"""The names of module-level code one node owns (a Formula Node's function, a
stored-state global) are the VI's function name and the node's uid: the same
wherever the project sits on disk."""

from __future__ import annotations

from pathlib import Path

from lvkit.codegen.nodes import first_call
from lvkit.graph.models import (
    FormulaNode,
    GraphNode,
    PrimitiveNode,
    VIContext,
    Wire,
)
from lvkit.models import LVType, LVTypeKind, Terminal
from tests.class_graphs import INT, Project, pane


def _formula_and_first_call(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """``out = a + 1`` in a Formula Node (uid 42); First Call? (uid 43) to
    ``first``."""
    formula = FormulaNode(
        id=f"{vi}::42",
        vi_path=vi,
        name="Formula Node",
        node_type="fBox",
        script="y = a + 1;",
        terminals=[
            Terminal(
                id=f"{vi}::42.a", index=0, direction="input", name="a", lv_type=INT
            ),
            Terminal(
                id=f"{vi}::42.y", index=1, direction="output", name="y", lv_type=INT
            ),
        ],
    )
    flag = PrimitiveNode(
        id=f"{vi}::43",
        vi_path=vi,
        name="First Call?",
        node_type="prim",
        prim_id=first_call.FIRST_CALL_PRIM_ID,
        terminals=[Terminal(id=f"{vi}::43.first", index=0, direction="output")],
    )
    boolean = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "a", INT, "a", 0)],
            outputs=[
                pane(vi, "output", "out", INT, "out", 1),
                pane(vi, "output", "first", boolean, "first", 2),
            ],
            data_flow=[
                Wire.from_terminals(f"{vi}:a", f"{vi}::42.a"),
                Wire.from_terminals(f"{vi}::42.y", f"{vi}:out"),
                Wire.from_terminals(f"{vi}::43.first", f"{vi}:first"),
            ],
        ),
        [formula, flag],
    )


def _generated(root: Path) -> str:
    root.mkdir(parents=True)
    project = Project(root)
    project.add_vi("Sub Folder/Step_Rate", _formula_and_first_call)
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        step_rate = module("step_rate")
        assert step_rate.step_rate(a=4).out == 5
        assert step_rate.step_rate(a=4).first is False
    return package.sources["step_rate"]


def test_names_hold_the_function_and_uid_not_the_project_folder(
    tmp_path: Path,
) -> None:
    """``Sub Folder/Step_Rate.vi``: the helper is ``_formula_step_rate_42`` and
    the flag ``_lv_first_call_step_rate_43``, and moving the project to another
    folder changes no line of the module."""
    here = _generated(tmp_path / "one" / "project")
    moved = _generated(tmp_path / "two" / "renamed")
    assert "def _formula_step_rate_42(" in here
    assert "_lv_first_call_step_rate_43 = True" in here
    assert str(tmp_path) not in here
    assert here == moved

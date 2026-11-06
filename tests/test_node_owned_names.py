"""The names of module-level code one node owns (a Formula Node's function, a
stored-state global) are the VI's function name and the node's uid: the same
wherever the project sits on disk."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lvkit.codegen.ast_utils import node_owned_name
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.errors import CodeGenError
from lvkit.codegen.nodes import first_call
from lvkit.codegen.nodes import formula as formula_node
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


def test_functions_that_differ_only_in_underscores_get_their_own_names() -> None:
    """The function name is used as it is: ``read`` and ``__read`` keep two
    globals for one uid."""
    assert node_owned_name("_lv_state", "read", "a.vi::7") == "_lv_state_read_7"
    assert node_owned_name("_lv_state", "__read", "b.vi::7") == "_lv_state___read_7"
    assert node_owned_name("_lv_state", "get__value", "c.vi::7.x y") == (
        "_lv_state_get__value_7_x_y"
    )


def test_one_name_given_two_different_globals_is_an_lvkit_error() -> None:
    ctx = CodeGenContext()
    ctx.add_module_global("_lv_state_read_7", ast.Constant(value=0))
    ctx.add_module_global("_lv_state_read_7", ast.Constant(value=0))
    with pytest.raises(CodeGenError, match="one name"):
        ctx.add_module_global("_lv_state_read_7", ast.Constant(value=1))


def test_one_name_given_two_different_functions_is_an_lvkit_error() -> None:
    ctx = CodeGenContext()
    (one,) = ast.parse("def _helper():\n    return 1").body
    (two,) = ast.parse("def _helper():\n    return 2").body
    assert isinstance(one, ast.FunctionDef) and isinstance(two, ast.FunctionDef)
    ctx.add_module_function(one)
    ctx.add_module_function(one)
    with pytest.raises(CodeGenError, match="one name"):
        ctx.add_module_function(two)


def test_two_owners_of_one_name_is_an_lvkit_error_even_with_equal_values() -> None:
    """Two shift registers that both start at 0 read as one definition, but they
    are two pieces of state."""
    ctx = CodeGenContext()
    ctx.add_module_global("_lv_state_f_7", ast.Constant(value=0), owner="a.vi::7")
    with pytest.raises(CodeGenError, match="Two nodes were given one module-level"):
        ctx.add_module_global("_lv_state_f_7", ast.Constant(value=0), owner="b.vi::7")


def test_the_message_names_the_uids_not_the_vi_path() -> None:
    ctx = CodeGenContext()
    ctx.add_module_global("_g", ast.Constant(value=0), owner="/home/u/a.vi::7")
    with pytest.raises(CodeGenError) as raised:
        ctx.add_module_global("_g", ast.Constant(value=0), owner="/home/u/b.vi::9")
    assert "uid 7" in str(raised.value) and "uid 9" in str(raised.value)
    assert "/home" not in str(raised.value)


def test_one_owner_registering_a_name_twice_is_one_definition() -> None:
    ctx = CodeGenContext()
    ctx.add_module_global("_g", ast.Constant(value=0), owner="a.vi::7")
    ctx.add_module_global("_g", ast.Constant(value=0), owner="a.vi::7")
    assert list(ctx.module_globals) == ["_g"]


def test_a_shared_name_needs_one_definition() -> None:
    ctx = CodeGenContext()
    ctx.add_module_global("_g", ast.Constant(value=0))
    ctx.add_module_global("_g", ast.Constant(value=0))
    with pytest.raises(CodeGenError, match="one name"):
        ctx.add_module_global("_g", ast.Constant(value=1))


def test_a_shared_name_and_an_owned_name_collide() -> None:
    ctx = CodeGenContext()
    ctx.add_module_global("_g", ast.Constant(value=0))
    with pytest.raises(CodeGenError, match="Two nodes were given one module-level"):
        ctx.add_module_global("_g", ast.Constant(value=0), owner="a.vi::7")


def test_two_formula_nodes_with_one_function_name_are_an_lvkit_error() -> None:
    """Two Formula Nodes of one function, forced to one uid, would shadow each
    other's helper."""
    ctx = CodeGenContext()
    ctx.function_name = "f"
    ctx.bind("a.vi::7.a", "x")
    ctx.bind("b.vi::7.a", "x")
    for vi in ("a.vi", "b.vi"):
        formula = FormulaNode(
            id=f"{vi}::7",
            vi_path=vi,
            name="Formula Node",
            node_type="fBox",
            script="y = a + 1;",
            terminals=[
                Terminal(
                    id=f"{vi}::7.a", index=0, direction="input", name="a", lv_type=INT
                ),
                Terminal(
                    id=f"{vi}::7.y", index=1, direction="output", name="y", lv_type=INT
                ),
            ],
        )
        if vi == "a.vi":
            formula_node.generate(formula, ctx)
        else:
            with pytest.raises(CodeGenError, match="Two nodes were given one"):
                formula_node.generate(formula, ctx)

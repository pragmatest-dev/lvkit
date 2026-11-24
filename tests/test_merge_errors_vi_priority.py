"""The vi.lib ``Merge Errors.vi`` call: its error in 1 (pane index 7) wins over
error in 2 (6), over error in 3 (5), then the errors of ``error array in`` (4)
in array order; an error beats a warning. The generated statement runs."""

from __future__ import annotations

import ast
from dataclasses import dataclass

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.error_flow import merge_errors
from lvkit.graph.models import VINode
from lvkit.models import LVType, LVTypeKind, Terminal
from tests.helpers import error_cluster_type


@dataclass
class _Err:
    code: int
    status: bool


def _node() -> VINode:
    error = error_cluster_type()
    array = LVType(kind=LVTypeKind.ARRAY, element_type=error)
    terminals = [Terminal(id="out", index=0, direction="output", lv_type=error)]
    terminals.append(Terminal(id="t4", index=4, direction="input", lv_type=array))
    terminals += [
        Terminal(id=f"t{i}", index=i, direction="input", lv_type=error)
        for i in (5, 6, 7)
    ]
    return VINode(
        id="m",
        vi_path="caller.vi",
        name="Merge Errors.vi",
        node_type="iUse",
        terminals=terminals,
    )


def _merged(wired: dict[str, str], values: dict[str, object]) -> int | None:
    ctx = CodeGenContext()
    ctx.wired_input = lambda terminal_id, node: wired.get(terminal_id)  # type: ignore[method-assign]
    fragment = merge_errors(_node(), ctx, vi_pane=True)
    module = ast.fix_missing_locations(ast.Module(fragment.statements, []))
    scope: dict[str, object] = dict(values)
    exec(compile(module, "<merge>", "exec"), scope)  # noqa: S102
    result = next(v for k, v in scope.items() if k.startswith("merged_error"))
    return None if result is None else result.code  # type: ignore[attr-defined]


_ALL = {
    "a": _Err(10, True),
    "e1": _Err(1, True),
    "e2": _Err(2, True),
    "e3": _Err(3, True),
    "w1": _Err(1, False),
    "w3": _Err(3, False),
}


@pytest.mark.parametrize(
    ("wired", "expected"),
    [
        ({"t7": "e1", "t6": "e2", "t5": "e3"}, 1),
        ({"t7": "w1", "t6": "e2", "t5": "e3"}, 2),
        ({"t7": "w1", "t5": "w3"}, 1),
        ({"t5": "e3", "t4": "[a, e2]"}, 3),
        ({"t4": "[w1, e2, e3]"}, 2),
        ({}, None),
    ],
)
def test_error_in_1_wins_then_the_array(wired: dict[str, str], expected: int) -> None:
    assert _merged(wired, _ALL) == expected

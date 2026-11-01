"""The held error model: parallel branches that carry an error wire.

An error wire that forks makes parallel branches: each runs even when another
fails, and the first error is raised where they merge. Python exceptions would
stop the first failing branch's caller at once, so each branch catches its
LabVIEWError and holds the first one:

```python
def my_vi(input_data):
    _held_error = None

    with concurrent.futures.ThreadPoolExecutor() as _executor:
        def _branch_0():
            return branch_0_operations()
        _f0 = _executor.submit(_branch_0)

        def _branch_1():
            return branch_1_operations()
        _f1 = _executor.submit(_branch_1)

    # Merge point -- wrap future.result() calls
    try:
        branch_0_result = _f0.result()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_0_result = None

    try:
        branch_1_result = _f1.result()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_1_result = None

    if _held_error:
        raise _held_error

    return merge_results(branch_0_result, branch_1_result)
```

The model is on for a VI that has a Merge Errors anywhere in its node tree; a VI
without one raises natural exceptions (see ``error_wires``). ``LabVIEWError`` is
the class of the package's generated ``errors.py``.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode

from .error_path import ErrorRole, classify_error_node
from .errors_module import LABVIEW_ERROR_NAME

if TYPE_CHECKING:
    from lvkit.codegen.context import CodeGenContext


def needs_error_handling(
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
) -> bool:
    """Whether a VI needs the held error model: True only if Merge Errors exists
    anywhere in the VI's node tree. Recurses into every structure via
    ``ctx.child_nodes`` (which returns all inner operation-kind nodes across
    every frame, and [] for a leaf), so one uniform walk covers loops, case and
    sequence frames, IPES, etc.
    """
    for op in operations:
        if classify_error_node(op) is ErrorRole.MERGE:
            return True
        children = ctx.child_nodes(op)
        if children and needs_error_handling(children, ctx):
            return True
    return False


def build_held_error_init() -> ast.stmt:
    """Build `_held_error = None` initialization statement."""
    return ast.Assign(
        targets=[ast.Name(id="_held_error", ctx=ast.Store())],
        value=ast.Constant(value=None),
    )


def build_held_error_check() -> ast.stmt:
    """Build `if _held_error: raise _held_error` statement.

    This is placed at merge points to propagate the first held error.
    """
    return ast.If(
        test=ast.Name(id="_held_error", ctx=ast.Load()),
        body=[
            ast.Raise(
                exc=ast.Name(id="_held_error", ctx=ast.Load()),
                cause=None,
            )
        ],
        orelse=[],
    )


def build_merge_try_except(
    result_stmt: ast.stmt,
    defaults: dict[str, ast.expr] | None = None,
) -> ast.stmt:
    """Build try/except for a future.result() call at a merge point.

    Wraps:
        result = _fN.result()

    Into:
        try:
            result = _fN.result()
        except LabVIEWError as e:
            _held_error = _held_error or e
            result = <the default of its type>

    Args:
        result_stmt: The assignment statement (result = _fN.result())
        defaults: Each variable the statement assigns, with the value a failed
            node outputs: the default of its type
    """
    handler_body: list[ast.stmt] = [
        # _held_error = _held_error or e
        ast.Assign(
            targets=[ast.Name(id="_held_error", ctx=ast.Store())],
            value=ast.BoolOp(
                op=ast.Or(),
                values=[
                    ast.Name(id="_held_error", ctx=ast.Load()),
                    ast.Name(id="e", ctx=ast.Load()),
                ],
            ),
        ),
    ]

    for name, default in (defaults or {}).items():
        handler_body.append(
            ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=default)
        )

    return ast.Try(
        body=[result_stmt],
        handlers=[
            ast.ExceptHandler(
                type=ast.Name(id=LABVIEW_ERROR_NAME, ctx=ast.Load()),
                name="e",
                body=handler_body,
            )
        ],
        orelse=[],
        finalbody=[],
    )

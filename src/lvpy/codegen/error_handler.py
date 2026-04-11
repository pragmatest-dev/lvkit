"""Error handling code generation for LabVIEW error semantics.

This module generates Python code that translates LabVIEW's error cluster
propagation into Python exceptions. The mapping is graph-driven:

- Error passthrough: natural exception propagation (no code needed)
- Clear Errors: try/except that swallows LabVIEWError
- Merge Errors: try/except on future.result() that holds first error
- Error case structure: try/except with handler body

Generated Pattern (Merge Errors at parallel branch merge point):
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

    # Merge point — wrap future.result() calls
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
"""

from __future__ import annotations

import ast
from enum import Enum, auto
from typing import TYPE_CHECKING

from lvpy.graph.models import VIContext
from lvpy.models import (
    CaseOperation,
    Operation,
    PrimitiveOperation,
    SequenceOperation,
    _is_error_cluster,
)

if TYPE_CHECKING:
    from lvpy.codegen.context import CodeGenContext


class ErrorHandlingPattern(Enum):
    """How a node handles an error wire."""

    NONE = auto()
    MERGE = auto()
    CLEAR = auto()
    CASE_HANDLE = auto()


def classify_error_node(op: Operation) -> ErrorHandlingPattern:
    """Classify an operation by its error handling role.

    Checks the operation itself — not its downstream connections.
    This is a structural check based on node identity.
    """
    # Merge Errors primitive (prim 2401)
    if isinstance(op, PrimitiveOperation) and op.primResID == 2401:
        return ErrorHandlingPattern.MERGE

    # Clear Errors VI
    if "SubVI" in op.labels and op.name and "Clear Errors" in op.name:
        return ErrorHandlingPattern.CLEAR

    # Error case structure: selector terminal carries error cluster type
    if isinstance(op, CaseOperation) and op.selector_terminal:
        for term in op.terminals:
            if term.id == op.selector_terminal and term.lv_type:
                if _is_error_cluster(term.lv_type):
                    return ErrorHandlingPattern.CASE_HANDLE

    return ErrorHandlingPattern.NONE


def needs_error_handling(
    operations: list[Operation],
    vi_context: VIContext | None = None,
) -> bool:
    """Determine if a VI needs _held_error infrastructure.

    Graph-driven: True only if Merge Errors (prim 2401) exists
    in the VI's operations. Clear Errors and error case structures
    don't need _held_error — they use different patterns.
    """
    for op in operations:
        if classify_error_node(op) == ErrorHandlingPattern.MERGE:
            return True
        # Recurse into structures (case frames, sequence frames)
        if isinstance(op, CaseOperation | SequenceOperation):
            for frame in op.frames:
                if needs_error_handling(frame.operations):
                    return True
        if needs_error_handling(op.inner_nodes):
            return True
    return False


def find_error_path_ops(
    clear_op: Operation,
    all_ops: list[Operation],
    ctx: CodeGenContext,
) -> set[str]:
    """Find all operation IDs upstream of a Clear Errors on the error wire.

    Traces backward from Clear Errors' error input terminal through
    error-typed terminals only, collecting the set of operations that
    are on the error wire being cleared.

    Returns empty set if no graph is available (caller should fall back
    to wrapping all accumulated statements).
    """
    # Build output terminal → operation ID mapping
    output_to_op: dict[str, str] = {}
    op_by_id: dict[str, Operation] = {}
    for op in all_ops:
        op_by_id[op.id] = op
        for term in op.terminals:
            if term.direction == "output":
                output_to_op[term.id] = op.id

    # Find Clear Errors' error input terminal
    error_input_ids: list[str] = []
    for term in clear_op.terminals:
        if term.direction == "input" and term.is_error_cluster:
            error_input_ids.append(term.id)

    if not error_input_ids:
        return set()

    # BFS backward through error-typed terminals
    upstream: set[str] = set()
    queue = list(error_input_ids)
    visited: set[str] = set()

    while queue:
        tid = queue.pop()
        if tid in visited:
            continue
        visited.add(tid)

        source = ctx.get_source(tid)
        if not source:
            continue

        src_op_id = output_to_op.get(source.src_terminal)
        if not src_op_id or src_op_id in upstream:
            # Source is a VI input or already visited
            continue

        upstream.add(src_op_id)

        # Continue tracing through this op's error input terminals
        src_op = op_by_id.get(src_op_id)
        if src_op:
            for term in src_op.terminals:
                if term.direction == "input" and term.is_error_cluster:
                    queue.append(term.id)

    return upstream


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
    result_var: str | None = None,
) -> ast.stmt:
    """Build try/except for a future.result() call at a merge point.

    Wraps:
        result = _fN.result()

    Into:
        try:
            result = _fN.result()
        except LabVIEWError as e:
            _held_error = _held_error or e
            result = None

    Args:
        result_stmt: The assignment statement (result = _fN.result())
        result_var: Variable name to set to None on exception
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

    if result_var:
        handler_body.append(
            ast.Assign(
                targets=[ast.Name(id=result_var, ctx=ast.Store())],
                value=ast.Constant(value=None),
            )
        )

    return ast.Try(
        body=[result_stmt],
        handlers=[
            ast.ExceptHandler(
                type=ast.Name(id="LabVIEWError", ctx=ast.Load()),
                name="e",
                body=handler_body,
            )
        ],
        orelse=[],
        finalbody=[],
    )


def build_clear_try_except(body: list[ast.stmt]) -> ast.stmt:
    """Build try/except that swallows LabVIEWError (Clear Errors pattern).

    Generates:
        try:
            <body>
        except LabVIEWError:
            pass
    """
    if not body:
        body = [ast.Pass()]

    return ast.Try(
        body=body,
        handlers=[
            ast.ExceptHandler(
                type=ast.Name(id="LabVIEWError", ctx=ast.Load()),
                name=None,
                body=[ast.Pass()],
            )
        ],
        orelse=[],
        finalbody=[],
    )


def build_labview_error_import() -> ast.stmt:
    """Build import statement for LabVIEWError."""
    return ast.ImportFrom(
        module="lvpy.labview_error",
        names=[ast.alias(name="LabVIEWError", asname=None)],
        level=0,
    )

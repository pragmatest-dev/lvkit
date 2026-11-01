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
from lvkit.models import LVType

from .ast_utils import default_value_expr
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
    *,
    hold: bool = True,
    reraise: bool = False,
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
        hold: Whether the error is held for the merge
        reraise: Whether the error is raised again after the defaults are set,
            for a handler around the branch to catch
    """
    handler_body: list[ast.stmt] = []
    if hold:
        # _held_error = _held_error or e
        handler_body.append(
            ast.Assign(
                targets=[ast.Name(id="_held_error", ctx=ast.Store())],
                value=ast.BoolOp(
                    op=ast.Or(),
                    values=[
                        ast.Name(id="_held_error", ctx=ast.Load()),
                        ast.Name(id="e", ctx=ast.Load()),
                    ],
                ),
            )
        )

    for name, default in (defaults or {}).items():
        handler_body.append(
            ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=default)
        )

    if reraise:
        handler_body.append(ast.Raise(exc=None, cause=None))

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


def failed_output_defaults(
    bindings: dict[str, str], bound_vars: list[str], ctx: CodeGenContext
) -> dict[str, ast.expr]:
    """What each variable of ``bound_vars`` holds when the nodes that assign it
    fail: the default of its output terminal's type, as a failed node outputs.
    ``bindings`` maps each output terminal to the variable that holds it. A
    variable that holds one terminal's value takes that type's default; one that
    holds a call's several outputs (``result.field`` bindings) is a namespace of
    each field's default; any other has no type and is None."""
    whole: dict[str, LVType | None] = {}
    fields: dict[str, dict[str, LVType | None]] = {}
    if ctx.graph is not None:
        for terminal_id, name in bindings.items():
            terminal = ctx.graph.get_terminal(terminal_id)
            if terminal is None:
                continue
            root, _, field = name.partition(".")
            if root not in bound_vars:
                continue
            if not field:
                whole.setdefault(root, terminal.lv_type)
            elif field.isidentifier():
                fields.setdefault(root, {}).setdefault(field, terminal.lv_type)
    defaults: dict[str, ast.expr] = {}
    for name in bound_vars:
        if name not in whole and name in fields:
            ctx.add_import("from types import SimpleNamespace")
            defaults[name] = ast.Call(
                func=ast.Name(id="SimpleNamespace", ctx=ast.Load()),
                args=[],
                keywords=[
                    ast.keyword(
                        arg=field,
                        value=default_value_expr(lv_type, fresh_array=True),
                    )
                    for field, lv_type in fields[name].items()
                ],
            )
        else:
            defaults[name] = default_value_expr(whole.get(name), fresh_array=True)
    return defaults


class _AssignedNameVisitor(ast.NodeVisitor):
    """Collects Name targets of Assign/AnnAssign (each name of an unpacking
    target too) anywhere in a statement tree, including inside nested blocks
    (while/for/if/with/try) -- e.g. a shift-register update assigned inside a
    while loop's body. Does NOT
    descend into nested function/lambda defs, which introduce their own
    scope (a name assigned there is not a name of the enclosing branch)."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def _add_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.names.add(target.id)
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._add_target(element)
        elif isinstance(target, ast.Starred):
            self._add_target(target.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._add_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.names.add(node.target.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # New scope -- do not descend.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass  # New scope -- do not descend.

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass  # New scope -- do not descend.


def assigned_names(stmts: list[ast.stmt]) -> set[str]:
    """Extract variable names assigned anywhere in a list of statements.

    Walks into nested blocks (while/for/if/with/try) since a value bound
    by a node's CodeFragment may be assigned inside a loop body it
    generated (e.g. a shift-register update) rather than as a top-level
    statement of the fragment.
    """
    visitor = _AssignedNameVisitor()
    for stmt in stmts:
        visitor.visit(stmt)
    return visitor.names

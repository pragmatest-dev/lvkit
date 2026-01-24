"""Error handling code generation for LabVIEW parallel branches.

This module generates Python code that handles LabVIEW's error propagation
semantics for parallel branches:

1. Each parallel branch is wrapped in a try/except
2. Errors are "held" rather than immediately raised
3. At merge points, the first held error is raised

Generated Pattern:
```python
def my_vi(input_data):
    _held_error = None  # Track errors from branches

    # Parallel branch 0
    try:
        branch_0_result = branch_0_operations()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_0_result = None

    # Parallel branch 1
    try:
        branch_1_result = branch_1_operations()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_1_result = None

    # Merge point - raise first error
    if _held_error:
        raise _held_error

    return merge_results(branch_0_result, branch_1_result)
```
"""

from __future__ import annotations

import ast


def build_held_error_init() -> ast.stmt:
    """Build `_held_error = None` initialization statement."""
    return ast.Assign(
        targets=[ast.Name(id="_held_error", ctx=ast.Store())],
        value=ast.Constant(value=None),
    )


def build_held_error_check() -> ast.stmt:
    """Build `if _held_error: raise _held_error` statement.

    This is placed at the end of a function or at merge points
    to propagate the first held error.
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


def build_clear_error() -> ast.stmt:
    """Build `_held_error = None` to clear any held error.

    This corresponds to LabVIEW's "Clear Errors" primitive.
    """
    return ast.Assign(
        targets=[ast.Name(id="_held_error", ctx=ast.Store())],
        value=ast.Constant(value=None),
    )


def build_branch_try_except(
    branch_body: list[ast.stmt],
    result_var: str | None = None,
) -> ast.stmt:
    """Build try/except wrapper for a parallel branch.

    Args:
        branch_body: Statements to execute in the branch
        result_var: If provided, set to None on exception

    Returns:
        ast.Try statement wrapping the branch
    """
    # Exception handler body
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

    # Set result to None if we have a result variable
    if result_var:
        handler_body.append(
            ast.Assign(
                targets=[ast.Name(id=result_var, ctx=ast.Store())],
                value=ast.Constant(value=None),
            )
        )

    return ast.Try(
        body=branch_body,
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


def build_branch_function(
    branch_id: int,
    branch_body: list[ast.stmt],
    return_value: ast.expr | None = None,
) -> ast.FunctionDef:
    """Build a nested function for a parallel branch.

    Args:
        branch_id: Index of this branch (0, 1, 2, ...)
        branch_body: Statements for the branch body
        return_value: Optional expression to return

    Returns:
        FunctionDef AST node for the nested function
    """
    func_name = f"_branch_{branch_id}"
    body = list(branch_body)

    # Add return statement if we have a return value
    if return_value:
        body.append(ast.Return(value=return_value))

    # Ensure non-empty body
    if not body:
        body = [ast.Pass()]

    return ast.FunctionDef(
        name=func_name,
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=body,
        decorator_list=[],
        returns=None,
    )


def build_branch_execution(
    branch_funcs: list[str],
    result_vars: list[str],
) -> list[ast.stmt]:
    """Build code to execute branch functions with exception isolation.

    Generates:
    ```python
    # Execute with exception isolation
    results, errors = {}, {}
    for name, fn in [('0', _branch_0), ('1', _branch_1)]:
        try:
            results[name] = fn()
        except LabVIEWError as e:
            errors[name] = e

    # Merge errors (first error wins)
    if errors:
        raise list(errors.values())[0]
    ```

    Args:
        branch_funcs: Names of the branch functions
        result_vars: Names for storing each branch's result

    Returns:
        List of statements for branch execution
    """
    statements: list[ast.stmt] = []

    # Execute each branch with try/except
    for i, (func_name, result_var) in enumerate(zip(branch_funcs, result_vars)):
        # Build: result_var = func_name()
        call = ast.Call(
            func=ast.Name(id=func_name, ctx=ast.Load()),
            args=[],
            keywords=[],
        )
        assign = ast.Assign(
            targets=[ast.Name(id=result_var, ctx=ast.Store())],
            value=call,
        )

        # Wrap in try/except
        try_stmt = build_branch_try_except([assign], result_var)
        statements.append(try_stmt)

    # Add held error check after all branches
    statements.append(build_held_error_check())

    return statements


def needs_error_handling(has_parallel_branches: bool, vi_context: dict | None = None) -> bool:
    """Determine if a VI needs error handling infrastructure.

    The held error model is only needed when:
    1. There are parallel branches that need error coordination, AND
    2. The VI actually has error cluster terminals (input or output)

    Without error terminals, Python's natural exception propagation is sufficient.

    Args:
        has_parallel_branches: True if VI has parallel branches
        vi_context: Optional VI context with inputs/outputs to check for error terminals

    Returns:
        True if held error model should be enabled
    """
    if not has_parallel_branches:
        return False

    # Check if VI has any error cluster terminals
    if vi_context:
        if _has_error_terminals(vi_context):
            return True
        # No error terminals - just let exceptions propagate
        return False

    # No context to check - be conservative and enable if parallel branches
    return True


def _has_error_terminals(vi_context: dict) -> bool:
    """Check if VI has any error cluster terminals."""
    from vipy.type_defaults import _is_error_cluster

    # Check inputs
    for inp in vi_context.get("inputs", []):
        if inp.lv_type and _is_error_cluster(inp.lv_type):
            return True

    # Check outputs
    for out in vi_context.get("outputs", []):
        if out.lv_type and _is_error_cluster(out.lv_type):
            return True

    return False


def build_labview_error_import() -> ast.stmt:
    """Build import statement for LabVIEWError."""
    return ast.ImportFrom(
        module="vipy.labview_error",
        names=[ast.alias(name="LabVIEWError", asname=None)],
        level=0,
    )

"""AST optimization passes for code cleanup.

Provides post-generation cleanup of generated Python AST:
- Dead code elimination (unused variable assignments)
- Duplicate import removal
"""

from __future__ import annotations

import ast

from .gaps import is_gap_raise


class DeadCodeEliminator(ast.NodeTransformer):
    """Remove unused variable assignments from AST.

    Identifies assignments where the target variable is never referenced
    (never used in a Load context) and removes them from the AST.

    This is particularly useful for cleaning up output bindings from inlined
    SubVIs that produce outputs which aren't consumed downstream.

    Example:
        # Before:
        result = some_function()
        unused_var = result.field  # Dead - never referenced
        used_var = result.other
        return used_var

        # After:
        result = some_function()
        used_var = result.other
        return used_var
    """

    def __init__(self) -> None:
        self.assigned_vars: set[str] = set()
        self.loaded_vars: set[str] = set()

    def optimize(self, module: ast.Module) -> ast.Module:
        """Run dead code elimination on module.

        Args:
            module: The module AST to optimize

        Returns:
            Optimized module AST with unused assignments removed
        """
        # First pass: collect all assigned and loaded variables
        self._collect_usage(module)

        # Identify dead variables (assigned but never loaded)
        dead_vars = self.assigned_vars - self.loaded_vars

        # Second pass: remove dead assignments
        if dead_vars:
            self.dead_vars = dead_vars
            return self.visit(module)

        return module

    def _collect_usage(self, node: ast.AST) -> None:
        """Collect all variable assignments and loads."""
        for child in ast.walk(node):
            # Track assignments (Store context)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                self.assigned_vars.add(child.id)

            # Track variable references (Load context)
            elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                self.loaded_vars.add(child.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        """Visit function definition and filter out dead assignments."""
        # Process function body, removing dead assignments
        new_body = []
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and self._is_dead_assignment(stmt):
                # Dead assignment — but preserve side effects (function calls)
                if self._has_side_effects(stmt.value):
                    new_body.append(ast.Expr(value=stmt.value))
                continue

            # Keep and recursively visit
            new_stmt = self.visit(stmt)
            new_body.append(new_stmt)

        if not new_body:
            # Function must have at least one statement
            new_body = [ast.Pass()]

        node.body = new_body  # type: ignore[misc]  # ast node attrs are mutable at runtime
        return node

    def _is_dead_assignment(self, stmt: ast.stmt) -> bool:
        """Check if statement is an assignment to a dead variable.

        Args:
            stmt: AST statement to check

        Returns:
            True if this is an assignment to a variable that's never used
        """
        if not isinstance(stmt, ast.Assign):
            return False

        # Only handle simple assignments (single target)
        if len(stmt.targets) != 1:
            return False

        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            return False

        # Check if this variable is in our dead set
        return target.id in self.dead_vars

    def _has_side_effects(self, node: ast.expr) -> bool:
        """Check if expression might have side effects (function calls, etc.)."""
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                return True
        return False


ImportStmt = ast.Import | ast.ImportFrom


def _binding(stmt: ImportStmt, alias: ast.alias) -> tuple[str, int, str, str]:
    """What one imported name binds: (source module, level, name, bound as).
    Two imports with the same binding are the same import."""
    if isinstance(stmt, ast.ImportFrom):
        source, level = stmt.module or "", stmt.level
    else:
        source, level = "", 0
    return (source, level, alias.name, alias.asname or alias.name)


class DuplicateImportRemover(ast.NodeTransformer):
    """Remove duplicate top-level import statements from AST.

    Keeps only the first occurrence of each import (by `_binding`).

    Example:
        # Before:
        from pathlib import Path
        from .module import func
        from pathlib import Path  # Duplicate

        # After:
        from pathlib import Path
        from .module import func
    """

    def optimize(self, module: ast.Module) -> ast.Module:
        """Remove duplicate imports from module.

        Args:
            module: The module AST to optimize

        Returns:
            Module with duplicate imports removed
        """
        seen: set[tuple[str, int, str, str]] = set()
        new_body: list[ast.stmt] = []
        for stmt in module.body:
            if isinstance(stmt, ast.Import | ast.ImportFrom):
                new_names = []
                for alias in stmt.names:
                    key = _binding(stmt, alias)
                    if key not in seen:
                        seen.add(key)
                        new_names.append(alias)
                if new_names:
                    stmt.names = new_names
                    new_body.append(stmt)
            else:
                new_body.append(stmt)
        module.body = new_body
        return module


def remove_duplicate_imports(module: ast.Module) -> ast.Module:
    """Remove duplicate imports from module AST.

    Args:
        module: Module AST to optimize

    Returns:
        Module with duplicate imports removed
    """
    remover = DuplicateImportRemover()
    return remover.optimize(module)


class UnusedImportRemover(ast.NodeTransformer):
    """Remove unused imports from AST.

    Identifies imports where the imported name is never used in the code
    and removes them. A module-level import is judged by the names the whole
    module reads; an import at the top of a function or method body is judged
    by the names that function's body reads, and goes when the module already
    makes the same import at run time. A function import of a name the function
    also rebinds (assigns, deletes, or declares `global`/`nonlocal`) is kept.
    `from __future__` imports are always kept.

    Example:
        # Before:
        from pathlib import Path
        from .module import unused_func
        def foo():
            return Path(".")

        # After:
        from pathlib import Path
        def foo():
            return Path(".")
    """

    def optimize(self, module: ast.Module) -> ast.Module:
        """Remove unused imports from module.

        Args:
            module: The module AST to optimize

        Returns:
            Module with unused imports removed
        """
        module.body = _keep_used(module.body, _used_names([module]), set(), set())
        # What the module's own top-level imports bind at run time (an import
        # under `if TYPE_CHECKING:` is not top level, so it binds nothing).
        bound = {
            _binding(stmt, alias)
            for stmt in module.body
            if isinstance(stmt, ast.Import | ast.ImportFrom)
            for alias in stmt.names
        }
        for node in ast.walk(module):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                node.body = _keep_used(
                    node.body,
                    _used_names(node.body),
                    bound,
                    _rebound_names(node.body),
                ) or [ast.Pass()]
        return module


def remove_unused_imports(module: ast.Module) -> ast.Module:
    """Remove unused imports from module AST.

    Args:
        module: Module AST to optimize

    Returns:
        Module with unused imports removed
    """
    remover = UnusedImportRemover()
    return remover.optimize(module)


def _keep_used(
    body: list[ast.stmt],
    used: set[str],
    bound: set[tuple[str, int, str, str]],
    rebound: set[str],
) -> list[ast.stmt]:
    """`body` with each import kept only for a name that is rebound, or that is
    read (`used`) and not already imported the same way (`bound`)."""
    new_body: list[ast.stmt] = []
    for stmt in body:
        if not isinstance(stmt, ast.Import | ast.ImportFrom):
            new_body.append(stmt)
            continue
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            new_body.append(stmt)
            continue
        names = [
            alias
            for alias in stmt.names
            if _bound_name(alias) in rebound
            or (_binding(stmt, alias) not in bound and _is_read(stmt, alias, used))
        ]
        if names:
            stmt.names = names
            new_body.append(stmt)
    return new_body


def _bound_name(alias: ast.alias) -> str:
    """The name an import binds (`import a.b` binds `a`)."""
    return alias.asname or alias.name.split(".")[0]


def _is_read(stmt: ImportStmt, alias: ast.alias, used: set[str]) -> bool:
    """Whether the name `alias` binds is among the names `used`; for a dotted
    `import concurrent.futures`, its top-level name."""
    name = alias.asname or alias.name
    if isinstance(stmt, ast.Import):
        return name in used or name.split(".")[0] in used
    return name in used


def _used_names(nodes: list[ast.stmt] | list[ast.Module]) -> set[str]:
    """Every name `nodes` read (Load context), imports and the annotations of
    annotated assignments in a function body excluded (those are never
    evaluated)."""
    skipped: set[int] = set()
    for root in nodes:
        if isinstance(root, ast.Module):
            continue
        for child in ast.walk(root):
            if isinstance(child, ast.AnnAssign):
                skipped.update(id(n) for n in ast.walk(child.annotation))
    used: set[str] = set()
    for root in nodes:
        for child in ast.walk(root):
            if id(child) in skipped:
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                used.add(child.id)
                # Dotted names like "Enum.MEMBER" in keyword args: the prefix is
                # read
                if "." in child.id:
                    used.add(child.id.split(".")[0])
            elif isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load):
                value = child.value
                while isinstance(value, ast.Attribute):
                    value = value.value
                if isinstance(value, ast.Name):
                    used.add(value.id)
    return used


def _rebound_names(body: list[ast.stmt]) -> set[str]:
    """The names `body` assigns, deletes, or declares `global`/`nonlocal`."""
    rebound: set[str] = set()
    for root in body:
        for child in ast.walk(root):
            if isinstance(child, ast.Global | ast.Nonlocal):
                rebound.update(child.names)
            elif isinstance(child, ast.Name) and isinstance(
                child.ctx, ast.Store | ast.Del
            ):
                rebound.add(child.id)
    return rebound


def eliminate_dead_code(module: ast.Module) -> ast.Module:
    """Eliminate dead code from module AST.

    Args:
        module: Module AST to optimize

    Returns:
        Optimized module with unused assignments removed
    """
    eliminator = DeadCodeEliminator()
    return eliminator.optimize(module)


class ConstantFolder(ast.NodeTransformer):
    """Fold constant expressions at compile time.

    Evaluates BoolOp (and/or) and BinOp with all-constant operands.
    E.g., `True or False` → `True`.
    """

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        const_values = [v for v in node.values if isinstance(v, ast.Constant)]
        if len(const_values) == len(node.values):
            result = const_values[0].value
            for v in const_values[1:]:
                if isinstance(node.op, ast.Or):
                    result = result or v.value
                elif isinstance(node.op, ast.And):
                    result = result and v.value
            return ast.Constant(value=result)
        return node


class DeadBranchEliminator(ast.NodeTransformer):
    """Remove branches with constant conditions and code after raise.

    - `if True: body` → body (inline)
    - `if False: body` → removed
    - `if not True: body; else: alt` → alt (inline)
    - Code after `raise` in a block → removed
    """

    def visit_If(self, node: ast.If) -> ast.AST | list[ast.stmt] | None:
        self.generic_visit(node)
        test = node.test

        # if True: / if False:
        if isinstance(test, ast.Constant) and isinstance(test.value, bool):
            if test.value:
                return node.body  # inline the True branch
            else:
                return node.orelse or None  # inline else or remove

        # if not <const>:
        if (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Constant)
            and isinstance(test.operand.value, bool)
        ):
            if not test.operand.value:  # not False = True
                return node.body
            else:  # not True = False
                return node.orelse or None

        return node

    def _truncate_after_raise(self, stmts: list[ast.stmt]) -> list[ast.stmt]:
        """Remove statements after a raise in a block. The raise of a gap keeps what
        follows it: the developer who replaces the raise needs the rest."""
        result = []
        for stmt in stmts:
            result.append(stmt)
            if isinstance(stmt, ast.Raise) and not is_gap_raise(stmt):
                break
        return result

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        self.generic_visit(node)
        node.body = self._truncate_after_raise(node.body)
        if not node.body:
            node.body = [ast.Pass()]
        return node


def fold_constants(module: ast.Module) -> ast.Module:
    """Fold constant expressions in module AST."""
    return ConstantFolder().visit(module)


def eliminate_dead_branches(module: ast.Module) -> ast.Module:
    """Remove branches with constant conditions."""
    return DeadBranchEliminator().visit(module)


def optimize_module(module: ast.Module) -> ast.Module:
    """Run all optimization passes on module.

    Args:
        module: Module AST to optimize

    Returns:
        Fully optimized module
    """
    module = remove_duplicate_imports(module)
    module = fold_constants(module)
    module = eliminate_dead_branches(module)
    module = eliminate_dead_code(module)
    module = remove_unused_imports(module)
    return module

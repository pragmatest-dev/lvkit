"""AST optimization passes for code cleanup.

Provides post-generation cleanup of generated Python AST:
- Dead code elimination (unused variable assignments)
- Duplicate import removal
"""

from __future__ import annotations

import ast


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
            if self._is_dead_assignment(stmt):
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

        node.body = new_body
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


class DuplicateImportRemover(ast.NodeTransformer):
    """Remove duplicate import statements from AST.

    Tracks imported names and removes duplicate imports, keeping only the first
    occurrence of each import.

    Handles:
    - `import X` statements
    - `from X import Y` statements
    - `from X import Y as Z` statements

    Example:
        # Before:
        from pathlib import Path
        from .module import func
        from pathlib import Path  # Duplicate

        # After:
        from pathlib import Path
        from .module import func
    """

    def __init__(self) -> None:
        # Track (module, name, asname) tuples for ImportFrom
        self.seen_from_imports: set[tuple[str | None, str, str | None]] = set()
        # Track module names for plain imports
        self.seen_imports: set[tuple[str, str | None]] = set()

    def optimize(self, module: ast.Module) -> ast.Module:
        """Remove duplicate imports from module.

        Args:
            module: The module AST to optimize

        Returns:
            Module with duplicate imports removed
        """
        new_body = []
        for stmt in module.body:
            if isinstance(stmt, ast.ImportFrom):
                # Filter out already-seen names
                new_names = []
                for alias in stmt.names:
                    key = (stmt.module, alias.name, alias.asname)
                    if key not in self.seen_from_imports:
                        self.seen_from_imports.add(key)
                        new_names.append(alias)

                if new_names:
                    stmt.names = new_names
                    new_body.append(stmt)
                # else: entire import was duplicates, skip it

            elif isinstance(stmt, ast.Import):
                # Filter out already-seen modules
                new_names = []
                for alias in stmt.names:
                    key = (alias.name, alias.asname)
                    if key not in self.seen_imports:
                        self.seen_imports.add(key)
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
    and removes them.

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

    # Names that should never be removed (always needed for type hints)
    ALWAYS_KEEP = {"annotations", "Any", "NamedTuple"}

    def __init__(self) -> None:
        self.used_names: set[str] = set()

    def optimize(self, module: ast.Module) -> ast.Module:
        """Remove unused imports from module.

        Args:
            module: The module AST to optimize

        Returns:
            Module with unused imports removed
        """
        # First pass: collect all used names
        self._collect_used_names(module)

        # Second pass: filter imports
        new_body = []
        for stmt in module.body:
            if isinstance(stmt, ast.ImportFrom):
                # Keep only used names
                new_names = []
                for alias in stmt.names:
                    name = alias.asname or alias.name
                    if name in self.used_names or name in self.ALWAYS_KEEP:
                        new_names.append(alias)

                if new_names:
                    stmt.names = new_names
                    new_body.append(stmt)
                # else: all names unused, skip entire import

            elif isinstance(stmt, ast.Import):
                # Keep only used modules
                # For dotted imports like `import concurrent.futures`,
                # check if the top-level name (e.g. "concurrent") is used
                new_names = []
                for alias in stmt.names:
                    name = alias.asname or alias.name
                    top_level = name.split(".")[0]
                    if (
                        name in self.used_names
                        or top_level in self.used_names
                        or name in self.ALWAYS_KEEP
                    ):
                        new_names.append(alias)

                if new_names:
                    stmt.names = new_names
                    new_body.append(stmt)

            else:
                new_body.append(stmt)

        module.body = new_body
        return module

    def _collect_used_names(self, node: ast.AST) -> None:
        """Collect all names used in Load context (excluding imports)."""
        for child in ast.walk(node):
            # Skip imports themselves
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                continue

            # Track name references
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                self.used_names.add(child.id)

            # Track attribute access (e.g., module.func)
            elif isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load):
                # Get root name of attribute chain
                value = child.value
                while isinstance(value, ast.Attribute):
                    value = value.value
                if isinstance(value, ast.Name):
                    self.used_names.add(value.id)


def remove_unused_imports(module: ast.Module) -> ast.Module:
    """Remove unused imports from module AST.

    Args:
        module: Module AST to optimize

    Returns:
        Module with unused imports removed
    """
    remover = UnusedImportRemover()
    return remover.optimize(module)


def eliminate_dead_code(module: ast.Module) -> ast.Module:
    """Eliminate dead code from module AST.

    Args:
        module: Module AST to optimize

    Returns:
        Optimized module with unused assignments removed
    """
    eliminator = DeadCodeEliminator()
    return eliminator.optimize(module)


def optimize_module(module: ast.Module) -> ast.Module:
    """Run all optimization passes on module.

    Args:
        module: Module AST to optimize

    Returns:
        Fully optimized module
    """
    module = remove_duplicate_imports(module)
    module = eliminate_dead_code(module)
    module = remove_unused_imports(module)
    return module

"""AST optimization passes for code cleanup.

Provides post-generation cleanup of generated Python AST:
- Dead code elimination (unused variable assignments)
- Future passes can be added here
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
                # Skip this statement - it's dead code
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


def eliminate_dead_code(module: ast.Module) -> ast.Module:
    """Eliminate dead code from module AST.

    Args:
        module: Module AST to optimize

    Returns:
        Optimized module with unused assignments removed
    """
    eliminator = DeadCodeEliminator()
    return eliminator.optimize(module)

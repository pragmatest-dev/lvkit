"""AST optimization passes for code cleanup.

Provides post-generation cleanup of generated Python AST:
- Dead code elimination (unused variable assignments)
- Self-assignment removal (``x = x``)
- Dead default removal (``x = None`` right before ``x`` is given its value)
- Duplicate import removal
"""

from __future__ import annotations

import ast
import copy
from typing import cast

from .gaps import is_gap_raise


class DeadCodeEliminator(ast.NodeTransformer):
    """Remove unused variable assignments from AST.

    Identifies assignments where the target variable is never relied on
    (``_collect_usage``) and removes them from the AST.

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
        """Collect every assigned variable and every variable the code relies on.

        A variable is relied on where it is read (``x``), deleted (``del x``),
        updated in place (``x += 1`` reads ``x`` first) or named by a
        ``nonlocal``/``global`` statement (which needs the binding to exist in the
        enclosing scope). Reads in nested functions and lambdas are reads too:
        the walk covers the whole module.
        """
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                if isinstance(child.ctx, ast.Store):
                    self.assigned_vars.add(child.id)
                else:
                    self.loaded_vars.add(child.id)
            if isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name):
                self.loaded_vars.add(child.target.id)
            elif isinstance(child, ast.Nonlocal | ast.Global):
                self.loaded_vars.update(child.names)

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
                    _rebound_names(node.body) | _parameter_names(node.args),
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


def _parameter_names(args: ast.arguments) -> set[str]:
    """The names a function's own parameters bind."""
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    names.update(a.arg for a in (args.vararg, args.kwarg) if a is not None)
    return names


def _rebound_names(body: list[ast.stmt]) -> set[str]:
    """The names `body` binds other than by import (assigns, deletes, defines
    with `def`/`class`, catches with `except ... as`, captures in a `match`
    pattern) or declares `global`/`nonlocal`."""
    rebound: set[str] = set()
    for root in body:
        for child in ast.walk(root):
            if isinstance(child, ast.Global | ast.Nonlocal):
                rebound.update(child.names)
            elif isinstance(child, ast.Name) and isinstance(
                child.ctx, ast.Store | ast.Del
            ):
                rebound.add(child.id)
            elif isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                rebound.add(child.name)
            elif isinstance(child, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
                if child.name:
                    rebound.add(child.name)
            elif isinstance(child, ast.MatchMapping) and child.rest:
                rebound.add(child.rest)
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


class SelfAssignmentRemover(ast.NodeTransformer):
    """Remove ``x = x``: the variable already holds the value, so the statement
    does nothing. A block left empty is tidied: an ``if`` with nothing in either
    branch goes (its test is kept as a statement when it calls something), an
    ``if`` with only an ``else`` tests the opposite, and any other empty block
    holds ``pass``."""

    _BLOCKS = ("body", "orelse", "finalbody")

    def generic_visit(self, node: ast.AST) -> ast.AST:
        super().generic_visit(node)
        for field in self._BLOCKS:
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            kept: list[ast.stmt] = [s for s in block if not _is_self_assignment(s)]
            # A body needs a statement; an else or finally may be empty.
            if not kept and field == "body":
                kept = [ast.Pass()]
            setattr(node, field, kept)
        return node

    def visit_If(self, node: ast.If) -> ast.AST | list[ast.stmt] | None:
        self.generic_visit(node)
        body = _without_pass(node.body)
        orelse = _without_pass(node.orelse)
        if not body and not orelse:
            if any(isinstance(n, ast.Call) for n in ast.walk(node.test)):
                return ast.Expr(value=node.test)
            return None
        if not body:
            return ast.If(
                test=ast.UnaryOp(op=ast.Not(), operand=node.test),
                body=orelse,
                orelse=[],
            )
        node.body = body
        node.orelse = orelse
        return node


def _is_self_assignment(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and isinstance(stmt.value, ast.Name)
        and stmt.targets[0].id == stmt.value.id
    )


def _without_pass(block: list[ast.stmt]) -> list[ast.stmt]:
    return [s for s in block if not isinstance(s, ast.Pass)]


def remove_self_assignments(module: ast.Module) -> ast.Module:
    """Remove every ``x = x`` (``SelfAssignmentRemover``)."""
    return SelfAssignmentRemover().visit(module)


class DeadDefaultRemover(ast.NodeTransformer):
    """Remove the default a variable is given just before it is given its value.

    Each rule looks only at the statement right after ``x = <default>`` in the
    same block (a default is a constant, or an empty list or tuple):

    - the next statement assigns ``x`` without reading it: the default goes;
    - the next statement assigns ``x`` and reads it once: the default goes and
      takes the place of that read;
    - the next statement is an ``if``/``elif``/``else`` chain ending in ``else``,
      or a ``match`` ending in ``case _``, and every branch assigns ``x`` at its
      top level before anything reads it (a branch that raises first counts):
      the default goes. A chain with no ``else`` keeps it.

    A name that is global or nonlocal, read in a nested function, or inside a
    ``try`` is never touched: an exception or a later call could still read the
    default.
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        self._clean_function(node)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)
        self._clean_function(node)
        return node

    def _clean_function(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        protected: set[str] = set()
        for inner in ast.walk(func):
            if isinstance(inner, (ast.Global, ast.Nonlocal)):
                protected.update(inner.names)
            if inner is not func and isinstance(
                inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                protected.update(_names_read(inner))
        self._clean_blocks(func, protected, inside_try=False)

    def _clean_blocks(
        self, node: ast.AST, protected: set[str], inside_try: bool
    ) -> None:
        inside_try = inside_try or isinstance(node, ast.Try)
        for field in ("body", "orelse", "finalbody", "handlers", "cases"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for child in block:
                if isinstance(
                    child,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    continue
                self._clean_blocks(child, protected, inside_try)
            if not inside_try and block and isinstance(block[0], ast.stmt):
                _remove_dead_defaults(block, protected)


def _is_default(value: ast.expr) -> bool:
    return isinstance(value, ast.Constant) or (
        isinstance(value, (ast.List, ast.Tuple)) and not value.elts
    )


def _single_name_assignment(stmt: ast.stmt) -> str | None:
    """The name ``stmt`` assigns when it is ``name = value`` with one plain target."""
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        return stmt.targets[0].id
    return None


def _names_read(node: ast.AST) -> set[str]:
    return {
        n.id
        for n in ast.walk(node)
        if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Store)
    }


def _reads(node: ast.AST, name: str) -> int:
    """How many times ``node`` reads ``name``; ``name += …`` reads it too."""
    return sum(
        1
        for n in ast.walk(node)
        if (
            isinstance(n, ast.Name)
            and n.id == name
            and not isinstance(n.ctx, ast.Store)
        )
        or (
            isinstance(n, ast.AugAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == name
        )
    )


def _leaves_block(node: ast.AST) -> bool:
    """True when ``node`` holds a ``break`` or ``continue`` of the loop around
    the branch: the branch can end before a later statement of it runs. One
    inside a loop nested in ``node`` ends only that loop (its ``else`` block
    still belongs to the loop around)."""
    if isinstance(node, (ast.Break, ast.Continue)):
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return False
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        return any(_leaves_block(s) for s in node.orelse)
    return any(_leaves_block(child) for child in ast.iter_child_nodes(node))


def _mentions(node: ast.AST, name: str) -> bool:
    """True when ``node`` uses ``name`` in any way (read, write, or a match capture)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == name:
            return True
        if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name == name:
            return True
        if isinstance(n, ast.MatchMapping) and n.rest == name:
            return True
    return False


class _ReadReplacer(ast.NodeTransformer):
    def __init__(self, name: str, value: ast.expr) -> None:
        self.name = name
        self.value = value

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == self.name and isinstance(node.ctx, ast.Load):
            return copy.deepcopy(self.value)
        return node


def _read_in_a_deferred_scope(value: ast.expr, name: str) -> bool:
    """True when a read of ``name`` sits in a lambda or comprehension, where a
    default put in its place would be built again on every call or pass."""
    scopes = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    return any(isinstance(n, scopes) and _reads(n, name) for n in ast.walk(value))


def _assigns_before_reading(branch: list[ast.stmt], name: str) -> bool:
    """True when a top-level statement of ``branch`` assigns ``name`` before
    anything reads it, or the branch raises before anything reads it. A
    statement before the assignment that can ``break`` or ``continue`` keeps
    the default: on that path the name is not assigned."""
    for stmt in branch:
        if _single_name_assignment(stmt) == name:
            return _reads(cast(ast.Assign, stmt).value, name) == 0
        if isinstance(stmt, ast.Raise):
            return _reads(stmt, name) == 0
        if _reads(stmt, name) or _leaves_block(stmt):
            return False
    return False


def _if_chain_assigns(node: ast.If, name: str) -> bool:
    """True when the chain ends in ``else`` and every branch assigns ``name``
    first, with none of the tests reading it."""
    while True:
        if _reads(node.test, name) or not _assigns_before_reading(node.body, name):
            return False
        if not node.orelse:
            return False
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
            continue
        return _assigns_before_reading(node.orelse, name)


def _match_assigns(node: ast.Match, name: str) -> bool:
    last = node.cases[-1]
    if not (
        isinstance(last.pattern, ast.MatchAs)
        and last.pattern.pattern is None
        and last.pattern.name is None
        and last.guard is None
    ):
        return False
    if _reads(node.subject, name):
        return False
    for case in node.cases:
        if _mentions(case.pattern, name):
            return False
        if case.guard is not None and _reads(case.guard, name):
            return False
        if not _assigns_before_reading(case.body, name):
            return False
    return True


def _remove_dead_defaults(block: list[ast.stmt], protected: set[str]) -> None:
    i = 0
    while i < len(block) - 1:
        first, following = block[i], block[i + 1]
        name = _single_name_assignment(first)
        if name is None or name in protected:
            i += 1
            continue
        default = cast(ast.Assign, first).value
        if not _is_default(default):
            i += 1
            continue
        if _single_name_assignment(following) == name:
            value = cast(ast.Assign, following).value
            reads = _reads(value, name)
            if reads == 0:
                del block[i]
                continue
            if reads == 1 and not _read_in_a_deferred_scope(value, name):
                cast(ast.Assign, following).value = _ReadReplacer(name, default).visit(
                    value
                )
                del block[i]
                continue
        elif (isinstance(following, ast.If) and _if_chain_assigns(following, name)) or (
            isinstance(following, ast.Match) and _match_assigns(following, name)
        ):
            del block[i]
            continue
        i += 1


def remove_dead_defaults(module: ast.Module) -> ast.Module:
    """Remove every default that is overwritten at once (``DeadDefaultRemover``)."""
    return DeadDefaultRemover().visit(module)


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
    module = remove_self_assignments(module)
    module = remove_dead_defaults(module)
    module = eliminate_dead_code(module)
    module = remove_unused_imports(module)
    return module

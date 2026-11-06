"""Generated modules that import each other.

Each class and each VI of no class is one generated module (a class and its
member VIs are one). A module imports the modules of the VIs it calls, the
classes it uses, its class's parent, and the VI an Open VI Reference opens. When
modules import each other in a cycle, a module-level ``from .x import y`` fails,
so a module on a cycle imports the other generated modules inside the functions
that use them, when they run."""

from __future__ import annotations

import ast
import weakref

import networkx as nx

from lvkit.graph import InMemoryVIGraph

_CYCLIC: weakref.WeakKeyDictionary[InMemoryVIGraph, frozenset[str]] = (
    weakref.WeakKeyDictionary()
)


def in_import_cycle(graph: InMemoryVIGraph, module_key: str) -> bool:
    """Whether the module of the class or VI at ``module_key`` is in an import
    cycle with other generated modules (it calls, or opens a reference to, a VI
    that leads back to it)."""
    cyclic = _CYCLIC.get(graph)
    if cyclic is None:
        cyclic = _CYCLIC[graph] = _modules_in_import_cycles(graph)
    return module_key in cyclic


def _modules_in_import_cycles(graph: InMemoryVIGraph) -> frozenset[str]:
    """The modules that lie on a cycle of the imports between the modules: a
    class and its member VIs are one module, keyed by the class."""
    module_of: dict[str, str] = {}
    for key in graph.list_class_keys():
        module_of[key] = key
        for member in graph.class_member_keys(key):
            module_of[member] = key
    modules = nx.DiGraph()
    for caller, callee in [*graph.call_edges(), *graph.opened_vi_edges()]:
        a, b = module_of.get(caller, caller), module_of.get(callee, callee)
        if a != b:
            modules.add_edge(a, b)
    for key in sorted(set(module_of.values())):
        parent = graph.class_parent_key(key)
        if parent is not None:
            modules.add_edge(key, parent)
    cyclic: set[str] = set()
    for component in nx.strongly_connected_components(modules):
        if len(component) > 1:
            cyclic.update(component)
    return frozenset(cyclic)


def imports_inside(
    body: list[ast.stmt], imports: set[str]
) -> tuple[list[ast.stmt], set[str]]:
    """``body`` with the imports of other generated modules (the relative ones)
    among ``imports`` at its start, after the docstring if it has one, and the
    imports that stay at module level."""
    generated = {i for i in imports if i.startswith("from .")}
    local: list[ast.stmt] = []
    for statement in sorted(generated):
        local.extend(ast.parse(statement).body)
    return with_imports_after_docstring(local, body), imports - generated


def with_imports_after_docstring(
    imports: list[ast.stmt], body: list[ast.stmt]
) -> list[ast.stmt]:
    """``body`` with ``imports`` at its start, after the docstring if it has one."""
    first = body[0] if body else None
    has_docstring = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    return [*body[:1], *imports, *body[1:]] if has_docstring else [*imports, *body]

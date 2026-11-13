"""One module-level constant for each ``LVType`` a generated module writes more
than once.

A control's type is written wherever a module records it (each ``Panel``, each
wrap into a Variant, each Variant To Data check). A type the module writes twice
or more is written once after its imports, named from the type, and every place
reads the name; a type written once stays where it is. A constant's name ends in
``_TYPE``, so it has an underscore inside it, which no generated class name has
(``type_names.derive_python_name``).
"""

from __future__ import annotations

import ast

from .ast_utils import distinct_names, to_var_name
from .variants_module import LV_TYPE_NAME

_TYPE_SUFFIX = "_TYPE"
_FILE_EXTENSIONS = (".ctl", ".lvclass")


def _is_type_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == LV_TYPE_NAME
    )


def _constant_name(call: ast.Call) -> str:
    """The name a type's constant wants: its LabVIEW name (the second argument of
    the ``LVType``), without its library and file extension, upper case."""
    label = "type"
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        label = str(call.args[1].value).split("{", 1)[0].rsplit(":", 1)[-1]
        for extension in _FILE_EXTENSIONS:
            label = label.removesuffix(extension)
    return to_var_name(label).strip("_").upper() + _TYPE_SUFFIX


def _bound_names(module: ast.Module) -> set[str]:
    """Every name the module reads or binds anywhere."""
    names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def _after_imports(module: ast.Module) -> int:
    """The index after the module's last top-level import (and the
    ``TYPE_CHECKING`` block of annotation imports), else after its docstring."""
    index = 1 if ast.get_docstring(module) is not None else 0
    for position, stmt in enumerate(module.body):
        if isinstance(stmt, (ast.Import, ast.ImportFrom)) or (
            isinstance(stmt, ast.If)
            and isinstance(stmt.test, ast.Name)
            and stmt.test.id == "TYPE_CHECKING"
        ):
            index = position + 1
    return index


class _Replacer(ast.NodeTransformer):
    def __init__(self, names: dict[str, str]) -> None:
        self._names = names

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if _is_type_call(node):
            name = self._names.get(ast.dump(node))
            if name is not None:
                return ast.copy_location(ast.Name(id=name, ctx=ast.Load()), node)
        return self.generic_visit(node)


def share_type_constants(module: ast.Module) -> ast.Module:
    """``module`` with each ``LVType(...)`` it writes more than once defined once
    after its imports and read by name everywhere."""
    counts: dict[str, int] = {}
    first: dict[str, ast.Call] = {}
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and _is_type_call(node):
            key = ast.dump(node)
            counts[key] = counts.get(key, 0) + 1
            first.setdefault(key, node)
    repeated = [key for key, count in counts.items() if count > 1]
    if not repeated:
        return module
    names = distinct_names(
        _bound_names(module), [_constant_name(first[key]) for key in repeated]
    )
    by_key = dict(zip(repeated, names, strict=True))
    definitions: list[ast.stmt] = [
        ast.Assign(
            targets=[ast.Name(id=by_key[key], ctx=ast.Store())],
            value=ast.parse(ast.unparse(first[key]), mode="eval").body,
            lineno=0,
        )
        for key in repeated
    ]
    module = _Replacer(by_key).visit(module)
    index = _after_imports(module)
    module.body[index:index] = definitions
    return module

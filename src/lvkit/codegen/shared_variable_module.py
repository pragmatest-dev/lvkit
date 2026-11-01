"""Python module for the Shared Variables of a generated package.

A Shared Variable holds one value for everything that reads or writes it, as a
global variable does, and belongs to a project library, so it is named by the
library's file and the variable. The package gets a module, ``shared_variables``,
that holds each as a module-level name set to the variable's type default, and a
``<name>_timestamp`` set to ``0.0``; a VI that reads or writes the variable
imports the module and uses the attribute, so every VI that names the same
library and variable reaches the same value. A write also sets the timestamp to
``time.time()``.

The module is written from ``InMemoryVIGraph.shared_variable_uses`` over the whole
package, with the variables in the order of their library path and name, so it
does not depend on the order the VIs are generated in.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass

from lvkit.graph.package_queries import SharedVariableUse

from .ast_utils import default_value_expr, to_var_name, unique_names

SHARED_VARIABLES_MODULE = "shared_variables"


@dataclass(frozen=True)
class SharedVariableAttributes:
    """The names a shared variable has in the ``shared_variables`` module, and
    why the node cannot be generated when it has none."""

    value: str
    timestamp: str
    default: str
    error: str | None = None


@dataclass(frozen=True)
class PackageSharedVariables:
    """The Shared Variables of one package, from its Shared Variable nodes."""

    by_node: dict[str, SharedVariableAttributes]

    @classmethod
    def from_uses(cls, uses: Iterable[SharedVariableUse]) -> PackageSharedVariables:
        """Collect ``uses`` (all the Shared Variable nodes of the package). Each
        (library path, variable) gets its attribute in sorted order, so the same
        one whatever order the nodes come in. A variable whose nodes disagree on
        the type default is used as two types, which no node of it generates."""
        ordered = sorted(uses, key=lambda u: (u.library_key, u.variable, u.node_id))
        defaults: dict[tuple[str, str], set[str]] = {}
        for use in ordered:
            default = ast.unparse(default_value_expr(use.value_type))
            defaults.setdefault((use.library_key, use.variable), set()).add(default)
        keys = list(defaults)
        names: dict[tuple[str, str], str] = {}
        taken: list[str] = []
        for key in keys:
            library_name = next(
                u.library_name for u in ordered if (u.library_key, u.variable) == key
            )
            taken.append(to_var_name(f"{library_name}_{key[1]}"))
        for key, name in zip(keys, unique_names(taken), strict=True):
            names[key] = name
        by_node: dict[str, SharedVariableAttributes] = {}
        for use in ordered:
            key = (use.library_key, use.variable)
            found = defaults[key]
            default = ast.unparse(default_value_expr(use.value_type))
            error = None
            if len(found) > 1:
                error = (
                    f"Shared Variable '{use.variable}' of '{use.library_name}' is "
                    f"used as two types (defaults {sorted(found)}); it is defined "
                    "once, in the shared_variables module"
                )
            by_node[use.node_id] = SharedVariableAttributes(
                value=names[key],
                timestamp=f"{names[key]}_timestamp",
                default=default if len(found) == 1 else sorted(found)[0],
                error=error,
            )
        return cls(by_node)

    def build_module(self) -> str | None:
        """Module source defining every variable; None when there is none."""
        definitions: dict[str, SharedVariableAttributes] = {}
        for attributes in self.by_node.values():
            if attributes.error is None:
                definitions.setdefault(attributes.value, attributes)
        if not definitions:
            return None
        body: list[ast.stmt] = [
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            ast.ImportFrom(module="typing", names=[ast.alias(name="Any")], level=0),
        ]
        for name in sorted(definitions):
            attributes = definitions[name]
            body.append(
                ast.AnnAssign(
                    target=ast.Name(id=attributes.value, ctx=ast.Store()),
                    annotation=ast.Name(id="Any", ctx=ast.Load()),
                    value=ast.parse(attributes.default, mode="eval").body,
                    simple=1,
                )
            )
            body.append(
                ast.AnnAssign(
                    target=ast.Name(id=attributes.timestamp, ctx=ast.Store()),
                    annotation=ast.Name(id="float", ctx=ast.Load()),
                    value=ast.Constant(value=0.0),
                    simple=1,
                )
            )
        module = ast.Module(body=body, type_ignores=[])
        ast.fix_missing_locations(module)
        return ast.unparse(module)

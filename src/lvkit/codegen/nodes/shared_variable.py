"""Code generator for Shared Variable nodes.

A Shared Variable holds one value shared by the whole application, as a global
variable does. The package's ``shared_variables`` module (see
``shared_variable_module``) holds each variable, named by its library and
variable, as a module-level name; a node reads or writes that attribute, so every
generated module that names the same library and variable reaches the same
value. A single-process and a network-published variable are generated the same
way. A write also sets the variable's ``_timestamp`` attribute to the time of the
write, which is the node's timestamp output.

A node whose library the calling VI's link table does not record, or records in a
form that could not be read, has no generated code: it raises a ``CodeGenError``
saying which.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import SharedVariableNode
from lvkit.models import Terminal
from lvkit.parser.node_types import SharedVariableTerminal

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver, ModuleImport
from ..shared_variable_module import SharedVariableAttributes


def _terminal(node: SharedVariableNode, which: SharedVariableTerminal) -> Terminal:
    """The node's terminal with the given ``terminal_ID``."""
    for term in node.terminals:
        if term.index == which:
            return term
    raise CodeGenError(
        f"Shared Variable '{node.variable_name}' has no {which.label} terminal", node
    )


def _attribute(module: ModuleImport, attribute: str, store: bool) -> ast.Attribute:
    return ast.Attribute(
        value=ast.Name(id=module.alias, ctx=ast.Load()),
        attr=attribute,
        ctx=ast.Store() if store else ast.Load(),
    )


def _declare(
    node: SharedVariableNode, ctx: CodeGenContext
) -> tuple[ModuleImport, SharedVariableAttributes]:
    library = node.library
    if library is None and node.library_unreadable is not None:
        raise ConversionGap(
            f"Shared Variable '{node.variable_name}' cannot be generated: its "
            f"library link is unreadable ({node.library_unreadable})",
            node,
        )
    if library is None:
        raise ConversionGap(
            f"Shared Variable '{node.variable_name}' cannot be generated: the VI's "
            "link table records no library for it, so how it is published is "
            "unknown",
            node,
        )
    resolver = ctx.import_resolver
    if not isinstance(resolver, ImportResolver):
        raise CodeGenError(
            f"Shared Variable '{node.variable_name}' is shared by name, and there "
            "is no generated package to define it in",
            node,
        )
    return resolver.shared_variable(node)


def generate(node: SharedVariableNode, ctx: CodeGenContext) -> CodeFragment:
    """``module.x = value`` and ``module.x_timestamp = time.time()`` for a write
    node; ``x = module.x`` for a read node, plus ``t = module.x_timestamp`` when
    its timestamp terminal is wired."""
    value_term = _terminal(node, SharedVariableTerminal.VARIABLE)
    module, attributes = _declare(node, ctx)
    if node.is_write:
        if not ctx.is_wired(value_term.id):
            raise ConversionGap(
                f"Shared Variable '{node.variable_name}' is written with nothing wired",
                node,
            )
        value = ctx.wired_input(value_term.id, node)
        if value is None:
            raise CodeGenError(
                f"Shared Variable '{node.variable_name}' write value is unresolved",
                node,
            )
        return CodeFragment(
            statements=[
                ast.Assign(
                    targets=[_attribute(module, attributes.value, True)],
                    value=parse_expr(value),
                ),
                ast.Assign(
                    targets=[_attribute(module, attributes.timestamp, True)],
                    value=parse_expr("time.time()"),
                ),
            ],
            imports={module.statement, "import time"},
        )

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    value_var = ctx.make_output_var(node.variable_name, node.id, value_term.id)
    statements.append(
        ast.Assign(
            targets=[ast.Name(id=value_var, ctx=ast.Store())],
            value=_attribute(module, attributes.value, False),
        )
    )
    bindings[value_term.id] = value_var
    timestamp_term = next(
        (t for t in node.terminals if t.index == SharedVariableTerminal.TIMESTAMP),
        None,
    )
    if timestamp_term is not None and ctx.is_wired(timestamp_term.id):
        timestamp_var = ctx.make_output_var(
            f"{node.variable_name}_timestamp", node.id, timestamp_term.id
        )
        statements.append(
            ast.Assign(
                targets=[ast.Name(id=timestamp_var, ctx=ast.Store())],
                value=_attribute(module, attributes.timestamp, False),
            )
        )
        bindings[timestamp_term.id] = timestamp_var
    return CodeFragment(
        statements=statements, bindings=bindings, imports={module.statement}
    )

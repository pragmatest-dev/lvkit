"""Code generator for Shared Variable nodes.

A Shared Variable holds one value shared by the whole application, as a global
variable does, so a node reads or writes it through
``lvkit.runtime.shared_variable``, which keeps the value per library and variable:
every generated module that names the same library and variable reaches the same
value. A single-process and a network-published variable are generated the same
way. The timestamp output is the time of the variable's last write.

A node whose library the calling VI's link table does not record, or records in a
form that could not be read, has no generated code: it raises a ``CodeGenError``
saying which.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import SharedVariableNode
from lvkit.models import Terminal
from lvkit.parser.node_types import SharedVariableTerminal

from ..ast_utils import default_value_expr, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import CodeGenError

_IMPORT = "from lvkit.runtime import shared_variable as _shared_variable"
_MODULE = "_shared_variable"


def _terminal(node: SharedVariableNode, which: SharedVariableTerminal) -> Terminal:
    """The node's terminal with the given ``terminal_ID``."""
    for term in node.terminals:
        if term.index == which:
            return term
    raise CodeGenError(
        f"Shared Variable '{node.variable_name}' has no {which.label} terminal", node
    )


def _call(function: str, args: list[ast.expr]) -> ast.Call:
    return ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=_MODULE, ctx=ast.Load()),
            attr=function,
            ctx=ast.Load(),
        ),
        args=args,
        keywords=[],
    )


def generate(node: SharedVariableNode, ctx: CodeGenContext) -> CodeFragment:
    """``_shared_variable.write(library, variable, value)`` for a write node;
    ``x = _shared_variable.read(library, variable, default)`` for a read node,
    plus ``t = _shared_variable.timestamp(library, variable)`` when its timestamp
    terminal is wired."""
    library = node.library
    if library is None and node.library_unreadable is not None:
        raise CodeGenError(
            f"Shared Variable '{node.variable_name}' cannot be generated: its "
            f"library link is unreadable ({node.library_unreadable})",
            node,
        )
    if library is None:
        raise CodeGenError(
            f"Shared Variable '{node.variable_name}' cannot be generated: the VI's "
            "link table records no library for it, so how it is published is "
            "unknown",
            node,
        )
    name: list[ast.expr] = [
        ast.Constant(value=library.qualified_name),
        ast.Constant(value=node.variable_name),
    ]
    value_term = _terminal(node, SharedVariableTerminal.VARIABLE)
    if node.is_write:
        if not ctx.is_wired(value_term.id):
            raise CodeGenError(
                f"Shared Variable '{node.variable_name}' is written with nothing wired",
                node,
            )
        value = ctx.resolve(value_term.id)
        if value is None:
            raise CodeGenError(
                f"Shared Variable '{node.variable_name}' write value is unresolved",
                node,
            )
        return CodeFragment(
            statements=[
                ast.Expr(value=_call("write", [*name, parse_expr(value)])),
            ],
            imports={_IMPORT},
        )

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    value_var = ctx.make_output_var(node.variable_name, node.id, value_term.id)
    statements.append(
        ast.Assign(
            targets=[ast.Name(id=value_var, ctx=ast.Store())],
            value=_call("read", [*name, default_value_expr(value_term.lv_type)]),
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
                value=_call("timestamp", name),
            )
        )
        bindings[timestamp_term.id] = timestamp_var
    return CodeFragment(statements=statements, bindings=bindings, imports={_IMPORT})

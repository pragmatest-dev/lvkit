"""Code generator for global variable nodes.

A Global VI generates as a module whose attributes are its controls (see
``global_module``); a global variable node reads or writes one attribute of that
module, so every caller shares the one value, as LabVIEW's global does.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import GlobalVariableNode, VINode

from ..ast_utils import parameter_names, parse_expr, to_module_name
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver, ModuleImport


def _module_import(
    node: GlobalVariableNode, global_vi_key: str, ctx: CodeGenContext
) -> ModuleImport:
    """How the generated module imports the Global VI's module."""
    resolver = ctx.import_resolver
    if isinstance(resolver, ImportResolver):
        return resolver.module(global_vi_key)
    module = to_module_name(node.global_vi.qualified_name)
    alias = f"{module}_global"
    return ModuleImport(f"from . import {module} as {alias}", alias)


def generate(node: GlobalVariableNode, ctx: CodeGenContext) -> CodeFragment:
    """``x = <global module>.<control>`` for a read, ``<global module>.<control>
    = value`` for a write."""
    if ctx.graph is None:
        raise CodeGenError("A global variable needs the VI graph to be resolved", node)
    located = ctx.graph.global_vi_control(node)
    if located is None:
        raise ConversionGap(
            f"Global VI '{node.global_vi.qualified_name}' is not loaded, so control "
            f"{node.param_idx} ('{node.control_name}') cannot be resolved",
            node,
        )
    global_vi_key, control = located
    global_vi = ctx.graph.get_graph_node(global_vi_key)
    assert isinstance(global_vi, VINode)
    attr = parameter_names(global_vi.terminals).get(control.id)
    if attr is None:
        # An error cluster has no Python value: errors are exceptions, so the
        # node's wire carries nothing (as for a property node's error terminals).
        return CodeFragment.empty()
    module = _module_import(node, global_vi_key, ctx)
    target = ast.Attribute(
        value=ast.Name(id=module.alias, ctx=ast.Load()),
        attr=attr,
        ctx=ast.Store() if node.is_write else ast.Load(),
    )
    term = node.terminals[0]
    if node.is_write:
        if not ctx.is_wired(term.id):
            raise ConversionGap(
                f"Global variable '{node.control_name}' is written with nothing wired",
                node,
            )
        value = ctx.resolve(term.id)
        if value is None:
            raise CodeGenError(
                f"Global variable '{node.control_name}' write value is unresolved",
                node,
            )
        return CodeFragment(
            statements=[ast.Assign(targets=[target], value=parse_expr(value))],
            imports={module.statement},
        )
    var_name = ctx.make_output_var(attr, term.id, term.id)
    return CodeFragment(
        statements=[
            ast.Assign(targets=[ast.Name(id=var_name, ctx=ast.Store())], value=target)
        ],
        bindings={term.id: var_name},
        imports={module.statement},
    )

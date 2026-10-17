"""Python module for a Global VI.

A Global VI has no diagram: it is its front-panel controls, and every VI that
reads or writes one of them shares the value. The module holds each control as a
module-level name set to the control's saved default; a VI that uses the global
imports the module and reads or writes those attributes (see
``nodes/global_variable``).

An error cluster control gets no name. Errors are exceptions in generated code, so
an error cluster carries nothing: ``build_args`` and ``build_result_class`` skip
it, and the property node generator skips its error terminals. A global variable
node on such a control generates nothing for the same reason.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import Constant, VINode
from lvkit.models import LVTypeKind, Terminal

from .ast_utils import param_default_expr, parameter_names, parse_expr
from .context import (
    _SIMPLENAMESPACE_IMPORT,
    _format_constant,
    _needs_simplenamespace,
)


def _saved_default(control: Terminal) -> tuple[ast.expr, bool]:
    """The control's saved default as an expression, and whether it needs
    ``SimpleNamespace``. A cluster or array keeps its saved value, formatted by the
    same path as a cluster or array constant on a diagram; unlike a function
    parameter default this is not a signature default, so a mutable container is
    the intended initial value. Every other control takes ``param_default_expr``."""
    lv_type = control.lv_type
    default = control.default_value
    if (
        lv_type is not None
        and lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.ARRAY)
        and isinstance(default, str)
    ):
        text = _format_constant(Constant(id=control.id, value=default, lv_type=lv_type))
        try:
            return ast.parse(text, mode="eval").body, _needs_simplenamespace(text)
        except SyntaxError:
            pass
    return param_default_expr(control), False


def build_global_module(vi_node: VINode) -> str:
    """Module source for a Global VI: one annotated module-level name per
    front-panel control, in the VI's own control order, bound to the control's
    saved default. The names are the ones a global variable node reads
    (``parameter_names`` over the same control list)."""
    controls = vi_node.terminals
    names = parameter_names(controls)
    body: list[ast.stmt] = [
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        ),
        ast.ImportFrom(module="typing", names=[ast.alias(name="Any")], level=0),
        ast.ImportFrom(module="pathlib", names=[ast.alias(name="Path")], level=0),
    ]
    assignments: list[ast.stmt] = []
    needs_namespace = False
    for control in controls:
        if control.id not in names:
            continue
        value, namespace = _saved_default(control)
        needs_namespace = needs_namespace or namespace
        assignments.append(
            ast.AnnAssign(
                target=ast.Name(id=names[control.id], ctx=ast.Store()),
                annotation=parse_expr(control.python_type()),
                value=value,
                simple=1,
            )
        )
    if needs_namespace:
        body.extend(ast.parse(_SIMPLENAMESPACE_IMPORT).body)
    module = ast.Module(body=[*body, *assignments], type_ignores=[])
    ast.fix_missing_locations(module)
    return ast.unparse(module)

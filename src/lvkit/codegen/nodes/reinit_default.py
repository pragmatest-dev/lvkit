"""Reinit To Default, the method of a control or indicator the Invoke Node is bound to.

NI: "Reinitializes the value of the control to the default value"
(``control_properties.json``, ``methods.reinit_to_default.doc_url``). An Invoke
Node bound to a control or indicator of the VI (``bound_control_uid``, no reference
wired) writes its default value as a ``Value`` row of a bound Property Node writes
a value (``property_node._bound_value``): a control through ``write_control``, so
its variable is assigned and a VI with the panel hooks calls ``live_io.write``; an
indicator the function holds (``local_indicators``; the graph holds one that
Reinit To Default writes, ``indicators_of_value_rows``) through its ``write``. The
default is the saved default when it maps to the type, else the type's default
(``type_default.control_default``), the value the control's parameter starts at.

A control of another VI and a reference that is wired are not bound to this VI's
panel, and stay gaps."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..type_default import control_default
from .local_variable import write_control


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The statements that assign the bound control or indicator its default."""
    bound = ctx.graph.bound_terminal(node) if ctx.graph is not None else None
    if bound is None:
        raise ConversionGap(
            "Reinit To Default is generated only on a control or indicator of this "
            "VI that the node is bound to, and this node is not bound to one",
            node,
        )
    value = ast.unparse(control_default(ctx, bound))
    if bound.is_indicator:
        if bound.id not in ctx.local_indicators.variables:
            raise ConversionGap(
                f"The indicator {bound.name!r} is not held in a variable of the "
                "function, so there is no value to reinitialize",
                node,
            )
        return CodeFragment(statements=ctx.local_indicators.write(bound.id, value))
    variable = ctx.resolve(bound.id)
    if variable is None:
        raise ConversionGap(f"The control {bound.name!r} has no variable", node)
    return CodeFragment(statements=write_control(bound, variable, value, ctx))

"""The front-panel controls and indicators that are not on the connector pane.

The connector pane is the interface of a VI's function: its parameters and its
result. A control that is not on the pane is not a parameter; it is a local
variable of the function, set to the control's saved default when the function
starts, as the control holds it when the VI runs. An indicator that is not on the
pane is not returned. The function's docstring lists both."""

from __future__ import annotations

import ast
from collections.abc import Sequence

from lvkit.graph.models import VIContext
from lvkit.models import Terminal, TypeNamer

from .ast_utils import build_assign, distinct_names, param_default_expr, to_var_name

_HEADING = "Front panel, not on the connector pane:"


def off_pane_locals(
    controls: Sequence[Terminal], names: dict[str, str]
) -> list[ast.stmt]:
    """The assignment of each off-pane control's saved default to its local
    variable (``names``: terminal id to name), in front-panel order."""
    return [
        build_assign(names[c.id], param_default_expr(c))
        for c in controls
        if not c.is_error_cluster and c.id in names
    ]


def front_panel_docstring(
    vi_context: VIContext, names: dict[str, str], spell: TypeNamer | None
) -> ast.stmt | None:
    """The docstring that lists the off-pane controls (the local variable, type
    and saved default of each) and indicators (label and type); None when the VI
    has neither."""
    controls = [c for c in vi_context.off_pane_inputs if not c.is_error_cluster]
    indicators = [c for c in vi_context.off_pane_outputs if not c.is_error_cluster]
    if not controls and not indicators:
        return None
    lines = [_HEADING]
    if controls:
        lines.append("  Controls (local variables, set to their saved default):")
        lines.extend(
            f"    {names[c.id]}: {c.python_type(spell)} = "
            f"{ast.unparse(param_default_expr(c))}"
            for c in controls
        )
    if indicators:
        lines.append("  Indicators (written by the diagram, not returned):")
        taken = [names[c.id] for c in vi_context.inputs if c.id in names]
        taken.extend(names[c.id] for c in controls)
        labels = distinct_names(
            taken, [to_var_name(c.name or "indicator") for c in indicators]
        )
        lines.extend(
            f"    {label}: {c.python_type(spell)}"
            for label, c in zip(labels, indicators, strict=True)
        )
    text = "\n".join(lines)
    return ast.Expr(value=ast.Constant(value=f"{text}\n"))

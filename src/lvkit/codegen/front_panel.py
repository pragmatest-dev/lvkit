"""The front-panel controls and indicators that are not on the connector pane.

The connector pane is the interface of a VI's function: its parameters and its
result. A control that is not on the pane is not a parameter; it is a local
variable of the function, set to the control's saved default when the function
starts, as the control holds it when the VI runs. An indicator that is not on the
pane is not returned. The function's docstring lists both."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import TYPE_CHECKING

from lvkit.graph.models import VIContext
from lvkit.models import Terminal, TypeNamer

from .ast_utils import (
    build_assign,
    distinct_names,
    terminal_annotation,
    to_var_name,
)
from .label_names import python_label
from .type_default import control_default

if TYPE_CHECKING:
    from .context import CodeGenContext

_HEADING = "Front panel, not on the connector pane:"


def off_pane_locals(
    controls: Sequence[Terminal], names: dict[str, str], ctx: CodeGenContext
) -> list[ast.stmt]:
    """The assignment of each off-pane control's saved default to its local
    variable (``names``: terminal id to name), in front-panel order."""
    return [
        build_assign(names[c.id], control_default(ctx, c))
        for c in controls
        if c.id in names
    ]


def front_panel_docstring(
    vi_context: VIContext,
    names: dict[str, str],
    spell: TypeNamer | None,
    ctx: CodeGenContext,
    lookup_parameters: Sequence[str] = (),
) -> ast.stmt | None:
    """The docstring that says which Variant parameters are dicts of attributes
    (``lookup_parameters``: terminal ids) and lists the off-pane controls (the
    local variable, type and saved default of each) and indicators (label and
    type); None when the VI has none of them."""
    controls = list(vi_context.off_pane_inputs)
    indicators = list(vi_context.off_pane_outputs)
    tables = [names[t] for t in lookup_parameters if t in names]
    if not controls and not indicators and not tables:
        return None
    lines = [
        f"{name}: a dict of the variant's attributes (the caller builds it with "
        "Set Variant Attribute)."
        for name in sorted(tables)
    ]
    if not controls and not indicators:
        return ast.Expr(value=ast.Constant(value="\n".join(lines) + "\n"))
    lines.append(_HEADING)
    if controls:
        lines.append("  Controls (local variables, set to their saved default):")
        lines.extend(
            f"    {names[c.id]}: {terminal_annotation(c, spell)} = "
            f"{ast.unparse(control_default(ctx, c))}"
            for c in controls
        )
    if indicators:
        lines.append("  Indicators (written by the diagram, not returned):")
        taken = [names[c.id] for c in vi_context.inputs if c.id in names]
        taken.extend(names[c.id] for c in controls)
        labels = distinct_names(
            taken,
            [to_var_name(python_label(c) or "indicator") for c in indicators],
        )
        lines.extend(
            f"    {label}: {terminal_annotation(c, spell)}"
            for label, c in zip(labels, indicators, strict=True)
        )
    text = "\n".join(lines)
    return ast.Expr(value=ast.Constant(value=f"{text}\n"))

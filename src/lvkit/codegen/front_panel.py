"""The front-panel controls and indicators that are not on the connector pane.

The connector pane is the interface of a VI's function: its parameters and its
result. A control that is not on the pane is not a parameter; it is a local
variable of the function, set to the control's saved default when the function
starts, as the control holds it when the VI runs. An indicator that is not on the
pane is not returned. The function's docstring is the VI's description followed by
the list of both."""

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


def function_docstring(
    vi_context: VIContext,
    names: dict[str, str],
    spell: TypeNamer | None,
    ctx: CodeGenContext,
) -> ast.stmt | None:
    """The function's docstring: the VI's description, verbatim (LabVIEW's bold
    markup stays text; a blank one is none), then the front-panel notes; None when
    the VI has neither."""
    notes = _front_panel_notes(vi_context, names, spell, ctx)
    description = vi_context.description
    if not description or not description.strip():
        text = notes
    elif notes is None:
        text = description
    else:
        text = f"{description}\n\n{notes}"
    return None if text is None else ast.Expr(value=ast.Constant(value=text))


def _front_panel_notes(
    vi_context: VIContext,
    names: dict[str, str],
    spell: TypeNamer | None,
    ctx: CodeGenContext,
) -> str | None:
    """The text that lists the off-pane controls (the local variable, type and
    saved default of each) and indicators (label and type); None when the VI has
    none of them."""
    controls = list(vi_context.off_pane_inputs)
    indicators = list(vi_context.off_pane_outputs)
    if not controls and not indicators:
        return None
    lines = [_HEADING]
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
    return "\n".join(lines) + "\n"

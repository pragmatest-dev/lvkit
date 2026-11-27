"""The gap for a write of a row NI says "cannot be set while the VI runs", on a
reference to a front-panel object.

NI's rule is about the VI that owns the object being in run mode, so it applies to
the objects of this VI's own front panel. A write through a reference to an object
on another VI's front panel (the Panel of a VI reference the diagram opened or was
given) edits that VI, which is VI Scripting, and converted code has no equivalent
for it. Which of the two it is comes from where the reference was made
(``reference_origin.panel_owner``); when the diagram does not show it, the gap
names both.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ..context import CodeGenContext
from .reference_origin import PanelOwner, panel_owner

_EDITING = (
    "that is editing a VI (VI Scripting), "
    "which converted code has no equivalent for"
)


def unsettable_write_gap(
    node: PrimitiveNode, row_name: str, page: str, ctx: CodeGenContext
) -> ConversionGap:
    """The gap for the write of the row ``row_name`` of ``node``, whose NI page
    ``page`` says it cannot be set while the VI runs."""
    owner = (
        panel_owner(ctx.graph, node)
        if ctx.graph is not None
        else PanelOwner.UNKNOWN
    )
    head = f"Property '{row_name}' on a {node.object_name} reference is written"
    quote = f"NI's page says it cannot be set while the VI runs ({page})"
    if owner is PanelOwner.THIS_VI:
        return ConversionGap(f"{head}, and {quote}", node)
    if owner is PanelOwner.OTHER_VI:
        return ConversionGap(
            f"{head} on an object of another VI's front panel, reached through a "
            f"VI reference: {_EDITING}",
            node,
        )
    return ConversionGap(
        f"{head}: on an object of this VI's own front panel, {quote}; on an "
        f"object of another VI's front panel reached through a VI reference, "
        f"{_EDITING}",
        node,
    )

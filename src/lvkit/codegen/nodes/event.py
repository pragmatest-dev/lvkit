"""Code generator for Event Structures.

An Event Structure is an asynchronous UI event loop: it blocks waiting for
front-panel events (Value Change, Panel Close, timeout, ...) and runs the frame
whose event fired. lvkit generates HEADLESS Python with no front panel, so there
is no event source and no faithful runtime analog (unlike a queue, which has a
clean headless meaning). The structure is a gap: the generated code raises where
it would run, and the reason names the registered events for context.

The structure IS rendered / described / diffed faithfully; only codegen cannot
execute it. See ``project_nicegui_ui_target`` for the planned UI-generation path
where these same event frames compile to async event handlers.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import EventStructureNode

from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: EventStructureNode, ctx: CodeGenContext) -> CodeFragment:
    """An Event Structure is a gap: headless Python has no UI to raise the
    events, so the structure has no runtime analog. The reason lists the
    registered events."""
    labels = [f.event_label for f in node.frames if f.event_label]
    events = "; ".join(labels) if labels else "no registered events"
    raise ConversionGap(
        "Event Structure not supported in headless codegen: an asynchronous UI "
        "event loop has no runtime equivalent without a front panel "
        f"(events: {events}). lvkit renders, describes and diffs it faithfully "
        "but cannot execute it.",
        node,
    )

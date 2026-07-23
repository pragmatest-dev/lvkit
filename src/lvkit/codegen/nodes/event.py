"""Code generator for Event Structures.

An Event Structure is an asynchronous UI event loop: it blocks waiting for
front-panel events (Value Change, Panel Close, timeout, ...) and runs the frame
whose event fired. lvkit generates HEADLESS Python with no front panel, so there
is no event source and no faithful runtime analog (unlike a queue, which has a
clean headless meaning). Rather than silently drop the VI's entire event
behaviour -- the old generic fallback emitted only a ``# WARNING`` comment,
which is exactly the silent no-op we forbid -- emit an explicit ``raise`` so
running the generated code fails loudly and honestly at the event structure, and
name the registered events for context.

The structure IS rendered / described / diffed faithfully; only codegen cannot
execute it. See ``project_nicegui_ui_target`` for the planned UI-generation path
where these same event frames compile to async event handlers.
"""
from __future__ import annotations

import ast

from lvkit.models import EventOperation

from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: EventOperation, ctx: CodeGenContext) -> CodeFragment:
    """Emit an explicit ``raise NotImplementedError`` for an Event Structure.

    Headless Python has no UI to raise the events, so the structure has no
    runtime analog. We fail loudly (never a silent no-op) and list the
    registered events so the failure is self-explaining.
    """
    labels = [f.event_label for f in node.frames if f.event_label]
    events = "; ".join(labels) if labels else "no registered events"
    msg = (
        "Event Structure not supported in headless codegen: an asynchronous UI "
        "event loop has no runtime equivalent without a front panel "
        f"(events: {events}). lvkit renders, describes and diffs it faithfully "
        "but cannot execute it."
    )
    raise_stmt = ast.Raise(
        exc=ast.Call(
            func=ast.Name(id="NotImplementedError", ctx=ast.Load()),
            args=[ast.Constant(value=msg)],
            keywords=[],
        ),
        cause=None,
    )
    return CodeFragment(statements=[raise_stmt])

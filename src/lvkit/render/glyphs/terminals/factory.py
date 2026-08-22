"""Kind→class factory for structure border-terminal glyphs.

Resolves a border terminal's ``glyph_kind`` to the one glyph class that draws
it, configured with the injected fields (wire-type color, conditional mode,
unwired frames). Single dispatch point — a new border-terminal kind is one new
file with one class plus one line here. Pure: the tree unpacks the scene
``RenderBorderTerminal`` and passes primitives.
"""

from __future__ import annotations

from .autoindex import AutoIndexTerminalGlyph
from .base import BorderTerminalGlyph
from .concatenate import ConcatenateTerminalGlyph
from .conditional import ConditionalTerminalGlyph
from .event_dyn import EventDynTerminalGlyph
from .event_timeout import EventTimeoutTerminalGlyph
from .generic import GenericTerminalGlyph
from .loop_count import LoopCountTerminalGlyph
from .selector import SelectorTerminalGlyph
from .shift_register import ShiftRegisterTerminalGlyph
from .tunnel import TunnelTerminalGlyph

__all__ = ["border_terminal_glyph"]


def border_terminal_glyph(
    kind: str | None,
    *,
    color: str | None = None,
    cond_continue: bool = False,
    unwired_frames: frozenset[str] = frozenset(),
) -> BorderTerminalGlyph:
    """Return the glyph for ``kind``, configured with the injected fields."""
    if kind in ("N", "i"):
        return LoopCountTerminalGlyph(kind)
    if kind == "eventTimeout":
        return EventTimeoutTerminalGlyph()
    if kind == "eventDyn":
        return EventDynTerminalGlyph()
    if kind == "cond":
        return ConditionalTerminalGlyph(cond_continue)
    if kind == "selector":
        return SelectorTerminalGlyph(color)
    if kind in ("sr_up", "sr_down"):
        return ShiftRegisterTerminalGlyph(kind == "sr_up", color)
    if kind == "autoindex":
        return AutoIndexTerminalGlyph(color)
    if kind == "concatenate":
        return ConcatenateTerminalGlyph(color)
    if kind == "tunnel":
        return TunnelTerminalGlyph(color, unwired_frames)
    return GenericTerminalGlyph()

"""Kind→class factory for structure body glyphs.

Resolves a structure's ``node_type`` to the one glyph class that draws it. This
is the single dispatch point — adding a new structure kind is one new file with
one class plus one line here; nothing else changes. The factory is PURE: the
tree builder computes the scene-derived config (error-border colour, disable
dashing, sequence dividers) and passes it in as plain values.
"""

from __future__ import annotations

from .base import StructureBodyGlyph
from .case import CaseGlyph
from .event import EventGlyph
from .flat_sequence import FlatSequenceGlyph
from .for_loop import ForLoopGlyph
from .generic import GenericStructureGlyph
from .in_place import InPlaceGlyph
from .stacked_sequence import StackedSequenceGlyph
from .while_loop import WhileLoopGlyph

__all__ = ["structure_body_glyph"]


def structure_body_glyph(
    node_type: str | None,
    *,
    border_color: str | None = None,
    dotted: bool = False,
    case_insensitive: bool = False,
    dividers: list[float] | None = None,
) -> StructureBodyGlyph:
    """Return the glyph for ``node_type``, configured with the injected fields."""
    if node_type == "forLoop":
        return ForLoopGlyph()
    if node_type == "whileLoop":
        return WhileLoopGlyph()
    if node_type in ("caseStruct", "select", "commentNode"):
        return CaseGlyph(
            border_color=border_color,
            dotted=dotted,
            case_insensitive=case_insensitive,
        )
    if node_type in ("seq", "sequence"):
        return StackedSequenceGlyph(border_color=border_color)
    if node_type == "flatSequence":
        return FlatSequenceGlyph(dividers=dividers)
    if node_type == "eventStruct":
        return EventGlyph()
    if node_type == "decomposeRecomposeStructure":
        return InPlaceGlyph()
    return GenericStructureGlyph()

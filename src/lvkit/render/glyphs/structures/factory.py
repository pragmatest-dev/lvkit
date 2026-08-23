"""Kind→class factory for structure body glyphs.

Resolves a structure's ``node_type`` to the one glyph class that draws it. This
is the single dispatch point — adding a new structure kind is one new file with
one class plus one line here; nothing else changes. The factory is PURE: the
tree builder computes the scene-derived config (error-border colour, disable
dashing, sequence dividers) and passes it in as plain values.
"""

from __future__ import annotations

from ....models import DisableStructureKind
from .base import StructureBodyGlyph
from .case import CaseGlyph
from .disable import DisableGlyph, TypeSpecGlyph
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
    disable_kind: DisableStructureKind | None = None,
    case_insensitive: bool = False,
    dividers: list[float] | None = None,
) -> StructureBodyGlyph:
    """Return the glyph for ``node_type``, configured with the injected fields.
    ``disable_kind`` (set only for a disable-family ``commentNode``) picks the
    per-subtype class — the subtype, not a dash flag, chooses the appearance."""
    if node_type == "forLoop":
        return ForLoopGlyph()
    if node_type == "whileLoop":
        return WhileLoopGlyph()
    # Disable-family structures serialize as commentNode; the kind picks the
    # class (Type Specialization = solid box + icon; the rest = dotted box).
    if disable_kind is not None:
        if disable_kind is DisableStructureKind.TYPE_SPEC:
            return TypeSpecGlyph(border_color=border_color)
        return DisableGlyph(border_color=border_color)
    # ``select`` (the Select primitive) and a plain ``commentNode`` (a boxed
    # comment) render as a plain bordered box — the same static chrome as a case.
    if node_type in ("caseStruct", "select", "commentNode"):
        return CaseGlyph(
            border_color=border_color,
            case_insensitive=case_insensitive,
        )
    if node_type in ("seq", "sequence"):
        return StackedSequenceGlyph(border_color=border_color)
    if node_type == "flatSequence":
        return FlatSequenceGlyph(dividers=dividers, border_color=border_color)
    if node_type == "eventStruct":
        return EventGlyph()
    if node_type == "decomposeRecomposeStructure":
        return InPlaceGlyph()
    return GenericStructureGlyph()

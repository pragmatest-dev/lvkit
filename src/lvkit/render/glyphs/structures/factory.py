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
    bg_color: str | None = None,
    frame_colors: list[str | None] | None = None,
) -> StructureBodyGlyph:
    """Return the glyph for ``node_type``, configured with the injected fields.
    ``disable_kind`` (set only for a disable-family ``commentNode``) picks the
    per-subtype class — the subtype, not a dash flag, chooses the appearance.
    ``bg_color`` is the structure's own saved background fill -- for a loop,
    its single diagram's; for a one-frame-visible-at-a-time structure (case/
    disable/stacked-sequence/event), its default frame's (see composite.
    _structure_bg_color). ``frame_colors`` is the flat-sequence-only variant:
    every frame shows at once, side by side, so each compartment needs its
    OWN color instead of one shared fill.

    ``bg_color`` is applied to EVERY returned glyph in the one ``return``
    below, via the common ``StructureBodyGlyph.bg_color`` attribute every
    kind inherits -- never per-branch (a prior version set it only on the
    loop branches, leaving case/disable/stacked-sequence/event always
    white regardless of their own real saved color)."""
    glyph: StructureBodyGlyph
    if node_type == "forLoop":
        glyph = ForLoopGlyph()
    elif node_type == "whileLoop":
        glyph = WhileLoopGlyph()
    # Disable-family structures serialize as commentNode; the kind picks the
    # class (Type Specialization = solid box + icon; the rest = dotted box).
    elif disable_kind is not None:
        glyph = (
            TypeSpecGlyph(border_color=border_color)
            if disable_kind is DisableStructureKind.TYPE_SPEC
            else DisableGlyph(border_color=border_color)
        )
    # ``select`` (the Select primitive) and a plain ``commentNode`` (a boxed
    # comment) render as a plain bordered box — the same static chrome as a case.
    elif node_type in ("caseStruct", "select", "commentNode"):
        glyph = CaseGlyph(border_color=border_color, case_insensitive=case_insensitive)
    elif node_type in ("seq", "sequence"):
        glyph = StackedSequenceGlyph(border_color=border_color)
    elif node_type == "flatSequence":
        glyph = FlatSequenceGlyph(
            dividers=dividers, border_color=border_color, frame_colors=frame_colors
        )
    elif node_type == "eventStruct":
        glyph = EventGlyph()
    elif node_type == "decomposeRecomposeStructure":
        glyph = InPlaceGlyph()
    else:
        glyph = GenericStructureGlyph()
    glyph.bg_color = bg_color
    return glyph

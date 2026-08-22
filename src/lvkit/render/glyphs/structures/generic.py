"""``GenericStructureGlyph`` — fallback for any unrecognised structure kind:
an opaque body + a plain border (the base behaviour)."""

from __future__ import annotations

from .base import StructureBodyGlyph


class GenericStructureGlyph(StructureBodyGlyph):
    pass

"""Block-diagram decoration glyphs — clean-room shapes for LabVIEW's Decorations
palette (Flat Frame, Thin/Thick Line, Thin/Thick Line with Arrow). Pure visual;
resolved by ``decoration_glyph`` from a cosm element's ``ImageResID``."""

from __future__ import annotations

from .arrow import ArrowGlyph
from .base import DecorationGlyph
from .factory import decoration_glyph
from .fallback import FallbackGlyph
from .frame import FrameGlyph
from .line import LineGlyph

__all__ = [
    "ArrowGlyph",
    "DecorationGlyph",
    "FallbackGlyph",
    "FrameGlyph",
    "LineGlyph",
    "decoration_glyph",
]

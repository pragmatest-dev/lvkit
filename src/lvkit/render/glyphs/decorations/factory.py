"""``decoration_glyph`` — resolve a decoration's ``ImageResID`` to its glyph.

The block-diagram Decorations palette is a small CLOSED set (Flat Frame, Thin/
Thick Line, Thin/Thick Line with Arrow — see the LabVIEW Wiki). The map below is
grounded in real data, NOT aspect-ratio guesses: ``-35``/``-502`` are confirmed
by issue #32's reporter image (frame + thick arrow); ``-233`` is a Thin Line by
its 1px-tall bounds; ``-303`` shares ``-35``'s grey-box signature. Any id not in
the map draws a neutral placeholder (``FallbackGlyph``) and is logged once, so
the table extends from evidence as new ids appear.
"""

from __future__ import annotations

import logging

from .arrow import ArrowGlyph
from .base import DecorationGlyph
from .fallback import FallbackGlyph
from .frame import FrameGlyph
from .line import LineGlyph

logger = logging.getLogger(__name__)

_FRAME_IDS = frozenset({"-35", "-303"})
_THIN_LINE_IDS = frozenset({"-233"})
_THICK_ARROW_IDS = frozenset({"-502"})

_warned: set[str] = set()


def decoration_glyph(image_res_id: str) -> DecorationGlyph:
    """The clean-room glyph for a decoration ``ImageResID``."""
    if image_res_id in _FRAME_IDS:
        return FrameGlyph()
    if image_res_id in _THIN_LINE_IDS:
        return LineGlyph(thick=False)
    if image_res_id in _THICK_ARROW_IDS:
        return ArrowGlyph(thick=True)
    if image_res_id not in _warned:
        _warned.add(image_res_id)
        logger.warning(
            "unknown block-diagram decoration ImageResID %s — drawing a "
            "placeholder box; add it to render/glyphs/decorations/factory.py "
            "once its shape is confirmed",
            image_res_id,
        )
    return FallbackGlyph()

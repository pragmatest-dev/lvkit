from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@dataclass(frozen=True)
class InlineSvgGlyph:
    """A hand-authored SVG fragment (JSON ``icon.svg``/``icon.file``),
    scaled to the node's bounds via a nested ``<svg>`` element — the fragment
    is written in its own local coordinate space (``size``) and the SVG
    viewport/viewBox machinery does the scaling, so no manual coordinate
    transform code is needed here.

    Backend-specific: only ``SvgBackend`` can embed raw markup (see
    ``Backend.raw_svg``); other backends may fall back to skipping it,
    which resolve_glyph() callers never need to know about, since the
    resolver chain will simply try this before falling through further.
    """

    fragment: str
    size: tuple[int, int] = (24, 24)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = self.size
        backend.raw_svg(self.fragment, x1, y1, x2 - x1, y2 - y1, viewbox=(w, h))

"""``BorderTerminalGlyph`` — abstract base for every structure border-terminal
glyph, plus the shared inset helper.

``draw()`` insets the raw ``termBounds`` (LabVIEW draws the visible glyph ~15%
smaller than the clickable region) and delegates to :meth:`_draw`. Subclasses
implement ``_draw`` from bounds + theme + their own injected config; ``frame_value``
is threaded through for the one kind (a data tunnel) whose look is per-frame.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...backend import Backend
from ...style import Theme

Rect = tuple[float, float, float, float]


def inset(bounds: Rect, frac: float = 0.075) -> Rect:
    """Shrink a rect toward its center by ``frac`` on each side."""
    x1, y1, x2, y2 = bounds
    dx, dy = (x2 - x1) * frac, (y2 - y1) * frac
    return x1 + dx, y1 + dy, x2 - dx, y2 - dy


class BorderTerminalGlyph(ABC):
    def draw(
        self,
        backend: Backend,
        bounds: Rect,
        theme: Theme,
        frame_value: str | None = None,
    ) -> None:
        self._draw(backend, inset(bounds), theme, frame_value)

    @abstractmethod
    def _draw(
        self,
        backend: Backend,
        bounds: Rect,
        theme: Theme,
        frame_value: str | None,
    ) -> None: ...

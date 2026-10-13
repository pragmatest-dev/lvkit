"""``BorderTerminalGlyph`` — abstract base for every structure border-terminal
glyph.

Subclasses implement ``draw`` from the terminal's saved ``termBounds`` + theme +
their own injected config, filling those bounds exactly, so neighbouring border
terminals whose saved bounds touch (a tunnel and its condition terminal, the
elements of a stacked shift register) are drawn touching. ``frame_value`` is
threaded through for the one kind (a data tunnel) whose look is per-frame.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...backend import Backend
from ...style import Theme

Rect = tuple[float, float, float, float]


class BorderTerminalGlyph(ABC):
    @abstractmethod
    def draw(
        self,
        backend: Backend,
        bounds: Rect,
        theme: Theme,
        frame_value: str | None,
    ) -> None: ...

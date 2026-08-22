from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ....parser.layout import Rect
from ...backend import Backend
from ...icons import icon_data_uri
from ...style import Theme


@dataclass(frozen=True)
class IconImageGlyph:
    """A real, extracted ``_ICON.png`` filling the node's box — matches how
    LabVIEW itself draws a SubVI call (the icon IS the node's border, no
    separate label/rect drawn around it)."""

    icon_path: Path
    opacity: float = 1.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        uri = icon_data_uri(self.icon_path)
        if uri is None:
            return
        backend.image(uri, x1, y1, x2 - x1, y2 - y1, opacity=self.opacity)

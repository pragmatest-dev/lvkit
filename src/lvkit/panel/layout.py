"""Where each control sits on the panel: at its stored front-panel bounds, its
label above it, in a container as large as the panel."""

from __future__ import annotations

from dataclasses import dataclass

from .kinds import PanelControl

CAPTION_HEIGHT = 16
MARGIN = 12
MIN_WIDTH = 80


@dataclass(frozen=True)
class Box:
    """A control's wrapper in pixels: the label and the widget, from ``top``."""

    left: int
    top: int
    width: int


def place(
    controls: list[PanelControl], panel_bounds: tuple[int, int, int, int]
) -> tuple[list[Box], int, int]:
    """The wrapper of each control, in order, and the width and height of the
    container. Bounds are (top, left, bottom, right). Positions are the stored
    ones, moved only as far as keeps the first label inside the container."""
    if not controls:
        return [], 400, 200
    shift_left = min(0, min(c.bounds[1] for c in controls))
    shift_top = min(0, min(c.bounds[0] - CAPTION_HEIGHT for c in controls))
    boxes = [
        Box(
            left=c.bounds[1] - shift_left + MARGIN,
            top=c.bounds[0] - CAPTION_HEIGHT - shift_top + MARGIN,
            width=max(c.bounds[3] - c.bounds[1], MIN_WIDTH),
        )
        for c in controls
    ]
    bottoms = [
        box.top + CAPTION_HEIGHT + max(c.bounds[2] - c.bounds[0], 40)
        for box, c in zip(boxes, controls, strict=True)
    ]
    width = max(
        max(box.left + box.width for box in boxes) + MARGIN,
        panel_bounds[3] - panel_bounds[1],
    )
    height = max(max(bottoms) + MARGIN, panel_bounds[2] - panel_bounds[0])
    return boxes, width, height

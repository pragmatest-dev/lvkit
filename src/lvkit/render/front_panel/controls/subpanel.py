"""A Subpanel control, drawn from its stored parts: the frame (part 9), the
content area (part 28) filled with its stored color, and the caption.

A Modern frame (picture ``-412``) is the bevel a Modern cluster has around its
viewport; any other frame is a plain box in the frame part's own colors.
"""

from __future__ import annotations

from ....parser.layout import Rect
from ....parser.models import ParsedFPSubPanel
from ...backend import Backend
from ...style import Theme
from ...text_style import draw_styled_text, text_style
from . import modern
from .base import FRAME_PART_ID, find_part, part_rect_of, stored_rect
from .cluster import VIEWPORT_PART_ID
from .style import ControlStyle, numeric_style


def subpanel_extent(subpanel: ParsedFPSubPanel, bounds: Rect) -> Rect:
    """``bounds`` grown to hold the caption, which the file can place above the
    Subpanel's own box."""
    if subpanel.label is None:
        return bounds
    x1, y1, x2, y2 = _shifted(stored_rect(subpanel.label.bounds), bounds)
    return (
        min(bounds[0], x1),
        min(bounds[1], y1),
        max(bounds[2], x2),
        max(bounds[3], y2),
    )


def draw_subpanel(
    subpanel: ParsedFPSubPanel, bounds: Rect, backend: Backend, theme: Theme
) -> None:
    """Draw ``subpanel`` into its placed ``bounds``."""
    frame_part = find_part(subpanel, FRAME_PART_ID)
    viewport_part = find_part(subpanel, VIEWPORT_PART_ID)
    frame = _shifted(part_rect_of(frame_part), bounds) if frame_part else None
    viewport = _shifted(part_rect_of(viewport_part), bounds) if viewport_part else None
    viewport_fill = (
        viewport_part.bg_color if viewport_part is not None else None
    ) or theme.fp_panel
    if frame is not None and viewport is not None:
        if numeric_style(subpanel) is ControlStyle.MODERN:
            modern.draw_inset_bevel(backend, frame, viewport, viewport_fill)
        else:
            backend.rect(
                *frame,
                fill=(frame_part.bg_color if frame_part else None) or theme.fp_panel,
                stroke=(frame_part.fg_color if frame_part else None)
                or theme.struct_border,
                stroke_width=1.0,
            )
            backend.rect(*viewport, fill=viewport_fill)
    elif viewport is not None:
        backend.rect(*viewport, fill=viewport_fill)
    elif frame is not None:
        backend.rect(*frame, fill="none", stroke=theme.struct_border)
    _draw_caption(subpanel, bounds, backend, theme)


def _draw_caption(
    subpanel: ParsedFPSubPanel, bounds: Rect, backend: Backend, theme: Theme
) -> None:
    label = subpanel.label
    if label is None or label.hidden or not label.text:
        return
    x1, y1, _x2, y2 = _shifted(stored_rect(label.bounds), bounds)
    run_colour = label.font_runs[0].color if label.font_runs else None
    style = text_style(label.font, run_colour or label.fg_color)
    draw_styled_text(
        backend,
        x1 + 1.0,
        (y1 + y2) / 2 + style.size * 0.34,
        label.text,
        style,
        theme.text,
    )


def _shifted(rect: Rect, origin: Rect) -> Rect:
    return (
        rect[0] + origin[0],
        rect[1] + origin[1],
        rect[2] + origin[0],
        rect[3] + origin[1],
    )

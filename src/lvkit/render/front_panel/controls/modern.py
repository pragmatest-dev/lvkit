"""The Modern look's shared pieces: a value field with its inner shadow, the
spinner's two pills, and the value text. Colors and sizes are the ones measured
on real screenshots of Modern controls (``labview-binary-format.md`` "Front
panel structure"); every shape is drawn here from them, never copied.

A Modern value field has no outline. Its frame (``partID`` 9) is only the room
for the inner shadow around the field: a strip along the top and left that
fades from the darker edge at the field to the light page, and a 1 px edge at
the right and bottom.
"""

from __future__ import annotations

from ....parser.layout import Rect
from ...backend import Backend
from ...text_style import TextStyle, draw_styled_text

# A control's field is nearly white, an indicator's grey.
CONTROL_FILL = "#FAFAFA"
INDICATOR_FILL = "#D2D2D2"
# An array's element while the array has no saved elements is drawn disabled: a
# control's field a little lighter than an enabled control's, an indicator's
# much lighter than an enabled indicator's, with light grey text. Measured on
# docs/images/Measurement UI.png of the measurement-plugin-labview sample (150%
# scale): the "double array in" element field at (180, 325) is #FDFDFD, the
# "double array out" element field at (420, 325) is #EDEDED, and the darkest
# pixel of the element's "0" is #A7AAA8 at (146, 319) in the control and
# #A3A7A7 at (384, 319) in the indicator, so the text is their mean. The array's
# index readout beside it is not disabled: #FAFAFA with dark text in both.
DISABLED_CONTROL_FILL = "#FDFDFD"
DISABLED_INDICATOR_FILL = "#EDEDED"
DISABLED_TEXT = "#A5A8A8"
TEXT = "#0F0F0F"
# Digits 10 px tall in the screenshots; this size draws digits about 9.5 px tall.
TEXT_SIZE = 13.0
# The field's text starts 1 px in from the field's left edge.
TEXT_INSET = 1.0

# The inner shadow's fade, light at the frame to dark at the field: the rows of
# the measured strip above a field (7 px at 150%).
_SHADOW_STOPS = (
    (0.0, "#F7F7F7"),
    (0.2, "#EDEDED"),
    (0.4, "#E2E2E2"),
    (0.6, "#D9D9D9"),
    (0.8, "#D3D3D3"),
    (1.0, "#CFCFCF"),
)
# The last row of the shadow, the one against the field, is lighter than the
# fade before it.
_EDGE_TOP = "#E4E4E4"
_EDGE_RIGHT = "#E3E3E3"
_EDGE_BOTTOM = "#DADADA"

_PILL_TOP = "#D1D1D1"
_PILL_BOTTOM = "#A9A9A9"
_PILL_HIGHLIGHT = "#F4F4F4"
_PILL_SHADOW = "#808080"
_PILL_ARROW = "#3C3C3C"
# A disabled element's spinner (same screenshot, "double array in" element
# spinner, x 116-135, y 306-342): the body fades from #E8E8E8 (y 310) to #DBDBDB
# (y 322-324), the lightest pixel is #FAFAFA, the darkest edge #B1B1B1, and the
# darkest arrow pixels #B1B1B1 (up) and #BBBBBB (down), so the arrow is #B5B5B5.
_DISABLED_PILL_TOP = "#E8E8E8"
_DISABLED_PILL_BOTTOM = "#DBDBDB"
_DISABLED_PILL_HIGHLIGHT = "#FAFAFA"
_DISABLED_PILL_SHADOW = "#B1B1B1"
_DISABLED_PILL_ARROW = "#B5B5B5"
_PILL_RADIUS = 3.0
_ARROW_HALF_WIDTH = 3.0
_ARROW_HALF_HEIGHT = 1.5


def field_fill(stored: str | None, is_indicator: bool, disabled: bool = False) -> str:
    """The fill of a Modern value field: the disabled grey when disabled, else
    the color its part stores when that is a literal, else the control's or
    indicator's own."""
    if disabled:
        return DISABLED_INDICATOR_FILL if is_indicator else DISABLED_CONTROL_FILL
    if stored is not None:
        return stored
    return INDICATOR_FILL if is_indicator else CONTROL_FILL


def draw_field(backend: Backend, frame: Rect | None, field: Rect, fill: str) -> None:
    """``field`` filled, with the inner shadow in the room ``frame`` leaves
    around it (none without a frame)."""
    fx1, fy1, fx2, fy2 = field
    if frame is not None:
        ax1, ay1, ax2, ay2 = frame
        if fy1 > ay1:
            backend.gradient_rect(ax1, ay1, ax2, fy1 - 1, stops=_SHADOW_STOPS)
            backend.rect(ax1, fy1 - 1, ax2, fy1, fill=_EDGE_TOP)
        if fx1 > ax1:
            backend.gradient_rect(
                ax1, fy1, fx1, ay2, stops=_SHADOW_STOPS, end=(1.0, 0.0)
            )
        backend.rect(fx2, fy1, fx2 + 1, fy2 + 1, fill=_EDGE_RIGHT)
        backend.rect(fx1, fy2, fx2, fy2 + 1, fill=_EDGE_BOTTOM)
    backend.rect(fx1, fy1, fx2, fy2, fill=fill)


def draw_text(
    backend: Backend,
    field: Rect,
    text: str,
    fill: str = TEXT,
    style: TextStyle | None = None,
) -> None:
    """``text`` at the field's left, centered between its top and bottom, in
    ``style`` (the field's default font at ``TEXT_SIZE`` when None)."""
    fx1, fy1, _fx2, fy2 = field
    style = style or TextStyle(TEXT_SIZE)
    draw_styled_text(
        backend,
        fx1 + TEXT_INSET,
        (fy1 + fy2) / 2 + style.size * 0.36,
        text,
        style,
        fill,
    )


def draw_pill(
    backend: Backend, rect: Rect, up: bool, disabled: bool = False
) -> None:
    """One half of the spinner: a rounded pill with a light line along its top,
    a dark line along its bottom and a small arrow pointing ``up`` or down, in
    the faded colors when ``disabled``."""
    x1, y1, x2, y2 = rect
    top, bottom, highlight, shadow, arrow = (
        (
            _DISABLED_PILL_TOP, _DISABLED_PILL_BOTTOM, _DISABLED_PILL_HIGHLIGHT,
            _DISABLED_PILL_SHADOW, _DISABLED_PILL_ARROW,
        )
        if disabled
        else (_PILL_TOP, _PILL_BOTTOM, _PILL_HIGHLIGHT, _PILL_SHADOW, _PILL_ARROW)
    )
    backend.gradient_rect(
        x1, y1, x2, y2, stops=((0.0, top), (1.0, bottom)), rx=_PILL_RADIUS
    )
    inset = _PILL_RADIUS - 1
    backend.line(x1 + inset, y1 + 0.5, x2 - inset, y1 + 0.5, stroke=highlight)
    backend.line(x1 + inset, y2 - 0.5, x2 - inset, y2 - 0.5, stroke=shadow)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    tip, base = (
        (cy - _ARROW_HALF_HEIGHT, cy + _ARROW_HALF_HEIGHT)
        if up
        else (cy + _ARROW_HALF_HEIGHT, cy - _ARROW_HALF_HEIGHT)
    )
    backend.polygon(
        [(cx - _ARROW_HALF_WIDTH, base), (cx + _ARROW_HALF_WIDTH, base), (cx, tip)],
        fill=arrow,
    )

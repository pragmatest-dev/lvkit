"""The Modern look's shared pieces: a value field with its inner shadow, the
spinner oval, and the value text. Colors and sizes are the ones measured
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

# The spinner is one oval (an ellipse: the sides curve all the way, rows 227 and
# 231 are 10 and 14 px wide of 18) spanning both arrow parts (``partID`` 2 and 3),
# the two parts' 2 px gap showing the page through. Measured on
# docs/images/Measurement UI.png of the measurement-plugin-labview sample (150%
# scale), "double in" spinner, x 113-131, y 225-262 (the parts are stored 12 x
# 12 px, 2 px apart: 18 x 39 px at 150%).
# Body, shaded left to right (row 239, x 114..131): #CE #D5 #D5 #CF #C4 #BE #BD
# #C1 #C1 #C0 #C0 #BE #B9 #AE #A2 #92 #71 #9F -- light at the left, the darkest
# rim 2 px in from the right edge. The upper half's last 2 rows (y 240-241) are
# flat #B2 and its row 242 is the faint light edge; the lower half's first row
# (y 244) is #F7 and the next (y 245) #E0 at the left, the lighter lip.
_CAPSULE_STOPS = (
    (0.0, "#D5D5D5"),
    (0.25, "#C4C4C4"),
    (0.45, "#C0C0C0"),
    (0.65, "#BCBCBC"),
    (0.78, "#AAAAAA"),
    (0.86, "#929292"),
    (0.92, "#717171"),
    (1.0, "#9F9F9F"),
)
_CAPSULE_BOTTOM_BAND = "#B2B2B2"
_CAPSULE_TOP_LIP = "#E4E4E4"
# Arrows (same shot): a bevelled triangle 7 px wide and 7 px tall, centered in its
# half. The up arrow's apex is at (121.5, 231), its left edge runs to (119, 238),
# darkest pixels #3C-#78 along that edge; the down arrow's top bar is y 248
# (x 119-124, #56-#80) and its left edge runs down to its apex at y 255. The rest
# of the triangle is about #9A, hardly darker than the body.
_ARROW_EDGE = "#505050"
_ARROW_FILL = "#9A9A9A"
# A disabled element's spinner (same screenshot, "double array in" element,
# x 116-133, y 306-343, the frame's pale surround behind it). Body, left to right
# (row 319, x 116..132): #DC #EC #EF #EF #EB ... #E8 #DF #D9 #D2. Upper half's
# last rows (y 322-324) flat #DB, lower half's first rows (y 326-327) #ED-#EF.
# Arrows: the up arrow's apex is at (124, 312) and its left edge's darkest pixels
# are #B1-#C4 down to y 318; the down arrow's top bar is y 329 (x 122-127,
# #BB-#CC) with the left edge #BC-#C5 below it. The inside is about #CC.
_DISABLED_CAPSULE_STOPS = (
    (0.0, "#DCDCDC"),
    (0.1, "#ECECEC"),
    (0.25, "#EFEFEF"),
    (0.6, "#E8E8E8"),
    (0.8, "#DFDFDF"),
    (0.9, "#D9D9D9"),
    (1.0, "#D2D2D2"),
)
_DISABLED_CAPSULE_BOTTOM_BAND = "#DBDBDB"
_DISABLED_CAPSULE_TOP_LIP = "#EEEEEE"
_DISABLED_ARROW_EDGE = "#B5B5B5"
_DISABLED_ARROW_FILL = "#CCCCCC"
_BAND_HEIGHT = 1.3
_LIP_HEIGHT = 0.8
_ARROW_HALF_SIZE = 2.4


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


def draw_spinner(
    backend: Backend, halves: tuple[Rect, ...], disabled: bool = False
) -> None:
    """The spinner: one oval spanning ``halves`` (the arrow parts, up first),
    the gap between them showing the page, with a small arrow pointing up in the
    first half and down in the second; the faded colors when ``disabled``."""
    if not halves:
        return
    stops, band, lip, edge, fill = (
        (
            _DISABLED_CAPSULE_STOPS, _DISABLED_CAPSULE_BOTTOM_BAND,
            _DISABLED_CAPSULE_TOP_LIP, _DISABLED_ARROW_EDGE, _DISABLED_ARROW_FILL,
        )
        if disabled
        else (
            _CAPSULE_STOPS, _CAPSULE_BOTTOM_BAND, _CAPSULE_TOP_LIP,
            _ARROW_EDGE, _ARROW_FILL,
        )
    )
    cx1 = min(r[0] for r in halves)
    cy1 = min(r[1] for r in halves)
    cx2 = max(r[2] for r in halves)
    cy2 = max(r[3] for r in halves)
    for index, (x1, y1, x2, y2) in enumerate(halves):
        up = index == 0
        backend.begin_group(clip=(x1, y1, x2, y2))
        backend.gradient_rect(
            cx1, cy1, cx2, cy2, stops=stops, end=(1.0, 0.0),
            rx=(cx2 - cx1) / 2, ry=(cy2 - cy1) / 2,
        )
        if up:
            backend.rect(x1, y2 - _BAND_HEIGHT, x2, y2, fill=band)
        else:
            backend.rect(x1, y1, x2, y1 + _LIP_HEIGHT, fill=lip)
        backend.end_group()
        _draw_arrow(backend, (x1, y1, x2, y2), up, edge, fill)


def _draw_arrow(
    backend: Backend, half: Rect, up: bool, edge: str, fill: str
) -> None:
    """A triangle centered in ``half``: the fill, and the dark edge a bevel
    leaves along its left side (and its top bar when it points down)."""
    x1, y1, x2, y2 = half
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    h = _ARROW_HALF_SIZE
    tip_y, base_y = (cy - h, cy + h) if up else (cy + h, cy - h)
    left, right, tip = (cx - h, base_y), (cx + h, base_y), (cx, tip_y)
    backend.polygon([left, right, tip], fill=fill)
    backend.line(*left, *tip, stroke=edge)
    if not up:
        backend.line(*left, *right, stroke=edge)

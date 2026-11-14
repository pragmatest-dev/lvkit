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
# A value's digits are 13 px tall at 150% on docs/images/Measurement UI.png (the
# "0" of "double in" at x 141-147, y 238-250; of "I32 in" at x 148-154, y 718-730;
# of the index of "double array in" at x 79-85, y 311-323), 8.7 px at 100%, and 11
# px at 125% on docs/images/Running Run Service VI.png ("56276", x 68-103, y
# 196-206): 8.7-8.8 px tall. This size draws digits 8.75 px tall (13 drew 9.3, 14
# px at 150%).
TEXT_SIZE = 12.0
# The field's text starts 1 px in from the field's left edge.
TEXT_INSET = 1.0

# The inner shadow's fade, light at the frame to dark at the field: the rows of
# the measured strip above a field (7 px at 150%).
SHADOW_STOPS = (
    (0.0, "#F7F7F7"),
    (0.2, "#EDEDED"),
    (0.4, "#E2E2E2"),
    (0.6, "#D9D9D9"),
    (0.8, "#D3D3D3"),
    (1.0, "#CFCFCF"),
)
# An array's frame has its own strip, and an element in the array its own over
# it. Measured on docs/images/Measurement UI.png, "double array in", column x 180:
# the frame's strip is y 298-304 #FC #F3 #E7 #DD #D6 #D1 #D0; the element's strip
# is y 305-311 #D9 #D8 #D4 #D0 #CC #CA #C8, then the edge row #E2.
_FRAME_SHADOW_STOPS = (
    (0.0, "#FCFCFC"),
    (1 / 6, "#F3F3F3"),
    (2 / 6, "#E7E7E7"),
    (3 / 6, "#DDDDDD"),
    (4 / 6, "#D6D6D6"),
    (5 / 6, "#D1D1D1"),
    (1.0, "#D0D0D0"),
)
ARRAY_ELEMENT_SHADOW_STOPS = (
    (0.0, "#D9D9D9"),
    (1 / 6, "#D8D8D8"),
    (2 / 6, "#D4D4D4"),
    (3 / 6, "#D0D0D0"),
    (4 / 6, "#CCCCCC"),
    (5 / 6, "#CACACA"),
    (1.0, "#C8C8C8"),
)
# The last row of the shadow, the one against the field, is lighter than the
# fade before it.
_EDGE_TOP = "#E4E4E4"
# The 1 px edge at the field's right and bottom is the fill stepped toward the
# page's white. Measured on docs/images/Measurement UI.png: the indicator "double
# out" field (#D2) has #E3 at x 399 (right) and #DA at y 256 (bottom), both then
# #FF: 17/45 and 8/45 of the way to white. The control "double in" field (#FA)
# shows #FB at x 204 and y 256: no outline, which these fractions give (#FC, #FB).
_EDGE_RIGHT_STEP = 17 / 45
_EDGE_BOTTOM_STEP = 8 / 45

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


def _toward_white(color: str, step: float) -> str:
    """``#RRGGBB`` ``color`` moved ``step`` (0-1) of the way to white."""
    channels = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (255 - c) * step):02X}" for c in channels)


def _color_at(stops: tuple[tuple[float, str], ...], t: float) -> str:
    """The color ``t`` (0-1) of the way along ``stops``, blended linearly."""
    for (o1, c1), (o2, c2) in zip(stops, stops[1:]):
        if t <= o2:
            f = 0.0 if o2 == o1 else (t - o1) / (o2 - o1)
            a = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
            b = [int(c2[i : i + 2], 16) for i in (1, 3, 5)]
            return "#" + "".join(f"{round(x + (y - x) * f):02X}" for x, y in zip(a, b))
    return stops[-1][1]


def draw_shadow_strips(
    backend: Backend,
    outer: Rect,
    inner: Rect,
    stops: tuple[tuple[float, str], ...] = SHADOW_STOPS,
) -> None:
    """The dark strips along the top and left of ``inner`` inside ``outer``,
    each fading from the light page at ``outer`` to the dark at ``inner``, meeting
    in a mitred corner (a diagonal from ``outer``'s corner to ``inner``'s), as on
    the real screenshots: the top strip's left end and the left strip's top end
    slant along the diagonal. One band per pixel of strip, each painted from its
    own depth to the strip's inner edge so the bands leave no seams."""
    ox1, oy1, ox2, oy2 = outer
    ix1, iy1, _ix2, _iy2 = inner
    top, left = iy1 - oy1, ix1 - ox1
    if top > 0:
        n = max(1, round(top))
        slope = left / top if top else 0.0
        for i in range(n):
            y = oy1 + top * i / n
            xl = ox1 + (y - oy1) * slope
            backend.polygon(
                [(xl, y), (ox2, y), (ox2, iy1), (ix1, iy1)],
                fill=_color_at(stops, (i + 0.5) / n),
            )
    if left > 0:
        n = max(1, round(left))
        slope = top / left if left else 0.0
        for i in range(n):
            x = ox1 + left * i / n
            yt = oy1 + (x - ox1) * slope
            backend.polygon(
                [(x, yt), (ix1, iy1), (ix1, oy2), (x, oy2)],
                fill=_color_at(stops, (i + 0.5) / n),
            )


def draw_field(
    backend: Backend,
    frame: Rect | None,
    field: Rect,
    fill: str,
    stops: tuple[tuple[float, str], ...] = SHADOW_STOPS,
) -> None:
    """``field`` filled, with the inner shadow (``stops``) in the room ``frame``
    leaves around it (none without a frame)."""
    fx1, fy1, fx2, fy2 = field
    if frame is not None:
        ax1, ay1, ax2, ay2 = frame
        draw_shadow_strips(backend, frame, field, stops)
        if fy1 > ay1:
            backend.rect(ax1, fy1 - 1, ax2, fy1, fill=_EDGE_TOP)
        backend.rect(
            fx2, fy1, fx2 + 1, fy2 + 1, fill=_toward_white(fill, _EDGE_RIGHT_STEP)
        )
        backend.rect(
            fx1, fy2, fx2, fy2 + 1, fill=_toward_white(fill, _EDGE_BOTTOM_STEP)
        )
    backend.rect(fx1, fy1, fx2, fy2, fill=fill)


def draw_frame(backend: Backend, frame: Rect, inner: Rect, fill: str) -> None:
    """An array's frame in the Modern look: no outline; the fill and the dark
    strips along the top and left run from ``frame``'s top-left to ``inner``'s
    (the room for the elements) bottom-right, so ``frame``'s right and bottom
    margin beyond ``inner`` is left unpainted. Measured on
    docs/images/Measurement UI.png, "double array in": the fill ends at the
    elements viewport's right and bottom (x 214, y 345) while the stored frame
    (part 9) reaches x 220, y 351, and the page shows through that margin
    (only a faint white veil over the grid); top (x 180, y 298-304) #FC #F3 #E7
    #DD #D6 #D1 #D0 fading toward the elements."""
    fx1, fy1, _fx2, _fy2 = frame
    _ix1, _iy1, ix2, iy2 = inner
    drawn = (fx1, fy1, ix2, iy2)
    backend.rect(*drawn, fill=fill)
    draw_shadow_strips(backend, drawn, inner, _FRAME_SHADOW_STOPS)


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
            _DISABLED_CAPSULE_STOPS,
            _DISABLED_CAPSULE_BOTTOM_BAND,
            _DISABLED_CAPSULE_TOP_LIP,
            _DISABLED_ARROW_EDGE,
            _DISABLED_ARROW_FILL,
        )
        if disabled
        else (
            _CAPSULE_STOPS,
            _CAPSULE_BOTTOM_BAND,
            _CAPSULE_TOP_LIP,
            _ARROW_EDGE,
            _ARROW_FILL,
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
            cx1,
            cy1,
            cx2,
            cy2,
            stops=stops,
            end=(1.0, 0.0),
            rx=(cx2 - cx1) / 2,
            ry=(cy2 - cy1) / 2,
        )
        if up:
            backend.rect(x1, y2 - _BAND_HEIGHT, x2, y2, fill=band)
        else:
            backend.rect(x1, y1, x2, y1 + _LIP_HEIGHT, fill=lip)
        backend.end_group()
        _draw_arrow(backend, (x1, y1, x2, y2), up, edge, fill)


def _draw_arrow(backend: Backend, half: Rect, up: bool, edge: str, fill: str) -> None:
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


# A cluster's Modern frame (``partID`` 9, picture id -412) holds the cluster's
# viewport (``partID`` 28) filled with the frame's own color, inside a 4 px bevel
# lit from the bottom right, drawn over the panel's own color. Measured on
# PCO_FrontPanel.PNG of the LabVIEW-DAQ sample (100%), cluster "roi": frame x
# 238-310, y 500-675 (stored 73 x 176), viewport x 242-306, y 504-671 (65 x 168)
# filled #B3B3B3 (the frame's stored color). Over the panel's #006698: the top
# rows (y 500-503) are #0D6778 (a grid line), #004678, #003B6D, #003567 and the
# left columns (x 238-241) #005789, #005667, #003B6D, #003567 -- the darkening
# grows toward the fill, 32, 43 and 49 less on green and blue, 0.26, 0.36 and 0.40
# of the way to black in mean -- and the bottom rows (y 672-675) and right columns
# (x 307-310) are #086EA0, #0B71A3, #1076A8, #0C72A4, 8, 11, 16 and 12 more, 0.065,
# 0.09, 0.125 and 0.095 of the way to white in mean. Each strip meets the next in a
# mitred corner (the top-right pixel of the viewport row is half dark).
_BEVEL_DARK_ALPHA = (0.14, 0.26, 0.36, 0.40)
_BEVEL_LIGHT_ALPHA = (0.065, 0.09, 0.125, 0.095)


def _band_alpha(alphas: tuple[float, ...], i: int, n: int) -> float:
    """Band ``i`` of ``n``: the measured alpha, by position along ``alphas``."""
    return alphas[min(len(alphas) - 1, int(i * len(alphas) / n))]


def draw_inset_bevel(backend: Backend, outer: Rect, inner: Rect, fill: str) -> None:
    """``inner`` filled with ``fill`` inside a bevel between ``outer`` and
    ``inner``: dark at the top and left, light at the bottom and right, each a
    stack of one-pixel bands that grows toward the fill and meets its neighbors
    in a mitred corner, drawn translucent over whatever is behind."""
    ox1, oy1, ox2, oy2 = outer
    ix1, iy1, ix2, iy2 = inner
    top, left, right, bottom = iy1 - oy1, ix1 - ox1, ox2 - ix2, oy2 - iy2
    backend.rect(ix1, iy1, ix2, iy2, fill=fill)

    def band(points: list[tuple[float, float]], color: str) -> None:
        backend.polygon(points, fill=color)

    dark, light = "rgba(0,0,0,{})", "rgba(255,255,255,{})"
    for i in range(max(0, round(top))):
        a, b = top * i / round(top), top * (i + 1) / round(top)
        color = dark.format(_band_alpha(_BEVEL_DARK_ALPHA, i, round(top)))
        la, lb = left * a / top, left * b / top
        ra, rb = right * a / top, right * b / top
        band(
            [
                (ox1 + la, oy1 + a),
                (ox2 - ra, oy1 + a),
                (ox2 - rb, oy1 + b),
                (ox1 + lb, oy1 + b),
            ],
            color,
        )
    for i in range(max(0, round(left))):
        a, b = left * i / round(left), left * (i + 1) / round(left)
        color = dark.format(_band_alpha(_BEVEL_DARK_ALPHA, i, round(left)))
        ta, tb = top * a / left, top * b / left
        ba, bb = bottom * a / left, bottom * b / left
        band(
            [
                (ox1 + a, oy1 + ta),
                (ox1 + b, oy1 + tb),
                (ox1 + b, oy2 - bb),
                (ox1 + a, oy2 - ba),
            ],
            color,
        )
    for i in range(max(0, round(bottom))):
        a, b = bottom * i / round(bottom), bottom * (i + 1) / round(bottom)
        color = light.format(_band_alpha(_BEVEL_LIGHT_ALPHA, i, round(bottom)))
        la, lb = left * a / bottom, left * b / bottom
        ra, rb = right * a / bottom, right * b / bottom
        band(
            [
                (ox1 + la, oy2 - a),
                (ox2 - ra, oy2 - a),
                (ox2 - rb, oy2 - b),
                (ox1 + lb, oy2 - b),
            ],
            color,
        )
    for i in range(max(0, round(right))):
        a, b = right * i / round(right), right * (i + 1) / round(right)
        color = light.format(_band_alpha(_BEVEL_LIGHT_ALPHA, i, round(right)))
        ta, tb = top * a / right, top * b / right
        ba, bb = bottom * a / right, bottom * b / right
        band(
            [
                (ox2 - a, oy1 + ta),
                (ox2 - b, oy1 + tb),
                (ox2 - b, oy2 - bb),
                (ox2 - a, oy2 - ba),
            ],
            color,
        )

"""One drawing per kind of Boolean (``style.BooleanKind``), each from the colors
and sizes measured on screenshots of that kind (``labview-binary-format.md``
"Front panel structure"). Every shape is our own vector drawing.

State colors come from the body part's stored states (``ParsedFPPartState``):
the first is off, the second on. A kind with no stored state color uses the
measured LED colors below.
"""

from __future__ import annotations

from ....parser.layout import Rect
from ...backend import Backend

# The measured LED colors, used when the body stores none.
LED_ON = "#64FF00"
LED_OFF = "#1E4B00"

_WHITE = "#FFFFFF"
_BLACK = "#000000"

_HALO_OUTER = "#EFEFEF"
_HALO_INNER = "#DEDEDE"

_SWITCH_OUTLINE = "#848484"
_SWITCH_SHADOW = "#808080"
_SWITCH_TOP = "#D1D1D1"
_SWITCH_BOTTOM = "#B5B5B5"

_MODERN_BUTTON_STOPS = ((0.0, "#D3D3D3"), (0.5, "#C7C7C7"), (1.0, "#B2B2B2"))
_MODERN_BUTTON_LIGHT_EDGE = "#929292"
_MODERN_BUTTON_DARK_EDGE = "#6A6A6A"
_MODERN_BUTTON_RADIUS = 3.0

_SYSTEM_BUTTON_FACE = "#E1E1E1"
_SYSTEM_BUTTON_EDGE = "#ADADAD"

_CHECK_EDGE = "#333333"
_SQUARE_LED_RADIUS = 6.0


def blend(color: str, other: str, amount: float) -> str:
    """``color`` moved ``amount`` (0..1) of the way to ``other``; both are
    ``#RRGGBB``."""
    channels = []
    for i in (1, 3, 5):
        a, b = int(color[i : i + 2], 16), int(other[i : i + 2], 16)
        channels.append(round(a + (b - a) * amount))
    return "#" + "".join(f"{c:02X}" for c in channels)


def draw_round_led(
    backend: Backend, body: Rect, halo: Rect | None, color: str
) -> None:
    """A circle in the state color, lighter at its top left, with a darker rim
    and, when the Boolean has a bezel part, a pale 2 px halo around it."""
    x1, y1, x2, y2 = body
    cx, cy, r = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) / 2
    if halo is not None:
        hr = (halo[2] - halo[0]) / 2
        backend.circle((halo[0] + halo[2]) / 2, (halo[1] + halo[3]) / 2, hr,
                       fill=_HALO_OUTER)
        backend.circle((halo[0] + halo[2]) / 2, (halo[1] + halo[3]) / 2, hr - 1,
                       fill=_HALO_INNER)
    backend.gradient_rect(
        cx - r, cy - r, cx + r, cy + r,
        stops=((0.0, blend(color, _WHITE, 0.3)), (1.0, blend(color, _BLACK, 0.15))),
        end=(1.0, 1.0),
        stroke=blend(color, _BLACK, 0.35),
        stroke_width=1.0,
        rx=r,
    )


def draw_slide_switch(
    backend: Backend, body: Rect, thumb: Rect | None, thumb_color: str
) -> None:
    """A pill with fully round ends, a vertical gradient and a lower shadow; the
    thumb is a triangle pointing right, in the state color, at its stored rect."""
    x1, y1, x2, y2 = body
    radius = (y2 - y1) / 2
    backend.rect(x1, y1 + 2, x2, y2, fill=_SWITCH_SHADOW, rx=radius)
    backend.gradient_rect(
        x1, y1, x2, y2 - 2,
        stops=((0.0, _SWITCH_TOP), (1.0, _SWITCH_BOTTOM)),
        stroke=_SWITCH_OUTLINE,
        stroke_width=1.5,
        rx=radius - 1,
    )
    if thumb is not None:
        tx1, ty1, tx2, ty2 = thumb
        backend.polygon(
            [(tx1, ty1), (tx1, ty2), (tx2, (ty1 + ty2) / 2)], fill=thumb_color
        )


def draw_modern_button(
    backend: Backend, body: Rect, caption: str, color: str | None
) -> None:
    """A rounded rectangle with a vertical gradient, a 1 px lighter edge at the
    left and top and a darker one at the bottom and right."""
    x1, y1, x2, y2 = body
    backend.rect(x1, y1, x2, y2, fill=_MODERN_BUTTON_DARK_EDGE,
                 rx=_MODERN_BUTTON_RADIUS)
    backend.rect(x1, y1, x2 - 1, y2 - 1, fill=_MODERN_BUTTON_LIGHT_EDGE,
                 rx=_MODERN_BUTTON_RADIUS)
    backend.gradient_rect(
        x1 + 1, y1 + 1, x2 - 1, y2 - 1,
        stops=_MODERN_BUTTON_STOPS, rx=_MODERN_BUTTON_RADIUS - 1,
    )
    _caption(backend, body, caption, color)


def draw_system_button(
    backend: Backend, body: Rect, caption: str, color: str | None
) -> None:
    """A flat face with a 1 px border."""
    backend.rect(*body, fill=_SYSTEM_BUTTON_FACE, stroke=_SYSTEM_BUTTON_EDGE,
                 stroke_width=1.0)
    _caption(backend, body, caption, color)


def draw_check_box(backend: Backend, body: Rect, on: bool) -> None:
    """A white square with a 1 px dark border and, when on, a check mark."""
    x1, y1, x2, y2 = body
    backend.rect(x1, y1, x2, y2, fill=_WHITE, stroke=_CHECK_EDGE, stroke_width=1.0)
    if on:
        w, h = x2 - x1, y2 - y1
        backend.path(
            [
                (x1 + w * 0.22, y1 + h * 0.5),
                (x1 + w * 0.42, y1 + h * 0.72),
                (x1 + w * 0.78, y1 + h * 0.28),
            ],
            stroke=_BLACK, stroke_width=1.5,
        )


def draw_radio_button(backend: Backend, body: Rect, on: bool) -> None:
    """A white circle with a 1 px dark border and, when on, a dot. Not measured
    against a screenshot: it uses the check box's colors."""
    x1, y1, x2, y2 = body
    cx, cy, r = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) / 2
    backend.circle(cx, cy, r, fill=_WHITE, stroke=_CHECK_EDGE, stroke_width=1.0)
    if on:
        backend.circle(cx, cy, r * 0.45, fill=_BLACK)


def draw_square_led(backend: Backend, body: Rect, color: str) -> None:
    """A rounded rectangle with a vertical gloss: a lighter tint of the state
    color at the top, a darker shade at the bottom."""
    backend.gradient_rect(
        *body,
        stops=(
            (0.0, blend(color, _WHITE, 0.38)),
            (0.25, blend(color, _WHITE, 0.09)),
            (0.5, blend(color, _WHITE, 0.05)),
            (1.0, blend(color, _BLACK, 0.4)),
        ),
        rx=_SQUARE_LED_RADIUS,
    )


def _caption(backend: Backend, body: Rect, caption: str, color: str | None) -> None:
    """``caption`` centered on the button in its stored color (black when it
    stores none)."""
    if not caption:
        return
    x1, y1, x2, y2 = body
    backend.text(
        (x1 + x2) / 2, (y1 + y2) / 2 + 4.5, caption, 13.0, fill=color or _BLACK
    )

"""``RefnumGlyph`` — every refnum control (queue / notifier / user event /
menu / VI-Server / control reference / …), data-typed or not: a clean-room
DOG-EAR reference frame (our own shape — LabVIEW's own icon is proprietary
artwork we never reproduce, but a folded-corner box reads as "this is a
reference", the same visual grammar LabVIEW itself uses), a small KIND
SYMBOL naming the refnum's ``LVType.ref_type`` (a User Event's broadcast/
radar mark, a Queue's stack mark, …, a generic reference mark for anything
else), and a TERMINAL area showing the registered payload's TYPE:

- COMPACT (the heap's default state): a small type-mnemonic badge in the
  corner (``style.type_repr`` — ``abc``/``TF``/``OBJ``/a class short name),
  our own chrome position (LabVIEW's exact compact-badge placement isn't a
  heap-recorded fact — verified against reference renders of a User Event
  and a Queue control: box + kind symbol + corner badge).
- EXPANDED (``layout.ClusterFieldGeom.refnum_expanded`` /
  ``Layout.refnum_expanded`` — the heap's own ``multiCosm`` bit, never
  inferred from size): the registered payload's own REAL per-field elements
  (``terminal``, built by the SAME recursive cluster-element composer every
  genuine nested cluster field uses — see ``nodes._cluster_value_glyph`` —
  wrapped ``DimmedGlyph``, since this is a TYPE display, not real data),
  positioned at the heap's own recorded ``layout.RefnumPayload.offset`` — the
  registered payload ddo's real ``<bounds>`` within this refnum's own box,
  never a guessed/centered placement.

Neither state ever draws the payload's field VALUES (F/0/testPass) — a
refnum's payload is a TYPE, never editable data (issue #45's refnum
trigger)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import Glyph

_CUT_FRAC = 0.30  # dog-ear corner-cut, as a fraction of min(w, h)
_CUT_MAX = 10.0


def _arc_points(
    cx: float, cy: float, r: float, a0: float, a1: float, n: int = 10
) -> list[tuple[float, float]]:
    """Sample a TRUE circular arc (center ``(cx, cy)``, radius ``r``, from
    angle ``a0`` to ``a1`` radians) into ``n`` straight segments — ``Backend``
    has no native arc op (see ``base._quad_bezier_points`` for the same
    reasoning applied to a Bezier curve)."""
    pts = []
    for i in range(n + 1):
        ang = a0 + (a1 - a0) * i / n
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def _kind_broadcast(
    backend: Backend, cx: float, cy: float, s: float, color: str
) -> None:
    """User Event: a dot with 2 concentric arcs curving up-right — a
    broadcast/radar mark (an event is "announced" outward)."""
    ox, oy = cx - s * 0.3, cy + s * 0.3  # the dot sits at the lower-left
    backend.circle(ox, oy, s * 0.12, fill=color, stroke="none")
    for i, r in enumerate((s * 0.45, s * 0.75)):
        backend.path(
            _arc_points(ox, oy, r, -math.pi * 0.5, 0.0),
            stroke=color, stroke_width=1.0,
        )


def _kind_queue(backend: Backend, cx: float, cy: float, s: float, color: str) -> None:
    """Queue: 3 small stacked bars (items waiting in line, FIFO)."""
    w, h, gap = s * 0.8, s * 0.22, s * 0.12
    x1 = cx - w / 2
    top = cy - (3 * h + 2 * gap) / 2
    for i in range(3):
        y = top + i * (h + gap)
        backend.rect(x1, y, x1 + w, y + h, fill="none", stroke=color, stroke_width=1.0)


def _kind_notifier(
    backend: Backend, cx: float, cy: float, s: float, color: str
) -> None:
    """Notifier: a small pennant flag on a pole (a one-shot "notice")."""
    pole_x = cx - s * 0.35
    backend.line(
        pole_x, cy - s * 0.5, pole_x, cy + s * 0.5, stroke=color, stroke_width=1.0
    )
    backend.polygon(
        [
            (pole_x, cy - s * 0.5), (pole_x + s * 0.6, cy - s * 0.22),
            (pole_x, cy + s * 0.05),
        ],
        fill="none", stroke=color, stroke_width=1.0,
    )


def _kind_menu(backend: Backend, cx: float, cy: float, s: float, color: str) -> None:
    """Menu: a small box with 2 divider lines (a dropdown-menu look)."""
    x1, y1, x2, y2 = cx - s * 0.4, cy - s * 0.4, cx + s * 0.4, cy + s * 0.4
    backend.rect(x1, y1, x2, y2, fill="none", stroke=color, stroke_width=1.0)
    for i in (1, 2):
        y = y1 + (y2 - y1) * i / 3
        backend.line(x1, y, x2, y, stroke=color, stroke_width=0.8)


def _kind_generic(backend: Backend, cx: float, cy: float, s: float, color: str) -> None:
    """Generic reference mark (VI/control/application refnum, or any kind
    without a dedicated symbol): a ring with a center dot."""
    backend.circle(cx, cy, s * 0.42, fill="none", stroke=color, stroke_width=1.0)
    backend.circle(cx, cy, s * 0.1, fill=color, stroke="none")


# Keyed by the real ``LVType.ref_type`` strings the parser records (see
# ``parser.conp_types._REFNUM_KIND`` / ``type_mapping``'s VCTP ``RefType``
# attribute — verified corpus values include "UserEvent", "Queue", "Menu",
# "LVObjCtl", "EventReg", "DataLog"). Any kind without its own entry —
# including "LVObjCtl" (VI/control/application refnums, the broadest
# catch-all) — draws the generic reference mark.
_KIND_SYMBOLS: dict[str, Callable[[Backend, float, float, float, str], None]] = {
    "UserEvent": _kind_broadcast,
    "Queue": _kind_queue,
    "NotifierRef": _kind_notifier,
    "Menu": _kind_menu,
}


@dataclass(frozen=True)
class RefnumGlyph:
    """``kind`` is the refnum's own ``LVType.ref_type`` (selects the KIND
    SYMBOL via ``_KIND_SYMBOLS``, generic fallback otherwise). ``terminal``
    is the payload TYPE display (see module docstring) — None for a refnum
    with no registered payload at all (draws the frame + kind symbol only).
    ``terminal_rect``, when given, is the EXPANDED payload's real heap
    placement as 0..1 FRACTIONS of this glyph's own box (``layout.
    RefnumPayload.offset``); None draws the default COMPACT corner-badge
    position instead."""

    kind: str | None
    border_color: str
    terminal: Glyph | None = None
    terminal_rect: Rect | None = None

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        cut = max(0.0, min(_CUT_MAX, w * _CUT_FRAC, h * _CUT_FRAC))
        self._draw_dogear(backend, bounds, cut, theme)
        sym = max(6.0, min(15.0, min(w, h) * 0.34))
        if w > sym + 6.0 and h > sym + 6.0 + cut:
            drawer = _KIND_SYMBOLS.get(self.kind or "", _kind_generic)
            drawer(
                backend, x1 + sym * 0.6 + 2.0, y1 + cut + sym * 0.6 + 2.0, sym,
                self.border_color,
            )
        self._draw_terminal(backend, bounds, theme)

    def _draw_dogear(
        self, backend: Backend, bounds: Rect, cut: float, theme: Theme
    ) -> None:
        x1, y1, x2, y2 = bounds
        backend.polygon(
            [(x1, y1), (x2 - cut, y1), (x2, y1 + cut), (x2, y2), (x1, y2)],
            fill=theme.const_fill, stroke=self.border_color, stroke_width=1.2,
        )
        if cut > 1.5:
            # The fold crease — a short diagonal just inside the cut corner,
            # suggesting a folded paper corner (the classic "dog-ear" cue).
            backend.line(
                x2 - cut, y1 + cut * 0.3, x2 - cut * 0.3, y1 + cut,
                stroke=self.border_color, stroke_width=0.9,
            )

    def _draw_terminal(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        if self.terminal is None:
            return
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        if self.terminal_rect is not None:
            fx1, fy1, fx2, fy2 = self.terminal_rect
            term = (x1 + fx1 * w, y1 + fy1 * h, x1 + fx2 * w, y1 + fy2 * h)
        else:
            bw = min(w - 4.0, max(16.0, w * 0.42))
            bh = min(h - 4.0, max(11.0, h * 0.26))
            if bw <= 2.0 or bh <= 2.0:
                return
            term = (x2 - 2.0 - bw, y2 - 2.0 - bh, x2 - 2.0, y2 - 2.0)
        tx1, ty1, tx2, ty2 = term
        if tx2 - tx1 > 1.0 and ty2 - ty1 > 1.0:
            self.terminal.draw(backend, term, theme)


@dataclass(frozen=True)
class TypeTerminalGlyph:
    """The COMPACT terminal's content: a small color-bordered box holding a
    type mnemonic (``style.type_repr`` — ``abc``/``TF``/``OBJ``/…), or an
    empty color-bordered box when the payload has no single-token mnemonic
    (e.g. a cluster payload whose heap geometry couldn't be resolved)."""

    text: str
    color: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill=theme.const_fill, stroke=self.color, stroke_width=1.0,
        )
        if self.text:
            size = min(6.0, (y2 - y1) - 2.0)
            if size > 0:
                backend.text(
                    (x1 + x2) / 2, (y1 + y2) / 2 + size * 0.35, self.text, size,
                    fill=self.color,
                )

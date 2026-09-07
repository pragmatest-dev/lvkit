"""``RefnumGlyph`` — every refnum control (queue / notifier / user event /
menu / VI-Server / control reference / …), data-typed or not: a clean-room
DOG-EAR reference frame (our own shape — LabVIEW's own icon is proprietary
artwork we never reproduce, but a folded-corner box reads as "this is a
reference", the same visual grammar LabVIEW itself uses), a small KIND
SYMBOL naming the refnum's ``LVType.ref_type`` — each verified against a
real LabVIEW source (the maintainer's reference images 57/58/59, or NI's
own public docs where no reference image was supplied — see each
``_kind_*`` drawer's own docstring for its source) — and a TERMINAL area
showing the registered payload's TYPE:

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

from collections.abc import Callable
from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import Glyph

_CUT_FRAC = 0.30  # dog-ear corner-cut, as a fraction of min(w, h)
_CUT_MAX = 10.0
# Below this box size, LabVIEW-style controls draw a distinct MINI form —
# just the type mark, no full chrome — rather than the SAME glyph scaled
# down (a real heap box this small can't legibly hold a dog-ear frame + a
# corner kind symbol + a separate terminal badge anyway; verified against
# GTR's real "menubar" field, 21x27, which is too small for the full form).
_MINI_MAX_W = 32.0
_MINI_MAX_H = 32.0


def _kind_user_event(
    backend: Backend, cx: float, cy: float, s: float, color: str
) -> None:
    """User Event: a GROUND RADAR DISH (a tilted parabolic dish on a post
    over a pedestal base) set in a pale-blue disc — verified against the
    maintainer's reference (img #65, a zoomed User Event glyph) and NI's
    "Generate User Event" icon. Clean-room VECTOR redraw of the shape, not
    NI raster; the disc/dish colors are the glyph's own (fixed), not the
    refnum wire ``color`` (which the reference shows is constant across
    payload types)."""
    r = s * 0.48
    disc = "#5cc2ef"  # pale-blue disc background
    navy = "#16299a"  # the dish
    edge = "#2f7fa0"  # disc rim
    sw = max(0.9, s * 0.1)
    backend.circle(cx, cy, r, fill=disc, stroke=edge, stroke_width=max(0.6, s * 0.05))
    u = r * 0.86
    # pedestal base (trapezoid, wide at the bottom)
    backend.polygon(
        [
            (cx - 0.32 * u, cy + 0.72 * u),
            (cx + 0.32 * u, cy + 0.72 * u),
            (cx + 0.15 * u, cy + 0.40 * u),
            (cx - 0.15 * u, cy + 0.40 * u),
        ],
        fill=navy,
        stroke="none",
    )
    # post rising from the base to the dish mount
    backend.line(cx, cy + 0.40 * u, cx, cy - 0.02 * u, stroke=navy, stroke_width=sw)
    # dish reflector: a shallow tilted bowl opening up-right (polyline arc)
    backend.path(
        [
            (cx - 0.58 * u, cy + 0.04 * u),
            (cx - 0.52 * u, cy - 0.40 * u),
            (cx - 0.18 * u, cy - 0.62 * u),
            (cx + 0.26 * u, cy - 0.52 * u),
        ],
        stroke=navy,
        stroke_width=sw,
        fill="none",
    )
    # feed at the dish focus
    backend.circle(cx - 0.10 * u, cy - 0.24 * u, max(0.6, u * 0.11), fill=navy,
                   stroke="none")


def _kind_queue(backend: Backend, cx: float, cy: float, s: float, color: str) -> None:
    """Queue: a 3-COMPARTMENT COMB with a line entering left and an arrow
    exiting right — verified against NI's public docs (docs-be.ni.com's
    "Obtain Queue" function icon, fetched via
    unofficial-lvdocs.github.io/glang/creatque.gif, a mirror of the same
    NI-published page content): the real LabVIEW Queue pictograph is a
    3-slot rectangle (queued items in a line) with dataflow arrows either
    side, not a stack of separate bars."""
    w, h = s * 0.6, s * 0.42
    x1, x2 = cx - w / 2, cx + w / 2
    y1, y2 = cy - h / 2, cy + h / 2
    backend.rect(x1, y1, x2, y2, fill="none", stroke=color, stroke_width=1.0)
    for i in (1, 2):
        x = x1 + (x2 - x1) * i / 3
        backend.line(x, y1, x, y2, stroke=color, stroke_width=1.0)
    backend.line(x1 - s * 0.18, cy, x1, cy, stroke=color, stroke_width=1.0)
    ah = s * 0.1
    backend.polygon(
        [
            (x2 + s * 0.2, cy), (x2, cy - ah), (x2, cy + ah),
        ],
        fill=color, stroke="none",
    )


def _kind_notifier(
    backend: Backend, cx: float, cy: float, s: float, color: str
) -> None:
    """Notifier: an EXCLAMATION MARK IN A RING — verified against NI's
    public docs ("Obtain Notifier" function icon, unofficial-lvdocs.github.
    io/glang/creatnot.gif, a mirror of NI-published content): the real
    LabVIEW Notifier pictograph is a circled "!" (a one-shot alert), not a
    flag on a pole."""
    r = s * 0.4
    backend.circle(cx, cy, r, fill="none", stroke=color, stroke_width=1.0)
    backend.line(cx, cy - r * 0.5, cx, cy + r * 0.05, stroke=color, stroke_width=1.2)
    backend.circle(cx, cy + r * 0.42, r * 0.1, fill=color, stroke="none")


def _kind_menu(backend: Backend, cx: float, cy: float, s: float, color: str) -> None:
    """Menu: 3 short horizontal bars of varying width (a menu-item LIST) —
    verified against NI's public docs (the "Types of Refnum Controls" page's
    Menu Refnum icon, unofficial-lvdocs.github.io/lvhowto/
    noloc_env_menuref.gif, a mirror of NI-published content): the real
    LabVIEW Menu-refnum icon shows stacked horizontal list rows, no
    enclosing box."""
    w = s * 0.7
    x1 = cx - w / 2
    widths = (1.0, 0.85, 0.6)
    top = cy - s * 0.28
    for i, frac in enumerate(widths):
        y = top + i * s * 0.28
        backend.line(x1, y, x1 + w * frac, y, stroke=color, stroke_width=1.3)


def _kind_generic(backend: Backend, cx: float, cy: float, s: float, color: str) -> None:
    """Generic reference mark (VI Server / application / control refnum, or
    any kind without a dedicated symbol) — verified against NI's public
    docs (the "Types of Refnum Controls" page's VI Refnum icon,
    unofficial-lvdocs.github.io/lvhowto/noloc_env_viref.gif, a mirror of
    NI-published content): a real LabVIEW VI-reference icon is a 2x2 grid
    with one cell highlighted. Simplified here to a 2x2 grid of small
    squares with one filled."""
    half = s * 0.32
    gap = s * 0.08
    cell = half - gap / 2
    for i, (dx, dy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
        x0 = cx + dx * gap / 2 + (0 if dx > 0 else -cell)
        y0 = cy + dy * gap / 2 + (0 if dy > 0 else -cell)
        backend.rect(
            x0, y0, x0 + cell, y0 + cell,
            fill=color if i == 0 else "none", stroke=color, stroke_width=0.9,
        )


# Keyed by the real ``LVType.ref_type`` strings the parser records (see
# ``parser.conp_types._REFNUM_KIND`` / ``type_mapping``'s VCTP ``RefType``
# attribute — verified corpus values include "UserEvent", "Queue", "Menu",
# "LVObjCtl", "EventReg", "DataLog"). Any kind without its own entry —
# including "LVObjCtl" (VI/control/application refnums, the broadest
# catch-all) — draws the generic reference mark.
_KIND_SYMBOLS: dict[str, Callable[[Backend, float, float, float, str], None]] = {
    "UserEvent": _kind_user_event,
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
        if w < _MINI_MAX_W or h < _MINI_MAX_H:
            self._draw_mini(backend, bounds, theme)
            return
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

    def _draw_mini(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """The MINI form (see ``_MINI_MAX_W``/``_MINI_MAX_H``): a plain
        bordered box (no dog-ear cut, no terminal — there's no room to draw
        either legibly) holding just the kind symbol, scaled to nearly fill
        the box — LabVIEW's own small-size control behavior, not this
        glyph's full form shrunk."""
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill=theme.const_fill, stroke=self.border_color,
            stroke_width=1.0,
        )
        w, h = x2 - x1, y2 - y1
        sym = max(4.0, min(w, h) * 0.72)
        drawer = _KIND_SYMBOLS.get(self.kind or "", _kind_generic)
        drawer(backend, (x1 + x2) / 2, (y1 + y2) / 2, sym, self.border_color)

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
    """The COMPACT terminal's content: a small box holding a type mnemonic
    (``style.type_repr`` — ``abc``/``TF``/``OBJ``/…), bordered DASHED in the
    FIXED ``theme.refnum_terminal_border`` pink — verified directly against
    the maintainer's reference images (57/58/59): a string payload's "abc"
    box and a class payload's "OBJ" box draw the SAME dashed pink border, so
    this is fixed terminal chrome, never colored by the payload's own wire
    color. ``color`` (the payload's own wire color) still tints the text
    itself, for a scalar-type reading cue text alone can't carry."""

    text: str
    color: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill=theme.const_fill,
            stroke=theme.refnum_terminal_border, stroke_width=1.0,
            stroke_dasharray="2,1.5",
        )
        if self.text:
            size = min(6.0, (y2 - y1) - 2.0)
            if size > 0:
                backend.text(
                    (x1 + x2) / 2, (y1 + y2) / 2 + size * 0.35, self.text, size,
                    fill=self.color,
                )

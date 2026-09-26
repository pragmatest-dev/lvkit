"""What every front-panel control glyph shares: reading a control's heap parts,
and the optional ``value_bounds`` hook a glyph uses when its drawn box is
smaller than its heap ``bounds``.
"""

from __future__ import annotations

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl, ParsedFPPart
from ...glyph import Glyph

# objFlags bit 0x8 = hidden -- the same convention ``layout._field_label_hidden``
# reads for a cluster field's label part.
HIDDEN_FLAG_BIT = 0x8


class ControlGlyph:
    """Base for a front-panel control glyph whose drawn box is not its whole
    heap ``bounds``. ``value_bounds`` maps the control's full heap box (what a
    top-level control is placed at) to the box the glyph actually draws into
    (what a cluster hands a nested field). The default is the whole box."""

    def value_bounds(self, bounds: Rect) -> Rect:
        return bounds


def value_bounds(glyph: Glyph, bounds: Rect) -> Rect:
    """``bounds`` narrowed to ``glyph``'s drawn box, when it declares one."""
    if isinstance(glyph, ControlGlyph):
        return glyph.value_bounds(bounds)
    return bounds


def control_value_bounds(ctrl: ParsedFPControl, glyph: Glyph, bounds: Rect) -> Rect:
    """The box ``glyph`` draws into for a top-level ``ctrl`` placed at the
    full heap box ``bounds``. A glyph that narrows itself does; every other
    glyph gets the control's value extent -- the union of its non-label parts --
    so it never paints over the label part inside its heap box."""
    if isinstance(glyph, ControlGlyph):
        return glyph.value_bounds(bounds)
    ex1, ey1, ex2, ey2 = value_extent(ctrl)
    return (bounds[0] + ex1, bounds[1] + ey1, bounds[0] + ex2, bounds[1] + ey2)


def part_rect_of(part: ParsedFPPart) -> Rect:
    """A heap part's rect: ``ParsedFPPart.bounds`` is ``(top, left, bottom,
    right)``, ``Rect`` is ``(x1, y1, x2, y2)`` = ``(left, top, right, bottom)``."""
    top, left, bottom, right = part.bounds
    return (float(left), float(top), float(right), float(bottom))


def find_part(ctrl: ParsedFPControl, part_id: int) -> ParsedFPPart | None:
    return next((p for p in ctrl.parts if p.part_id == part_id), None)


def part_rect(ctrl: ParsedFPControl, part_id: int) -> Rect | None:
    """The control-local rect of the ``partID`` part; ``None`` when this
    control's heap carries no such part."""
    part = find_part(ctrl, part_id)
    return part_rect_of(part) if part is not None else None


def part_is_hidden(part: ParsedFPPart) -> bool:
    """True when ``part`` is flagged hidden (objFlags bit 0x8)."""
    try:
        flags = int(part.props.get("objFlags", "0"))
    except ValueError:
        return True
    return bool(flags & HIDDEN_FLAG_BIT)


def part_hidden(ctrl: ParsedFPControl, part_id: int) -> bool:
    """True when the ``partID`` part is flagged hidden, or absent."""
    part = find_part(ctrl, part_id)
    return True if part is None else part_is_hidden(part)


def value_extent(ctrl: ParsedFPControl) -> Rect:
    """The control-local box a cluster places this control into: the union of
    its non-label parts, clamped to the heap box -- ``layout._const_value_box``
    applied to the parsed parts. It excludes the caption strip (drawn
    separately) so a top-level control and a nested field share one box."""
    top, left, bottom, right = ctrl.bounds
    width, height = float(right - left), float(bottom - top)
    rects = [
        part_rect_of(p)
        for p in ctrl.parts
        if p.part_id is not None and p.part_class != "label"
    ]
    if not rects:
        return (0.0, 0.0, width, height)
    return (
        max(0.0, min(r[0] for r in rects)),
        max(0.0, min(r[1] for r in rects)),
        min(width, max(r[2] for r in rects)),
        min(height, max(r[3] for r in rects)),
    )


def local_part_rect(
    ctrl: ParsedFPControl, part_id: int, origin: tuple[float, float]
) -> Rect | None:
    """The ``partID`` part's rect relative to the value box's ``origin``."""
    part = find_part(ctrl, part_id)
    if part is None:
        return None
    x1, y1, x2, y2 = part_rect_of(part)
    ox, oy = origin
    return (x1 - ox, y1 - oy, x2 - ox, y2 - oy)


def value_box_bounds(
    bounds: Rect, origin: tuple[float, float], size: tuple[float, float]
) -> Rect:
    """The value box inside ``bounds`` (a control's full heap box)."""
    x1, y1, _x2, _y2 = bounds
    ox, oy = origin
    w, h = size
    return (x1 + ox, y1 + oy, x1 + ox + w, y1 + oy + h)

"""``SelectableStructureGlyph`` — the shared parent for the interactive frame
structures (case, stacked sequence, event, disable family). It owns the frame
SELECTOR chrome — the ``◄ value ▼ ►`` box, the dropdown menu, and the per-frame
value label — that LabVIEW draws on every structure the user can page through.

Glyphs stay PURE: the selector reads no scene/graph/layout. The composite
resolves the scene's frame data into a plain :class:`SelectorState` and injects
it, exactly the way ``border_color`` / ``dividers`` are injected today. A kind
that wants a decoration in the selector's icon zone (Type Specialization) sets
``has_selector_icon`` and overrides :meth:`draw_selector_icon`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...backend import Backend
from ...style import Theme
from ..nodes.base import fit_label
from .base import Rect, StructureBodyGlyph

# The selector sits INSIDE the top of the frame (heap termBounds are inside the
# bounds; there is no band above the top edge).
_CASE_BAR_H = 14.0
_SELECTOR_SIZE = 9.0  # font size of the value + arrows
_SELECTOR_TRI_W = 11.0  # width of the ▼ dropdown zone
_SELECTOR_ICON_W = 11.0  # type-icon zone (Type Specialization selector only)
_MENU_ROW_H = 13.0  # dropdown menu row height


@dataclass(frozen=True)
class SelectorState:
    """The scene-derived frame data a selector needs, resolved to plain values by
    the composite so the glyph stays pure. ``display`` maps each frame value to
    its selector text (a case's typed label, or a sequence's ``N [0..M]``)."""

    raw_uid: str
    values: list[str]
    default: str
    display: dict[str, str]


@dataclass(frozen=True)
class _SelectorGeom:
    """Laid-out pieces of a selector: ONE enclosing box holding, left to right, a
    ◄ arrow cell | the value (+ ▼ dropdown, + type icon for type-spec) | a ►
    arrow cell. Computed once from the measured frame-value widths."""

    outer: Rect
    box: Rect
    tri: Rect | None
    icon: Rect | None
    text_cx: float
    baseline: float
    left_x: float
    right_x: float


class SelectableStructureGlyph(StructureBodyGlyph):
    """A structure the user can page frames of. Inherits the body/border drawing
    from :class:`StructureBodyGlyph` and adds the selector chrome. Set
    ``has_selector_icon`` (Type Specialization only) to reserve + draw a
    type-icon in the value cell."""

    has_selector_icon: bool = False

    def selector_geom(
        self, bounds: Rect, state: SelectorState, backend: Backend
    ) -> _SelectorGeom:
        x1, y1, x2, _ = bounds
        max_val_w = max(
            (
                backend.measure_text(state.display[v], _SELECTOR_SIZE)
                for v in state.values
            ),
            default=10.0,
        )
        pad = 4.0
        tri_w = _SELECTOR_TRI_W
        icon_w = _SELECTOR_ICON_W if self.has_selector_icon else 0.0
        arrow_w = 13.0  # width of each flanking ◄ / ► arrow cell
        val_w = max(22.0, max_val_w + 2 * pad + tri_w + icon_w)
        total_w = min(arrow_w + val_w + arrow_w, (x2 - x1) - 6.0)
        cx = (x1 + x2) / 2
        ox1, ox2 = cx - total_w / 2, cx + total_w / 2
        oy1, oy2 = y1 + 1.0, y1 + _CASE_BAR_H - 1.0
        vc1, vc2 = ox1 + arrow_w, ox2 - arrow_w  # value-cell x range
        box = (vc1, oy1, vc2, oy2)
        icon = (vc1, oy1, vc1 + icon_w, oy2) if icon_w else None
        tri = (vc2 - tri_w, oy1, vc2, oy2)
        text_left = vc1 + icon_w
        text_right = vc2 - tri_w
        baseline = (oy1 + oy2) / 2 + _SELECTOR_SIZE * 0.34
        return _SelectorGeom(
            outer=(ox1, oy1, ox2, oy2),
            box=box,
            tri=tri,
            icon=icon,
            text_cx=(text_left + text_right) / 2,
            baseline=baseline,
            left_x=(ox1 + vc1) / 2,
            right_x=(vc2 + ox2) / 2,
        )

    def draw_selector_icon(self, backend: Backend, box: Rect, theme: Theme) -> None:
        """Optional decoration in the selector's icon zone. The base draws
        nothing; Type Specialization overrides this to stamp its type icon."""

    def draw_selector(
        self, backend: Backend, bounds: Rect, theme: Theme, state: SelectorState
    ) -> None:
        """The selector chrome as separate CLICK TARGETS: a ◄ prev arrow, the
        value box (a ▼ dropdown toggle carrying the frame list), and a ► next
        arrow. The dropdown MENU is drawn by :meth:`draw_menu`; the frame LABEL
        by :meth:`draw_value_label`."""
        if not state.values:
            return
        g = self.selector_geom(bounds, state, backend)
        ox1, oy1, ox2, oy2 = g.outer
        vc1, _, vc2, _ = g.box
        struct = state.raw_uid

        # One enclosing box (case-bar fill), with vertical dividers between the
        # flanking arrow cells and the central value cell.
        backend.rect(
            ox1, oy1, ox2, oy2, fill=theme.case_bar_fill,
            stroke=theme.struct_border, stroke_width=0.75,
        )
        backend.line(vc1, oy1, vc1, oy2, stroke=theme.struct_border, stroke_width=0.5)
        backend.line(vc2, oy1, vc2, oy2, stroke=theme.struct_border, stroke_width=0.5)

        if g.icon is not None:
            self.draw_selector_icon(backend, g.icon, theme)

        def _arrow(
            action: str, xc: float, glyph: str, cell: tuple[float, float]
        ) -> None:
            cx1, cx2 = cell
            backend.begin_group(
                cls="lv-selector lv-clickable",
                data={"lv-action": action, "lv-struct": struct},
            )
            backend.rect(cx1, oy1, cx2, oy2, fill="transparent", stroke="none")
            backend.text(
                xc, g.baseline, glyph, _SELECTOR_SIZE, fill=theme.case_bar_text
            )
            backend.end_group()

        _arrow("prev", g.left_x, "◄", (ox1, vc1))

        # Middle target — carries the frame list the JS controller reads, and
        # draws the ▼ dropdown toggle. The value TEXT is drawn per-frame by
        # draw_value_label.
        backend.begin_group(
            cls="lv-selector lv-clickable",
            data={
                "lv-struct": struct,
                "lv-frames": ";".join(state.values),
                "lv-default": state.default,
                "lv-action": "toggle",
            },
        )
        backend.rect(vc1, oy1, vc2, oy2, fill="transparent", stroke="none")
        if g.tri is not None:
            tx1, ty1, tx2, ty2 = g.tri
            backend.line(tx1, ty1, tx1, ty2, stroke="#cccccc", stroke_width=0.5)
            tcx, tcy = (tx1 + tx2) / 2, (ty1 + ty2) / 2
            backend.polygon(
                [(tcx - 3.0, tcy - 1.6), (tcx + 3.0, tcy - 1.6), (tcx, tcy + 2.2)],
                fill=theme.case_bar_text,
            )
        backend.end_group()

        _arrow("next", g.right_x, "►", (vc2, ox2))

    def draw_menu(
        self, backend: Backend, bounds: Rect, theme: Theme, state: SelectorState
    ) -> None:
        """The dropdown MENU: one clickable row per frame value, stacked below
        the value box, hidden until the ▼ toggle opens it. Drawn in a final
        topmost pass so it overlays the diagram."""
        if not state.values:
            return
        g = self.selector_geom(bounds, state, backend)
        bx1, _, bx2, by2 = g.box
        struct = state.raw_uid
        zone_w = (bx2 - bx1) - 6.0
        backend.begin_group(cls="lv-menu", data={"lv-struct": struct})
        for i, v in enumerate(state.values):
            ry1 = by2 + i * _MENU_ROW_H
            ry2 = ry1 + _MENU_ROW_H
            backend.begin_group(
                cls="lv-option lv-clickable",
                data={"lv-struct": struct, "lv-value": v},
            )
            backend.rect(
                bx1, ry1, bx2, ry2, fill=theme.case_bar_fill,
                stroke="#999999", stroke_width=0.5,
            )
            label = state.display[v]
            text = (
                label
                if backend.measure_text(label, _SELECTOR_SIZE) <= zone_w
                else fit_label(label, zone_w, backend, _SELECTOR_SIZE)
            )
            backend.text(
                (bx1 + bx2) / 2,
                ry1 + _MENU_ROW_H / 2 + _SELECTOR_SIZE * 0.34,
                text, _SELECTOR_SIZE, fill=theme.case_bar_text,
            )
            backend.end_group()
        backend.end_group()

    def draw_value_label(
        self,
        backend: Backend,
        bounds: Rect,
        theme: Theme,
        state: SelectorState,
        value: str,
    ) -> None:
        """The selected frame's label, centered in the selector's text zone (to
        the LEFT of the ▼, never under it or the arrows)."""
        g = self.selector_geom(bounds, state, backend)
        label = state.display[value]
        tri_w = (g.tri[2] - g.tri[0]) if g.tri is not None else 0.0
        zone_w = (g.box[2] - g.box[0]) - tri_w - 4.0
        text = (
            label
            if backend.measure_text(label, _SELECTOR_SIZE) <= zone_w
            else fit_label(label, zone_w, backend, _SELECTOR_SIZE)
        )
        backend.text(
            g.text_cx, g.baseline, text, _SELECTOR_SIZE, fill=theme.case_bar_text
        )

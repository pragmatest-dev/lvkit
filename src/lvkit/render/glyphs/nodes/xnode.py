from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...glyph import fit_label
from ...style import Theme
from .base import DrawerHeaderGlyphBase, _draw_drawer_row


@dataclass(frozen=True)
class XNodeGlyph(DrawerHeaderGlyphBase):
    """An FPGA Interface XNode (class="xNode", #107): Open/Close FPGA VI
    Reference, Read/Write Control, Invoke Method, etc. Reuses the Property/
    Invoke-node drawer convention -- a header above one row per terminal
    EXCLUDING the reference (Refnum-typed) and error (``is_error_cluster``)
    pass-through pair, which thread the box edges from their own real heap
    geometry, same as any Property/Invoke node's permDCOList pair -- never
    drawn here. See ``_xnode_glyph`` (render/nodes.py) for how
    ``rows``/``method``/``resource_name`` are built.

    ``resource_name`` is the node's own bound resource/module identifier
    (e.g. ``"Mod4"``, decoded from ``<StateData>``) -- LabVIEW's own real
    header text. ``""`` draws a BLANK header band: verified against the real
    corpus/screenshot that an "FPGA I/O Node" reading raw channels (its row
    names ARE the channel paths, e.g. "Mod1/AI0"/"Mod1/AI2") has NO header
    text at all -- never the generic ``class_name`` as a fallback, unlike
    Property/InvokeNodeGlyph's own ``"⚙ <class>"`` header.

    ``method`` (an "Invoke Method" node's specific invoked method, e.g.
    "Run", "Wait on IRQ") draws as an extra row directly under the header,
    with NO arrows -- it is selected internally, never wired, exactly like
    InvokeNodeGlyph's own method row. Empty for every other XNode class
    (Open/Close FPGA VI Reference, Read/Write Control have no such row). It
    has no real heap geometry of its own, so it always gets an equal share
    of the box rather than a real fraction.

    Each row's own ``(y1, y2)`` is a REAL fraction of the node's own height
    (from the heap's own ``termBounds`` -- see ``_xnode_glyph``), not an
    equal division: verified real row heights are NOT uniform (FIFO Write's
    Element/Timeout/Timed Out? rows are 15/15/14 units, with a 35-of-79-unit
    header gap before them, not an even quarter each) -- dividing evenly
    visibly misaligns a row's own drawn arrow against its real wire.
    ``(0.0, 0.0)`` (both zero) marks a row with no real geometry, which
    equal-shares whatever space remains after the real-fraction rows."""

    # LabVIEW draws an XNode in its own magenta/purple-on-white chrome, never
    # the tan prim_fill/prim_stroke every other drawer glyph shares (verified
    # against raph's real screenshot) -- overrides DrawerHeaderGlyphBase's
    # defaults. The header band itself is filled separately (xnode_header_fill,
    # a lighter tint) in ``draw()``; ``fill_attr`` is the ROW body only.
    fill_attr: str = "xnode_fill"
    stroke_attr: str = "xnode_stroke"
    resource_name: str = ""
    method: str = ""
    # (terminal label, show_left, show_right, (y1_frac, y2_frac))
    rows: tuple[tuple[str, bool, bool, tuple[float, float]], ...] = ()
    # Each row's own text color, by data type (LabVIEW's wire-color
    # convention -- verified: "Antenna Status" draws in Enum's blue, "UTC
    # Offset Valid" in Boolean's green) -- aligned by index with ``rows``.
    # "" (the header/method text's own neutral color) for a row with no type.
    row_colors: tuple[str, ...] = ()

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        # Fill first, stroke LAST (after the header-fill overlay below) --
        # drawing the border now and overlaying the header fill on top of it
        # would paint over half the border's stroke width along the header's
        # own top/left/right edges (an SVG stroke is centered on its path),
        # making the header look unbordered while the rows below stayed
        # crisp (verified bug, reported directly against this render).
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke="none",
        )
        rows = self.rows
        height = y2 - y1
        extra = 1 if self.method else 0
        equal_share = height / (len(rows) + 1 + extra)
        lsize = max(5.0, min(9.0, equal_share * 0.62) - 1.0)

        # The header's real height: the gap up to the first row with real
        # geometry -- not an equal slice (see class docstring). When a method
        # row is ALSO present, it shares this same real gap with the header
        # (split evenly) rather than adding its own equal_share on top of
        # it -- the real per-row fractions below are measured from the
        # node's own total height and know nothing about a method row, so
        # adding extra space before them would overlap the first real row
        # (verified bug: FPGA_v1.vi's "FIFO Write" node drew "Write" and
        # "Element" on top of each other before this fix).
        real_starts = [frac[0] * height for *_, frac in rows if frac != (0.0, 0.0)]
        has_real = bool(real_starts)
        total_gap = min(real_starts) if has_real else equal_share
        header_h = total_gap / 2 if self.method and has_real else total_gap
        method_h = total_gap - header_h if self.method and has_real else equal_share
        hy2 = y1 + header_h
        backend.rect(
            x1, y1, x2, hy2, fill=theme.xnode_header_fill, stroke="none",
        )
        backend.line(x1, hy2, x2, hy2, stroke=stroke, stroke_width=1.0)
        if self.resource_name:
            cy = y1 + header_h / 2
            # A small leading arrow glyph -- LabVIEW marks a resource-bound
            # header this way (verified: raph's real screenshot shows one
            # before "Mod4"/"Raw data to RT", never before a blank header) --
            # a plain clean-room triangle, not NI's own icon artwork.
            arrow_w = min(lsize * 0.7, (x2 - x1) * 0.08)
            ax = x1 + 6.0
            backend.path(
                [
                    (ax, cy - arrow_w * 0.55),
                    (ax + arrow_w, cy),
                    (ax, cy + arrow_w * 0.55),
                ],
                stroke="none",
                stroke_width=0.0,
                fill=text_fill,
            )
            backend.text(
                (x1 + x2) / 2,
                cy + lsize * 0.34,
                fit_label(self.resource_name, (x2 - x1) - 8.0, backend, lsize),
                lsize,
                fill=text_fill,
            )

        if self.method:
            my2 = hy2 + method_h
            _draw_drawer_row(
                backend,
                x1,
                x2,
                hy2,
                my2,
                self.method,
                show_left=False,
                show_right=False,
                text_fill=text_fill,
                lsize=lsize,
            )
            backend.line(x1, my2, x2, my2, stroke=stroke, stroke_width=1.0)
            hy2 = my2

        prev_y2 = hy2
        for i, (label, show_left, show_right, frac) in enumerate(rows):
            if frac != (0.0, 0.0):
                ry1, ry2 = y1 + frac[0] * height, y1 + frac[1] * height
            else:
                ry1, ry2 = prev_y2, prev_y2 + equal_share
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=1.0)
            row_color = (
                self.row_colors[i]
                if i < len(self.row_colors) and self.row_colors[i]
                else text_fill
            )
            _draw_drawer_row(
                backend,
                x1,
                x2,
                ry1,
                ry2,
                label,
                show_left=show_left,
                show_right=show_right,
                text_fill=row_color,
                lsize=lsize,
            )
            prev_y2 = ry2

        # The outer border, drawn LAST so it's never covered by the header
        # fill (see the comment on the body fill above) -- the SAME stroke
        # width/color the row dividers use, so the header's own outline
        # matches the terminals' outline exactly.
        backend.rect(x1, y1, x2, y2, fill="none", stroke=stroke, stroke_width=1.2)

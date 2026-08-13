"""Render a VI's connector pane as an SVG — the faithful LabVIEW pattern grid,
each occupied cell colored by its terminal's LabVIEW type and labeled with the
control name.

The grid geometry comes from :mod:`connector_pane_geometry` (the clean-room
pattern table); this module only paints it. It is PURE — it takes the pattern
id and a list of :class:`PaneTerminal` (index + type + direction + wiring rule)
and returns an ``<svg>`` fragment. The graph adapter
(:func:`pane_terminals_for_vi`) is what pulls those out of a loaded VI.

Both homes for the pane use this one renderer: the standalone single-VI view,
and the before/after diff (pass ``ring`` = the set of changed terminal indices
to highlight them).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..models import FPTerminal, LVType, Terminal
from .connector_pane_geometry import PaneCell, get_pattern
from .style import DEFAULT_THEME, Theme, lv_type_label, wire_style

__all__ = [
    "PaneTerminal",
    "pane_terminals",
    "render_connector_pane",
    "render_connector_pane_compact",
    "render_connector_pane_diff",
    "render_connector_pane_help",
]

# Cell sizing (px). Cells are laid out in a normalized unit square by the
# geometry loader; these scale it to something with room for a name + type.
_COL_W = 116
_ROW_H = 34
_PAD = 6  # outer margin around the whole pane (room for direction stubs)

# Wiring-rule -> border stroke width. LabVIEW draws a REQUIRED terminal bold, a
# recommended one plain, an optional one thin. (0 unknown, 1 required,
# 2 recommended, 3 optional, 4 dynamic-dispatch — see ParsedWiringRule.)
_RULE_WIDTH = {1: 2.6, 2: 1.5, 3: 0.8, 4: 2.6, 0: 1.2}
_RULE_TITLE = {
    1: "required", 2: "recommended", 3: "optional", 4: "dynamic dispatch",
    0: "connected",
}


@dataclass(frozen=True)
class PaneTerminal:
    """One wired connector-pane terminal of a VI, keyed by its slot index."""

    index: int
    name: str | None
    lv_type: LVType | None
    is_output: bool
    wiring_rule: int = 0


def pane_terminals(terminals: Iterable[Terminal]) -> list[PaneTerminal]:
    """Adapt a VI's connector-pane ``Terminal``s (``VIContext.inputs +
    .outputs`` — already filtered to public FP terminals) to ``PaneTerminal``,
    the render layer's slot-keyed view. Keeps this module free of any graph
    dependency."""
    out: list[PaneTerminal] = []
    for t in terminals:
        out.append(PaneTerminal(
            index=t.index,
            name=t.name,
            lv_type=t.lv_type,
            is_output=t.direction == "output",
            wiring_rule=t.wiring_rule if isinstance(t, FPTerminal) else 0,
        ))
    return out


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _tint(hex_color: str, amount: float) -> str:
    """Mix ``hex_color`` toward white by ``amount`` in [0,1] (1 => white)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16)
        out.append(int(c + (255 - c) * amount))
    return "#" + "".join(f"{c:02x}" for c in out)


def _truncate(text: str, cell_w: float, *, px_per_char: float = 6.4) -> str:
    limit = max(1, int((cell_w - 8) / px_per_char))
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _tooltip(term: PaneTerminal) -> str:
    return (
        f'<title>{_esc(term.name or "?")} : {_esc(lv_type_label(term.lv_type))}'
        f' ({_RULE_TITLE.get(term.wiring_rule, "connected")},'
        f' {"output" if term.is_output else "input"}, idx {term.index})</title>'
    )


def _cell_svg_compact(
    cell: PaneCell, term: PaneTerminal | None, W: float, H: float,
    theme: Theme, ringed: bool,
) -> list[str]:
    """One cell at ICON size — the faithful LabVIEW pane face: solid type color,
    no labels (the terminal name/type is a hover <title>), each cell keeping its
    true pattern shape (square, tall, or column-spanning). Empty slots faint."""
    x, y = cell.x * W, cell.y * H
    w, h = cell.w * W, cell.h * H
    if term is None:
        return [
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{theme.canvas}" stroke="{theme.subvi_stroke}" '
            f'stroke-width="0.4" opacity="0.5"/>'
        ]
    color = wire_style(term.lv_type, theme).color
    parts = [
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
        f'fill="{color}" stroke="{theme.struct_border}" stroke-width="0.4">'
        f'{_tooltip(term)}</rect>'
    ]
    if ringed:
        parts.append(
            f'<rect x="{x - 1:.2f}" y="{y - 1:.2f}" width="{w + 2:.2f}" '
            f'height="{h + 2:.2f}" fill="none" stroke="{theme.coercion_dot}" '
            f'stroke-width="1.4"/>'
        )
    return parts


def _cell_svg(
    cell: PaneCell,
    term: PaneTerminal | None,
    W: float,
    H: float,
    theme: Theme,
    ringed: bool,
) -> list[str]:
    x, y = _PAD + cell.x * W, _PAD + cell.y * H
    w, h = cell.w * W, cell.h * H
    parts: list[str] = []

    if term is None:
        # An empty slot the pattern offers but this VI doesn't wire.
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="2" fill="{theme.canvas}" stroke="{theme.subvi_stroke}" '
            f'stroke-width="0.8" stroke-dasharray="2 2" opacity="0.6"/>'
        )
        return parts

    color = wire_style(term.lv_type, theme).color
    fill = _tint(color, 0.72)
    text_color = theme.text
    width = _RULE_WIDTH.get(term.wiring_rule, 1.2)
    parts.append(
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="2" '
        f'fill="{fill}" stroke="{color}" stroke-width="{width}">'
        f'{_tooltip(term)}</rect>'
    )
    # Full-height accent bar in the true type color on the wire-entry edge
    # (left for an input, right for an output).
    bar_w = 3.0
    bar_x = x if not term.is_output else x + w - bar_w
    parts.append(
        f'<rect x="{bar_x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
        f'height="{h:.1f}" fill="{color}"/>'
    )
    # Name (top) + type (bottom), left-aligned past the accent bar.
    tx = x + bar_w + 4
    name = _truncate(term.name or f"idx {term.index}", w - bar_w)
    type_label = _truncate(lv_type_label(term.lv_type), w - bar_w)
    if h >= 26:
        parts.append(
            f'<text x="{tx:.1f}" y="{y + h / 2 - 2:.1f}" font-size="11" '
            f'fill="{text_color}" font-family="sans-serif">{_esc(name)}</text>'
        )
        parts.append(
            f'<text x="{tx:.1f}" y="{y + h / 2 + 11:.1f}" font-size="9" '
            f'fill="{theme.pane_type_text}" font-family="sans-serif">'
            f'{_esc(type_label)}</text>'
        )
    else:
        parts.append(
            f'<text x="{tx:.1f}" y="{y + h / 2 + 3.5:.1f}" font-size="10" '
            f'fill="{text_color}" font-family="sans-serif">{_esc(name)}</text>'
        )
    if ringed:
        parts.append(
            f'<rect x="{x - 1.5:.1f}" y="{y - 1.5:.1f}" width="{w + 3:.1f}" '
            f'height="{h + 3:.1f}" rx="3" fill="none" stroke="{theme.coercion_dot}"'
            f' stroke-width="2.2"/>'
        )
    return parts


def render_connector_pane(
    pattern_id: int | None,
    terminals: list[PaneTerminal],
    *,
    theme: Theme = DEFAULT_THEME,
    ring: frozenset[int] = frozenset(),
) -> str:
    """Render the connector pane for ``pattern_id`` with ``terminals`` placed by
    slot index. ``ring`` highlights the given indices (diff use). Returns a
    self-contained ``<svg>`` element. Falls back to a plain input/output column
    layout when the pattern id is unknown or absent, so a VI always renders."""
    by_index = {t.index: t for t in terminals}
    pattern = get_pattern(pattern_id) if pattern_id is not None else None
    if pattern is None:
        return _fallback_svg(pattern_id, terminals, theme=theme, ring=ring)

    W = pattern.cols * _COL_W
    H = pattern.rows * _ROW_H
    body: list[str] = []
    for cell in pattern.cells:
        body.extend(
            _cell_svg(cell, by_index.get(cell.index), W, H, theme, cell.index in ring)
        )
    svg_w, svg_h = W + 2 * _PAD, H + 2 * _PAD
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w:.0f}" '
        f'height="{svg_h:.0f}" viewBox="0 0 {svg_w:.0f} {svg_h:.0f}" '
        f'font-family="sans-serif">'
        f'<rect x="{_PAD - 2}" y="{_PAD - 2}" width="{W + 4:.0f}" '
        f'height="{H + 4:.0f}" fill="none" stroke="{theme.struct_border}" '
        f'stroke-width="1.4"/>'
        + "".join(body)
        + "</svg>"
    )


def render_connector_pane_compact(
    pattern_id: int | None,
    terminals: list[PaneTerminal],
    *,
    theme: Theme = DEFAULT_THEME,
    ring: frozenset[int] = frozenset(),
    size: float = 40.0,
    height: float | None = None,
) -> str:
    """The ICON-SIZED pane face — the faithful LabVIEW connector pane as shown
    beside the icon: a small ``size``×``height`` colored grid (``height``
    defaults to ``size``), each cell in its true pattern shape (square / tall /
    column-spanning), filled by terminal type, name/type on hover (``<title>``).
    No text labels. ``ring`` highlights changed cells (diff). Unknown conId → a
    minimal box so it never breaks."""
    h = height if height is not None else size
    by_index = {t.index: t for t in terminals}
    pattern = get_pattern(pattern_id) if pattern_id is not None else None
    inner = f'<rect width="{size:.1f}" height="{h:.1f}" fill="{theme.canvas}"/>'
    if pattern is not None:
        parts = [inner]
        for cell in pattern.cells:
            parts.extend(
                _cell_svg_compact(
                    cell, by_index.get(cell.index), size, h, theme,
                    cell.index in ring,
                )
            )
        inner = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size:.0f}" '
        f'height="{h:.0f}" viewBox="0 0 {size:.0f} {h:.0f}">'
        f'{inner}'
        f'<rect x="0" y="0" width="{size:.1f}" height="{h:.1f}" fill="none" '
        f'stroke="{theme.struct_border}" stroke-width="1"/>'
        f'</svg>'
    )


def _tw(s: str, size: float) -> float:
    """Rough proportional text width (no backend measure in this string
    builder) — sans-serif averages ~0.6em/char, enough to size the panel."""
    return len(s) * size * 0.6


def _wrap(text: str, width: float, size: float, max_lines: int) -> list[str]:
    out: list[str] = []
    line = ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if _tw(trial, size) <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
            if len(out) == max_lines - 1:
                break
    if line and len(out) < max_lines:
        out.append(line)
    if len(text.split()) and out and _tw(out[-1], size) > width:
        out[-1] = out[-1][: max(1, int(width / (size * 0.55)) - 1)] + "…"
    return out


_HELP_TITLE = 12.0
_HELP_DESC = 9.5
_HELP_DESC_LH = 12.0
_HELP_LABEL = 11.0
_HELP_TYPE = 9.0
_HELP_PANE = 76.0
_HELP_LEADER = 26.0
_HELP_PAD = 12.0
_HELP_TEXTGAP = 7.0
_HELP_ICON = 40.0  # bigger, with title + description to its right


def render_connector_pane_help(
    pattern_id: int | None,
    terminals: list[PaneTerminal],
    *,
    title: str,
    description: str | None = None,
    icon_uri: str | None = None,
    theme: Theme = DEFAULT_THEME,
) -> str:
    """A Context-Help-style panel: the connector-pane grid centered, each wired
    terminal's NAME + type on a type-colored leader stub out to the side (inputs
    left, outputs right), with the VI icon + title and (wrapped) description
    above — the way LabVIEW's Context Help window presents a VI. Unknown conId
    falls back to the labeled column pane."""
    pattern = get_pattern(pattern_id) if pattern_id is not None else None
    if pattern is None:
        return render_connector_pane(pattern_id, terminals, theme=theme)
    cx = pattern.cell_by_index()

    def _side(is_out: bool) -> list[tuple[PaneCell, PaneTerminal]]:
        rows = [
            (cx[t.index], t) for t in terminals
            if t.is_output == is_out and t.index in cx
        ]
        return sorted(rows, key=lambda ct: ct[0].y + ct[0].h / 2)

    ins, outs = _side(False), _side(True)

    def _classify(
        rows: list[tuple[PaneCell, PaneTerminal]], is_out: bool
    ) -> tuple[list, list, list]:
        """Split one side into (inner_top, outer, inner_bot). OUTER = the column
        touching the pane's edge (straight leaders); INNER = the middle column,
        whose cells route up-and-over / down-and-under so their leaders never
        cross the outer ones — inner cells above the outer block go to the TOP
        stack, below it to the BOTTOM stack."""
        if not rows:
            return ([], [], [])
        if is_out:
            key = max(c.x + c.w for c, _ in rows)
            outer = [r for r in rows if abs((r[0].x + r[0].w) - key) < 1e-6]
        else:
            key = min(c.x for c, _ in rows)
            outer = [r for r in rows if abs(r[0].x - key) < 1e-6]
        outer_ids = {id(r) for r in outer}
        inner = [r for r in rows if id(r) not in outer_ids]
        outer.sort(key=lambda r: r[0].y + r[0].h / 2)
        oc = (
            sum(r[0].y + r[0].h / 2 for r in outer) / len(outer) if outer else 0.5
        )
        cy = lambda r: r[0].y + r[0].h / 2  # noqa: E731
        it = sorted([r for r in inner if cy(r) <= oc], key=cy)
        ib = sorted([r for r in inner if cy(r) > oc], key=cy)
        return (it, outer, ib)

    def _label_w(rows: list[tuple[PaneCell, PaneTerminal]]) -> float:
        # one line: name + a grey type appended — width is name + gap + type.
        w = 0.0
        for _, t in rows:
            w = max(w, _tw((t.name or f"idx {t.index}").strip(), _HELP_LABEL)
                    + _HELP_TEXTGAP + _tw(lv_type_label(t.lv_type).strip(),
                                          _HELP_TYPE))
        return w

    left_w, right_w = _label_w(ins), _label_w(outs)
    diagram_w = (
        (left_w + _HELP_LEADER if ins else 0) + _HELP_PANE
        + (_HELP_LEADER + right_w if outs else 0)
    )
    inner_w = max(diagram_w, 200.0)
    # Header is CENTERED and STACKED: icon on top, then title, then description.
    wrap_w = inner_w - 8
    title_line = title if _tw(title, _HELP_TITLE) <= wrap_w else (
        title[: max(1, int(wrap_w / (_HELP_TITLE * 0.6)) - 1)] + "…"
    )
    desc_lines = _wrap(description, wrap_w, _HELP_DESC, 4) if description else []
    icon_bottom = _HELP_PAD + (_HELP_ICON if icon_uri else 0.0)
    title_y = icon_bottom + _HELP_TITLE + (6 if icon_uri else 0.0)
    desc_y0 = title_y + _HELP_DESC + 6
    header_h = (
        desc_y0 + (len(desc_lines) - 1) * _HELP_DESC_LH + 6 if desc_lines
        else title_y + 6
    )
    left = _classify(ins, False)
    right = _classify(outs, True)
    # Keep the pane its natural square size; labels are a SINGLE line (name),
    # with the type on hover — so outer labels align with their terminals (short
    # cell spacing is enough) and leaders stay straight, no vertical stretch.
    pane_w = pane_h = _HELP_PANE
    top_n = max(len(left[0]), len(right[0]))
    bot_n = max(len(left[2]), len(right[2]))
    # Inner (middle-column) labels stack above/below the pane on the SAME vertical
    # rhythm as the outer cells (pane height / row count), so the whole side reads
    # as ONE evenly-spaced column — the top/bottom folded labels sit exactly one
    # cell-row past the pane edge, never floating far above/below it.
    row_h = pane_h / max(1, pattern.rows)
    top_margin = top_n * row_h
    bot_margin = bot_n * row_h
    diagram_h = top_margin + pane_h + bot_margin
    panel_w = inner_w + 2 * _HELP_PAD
    panel_h = header_h + diagram_h + 2 * _HELP_PAD

    diagram_top = header_h + _HELP_PAD
    pane_x = _HELP_PAD + (inner_w - diagram_w) / 2 + (
        left_w + _HELP_LEADER if ins else 0
    )
    pane_y = diagram_top + top_margin

    hcx = panel_w / 2  # header is centered on the panel's horizontal centre
    parts = [
        f'<g class="lv-vi-help">'
        f'<rect x="0" y="0" width="{panel_w:.1f}" height="{panel_h:.1f}" rx="4" '
        f'fill="{theme.canvas}" stroke="{theme.struct_border}" stroke-width="1"/>'
    ]
    if icon_uri:
        parts.append(
            f'<image x="{hcx - _HELP_ICON / 2:.1f}" y="{_HELP_PAD:.1f}" '
            f'width="{_HELP_ICON}" height="{_HELP_ICON}" href="{icon_uri}"/>'
        )
    parts.append(
        f'<text x="{hcx:.1f}" y="{title_y:.1f}" text-anchor="middle" '
        f'font-size="{_HELP_TITLE}" font-weight="bold" font-family="sans-serif" '
        f'fill="{theme.text}">{_esc(title_line)}</text>'
    )
    for i, dl in enumerate(desc_lines):
        parts.append(
            f'<text x="{hcx:.1f}" y="{desc_y0 + i * _HELP_DESC_LH:.1f}" '
            f'text-anchor="middle" font-size="{_HELP_DESC}" '
            f'font-family="sans-serif" fill="{theme.pane_type_text}">'
            f'{_esc(dl)}</text>'
        )
    # The pane grid, nested at (pane_x, pane_y).
    grid = render_connector_pane_compact(
        pattern_id, terminals, theme=theme, size=pane_w, height=pane_h
    )
    parts.append(
        f'<g transform="translate({pane_x:.1f},{pane_y:.1f})">{grid}</g>'
    )

    def _wire_label(
        cell: PaneCell, t: PaneTerminal, label_y: float, is_out: bool, over: bool
    ) -> None:
        color = wire_style(t.lv_type, theme).color
        # Wires connect to the MIDDLE of the terminal cell.
        cxm = pane_x + (cell.x + cell.w / 2) * pane_w
        ccy = pane_y + (cell.y + cell.h / 2) * pane_h
        # Trim whitespace so right-justified names line up; the type is appended
        # to the END of the label in grey (like the connector-help tooltips).
        name = (t.name or f"idx {t.index}").strip()
        type_str = lv_type_label(t.lv_type).strip()
        ty = label_y + _HELP_LABEL * 0.34
        # NAME then grey TYPE as ONE text with a FIXED dx gap, justified by
        # text-anchor: controls (inputs) end at the leader on the right,
        # indicators (outputs) start at it on the left. One element (not two
        # estimate-placed ones) keeps the name<->type gap CONSTANT and the
        # wire-adjacent end exact. (cairosvg mis-anchors tspans; browsers don't.)
        anchor = "start" if is_out else "end"
        ax = (
            pane_x + pane_w + _HELP_LEADER + _HELP_TEXTGAP if is_out
            else pane_x - _HELP_LEADER - _HELP_TEXTGAP
        )
        hub = pane_x + pane_w + _HELP_LEADER if is_out else pane_x - _HELP_LEADER
        tspan = (
            f'<tspan dx="{_HELP_TEXTGAP:.0f}" font-size="{_HELP_TYPE}" '
            f'fill="{theme.pane_type_text}">{_esc(type_str)}</tspan>'
            if type_str else ""
        )
        parts.append(
            f'<text x="{ax:.1f}" y="{ty:.1f}" text-anchor="{anchor}" '
            f'font-size="{_HELP_LABEL}" font-family="sans-serif" '
            f'fill="{theme.text}">{_esc(name)}{tspan}</text>'
        )
        # OUTER cells (over=False): out from the centre then a bend to the label.
        # INNER cells (over=True): up/down FIRST along the cell's own centre-line,
        # then out over/under the outer block — leaders never cross.
        if over:
            path = (
                f'M{cxm:.1f},{ccy:.1f} L{cxm:.1f},{label_y:.1f} '
                f'L{hub:.1f},{label_y:.1f}'
            )
        else:
            path = (
                f'M{cxm:.1f},{ccy:.1f} L{hub:.1f},{ccy:.1f} '
                f'L{hub:.1f},{label_y:.1f}'
            )
        parts.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.4"/>'
        )

    def _place(side, is_out: bool) -> None:
        inner_top, outer, inner_bot = side
        # Outer labels sit ALIGNED with their terminal cell centre (the pane is
        # stretched so they don't collide) — straight, un-kinked leaders.
        for cell, t in outer:
            ccy = pane_y + (cell.y + cell.h / 2) * pane_h
            _wire_label(cell, t, ccy, is_out, over=False)
        # Inner (middle-column) labels continue the outer cell rhythm just past
        # the pane edge — the top stack rising above it, the bottom stack below —
        # routed over/under so nothing crosses.
        n_top = len(inner_top)
        for k, (cell, t) in enumerate(inner_top):
            _wire_label(cell, t, pane_y + row_h / 2 - (n_top - k) * row_h,
                        is_out, over=True)
        for k, (cell, t) in enumerate(inner_bot):
            _wire_label(cell, t, pane_y + pane_h - row_h / 2 + (k + 1) * row_h,
                        is_out, over=True)

    _place(left, False)
    _place(right, True)
    parts.append("</g>")
    inner = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{panel_w:.0f}" '
        f'height="{panel_h:.0f}" viewBox="0 0 {panel_w:.0f} {panel_h:.0f}">'
        f'{inner}</svg>'
    )


def _changed_names(
    before: list[PaneTerminal], after: list[PaneTerminal]
) -> set[str]:
    """Terminal names that were added, removed, or had their type change —
    the diff engine's own connector-pane rule (match by name; a type change is
    a modify), computed here on the two slot lists so the render is
    self-contained."""
    a = {t.name: t for t in before if t.name}
    b = {t.name: t for t in after if t.name}
    changed: set[str] = set(a) ^ set(b)  # added or removed
    for name in set(a) & set(b):
        ta, tb = a[name].lv_type, b[name].lv_type
        if lv_type_label(ta) != lv_type_label(tb):
            changed.add(name)
    return changed


def _ring_for(terms: list[PaneTerminal], names: set[str]) -> frozenset[int]:
    return frozenset(t.index for t in terms if t.name in names)


def render_connector_pane_diff(
    pattern_before: int | None,
    before: list[PaneTerminal],
    pattern_after: int | None,
    after: list[PaneTerminal],
    *,
    theme: Theme = DEFAULT_THEME,
) -> str:
    """Render BEFORE and AFTER panes side by side, each with its changed cells
    ringed. Changed = a terminal added / removed / retyped (matched by name).
    Returns one ``<svg>`` containing both panes with captions."""
    changed = _changed_names(before, after)
    left = render_connector_pane(
        pattern_before, before, theme=theme, ring=_ring_for(before, changed)
    )
    right = render_connector_pane(
        pattern_after, after, theme=theme, ring=_ring_for(after, changed)
    )
    lw, lh = _svg_dims(left)
    rw, rh = _svg_dims(right)
    gap, cap_h = 28, 20
    total_w = lw + gap + rw
    total_h = max(lh, rh) + cap_h
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w:.0f}" '
        f'height="{total_h:.0f}" viewBox="0 0 {total_w:.0f} {total_h:.0f}" '
        f'font-family="sans-serif">'
        f'<text x="0" y="13" font-size="12" font-weight="bold" '
        f'fill="{theme.text}">before</text>'
        f'<text x="{lw + gap:.0f}" y="13" font-size="12" font-weight="bold" '
        f'fill="{theme.text}">after</text>'
        f'<g transform="translate(0,{cap_h})">{_svg_inner(left)}</g>'
        f'<g transform="translate({lw + gap:.0f},{cap_h})">{_svg_inner(right)}</g>'
        "</svg>"
    )


def _svg_dims(svg: str) -> tuple[float, float]:
    w = re.search(r'width="([\d.]+)"', svg)
    h = re.search(r'height="([\d.]+)"', svg)
    return (float(w.group(1)) if w else 0.0, float(h.group(1)) if h else 0.0)


def _svg_inner(svg: str) -> str:
    """Strip the outer <svg ...> wrapper so the content can be re-nested in a
    <g>."""
    start = svg.index(">") + 1
    end = svg.rindex("</svg>")
    return svg[start:end]


def _fallback_svg(
    pattern_id: int | None,
    terminals: list[PaneTerminal],
    *,
    theme: Theme,
    ring: frozenset[int],
) -> str:
    """Unknown/absent pattern: lay inputs in a left column, outputs in a right
    column, ordered by index. Honest about the missing geometry."""
    inputs = sorted((t for t in terminals if not t.is_output), key=lambda t: t.index)
    outputs = sorted((t for t in terminals if t.is_output), key=lambda t: t.index)
    rows = max(len(inputs), len(outputs), 1)
    cols = 2 if (inputs and outputs) else 1
    W, H = cols * _COL_W, rows * _ROW_H
    body: list[str] = []

    def col(seq: list[PaneTerminal], cx: float) -> None:
        n = len(seq) or 1
        for i, t in enumerate(seq):
            cell = PaneCell(t.index, cx, i / n, 1 / cols, 1 / n)
            body.extend(_cell_svg(cell, t, W, H, theme, t.index in ring))

    if inputs:
        col(inputs, 0.0)
    if outputs:
        col(outputs, 1.0 - 1.0 / cols)
    svg_w, svg_h = W + 2 * _PAD, H + 2 * _PAD
    note = (
        f"conId {pattern_id} — no pattern geometry" if pattern_id
        else "no connector pane"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w:.0f}" '
        f'height="{svg_h:.0f}" viewBox="0 0 {svg_w:.0f} {svg_h:.0f}" '
        f'font-family="sans-serif"><title>{_esc(note)}</title>'
        + "".join(body)
        + "</svg>"
    )

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
    "render_connector_pane_diff",
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
        f'<title>{_esc(term.name or "?")} : {_esc(lv_type_label(term.lv_type))}'
        f' ({_RULE_TITLE.get(term.wiring_rule, "connected")},'
        f' {"output" if term.is_output else "input"}, idx {term.index})</title>'
        f'</rect>'
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

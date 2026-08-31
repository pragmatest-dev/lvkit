"""Shared foundation for the leaf node glyphs: the ``Glyph`` protocol plus the
text-fitting, split-box and node-tile helpers (and their tuning constants) that
several glyph classes share."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@runtime_checkable
class Glyph(Protocol):
    """A node visual. Scales to ``bounds`` — no intrinsic size."""

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None: ...


def fit_label(text: str, width: float, backend: Backend, size: float) -> str:
    """Truncate a label to fit ``width`` px, using the backend's own text
    measurement (not a fixed px/char heuristic — S7)."""
    if not text:
        return ""
    if backend.measure_text(text, size) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid] + "…"
        if backend.measure_text(candidate, size) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…" if lo else "…"


def _is_plain_decimal(s: str) -> bool:
    """``s`` is a plain decimal literal (optional sign, one dot) — the only
    shape ``fit_value`` sheds precision on. Excludes hex/octal (``xFF``),
    exponent, and paths (``a.vi``)."""
    body = s[1:] if s[:1] in "+-" else s
    return body.count(".") == 1 and body.replace(".", "").isdigit()


def fit_value(text: str, width: float, backend: Backend, size: float) -> str:
    """Fit a scalar VALUE to ``width``. A plain decimal number sheds trailing
    fractional digits to fit (``3.00`` → ``3.0`` → ``3``) rather than
    ellipsizing — an ellipsis is no narrower than a digit, so ``3…`` wastes the
    space. Anything else (or an integer part that still won't fit) falls back to
    the generic ellipsizing ``fit_label``."""
    if not text or backend.measure_text(text, size) <= width:
        return text
    if _is_plain_decimal(text):
        s = text
        while "." in s and backend.measure_text(s, size) > width:
            s = s[:-1]
        s = s.rstrip(".")
        if backend.measure_text(s, size) <= width:
            return s
        text = s
    return fit_label(text, width, backend, size)


def wrap_label(
    text: str,
    width: float,
    backend: Backend,
    size: float,
    max_lines: int,
) -> list[str]:
    """Greedy word-wrap ``text`` into at most ``max_lines`` lines that each fit
    ``width`` px at ``size`` (measured via the backend, not a px/char guess).
    A single word wider than the box is hard-broken across lines; anything past
    ``max_lines`` is dropped and the last kept line is ellipsized."""
    if not text or width <= 0 or max_lines <= 0:
        return []
    lines: list[str] = []
    cur = ""
    for word in text.split():
        # Hard-break a word too wide to ever fit on one line.
        while backend.measure_text(word, size) > width and len(word) > 1:
            i = len(word)
            while i > 1 and backend.measure_text(word[:i], size) > width:
                i -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:i])
            word = word[i:]
        trial = f"{cur} {word}".strip()
        if not cur or backend.measure_text(trial, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1]
    while last and backend.measure_text(last + "…", size) > width:
        last = last[:-1]
    lines[-1] = last + "…"
    return lines


_FIT_GAP = 1.3  # extra px between baselines beyond the font size


def fit_wrapped(
    label: str,
    avail_w: float,
    avail_h: float,
    backend: Backend,
    max_size: float,
    max_lines: int,
    min_size: float = 5.0,
) -> tuple[float, float, list[str]]:
    """Largest font (down to ``min_size``) at which the FULL ``label`` wraps into
    at most ``max_lines`` lines that fit ``avail_w`` x ``avail_h`` — so a short
    label stays big and one line, a long one shrinks and uses more lines, and
    truncation happens only when even ``min_size`` can't hold it. Returns
    ``(size, line_h, lines)``. Shared by WrappedBoxGlyph (subVI name box) and a
    ConstantGlyph in ``fit`` mode (a class/refnum constant's class name)."""
    best: tuple[float, float, list[str]] | None = None
    for tenths in range(int(max_size * 10), int(min_size * 10) - 1, -5):
        size = tenths / 10
        line_h = size + _FIT_GAP
        vfit = max(1, int(avail_h // line_h))
        lines = wrap_label(label, avail_w, backend, size, min(max_lines, vfit))
        best = (size, line_h, lines)
        if lines and not lines[-1].endswith("…"):
            return best  # full label shown at this (largest passing) size
    return best if best is not None else (max_size, max_size, [])


def _truncate_to_width(
    backend: Backend,
    s: str,
    size: float,
    max_w: float,
) -> str:
    """``s`` shortened with a trailing ellipsis until it fits ``max_w`` px at
    ``size`` (via the backend's own text metrics), or ``s`` unchanged if it
    already fits. Empty when even one char + ellipsis won't fit."""
    if not s or backend.measure_text(s, size) <= max_w:
        return s
    while s and backend.measure_text(s + "…", size) > max_w:
        s = s[:-1]
    return (s + "…") if s else ""


# One FIXED font size for every operator-symbol glyph — arithmetic/comparison
# triangles, Select, comparison-to-0, and the boolean logic gates — so the
# symbols are identical in size everywhere on the diagram, independent of each
# node's own bounds.
_OPERATOR_SYMBOL_SIZE = 9.0


_GATE_ARC_SEGMENTS = 14


def _quad_bezier_points(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    n: int = _GATE_ARC_SEGMENTS,
) -> list[tuple[float, float]]:
    """Sample a quadratic Bezier curve (``p0`` -> control ``p1`` -> ``p2``)
    into ``n`` straight segments. ``Backend.path``/``polygon`` only draw
    straight-line vertex lists (no native arc/bezier op), so every curved
    gate outline below is built by sampling one of these instead of a
    hand-authored SVG ``<path d="... A ...">`` — the geometry is ours, not
    traced NI artwork."""
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
        y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def draw_split_box(
    backend: Backend,
    bounds: Rect,
    theme: Theme,
    *,
    symbol: str,
    num_cells: int,
    symbol_side: str,
    fill_attr: str = "prim_fill",
    stroke_attr: str = "prim_stroke",
    text_attr: str = "prim_text",
    stroke_width: float = 1.2,
    cell_labels: tuple[str, ...] | None = None,
    sym_w: float | None = None,
) -> None:
    """Reusable clean-room glyph body: a bordered rectangle split by a vertical
    divider into a narrow SYMBOL cell (spanning the full height, holding one
    centered symbol/text) and a wider CELLS area divided into ``num_cells``
    horizontal rows — one row per element terminal, where each wire lands.

    ``symbol_side`` (``"left"``/``"right"``) picks which side the symbol cell
    sits on. This is the shared skeleton of every "assemble N things into /
    split one thing into N" primitive — Compound Arithmetic (symbol on the
    right, one row per input), Bundle (element rows left, cluster arrow right),
    and Unbundle (cluster arrow left, element rows right) — matching each real
    primitive's outline+proportions while keeping the interior our own drawing.

    N-cell growth is free: the glyph carries no intrinsic size, it scales to
    whatever heap ``bounds`` the node has, so an N-input Bundle/Compound
    Arithmetic draws correctly at any width."""
    x1, y1, x2, y2 = bounds
    fill = getattr(theme, fill_attr)
    stroke = getattr(theme, stroke_attr)
    backend.rect(x1, y1, x2, y2, fill=fill, stroke=stroke, stroke_width=stroke_width)

    width, height = x2 - x1, y2 - y1
    if sym_w is None:
        sym_w = min(width * 0.5, max(8.0, height))
    else:
        sym_w = max(6.0, min(sym_w, width * 0.5))
    if symbol_side == "left":
        x_div = x1 + sym_w
        sym_x1, sym_x2 = x1, x_div
        cell_x1, cell_x2 = x_div, x2
    else:  # "right" (default / Compound-Arithmetic layout)
        x_div = x2 - sym_w
        sym_x1, sym_x2 = x_div, x2
        cell_x1, cell_x2 = x1, x_div
    backend.line(x_div, y1, x_div, y2, stroke=stroke, stroke_width=1.0)

    cells = max(1, num_cells)
    for i in range(1, cells):
        y = y1 + i * height / cells
        backend.line(cell_x1, y, cell_x2, y, stroke=stroke, stroke_width=1.0)

    # Bundle/Unbundle By Name label each cell row with the accessed field name
    # (left-aligned, fit to the cell width). Positional Bundle/Unbundle pass no
    # labels — their rows are blank (terminals only).
    if cell_labels:
        lpad = 3.0
        lsize = max(6.0, min(9.0, (height / cells) * 0.62))
        for i in range(cells):
            ry1 = y1 + i * height / cells
            ry2 = y1 + (i + 1) * height / cells
            raw = cell_labels[i] if i < len(cell_labels) else ""
            label = fit_label(raw, (cell_x2 - cell_x1) - 2 * lpad, backend, lsize)
            backend.text(
                cell_x1 + lpad,
                (ry1 + ry2) / 2 + lsize * 0.34,
                label,
                lsize,
                anchor="start",
                fill=getattr(theme, text_attr),
            )

    size = max(6.0, min(15.0, height * 0.62, sym_w * 0.85))
    backend.text(
        (sym_x1 + sym_x2) / 2,
        (y1 + y2) / 2 + size * 0.34,
        symbol,
        size,
        fill=getattr(theme, text_attr),
    )


# Compound Arithmetic operator -> symbol (raw ``PrimitiveNode.operation``
# strings, lowercase — not yet boolean-translated by codegen; for rendering
# we just show the operator's own symbol). An unmapped operation (e.g. the
# "unsupported" sentinel for a dcoFiller code we haven't verified) degrades
# to "?" so the node still renders (see CompoundArithGlyph.draw).
_CPD_ARITH_SYMBOL = {
    "or": "∨",
    "and": "∧",
    "xor": "⊕",
    "add": "+",
    "multiply": "×",
}

# The direction arrow shown in a Bundle/Unbundle's cluster cell — data flows
# left-to-right through the node, so both point right (our own symbol, not
# NI's artwork). The mirror of WHICH side the element rows sit on is what
# distinguishes bundling from unbundling, exactly as the real glyphs do.
_CLUSTER_ARROW = "▶"

# ARRAY family (goal #14 follow-up): a row of small "element boxes" reads as
# an array the same way LabVIEW's own array shell does, without tracing NI
# artwork. The fixed-shape members (Size/Reverse/Search/Sort/Split) always
# draw a decorative row of 3 boxes — none of these primitives' terminal
# counts say anything about array length, so 3 is just the motif, not data.
_ARRAY_ELEMENTS_N = 3


def _draw_element_boxes(
    backend: Backend,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    n: int,
    stroke: str,
    stroke_width: float,
) -> None:
    """A row of ``n`` small square outline boxes evenly spaced across
    ``(x1, y1, x2, y2)`` — the ARRAY family's shared "little element boxes"
    motif. Each box is square (side capped to the band height) and centered
    in its own equal-width cell."""
    n = max(1, n)
    cell_w = (x2 - x1) / n
    side = max(2.0, min(cell_w * 0.78, y2 - y1))
    cy = (y1 + y2) / 2
    for i in range(n):
        ccx = x1 + cell_w * (i + 0.5)
        backend.rect(
            ccx - side / 2,
            cy - side / 2,
            ccx + side / 2,
            cy + side / 2,
            fill="none",
            stroke=stroke,
            stroke_width=stroke_width,
        )


def _draw_arrow(
    backend: Backend,
    x_from: float,
    x_to: float,
    y: float,
    *,
    stroke: str,
    stroke_width: float,
    head_len: float,
    head_half_h: float,
) -> None:
    """A straight horizontal arrow: a shaft (``path``) from ``x_from`` to
    ``x_to`` plus a small filled triangular head at ``x_to`` pointing in the
    direction of travel. ``Backend`` has no native marker-end op (that was
    an SVG-only ``<defs><marker>`` in the proof sketch), so the head is an
    explicit ``polygon`` — used by ``ArrayReverseGlyph`` (backward) and
    ``ConvertGlyph`` (forward, "entering" the type abbreviation)."""
    direction = 1.0 if x_to >= x_from else -1.0
    shaft_end = x_to - direction * head_len
    backend.path(
        [(x_from, y), (shaft_end, y)],
        stroke=stroke,
        stroke_width=stroke_width,
    )
    backend.polygon(
        [(x_to, y), (shaft_end, y - head_half_h), (shaft_end, y + head_half_h)],
        fill=stroke,
        stroke=None,
    )


def _draw_node_tile(
    backend: Backend,
    bounds: Rect,
    theme: Theme,
    fill_attr: str,
    stroke_attr: str,
) -> None:
    """The filled node tile every primitive sits on (same as WrappedBoxGlyph's
    box / ArithGlyph's filled triangle). A motif glyph draws this FIRST, then its
    symbol on top — so it reads as a real node instead of strokes floating on the
    diagram."""
    x1, y1, x2, y2 = bounds
    backend.rect(
        x1,
        y1,
        x2,
        y2,
        fill=getattr(theme, fill_attr),
        stroke=getattr(theme, stroke_attr),
        stroke_width=max(1.0, min(x2 - x1, y2 - y1) * 0.05),
    )


# Shared drawer-row geometry for PropertyNodeGlyph and InvokeNodeGlyph: a
# fixed left gutter wide enough for one arrow, so a row's label starts at the
# same x whether or not that row actually draws a left arrow.
_ROW_LPAD = 3.0

# Shared drawer-row geometry for PropertyNodeGlyph and InvokeNodeGlyph: a
# fixed left gutter wide enough for one arrow, so a row's label starts at the
# same x whether or not that row actually draws a left arrow.
_ROW_LPAD = 3.0
_ROW_ARROW_W = 7.0

_ROW_ARROW_W = 7.0
_ROW_GUTTER = _ROW_ARROW_W + _ROW_LPAD


def _draw_drawer_row(
    backend: Backend,
    x1: float,
    x2: float,
    ry1: float,
    ry2: float,
    label: str,
    *,
    show_left: bool,
    show_right: bool,
    text_fill: str,
    lsize: float,
) -> None:
    """Draw one property/invoke drawer row: the label plus the shared arrow
    rule -- the glyph is always the rightward ``▸``; an INPUT terminal draws
    it at the row's LEFT edge, an OUTPUT terminal at the RIGHT edge, and a
    pass-through row (both present) draws both. The left gutter (one arrow's
    width) is reserved on every row regardless of ``show_left``, so labels
    across all rows of a node stay left-aligned at the same x."""
    cy = (ry1 + ry2) / 2
    if show_left:
        backend.text(
            x1 + _ROW_ARROW_W * 0.45,
            cy + lsize * 0.34,
            "▸",
            lsize,
            fill=text_fill,
        )
    avail = (x2 - x1) - _ROW_GUTTER - _ROW_LPAD - (_ROW_ARROW_W if show_right else 0.0)
    backend.text(
        x1 + _ROW_GUTTER,
        cy + lsize * 0.34,
        fit_label(label, avail, backend, lsize),
        lsize,
        anchor="start",
        fill=text_fill,
    )
    if show_right:
        backend.text(
            x2 - _ROW_ARROW_W * 0.55,
            cy + lsize * 0.34,
            "▸",
            lsize,
            fill=text_fill,
        )

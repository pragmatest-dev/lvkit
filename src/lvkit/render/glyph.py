"""``Glyph``: a node visual that scales to the node's heap bounds.

Design choice — protocol-with-draw, not an op-list:

A ``Glyph`` is anything with a ``draw(backend, bounds, theme)`` method. Each
concrete glyph is a small frozen dataclass holding only the node-specific
values a resolver already knows (a label, a symbol, an icon path, a color
attribute name) and calling ``Backend`` ops directly, the same way the prior
dispatch-dict drawer did. An op-list value object (record shapes once, replay
generically) was considered and rejected: nothing here is actually shared
geometry — an arithmetic triangle, a labeled box, and an embedded icon each
need different backend calls, so a generic replay layer would just be an
indirection between "resolver decided this is an Add" and "draw an Add
triangle" without saving any code. Protocol-with-draw keeps that one hop.

Per DESIGN's extensibility contract, glyphs declare NO intrinsic size — they
receive the node's heap ``bounds`` (Rect) and scale to it, which is what
makes growable nodes (Build Array, N-input Compound Arithmetic) free: the
same glyph instance draws correctly at any width. Terminal-anchor points for
wire routing always come from heap ``termBounds`` centers (``layout.py`` /
``scene.py``) — a glyph's shape is purely decorative and never consulted for
wire endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .backend import Backend
from .icons import icon_data_uri
from .layout import Rect
from .style import Theme


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
    text: str, width: float, backend: Backend, size: float, max_lines: int,
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


@dataclass(frozen=True)
class LabeledBoxGlyph:
    """A bordered box with a centered, fitted label.

    Colors are ``Theme`` attribute names (not literal colors) so a
    dark-mode/doc theme swap changes every labeled box in one place —
    the same principle ``wire_style`` already follows.
    """

    label: str
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    stroke_width: float = 1.0
    text_size: float = 8.0
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=self.stroke_width,
        )
        backend.text(
            (x1 + x2) / 2, (y1 + y2) / 2 + 3,
            self.label,
            self.text_size,
            fill=getattr(theme, self.text_attr),
        )


@dataclass(frozen=True)
class WrappedBoxGlyph:
    """A subVI box with its NAME wrapped inside — LabVIEW's default look for a
    subVI that has no custom icon. The name word-wraps to at most ``max_lines``
    (default 4) lines sized to the box width, vertically centered; an overlong
    name is hard-broken and ellipsized. Used instead of drawing an empty box +
    a label underneath, so the name lives in the icon space itself."""

    label: str
    fill_attr: str = "subvi_fill"
    stroke_attr: str = "subvi_stroke"
    stroke_width: float = 1.5
    max_lines: int = 4
    text_size: float = 7.0
    text_attr: str = "subvi_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=self.stroke_width,
        )
        self.draw_wrapped_text(backend, bounds, theme)

    def draw_wrapped_text(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """Draw only the word-wrapped, best-fit-sized label centered in
        ``bounds`` (no box) — shared by ``draw`` and by other glyphs that own
        their own frame (e.g. ``LocalVariableGlyph``, which draws a badge in a
        left cell and delegates the remaining width to this)."""
        x1, y1, x2, y2 = bounds
        if not self.label:
            return
        pad = 2.0
        avail_w = (x2 - x1) - 2 * pad
        avail_h = (y2 - y1) - 2 * pad
        if avail_w <= 2 or avail_h <= 2:
            return
        size, line_h, lines = self._best_fit(avail_w, avail_h, backend)
        if not lines:
            return
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        # Center the baseline set vertically on cy (small +size*0.32 nudge so
        # the visual mass, not the baselines, sits centered).
        first = cy - (len(lines) - 1) * line_h / 2 + size * 0.32
        text_fill = getattr(theme, self.text_attr)
        for i, line in enumerate(lines):
            backend.text(cx, first + i * line_h, line, size, fill=text_fill)

    _GAP = 1.3  # extra px between baselines beyond the font size

    def _best_fit(
        self, avail_w: float, avail_h: float, backend: Backend,
    ) -> tuple[float, float, list[str]]:
        """Largest font (down to a legibility floor) at which the FULL name
        wraps into at most ``max_lines`` lines that fit the box — so a short
        name stays big and one line, a long one shrinks and uses up to 4 lines,
        and truncation happens only when even the smallest font can't hold it."""
        best: tuple[float, float, list[str]] | None = None
        for tenths in range(int(self.text_size * 10), 49, -5):  # 7.0 .. 5.0 step .5
            size = tenths / 10
            line_h = size + self._GAP
            vfit = max(1, int(avail_h // line_h))
            max_lines = min(self.max_lines, vfit)
            lines = wrap_label(self.label, avail_w, backend, size, max_lines)
            best = (size, line_h, lines)
            if lines and not lines[-1].endswith("…"):
                return best  # full name shown at this (largest passing) size
        # Nothing fit without truncation — use the smallest tried (ellipsized).
        return best if best is not None else (self.text_size, self.text_size, [])


def _truncate_to_width(
    backend: Backend, s: str, size: float, max_w: float,
) -> str:
    """``s`` shortened with a trailing ellipsis until it fits ``max_w`` px at
    ``size`` (via the backend's own text metrics), or ``s`` unchanged if it
    already fits. Empty when even one char + ellipsis won't fit."""
    if not s or backend.measure_text(s, size) <= max_w:
        return s
    while s and backend.measure_text(s + "…", size) > max_w:
        s = s[:-1]
    return (s + "…") if s else ""


@dataclass(frozen=True)
class FormulaNodeGlyph:
    """A Formula Node (fBox): a flat, structure-styled box (same outline
    treatment as a Sequence/Case structure — see ``draw.py::draw_structure``
    and ``Theme.struct_border``) holding its embedded C-like ``script`` as
    plain MONOSPACE text, left- and top-aligned, one line per source line.

    Unlike a real structure, a Formula Node's box is drawn as an ordinary
    node glyph (not via ``draw_structure``'s wire-safe outline-only pass)
    because nothing ever routes THROUGH its interior — every variable is a
    named tunnel on the border (drawn separately, in ``draw.py``, where the
    node's terminals are available) — so an opaque fill is safe here and
    keeps the script text readable over whatever sits behind the node.

    The script's LEADING blank lines (the decoded XML often opens with a
    few) are stripped so the first real line of code sits at the top
    padding; INTERIOR blank lines are kept so the script's own line breaks
    are preserved. A line that would fall past the box's bottom edge is
    simply not drawn — no shrinking or wrapping, matching how LabVIEW itself
    just clips a Formula Node's script to its stored size."""

    script: str
    fill_attr: str = "canvas"
    stroke_attr: str = "struct_border"
    stroke_width: float = 1.5
    text_size: float = 7.5
    text_attr: str = "text"
    pad: float = 7.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        self.draw_box(backend, bounds, theme)
        self.draw_script(backend, bounds, theme, self.pad, self.pad)

    def draw_box(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=self.stroke_width,
        )

    def draw_script(
        self, backend: Backend, bounds: Rect, theme: Theme,
        left_inset: float, right_inset: float,
    ) -> None:
        """Draw the C source as monospace text, inset from the box's LEFT and
        RIGHT edges by the given amounts so it clears the input-tunnel column on
        the left and the output-tunnel column on the right (roadmap #61 — the
        variables live on the border, so the code must not run under them). Each
        line is truncated to the remaining width; lines past the bottom edge are
        clipped, matching how LabVIEW clips a Formula Node's script to its size."""
        x1, y1, x2, y2 = bounds
        lines = self.script.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            return
        tx = x1 + left_inset
        max_w = (x2 - right_inset) - tx
        if max_w <= 4:
            return
        line_h = self.text_size + 2.0
        ty = y1 + self.pad + self.text_size
        text_fill = getattr(theme, self.text_attr)
        bottom = y2 - self.pad * 0.5
        for line in lines:
            if ty > bottom:
                break
            s = _truncate_to_width(backend, line, self.text_size, max_w)
            if s:
                backend.text(
                    tx, ty, s, self.text_size, anchor="start", fill=text_fill,
                    mono=True,
                )
            ty += line_h


@dataclass(frozen=True)
class LocalVariableGlyph:
    """A Local/Global Variable node: a bordered box with the referenced
    control's NAME, marked by a small filled right-pointing triangle badge —
    the marker that tells it apart from a same-shaped constant/subVI box (a
    constant has no badge). Grounded in NI's public "Local Variable"
    function-reference node image (a ``▶`` glyph beside the control name); the
    triangle is our own clean-room shape, not NI artwork.

    The badge sits on the DATAFLOW side: a READ variable is a source (data
    leaves to the right), so its ▶ is right-justified on the output edge; a
    WRITE variable is a sink (data enters from the left), so its ▶ is on the
    left input edge — that side IS the read/write signal. Both get a BOLD border
    so a local variable stands out from a plain constant/subVI box."""

    label: str
    is_write: bool = False
    fill_attr: str = "localvar_fill"
    stroke_attr: str = "localvar_stroke"
    text_attr: str = "localvar_text"
    max_lines: int = 2
    text_size: float = 7.0

    _STROKE_W = 2.5   # bold border for both read and write

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr), stroke=stroke,
            stroke_width=self._STROKE_W,
        )
        # A small filled right-pointing triangle on the dataflow side: right
        # (output) edge for a read, left (input) edge for a write.
        pad = 2.0
        cy = (y1 + y2) / 2
        tri_h = min(8.0, (y2 - y1) - 2 * pad)
        left_pad = right_pad = 0.0
        if tri_h >= 4.0 and (x2 - x1) > 2 * pad + 6.0:
            tw = tri_h * 0.72
            fill = getattr(theme, self.text_attr)
            if self.is_write:
                tx = x1 + pad + 1.0        # left input edge
                left_pad = pad + 1.0 + tw
            else:
                tx = x2 - pad - 1.0 - tw   # right output edge
                right_pad = pad + 1.0 + tw
            backend.polygon(
                [(tx, cy - tri_h / 2), (tx + tw, cy), (tx, cy + tri_h / 2)],
                fill=fill, stroke=None,
            )
        # Name fills the width remaining beside the badge.
        text_glyph = WrappedBoxGlyph(
            self.label, self.fill_attr, self.stroke_attr,
            max_lines=self.max_lines, text_size=self.text_size,
            text_attr=self.text_attr,
        )
        text_glyph.draw_wrapped_text(
            backend, (x1 + left_pad, y1, x2 - right_pad, y2), theme,
        )


@dataclass(frozen=True)
class ArithGlyph:
    """The arithmetic/comparison-primitive triangle (Add/Subtract/Multiply/
    Divide/Increment/Decrement, and the comparison functions Equal?/Greater?/
    ...) with its operator symbol. LabVIEW draws all of these as the same
    borderless right-pointing triangle — only the interior symbol differs."""

    symbol: str
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.polygon(
            [(x1, y1), (x2, (y1 + y2) / 2), (x1, y2)],
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr), stroke_width=1.2,
        )
        # Scale the operator to the (often small) triangle so it doesn't overflow.
        size = max(6.0, min(15.0, (y2 - y1) * 0.62, (x2 - x1) * 0.85))
        cy = (y1 + y2) / 2
        backend.text(x1 + (x2 - x1) * 0.36, cy + size * 0.34, self.symbol, size,
                     fill=getattr(theme, self.text_attr))


def draw_split_box(
    backend: Backend, bounds: Rect, theme: Theme, *,
    symbol: str, num_cells: int, symbol_side: str,
    fill_attr: str = "prim_fill", stroke_attr: str = "prim_stroke",
    text_attr: str = "prim_text", stroke_width: float = 1.2,
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
    sym_w = min(width * 0.5, max(8.0, height))
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

    size = max(6.0, min(15.0, height * 0.62, sym_w * 0.85))
    backend.text(
        (sym_x1 + sym_x2) / 2, (y1 + y2) / 2 + size * 0.34, symbol, size,
        fill=getattr(theme, text_attr),
    )


# Compound Arithmetic operator -> symbol (raw ``PrimitiveNode.operation``
# strings, lowercase — not yet boolean-translated by codegen; for rendering
# we just show the operator's own symbol). Unknown operations fall back to
# the raw operation string itself (see CompoundArithGlyph.draw).
_CPD_ARITH_SYMBOL = {
    "or": "∨", "and": "∧", "xor": "⊕", "add": "+", "multiply": "×",
}


@dataclass(frozen=True)
class CompoundArithGlyph:
    """The Compound Arithmetic node (``node_type == "cpdArith"``): a plain
    rectangle (sharp corners — block-diagram objects aren't rounded, unlike
    the borderless arithmetic triangle), split like LabVIEW's real glyph:

    - a narrower RIGHT cell holding the operator symbol, spanning the full
      node height (the output side);
    - a wider LEFT area divided into ``num_inputs`` horizontal rows by
      ``num_inputs - 1`` evenly-spaced lines — one row per input terminal,
      where each input wire lands.

    N-input growth is free — the glyph carries no intrinsic size, it scales
    to whatever heap ``bounds`` the node has."""

    operation: str
    num_inputs: int = 1
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        draw_split_box(
            backend, bounds, theme,
            symbol=_CPD_ARITH_SYMBOL.get(self.operation, self.operation),
            num_cells=self.num_inputs, symbol_side="right",
            fill_attr=self.fill_attr, stroke_attr=self.stroke_attr,
            text_attr=self.text_attr,
        )


# The direction arrow shown in a Bundle/Unbundle's cluster cell — data flows
# left-to-right through the node, so both point right (our own symbol, not
# NI's artwork). The mirror of WHICH side the element rows sit on is what
# distinguishes bundling from unbundling, exactly as the real glyphs do.
_CLUSTER_ARROW = "▶"


@dataclass(frozen=True)
class BundleGlyph:
    """The Bundle primitive (assemble N elements into one cluster). Original
    clean-room glyph matching LabVIEW's outline (NI docs slug ``bundle``): a
    box whose LEFT area is split into one row per element input, and whose
    narrow RIGHT cell (the output cluster side) carries a right-pointing
    direction arrow. Drawn via the shared ``draw_split_box`` skeleton."""

    num_fields: int = 1
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        draw_split_box(
            backend, bounds, theme,
            symbol=_CLUSTER_ARROW, num_cells=self.num_fields, symbol_side="right",
            fill_attr=self.fill_attr, stroke_attr=self.stroke_attr,
            text_attr=self.text_attr,
        )


@dataclass(frozen=True)
class UnbundleGlyph:
    """The Unbundle primitive (split one cluster into N elements). Mirror of
    ``BundleGlyph`` matching LabVIEW's outline (NI docs slug ``unbundle``): a
    narrow LEFT cell (the input cluster side) with a right-pointing direction
    arrow, and a RIGHT area split into one row per element output."""

    num_fields: int = 1
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        draw_split_box(
            backend, bounds, theme,
            symbol=_CLUSTER_ARROW, num_cells=self.num_fields, symbol_side="left",
            fill_attr=self.fill_attr, stroke_attr=self.stroke_attr,
            text_attr=self.text_attr,
        )


@dataclass(frozen=True)
class BundleByNameGlyph:
    """Bundle / Unbundle By Name (heap class ``nMux``): a box with one row per
    accessed field, each row LABELED with the field's NAME. Unlike the compact
    positional Bundle/Unbundle (``BundleGlyph``/``UnbundleGlyph``, terminals
    only), By Name shows the names — resolved from the wired cluster's type by
    the resolver. The small cluster (refnum) terminal is drawn separately at the
    node's corner; the named rows fill the box, one per ``list`` terminal in
    top-to-bottom order. Scales to the node's heap bounds (names left-aligned,
    truncated to fit)."""

    names: tuple[str, ...]
    bundling: bool = True  # True: Bundle By Name; False: Unbundle By Name
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        fill = getattr(theme, self.fill_attr)
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        backend.rect(x1, y1, x2, y2, fill=fill, stroke=stroke, stroke_width=1.2)
        rows = self.names or ("",)
        n = len(rows)
        h = y2 - y1
        pad = 3.0
        size = max(6.0, min(9.0, (h / n) * 0.62))
        for i, name in enumerate(rows):
            ry1 = y1 + i * h / n
            ry2 = y1 + (i + 1) * h / n
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=0.75)
            label = fit_label(name, (x2 - x1) - 2 * pad, backend, size)
            backend.text(
                x1 + pad, (ry1 + ry2) / 2 + size * 0.34, label, size, anchor="start",
                fill=text_fill,
            )


@dataclass(frozen=True)
class ConstantGlyph:
    """A constant's colored box (color from its own ``LVType``, computed by
    the resolver — a literal color, not a theme attribute, since it varies
    per-node rather than per-role).

    ``multiline`` (string constants) renders the FULL text word-wrapped to the
    box, honoring explicit newlines, top- and left-aligned like a LabVIEW
    string constant — LabVIEW already sizes these boxes to their content, so
    wrapping fills the box instead of collapsing a multi-line word list to one
    ellipsized line. Scalar constants stay single-line and centered."""

    value: str
    color: str
    fill_attr: str = "term_fill"
    text_size: float = 9.0
    multiline: bool = False
    text_attr: str = "const_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill=theme.const_fill,
            stroke=self.color, stroke_width=1.2,
        )
        if not self.value:
            return
        if self.multiline:
            self._draw_wrapped(backend, x1, y1, x2, y2, theme)
        else:
            backend.text(
                (x1 + x2) / 2, (y1 + y2) / 2 + 3,
                fit_value(self.value, x2 - x1, backend, self.text_size),
                self.text_size,
                fill=getattr(theme, self.text_attr),
            )

    def _draw_wrapped(
        self, backend: Backend, x1: float, y1: float, x2: float, y2: float,
        theme: Theme,
    ) -> None:
        pad = 2.5
        avail_w = x2 - x1 - 2 * pad
        line_h = self.text_size + 2.0
        max_lines = max(1, int((y2 - y1 - 2 * pad) / line_h))
        # Honor explicit newlines first, then word-wrap each segment. A blank
        # segment (e.g. the leading newline of a line-indexed word list) keeps
        # a blank row so line indices line up with the source string.
        lines: list[str] = []
        for seg in self.value.split("\n"):
            if not seg:
                lines.append("")
                continue
            lines.extend(wrap_label(seg, avail_w, backend, self.text_size, max_lines))
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and backend.measure_text(last + "…", self.text_size) > avail_w:
                last = last[:-1]
            lines[-1] = last + "…"
        ty = y1 + pad + self.text_size
        text_fill = getattr(theme, self.text_attr)
        for line in lines:
            backend.text(
                x1 + pad, ty, line, self.text_size, anchor="start", fill=text_fill,
            )
            ty += line_h


@dataclass(frozen=True)
class BooleanConstantGlyph:
    """A LabVIEW Boolean constant — the modern (2010+) look shows only the
    CURRENT value:

    - **True**: a green button with a white bezel — green outer outline, a
      white inner outline, green fill, and a bold white ``T``.
    - **False**: a plain white box — green outline, white fill, green ``F``.

    Replaces showing the raw ``True``/``False`` text in a box."""

    value: bool
    green_attr: str = "wire_bool"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        # LabVIEW's Boolean constant is a small SQUARE — draw a centered square
        # (side = the smaller dimension) rather than stretching to fill a box
        # that may be wider (a resized/labeled constant).
        side = min(x2 - x1, y2 - y1)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        x1, y1, x2, y2 = cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2
        green = getattr(theme, self.green_attr)
        if self.value:
            # Green outer outline + green fill, then a white inner outline
            # (the bezel) inset within it, still green-filled — a white T.
            # Sharp corners: LabVIEW block-diagram objects aren't rounded.
            backend.rect(x1, y1, x2, y2, fill=green, stroke=green,
                         stroke_width=1.0)
            inset = min(2.0, (x2 - x1) * 0.16, (y2 - y1) * 0.16)
            backend.rect(x1 + inset, y1 + inset, x2 - inset, y2 - inset,
                         fill=green, stroke="#ffffff", stroke_width=1.0)
            text_fill, letter = "#ffffff", "T"
        else:
            backend.rect(x1, y1, x2, y2, fill="#ffffff", stroke=green,
                         stroke_width=1.2)
            text_fill, letter = green, "F"
        size = max(6.0, min(11.0, (y2 - y1) * 0.72, (x2 - x1) * 0.9))
        backend.text(
            (x1 + x2) / 2, (y1 + y2) / 2 + size * 0.34, letter, size,
            fill=text_fill, bold=True,
        )


@dataclass(frozen=True)
class IconImageGlyph:
    """A real, extracted ``_ICON.png`` filling the node's box — matches how
    LabVIEW itself draws a SubVI call (the icon IS the node's border, no
    separate label/rect drawn around it)."""

    icon_path: Path
    opacity: float = 1.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        uri = icon_data_uri(self.icon_path)
        if uri is None:
            return
        backend.image(uri, x1, y1, x2 - x1, y2 - y1, opacity=self.opacity)


@dataclass(frozen=True)
class CenteredSvgGlyph:
    """An SVG icon drawn at its NATURAL pixel size, CENTERED in the node box —
    LabVIEW draws a primitive's icon at its own size within the (larger)
    clickable box, not stretched to fill it. Scaled down proportionally only
    if the natural size is larger than the available bounds."""

    fragment: str
    natural: tuple[int, int]  # (width, height) in the SVG's own viewBox units

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        bw, bh = x2 - x1, y2 - y1
        nw, nh = self.natural
        if nw <= 0 or nh <= 0:
            return
        scale = min(1.0, bw / nw, bh / nh)
        w, h = nw * scale, nh * scale
        ox, oy = x1 + (bw - w) / 2, y1 + (bh - h) / 2
        backend.raw_svg(self.fragment, ox, oy, w, h, viewbox=(nw, nh))


@dataclass(frozen=True)
class BracketGlyph:
    """A plain bracketed body (``[`` ``]`` ends, no operator symbol) — the
    real LabVIEW look for Build Array and similarly bracket-shaped
    primitives. A seed case for ``GeneratedGlyphResolver``, drawn the same
    way regardless of node width, so an N-input Build Array is free."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        backend.rect(x1, y1, x2, y2, fill=getattr(theme, self.fill_attr))
        o = min(6.0, (x2 - x1) / 4)
        backend.path(
            [(x1 + o, y1), (x1, y1), (x1, y2), (x1 + o, y2)],
            stroke=stroke, stroke_width=1.2,
        )
        backend.path(
            [(x2 - o, y1), (x2, y1), (x2, y2), (x2 - o, y2)],
            stroke=stroke, stroke_width=1.2,
        )


@dataclass(frozen=True)
class VariantGlyph:
    """A LabVIEW Variant: opaque, type-erased data — the schematic is a
    SOLID filled box (no internal detail, since there IS no visible
    internal structure to a Variant), sharp corners like every other
    block-diagram object."""

    fill_attr: str = "wire_variant"
    stroke_attr: str = "struct_border"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=1.0,
        )


@dataclass(frozen=True)
class ErrorClusterGlyph:
    """A LabVIEW error cluster ({status: bool, code: i32, source: string}):
    a mustard-bordered box with a round status LED (top-left) and two short
    horizontal bars beneath it standing in for the code/source fields."""

    fill_attr: str = "const_fill"
    stroke_attr: str = "wire_error"
    led_attr: str = "wire_bool"
    bar_attr: str = "wire_error"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=1.2,
        )
        w, h = x2 - x1, y2 - y1
        pad = max(1.5, min(w, h) * 0.15)
        # Status LED: a small round indicator at the top-left.
        r = max(1.0, min(w, h) * 0.16)
        cx, cy = x1 + pad + r, y1 + pad + r
        backend.circle(
            cx, cy, r,
            fill=getattr(theme, self.led_attr),
            stroke=getattr(theme, self.stroke_attr), stroke_width=0.6,
        )
        # Code/source fields: two short horizontal bars below the LED.
        bar_color = getattr(theme, self.bar_attr)
        bar_x1 = x1 + pad
        bar_x2 = x2 - pad
        if bar_x2 <= bar_x1:
            return
        bar_h = max(1.0, min(h * 0.12, 3.0))
        gap = max(1.0, h * 0.10)
        bar1_y = cy + r + gap
        bar2_y = bar1_y + bar_h + gap
        if bar2_y + bar_h <= y2 - pad * 0.5:
            backend.rect(bar_x1, bar1_y, bar_x2, bar1_y + bar_h, fill=bar_color)
            backend.rect(bar_x1, bar2_y, bar_x2, bar2_y + bar_h, fill=bar_color)


@dataclass(frozen=True)
class ClusterConstantGlyph:
    """A cluster constant drawn by COMPOSING each field's own constant glyph
    (boolean / numeric / string / …) inside a cluster box.

    LabVIEW stores no inner-element positions for a block-diagram constant, so
    the fields are laid out here — a vertical stack, each row a small field-name
    label beside that field's value glyph. Error clusters get the mustard border
    (``wire_error``) and the stored status / code / source field order; any
    other cluster gets the generic cluster brown."""

    fields: tuple[tuple[str, Glyph], ...]
    is_error: bool = False
    fill_attr: str = "const_fill"
    # ``name: value`` per field, for a hover tooltip — useful when the cluster
    # is drawn small/collapsed and the inline values aren't legible.
    value_summary: str = ""

    # Below these, a stacked "name: value" row can't fit both a name AND a
    # value cell, so we drop the field-NAME labels and draw the field VALUES
    # alone (stacked, scaled to the box) — never a blank box, since LabVIEW
    # always shows a cluster constant's contents (the names are the toggleable
    # part; see the field-label discussion). Names stay on the hover tooltip
    # (``value_summary``). These are legibility floors tied to ``label_size``
    # — NOT a semantic "collapsed" flag, which the heap does not carry.
    _MIN_ROW_H = 9.0
    _MIN_FIELD_W = 40.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        border = theme.wire_error if self.is_error else theme.wire_cluster
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr), stroke=border, stroke_width=1.5,
        )
        if not self.fields:
            return  # a genuinely empty cluster (no elements) — box only
        pad = 3.0
        label_size = 7.0
        row_h = (y2 - y1 - 2 * pad) / len(self.fields)
        if row_h < self._MIN_ROW_H or (x2 - x1 - 2 * pad) < self._MIN_FIELD_W:
            # Too small for labeled rows: draw just the field VALUES, stacked
            # and scaled to fit, so the box is never blank. Names on hover.
            vpad = 1.0
            vrow = (y2 - y1 - 2 * vpad) / len(self.fields)
            for i, (_name, field_glyph) in enumerate(self.fields):
                ry1 = y1 + vpad + i * vrow
                ry2 = ry1 + vrow
                if x2 - vpad > x1 + vpad and ry2 - 0.5 > ry1 + 0.5:
                    field_glyph.draw(
                        backend, (x1 + vpad, ry1 + 0.5, x2 - vpad, ry2 - 0.5), theme,
                    )
            return
        label_w = min(
            0.4 * (x2 - x1),
            max(backend.measure_text(nm, label_size) for nm, _ in self.fields) + 4.0,
        )
        for i, (name, field_glyph) in enumerate(self.fields):
            ry1 = y1 + pad + i * row_h
            ry2 = ry1 + row_h
            backend.text(
                x1 + pad, (ry1 + ry2) / 2 + label_size * 0.34, name,
                label_size, anchor="start", fill=border,
            )
            cx1 = x1 + pad + label_w
            if x2 - pad > cx1 and ry2 - 1.0 > ry1 + 1.0:
                field_glyph.draw(backend, (cx1, ry1 + 1.0, x2 - pad, ry2 - 1.0), theme)


@dataclass(frozen=True)
class InlineSvgGlyph:
    """A hand-authored SVG fragment (JSON ``icon.svg``/``icon.file``),
    scaled to the node's bounds via a nested ``<svg>`` element — the fragment
    is written in its own local coordinate space (``size``) and the SVG
    viewport/viewBox machinery does the scaling, so no manual coordinate
    transform code is needed here.

    Backend-specific: only ``SvgBackend`` can embed raw markup (see
    ``Backend.raw_svg``); other backends may fall back to skipping it,
    which resolve_glyph() callers never need to know about, since the
    resolver chain will simply try this before falling through further.
    """

    fragment: str
    size: tuple[int, int] = (24, 24)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = self.size
        backend.raw_svg(self.fragment, x1, y1, x2 - x1, y2 - y1, viewbox=(w, h))

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

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import LVType
from ..parser.layout import Rect
from .backend import Backend
from .icons import icon_data_uri
from .style import Theme, wire_style


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
    so a local variable stands out from a plain constant/subVI box.

    ``border_color`` (a literal color, like ``ConstantGlyph.color``) is the
    variable's DATA-TYPE wire color — LabVIEW colors a local variable's border
    by its type (boolean green, string pink, ...). It's None only when the type
    is unresolved, falling back to the neutral ``stroke_attr``."""

    label: str
    is_write: bool = False
    border_color: str | None = None
    fill_attr: str = "localvar_fill"
    stroke_attr: str = "localvar_stroke"
    text_attr: str = "localvar_text"
    max_lines: int = 2
    text_size: float = 7.0

    _STROKE_W = 2.5   # bold border for both read and write

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = self.border_color or getattr(theme, self.stroke_attr)
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


@dataclass
class ControlRefConstGlyph:
    """A Control Reference Constant (class="ctlRefConst"): a box bordered in the
    **referenced control's DATA-TYPE color** (boolean green, numeric orange, …),
    holding a small **reference arrow** and the **type text** (``TF``/``DBL``/
    ``abc``/…, the same ``type_repr`` we stamp on FP terminals) in that type
    color — with the control **name as a label ABOVE** the box. Its OUTPUT wire
    is a refnum (the reference wire color); the border is NOT the refnum color:
    LabVIEW type-colors the constant by the CONTROL it references, and only the
    wire it feeds is a reference wire.

    Distinct from a Local Variable (which carries a ▶ read/write badge this does
    NOT). Clean-room: the arrow and box are our own shapes (LabVIEW draws the
    control's own icon in the box); the type text and name are real data.
    ``type_color`` is the referenced control's type wire color; None falls back
    to the neutral stroke.
    """

    name: str
    type_text: str = ""
    type_color: str | None = None
    fill_attr: str = "localvar_fill"
    stroke_attr: str = "localvar_stroke"
    text_attr: str = "localvar_text"
    text_size: float = 7.0

    _STROKE_W = 1.6
    _ARROW_COLOR = "#111111"   # black shortcut/link-overlay arrow

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        color = self.type_color or getattr(theme, self.stroke_attr)
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr), stroke=color,
            stroke_width=self._STROKE_W,
        )
        h = y2 - y1
        cy = (y1 + y2) / 2
        pad = 2.5
        text_left = x1 + pad
        s = min(h - 3.0, 12.0)
        if s >= 6.0:
            self._shortcut_arrow(backend, x1 + 2.0, cy - s / 2, s)
            text_left = x1 + 2.0 + s + 2.0
        # The TYPE (TF / DBL / abc / …) fills the box, in the type color — this
        # IS what LabVIEW draws inside a control-reference constant.
        if self.type_text:
            backend.text(
                (text_left + x2 - pad) / 2, cy + self.text_size * 0.34,
                self.type_text, self.text_size, fill=color, anchor="middle",
            )
        # The control NAME is a label ABOVE the box (LabVIEW's own placement),
        # in the neutral label color.
        if self.name:
            backend.text(
                (x1 + x2) / 2, y1 - 2.0, self.name, self.text_size,
                fill=getattr(theme, self.text_attr), anchor="middle",
            )

    def _shortcut_arrow(
        self, backend: Backend, ix: float, iy: float, s: float,
    ) -> None:
        """A black Windows-style shortcut/link overlay: a curved arrow rising
        from the lower-left and hooking to point up-right (the reference mark)."""
        c = self._ARROW_COLOR
        p0 = (ix + 0.18 * s, iy + 0.86 * s)   # tail, lower-left
        cp = (ix + 0.18 * s, iy + 0.30 * s)   # control -> hook up the left side
        p1 = (ix + 0.86 * s, iy + 0.20 * s)   # tip, upper-right
        n = 8
        pts = [
            (
                (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * cp[0] + t * t * p1[0],
                (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * cp[1] + t * t * p1[1],
            )
            for t in (i / n for i in range(n + 1))
        ]
        backend.path(pts, stroke=c, stroke_width=1.2, fill="none")
        # Arrowhead at the tip, aligned to the final tangent (cp -> p1).
        ang = math.atan2(p1[1] - cp[1], p1[0] - cp[0])
        ah = 0.34 * s
        backend.polygon(
            [
                p1,
                (p1[0] - ah * math.cos(ang - 0.5), p1[1] - ah * math.sin(ang - 0.5)),
                (p1[0] - ah * math.cos(ang + 0.5), p1[1] - ah * math.sin(ang + 0.5)),
            ],
            fill=c, stroke=None,
        )


# One FIXED font size for every operator-symbol glyph — arithmetic/comparison
# triangles, Select, comparison-to-0, and the boolean logic gates — so the
# symbols are identical in size everywhere on the diagram, independent of each
# node's own bounds.
_OPERATOR_SYMBOL_SIZE = 9.0


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
        size = _OPERATOR_SYMBOL_SIZE
        cy = (y1 + y2) / 2
        backend.text(x1 + (x2 - x1) * 0.36, cy + size * 0.34, self.symbol, size,
                     fill=getattr(theme, self.text_attr))


_GATE_ARC_SEGMENTS = 14


def _quad_bezier_points(
    p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float],
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


@dataclass(frozen=True)
class BooleanGateGlyph:
    """The logic-gate SILHOUETTE LabVIEW's own boolean primitives use —
    clean-room curves (quadratic-Bezier-sampled; see ``_quad_bezier_points``)
    matching each gate's outline/aspect from the NI public function-reference
    images (goal #99), not traced NI pixels:

    - ``kind="and"``: a "D" — flat left (input) edge, straight top/bottom,
      a semicircular bulge on the right (the output tip).
    - ``kind="or"``/``kind="xor"``: a pointed "shield" — convex top/bottom
      curves converging to a point on the right (output), a concave scoop on
      the left (input edge). ``xor`` adds a second, detached concave arc just
      outside that left edge (stroke-only, no fill) — the extra "double
      line" XOR gates always carry.
    - ``kind="not"``: the same borderless right-pointing triangle as
      ``ArithGlyph`` — LabVIEW draws Not with no distinct gate body, just the
      triangle plus an input bubble.

    ``negated`` draws a small unfilled circle at the OUTPUT (right) tip —
    Not And / Not Or / Not Exclusive Or. ``input_bubble`` draws it at the
    INPUT (left) edge instead — Not itself. A gate uses at most one of the
    two in practice."""

    symbol: str
    kind: str = "and"  # "and" | "or" | "xor" | "not"
    negated: bool = False
    input_bubble: bool = False
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"
    bubble_fill_attr: str = "canvas"
    stroke_width: float = 1.2

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        fill = getattr(theme, self.fill_attr)
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        cy = (y1 + y2) / 2

        if self.kind == "not":
            backend.polygon(
                [(x1, y1), (x2, cy), (x1, y2)],
                fill=fill, stroke=stroke, stroke_width=self.stroke_width,
            )
            in_x, out_x = x1, x2
            sym_x = x1 + (x2 - x1) * 0.36
        elif self.kind == "and":
            out_x = self._draw_and(backend, bounds, fill, stroke)
            in_x = x1
            sym_x = x1 + (out_x - x1) * 0.42
        else:  # "or" / "xor"
            self._draw_or(backend, bounds, fill, stroke, extra_arc=self.kind == "xor")
            in_x, out_x = x1, x2
            sym_x = x1 + (x2 - x1) * 0.40

        r = max(1.4, min(3.2, min(x2 - x1, y2 - y1) * 0.11))
        bubble_fill = getattr(theme, self.bubble_fill_attr)
        if self.input_bubble:
            backend.circle(
                in_x - r, cy, r, fill=bubble_fill, stroke=stroke, stroke_width=1.0,
            )
        if self.negated:
            backend.circle(
                out_x + r, cy, r, fill=bubble_fill, stroke=stroke, stroke_width=1.0,
            )

        size = _OPERATOR_SYMBOL_SIZE
        backend.text(sym_x, cy + size * 0.34, self.symbol, size, fill=text_fill)

    @staticmethod
    def _draw_and(
        backend: Backend, bounds: Rect, fill: str, stroke: str,
    ) -> float:
        """Flat-left / semicircle-right "D" outline. Returns the output
        (bulge) tip's x — the right-most point, for bubble placement."""
        x1, y1, x2, y2 = bounds
        cy = (y1 + y2) / 2
        r = min((y2 - y1) / 2, (x2 - x1) * 0.5)
        x_flat = x2 - r
        # Arc from the top-flat corner (cy - r), through the rightmost bulge
        # point (x_flat + r, cy), to the bottom-flat corner (cy + r). When
        # ``r`` is height-limited (the normal, roughly-square case) this
        # meets ``y1``/``y2`` exactly, so the top/bottom flat edges below are
        # plain horizontal lines; a width-clamped ``r`` degrades gracefully
        # to a short diagonal instead of a discontinuity.
        arc = [
            (x_flat + r * math.cos(t), cy + r * math.sin(t))
            for t in (
                -math.pi / 2 + i * math.pi / _GATE_ARC_SEGMENTS
                for i in range(_GATE_ARC_SEGMENTS + 1)
            )
        ]
        pts = [(x1, y1), *arc, (x1, y2), (x1, y1)]
        backend.path(pts, stroke=stroke, stroke_width=1.2, fill=fill)
        return x2

    @staticmethod
    def _draw_or(
        backend: Backend, bounds: Rect, fill: str, stroke: str, *, extra_arc: bool,
    ) -> None:
        """Pointed "shield" outline: convex top/bottom curves meeting at a
        right-hand tip, concave scoop on the left. ``extra_arc`` (Exclusive
        Or) adds a second, detached concave stroke just outside the left
        edge, unfilled."""
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        cy = (y1 + y2) / 2
        tip = (x2, cy)
        top_left = (x1, y1)
        bottom_left = (x1, y2)
        top_ctrl = (x1 + w * 0.55, y1 - h * 0.08)
        bottom_ctrl = (x1 + w * 0.55, y2 + h * 0.08)
        left_ctrl = (x1 + w * 0.28, cy)

        outline = [
            *_quad_bezier_points(top_left, top_ctrl, tip),
            *_quad_bezier_points(tip, bottom_ctrl, bottom_left)[1:],
            *_quad_bezier_points(bottom_left, left_ctrl, top_left)[1:],
        ]
        backend.path(outline, stroke=stroke, stroke_width=1.2, fill=fill)

        if extra_arc:
            outset = max(2.0, w * 0.10)
            top_left2 = (x1 - outset, y1)
            bottom_left2 = (x1 - outset, y2)
            left_ctrl2 = (x1 - outset + w * 0.28, cy)
            second = _quad_bezier_points(top_left2, left_ctrl2, bottom_left2)
            backend.path(second, stroke=stroke, stroke_width=1.2, fill="none")


def draw_split_box(
    backend: Backend, bounds: Rect, theme: Theme, *,
    symbol: str, num_cells: int, symbol_side: str,
    fill_attr: str = "prim_fill", stroke_attr: str = "prim_stroke",
    text_attr: str = "prim_text", stroke_width: float = 1.2,
    cell_labels: tuple[str, ...] | None = None, sym_w: float | None = None,
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
                cell_x1 + lpad, (ry1 + ry2) / 2 + lsize * 0.34, label, lsize,
                anchor="start", fill=getattr(theme, text_attr),
            )

    size = max(6.0, min(15.0, height * 0.62, sym_w * 0.85))
    backend.text(
        (sym_x1 + sym_x2) / 2, (y1 + y2) / 2 + size * 0.34, symbol, size,
        fill=getattr(theme, text_attr),
    )


# Compound Arithmetic operator -> symbol (raw ``PrimitiveNode.operation``
# strings, lowercase — not yet boolean-translated by codegen; for rendering
# we just show the operator's own symbol). An unmapped operation (e.g. the
# "unsupported" sentinel for a dcoFiller code we haven't verified) degrades
# to "?" so the node still renders (see CompoundArithGlyph.draw).
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
            symbol=_CPD_ARITH_SYMBOL.get(self.operation, "?"),
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
        # Same skeleton as positional Bundle/Unbundle: a narrow cluster-direction
        # arrow cell (RIGHT when bundling, LEFT when unbundling) plus one row per
        # field — here the rows are LABELED with the field names. The arrow cell
        # is kept thin (~one row tall) since By-Name boxes grow with field count.
        x1, y1, x2, y2 = bounds
        rows = self.names or ("",)
        n = len(rows)
        arrow_w = min((x2 - x1) * 0.33, max(10.0, (y2 - y1) / n))
        draw_split_box(
            backend, bounds, theme,
            symbol=_CLUSTER_ARROW, num_cells=n,
            symbol_side="right" if self.bundling else "left",
            fill_attr=self.fill_attr, stroke_attr=self.stroke_attr,
            text_attr=self.text_attr, cell_labels=rows, sym_w=arrow_w,
        )


@dataclass(frozen=True)
class EventDataGlyph:
    """An Event Structure's Event Data Node / Event Filter Node (heap class
    ``eventDataNode`` for BOTH — see parser/nodes/event.py). Faithful to the
    reference LabVIEW screenshot: a WHITE box (never the tan Bundle/Unbundle-
    By-Name look — this isn't a real cluster assemble/disassemble), one row
    per accessed field, each row's NAME colored by that field's OWN LVType via
    ``wire_style()`` — the same data-driven table that colors wires (int blue,
    string pink, refnum green, ...) — never a fixed palette. A single thin
    vertical accent band marks the node's OUTER edge: the Data Node sits on
    the frame's data-in side (fields flow OUT of it into the diagram), so its
    band is on the LEFT; the Filter Node sits on the opposite side (the
    diagram writes INTO its filterable fields), so its band is on the RIGHT.
    ``is_filter`` (resolved by the caller from ``EventStructureNode.
    filter_node_uids`` — same heap class either way, see render/nodes.py)
    picks which side.
    """

    rows: tuple[tuple[str, LVType | None], ...]
    is_filter: bool = False
    fill_attr: str = "const_fill"     # white
    stroke_attr: str = "tunnel_border"
    band_attr: str = "tunnel_border"
    text_size: float = 7.5

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        backend.rect(
            x1, y1, x2, y2, fill=getattr(theme, self.fill_attr),
            stroke=stroke, stroke_width=1.2,
        )
        rows = self.rows or (("", None),)
        n = len(rows)
        row_h = (y2 - y1) / n
        lsize = max(5.0, min(self.text_size, row_h * 0.62))

        band_w = max(2.0, min(5.0, (x2 - x1) * 0.10))
        band = getattr(theme, self.band_attr)
        if self.is_filter:
            backend.rect(x2 - band_w, y1, x2, y2, fill=band, stroke="none")
            text_x1, text_x2 = x1 + 3.0, x2 - band_w - 3.0
        else:
            backend.rect(x1, y1, x1 + band_w, y2, fill=band, stroke="none")
            text_x1, text_x2 = x1 + band_w + 3.0, x2 - 3.0
        avail = max(2.0, text_x2 - text_x1)

        for i, (name, lv_type) in enumerate(rows):
            ry1 = y1 + i * row_h
            ry2 = ry1 + row_h
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=0.75)
            color = wire_style(lv_type, theme).color
            label = fit_label(name, avail, backend, lsize)
            backend.text(
                text_x1, (ry1 + ry2) / 2 + lsize * 0.34, label, lsize,
                anchor="start", fill=color,
            )


# ARRAY family (goal #14 follow-up): a row of small "element boxes" reads as
# an array the same way LabVIEW's own array shell does, without tracing NI
# artwork. The fixed-shape members (Size/Reverse/Search/Sort/Split) always
# draw a decorative row of 3 boxes — none of these primitives' terminal
# counts say anything about array length, so 3 is just the motif, not data.
_ARRAY_ELEMENTS_N = 3


def _draw_element_boxes(
    backend: Backend, x1: float, y1: float, x2: float, y2: float, n: int,
    stroke: str, stroke_width: float,
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
            ccx - side / 2, cy - side / 2, ccx + side / 2, cy + side / 2,
            fill="none", stroke=stroke, stroke_width=stroke_width,
        )


def _draw_arrow(
    backend: Backend, x_from: float, x_to: float, y: float, *,
    stroke: str, stroke_width: float, head_len: float, head_half_h: float,
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
        [(x_from, y), (shaft_end, y)], stroke=stroke, stroke_width=stroke_width,
    )
    backend.polygon(
        [(x_to, y), (shaft_end, y - head_half_h), (shaft_end, y + head_half_h)],
        fill=stroke, stroke=None,
    )


def _draw_node_tile(
    backend: Backend, bounds: Rect, theme: Theme, fill_attr: str, stroke_attr: str,
) -> None:
    """The filled node tile every primitive sits on (same as WrappedBoxGlyph's
    box / ArithGlyph's filled triangle). A motif glyph draws this FIRST, then its
    symbol on top — so it reads as a real node instead of strokes floating on the
    diagram."""
    x1, y1, x2, y2 = bounds
    backend.rect(
        x1, y1, x2, y2,
        fill=getattr(theme, fill_attr), stroke=getattr(theme, stroke_attr),
        stroke_width=max(1.0, min(x2 - x1, y2 - y1) * 0.05),
    )


@dataclass(frozen=True)
class ArraySizeGlyph:
    """Array Size: a row of element boxes over a dimension bracket labeled
    "n" — the array's length shown as a bracketed span beneath its
    elements, matching the approved proof sketch (``g_size``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.04, x1 + w * 0.66
        row_y1, row_y2 = y1 + h * 0.10, y1 + h * 0.52
        _draw_element_boxes(
            backend, row_x1, row_y1, row_x2, row_y2, _ARRAY_ELEMENTS_N, stroke, sw,
        )
        by1, by2 = y1 + h * 0.66, y1 + h * 0.80
        backend.path(
            [(row_x1, by1), (row_x1, by2), (row_x2, by2), (row_x2, by1)],
            stroke=stroke, stroke_width=sw,
        )
        size = max(6.0, min(12.0, h * 0.42))
        backend.text(
            x1 + w * 0.85, by2 + size * 0.28, "n", size,
            fill=text_fill, bold=True,
        )


@dataclass(frozen=True)
class ArrayReverseGlyph:
    """Reverse 1D Array: element boxes with a STRAIGHT backward (right-to-
    left) arrow above them — a curved arc would read as "loop", not
    "reverse", matching the approved proof sketch (``g_reverse``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.17, x1 + w * 0.65
        row_y1, row_y2 = y1 + h * 0.47, y1 + h * 0.82
        _draw_element_boxes(
            backend, row_x1, row_y1, row_x2, row_y2, _ARRAY_ELEMENTS_N, stroke, sw,
        )
        arrow_y = y1 + h * 0.24
        _draw_arrow(
            backend, row_x2, row_x1, arrow_y,
            stroke=stroke, stroke_width=sw,
            head_len=max(3.0, w * 0.07), head_half_h=max(2.0, h * 0.09),
        )


@dataclass(frozen=True)
class ArraySearchGlyph:
    """Search 1D Array: element boxes with a magnifier (circle + short
    handle) over one of them, matching the approved proof sketch
    (``g_search``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.04, x1 + w * 0.60
        row_y1, row_y2 = y1 + h * 0.38, y1 + h * 0.74
        _draw_element_boxes(
            backend, row_x1, row_y1, row_x2, row_y2, _ARRAY_ELEMENTS_N, stroke, sw,
        )
        r = max(2.0, min(w, h) * 0.18)
        cx, cy = x1 + w * 0.74, y1 + h * 0.30
        backend.circle(cx, cy, r, fill="none", stroke=stroke, stroke_width=sw)
        hx, hy = r * 0.78, r * 0.78
        backend.path(
            [(cx + hx * 0.65, cy + hy * 0.65), (cx + hx * 1.55, cy + hy * 1.55)],
            stroke=stroke, stroke_width=sw,
        )


@dataclass(frozen=True)
class ArraySortGlyph:
    """Sort 1D Array: element boxes with ascending bars beside them — NO
    arrow (sorting implies resulting order, not travel direction), matching
    the approved proof sketch (``g_sort``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.06, x1 + w * 0.62
        row_y1, row_y2 = y1 + h * 0.38, y1 + h * 0.74
        _draw_element_boxes(
            backend, row_x1, row_y1, row_x2, row_y2, _ARRAY_ELEMENTS_N, stroke, sw,
        )
        n_bars = 3
        bars_x1, bars_x2 = x1 + w * 0.68, x2 - w * 0.04
        span = bars_x2 - bars_x1
        bar_w = span / n_bars * 0.6
        step = span / n_bars
        base_y = y1 + h * 0.80
        max_bar_h = h * 0.56
        for i in range(n_bars):
            bh = max_bar_h * (i + 1) / n_bars
            bx = bars_x1 + i * step
            backend.rect(bx, base_y - bh, bx + bar_w, base_y, fill=stroke)


@dataclass(frozen=True)
class ArraySplitGlyph:
    """Split 1D Array: one array splitting into two, matching the approved
    proof sketch (``g_split``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.04, x1 + w * 0.36
        row_y1, row_y2 = y1 + h * 0.38, y1 + h * 0.74
        _draw_element_boxes(backend, row_x1, row_y1, row_x2, row_y2, 2, stroke, sw)
        arrow_y = (row_y1 + row_y2) / 2
        arrow_x2 = x1 + w * 0.55
        _draw_arrow(
            backend, row_x2, arrow_x2, arrow_y,
            stroke=stroke, stroke_width=sw,
            head_len=max(3.0, w * 0.06), head_half_h=max(2.0, h * 0.08),
        )
        side = min(w, h) * 0.22
        box_x1 = x1 + w * 0.60
        backend.rect(
            box_x1, y1 + h * 0.10, box_x1 + side, y1 + h * 0.10 + side,
            fill="none", stroke=stroke, stroke_width=sw,
        )
        backend.rect(
            box_x1, y1 + h * 0.58, box_x1 + side, y1 + h * 0.58 + side,
            fill="none", stroke=stroke, stroke_width=sw,
        )


@dataclass(frozen=True)
class ArrayBuildGlyph:
    """Build Array (``aBuild``): a DRAWER that grows a row taller per wired
    input — the input terminals themselves make the rows, so the node's bounds
    (and this tile) grow for free. The body carries a clean-room ARRAY BRACKET
    enclosing a column of element cells (the appended array), modelled on the
    real LabVIEW Build Array icon — so it reads as array-assembly and is NOT
    mistaken for a Bundle (which uses the generic ``draw_split_box`` skeleton).
    ``num_inputs`` is kept for reference; growth comes from the bounds."""

    num_inputs: int = 1
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.045)
        # Array bracket [ ] enclosing a stacked column of element cells.
        bx1, bx2 = x1 + w * 0.28, x2 - w * 0.12
        by1, by2 = y1 + h * 0.13, y2 - h * 0.13
        foot = (bx2 - bx1) * 0.28
        backend.path(
            [(bx1 + foot, by1), (bx1, by1), (bx1, by2), (bx1 + foot, by2)],
            stroke=stroke, stroke_width=sw,
        )
        backend.path(
            [(bx2 - foot, by1), (bx2, by1), (bx2, by2), (bx2 - foot, by2)],
            stroke=stroke, stroke_width=sw,
        )
        cx1, cx2 = bx1 + foot * 1.15, bx2 - foot * 1.15
        n_cells = max(2, min(5, int((by2 - by1) / max(1.0, w * 0.24))))
        gap = (by2 - by1) / n_cells
        cell_h = gap * 0.6
        for i in range(n_cells):
            cyy = by1 + i * gap + (gap - cell_h) / 2
            backend.rect(
                cx1, cyy, cx2, cyy + cell_h,
                fill="none", stroke=stroke, stroke_width=sw * 0.8,
            )


@dataclass(frozen=True)
class ConvertGlyph:
    """A numeric/type CONVERTER primitive: a small entering arrow feeding a
    bold target-type abbreviation ("going to that type") — e.g. "To Long
    Integer" -> "I32". Matches the approved proof sketch (``conv``):
    monochrome outline arrow, bold monospace abbreviation, scaled to the
    node's real bounds (no fixed pixel size)."""

    abbr: str
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        cy = (y1 + y2) / 2
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.07)
        arrow_x1, arrow_x2 = x1 + w * 0.06, x1 + w * 0.30
        _draw_arrow(
            backend, arrow_x1, arrow_x2, cy,
            stroke=stroke, stroke_width=sw,
            head_len=max(3.0, w * 0.08), head_half_h=max(2.0, h * 0.14),
        )
        text_x = x1 + w * 0.34
        avail_w = max(2.0, (x2 - w * 0.04) - text_x)
        size = max(6.0, min(15.0, h * 0.55))
        while size > 6.0 and backend.measure_text(self.abbr, size) > avail_w:
            size -= 0.5
        backend.text(
            text_x, cy + size * 0.34, self.abbr, size,
            fill=text_fill, bold=True, mono=True, anchor="start",
        )


# Shared drawer-row geometry for PropertyNodeGlyph and InvokeNodeGlyph: a
# fixed left gutter wide enough for one arrow, so a row's label starts at the
# same x whether or not that row actually draws a left arrow.
_ROW_LPAD = 3.0
_ROW_ARROW_W = 7.0
_ROW_GUTTER = _ROW_ARROW_W + _ROW_LPAD


def _draw_drawer_row(
    backend: Backend, x1: float, x2: float, ry1: float, ry2: float,
    label: str, *, show_left: bool, show_right: bool,
    text_fill: str, lsize: float,
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
            x1 + _ROW_ARROW_W * 0.45, cy + lsize * 0.34, "▸", lsize,
            fill=text_fill,
        )
    avail = (x2 - x1) - _ROW_GUTTER - _ROW_LPAD - (_ROW_ARROW_W if show_right else 0.0)
    backend.text(
        x1 + _ROW_GUTTER, cy + lsize * 0.34,
        fit_label(label, avail, backend, lsize), lsize,
        anchor="start", fill=text_fill,
    )
    if show_right:
        backend.text(
            x2 - _ROW_ARROW_W * 0.55, cy + lsize * 0.34, "▸", lsize,
            fill=text_fill,
        )


@dataclass(frozen=True)
class PropertyNodeGlyph:
    """A Property Node (heap class ``propNode``), matching LabVIEW's layout: a
    HEADER band naming the object CLASS the reference is of (``⚙ <class>``), above
    a DRAWER of one rectangle per accessed property. The reference (in/out) and
    error (in/out) terminals thread the box edges at the header level and are
    placed by the scene, not drawn here.

    Each drawer row is LABELED with the property's NAME and marked with the
    shared arrow rule (see ``_draw_drawer_row``): read (value flows OUT) draws
    ``▸`` at the RIGHT edge, write (value flows IN) draws it at the LEFT edge.
    A property is read-or-write in practice, so a row draws at most one arrow.
    Names come from the node's ``properties`` list; the per-row direction from
    the matching value terminal. Grows with the property count, scaling to the
    node's heap bounds."""

    rows: tuple[tuple[str, bool], ...]  # (property name, is_read)
    class_name: str = ""  # object class shown in the header (e.g. "VI")
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        backend.rect(
            x1, y1, x2, y2, fill=getattr(theme, self.fill_attr),
            stroke=stroke, stroke_width=1.2,
        )
        rows = self.rows or (("", True),)
        # One header cell (the class/reference row) + one cell per property.
        cell_h = (y2 - y1) / (len(rows) + 1)
        lpad = 3.0
        lsize = max(5.0, min(9.0, cell_h * 0.62) - 1.0)

        # Header band: the object class, centered, with a divider beneath. This
        # is the row the reference + error terminals thread through (drawn by the
        # scene at the box edges).
        hy2 = y1 + cell_h
        header = f"⚙ {self.class_name}".strip() if self.class_name else "⚙ class"
        backend.text(
            (x1 + x2) / 2, y1 + cell_h / 2 + lsize * 0.34,
            fit_label(header, (x2 - x1) - 2 * lpad, backend, lsize), lsize,
            fill=text_fill,
        )
        backend.line(x1, hy2, x2, hy2, stroke=stroke, stroke_width=1.0)

        # Property drawer: one named rectangle per property, below the header.
        for i, (name, is_read) in enumerate(rows):
            ry1 = hy2 + i * cell_h
            ry2 = hy2 + (i + 1) * cell_h
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=1.0)
            _draw_drawer_row(
                backend, x1, x2, ry1, ry2, name,
                show_left=not is_read, show_right=is_read,
                text_fill=text_fill, lsize=lsize,
            )


@dataclass(frozen=True)
class InvokeNodeGlyph:
    """An Invoke Node (heap class ``invokeNode``): like a Property Node, but the
    invoked METHOD name is the first drawer row, and the method's parameters
    grow DOWNWARD beneath it. The reference (in/out) and error (in/out)
    terminals thread the header edges and are placed by the scene.

    Row count and content come from the heap's ``dcoList`` (method + params,
    NOT one row per raw terminal — see ``render/nodes.py:_invoke_node_glyph``):
    the method row's left side is always a Void select-slot, so it never draws
    an arrow -- just the method name; its right side is the return value,
    drawn with the shared arrow rule (``▸`` right, only if present). Parameter
    NAMES aren't in the VI file (they belong to the method's VI-server
    signature), so param rows are labeled by index (``[i]``); each side draws
    its arrow via the same rule -- a plain input gets a left ``▸``, a plain
    output a right ``▸``, and a pass-through param (both present) gets both.
    Scales to the node's heap bounds."""

    method: str = ""
    return_present: bool = False  # method row's return-value terminal (right ▸)
    # (param label, show_left, show_right)
    rows: tuple[tuple[str, bool, bool], ...] = ()
    class_name: str = ""  # object class shown in the header (e.g. "VI")
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        backend.rect(
            x1, y1, x2, y2, fill=getattr(theme, self.fill_attr),
            stroke=stroke, stroke_width=1.2,
        )
        rows = self.rows
        # header (class) + method row + one cell per parameter row.
        cell_h = (y2 - y1) / (len(rows) + 2)
        lpad = 3.0
        lsize = max(5.0, min(9.0, cell_h * 0.62) - 1.0)

        # Header band: the object class, centered, with a divider beneath.
        hy2 = y1 + cell_h
        header = f"⚙ {self.class_name}".strip() if self.class_name else "⚙ class"
        backend.text(
            (x1 + x2) / 2, y1 + cell_h / 2 + lsize * 0.34,
            fit_label(header, (x2 - x1) - 2 * lpad, backend, lsize), lsize,
            fill=text_fill,
        )
        backend.line(x1, hy2, x2, hy2, stroke=stroke, stroke_width=1.0)

        # Method row: the invoked method name, never a left arrow (that side is
        # always a Void select-slot); the return value (if any) draws right.
        my2 = hy2 + cell_h
        method = self.method or "method"
        _draw_drawer_row(
            backend, x1, x2, hy2, my2, method,
            show_left=False, show_right=self.return_present,
            text_fill=text_fill, lsize=lsize,
        )
        backend.line(x1, my2, x2, my2, stroke=stroke, stroke_width=1.0)

        # Parameter drawer, one row per dcoList param pair below the method.
        for i, (name, show_left, show_right) in enumerate(rows):
            ry1 = my2 + i * cell_h
            ry2 = my2 + (i + 1) * cell_h
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=1.0)
            _draw_drawer_row(
                backend, x1, x2, ry1, ry2, name,
                show_left=show_left, show_right=show_right,
                text_fill=text_fill, lsize=lsize,
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

    def truncated_value(self, backend: Backend, bounds: Rect) -> str | None:
        """The FULL value, but ONLY when drawing it into ``bounds`` ellipsizes
        or clips it (so the caller can expose it as a hover tooltip). ``None``
        when the in-box text already shows the value in full — no redundant
        tooltip on short constants. Mirrors what ``draw`` fits into the box."""
        if not self.value:
            return None
        x1, y1, x2, y2 = bounds
        if not self.multiline:
            fitted = fit_value(self.value, x2 - x1, backend, self.text_size)
            return self.value if fitted != self.value else None
        pad = 2.5
        avail_w = x2 - x1 - 2 * pad
        line_h = self.text_size + 2.0
        max_lines = max(1, int((y2 - y1 - 2 * pad) / line_h))
        lines: list[str] = []
        for seg in self.value.split("\n"):
            if not seg:
                lines.append("")
                continue
            lines.extend(wrap_label(seg, avail_w, backend, self.text_size, max_lines))
        # Truncated if whole lines were clipped OR a line got ellipsized to fit
        # (wrap_label caps at max_lines and adds the "…" itself, so the line count
        # alone under-detects — check for the ellipsis it produced).
        if len(lines) > max_lines or any(ln.endswith("…") for ln in lines):
            return self.value
        return None

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
    """A raster VI icon (an ``_ICON.png`` vectorized to SVG), scaled to FILL
    its box, CENTERED, aspect preserved. Constructed only by
    ``nodes._vectorized_icon`` — real icons from disk, never procedural
    primitive art — so filling here can't stretch a drawn primitive."""

    fragment: str
    natural: tuple[int, int]  # (width, height) in the SVG's own viewBox units

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        bw, bh = x2 - x1, y2 - y1
        nw, nh = self.natural
        if nw <= 0 or nh <= 0:
            return
        # Fill the box (aspect preserved) — no 1.0 cap, so a small native icon
        # scales UP to its box instead of floating tiny in whitespace.
        scale = min(bw / nw, bh / nh)
        w, h = nw * scale, nh * scale
        ox, oy = x1 + (bw - w) / 2, y1 + (bh - h) / 2
        backend.raw_svg(self.fragment, ox, oy, w, h, viewbox=(nw, nh))


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

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
        )


@dataclass(frozen=True)
class ArithGlyph:
    """The arithmetic-primitive triangle (Add/Subtract/Multiply/Divide/
    Increment/Decrement) with its operator symbol."""

    symbol: str
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"

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
        backend.text(x1 + (x2 - x1) * 0.36, cy + size * 0.34, self.symbol, size)


@dataclass(frozen=True)
class ConstantGlyph:
    """A constant's colored box (color from its own ``LVType``, computed by
    the resolver — a literal color, not a theme attribute, since it varies
    per-node rather than per-role)."""

    value: str
    color: str
    fill_attr: str = "term_fill"
    text_size: float = 9.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill=theme.const_fill,
            stroke=self.color, stroke_width=1.2,
        )
        if self.value:
            backend.text(
                (x1 + x2) / 2, (y1 + y2) / 2 + 3,
                fit_label(self.value, x2 - x1, backend, self.text_size),
                self.text_size,
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

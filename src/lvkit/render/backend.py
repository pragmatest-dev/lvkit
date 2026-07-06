"""Rendering backend: one op vocabulary for every glyph/structure drawer.

Node glyphs and structure renderers emit the SAME backend ops — no imperative
side channel — so a future PNG/Canvas backend draws identically. ``Backend``
also provides text measurement so label-fitting isn't a fixed-px/char
heuristic: it needs to match whatever backend eventually renders the text.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from xml.sax.saxutils import escape

Point = tuple[float, float]

# Average glyph width as a fraction of font size, for a generic sans-serif
# face. A real backend (or a future PIL/PNG backend) can measure exactly;
# this table is deliberately per-glyph (not a flat px/char constant) so
# truncation decisions are reasonably font-shape-aware without a renderer.
_NARROW = set("iIl.,:;'|!ftjr ")
_WIDE = set("mMWw@")
_DEFAULT_EM = 0.58
_NARROW_EM = 0.30
_WIDE_EM = 0.92


def _text_width_em(text: str) -> float:
    total = 0.0
    for ch in text:
        if ch in _NARROW:
            total += _NARROW_EM
        elif ch in _WIDE:
            total += _WIDE_EM
        else:
            total += _DEFAULT_EM
    return total


@runtime_checkable
class Backend(Protocol):
    """Backend-agnostic drawing surface for block-diagram rendering."""

    def rect(
        self, x1: float, y1: float, x2: float, y2: float, *,
        fill: str | None = None, stroke: str | None = None,
        stroke_width: float | None = None, rx: float | None = None,
    ) -> None: ...

    def path(
        self, points: list[Point], *,
        stroke: str, stroke_width: float, fill: str = "none",
    ) -> None: ...

    def text(
        self, x: float, y: float, s: str, size: float, *,
        fill: str | None = None, italic: bool = False, bold: bool = False,
    ) -> None: ...

    def image(
        self, href: str, x: float, y: float, w: float, h: float, *,
        opacity: float | None = None,
    ) -> None: ...

    def polygon(
        self, points: list[Point], *,
        fill: str | None = None, stroke: str | None = None,
        stroke_width: float | None = None,
    ) -> None: ...

    def circle(
        self, cx: float, cy: float, r: float, *,
        fill: str | None = None, stroke: str | None = None,
        stroke_width: float | None = None,
    ) -> None: ...

    def line(
        self, x1: float, y1: float, x2: float, y2: float, *,
        stroke: str, stroke_width: float = 1.0,
    ) -> None: ...

    def measure_text(self, text: str, size: float) -> float:
        """Approximate rendered width of ``text`` at ``size`` px."""
        ...

    def raw_svg(
        self, fragment: str, x: float, y: float, w: float, h: float, *,
        viewbox: tuple[float, float],
    ) -> None:
        """Embed a hand-authored SVG fragment (JSON-declared node icons).

        ``fragment`` is written in its own local coordinate space of size
        ``viewbox`` (width, height); it is scaled into the ``(x, y, w, h)``
        box the same way an ``<img>`` would be. A non-SVG backend may
        legitimately no-op this (there is no universal raw-markup
        equivalent) — callers that need a guaranteed visual should treat
        this as best-effort, matching every other JSON-declared glyph path.
        """
        ...


class SvgBackend:
    """Renders block-diagram ops to a self-contained SVG string."""

    def __init__(self) -> None:
        self._elements: list[str] = []

    @staticmethod
    def _attrs(**attrs: str | float | None) -> str:
        parts = []
        for k, v in attrs.items():
            if v is None:
                continue
            parts.append(f'{k.replace("_", "-")}="{v}"')
        return " ".join(parts)

    def rect(
        self, x1: float, y1: float, x2: float, y2: float, *,
        fill: str | None = None, stroke: str | None = None,
        stroke_width: float | None = None, rx: float | None = None,
    ) -> None:
        a = self._attrs(
            fill=fill, stroke=stroke, stroke_width=stroke_width, rx=rx,
        )
        self._elements.append(
            f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{x2 - x1:.1f}" '
            f'height="{y2 - y1:.1f}" {a}/>'
        )

    def path(
        self, points: list[Point], *,
        stroke: str, stroke_width: float, fill: str = "none",
    ) -> None:
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
        self._elements.append(
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{stroke_width}" stroke-linejoin="round"/>'
        )

    def text(
        self, x: float, y: float, s: str, size: float, *,
        fill: str | None = None, italic: bool = False, bold: bool = False,
    ) -> None:
        a = self._attrs(
            fill=fill, font_style="italic" if italic else None,
            font_weight="bold" if bold else None,
        )
        self._elements.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'text-anchor="middle" {a}>{escape(s)}</text>'
        )

    def image(
        self, href: str, x: float, y: float, w: float, h: float, *,
        opacity: float | None = None,
    ) -> None:
        a = self._attrs(opacity=opacity)
        # LabVIEW icons are small (32x32-ish) pixel art scaled up to a
        # node's on-diagram bounds — without this, browsers smooth-scale
        # them and they read as a blurry smudge instead of crisp pixels.
        self._elements.append(
            f'<image href="{href}" x="{x:.1f}" y="{y:.1f}" '
            f'width="{w:.1f}" height="{h:.1f}" '
            f'style="image-rendering: pixelated" {a}/>'
        )

    def polygon(
        self, points: list[Point], *,
        fill: str | None = None, stroke: str | None = None,
        stroke_width: float | None = None,
    ) -> None:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        a = self._attrs(fill=fill, stroke=stroke, stroke_width=stroke_width)
        self._elements.append(f'<polygon points="{pts}" {a}/>')

    def circle(
        self, cx: float, cy: float, r: float, *,
        fill: str | None = None, stroke: str | None = None,
        stroke_width: float | None = None,
    ) -> None:
        a = self._attrs(fill=fill, stroke=stroke, stroke_width=stroke_width)
        self._elements.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" {a}/>'
        )

    def line(
        self, x1: float, y1: float, x2: float, y2: float, *,
        stroke: str, stroke_width: float = 1.0,
    ) -> None:
        self._elements.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{stroke_width}"/>'
        )

    def measure_text(self, text: str, size: float) -> float:
        return _text_width_em(text) * size

    def raw_svg(
        self, fragment: str, x: float, y: float, w: float, h: float, *,
        viewbox: tuple[float, float],
    ) -> None:
        vw, vh = viewbox
        self._elements.append(
            f'<svg x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'viewBox="0 0 {vw} {vh}" preserveAspectRatio="none">'
            f"{fragment}</svg>"
        )

    def render(
        self, bounds: tuple[float, float, float, float], *, title: str | None = None,
    ) -> str:
        """Wrap accumulated ops into a complete SVG document.

        ``title`` (e.g. the VI name), when given, is emitted as an SVG
        ``<title>`` element right after the root tag for accessibility.
        """
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x1:.0f} '
            f'{y1:.0f} {w:.0f} {h:.0f}" font-family="sans-serif">'
        )
        title_el = f"<title>{escape(title)}</title>" if title else None
        parts = [head, title_el, *self._elements, "</svg>"]
        return "\n".join(p for p in parts if p is not None)

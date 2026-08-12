"""Rendering backend: one op vocabulary for every glyph/structure drawer.

Node glyphs and structure renderers emit the SAME backend ops — no imperative
side channel — so a future PNG/Canvas backend draws identically. ``Backend``
also provides text measurement so label-fitting isn't a fixed-px/char
heuristic: it needs to match whatever backend eventually renders the text.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from xml.sax.saxutils import escape, quoteattr

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
        stroke_dasharray: str | None = None,
    ) -> None: ...

    def path(
        self, points: list[Point], *,
        stroke: str, stroke_width: float, fill: str = "none",
    ) -> None: ...

    def text(
        self, x: float, y: float, s: str, size: float, *,
        fill: str | None = None, italic: bool = False, bold: bool = False,
        anchor: str = "middle", mono: bool = False,
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

    def begin_group(
        self, *, cls: str | None = None, data: dict[str, str] | None = None,
        style: str | None = None, title: str | None = None,
        href: str | None = None,
    ) -> None:
        """Open a grouping container (``<g>``) — used for the interactive
        case-frame layering (``lv-frame``) and click targets (``lv-selector``).
        ``data`` becomes ``data-*`` attributes (keys sorted for determinism).
        ``title``, when given, is emitted as a ``<title>`` child so the whole
        group shows a native hover tooltip. ``href``, when given, wraps the
        group in an ``<a>`` (opens in a new tab) — e.g. a node linking to its
        NI docs page. Must be paired with ``end_group()``."""
        ...

    def end_group(self) -> None:
        """Close the most recently opened ``begin_group()``."""
        ...


class SvgBackend:
    """Renders block-diagram ops to a self-contained SVG string."""

    def __init__(self) -> None:
        self._elements: list[str] = []
        # Parallel to the open-group stack: whether each begin_group() also
        # opened an <a> wrapper (href given), so end_group() closes it too.
        self._anchor_stack: list[bool] = []

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
        stroke_dasharray: str | None = None,
    ) -> None:
        a = self._attrs(
            fill=fill, stroke=stroke, stroke_width=stroke_width, rx=rx,
            stroke_dasharray=stroke_dasharray,
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
        anchor: str = "middle", mono: bool = False,
    ) -> None:
        a = self._attrs(
            fill=fill, font_style="italic" if italic else None,
            font_weight="bold" if bold else None,
            font_family="monospace" if mono else None,
        )
        self._elements.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'text-anchor="{anchor}" {a}>{escape(s)}</text>'
        )

    def image(
        self, href: str, x: float, y: float, w: float, h: float, *,
        opacity: float | None = None,
    ) -> None:
        a = self._attrs(opacity=opacity)
        # LabVIEW icons are small (32x32-ish) pixel art scaled up to a
        # node's on-diagram bounds — the .lv-raster rule (image-rendering:
        # pixelated, in _BASE_CSS) keeps them crisp; without it browsers
        # smooth-scale them into a blurry smudge instead of crisp pixels.
        self._elements.append(
            f'<image class="lv-raster" href="{href}" x="{x:.1f}" y="{y:.1f}" '
            f'width="{w:.1f}" height="{h:.1f}" {a}/>'
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

    def begin_group(
        self, *, cls: str | None = None, data: dict[str, str] | None = None,
        style: str | None = None, title: str | None = None,
        href: str | None = None,
    ) -> None:
        if href is not None:
            self._elements.append(
                f"<a href={quoteattr(href)} target=\"_blank\" "
                f"rel=\"noopener\">"
            )
        self._anchor_stack.append(href is not None)
        attrs = []
        if cls is not None:
            attrs.append(f"class={quoteattr(cls)}")
        data = data or {}
        for k in sorted(data):
            attrs.append(f"data-{k}={quoteattr(data[k])}")
        if style is not None:
            attrs.append(f"style={quoteattr(style)}")
        suffix = (" " + " ".join(attrs)) if attrs else ""
        self._elements.append(f"<g{suffix}>")
        if title is not None:
            self._elements.append(f"<title>{escape(title)}</title>")

    def end_group(self) -> None:
        self._elements.append("</g>")
        if self._anchor_stack and self._anchor_stack.pop():
            self._elements.append("</a>")

    def render(
        self, bounds: tuple[float, float, float, float], *, title: str | None = None,
        script: str | None = None, root_id: str | None = None,
        style: str | None = None, extra_attrs: dict[str, str] | None = None,
    ) -> str:
        """Wrap accumulated ops into a complete SVG document.

        ``title`` (e.g. the VI name), when given, is exposed as the root
        ``<svg role="img" aria-label=...>`` accessible name — NOT a ``<title>``
        child, which would be a whole-diagram browser tooltip that pops up
        wherever the pointer pauses and occludes the diagram.
        ``root_id``, when given, is set as the root ``<svg id=...>`` so
        inline ``script`` can scope its DOM queries to this one document
        (needed so multiple inlined SVGs on one HTML page don't collide).
        ``style``, when given, is emitted as a ``<style>`` element (static,
        author-controlled CSS — e.g. the ``.lv-node:hover`` connector-pane
        reveal rule — not escaped) right after ``title``. ``script``, when
        given, is appended as an inline ``<script>`` right before
        ``</svg>`` — author-controlled JS (the frame-toggle controller),
        not escaped. Its body is wrapped in a comment-guarded CDATA section
        (``/*<![CDATA[*/ … /*]]>*/``) so a standalone ``.svg`` (parsed as
        strict XML, where a ``<`` in the JS would be an invalid start-tag)
        AND inline SVG in HTML (where the markers are just JS comments)
        both parse. It must not contain a literal ``</script>`` or the
        CDATA terminator ``]]>``.
        ``extra_attrs``, when given, becomes ``data-<key>="<value>"`` attributes
        on the root ``<svg>`` (keys sorted for determinism), quoted/escaped via
        ``quoteattr`` — e.g. task #19's ``data-lv-properties``/``data-lv-
        structure`` compact-JSON payloads, the single carrier both the viewer
        chrome and a host (which only ever sees the raw SVG) read from.
        """
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        id_attr = f" id={quoteattr(root_id)}" if root_id is not None else ""
        # The VI name is exposed via role="img" + aria-label, NOT a root <title>
        # child: a root <title> is the browser's tooltip for the WHOLE svg, so it
        # pops up wherever the pointer pauses and occludes the diagram. aria-label
        # gives the same accessible name with no hover tooltip. (Per-node <title>s,
        # emitted by start_group, are unaffected — they're intentional node hovers.)
        aria_attr = f' role="img" aria-label={quoteattr(title)}' if title else ""
        attrs = extra_attrs or {}
        data_attr = "".join(
            f" data-{k}={quoteattr(attrs[k])}" for k in sorted(attrs)
        )
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg"{id_attr}{aria_attr}{data_attr} '
            f'viewBox="{x1:.0f} {y1:.0f} {w:.0f} {h:.0f}" font-family="sans-serif">'
        )
        title_el = None
        style_el = f"<style>{style}</style>" if style else None
        script_el = None
        if script is not None:
            if "</script>" in script or "]]>" in script:
                raise ValueError(
                    "script must not contain a literal </script> or ]]>"
                )
            script_el = f"<script>/*<![CDATA[*/\n{script}\n/*]]>*/</script>"
        parts = [head, title_el, style_el, *self._elements, script_el, "</svg>"]
        return "\n".join(p for p in parts if p is not None)

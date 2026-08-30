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


def _stroke_inset(stroke: str | None, stroke_width: float | None) -> float:
    """THE single definition of the outline inset, in one place so no drawer
    ever re-derives or forgets it.

    SVG strokes are CENTERED on the path, so an outline drawn at the raw bounds
    bleeds ``stroke_width / 2`` OUTSIDE the shape — the box then reads larger
    than its bounds. Every stroked *bounded* shape (rect/polygon/circle) pulls
    its outer boundary inward by this amount so the stroke's OUTER edge lands on
    the bounds. Zero when there's no visible outline (no stroke, or width 0/None),
    so an un-stroked fill is never moved."""
    return stroke_width / 2.0 if (stroke is not None and stroke_width) else 0.0


def _inset_polygon(points: list[Point], d: float) -> list[Point]:
    """Offset a CLOSED convex polygon inward by ``d`` (perpendicular to every
    edge), so a centered stroke of width ``2d`` stays inside the shape's bounds.
    Offsets each edge line inward and intersects consecutive offset lines for the
    new vertices — exact for the convex glyph shapes (triangles, gates). Returns
    the input unchanged for a degenerate polygon (< 3 pts, zero-length edge,
    parallel neighbours)."""
    n = len(points)
    if n < 3 or d <= 0:
        return points
    area = sum(
        points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
        for i in range(n)
    )
    s = 1.0 if area > 0 else -1.0  # inward-normal sign by winding
    lines: list[tuple[float, float, float, float]] = []
    for i in range(n):
        (x1, y1), (x2, y2) = points[i], points[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            return points
        nx, ny = -dy / length * s, dx / length * s  # inward normal
        lines.append((x1 + nx * d, y1 + ny * d, dx, dy))
    out: list[Point] = []
    for i in range(n):
        px1, py1, dx1, dy1 = lines[(i - 1) % n]
        px2, py2, dx2, dy2 = lines[i]
        denom = dx1 * dy2 - dy1 * dx2
        if abs(denom) < 1e-9:
            return points  # parallel neighbours — bail rather than blow up
        t = ((px2 - px1) * dy2 - (py2 - py1) * dx2) / denom
        out.append((px1 + dx1 * t, py1 + dy1 * t))
    return out


@runtime_checkable
class Backend(Protocol):
    """Backend-agnostic drawing surface for block-diagram rendering."""

    def rect(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        stroke_width: float | None = None,
        rx: float | None = None,
        stroke_dasharray: str | None = None,
    ) -> None: ...

    def path(
        self,
        points: list[Point],
        *,
        stroke: str,
        stroke_width: float,
        fill: str = "none",
    ) -> None: ...

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float,
        *,
        fill: str | None = None,
        italic: bool = False,
        bold: bool = False,
        anchor: str = "middle",
        mono: bool = False,
    ) -> None: ...

    def image(
        self,
        href: str,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        opacity: float | None = None,
    ) -> None: ...

    def polygon(
        self,
        points: list[Point],
        *,
        fill: str | None = None,
        stroke: str | None = None,
        stroke_width: float | None = None,
    ) -> None: ...

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        stroke_width: float | None = None,
    ) -> None: ...

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke: str,
        stroke_width: float = 1.0,
    ) -> None: ...

    def measure_text(self, text: str, size: float) -> float:
        """Approximate rendered width of ``text`` at ``size`` px."""
        ...

    def raw_svg(
        self,
        fragment: str,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
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
        self,
        *,
        cls: str | None = None,
        data: dict[str, str] | None = None,
        style: str | None = None,
        title: str | None = None,
        href: str | None = None,
        clip: tuple[float, float, float, float] | None = None,
    ) -> None:
        """Open a grouping container (``<g>``) — used for the interactive
        case-frame layering (``lv-frame``) and click targets (``lv-selector``).
        ``data`` becomes ``data-*`` attributes (keys sorted for determinism).
        ``title``, when given, is emitted as a ``<title>`` child so the whole
        group shows a native hover tooltip. ``href``, when given, wraps the
        group in an ``<a>`` (opens in a new tab) — e.g. a node linking to its
        NI docs page. ``clip`` (a rect), when given, clips the group's contents
        to that rect via a ``<clipPath>`` — a structure clipping its inner
        content to its own bounds. Must be paired with ``end_group()``."""
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
        # Content-clip rects (structure bounds), deduped by rect so a structure
        # drawn once per frame reuses one <clipPath>. The final ids are resolved
        # in render() with the per-SVG root_id prefix (so many inlined SVGs on
        # one page don't collide on url(#id)); begin_group emits a placeholder.
        self._clip_ids: dict[tuple[float, float, float, float], int] = {}

    @staticmethod
    def _attrs(**attrs: str | float | None) -> str:
        parts = []
        for k, v in attrs.items():
            if v is None:
                continue
            parts.append(f'{k.replace("_", "-")}="{v}"')
        return " ".join(parts)

    def rect(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        stroke_width: float | None = None,
        rx: float | None = None,
        stroke_dasharray: str | None = None,
    ) -> None:
        # Keep the stroked outline inside the bounding box (see _stroke_inset).
        h = _stroke_inset(stroke, stroke_width)
        x1, y1, x2, y2 = x1 + h, y1 + h, x2 - h, y2 - h
        a = self._attrs(
            fill=fill,
            stroke=stroke,
            stroke_width=stroke_width,
            rx=rx,
            stroke_dasharray=stroke_dasharray,
        )
        self._elements.append(
            f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{x2 - x1:.1f}" '
            f'height="{y2 - y1:.1f}" {a}/>'
        )

    def path(
        self,
        points: list[Point],
        *,
        stroke: str,
        stroke_width: float,
        fill: str = "none",
    ) -> None:
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
        self._elements.append(
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{stroke_width}" stroke-linejoin="round"/>'
        )

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float,
        *,
        fill: str | None = None,
        italic: bool = False,
        bold: bool = False,
        anchor: str = "middle",
        mono: bool = False,
    ) -> None:
        a = self._attrs(
            fill=fill,
            font_style="italic" if italic else None,
            font_weight="bold" if bold else None,
            font_family="monospace" if mono else None,
        )
        self._elements.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'text-anchor="{anchor}" {a}>{escape(s)}</text>'
        )

    def image(
        self,
        href: str,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
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
        self,
        points: list[Point],
        *,
        fill: str | None = None,
        stroke: str | None = None,
        stroke_width: float | None = None,
    ) -> None:
        # Keep the stroked outline inside the bounding box (see _stroke_inset);
        # for a polygon that means offsetting every (convex) edge inward.
        points = _inset_polygon(points, _stroke_inset(stroke, stroke_width))
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        a = self._attrs(fill=fill, stroke=stroke, stroke_width=stroke_width)
        self._elements.append(f'<polygon points="{pts}" {a}/>')

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        stroke_width: float | None = None,
    ) -> None:
        # Keep the stroked outline inside the bounding box (see _stroke_inset);
        # for a circle that means shrinking the radius.
        r = max(0.0, r - _stroke_inset(stroke, stroke_width))
        a = self._attrs(fill=fill, stroke=stroke, stroke_width=stroke_width)
        self._elements.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" {a}/>')

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke: str,
        stroke_width: float = 1.0,
    ) -> None:
        self._elements.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{stroke_width}"/>'
        )

    def measure_text(self, text: str, size: float) -> float:
        return _text_width_em(text) * size

    def raw_svg(
        self,
        fragment: str,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        viewbox: tuple[float, float],
    ) -> None:
        vw, vh = viewbox
        self._elements.append(
            f'<svg x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'viewBox="0 0 {vw} {vh}" preserveAspectRatio="none">'
            f"{fragment}</svg>"
        )

    def begin_group(
        self,
        *,
        cls: str | None = None,
        data: dict[str, str] | None = None,
        style: str | None = None,
        title: str | None = None,
        href: str | None = None,
        clip: tuple[float, float, float, float] | None = None,
    ) -> None:
        if href is not None:
            self._elements.append(
                f'<a href={quoteattr(href)} target="_blank" rel="noopener">'
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
        if clip is not None:
            idx = self._clip_ids.setdefault(clip, len(self._clip_ids))
            attrs.append(f'clip-path="url(#__LVCLIP{idx}__)"')
        suffix = (" " + " ".join(attrs)) if attrs else ""
        self._elements.append(f"<g{suffix}>")
        if title is not None:
            self._elements.append(f"<title>{escape(title)}</title>")

    def end_group(self) -> None:
        self._elements.append("</g>")
        if self._anchor_stack and self._anchor_stack.pop():
            self._elements.append("</a>")

    def render(
        self,
        bounds: tuple[float, float, float, float],
        *,
        title: str | None = None,
        script: str | None = None,
        root_id: str | None = None,
        style: str | None = None,
        extra_attrs: dict[str, str] | None = None,
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
        data_attr = "".join(f" data-{k}={quoteattr(attrs[k])}" for k in sorted(attrs))
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg"{id_attr}{aria_attr}{data_attr} '
            f'viewBox="{x1:.0f} {y1:.0f} {w:.0f} {h:.0f}" font-family="sans-serif">'
        )
        title_el = None
        style_el = f"<style>{style}</style>" if style else None
        # Content-clip <defs>: one <clipPath> per distinct structure rect, ids
        # prefixed with the per-SVG root scope so many inlined SVGs on one page
        # don't collide on url(#id). begin_group emitted __LVCLIP{idx}__
        # placeholders in the group tags; resolve them to the final ids here
        # (per element, so the element/newline structure is unchanged).
        prefix = root_id if root_id is not None else "lv"
        defs_el = None
        elements = self._elements
        if self._clip_ids:
            clips = []
            sub: dict[str, str] = {}
            for (cx1, cy1, cx2, cy2), idx in self._clip_ids.items():
                cid = f"{prefix}-c{idx}"
                sub[f"__LVCLIP{idx}__"] = cid
                clips.append(
                    f'<clipPath id="{cid}"><rect x="{cx1:.1f}" y="{cy1:.1f}" '
                    f'width="{cx2 - cx1:.1f}" height="{cy2 - cy1:.1f}"/></clipPath>'
                )
            defs_el = "<defs>" + "".join(clips) + "</defs>"

            def _resolve(el: str) -> str:
                if "__LVCLIP" not in el:
                    return el
                for k, v in sub.items():
                    el = el.replace(k, v)
                return el

            elements = [_resolve(el) for el in self._elements]
        script_el = None
        if script is not None:
            if "</script>" in script or "]]>" in script:
                raise ValueError("script must not contain a literal </script> or ]]>")
            script_el = f"<script>/*<![CDATA[*/\n{script}\n/*]]>*/</script>"
        parts = [head, title_el, style_el, defs_el, *elements, script_el, "</svg>"]
        return "\n".join(p for p in parts if p is not None)

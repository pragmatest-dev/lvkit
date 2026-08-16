"""Icon helpers for the renderer — data-URI embedding with best-effort
background knockout.

LabVIEW icons are 32x32 pixel art on a white field. On the diagram's pale
canvas the exterior white reads as an ugly box, so we knock it out by
flood-filling transparency inward from the four corners — this removes only the
*outside* white and preserves white *inside* the icon shape.

Transparency needs Pillow. It is best-effort: if Pillow is absent or anything
goes wrong we embed the original PNG unchanged. Rendering never fails over an icon.
"""

from __future__ import annotations

import base64
from collections import defaultdict
from pathlib import Path

_WHITE_CUTOFF = 238  # channel value at/above which a pixel counts as background


def _knockout_white_border(png_bytes: bytes) -> bytes:
    """Return PNG bytes with exterior near-white flood-filled to transparent.

    Best-effort: returns the input unchanged if Pillow is unavailable or the
    image can't be processed.
    """
    try:
        import io

        from PIL import Image  # noqa: PLC0415 - optional dependency, guarded
    except Exception:
        return png_bytes
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        w, h = img.size
        px = img.load()
        if px is None:
            return png_bytes

        def is_bg(x: int, y: int) -> bool:
            r, g, b, a = px[x, y]  # type: ignore[misc]
            return (
                a > 0
                and r >= _WHITE_CUTOFF
                and g >= _WHITE_CUTOFF
                and b >= _WHITE_CUTOFF
            )

        stack = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
        seen: set[tuple[int, int]] = set()
        while stack:
            x, y = stack.pop()
            if (x, y) in seen or not (0 <= x < w and 0 <= y < h):
                continue
            seen.add((x, y))
            if not is_bg(x, y):
                continue
            r, g, b, _a = px[x, y]  # type: ignore[misc]
            px[x, y] = (r, g, b, 0)
            stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return png_bytes


_ALPHA_OPAQUE_CUTOFF = 128


def png_to_svg(png_bytes: bytes) -> tuple[str, tuple[int, int]] | None:
    """Pixel-faithful PNG -> SVG string + (width, height).

    Knocks out the exterior near-white (``_knockout_white_border``), then finds
    maximal horizontal runs of identical opaque pixels (alpha>=128), merges
    vertically-adjacent runs of matching ``(x, width, color)`` into rectangles,
    and emits one ``<path>`` per color whose ``d`` concatenates a compact
    rectangle subpath (``M{x} {y}h{w}v{h}h-{w}z``) for each of that color's
    rectangles. This is far more compact than one element per rectangle while
    remaining pixel-perfect. Wraps the paths in an
    ``<svg viewBox="0 0 W H" shape-rendering="crispEdges">``. Returns None if
    the image is empty/degenerate. Used at render time to vectorize a SubVI's
    own ``_ICON.png`` (nodes.ExtractedIconResolver) — the user's own file, not
    any bundled asset.
    """
    try:
        import io

        from PIL import Image  # noqa: PLC0415 - optional dependency, guarded
    except Exception:
        return None
    try:
        knocked_out = _knockout_white_border(png_bytes)
        img = Image.open(io.BytesIO(knocked_out)).convert("RGBA")
        w, h = img.size
        if w <= 0 or h <= 0:
            return None
        px = img.load()
        if px is None:
            return None

        runs: list[tuple[int, int, int, tuple[int, int, int]]] = []
        for y in range(h):
            x = 0
            while x < w:
                r, g, b, a = px[x, y]  # type: ignore[misc]
                if a < _ALPHA_OPAQUE_CUTOFF:
                    x += 1
                    continue
                run_start = x
                run_color = (r, g, b)
                x += 1
                while x < w:
                    nr, ng, nb, na = px[x, y]  # type: ignore[misc]
                    if na < _ALPHA_OPAQUE_CUTOFF or (nr, ng, nb) != run_color:
                        break
                    x += 1
                runs.append((run_start, y, x - run_start, run_color))

        if not runs:
            return None

        rect_bounds: list[list[int]] = []
        rect_colors: list[tuple[int, int, int]] = []
        rect_index: dict[tuple[int, int, tuple[int, int, int]], int] = {}
        for run_x, run_y, run_w, run_color in runs:
            key = (run_x, run_w, run_color)
            existing = rect_index.get(key)
            if (
                existing is not None
                and rect_bounds[existing][1] + rect_bounds[existing][3] == run_y
            ):
                rect_bounds[existing][3] += 1
            else:
                rect_bounds.append([run_x, run_y, run_w, 1])
                rect_colors.append(run_color)
                rect_index[key] = len(rect_bounds) - 1

        by_color: dict[tuple[int, int, int], list[str]] = defaultdict(list)
        for (rx, ry, rw, rh), color in zip(rect_bounds, rect_colors, strict=True):
            by_color[color].append(f"M{rx} {ry}h{rw}v{rh}h-{rw}z")

        body = "".join(
            f"<path fill='#{c[0]:02x}{c[1]:02x}{c[2]:02x}' d='{''.join(p)}'/>"
            for c, p in by_color.items()
        )
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' "
            f"viewBox='0 0 {w} {h}' shape-rendering='crispEdges'>{body}</svg>"
        )
        return svg, (w, h)
    except Exception:
        return None


def icon_data_uri(path: Path, transparent: bool = True) -> str | None:
    """Return a ``data:image/png;base64,...`` URI for an icon PNG, or None.

    When ``transparent`` is set, the exterior white is knocked out (best-effort).
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if transparent:
        raw = _knockout_white_border(raw)
    return "data:image/png;base64," + base64.b64encode(raw).decode()

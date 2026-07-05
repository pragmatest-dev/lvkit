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
            return (a > 0 and r >= _WHITE_CUTOFF and g >= _WHITE_CUTOFF
                    and b >= _WHITE_CUTOFF)

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

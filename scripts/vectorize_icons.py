"""Build-time script: vectorize extracted LabVIEW icon PNGs into pixel-faithful SVGs.

This script is BUILD-TIME tooling — it requires Pillow, which is not a runtime
dependency of lvkit. Run it with ``uv run --with pillow python
scripts/vectorize_icons.py`` (or in any environment that has Pillow installed).

It converts the extracted icon PNGs in ``src/lvkit/data/glyphs/*.png`` into
pixel-faithful ``.svg`` files at the icon's TRUE pixel size (e.g. 32x32,
22x22, whatever the source asset actually is). Each SVG's ``viewBox`` carries
those true dimensions so the renderer can draw the icon centered at its
natural size — the same way it already draws the arithmetic triangles —
instead of stretching every icon to a fixed box.

For each row of the (background-knocked-out) image, maximal horizontal runs
of identical, fully-opaque pixels are merged into a single
``<rect ... height="1">``. Transparent runs are skipped entirely. The result
is a handful of KB per icon with exact, lossless fidelity to the source
raster.

Provenance note: the source PNGs are extracted from NI's LabVIEW
documentation (NI-derived assets). The SVGs produced here are pixel-faithful
copies of those PNGs — i.e. a derivative work, not a clean-room
re-implementation. This script is a pragmatic conversion pipeline to make the
shipped assets smaller and resolution-independent; it makes no claim of
original authorship over the icon artwork itself.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# Make `lvkit` importable when this script is run directly (not installed).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lvkit.render.icons import _knockout_white_border  # noqa: E402

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

_ALPHA_OPAQUE_CUTOFF = 128


def _svg_for_image(img: PILImage) -> tuple[str, int, int]:
    """Build a pixel-faithful SVG string for an RGBA image.

    Returns (svg_text, width, height).
    """
    w, h = img.size
    px = img.load()
    if px is None:
        msg = "image has no accessible pixel data"
        raise ValueError(msg)

    rects: list[str] = []
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
            run_len = x - run_start
            cr, cg, cb = run_color
            rects.append(
                f'<rect x="{run_start}" y="{y}" width="{run_len}" height="1" '
                f'fill="#{cr:02x}{cg:02x}{cb:02x}"/>'
            )

    body = "".join(rects)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {w} {h}" shape-rendering="crispEdges">{body}</svg>'
    )
    return svg, w, h


def vectorize_glyphs(
    glyphs_dir: Path,
) -> tuple[dict[str, tuple[int, int]], list[tuple[str, str]], int]:
    """Convert every ``*.png`` in ``glyphs_dir`` (except ``_``-prefixed files) to SVG.

    Returns (sizes, failures, total_bytes_written) where ``sizes`` maps stem
    to (width, height) and ``failures`` is a list of (filename, reason).
    """
    from PIL import Image  # noqa: PLC0415 - optional/build-time dependency

    sizes: dict[str, tuple[int, int]] = {}
    failures: list[tuple[str, str]] = []
    total_bytes = 0

    png_paths = sorted(
        p for p in glyphs_dir.glob("*.png") if not p.name.startswith("_")
    )
    for png_path in png_paths:
        stem = png_path.stem
        try:
            raw = png_path.read_bytes()
            knocked_out = _knockout_white_border(raw)
            img = Image.open(io.BytesIO(knocked_out)).convert("RGBA")
            svg_text, w, h = _svg_for_image(img)
        except Exception as exc:  # noqa: BLE001 - fail-closed per file
            failures.append((png_path.name, f"{type(exc).__name__}: {exc}"))
            continue

        svg_path = glyphs_dir / f"{stem}.svg"
        svg_bytes = svg_text.encode("utf-8")
        svg_path.write_bytes(svg_bytes)
        total_bytes += len(svg_bytes)
        sizes[stem] = (w, h)

    return sizes, failures, total_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--glyphs-dir",
        type=Path,
        default=Path("src/lvkit/data/glyphs"),
        help="Directory containing extracted icon PNGs (default: %(default)s)",
    )
    args = parser.parse_args()

    glyphs_dir: Path = args.glyphs_dir
    sizes, failures, total_bytes = vectorize_glyphs(glyphs_dir)

    sizes_path = glyphs_dir / "_svg_sizes.json"
    sizes_json = {stem: list(sizes[stem]) for stem in sorted(sizes)}
    sizes_path.write_text(
        json.dumps(sizes_json, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"{len(sizes)} SVG(s) written, {total_bytes} total bytes")
    print(f"Size index written to {sizes_path}")
    if failures:
        print(f"{len(failures)} failure(s):")
        for name, reason in failures:
            print(f"  {name}: {reason}")


if __name__ == "__main__":
    main()

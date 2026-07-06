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
import json
import sys
from pathlib import Path

# Make `lvkit` importable when this script is run directly (not installed).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lvkit.render.icons import png_to_svg  # noqa: E402


def vectorize_glyphs(
    glyphs_dir: Path,
) -> tuple[dict[str, tuple[int, int]], list[tuple[str, str]], int]:
    """Convert every ``*.png`` in ``glyphs_dir`` (except ``_``-prefixed files) to SVG.

    Returns (sizes, failures, total_bytes_written) where ``sizes`` maps stem
    to (width, height) and ``failures`` is a list of (filename, reason).
    """
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
            result = png_to_svg(raw)
            if result is None:
                msg = "png_to_svg returned None (empty/degenerate image?)"
                raise ValueError(msg)
            svg_text, (w, h) = result
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

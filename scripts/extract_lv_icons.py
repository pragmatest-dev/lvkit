#!/usr/bin/env python3
"""Extract real LabVIEW primitive icons from the reference PDF (build-time).

Not a runtime dependency: this script requires ``pymupdf`` (and Pillow,
already pulled in transitively via ``pylabview``), neither of which lvkit
ships as a runtime dependency for icon extraction. Run it with::

    uv run --with pymupdf python scripts/extract_lv_icons.py

For every entry in ``primitives.json`` (``primitives`` keyed by primResID,
plus a small curated set of ``node_types`` entries — see
``_NODE_TYPE_PDF_PAGES`` below) that carries a verified ``pdf_page``, this
locates that function's connector-pane figure in the reference manual,
border-detects the boxed icon within it (skipping borderless arithmetic/
comparison triangles — those are already drawn by ``GeneratedGlyphResolver``),
and saves an isolated PNG under ``src/lvkit/data/glyphs/``.

Fail-closed: any ambiguity (heading not found, no image, no box detected)
SKIPS the entry — recorded with a reason in ``_manifest.json`` — rather than
shipping a dubious crop. The reference PDF itself is a build-time input only;
it must never ship, only the small cached PNGs + manifest do.

Two known traps this script handles (see FIDELITY_PLAN.md step 5):
  1. Connector-pane figures can bleed across pages — a function's own pane
     sometimes renders at the TOP of the page *following* its heading
     (observed: "Build Array"'s heading page's first big image is actually
     "Initialize Array"'s pane, bled over from the previous page). We search
     the heading's own page first, then the very next page (only images that
     appear before that next page's own next heading, if any).
  2. Stored ``pdf_page`` can be off by a few pages — we scan a +/-5 window
     and require the exact function name to appear as a doubled heading
     line (LabVIEW reference manual headings render each title twice in a
     row, e.g. "Build Array \\nBuild Array \\n" — a reliable, cheap signal
     that distinguishes a real heading from an incidental body-text mention).

JUDGMENT CALL (see final report): several array primitives are modeled as
``node_types`` entries (XML class dispatch — aBuild/aInit/aIndx, etc.), not
``primitives`` entries, and codegen (``codegen/nodes/primitive.py``)
deliberately resolves node_type BEFORE prim_id for these, because several of
them alias a shared/misleading primResID (e.g. "Index Array" runs under
primResID 1809, which primitives.json separately and correctly labels
"Array Size" for the *generic* "prim" class node). Mirroring that precedent,
this script extracts node_type-keyed entries under a ``prim_nt_<node_type>``
asset name, and ``PdfIconResolver`` (render/nodes.py) checks the node_type
asset before the prim_id asset — so a node whose node_type has a dedicated
icon never accidentally inherits a same-numbered but differently-named
primitive's icon.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_PDF = REPO_ROOT / "docs" / "labview_programming_reference_manual.pdf"
DEFAULT_PRIMITIVES_JSON = REPO_ROOT / "src" / "lvkit" / "data" / "primitives.json"
DEFAULT_OUT_DIR = REPO_ROOT / "src" / "lvkit" / "data" / "glyphs"

# Verified (not guessed) 1-indexed pdf_page numbers for the handful of
# boxed-icon array primitives modeled as node_types rather than primResID
# primitives (primitives.json's `node_types` section has no pdf_page field
# at all — see FIDELITY_PLAN.md's "Key measured facts"). Found and confirmed
# by locating each function's doubled heading line in the reference PDF:
#   - aIndx  "Index Array"      -> page 392 (heading: "...Index Array
#            \\nIndex Array\\nReturns the element or subarray...")
#   - aInit  "Initialize Array" -> page 399 (heading appears at the END of
#            the "Delete From Array" page, its own pane bleeds onto page 400)
#   - aBuild "Build Array"      -> page 400 (heading mid-page; own pane is
#            the SECOND big image on that page — the first is Init Array's)
# These are intentionally NOT written back into primitives.json's
# `node_types` section by this script (see report) — kept local here so the
# extraction's provenance/verification method stays next to its use.
_NODE_TYPE_PDF_PAGES: dict[str, tuple[str, int]] = {
    "aIndx": ("Index Array", 392),
    "aInit": ("Initialize Array", 399),
    "aBuild": ("Build Array", 400),
}

_SEARCH_WINDOW = 5  # pages either side of the stored/known pdf_page
_MIN_BORDER_RUN = 14  # px; shortest vertical dark run worth considering
_MIN_BOX_SIDE = 16  # px
_MAX_BOX_SIDE = 40  # px
_DARK_CUTOFF = 120  # channel value below which a pixel counts as "border"


@dataclass(frozen=True)
class ExtractTarget:
    """One primitive/node_type entry to attempt extraction for."""

    asset_stem: str  # "prim_1901" or "prim_nt_aBuild"
    name: str  # function name to search for in the PDF
    pdf_page: int  # 1-indexed, as stored (may be off by a few)
    prim_id: str | None
    node_type: str | None


@dataclass(frozen=True)
class ManifestEntry:
    asset_stem: str
    prim_id: str | None
    node_type: str | None
    name: str
    pdf_page: int
    status: str  # "ok" | "skipped"
    reason: str = ""
    resolved_page: int | None = None
    source_xref: int | None = None
    source_digest: str | None = None
    figure_bbox: tuple[float, float, float, float] | None = None
    crop_rect: tuple[int, int, int, int] | None = None
    asset: str | None = None

    def to_json(self) -> dict:
        return {
            "id": self.asset_stem,
            "prim_id": self.prim_id,
            "node_type": self.node_type,
            "name": self.name,
            "pdf_page": self.pdf_page,
            "resolved_page": self.resolved_page,
            "source_xref": self.source_xref,
            "source_digest": self.source_digest,
            "figure_bbox": list(self.figure_bbox) if self.figure_bbox else None,
            "crop_rect": list(self.crop_rect) if self.crop_rect else None,
            "asset": self.asset,
            "status": self.status,
            "reason": self.reason,
        }


def load_targets(primitives_json: Path) -> list[ExtractTarget]:
    data = json.loads(primitives_json.read_text())
    targets: list[ExtractTarget] = []

    for prim_id, entry in data.get("primitives", {}).items():
        pdf_page = entry.get("pdf_page")
        if pdf_page is None:
            continue
        targets.append(ExtractTarget(
            asset_stem=f"prim_{prim_id}",
            name=entry.get("name", ""),
            pdf_page=int(pdf_page),
            prim_id=str(prim_id),
            node_type=None,
        ))

    for node_type, (name, pdf_page) in _NODE_TYPE_PDF_PAGES.items():
        targets.append(ExtractTarget(
            asset_stem=f"prim_nt_{node_type}",
            name=name,
            pdf_page=pdf_page,
            prim_id=None,
            node_type=node_type,
        ))

    # Deterministic order: independent of dict hash-order upstream.
    targets.sort(key=lambda t: t.asset_stem)
    return targets


def _heading_pattern(name: str) -> re.Pattern[str]:
    escaped = re.escape(name)
    return re.compile(rf"{escaped}\s*\n\s*{escaped}\s*\n")


# The reference manual titles most functions "<Name> Function" (occasionally
# "<Name> VI" / "<Name> Constant" / "<Name> Express VI") even though
# primitives.json's `name` field has that suffix already stripped (mirroring
# PrimitiveResolver._normalize_name's own suffix-stripping convention). Array
# functions (Build/Delete/Initialize Array, ...) are the exception and use
# the bare name. Try the bare name first, then each suffixed variant.
_HEADING_SUFFIXES = ("", " Function", " VI", " Constant", " Express VI")


def find_heading_page(doc, name: str, pdf_page: int) -> tuple[int, str] | None:
    """Return (0-based page index, exact heading text found) for ``name``
    rendered as a doubled heading line, searching pdf_page +/- _SEARCH_WINDOW
    (1-indexed input) and trying the manual's common title suffixes. None if
    not found in the window."""
    lo = max(0, pdf_page - 1 - _SEARCH_WINDOW)
    hi = min(len(doc), pdf_page - 1 + _SEARCH_WINDOW + 1)
    # Try the stored page first (fast path for the common case), then widen.
    order = sorted(range(lo, hi), key=lambda p: abs(p - (pdf_page - 1)))
    for pno in order:
        text = doc[pno].get_text()
        for suffix in _HEADING_SUFFIXES:
            candidate = f"{name}{suffix}"
            if _heading_pattern(candidate).search(text):
                return pno, candidate
    return None


def _page_headings(page) -> list[tuple[float, str]]:
    """Generic (name-agnostic) doubled-heading detector for one page.

    Returns (y0, heading_text) pairs sorted by y0 — used only to find where
    the NEXT function's heading starts, to bound "does this image belong to
    our heading or the next one" without needing a master list of every
    function name in the manual.
    """
    text = page.get_text()
    lines = [ln.strip() for ln in text.split("\n")]
    found: list[tuple[float, str]] = []
    seen_names: set[str] = set()
    for i in range(len(lines) - 1):
        line = lines[i]
        if not line or line != lines[i + 1]:
            continue
        if not (3 <= len(line) <= 60):
            continue
        if line in seen_names:
            continue
        hits = page.search_for(line)
        if not hits:
            continue
        seen_names.add(line)
        found.append((hits[0].y0, line))
    found.sort(key=lambda t: t[0])
    return found


def _heading_anchor_y(page, name: str) -> float | None:
    hits = page.search_for(name)
    if not hits:
        return None
    return min(h.y0 for h in hits)


def _big_images(page) -> list[dict]:
    """Embedded images plausibly large enough to be a connector-pane figure
    (excludes the tiny 32x16 inline data-type glyphs used in bullet text)."""
    return [
        info for info in page.get_image_info(xrefs=True)
        if info["width"] >= 60 and info["height"] >= 24
    ]


def find_connector_pane_candidates(doc, heading_page: int, name: str) -> list[dict]:
    """Like ``find_connector_pane_image`` but returns ALL plausible
    candidates in reading order (own page first, then the bleed page) so the
    caller can try border-detection on each until one succeeds — this is
    what actually disambiguates a real connector pane from an unrelated
    same-heading illustration (e.g. an "example configurations" table), since
    only the true icon figure will border-detect a square box."""
    page = doc[heading_page]
    anchor_y = _heading_anchor_y(page, name)
    if anchor_y is None:
        return []

    other_headings = [
        (y, text) for y, text in _page_headings(page)
        if text != name and y > anchor_y
    ]
    next_heading_y = other_headings[0][0] if other_headings else None

    own_page = sorted(
        (info for info in _big_images(page) if info["bbox"][1] > anchor_y - 1),
        key=lambda info: info["bbox"][1],
    )
    if next_heading_y is not None:
        own_page = [c for c in own_page if c["bbox"][1] < next_heading_y]
    for info in own_page:
        info["_page"] = heading_page

    if own_page or heading_page + 1 >= len(doc):
        return own_page

    next_page = doc[heading_page + 1]
    next_page_headings = _page_headings(next_page)
    boundary_y = next_page_headings[0][0] if next_page_headings else None
    bleed = sorted(_big_images(next_page), key=lambda info: info["bbox"][1])
    if boundary_y is not None:
        bleed = [c for c in bleed if c["bbox"][1] < boundary_y]
    for info in bleed:
        info["_page"] = heading_page + 1
    return bleed


def _is_dark(pixel: tuple[int, ...]) -> bool:
    r, g, b = pixel[:3]
    return r < _DARK_CUTOFF and g < _DARK_CUTOFF and b < _DARK_CUTOFF


def detect_icon_box(image) -> tuple[int, int, int, int] | None:
    """Find the maximal near-black axis-aligned square icon border within a
    connector-pane figure. Returns a crop rect (left, top, right, bottom) in
    the image's own pixel space, or None if no boxed icon is present
    (borderless arithmetic/comparison triangles correctly yield None here).

    Algorithm: a real LabVIEW icon border is a solid BLACK rectangle (the
    small inline data-type glyphs used elsewhere on the page are colored —
    blue/orange/green wire-color borders — so they never satisfy `_is_dark`
    and are naturally excluded without a separate size/color pre-filter).
    We look for a matching PAIR of columns, each with a long vertical dark
    run starting at (nearly) the same y, separated by a plausible icon-side
    width, then confirm a horizontal dark run along the top connecting them.
    """
    im = image.convert("RGB")
    w, h = im.size
    if w < _MIN_BOX_SIDE + 2 or h < _MIN_BOX_SIDE + 2:
        return None
    px = im.load()

    runs: list[tuple[int, int, int]] = []  # (x, y_start, run_len)
    for x in range(1, w - 1):
        best_len = 0
        best_start = 0
        cur = 0
        cur_start = 0
        for y in range(1, h - 1):
            if _is_dark(px[x, y]):
                if cur == 0:
                    cur_start = y
                cur += 1
                if cur > best_len:
                    best_len = cur
                    best_start = cur_start
            else:
                cur = 0
        if best_len >= _MIN_BORDER_RUN:
            runs.append((x, best_start, best_len))

    def row_coverage(y: int, x1: int, x2: int) -> float:
        span = x2 - x1 + 1
        dark = sum(1 for x in range(x1, x2 + 1) if _is_dark(px[x, y]))
        return dark / span

    # Icon boxes aren't always square in the manual's rendering (compact
    # single-row panes can render visibly wider than tall) — so width and
    # height are found independently where possible: a left/right column
    # PAIR fixes width and the top border row; a separate downward scan
    # fixes height. We take the DEEPEST (largest-y) strongly-covered row in
    # range, not the first — some icons have an internal horizontal divider
    # (e.g. "Get Type Information"'s icon has a full-width line splitting its
    # top bar from the graphic below) that would otherwise be mistaken for
    # the bottom border, truncating the crop early. Growable panes
    # (Build/Index/Initialize Array, ...) have a DOTTED bottom border
    # instead of a solid one — no row hits the coverage threshold there, so
    # we fall back to the nominal square crop (bottom = top + width), which
    # lands just above the dotted "grow chrome" as intended.
    def solid_bottom(top: int, x1: int, x2: int) -> int | None:
        # Exclude row h-1: the outer rectangle enclosing the WHOLE
        # connector-pane figure (icon + wire stubs + labels) always has a
        # fully-dark bottom edge there, which would otherwise always win
        # the "deepest qualifying row" search regardless of the icon's own
        # (much shallower) real border.
        found = None
        for y in range(top + _MIN_BOX_SIDE // 2, min(h - 2, top + _MAX_BOX_SIDE) + 1):
            if row_coverage(y, x1, x2) >= 0.7:
                found = y
        return found

    best_box: tuple[int, int, int, int] | None = None  # (x1, top, x2, bottom)
    for i, (x1, y1, len1) in enumerate(runs):
        for x2, y2, len2 in runs[i + 1:]:
            width = x2 - x1
            if not (_MIN_BOX_SIDE <= width <= _MAX_BOX_SIDE):
                continue
            if abs(y1 - y2) > 2:
                continue
            top = min(y1, y2)
            if row_coverage(top, x1, x2) < 0.7:
                continue
            bottom = solid_bottom(top, x1, x2)
            if bottom is None:
                nominal = top + width
                if nominal >= h - 1 or min(len1, len2) < width * 0.5:
                    continue
                bottom = nominal
            area = width * (bottom - top)
            best_area = (
                (best_box[2] - best_box[0]) * (best_box[3] - best_box[1])
                if best_box is not None else -1
            )
            if area > best_area:
                best_box = (x1, top, x2, bottom)

    if best_box is None:
        return None
    x1, top, x2, bottom = best_box
    right = min(w, x2 + 1)
    bottom = min(h, bottom + 1)
    return (x1, top, right, bottom)


def extract_one(doc, target: ExtractTarget, out_dir: Path) -> ManifestEntry:
    found = find_heading_page(doc, target.name, target.pdf_page)
    if found is None:
        return ManifestEntry(
            asset_stem=target.asset_stem, prim_id=target.prim_id,
            node_type=target.node_type, name=target.name,
            pdf_page=target.pdf_page, status="skipped",
            reason=(
                f"heading {target.name!r} not found within "
                f"+/-{_SEARCH_WINDOW} pages of {target.pdf_page}"
            ),
        )
    heading_page, heading_text = found

    candidates = find_connector_pane_candidates(doc, heading_page, heading_text)
    if not candidates:
        return ManifestEntry(
            asset_stem=target.asset_stem, prim_id=target.prim_id,
            node_type=target.node_type, name=target.name,
            pdf_page=target.pdf_page, status="skipped",
            resolved_page=heading_page + 1,
            reason="no candidate connector-pane image found near heading",
        )

    for info in candidates:
        xref = info["xref"]
        img_dict = doc.extract_image(xref)
        pil_img = Image.open(io.BytesIO(img_dict["image"]))
        box = detect_icon_box(pil_img)
        if box is None:
            continue
        crop = pil_img.convert("RGB").crop(box)
        asset_name = f"{target.asset_stem}.png"
        crop.save(out_dir / asset_name)
        return ManifestEntry(
            asset_stem=target.asset_stem, prim_id=target.prim_id,
            node_type=target.node_type, name=target.name,
            pdf_page=target.pdf_page, status="ok",
            resolved_page=info["_page"] + 1,
            source_xref=xref,
            source_digest=info.get("digest", b"").hex()
            if isinstance(info.get("digest"), bytes) else None,
            figure_bbox=tuple(info["bbox"]),
            crop_rect=box,
            asset=asset_name,
        )

    return ManifestEntry(
        asset_stem=target.asset_stem, prim_id=target.prim_id,
        node_type=target.node_type, name=target.name,
        pdf_page=target.pdf_page, status="skipped",
        resolved_page=heading_page + 1,
        reason=(
            f"{len(candidates)} candidate image(s) found near heading but "
            "none border-detected a boxed icon (likely a borderless "
            "arithmetic/comparison primitive, or an unrelated illustration)"
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--primitives-json", type=Path, default=DEFAULT_PRIMITIVES_JSON)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    try:
        import fitz  # type: ignore[import-not-found]  # noqa: PLC0415 - build-time-only (pymupdf)
    except ImportError:
        print(
            "pymupdf is required: run with "
            "`uv run --with pymupdf python scripts/extract_lv_icons.py`",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    args.out.mkdir(parents=True, exist_ok=True)
    targets = load_targets(args.primitives_json)
    doc = fitz.open(str(args.pdf))

    manifest: list[ManifestEntry] = []
    for target in targets:
        entry = extract_one(doc, target, args.out)
        manifest.append(entry)
        print(f"{entry.status:8} {entry.asset_stem:20} {entry.name}"
              + (f"  ({entry.reason})" if entry.reason else ""))

    manifest_path = args.out / "_manifest.json"
    manifest_path.write_text(
        json.dumps([m.to_json() for m in manifest], indent=2) + "\n"
    )

    ok = sum(1 for m in manifest if m.status == "ok")
    skipped = len(manifest) - ok
    print(f"\n{ok} extracted, {skipped} skipped, out of {len(manifest)} targets.")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()

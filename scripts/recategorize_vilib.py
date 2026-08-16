#!/usr/bin/env python3
"""Re-categorize vi.lib JSON entries using categories derived from the PDF TOC.

The original ``populate_vilib.py`` used a hand-maintained ``CATEGORY_MAP`` that
only covered PDF pages up to ~2100, so every entry past that (Mathematics,
Signal Processing, Data Communication, ...) fell through into a 4.5 MB
``other.json`` grab-bag.

This script rebuilds the category assignment from the PDF's actual table of
contents instead:

- The level-1 "Functions" section (pages 325-6164) is the only region that
  contains real functions/VIs. Anything at page >= 6165 (Property/Method,
  Environment, Dialog, Error-Code reference) is dropped.
- Under "Functions", the "Programming" level-2 palette is expanded into its
  level-3 children (Array, String, Numeric, ...) so the existing fine palettes
  are preserved. Every other level-2 palette (Mathematics, Signal Processing,
  ...) becomes its own category.

Entry dicts are preserved byte-for-byte except for their ``category`` value,
which is reassigned. Nothing is re-scraped from the PDF.

Usage:
    uv run --with pymupdf python scripts/recategorize_vilib.py
"""

from __future__ import annotations

import argparse
import json
import re
from bisect import bisect_right
from pathlib import Path
from typing import Any

import fitz  # pymupdf

# Boundaries of the level-1 "Functions" section in the reference manual.
FUNCTIONS_TITLE = "Functions"
PROGRAMMING_TITLE = "Programming"
MIN_PAGE = 325  # first page of "Functions"


def slugify(category: str) -> str:
    """Convert a category name to its JSON filename stem.

    Matches the convention of the existing files, inferred by comparing each
    file's ``category`` field to its filename:

        "Array"               -> "array"
        "File I/O"            -> "file-io"
        "Application Control" -> "application-control"
        "Error Handling"     -> "error-handling"
        "Signal Processing"  -> "signal-processing"
        "Control & Simulation" -> "control-simulation"

    Rule: lowercase; drop ``&``, ``,`` and ``/``; collapse whitespace runs to a
    single ``-``; strip leading/trailing ``-``.
    """
    text = category.lower()
    text = text.replace("&", " ").replace(",", " ").replace("/", "")
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def build_range_table(
    pdf_path: Path,
) -> tuple[list[int], list[tuple[int, int, str]]]:
    """Build an ordered page->category range table from the PDF TOC.

    Returns:
        (starts, ranges) where ``ranges`` is a sorted list of
        ``(start, end, category)`` half-open ``[start, end)`` page ranges and
        ``starts`` is the parallel list of start pages (for bisection).
    """
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    doc.close()

    # Locate the level-1 "Functions" section and the next level-1 after it,
    # which bounds where real functions/VIs live.
    functions_page: int | None = None
    functions_end: int | None = None
    for level, title, page in toc:
        if level == 1 and title.strip() == FUNCTIONS_TITLE:
            functions_page = page
        elif level == 1 and functions_page is not None and page > functions_page:
            functions_end = page
            break
    if functions_page is None or functions_end is None:
        raise RuntimeError("Could not locate the 'Functions' section in the TOC")

    # Collect the level-2 palettes under Functions, in TOC order.
    level2: list[tuple[int, str]] = [
        (page, title.strip())
        for level, title, page in toc
        if level == 2 and functions_page <= page < functions_end
    ]

    # Collect breakpoints as (page, category). For "Programming" we expand into
    # its level-3 children; every other palette becomes a single category.
    breakpoints: list[tuple[int, str]] = []
    for page, title in level2:
        if title == PROGRAMMING_TITLE:
            # Programming spans [page, next level-2 sibling).
            prog_end = functions_end
            for p2, _t2 in level2:
                if p2 > page:
                    prog_end = p2
                    break
            children = [
                (p, t.strip())
                for level, t, p in toc
                if level == 3 and page <= p < prog_end
            ]
            breakpoints.extend(children)
        else:
            breakpoints.append((page, title))

    # Sort by page (stable preserves TOC order for ties, so palettes that share
    # a start page collapse to an empty range for the earlier one).
    breakpoints.sort(key=lambda b: b[0])

    ranges: list[tuple[int, int, str]] = []
    for i, (start, category) in enumerate(breakpoints):
        end = breakpoints[i + 1][0] if i + 1 < len(breakpoints) else functions_end
        ranges.append((start, end, category))

    starts = [r[0] for r in ranges]
    return starts, ranges


def categorize(
    page: int | None,
    starts: list[int],
    ranges: list[tuple[int, int, str]],
) -> str | None:
    """Return the category for a page, or None if it falls outside all ranges."""
    if page is None or page < MIN_PAGE:
        return None
    # Rightmost range whose start <= page.
    idx = bisect_right(starts, page) - 1
    while idx >= 0:
        start, end, category = ranges[idx]
        if start <= page < end:
            return category
        idx -= 1
    return None


def load_all_entries(vilib_dir: Path) -> list[dict[str, Any]]:
    """Flatten entries from every non-underscore category file, unchanged."""
    entries: list[dict[str, Any]] = []
    for path in sorted(vilib_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        with open(path) as f:
            data = json.load(f)
        entries.extend(data.get("entries", []))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vilib-dir",
        type=Path,
        default=Path("src/lvkit/data/vilib"),
        help="Directory of vilib category JSON files.",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=Path("docs/labview_programming_reference_manual.pdf"),
        help="LabVIEW reference manual PDF.",
    )
    args = parser.parse_args()

    vilib_dir: Path = args.vilib_dir
    pdf_path: Path = args.pdf

    starts, ranges = build_range_table(pdf_path)

    # Before counts (per existing file's category field).
    before_counts: dict[str, int] = {}
    for path in sorted(vilib_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        with open(path) as f:
            data = json.load(f)
        before_counts[data.get("category", path.stem)] = len(data.get("entries", []))

    entries = load_all_entries(vilib_dir)
    total_in = len(entries)

    kept: list[dict[str, Any]] = []
    dropped_no_page = 0
    dropped_out_of_range = 0
    residual = 0
    for entry in entries:
        page = entry.get("page")
        if page is None:
            dropped_no_page += 1
            continue
        if page < MIN_PAGE or page >= ranges[-1][1]:
            dropped_out_of_range += 1
            continue
        category = categorize(page, starts, ranges)
        if category is None:
            category = "Other"
            residual += 1
        entry["category"] = category
        kept.append(entry)

    # Group by category (deterministic order).
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in kept:
        grouped.setdefault(entry["category"], []).append(entry)

    # Preserve within-category order by page then name for determinism.
    for cat_entries in grouped.values():
        cat_entries.sort(key=lambda e: (e.get("page", 0), e.get("name", "")))

    # Remove old category files (keep underscore-prefixed helpers).
    for path in sorted(vilib_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        path.unlink()

    # Write new category files.
    categories_index: dict[str, str] = {}
    after_counts: dict[str, int] = {}
    for category in sorted(grouped):
        cat_entries = grouped[category]
        filename = f"{slugify(category)}.json"
        out = {
            "category": category,
            "count": len(cat_entries),
            "entries": cat_entries,
        }
        with open(vilib_dir / filename, "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
            f.write("\n")
        categories_index[category] = filename
        after_counts[category] = len(cat_entries)

    # Regenerate _index.json preserving its existing shape.
    index_path = vilib_dir / "_index.json"
    with open(index_path) as f:
        index = json.load(f)
    index["categories"] = {cat: categories_index[cat] for cat in sorted(grouped)}
    index["total_entries"] = len(kept)
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Summary.
    print("=" * 60)
    print("Recategorize vi.lib summary")
    print("=" * 60)
    print(f"entries in:        {total_in}")
    print(f"  dropped (no page):     {dropped_no_page}")
    print(f"  dropped (out of range): {dropped_out_of_range}")
    print(f"  residual 'Other':       {residual}")
    print(f"kept:              {len(kept)}")
    print(
        f"conservation:      {total_in} - {dropped_no_page + dropped_out_of_range}"
        f" = {total_in - dropped_no_page - dropped_out_of_range} (== kept)"
    )
    print()
    print(f"{'category':32} {'before':>8} {'after':>8}")
    print("-" * 52)
    all_cats = sorted(set(before_counts) | set(after_counts))
    for cat in all_cats:
        b = before_counts.get(cat, 0)
        a = after_counts.get(cat, 0)
        print(f"{cat:32} {b:>8} {a:>8}")
    print("-" * 52)
    print(
        f"{'TOTAL':32} {sum(before_counts.values()):>8} {sum(after_counts.values()):>8}"
    )


if __name__ == "__main__":
    main()

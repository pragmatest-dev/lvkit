#!/usr/bin/env python3
"""Generate code using AST builder without LLM.

Uses the new AST-based code generation (builder.py), not skeleton.
This script is a thin wrapper around lvkit.pipeline.generate_python().
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lvkit.pipeline import generate_python


def main():
    parser = argparse.ArgumentParser(
        description="Generate code using AST builder (no LLM)",
    )
    parser.add_argument("input", help="VI file to convert")
    parser.add_argument(
        "-o", "--output", required=True, help="Output directory",
    )
    parser.add_argument(
        "--search-path", action="append", dest="search_paths",
        default=[], help="Additional search paths",
    )
    parser.add_argument(
        "--placeholder-on-unresolved", action="store_true",
        help=(
            "Don't fail on unknown primitives or vi.lib VIs. Instead emit "
            "an inline `raise PrimitiveResolutionNeeded(...)` / `raise "
            "VILibResolutionNeeded(...)` in the generated Python."
        ),
    )
    args = parser.parse_args()

    generate_python(
        args.input,
        args.output,
        search_paths=[Path(p) for p in args.search_paths],
        expand_subvis=True,
        soft_unresolved=args.placeholder_on_unresolved,
    )


if __name__ == "__main__":
    main()

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

from lvkit.graph.loading import LoadMode
from lvkit.pipeline import generate_python


def main():
    parser = argparse.ArgumentParser(
        description="Generate code using AST builder (no LLM)",
    )
    parser.add_argument("input", help="VI file to convert")
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="End the run at the first gap (its warning is printed).",
    )
    parser.add_argument(
        "--vilib",
        default=None,
        metavar="DIR",
        help="Path to LabVIEW vi.lib on disk for <vilib> resolution.",
    )
    parser.add_argument(
        "--userlib",
        default=None,
        metavar="DIR",
        help="Path to LabVIEW user.lib on disk for <userlib> resolution.",
    )
    parser.add_argument(
        "--instrlib",
        default=None,
        metavar="DIR",
        help="Path to LabVIEW instr.lib on disk for <instrlib> resolution.",
    )
    args = parser.parse_args()

    generate_python(
        args.input,
        args.output,
        mode=LoadMode.FULL,
        fail_fast=args.fail_fast,
        vilib_root=Path(args.vilib) if args.vilib else None,
        userlib_root=Path(args.userlib) if args.userlib else None,
        instrlib_root=Path(args.instrlib) if args.instrlib else None,
    )


if __name__ == "__main__":
    main()

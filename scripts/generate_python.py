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

from lvkit.cli_run import add_fail_fast_arg, command_run, finish, run_command
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
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Additional search paths",
    )
    add_fail_fast_arg(parser)
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
    args = parser.parse_args()
    return run_command(_generate, args)


def _generate(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    run = command_run(args, input_path)
    result = generate_python(
        input_path,
        args.output,
        search_paths=[Path(p) for p in args.search_paths],
        mode=LoadMode.FULL,
        vilib_root=Path(args.vilib) if args.vilib else None,
        userlib_root=Path(args.userlib) if args.userlib else None,
        run=run,
    )
    return finish(run, bool(result["error"] or result["failed"]))


if __name__ == "__main__":
    sys.exit(main())

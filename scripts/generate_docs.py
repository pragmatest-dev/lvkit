#!/usr/bin/env python3
"""CLI shim over ``lvkit.docs.generate`` — the real HTML docs pipeline.

Kept as a thin wrapper (rather than removed outright) because
``lvkit.mcp.tools.generate_documents`` subprocesses this script with
``--search-path``/``--no-expand``/``--vilib``/``--userlib``. All actual
generation logic — including class landing pages and method-level access
badges/override navigation — lives in ``lvkit.docs.generate.generate_documents``;
this file only parses argv and forwards.

Usage:
    python scripts/generate_docs.py <vi_or_library_path> <output_dir>
        [--search-path PATH ...]
"""
import argparse
import sys
import traceback
from pathlib import Path

# Add src to path if running as script
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lvkit.docs.generate import generate_documents


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate HTML documentation for LabVIEW VIs"
    )
    parser.add_argument(
        "library_path", help="Path to .lvlib, .lvclass, .vi file, or directory"
    )
    parser.add_argument("output_dir", help="Output directory for HTML files")
    parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        help="Search path for dependencies",
    )
    parser.add_argument(
        "--no-expand", action="store_true", help="Don't expand SubVI dependencies"
    )
    parser.add_argument(
        "--vilib", default=None, metavar="DIR",
        help="Path to LabVIEW vi.lib on disk for <vilib> resolution.",
    )
    parser.add_argument(
        "--userlib", default=None, metavar="DIR",
        help="Path to LabVIEW user.lib on disk for <userlib> resolution.",
    )

    args = parser.parse_args()

    try:
        result = generate_documents(
            library_path=args.library_path,
            output_dir=args.output_dir,
            search_paths=args.search_paths,
            expand_subvis=not args.no_expand,
            vilib_root=Path(args.vilib) if args.vilib else None,
            userlib_root=Path(args.userlib) if args.userlib else None,
        )
        print("\n" + result)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Deterministic VI analyzer - returns structured JSON data about a VI.

This is a thin CLI wrapper around the core analysis.analyze_vi() function.
It serializes the dataclass output to JSON for command-line use.

Usage:
    python scripts/analyze_vi.py <vi_path> [--search-path PATH ...] [--no-expand]
"""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Add src to path if running as script
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vipy.analysis import analyze_vi as core_analyze_vi


def main():
    """CLI entry point - wraps core function and serializes to JSON."""
    parser = argparse.ArgumentParser(
        description="Analyze a LabVIEW VI and return structured JSON"
    )
    parser.add_argument(
        "vi_path", help="Path to VI file (.vi) or block diagram XML (*_BDHb.xml)"
    )
    parser.add_argument(
        "--search-path", action="append", dest="search_paths",
        help="Search path for dependencies",
    )
    parser.add_argument(
        "--no-expand", action="store_true", help="Don't expand SubVI dependencies"
    )

    args = parser.parse_args()

    try:
        # Call core analysis function (returns VIAnalysis dataclass)
        result = core_analyze_vi(
            vi_path=args.vi_path,
            search_paths=args.search_paths,
            expand_subvis=not args.no_expand,
        )

        # Serialize to JSON for CLI output
        output = {
            "vi_name": result.vi_name,
            "summary": result.summary,
            "controls": [asdict(c) for c in result.controls],
            "indicators": [asdict(i) for i in result.indicators],
            "graph": {
                "inputs": [asdict(inp) for inp in result.graph.inputs],
                "outputs": [asdict(out) for out in result.graph.outputs],
                "operations": [asdict(op) for op in result.graph.operations],
                "constants": [asdict(c) for c in result.graph.constants],
                "data_flow": [asdict(w) for w in result.graph.data_flow],
            },
            "dependencies": result.dependencies,
            "execution_order": result.execution_order,
        }

        # Output JSON to stdout
        print(json.dumps(output, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

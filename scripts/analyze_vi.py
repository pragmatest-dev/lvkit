#!/usr/bin/env python3
"""Deterministic VI analyzer - returns structured JSON data about a VI.

Usage:
    python scripts/analyze_vi.py <vi_path> [--search-path PATH ...] [--no-expand]
"""
import sys
import json
import argparse
from pathlib import Path

# Add src to path if running as script
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vipy.memory_graph import InMemoryVIGraph
from vipy.docs.utils import generate_dependency_description


def infer_description(name: str | None, type_str: str | None, direction: str) -> str:
    """Infer description from name and type."""
    if not name:
        return f"{direction.capitalize()} parameter"
    type_part = f" ({type_str})" if type_str and type_str != "Any" else ""
    return f"{name}{type_part}"


def generate_vi_summary(vi_name: str, controls: list, indicators: list, dependencies: dict) -> str:
    """Generate brief summary of VI."""
    parts = []
    if controls:
        parts.append(f"takes {len(controls)} input(s)")
    if indicators:
        parts.append(f"returns {len(indicators)} output(s)")
    if dependencies:
        parts.append(f"calls {len(dependencies)} SubVI(s)")

    if parts:
        return f"VI '{vi_name}' - {', '.join(parts)}"
    return f"VI '{vi_name}'"


def analyze_vi(
    vi_path: str,
    search_paths: list[str] | None = None,
    expand_subvis: bool = True
) -> dict:
    """Analyze a VI and return structured data.

    Returns:
        Dictionary with vi_name, summary, controls, indicators, graph, dependencies, execution_order
    """
    from dataclasses import asdict

    # Load VI with optional dependency expansion
    graph = InMemoryVIGraph()
    search_path_objs = [Path(p) for p in (search_paths or [])]

    vi_path_obj = Path(vi_path)
    if not vi_path_obj.exists():
        raise FileNotFoundError(f"VI file not found: {vi_path}")

    graph.load_vi(vi_path_obj, expand_subvis=expand_subvis, search_paths=search_path_objs or None)

    # Get main VI name - resolve from path
    if vi_path.endswith("_BDHb.xml"):
        vi_name = Path(vi_path).name.replace("_BDHb.xml", ".vi")
    else:
        vi_name = Path(vi_path).name

    # Resolve qualified name if needed
    all_vis = graph.list_vis()
    if vi_name not in all_vis:
        # Try to find by matching filename
        for v in all_vis:
            if v.endswith(vi_name) or v.endswith(":" + vi_name):
                vi_name = v
                break

    # Get VI context (already returns dicts)
    vi_context = graph.get_vi_context(vi_name)

    # Extract controls with descriptions
    controls = []
    for inp in vi_context.get("inputs", []):
        controls.append({
            "name": inp["name"] or f"input_{inp['slot_index']}",
            "type": inp["type"] or "Any",
            "default_value": inp.get("default_value"),
            "description": infer_description(inp["name"], inp["type"], "input"),
            "slot_index": inp.get("slot_index", 0),
        })

    # Extract indicators with descriptions
    indicators = []
    for out in vi_context.get("outputs", []):
        indicators.append({
            "name": out["name"] or f"output_{out['slot_index']}",
            "type": out["type"] or "Any",
            "description": infer_description(out["name"], out["type"], "output"),
            "slot_index": out.get("slot_index", 0),
        })

    # Graph structure is already dicts from get_vi_context
    graph_data = {
        "inputs": vi_context.get("inputs", []),
        "outputs": vi_context.get("outputs", []),
        "operations": vi_context.get("operations", []),
        "constants": vi_context.get("constants", []),
        "data_flow": vi_context.get("data_flow", []),
    }

    # Generate dependency descriptions
    dependencies = {}
    for op in vi_context.get("operations", []):
        if "SubVI" in op.get("labels", []) and op.get("name"):
            dep_name = op["name"]
            if dep_name not in dependencies:  # Avoid duplicates
                dependencies[dep_name] = generate_dependency_description(dep_name, graph)

    # Get execution order
    try:
        execution_order = graph.get_operation_order(vi_name)
    except Exception:
        execution_order = []

    # Generate summary
    summary = generate_vi_summary(vi_name, controls, indicators, dependencies)

    return {
        "vi_name": vi_name,
        "summary": summary,
        "controls": controls,
        "indicators": indicators,
        "graph": graph_data,
        "dependencies": dependencies,
        "execution_order": execution_order,
    }


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Analyze a LabVIEW VI and return structured JSON")
    parser.add_argument("vi_path", help="Path to VI file (.vi) or block diagram XML (*_BDHb.xml)")
    parser.add_argument("--search-path", action="append", dest="search_paths", help="Search path for dependencies")
    parser.add_argument("--no-expand", action="store_true", help="Don't expand SubVI dependencies")

    args = parser.parse_args()

    try:
        result = analyze_vi(
            vi_path=args.vi_path,
            search_paths=args.search_paths,
            expand_subvis=not args.no_expand,
        )
        # Output JSON to stdout
        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

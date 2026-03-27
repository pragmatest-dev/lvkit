#!/usr/bin/env python3
"""Analyze OpenG VIs using InMemoryVIGraph for proper dataflow analysis.

Uses the graph infrastructure to:
1. Load each VI and parse its dataflow
2. Trace inputs → operations → outputs
3. Resolve primitives to Python hints
4. Synthesize inline Python for simple VIs

Output: CSV with derivation notes showing HOW each suggestion was determined.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vipy.memory_graph import InMemoryVIGraph
from vipy.primitive_resolver import get_resolver


@dataclass
class VIAnalysis:
    """Analysis results for a single VI."""
    name: str
    category: str

    # Structure from graph
    inputs: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    operations: list[dict] = field(default_factory=list)
    wires: list[dict] = field(default_factory=list)
    num_loops: int = 0

    # Primitives found
    primitives: list[dict] = field(default_factory=list)  # {id, name, python}
    subvi_calls: list[str] = field(default_factory=list)

    # Analysis results
    confidence: str = "low"
    python_inline: str = ""
    inline_imports: list[str] = field(default_factory=list)
    derivation: str = ""  # How we derived the inline


def guess_category(vi_name: str) -> str:
    """Guess category from VI name patterns."""
    name_lower = vi_name.lower()

    file_keywords = ["path", "file", "dir", "folder", "copy", "delete"]
    if any(x in name_lower for x in file_keywords):
        return "openg/file"
    if any(x in name_lower for x in ["array", "1d", "2d", "index", "sort", "reverse"]):
        return "openg/array"
    if any(x in name_lower for x in ["string", "str", "trim", "split", "join"]):
        return "openg/string"
    if any(x in name_lower for x in ["error", "err"]):
        return "openg/error"
    if any(x in name_lower for x in ["variant", "dict"]):
        return "openg/dictionary"
    if any(x in name_lower for x in ["time", "date", "tick"]):
        return "openg/time"
    if any(x in name_lower for x in ["bool"]):
        return "openg/boolean"
    if any(x in name_lower for x in ["numeric", "number"]):
        return "openg/numeric"

    return "openg/misc"


def analyze_vi(graph: InMemoryVIGraph, vi_name: str, resolver) -> VIAnalysis:
    """Analyze a single VI using graph data."""
    analysis = VIAnalysis(
        name=vi_name.replace(".vi", ""),
        category=guess_category(vi_name),
    )

    try:
        # Get structure from graph
        analysis.inputs = graph.get_inputs(vi_name)
        analysis.outputs = graph.get_outputs(vi_name)
        analysis.operations = graph.get_operations(vi_name)
        analysis.wires = graph.get_wires(vi_name)

        # Count loops
        for op in analysis.operations:
            if op.get("loop_type"):
                analysis.num_loops += 1

        # Categorize operations (check labels since kind varies)
        for op in analysis.operations:
            labels = op.get("labels", [])
            prim_id = op.get("primResID") or op.get("prim_id")

            if "Primitive" in labels or prim_id:
                if prim_id:
                    resolved = resolver.resolve(prim_id=int(prim_id))
                    if resolved:
                        analysis.primitives.append({
                            "id": prim_id,
                            "name": resolved.name,
                            "python": resolved.python_hint,
                            "terminals": resolved.terminals,
                        })
                    else:
                        analysis.primitives.append({
                            "id": prim_id,
                            "name": f"Unknown_{prim_id}",
                            "python": None,
                        })

            elif "SubVI" in labels:
                subvi_name = op.get("name", "")
                if subvi_name:
                    analysis.subvi_calls.append(subvi_name)

            # Check for primitives inside loops
            inner_nodes = op.get("inner_nodes", [])
            for inner in inner_nodes:
                inner_labels = inner.get("labels", [])
                inner_prim_id = inner.get("primResID")

                if "Primitive" in inner_labels or inner_prim_id:
                    if inner_prim_id:
                        resolved = resolver.resolve(prim_id=int(inner_prim_id))
                        if resolved:
                            analysis.primitives.append({
                                "id": inner_prim_id,
                                "name": resolved.name,
                                "python": resolved.python_hint,
                                "terminals": resolved.terminals,
                            })
                        else:
                            analysis.primitives.append({
                                "id": inner_prim_id,
                                "name": f"Unknown_{inner_prim_id}",
                                "python": None,
                            })

                elif "SubVI" in inner_labels:
                    subvi_name = inner.get("name", "")
                    if subvi_name:
                        analysis.subvi_calls.append(subvi_name)

        # Determine inline potential
        _determine_inline(analysis)

    except Exception as e:
        analysis.derivation = f"Error: {e}"

    return analysis


def _determine_inline(analysis: VIAnalysis) -> None:
    """Determine inline Python based on graph analysis."""

    # Complex VI - too many operations or loops
    if analysis.num_loops > 0:
        analysis.confidence = "low"
        analysis.derivation = f"Has {analysis.num_loops} loop(s) - convert normally"
        return

    if len(analysis.subvi_calls) > 2:
        analysis.confidence = "low"
        analysis.derivation = (
            f"Has {len(analysis.subvi_calls)} SubVI calls - convert normally"
        )
        return

    if len(analysis.primitives) > 3:
        analysis.confidence = "low"
        analysis.derivation = (
            f"Has {len(analysis.primitives)} primitives - convert normally"
        )
        return

    # Empty VI (just pass-through or type definition)
    if len(analysis.primitives) == 0 and len(analysis.subvi_calls) == 0:
        if len(analysis.outputs) > 0:
            # Likely a type definition or constant
            analysis.confidence = "medium"
            analysis.derivation = "No operations - possibly type def or constant"
        else:
            analysis.confidence = "low"
            analysis.derivation = "Empty VI"
        return

    # Single primitive - high confidence inline
    if len(analysis.primitives) == 1 and len(analysis.subvi_calls) == 0:
        prim = analysis.primitives[0]
        python_hint = prim.get("python")

        if python_hint:
            analysis.confidence = "high"
            analysis.python_inline = _build_inline_from_primitive(
                prim, analysis.inputs, analysis.outputs, analysis.wires
            )
            analysis.derivation = (
                f"Single prim {prim['id']} ({prim['name']}) → {python_hint}"
            )
            _extract_imports(analysis, python_hint)
        else:
            analysis.confidence = "medium"
            analysis.derivation = f"Single prim {prim['id']} but no Python hint"
        return

    # 2-3 primitives in sequence - medium confidence
    if 1 < len(analysis.primitives) <= 3 and len(analysis.subvi_calls) == 0:
        prim_names = [p["name"] for p in analysis.primitives]
        prim_ids = [p["id"] for p in analysis.primitives]

        # Check if all have Python hints
        all_have_hints = all(p.get("python") for p in analysis.primitives)

        if all_have_hints:
            analysis.confidence = "medium"
            analysis.derivation = (
                f"Sequence: {' → '.join(prim_names)} (prims {prim_ids})"
            )
            # Could try to chain the operations here
        else:
            analysis.confidence = "low"
            analysis.derivation = f"Multiple prims {prim_ids} - some missing hints"
        return

    # Has SubVI calls
    if len(analysis.subvi_calls) > 0:
        analysis.confidence = "low"
        analysis.derivation = f"Has SubVI calls: {analysis.subvi_calls}"
        return

    analysis.confidence = "low"
    analysis.derivation = "Could not determine inline"


def _build_inline_from_primitive(
    prim: dict,
    inputs: list[dict],
    outputs: list[dict],
    wires: list[dict],
) -> str:
    """Build inline Python from a single primitive using wire tracing."""
    python_hint = prim.get("python", "")
    if not python_hint:
        return ""

    # Get output names from FP terminals
    output_names = [out.get("name", f"out{i}") for i, out in enumerate(outputs)]

    # Try to build substituted template
    # For now, use placeholder format
    if output_names:
        out_part = ", ".join(f"{{{name}}}" for name in output_names)
        return f"{out_part} = {python_hint}"
    else:
        return python_hint


def _extract_imports(analysis: VIAnalysis, python_hint: str) -> None:
    """Extract required imports from Python hint."""
    if "Path" in python_hint or "path" in python_hint.lower():
        analysis.inline_imports.append("from pathlib import Path")
    if "os." in python_hint:
        analysis.inline_imports.append("import os")
    if "shutil." in python_hint:
        analysis.inline_imports.append("import shutil")
    if "tempfile." in python_hint:
        analysis.inline_imports.append("import tempfile")
    if "np." in python_hint or "numpy" in python_hint:
        analysis.inline_imports.append("import numpy as np")


def main():
    parsed_path = Path("samples/OpenG/parsed")
    if not parsed_path.exists():
        print(f"Error: {parsed_path} not found", file=sys.stderr)
        sys.exit(1)

    # Find all BDHb.xml files
    bd_files = list(parsed_path.glob("*__ogtk_BDHb.xml"))
    print(f"Found {len(bd_files)} OpenG VIs to analyze", file=sys.stderr)

    # Initialize resolver
    resolver = get_resolver()

    # Analyze each VI
    analyses: list[VIAnalysis] = []

    for i, bd_xml in enumerate(bd_files):
        if i % 100 == 0:
            print(f"  Processing {i}/{len(bd_files)}...", file=sys.stderr)

        # Load single VI into fresh graph
        graph = InMemoryVIGraph()
        try:
            graph.load_vi(bd_xml, expand_subvis=False)
        except Exception as e:
            print(f"  Error loading {bd_xml.name}: {e}", file=sys.stderr)
            continue

        # Get the VI name
        vi_names = graph.list_vis()
        if not vi_names:
            continue

        vi_name = vi_names[0]
        analysis = analyze_vi(graph, vi_name, resolver)
        analyses.append(analysis)

    # Sort by confidence
    analyses.sort(key=lambda a: (
        {"high": 0, "medium": 1, "low": 2}.get(a.confidence, 3),
        len(a.primitives),
        a.name
    ))

    # Output CSV
    writer = csv.writer(sys.stdout)
    writer.writerow([
        "name",
        "category",
        "confidence",
        "python_inline",
        "inline_imports",
        "primitive_ids",
        "primitive_names",
        "subvi_calls",
        "num_loops",
        "num_inputs",
        "num_outputs",
        "derivation",
    ])

    for a in analyses:
        prim_ids = ";".join(str(p["id"]) for p in a.primitives)
        prim_names = ";".join(p["name"] for p in a.primitives)
        imports = ";".join(a.inline_imports)
        subvis = ";".join(a.subvi_calls)

        writer.writerow([
            a.name,
            a.category,
            a.confidence,
            a.python_inline,
            imports,
            prim_ids,
            prim_names,
            subvis,
            a.num_loops,
            len(a.inputs),
            len(a.outputs),
            a.derivation,
        ])

    # Summary
    high = sum(1 for a in analyses if a.confidence == "high")
    med = sum(1 for a in analyses if a.confidence == "medium")
    low = sum(1 for a in analyses if a.confidence == "low")
    print(
        f"\nSummary: {high} high, {med} medium, {low} low confidence", file=sys.stderr
    )


if __name__ == "__main__":
    main()

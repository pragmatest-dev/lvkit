#!/usr/bin/env python3
"""Analyze OpenG VIs for inline Python potential.

Outputs CSV with:
- VI name, category, complexity metrics
- Inline confidence and suggested Python
- Format suitable for conversion to openg/*.json
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vipy.extractor import extract_vi_xml
from vipy.parser import parse_block_diagram, parse_connector_pane
from vipy.frontpanel import parse_front_panel


@dataclass
class VIAnalysis:
    """Analysis results for a single VI."""
    name: str
    category: str
    vi_path: str

    # Complexity metrics
    num_primitives: int = 0
    num_subvis: int = 0
    num_loops: int = 0
    num_case_structures: int = 0
    num_constants: int = 0
    num_wires: int = 0

    # Terminal info
    inputs: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)

    # Primitive breakdown
    primitive_ids: list[int] = field(default_factory=list)
    subvi_names: list[str] = field(default_factory=list)

    # Inline potential
    confidence: str = "low"  # high, medium, low
    inline_suggestion: str = ""
    notes: str = ""

    @property
    def complexity_score(self) -> int:
        """Calculate complexity score - lower is simpler."""
        return (
            self.num_primitives * 1 +
            self.num_subvis * 3 +
            self.num_loops * 5 +
            self.num_case_structures * 4 +
            self.num_constants
        )


# Known primitive -> Python mappings for inline suggestions
PRIMITIVE_INLINE_MAP = {
    # File operations
    1419: ("Path / {name}", "from pathlib import Path"),  # Build Path
    1420: ("{path}.parent, {path}.name", ""),  # Strip Path

    # String operations
    1364: ("len({string})", ""),  # String Length
    1365: ("{string1} + {string2}", ""),  # Concatenate Strings

    # Array operations
    1281: ("len({array})", ""),  # Array Size
    1285: ("{array}[{index}]", ""),  # Index Array

    # Numeric
    1157: ("{x} + {y}", ""),  # Add
    1158: ("{x} - {y}", ""),  # Subtract
    1159: ("{x} * {y}", ""),  # Multiply
    1160: ("{x} / {y}", ""),  # Divide

    # Boolean
    1199: ("not {x}", ""),  # Not
    1200: ("{x} and {y}", ""),  # And
    1201: ("{x} or {y}", ""),  # Or

    # Comparison
    1184: ("{x} == {y}", ""),  # Equal
    1185: ("{x} != {y}", ""),  # Not Equal
    1186: ("{x} > {y}", ""),  # Greater Than
    1187: ("{x} < {y}", ""),  # Less Than
}

# Known VI name -> inline Python patterns
VI_INLINE_PATTERNS = {
    "Create Dir if Non-Existant__ogtk": (
        "os.makedirs({path}, exist_ok=True)",
        ["import os"],
        "high"
    ),
    "File Exists - Scalar__ogtk": (
        "{exists} = Path({path}).exists()",
        ["from pathlib import Path"],
        "high"
    ),
    "File Exists__ogtk": (
        "{exists} = Path({path}).exists()",
        ["from pathlib import Path"],
        "high"  # Polymorphic wrapper
    ),
    "Delete Recursive__ogtk": (
        "shutil.rmtree({path}, ignore_errors=True)",
        ["import shutil"],
        "high"
    ),
    "Temporary Directory__ogtk": (
        "{path} = Path(tempfile.gettempdir())",
        ["import tempfile", "from pathlib import Path"],
        "high"
    ),
    "Temporary Filename__ogtk": (
        "{path} = Path(tempfile.mktemp())",
        ["import tempfile", "from pathlib import Path"],
        "medium"  # May need extension handling
    ),
    "Application Directory__ogtk": (
        "{path} = Path(__file__).parent",
        ["from pathlib import Path"],
        "medium"  # Context-dependent
    ),
    "Default Directory__ogtk": (
        "{path} = Path.cwd()",
        ["from pathlib import Path"],
        "high"
    ),
    "Current VI's Path__ogtk": (
        "{path} = Path(__file__)",
        ["from pathlib import Path"],
        "high"
    ),
    "Current VIs Parent Directory__ogtk": (
        "{path} = Path(__file__).parent",
        ["from pathlib import Path"],
        "high"
    ),
    "Strip Path Extension - Path__ogtk": (
        "{path_out} = {path}.with_suffix('')",
        [],
        "high"
    ),
    "Strip Path Extension - String__ogtk": (
        "{path_out} = Path({path}).with_suffix('').name",
        ["from pathlib import Path"],
        "high"
    ),
    "Convert File Extension (Path)__ogtk": (
        "{path_out} = {path}.with_suffix({new_ext})",
        [],
        "high"
    ),
    "List Directory__ogtk": (
        "{files} = list(Path({path}).iterdir())",
        ["from pathlib import Path"],
        "medium"  # May need filtering
    ),
    "List Directory Recursive__ogtk": (
        "{files} = list(Path({path}).rglob('*'))",
        ["from pathlib import Path"],
        "medium"
    ),
    # Array operations
    "Array Size__ogtk": (
        "{size} = len({array})",
        [],
        "high"
    ),
    "Reverse Array__ogtk": (
        "{reversed} = {array}[::-1]",
        [],
        "high"
    ),
    # String operations
    "Trim Whitespace__ogtk": (
        "{trimmed} = {string}.strip()",
        [],
        "high"
    ),
}


def analyze_vi(vi_path: Path, category: str) -> VIAnalysis | None:
    """Analyze a single VI file or pre-parsed XML."""
    # Handle pre-parsed XML files directly
    if vi_path.name.endswith("_BDHb.xml"):
        bd_xml = vi_path
        fp_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", "_FPHb.xml"))
        main_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", ".xml"))
        vi_name = vi_path.name.replace("_BDHb.xml", "")
    else:
        try:
            bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)
        except Exception as e:
            return None
        vi_name = vi_path.stem
    analysis = VIAnalysis(
        name=vi_name,
        category=category,
        vi_path=str(vi_path),
    )

    try:
        bd = parse_block_diagram(bd_xml)
    except Exception as e:
        analysis.notes = f"Parse error: {e}"
        return analysis

    # Count nodes by type
    for node in bd.nodes:
        if node.node_type == "prim":
            analysis.num_primitives += 1
            if node.prim_res_id:
                analysis.primitive_ids.append(node.prim_res_id)
        elif node.node_type in ("iUse", "polyIUse"):
            analysis.num_subvis += 1
            if node.name:
                analysis.subvi_names.append(node.name)

    # Count structures
    analysis.num_loops = len(bd.loops)
    analysis.num_constants = len(bd.constants)
    analysis.num_wires = len(bd.wires)

    # Parse front panel for terminal info
    if fp_xml and fp_xml.exists():
        try:
            fp = parse_front_panel(fp_xml, bd_xml)
            conpane = parse_connector_pane(fp_xml)

            for ctrl in fp.controls:
                term_info = {
                    "name": ctrl.name,
                    "type": ctrl.type_desc,
                    "index": None,  # Would need conpane mapping
                }
                if ctrl.is_indicator:
                    analysis.outputs.append(term_info)
                else:
                    analysis.inputs.append(term_info)
        except Exception:
            pass

    # Determine inline potential
    if vi_name in VI_INLINE_PATTERNS:
        pattern, imports, conf = VI_INLINE_PATTERNS[vi_name]
        analysis.inline_suggestion = pattern
        analysis.confidence = conf
        analysis.notes = f"imports: {imports}" if imports else ""
    elif analysis.complexity_score <= 2 and analysis.num_primitives == 1:
        # Single primitive VI - check if we know the mapping
        if analysis.primitive_ids and analysis.primitive_ids[0] in PRIMITIVE_INLINE_MAP:
            pattern, imp = PRIMITIVE_INLINE_MAP[analysis.primitive_ids[0]]
            analysis.inline_suggestion = pattern
            analysis.confidence = "medium"
            analysis.notes = f"Single prim {analysis.primitive_ids[0]}"
    elif analysis.complexity_score <= 3 and analysis.num_loops == 0:
        analysis.confidence = "medium"
        analysis.notes = "Simple logic, needs manual review"
    elif analysis.num_loops > 0 or analysis.num_subvis > 2:
        analysis.confidence = "low"
        analysis.notes = "Complex - convert normally"
    else:
        analysis.confidence = "low"

    return analysis


def find_openg_vis(base_path: Path) -> list[tuple[Path, str]]:
    """Find all OpenG VIs with their categories.

    Looks for pre-parsed XML files in samples/OpenG/parsed/ first,
    falls back to extracting from .vi files.
    """
    vis = []

    # Check for pre-parsed folder first
    parsed_path = base_path.parent / "parsed"
    if parsed_path.exists():
        # Use pre-parsed XML files - much faster
        for bd_xml in parsed_path.glob("*__ogtk_BDHb.xml"):
            vi_name = bd_xml.name.replace("_BDHb.xml", "")
            # Guess category from name patterns
            category = guess_category(vi_name)
            vis.append((bd_xml, category))
        return vis

    # Fall back to extracting from .vi files
    lib_path = base_path / "File Group 0" / "user.lib" / "_OpenG.lib"

    if lib_path.exists():
        for category_dir in lib_path.iterdir():
            if not category_dir.is_dir():
                continue
            category = category_dir.name

            # Find VIs in category (may be in .llb subdirs)
            for vi_file in category_dir.rglob("*__ogtk.vi"):
                vis.append((vi_file, f"openg/{category}"))

    # Also check other file groups for stray VIs
    for fg in base_path.iterdir():
        if fg.name.startswith("File Group") and fg.is_dir():
            for vi_file in fg.glob("*__ogtk.vi"):
                if not any(v[0] == vi_file for v in vis):
                    vis.append((vi_file, "openg/misc"))

    return vis


def guess_category(vi_name: str) -> str:
    """Guess category from VI name patterns."""
    name_lower = vi_name.lower()

    # File operations
    if any(x in name_lower for x in ["path", "file", "dir", "folder", "copy", "delete", "list"]):
        return "openg/file"
    # Array operations
    if any(x in name_lower for x in ["array", "1d", "2d", "index", "sort", "search", "reverse"]):
        return "openg/array"
    # String operations
    if any(x in name_lower for x in ["string", "str", "trim", "split", "join", "format"]):
        return "openg/string"
    # Error handling
    if any(x in name_lower for x in ["error", "err"]):
        return "openg/error"
    # Numeric
    if any(x in name_lower for x in ["numeric", "number", "int", "float", "round"]):
        return "openg/numeric"
    # Boolean
    if any(x in name_lower for x in ["boolean", "bool", "true", "false"]):
        return "openg/boolean"
    # Time
    if any(x in name_lower for x in ["time", "date", "tick", "wait"]):
        return "openg/time"
    # Comparison
    if any(x in name_lower for x in ["compare", "equal", "match"]):
        return "openg/comparison"
    # Dictionary
    if any(x in name_lower for x in ["dict", "variant"]):
        return "openg/dictionary"
    # Application control
    if any(x in name_lower for x in ["vi ", "app", "call", "run"]):
        return "openg/appcontrol"

    return "openg/misc"


def main():
    openg_path = Path("samples/OpenG/extracted")
    if not openg_path.exists():
        print(f"Error: {openg_path} not found")
        sys.exit(1)

    print(f"Scanning {openg_path}...", file=sys.stderr)
    vis = find_openg_vis(openg_path)
    print(f"Found {len(vis)} OpenG VIs", file=sys.stderr)

    # Analyze all VIs
    analyses: list[VIAnalysis] = []
    for vi_path, category in vis:
        result = analyze_vi(vi_path, category)
        if result:
            analyses.append(result)

    # Sort by confidence (high first) then by complexity
    analyses.sort(key=lambda a: (
        {"high": 0, "medium": 1, "low": 2}.get(a.confidence, 3),
        a.complexity_score,
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
        "num_primitives",
        "num_subvis",
        "num_loops",
        "complexity",
        "inputs",
        "outputs",
        "notes",
    ])

    for a in analyses:
        # Format inputs/outputs as JSON-like for easy parsing
        inputs_str = "; ".join(f"{i['name']}:{i['type']}" for i in a.inputs)
        outputs_str = "; ".join(f"{o['name']}:{o['type']}" for o in a.outputs)

        # Extract imports from notes if present
        imports_str = ""
        if "imports:" in a.notes:
            imports_str = a.notes.split("imports:")[1].strip()
            notes = ""
        else:
            notes = a.notes

        writer.writerow([
            a.name,
            a.category,
            a.confidence,
            a.inline_suggestion,
            imports_str,
            a.num_primitives,
            a.num_subvis,
            a.num_loops,
            a.complexity_score,
            inputs_str,
            outputs_str,
            notes,
        ])

    # Summary stats
    high = sum(1 for a in analyses if a.confidence == "high")
    med = sum(1 for a in analyses if a.confidence == "medium")
    low = sum(1 for a in analyses if a.confidence == "low")
    print(f"\nSummary: {high} high, {med} medium, {low} low confidence", file=sys.stderr)


if __name__ == "__main__":
    main()

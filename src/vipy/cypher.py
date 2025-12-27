"""Generate Cypher graph representation of LabVIEW VIs."""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .blockdiagram import (
    _get_enum_value_label,
    _get_fp_terminal_names,
    decode_constant,
)
from .frontpanel import FPControl, parse_front_panel
from .parser import BlockDiagram, parse_block_diagram, parse_vi_metadata


def from_blockdiagram(
    bd_xml_path: Path | str,
    main_xml_path: Path | str | None = None,
) -> str:
    """Generate a Cypher graph representation of a VI.

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)
        main_xml_path: Path to the main VI XML (optional, for metadata)

    Returns:
        Cypher CREATE statements representing the VI as a graph
    """
    bd_xml_path = Path(bd_xml_path)
    bd = parse_block_diagram(bd_xml_path)

    # Try to get front panel terminal names
    fp_names = _get_fp_terminal_names(bd_xml_path)

    # Get VI name and qualified name from metadata
    vi_name = "Unknown VI"
    qualified_name = None
    if main_xml_path:
        meta = parse_vi_metadata(main_xml_path)
        vi_name = meta.get("name", vi_name)
        qualified_name = meta.get("qualified_name", vi_name)
    else:
        vi_name = bd_xml_path.stem.replace("_BDHb", "")

    if qualified_name is None:
        qualified_name = vi_name

    # Sanitize name for Cypher variable
    vi_var = _sanitize_name(qualified_name)

    lines = [
        "// Cypher graph representation of LabVIEW VI",
        f"// VI: {qualified_name}",
        "",
        "// Create the VI node",
        f'CREATE ({vi_var}:VI {{name: "{qualified_name}"}})',
        "",
        "// Constants",
    ]

    # Track node variables for edge creation
    node_vars: dict[str, str] = {}

    # Create constant nodes
    for const in bd.constants:
        const_var = f"c_{const.uid}"
        node_vars[const.uid] = const_var
        node_id = _make_node_id(qualified_name, const.uid)

        # Decode constant value
        enum_label = _get_enum_value_label(const, bd.enum_labels)
        if enum_label:
            # Extract just the python hint if present
            value_desc = enum_label
            python_hint = ""
            if " -> Python: " in enum_label:
                parts = enum_label.split(" -> Python: ")
                value_desc = parts[0]
                python_hint = parts[1]
            lines.append(
                f'CREATE ({const_var}:Constant {{id: "{node_id}", '
                f'value: "{_escape(value_desc)}", '
                f'python: "{_escape(python_hint)}"}})'
            )
        else:
            val_type, val = decode_constant(const)
            lines.append(
                f'CREATE ({const_var}:Constant {{id: "{node_id}", '
                f'type: "{val_type}", value: "{_escape(val)}"}})'
            )

        # Link constant to VI
        lines.append(f"CREATE ({vi_var})-[:CONTAINS]->({const_var})")

    lines.append("")
    lines.append("// Operations (Primitives and SubVIs)")

    # Create operation nodes
    for node in bd.nodes:
        node_var = f"n_{node.uid}"
        node_vars[node.uid] = node_var
        node_id = _make_node_id(qualified_name, node.uid)

        if node.node_type == "iUse":
            # SubVI call
            subvi_name = node.name or "Unknown SubVI"
            lines.append(
                f'CREATE ({node_var}:SubVI {{id: "{node_id}", '
                f'name: "{_escape(subvi_name)}"}})'
            )
        elif node.node_type == "prim":
            # Store primitive with its ID - LLM infers meaning from context
            lines.append(
                f'CREATE ({node_var}:Primitive {{id: "{node_id}", '
                f'primResID: {node.prim_res_id}}})'
            )
        elif node.node_type in ("whileLoop", "forLoop"):
            lines.append(
                f'CREATE ({node_var}:Loop {{id: "{node_id}", '
                f'type: "{node.node_type}"}})'
            )
        elif node.node_type in ("select", "caseStruct"):
            lines.append(
                f'CREATE ({node_var}:Conditional {{id: "{node_id}", '
                f'type: "{node.node_type}"}})'
            )
        else:
            lines.append(
                f'CREATE ({node_var}:Node {{id: "{node_id}", '
                f'type: "{node.node_type}"}})'
            )

        # Link node to VI
        lines.append(f"CREATE ({vi_var})-[:CONTAINS]->({node_var})")

    # Create VI input and output nodes
    if bd.fp_terminals:
        lines.append("")
        lines.append("// VI Inputs and Outputs (front panel terminals)")
        for i, fp_term in enumerate(bd.fp_terminals):
            term_var = f"fp_{fp_term.uid}"
            node_vars[fp_term.uid] = term_var
            node_id = _make_node_id(qualified_name, fp_term.uid)

            # Try to get name from front panel, fall back to generic name
            name = fp_names.get(fp_term.fp_dco_uid) or fp_term.name
            if not name:
                name = f"output_{i}" if fp_term.is_indicator else f"input_{i}"

            if fp_term.is_indicator:
                # Output indicator
                lines.append(
                    f'CREATE ({term_var}:Output {{id: "{node_id}", '
                    f'name: "{_escape(name)}"}})'
                )
                lines.append(
                    f"CREATE ({vi_var})-[:RETURNS]->({term_var})"
                )
            else:
                # Input control
                lines.append(
                    f'CREATE ({term_var}:Input {{id: "{node_id}", '
                    f'name: "{_escape(name)}"}})'
                )
                lines.append(
                    f"CREATE ({term_var})-[:PARAMETER_OF]->({vi_var})"
                )

    lines.append("")
    lines.append("// Data flow edges")

    # Create edges for wires with terminal info
    for wire in bd.wires:
        from_parent = bd.term_to_parent.get(wire.from_term, wire.from_term)
        to_parent = bd.term_to_parent.get(wire.to_term, wire.to_term)

        from_var = node_vars.get(from_parent)
        to_var = node_vars.get(to_parent)

        if from_var and to_var:
            # Include terminal IDs to preserve connection details
            lines.append(
                f'CREATE ({from_var})-[:FLOWS_TO {{from_term: "{wire.from_term}", to_term: "{wire.to_term}"}}]->({to_var})'
            )

    lines.append("")
    lines.append("// End of VI graph")

    return "\n".join(lines)


def _generate_constants(
    bd: BlockDiagram,
    vi_var: str,
    node_vars: dict[str, str],
    prefix: str,
    qualified_name: str,
) -> list[str]:
    """Generate Cypher statements for block diagram constants."""
    lines = ["", "// === Block Diagram Constants ==="]

    for const in bd.constants:
        const_var = f"{prefix}c_{const.uid}"
        node_vars[const.uid] = const_var
        node_id = _make_node_id(qualified_name, const.uid)

        enum_label = _get_enum_value_label(const, bd.enum_labels)
        if enum_label:
            value_desc = enum_label
            python_hint = ""
            if " -> Python: " in enum_label:
                parts = enum_label.split(" -> Python: ")
                value_desc = parts[0]
                python_hint = parts[1]
            lines.append(
                f'CREATE ({const_var}:Constant {{id: "{node_id}", '
                f'value: "{_escape(value_desc)}", python: "{_escape(python_hint)}"}})'
            )
        else:
            val_type, val = decode_constant(const)
            lines.append(
                f'CREATE ({const_var}:Constant {{id: "{node_id}", '
                f'type: "{val_type}", value: "{_escape(val)}"}})'
            )
        lines.append(f"CREATE ({vi_var})-[:CONTAINS]->({const_var})")

    return lines


def _generate_operations(
    bd: BlockDiagram,
    vi_var: str,
    node_vars: dict[str, str],
    prefix: str,
    qualified_name: str,
) -> list[str]:
    """Generate Cypher statements for block diagram operations."""
    lines = ["", "// === Block Diagram Operations ==="]

    for node in bd.nodes:
        node_var = f"{prefix}n_{node.uid}"
        node_vars[node.uid] = node_var
        node_id = _make_node_id(qualified_name, node.uid)

        if node.node_type == "iUse":
            subvi_name = node.name or "Unknown SubVI"
            lines.append(
                f'CREATE ({node_var}:SubVI {{id: "{node_id}", '
                f'name: "{_escape(subvi_name)}"}})'
            )
        elif node.node_type == "prim":
            lines.append(
                f'CREATE ({node_var}:Primitive {{id: "{node_id}", '
                f'primResID: {node.prim_res_id}}})'
            )
        elif node.node_type in ("whileLoop", "forLoop"):
            lines.append(
                f'CREATE ({node_var}:Loop {{id: "{node_id}", '
                f'type: "{node.node_type}"}})'
            )
        elif node.node_type in ("select", "caseStruct"):
            lines.append(
                f'CREATE ({node_var}:Conditional {{id: "{node_id}", '
                f'type: "{node.node_type}"}})'
            )
        else:
            lines.append(
                f'CREATE ({node_var}:Node {{id: "{node_id}", '
                f'type: "{node.node_type}"}})'
            )
        lines.append(f"CREATE ({vi_var})-[:CONTAINS]->({node_var})")

    return lines


def _generate_terminals_fallback(
    bd: BlockDiagram,
    bd_xml_path: Path,
    vi_var: str,
    node_vars: dict[str, str],
    prefix: str,
    qualified_name: str,
) -> list[str]:
    """Generate Cypher statements for terminals when no front panel XML exists."""
    if not bd.fp_terminals:
        return []

    fp_names = _get_fp_terminal_names(bd_xml_path)
    lines = ["", "// === VI Terminals (no front panel XML) ==="]

    for i, fp_term in enumerate(bd.fp_terminals):
        term_var = f"{prefix}fp_{fp_term.uid}"
        node_vars[fp_term.uid] = term_var
        node_id = _make_node_id(qualified_name, fp_term.uid)
        name = fp_names.get(fp_term.fp_dco_uid) or fp_term.name or f"terminal_{i}"

        if fp_term.is_indicator:
            lines.append(
                f'CREATE ({term_var}:Output {{id: "{node_id}", '
                f'name: "{_escape(name)}"}})'
            )
            lines.append(f"CREATE ({vi_var})-[:RETURNS]->({term_var})")
        else:
            lines.append(
                f'CREATE ({term_var}:Input {{id: "{node_id}", '
                f'name: "{_escape(name)}"}})'
            )
            lines.append(f"CREATE ({term_var})-[:PARAMETER_OF]->({vi_var})")

    return lines


def _generate_dataflow(
    bd: BlockDiagram,
    node_vars: dict[str, str],
) -> list[str]:
    """Generate Cypher statements for data flow edges."""
    lines = ["", "// === Data Flow ==="]

    for wire in bd.wires:
        from_parent = bd.term_to_parent.get(wire.from_term, wire.from_term)
        to_parent = bd.term_to_parent.get(wire.to_term, wire.to_term)

        from_var = node_vars.get(from_parent)
        to_var = node_vars.get(to_parent)

        if from_var and to_var:
            lines.append(f"CREATE ({from_var})-[:FLOWS_TO]->({to_var})")

    return lines


def _expand_subvis(
    bd: BlockDiagram,
    node_vars: dict[str, str],
    prefix: str,
    _processed: set[str],
    _search_paths: list[Path],
) -> list[str]:
    """Generate Cypher statements for expanded SubVI definitions."""
    subvi_names = [
        (node.uid, node.name)
        for node in bd.nodes
        if node.node_type == "iUse" and node.name
    ]

    if not subvi_names:
        return []

    lines = ["", "// === SubVI Definitions ==="]

    for node_uid, subvi_name in subvi_names:
        subvi_path = _find_subvi(subvi_name, _search_paths)

        if subvi_path:
            node_var = node_vars.get(node_uid, f"{prefix}n_{node_uid}")
            subvi_var = _sanitize_name(
                subvi_name.replace(".vi", "").replace(".VI", "")
            )

            lines.append("")
            lines.append(f"// --- SubVI: {subvi_name} ---")

            try:
                subvi_bd_xml, subvi_fp_xml, subvi_main_xml = extract_vi_xml(
                    subvi_path,
                    output_dir=subvi_path.parent,
                )

                subvi_graph = from_vi(
                    subvi_bd_xml,
                    fp_xml_path=subvi_fp_xml,
                    main_xml_path=subvi_main_xml,
                    expand_subvis=True,
                    _processed=_processed,
                    _search_paths=_search_paths,
                )

                lines.append(subvi_graph)
                lines.append(f"CREATE ({node_var})-[:CALLS]->({subvi_var})")

            except (RuntimeError, ET.ParseError, OSError) as e:
                lines.append(f"// Failed to extract SubVI: {subvi_name} - {e}")
        else:
            lines.append(f"// SubVI not found: {subvi_name}")

    return lines


def from_vi(
    bd_xml_path: Path | str,
    fp_xml_path: Path | str | None = None,
    main_xml_path: Path | str | None = None,
    expand_subvis: bool = False,
    qualified_name: str | None = None,
    _processed: set[str] | None = None,
    _search_paths: list[Path] | None = None,
) -> str:
    """Generate a unified Cypher graph of both front panel and block diagram.

    This creates a richer graph that includes:
    - Front panel controls with full type information (clusters, arrays, etc.)
    - Block diagram operations and data flow
    - Connections between front panel and block diagram
    - Optionally: recursively expanded SubVIs

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)
        fp_xml_path: Path to the front panel XML (*_FPHb.xml), auto-detected if None
        main_xml_path: Path to the main VI XML (optional, for metadata)
        expand_subvis: If True, recursively expand SubVI definitions
        qualified_name: Qualified VI name for composite IDs (e.g., "MyLib.lvlib/MyVI.vi").
                        If None, defaults to the VI name from metadata or filename.
        _processed: Internal set to track processed VIs (cycle detection)
        _search_paths: Internal list of paths to search for SubVI files

    Returns:
        Cypher CREATE statements representing the complete VI
    """
    bd_xml_path = Path(bd_xml_path)

    # Initialize cycle detection set
    if _processed is None:
        _processed = set()

    # Build search paths for SubVIs
    if _search_paths is None:
        _search_paths = [bd_xml_path.parent]
        # Add parent directories up to 3 levels
        parent = bd_xml_path.parent.parent
        for _ in range(3):
            if parent.exists():
                _search_paths.append(parent)
                parent = parent.parent

    # Check for cycles
    vi_key = bd_xml_path.stem.replace("_BDHb", "")
    if vi_key in _processed:
        return f"// Already processed (cycle detected): {vi_key}"
    _processed.add(vi_key)

    # Auto-detect front panel XML if not provided
    if fp_xml_path is None:
        fp_xml_path = bd_xml_path.parent / bd_xml_path.name.replace("_BDHb.xml", "_FPHb.xml")
        if not fp_xml_path.exists():
            fp_xml_path = None
    else:
        fp_xml_path = Path(fp_xml_path)

    # Parse block diagram
    bd = parse_block_diagram(bd_xml_path)

    # Parse front panel if available
    fp = None
    if fp_xml_path and fp_xml_path.exists():
        fp = parse_front_panel(fp_xml_path, bd_xml_path)

    # Get VI name and qualified name from metadata
    vi_name = "Unknown VI"
    if main_xml_path:
        meta = parse_vi_metadata(main_xml_path)
        vi_name = meta.get("name", vi_name)
        # Get qualified_name from VI's own metadata (it knows its library)
        if qualified_name is None:
            qualified_name = meta.get("qualified_name", vi_name)
    else:
        vi_name = bd_xml_path.stem.replace("_BDHb", "")

    # Fall back to vi_name if no qualified_name available
    if qualified_name is None:
        qualified_name = vi_name

    vi_var = _sanitize_name(qualified_name)
    # Prefix for node variables to avoid collisions in hierarchy
    prefix = f"{vi_var}_" if len(_processed) > 1 else ""
    node_vars = {}

    lines = [
        "// Unified Cypher graph of LabVIEW VI (front panel + block diagram)",
        f"// VI: {qualified_name}",
        "",
        "// === VI Container ===",
        f'CREATE ({vi_var}:VI {{name: "{qualified_name}"}})',
    ]

    # Build mapping from front panel DCO uid to block diagram terminal uid
    fp_to_bd_terminal = {}
    for fp_term in bd.fp_terminals:
        fp_to_bd_terminal[fp_term.fp_dco_uid] = fp_term.uid

    # === FRONT PANEL SECTION ===
    if fp:
        inputs = [c for c in fp.controls if not c.is_indicator]
        outputs = [c for c in fp.controls if c.is_indicator]

        if inputs:
            lines.append("")
            lines.append("// === Front Panel Inputs (Controls) ===")
            for ctrl in inputs:
                _generate_control_nodes(lines, ctrl, vi_var, node_vars, is_input=True,
                                       qualified_name=qualified_name, prefix=prefix)
                # Also map the BD terminal uid to this control for data flow
                if ctrl.uid in fp_to_bd_terminal:
                    bd_term_uid = fp_to_bd_terminal[ctrl.uid]
                    node_vars[bd_term_uid] = node_vars[ctrl.uid]

        if outputs:
            lines.append("")
            lines.append("// === Front Panel Outputs (Indicators) ===")
            for ctrl in outputs:
                _generate_control_nodes(lines, ctrl, vi_var, node_vars, is_input=False,
                                       qualified_name=qualified_name, prefix=prefix)
                # Also map the BD terminal uid to this control for data flow
                if ctrl.uid in fp_to_bd_terminal:
                    bd_term_uid = fp_to_bd_terminal[ctrl.uid]
                    node_vars[bd_term_uid] = node_vars[ctrl.uid]

    # === BLOCK DIAGRAM SECTION ===
    lines.extend(_generate_constants(bd, vi_var, node_vars, prefix, qualified_name))
    lines.extend(_generate_operations(bd, vi_var, node_vars, prefix, qualified_name))

    # If we don't have front panel info, fall back to basic terminal representation
    if not fp:
        lines.extend(_generate_terminals_fallback(bd, bd_xml_path, vi_var, node_vars, prefix, qualified_name))

    # === DATA FLOW ===
    lines.extend(_generate_dataflow(bd, node_vars))

    # === SUBVI EXPANSION ===
    if expand_subvis:
        lines.extend(_expand_subvis(bd, node_vars, prefix, _processed, _search_paths))

    lines.append("")
    lines.append("// === End of VI Graph ===")

    return "\n".join(lines)


def extract_vi_xml(
    vi_path: Path | str,
    output_dir: Path | None = None,
) -> tuple[Path, Path | None, Path | None]:
    """Extract a VI file to XML using pylabview.

    Args:
        vi_path: Path to the .vi file
        output_dir: Directory for output files (default: temp directory)

    Returns:
        Tuple of (bd_xml_path, fp_xml_path, main_xml_path)
        fp_xml and main_xml may be None if not generated

    Raises:
        RuntimeError: If extraction fails
    """
    vi_path = Path(vi_path)

    if output_dir is None:
        output_dir = vi_path.parent

    result = subprocess.run(
        [sys.executable, "-m", "pylabview.readRSRC", "-i", str(vi_path), "-x"],
        capture_output=True,
        text=True,
        cwd=output_dir,
    )

    if result.returncode != 0:
        raise RuntimeError(f"pylabview extraction failed: {result.stderr}")

    vi_stem = vi_path.stem
    bd_xml = output_dir / f"{vi_stem}_BDHb.xml"
    fp_xml = output_dir / f"{vi_stem}_FPHb.xml"
    main_xml = output_dir / f"{vi_stem}.xml"

    if not bd_xml.exists():
        raise RuntimeError(f"Block diagram XML not found: {bd_xml}")

    return (
        bd_xml,
        fp_xml if fp_xml.exists() else None,
        main_xml if main_xml.exists() else None,
    )


def _find_subvi(subvi_name: str, search_paths: list[Path]) -> Path | None:
    """Find a SubVI file (.vi).

    Args:
        subvi_name: Name of the SubVI (e.g., "Calculate Test Coverage.vi" or
                    "Library.lvlib:SubVI.vi")
        search_paths: List of directories to search

    Returns:
        Path to the .vi file, or None if not found
    """
    # Handle qualified names like "Library.lvlib:SubVI.vi"
    # The library/class is just namespace info - VIs are sibling files, not in subdirectories
    if ":" in subvi_name:
        parts = subvi_name.split(":")
        vi_name = parts[-1]  # Just the VI name
    else:
        vi_name = subvi_name

    # Search for the .vi file
    for search_path in search_paths:
        # Direct match in search path
        candidate = search_path / vi_name
        if candidate.exists():
            return candidate

        # Recursive search through subdirectories
        try:
            for match in search_path.rglob(vi_name):
                return match
        except (PermissionError, OSError):
            continue

    return None


def _parse_qualified_name(name: str) -> tuple[str | None, str]:
    """Parse a qualified VI name into (library, vi_name).

    Args:
        name: Qualified name like "Library.lvlib:SubVI.vi" or just "SubVI.vi"

    Returns:
        Tuple of (library_name or None, vi_name)
    """
    if ":" in name:
        parts = name.split(":")
        return parts[0], parts[-1]
    return None, name


def _generate_control_nodes(
    lines: list[str],
    ctrl: FPControl,
    vi_var: str,
    node_vars: dict[str, str],
    is_input: bool,
    qualified_name: str,
    parent_var: str | None = None,
    prefix: str = "",
) -> str:
    """Generate Cypher nodes for a control/indicator, handling clusters recursively.

    Returns the variable name for this control.
    """
    ctrl_var = f"{prefix}ctrl_{ctrl.uid}" if ctrl.uid else f"{prefix}ctrl_{_sanitize_name(ctrl.name)}"
    node_vars[ctrl.uid] = ctrl_var
    node_id = _make_node_id(qualified_name, ctrl.uid)

    # Determine node label based on type
    base_label = "Input" if is_input else "Output"
    type_label = _control_type_to_label(ctrl.control_type)

    if ctrl.control_type == "stdClust" and ctrl.children:
        # Cluster with children
        lines.append(
            f'CREATE ({ctrl_var}:{base_label}:Cluster {{id: "{node_id}", '
            f'name: "{_escape(ctrl.name)}"}})'
        )

        # Generate child nodes
        for child in ctrl.children:
            child_var = _generate_control_nodes(
                lines, child, vi_var, node_vars, is_input, qualified_name,
                parent_var=ctrl_var, prefix=prefix
            )
            lines.append(f"CREATE ({ctrl_var})-[:CONTAINS]->({child_var})")
    else:
        # Simple control
        lines.append(
            f'CREATE ({ctrl_var}:{base_label}:{type_label} {{id: "{node_id}", '
            f'name: "{_escape(ctrl.name)}", type: "{ctrl.control_type}"}})'
        )

    # Link to parent (VI or parent cluster)
    if parent_var is None:
        if is_input:
            lines.append(f"CREATE ({ctrl_var})-[:PARAMETER_OF]->({vi_var})")
        else:
            lines.append(f"CREATE ({vi_var})-[:RETURNS]->({ctrl_var})")

    return ctrl_var


def _control_type_to_label(control_type: str) -> str:
    """Map LabVIEW control type to a Cypher label."""
    type_map = {
        "stdString": "String",
        "stdNum": "Numeric",
        "stdDBL": "Numeric",
        "stdI32": "Numeric",
        "stdI16": "Numeric",
        "stdU32": "Numeric",
        "stdBool": "Boolean",
        "stdPath": "Path",
        "stdEnum": "Enum",
        "stdRing": "Enum",
        "stdArray": "Array",
        "stdClust": "Cluster",
    }
    return type_map.get(control_type, "Control")


def create_prompt(cypher_graph: str, mode: str = "script") -> str:
    """Create an LLM prompt for Cypher graph input.

    Args:
        cypher_graph: Cypher graph from from_blockdiagram()
        mode: "script" for standalone, "gui" for backend function

    Returns:
        Complete prompt string for LLM
    """
    base_instructions = """The above is a Cypher graph representation of a LabVIEW VI.

Node types:
- :VI - The main VI container
- :Constant - Input values with 'python' property showing the Python equivalent
- :Primitive - LabVIEW built-in operations with 'python' property showing the equivalent
- :SubVI - Calls to other VIs (treat as function calls)
- :Input - VI input parameter (function argument)
- :Output - VI output/return value

Relationships:
- [:CONTAINS] - VI contains this node
- [:FLOWS_TO] - Data flows from source to destination
- [:RETURNS] - VI returns this output value
- [:PARAMETER_OF] - This input is a parameter of the VI

IMPORTANT: Pay attention to [:FLOWS_TO] edges going to :Output nodes - these indicate
what values should be returned. A single node's output can flow to BOTH other operations
AND to the :Output (return value).

Convert this graph to an equivalent Python function by:
1. Reading the 'python' property of each node for the Python equivalent
2. Following [:FLOWS_TO] edges to determine execution order
3. Using variable names that reflect the data being passed
4. Returning values that flow to :Output nodes"""

    if mode == "gui":
        return f"""{cypher_graph}

{base_instructions}
4. Create a backend function suitable for NiceGUI frontend
5. Return output values as a tuple

Output ONLY the Python code, no explanations."""

    return f"""{cypher_graph}

{base_instructions}

Output ONLY the Python code, no explanations."""


def _sanitize_name(name: str) -> str:
    """Convert a name to a valid Cypher variable name."""
    result = ""
    for c in name:
        if c.isalnum():
            result += c
        else:
            result += "_"
    # Ensure it starts with a letter
    if result and not result[0].isalpha():
        result = "vi_" + result
    return result.lower() or "vi"


def _escape(s: str) -> str:
    """Escape a string for use in Cypher."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def _make_node_id(qualified_name: str, local_id: str) -> str:
    """Create a globally unique node ID.

    Combines qualified VI name with local ID to avoid collisions when
    multiple VIs are loaded into the same graph.

    Args:
        qualified_name: Qualified name (e.g., "MyLib.lvlib/MyVI.vi" or "MyVI.vi")
        local_id: Local UID from the VI's XML

    Returns:
        Composite ID like "MyLib.lvlib/MyVI.vi:129"
    """
    return f"{qualified_name}:{local_id}"


def _build_qualified_name(vi_name: str, container: str | None) -> str:
    """Build qualified name using LabVIEW's colon syntax.

    Uses the same syntax as SubVI references so names match directly.

    Args:
        vi_name: VI name like "Helper.vi"
        container: Container name like "MyLibrary.lvlib" or None

    Returns:
        Qualified name like "MyLibrary.lvlib:Helper.vi" or just "Helper.vi"
    """
    if container:
        return f"{container}:{vi_name}"
    return vi_name


# === Higher-Level Discovery Functions ===


def from_directory(
    directory: Path | str,
    recursive: bool = True,
    expand_subvis: bool = True,
) -> str:
    """Generate Cypher graph for all VIs in a directory.

    Discovers all .vi files and generates a unified graph with all VIs
    and their dependencies.

    Args:
        directory: Directory to search for .vi files
        recursive: Search subdirectories
        expand_subvis: Expand SubVI definitions

    Returns:
        Cypher CREATE statements for all VIs
    """
    directory = Path(directory)

    # Find all .vi files
    pattern = "**/*.vi" if recursive else "*.vi"
    vi_files = list(directory.glob(pattern))

    if not vi_files:
        return "// No VI files found"

    return _generate_hierarchy_graph(vi_files, expand_subvis)


def from_lvlib(
    lvlib_path: Path | str,
    expand_subvis: bool = True,
) -> str:
    """Generate Cypher graph for all VIs in a LabVIEW library.

    The .lvlib file is parsed to find referenced VIs (they are sibling files).

    Args:
        lvlib_path: Path to the .lvlib file
        expand_subvis: Expand SubVI definitions

    Returns:
        Cypher CREATE statements for all VIs in the library
    """
    lvlib_path = Path(lvlib_path)

    if not lvlib_path.exists():
        return f"// Library not found: {lvlib_path}"

    # Parse the lvlib XML to find member VIs
    vi_files = _parse_lvlib_members(lvlib_path)

    if not vi_files:
        # Fall back to finding VIs in the same directory
        vi_files = list(lvlib_path.parent.glob("*.vi"))

    if not vi_files:
        return f"// No VI files found in library: {lvlib_path.name}"

    lines = [
        f"// LabVIEW Library: {lvlib_path.name}",
        f'CREATE (lib:Library {{name: "{lvlib_path.stem}", path: "{_escape(str(lvlib_path))}"}})',
        "",
    ]

    graph = _generate_hierarchy_graph(vi_files, expand_subvis)
    lines.append(graph)

    # Link VIs to the library
    lines.append("")
    lines.append("// Link VIs to library")
    for vi_file in vi_files:
        vi_var = _sanitize_name(vi_file.stem)
        lines.append(f"CREATE (lib)-[:CONTAINS]->({vi_var})")

    return "\n".join(lines)


def from_lvclass(
    lvclass_path: Path | str,
    expand_subvis: bool = True,
) -> str:
    """Generate Cypher graph for all VIs in a LabVIEW class.

    The .lvclass file is parsed to find methods and property VIs.

    Args:
        lvclass_path: Path to the .lvclass file
        expand_subvis: Expand SubVI definitions

    Returns:
        Cypher CREATE statements for all VIs in the class
    """
    lvclass_path = Path(lvclass_path)

    if not lvclass_path.exists():
        return f"// Class not found: {lvclass_path}"

    # Parse the lvclass XML to find member VIs
    vi_files = _parse_lvclass_members(lvclass_path)

    if not vi_files:
        # Fall back to finding VIs in the same directory
        vi_files = list(lvclass_path.parent.glob("*.vi"))

    if not vi_files:
        return f"// No VI files found in class: {lvclass_path.name}"

    lines = [
        f"// LabVIEW Class: {lvclass_path.name}",
        f'CREATE (cls:Class {{name: "{lvclass_path.stem}", path: "{_escape(str(lvclass_path))}"}})',
        "",
    ]

    graph = _generate_hierarchy_graph(vi_files, expand_subvis)
    lines.append(graph)

    # Link VIs to the class
    lines.append("")
    lines.append("// Link VIs to class")
    for vi_file in vi_files:
        vi_var = _sanitize_name(vi_file.stem)
        lines.append(f"CREATE (cls)-[:HAS_METHOD]->({vi_var})")

    return "\n".join(lines)


def from_project(
    lvproj_path: Path | str,
    expand_subvis: bool = True,
) -> str:
    """Generate Cypher graph for all VIs in a LabVIEW project.

    Args:
        lvproj_path: Path to the .lvproj file
        expand_subvis: Expand SubVI definitions

    Returns:
        Cypher CREATE statements for all VIs in the project
    """
    lvproj_path = Path(lvproj_path)

    if not lvproj_path.exists():
        return f"// Project not found: {lvproj_path}"

    # Parse the project file to find all VIs
    vi_files, libraries, classes = _parse_lvproj_members(lvproj_path)

    lines = [
        f"// LabVIEW Project: {lvproj_path.name}",
        f'CREATE (proj:Project {{name: "{lvproj_path.stem}", path: "{_escape(str(lvproj_path))}"}})',
        "",
    ]

    # Generate graphs for libraries
    for lib_path in libraries:
        lines.append(from_lvlib(lib_path, expand_subvis))
        lib_var = f"lib_{_sanitize_name(lib_path.stem)}"
        lines.append(f"CREATE (proj)-[:CONTAINS]->({lib_var})")
        lines.append("")

    # Generate graphs for classes
    for cls_path in classes:
        lines.append(from_lvclass(cls_path, expand_subvis))
        cls_var = f"cls_{_sanitize_name(cls_path.stem)}"
        lines.append(f"CREATE (proj)-[:CONTAINS]->({cls_var})")
        lines.append("")

    # Generate graphs for standalone VIs
    if vi_files:
        lines.append("// Standalone VIs")
        graph = _generate_hierarchy_graph(vi_files, expand_subvis)
        lines.append(graph)

        # Link VIs to project
        for vi_file in vi_files:
            vi_var = _sanitize_name(vi_file.stem)
            lines.append(f"CREATE (proj)-[:CONTAINS]->({vi_var})")

    return "\n".join(lines)


def _generate_hierarchy_graph(
    vi_files: list[Path],
    expand_subvis: bool = True,
) -> str:
    """Generate Cypher for a list of VI files.

    Processes VIs in dependency order (leaves first).
    """
    if not vi_files:
        return "// No VIs to process"

    # Build dependency graph first (without full expansion)
    dependencies: dict[str, set[str]] = {}
    vi_by_name: dict[str, Path] = {}

    for vi_path in vi_files:
        vi_name = vi_path.stem
        vi_by_name[vi_name] = vi_path
        dependencies[vi_name] = set()

        # Quick parse to find SubVI calls (without extracting)
        try:
            bd_xml, _, _ = extract_vi_xml(vi_path)
            subvis = _find_subvi_calls(bd_xml)
            for subvi_name in subvis:
                # Only track dependencies on VIs in our set
                clean_name = subvi_name.replace(".vi", "").replace(".VI", "")
                if ":" in clean_name:
                    clean_name = clean_name.split(":")[-1]
                if clean_name in vi_by_name or clean_name + ".vi" in [v.name for v in vi_files]:
                    dependencies[vi_name].add(clean_name)
        except (RuntimeError, ET.ParseError, OSError):
            pass  # Skip VIs that can't be parsed

    # Topological sort (leaves first)
    order = _topological_sort(dependencies)

    # Generate graphs in order
    processed: set[str] = set()
    search_paths = list({vi.parent for vi in vi_files})
    lines = []

    for vi_name in order:
        if vi_name not in vi_by_name:
            continue

        vi_path = vi_by_name[vi_name]
        try:
            bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)
            graph = from_vi(
                bd_xml,
                fp_xml_path=fp_xml,
                main_xml_path=main_xml,
                expand_subvis=expand_subvis,
                _processed=processed,
                _search_paths=search_paths,
            )
            lines.append(graph)
            lines.append("")
            processed.add(vi_name)
        except Exception as e:
            lines.append(f"// Failed to process {vi_name}: {e}")

    return "\n".join(lines)


def _find_subvi_calls(bd_xml_path: Path) -> list[str]:
    """Quick parse to find SubVI call names without full processing."""
    try:
        tree = ET.parse(bd_xml_path)
        root = tree.getroot()

        subvis = []
        for elem in root.iter():
            if elem.tag == "iUse":
                name = elem.get("name") or elem.get("viName")
                if name:
                    subvis.append(name)
        return subvis
    except (ET.ParseError, OSError):
        return []


def _topological_sort(dependencies: dict[str, set[str]]) -> list[str]:
    """Sort nodes so dependencies come before dependents."""
    result = []
    visited = set()
    temp_mark = set()

    def visit(node: str) -> None:
        if node in temp_mark:
            return  # Cycle detected, skip
        if node in visited:
            return

        temp_mark.add(node)

        for dep in dependencies.get(node, set()):
            visit(dep)

        temp_mark.remove(node)
        visited.add(node)
        result.append(node)

    for node in dependencies:
        if node not in visited:
            visit(node)

    return result


def _parse_lvlib_members(lvlib_path: Path) -> list[Path]:
    """Parse .lvlib XML to find member VI files."""
    try:
        tree = ET.parse(lvlib_path)
        root = tree.getroot()

        vi_files = []
        parent_dir = lvlib_path.parent

        # Look for Item elements with Type="VI"
        for item in root.iter("Item"):
            if item.get("Type") == "VI":
                vi_name = item.get("Name", "")
                if vi_name:
                    vi_path = parent_dir / vi_name
                    if vi_path.exists():
                        vi_files.append(vi_path)

        return vi_files
    except (ET.ParseError, OSError):
        return []


def _parse_lvclass_members(lvclass_path: Path) -> list[Path]:
    """Parse .lvclass XML to find member VI files."""
    try:
        tree = ET.parse(lvclass_path)
        root = tree.getroot()

        vi_files = []
        parent_dir = lvclass_path.parent

        # Look for Item elements with Type="VI" (methods)
        for item in root.iter("Item"):
            item_type = item.get("Type", "")
            if item_type in ("VI", "Method"):
                vi_name = item.get("Name", "")
                if vi_name:
                    vi_path = parent_dir / vi_name
                    if vi_path.exists():
                        vi_files.append(vi_path)

        return vi_files
    except (ET.ParseError, OSError):
        return []


def _parse_lvproj_members(lvproj_path: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Parse .lvproj XML to find VIs, libraries, and classes.

    Returns:
        Tuple of (vi_files, library_files, class_files)
    """
    try:
        tree = ET.parse(lvproj_path)
        root = tree.getroot()

        vi_files = []
        libraries = []
        classes = []
        parent_dir = lvproj_path.parent

        for item in root.iter("Item"):
            item_type = item.get("Type", "")
            name = item.get("Name", "")

            if not name:
                continue

            path = parent_dir / name

            if item_type == "VI" and path.exists():
                vi_files.append(path)
            elif item_type == "Library" and path.exists():
                libraries.append(path)
            elif item_type == "LVClass" and path.exists():
                classes.append(path)

        return vi_files, libraries, classes
    except (ET.ParseError, OSError):
        return [], [], []

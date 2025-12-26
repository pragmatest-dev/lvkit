"""Generate Cypher graph representation of LabVIEW VIs."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .parser import parse_block_diagram, parse_vi_metadata
from .blockdiagram import (
    PRIMITIVE_MAP,
    decode_constant,
    _build_terminal_map,
    _extract_enum_labels,
    _get_enum_value_label,
    _get_fp_terminal_names,
)
from .frontpanel import FPControl, parse_front_panel


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

    # Parse raw XML for additional context
    tree = ET.parse(bd_xml_path)
    root = tree.getroot()
    enum_labels = _extract_enum_labels(root)

    # Try to get front panel terminal names
    fp_names = _get_fp_terminal_names(bd_xml_path)

    # Get VI name
    vi_name = "Unknown VI"
    if main_xml_path:
        meta = parse_vi_metadata(main_xml_path)
        vi_name = meta.get("name", vi_name)
    else:
        vi_name = bd_xml_path.stem.replace("_BDHb", "")

    # Sanitize name for Cypher variable
    vi_var = _sanitize_name(vi_name)

    lines = [
        "// Cypher graph representation of LabVIEW VI",
        f"// VI: {vi_name}",
        "",
        f"// Create the VI node",
        f'CREATE ({vi_var}:VI {{name: "{vi_name}"}})',
        "",
        "// Constants",
    ]

    # Track node variables for edge creation
    node_vars = {}
    term_to_parent = _build_terminal_map(root)

    # Create constant nodes
    for const in bd.constants:
        const_var = f"c_{const.uid}"
        node_vars[const.uid] = const_var

        # Decode constant value
        enum_label = _get_enum_value_label(const, enum_labels)
        if enum_label:
            # Extract just the python hint if present
            value_desc = enum_label
            python_hint = ""
            if " -> Python: " in enum_label:
                parts = enum_label.split(" -> Python: ")
                value_desc = parts[0]
                python_hint = parts[1]
            lines.append(
                f'CREATE ({const_var}:Constant {{id: "{const.uid}", '
                f'value: "{_escape(value_desc)}", '
                f'python: "{_escape(python_hint)}"}})'
            )
        else:
            val_type, val = decode_constant(const)
            lines.append(
                f'CREATE ({const_var}:Constant {{id: "{const.uid}", '
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

        if node.node_type == "iUse":
            # SubVI call
            subvi_name = node.name or "Unknown SubVI"
            lines.append(
                f'CREATE ({node_var}:SubVI {{id: "{node.uid}", '
                f'name: "{_escape(subvi_name)}"}})'
            )
        elif node.node_type == "prim":
            # Primitive operation
            prim_info = PRIMITIVE_MAP.get(node.prim_res_id)
            if prim_info:
                name, desc, python_eq = prim_info
                lines.append(
                    f'CREATE ({node_var}:Primitive {{id: "{node.uid}", '
                    f'name: "{name}", '
                    f'description: "{_escape(desc)}", '
                    f'python: "{_escape(python_eq)}"}})'
                )
            else:
                lines.append(
                    f'CREATE ({node_var}:Primitive {{id: "{node.uid}", '
                    f'primResID: {node.prim_res_id}}})'
                )
        elif node.node_type in ("whileLoop", "forLoop"):
            lines.append(
                f'CREATE ({node_var}:Loop {{id: "{node.uid}", '
                f'type: "{node.node_type}"}})'
            )
        elif node.node_type in ("select", "caseStruct"):
            lines.append(
                f'CREATE ({node_var}:Conditional {{id: "{node.uid}", '
                f'type: "{node.node_type}"}})'
            )
        else:
            lines.append(
                f'CREATE ({node_var}:Node {{id: "{node.uid}", '
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

            # Try to get name from front panel, fall back to generic name
            name = fp_names.get(fp_term.fp_dco_uid) or fp_term.name
            if not name:
                name = f"output_{i}" if fp_term.is_indicator else f"input_{i}"

            if fp_term.is_indicator:
                # Output indicator
                lines.append(
                    f'CREATE ({term_var}:Output {{id: "{fp_term.uid}", '
                    f'name: "{_escape(name)}"}})'
                )
                lines.append(
                    f"CREATE ({vi_var})-[:RETURNS]->({term_var})"
                )
            else:
                # Input control
                lines.append(
                    f'CREATE ({term_var}:Input {{id: "{fp_term.uid}", '
                    f'name: "{_escape(name)}"}})'
                )
                lines.append(
                    f"CREATE ({term_var})-[:PARAMETER_OF]->({vi_var})"
                )

    lines.append("")
    lines.append("// Data flow edges")

    # Create edges for wires
    for wire in bd.wires:
        from_parent = term_to_parent.get(wire.from_term, wire.from_term)
        to_parent = term_to_parent.get(wire.to_term, wire.to_term)

        from_var = node_vars.get(from_parent)
        to_var = node_vars.get(to_parent)

        if from_var and to_var:
            lines.append(
                f"CREATE ({from_var})-[:FLOWS_TO]->({to_var})"
            )

    lines.append("")
    lines.append("// End of VI graph")

    return "\n".join(lines)


def from_vi(
    bd_xml_path: Path | str,
    fp_xml_path: Path | str | None = None,
    main_xml_path: Path | str | None = None,
) -> str:
    """Generate a unified Cypher graph of both front panel and block diagram.

    This creates a richer graph that includes:
    - Front panel controls with full type information (clusters, arrays, etc.)
    - Block diagram operations and data flow
    - Connections between front panel and block diagram

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)
        fp_xml_path: Path to the front panel XML (*_FPHb.xml), auto-detected if None
        main_xml_path: Path to the main VI XML (optional, for metadata)

    Returns:
        Cypher CREATE statements representing the complete VI
    """
    bd_xml_path = Path(bd_xml_path)

    # Auto-detect front panel XML if not provided
    if fp_xml_path is None:
        fp_xml_path = bd_xml_path.parent / bd_xml_path.name.replace("_BDHb.xml", "_FPHb.xml")
        if not fp_xml_path.exists():
            fp_xml_path = None
    else:
        fp_xml_path = Path(fp_xml_path)

    # Parse block diagram
    bd = parse_block_diagram(bd_xml_path)
    tree = ET.parse(bd_xml_path)
    root = tree.getroot()
    enum_labels = _extract_enum_labels(root)
    term_to_parent = _build_terminal_map(root)

    # Parse front panel if available
    fp = None
    if fp_xml_path and fp_xml_path.exists():
        fp = parse_front_panel(fp_xml_path, bd_xml_path)

    # Get VI name
    vi_name = "Unknown VI"
    if main_xml_path:
        meta = parse_vi_metadata(main_xml_path)
        vi_name = meta.get("name", vi_name)
    else:
        vi_name = bd_xml_path.stem.replace("_BDHb", "")

    vi_var = _sanitize_name(vi_name)
    node_vars = {}

    lines = [
        "// Unified Cypher graph of LabVIEW VI (front panel + block diagram)",
        f"// VI: {vi_name}",
        "",
        "// === VI Container ===",
        f'CREATE ({vi_var}:VI {{name: "{vi_name}"}})',
    ]

    # === FRONT PANEL SECTION ===
    if fp:
        inputs = [c for c in fp.controls if not c.is_indicator]
        outputs = [c for c in fp.controls if c.is_indicator]

        if inputs:
            lines.append("")
            lines.append("// === Front Panel Inputs (Controls) ===")
            for ctrl in inputs:
                _generate_control_nodes(lines, ctrl, vi_var, node_vars, is_input=True)

        if outputs:
            lines.append("")
            lines.append("// === Front Panel Outputs (Indicators) ===")
            for ctrl in outputs:
                _generate_control_nodes(lines, ctrl, vi_var, node_vars, is_input=False)

    # === BLOCK DIAGRAM SECTION ===
    lines.append("")
    lines.append("// === Block Diagram Constants ===")

    for const in bd.constants:
        const_var = f"c_{const.uid}"
        node_vars[const.uid] = const_var

        enum_label = _get_enum_value_label(const, enum_labels)
        if enum_label:
            value_desc = enum_label
            python_hint = ""
            if " -> Python: " in enum_label:
                parts = enum_label.split(" -> Python: ")
                value_desc = parts[0]
                python_hint = parts[1]
            lines.append(
                f'CREATE ({const_var}:Constant {{id: "{const.uid}", '
                f'value: "{_escape(value_desc)}", python: "{_escape(python_hint)}"}})'
            )
        else:
            val_type, val = decode_constant(const)
            lines.append(
                f'CREATE ({const_var}:Constant {{id: "{const.uid}", '
                f'type: "{val_type}", value: "{_escape(val)}"}})'
            )
        lines.append(f"CREATE ({vi_var})-[:CONTAINS]->({const_var})")

    lines.append("")
    lines.append("// === Block Diagram Operations ===")

    for node in bd.nodes:
        node_var = f"n_{node.uid}"
        node_vars[node.uid] = node_var

        if node.node_type == "iUse":
            subvi_name = node.name or "Unknown SubVI"
            lines.append(
                f'CREATE ({node_var}:SubVI {{id: "{node.uid}", '
                f'name: "{_escape(subvi_name)}"}})'
            )
        elif node.node_type == "prim":
            prim_info = PRIMITIVE_MAP.get(node.prim_res_id)
            if prim_info:
                name, desc, python_eq = prim_info
                lines.append(
                    f'CREATE ({node_var}:Primitive {{id: "{node.uid}", '
                    f'name: "{name}", description: "{_escape(desc)}", '
                    f'python: "{_escape(python_eq)}"}})'
                )
            else:
                lines.append(
                    f'CREATE ({node_var}:Primitive {{id: "{node.uid}", '
                    f'primResID: {node.prim_res_id}}})'
                )
        elif node.node_type in ("whileLoop", "forLoop"):
            lines.append(
                f'CREATE ({node_var}:Loop {{id: "{node.uid}", type: "{node.node_type}"}})'
            )
        elif node.node_type in ("select", "caseStruct"):
            lines.append(
                f'CREATE ({node_var}:Conditional {{id: "{node.uid}", type: "{node.node_type}"}})'
            )
        else:
            lines.append(
                f'CREATE ({node_var}:Node {{id: "{node.uid}", type: "{node.node_type}"}})'
            )
        lines.append(f"CREATE ({vi_var})-[:CONTAINS]->({node_var})")

    # If we don't have front panel info, fall back to basic terminal representation
    if not fp and bd.fp_terminals:
        fp_names = _get_fp_terminal_names(bd_xml_path)
        lines.append("")
        lines.append("// === VI Terminals (no front panel XML) ===")
        for i, fp_term in enumerate(bd.fp_terminals):
            term_var = f"fp_{fp_term.uid}"
            node_vars[fp_term.uid] = term_var
            name = fp_names.get(fp_term.fp_dco_uid) or fp_term.name or f"terminal_{i}"

            if fp_term.is_indicator:
                lines.append(
                    f'CREATE ({term_var}:Output {{id: "{fp_term.uid}", name: "{_escape(name)}"}})'
                )
                lines.append(f"CREATE ({vi_var})-[:RETURNS]->({term_var})")
            else:
                lines.append(
                    f'CREATE ({term_var}:Input {{id: "{fp_term.uid}", name: "{_escape(name)}"}})'
                )
                lines.append(f"CREATE ({term_var})-[:PARAMETER_OF]->({vi_var})")

    # === DATA FLOW ===
    lines.append("")
    lines.append("// === Data Flow ===")

    for wire in bd.wires:
        from_parent = term_to_parent.get(wire.from_term, wire.from_term)
        to_parent = term_to_parent.get(wire.to_term, wire.to_term)

        from_var = node_vars.get(from_parent)
        to_var = node_vars.get(to_parent)

        if from_var and to_var:
            lines.append(f"CREATE ({from_var})-[:FLOWS_TO]->({to_var})")

    lines.append("")
    lines.append("// === End of VI Graph ===")

    return "\n".join(lines)


def _generate_control_nodes(
    lines: list[str],
    ctrl: FPControl,
    vi_var: str,
    node_vars: dict[str, str],
    is_input: bool,
    parent_var: str | None = None,
) -> str:
    """Generate Cypher nodes for a control/indicator, handling clusters recursively.

    Returns the variable name for this control.
    """
    ctrl_var = f"ctrl_{ctrl.uid}" if ctrl.uid else f"ctrl_{_sanitize_name(ctrl.name)}"
    node_vars[ctrl.uid] = ctrl_var

    # Determine node label based on type
    base_label = "Input" if is_input else "Output"
    type_label = _control_type_to_label(ctrl.control_type)

    if ctrl.control_type == "stdClust" and ctrl.children:
        # Cluster with children
        lines.append(
            f'CREATE ({ctrl_var}:{base_label}:Cluster {{id: "{ctrl.uid}", '
            f'name: "{_escape(ctrl.name)}"}})'
        )

        # Generate child nodes
        for child in ctrl.children:
            child_var = _generate_control_nodes(
                lines, child, vi_var, node_vars, is_input, parent_var=ctrl_var
            )
            lines.append(f"CREATE ({ctrl_var})-[:CONTAINS]->({child_var})")
    else:
        # Simple control
        lines.append(
            f'CREATE ({ctrl_var}:{base_label}:{type_label} {{id: "{ctrl.uid}", '
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

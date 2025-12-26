"""Generate Cypher graph representation of LabVIEW block diagrams."""

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

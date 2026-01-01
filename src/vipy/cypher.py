"""Generate Cypher graph representation of LabVIEW VIs.

.. deprecated::
    This module is deprecated. Use vipy.memory_graph for in-memory graph
    operations instead. Neo4j support may be removed in a future version.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "vipy.cypher is deprecated. Use vipy.memory_graph for graph operations. "
    "Neo4j support may be removed in a future version.",
    DeprecationWarning,
    stacklevel=2,
)

import xml.etree.ElementTree as ET
from pathlib import Path

from .blockdiagram import (
    _get_enum_value_label,
    _get_fp_terminal_names,
    decode_constant,
)
from .extractor import extract_vi_xml
from .frontpanel import FPControl, parse_front_panel
from .parser import (
    BlockDiagram,
    ConnectorPane,
    SubVIPathRef,
    parse_block_diagram,
    parse_connector_pane,
    parse_subvi_paths,
    parse_type_map,
    parse_vi_metadata,
    resolve_type,
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

    # Try to get front panel terminal names
    fp_names = _get_fp_terminal_names(bd_xml_path)

    # Get VI name, qualified name, and type mappings from metadata
    vi_name = "Unknown VI"
    qualified_name = None
    type_map: dict[int, str] = {}
    if main_xml_path:
        meta = parse_vi_metadata(main_xml_path)
        vi_name = meta.get("name", vi_name)
        qualified_name = meta.get("qualified_name", vi_name)
        # Parse type mappings to resolve TypeID references
        type_map = parse_type_map(main_xml_path)
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
        f'MERGE ({vi_var}:VI {{name: "{qualified_name}"}})',
        "",
        "// Constants",
    ]

    # Track node variables for edge creation
    node_vars: dict[str, str] = {}

    # Create constant nodes (using shared helper for DRY)
    for const in bd.constants:
        const_var = f"c_{const.uid}"
        node_vars[const.uid] = const_var
        node_id = _make_node_id(qualified_name, const.uid)

        lines.append(_make_constant_cypher(const, const_var, node_id, bd.enum_labels))
        lines.append(f"CREATE ({vi_var})-[:CONTAINS]->({const_var})")

    lines.append("")
    lines.append("// Operations (Primitives and SubVIs)")

    # Create operation nodes (using shared helper for DRY)
    for node in bd.nodes:
        node_var = f"n_{node.uid}"
        node_vars[node.uid] = node_var
        node_id = _make_node_id(qualified_name, node.uid)

        lines.append(_make_operation_cypher(node, node_var, node_id, type_map))

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

    # Create edges for wires using terminal_info
    for wire in bd.wires:
        from_parent = bd.get_parent_uid(wire.from_term) or wire.from_term
        to_parent = bd.get_parent_uid(wire.to_term) or wire.to_term

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


def _make_operation_cypher(
    node: Node,
    node_var: str,
    node_id: str,
    type_map: dict[int, str] | None = None,
) -> str:
    """Generate Cypher CREATE statement for a single operation node.

    Stores ALL parser data: type, name, primResID, input/output types.
    Types are resolved using type_map if provided.
    """
    type_map = type_map or {}

    def _resolve_types(types: list[str]) -> list[str]:
        """Resolve a list of type references."""
        return [resolve_type(t, type_map) for t in types]

    # Map node types to descriptive names
    TYPE_NAMES = {
        "select": "Select",
        "caseStruct": "Case Structure",
        "whileLoop": "While Loop",
        "forLoop": "For Loop",
    }

    if node.node_type == "iUse":
        # SubVI call
        subvi_name = node.name or "Unknown SubVI"
        props = [
            f'id: "{node_id}"',
            f'name: "{_escape(subvi_name)}"',
        ]
        label = "SubVI"
    elif node.node_type == "prim":
        # Primitive - store ID and resolved terminal types for LLM context
        prim_name = f"Primitive_{node.prim_res_id}"
        props = [
            f'id: "{node_id}"',
            f'name: "{prim_name}"',
            f'primResID: {node.prim_res_id}',
        ]
        if node.input_types:
            resolved = _resolve_types(node.input_types)
            props.append(f'inputTypes: {resolved!r}')
        if node.output_types:
            resolved = _resolve_types(node.output_types)
            props.append(f'outputTypes: {resolved!r}')
        label = "Primitive"
    elif node.node_type in ("whileLoop", "forLoop"):
        name = TYPE_NAMES.get(node.node_type, node.node_type)
        props = [
            f'id: "{node_id}"',
            f'name: "{name}"',
            f'type: "{node.node_type}"',
        ]
        label = "Loop"
    elif node.node_type in ("select", "caseStruct"):
        name = TYPE_NAMES.get(node.node_type, node.node_type)
        props = [
            f'id: "{node_id}"',
            f'name: "{name}"',
            f'type: "{node.node_type}"',
        ]
        if node.input_types:
            resolved = _resolve_types(node.input_types)
            props.append(f'inputTypes: {resolved!r}')
        if node.output_types:
            resolved = _resolve_types(node.output_types)
            props.append(f'outputTypes: {resolved!r}')
        label = "Conditional"
    else:
        props = [
            f'id: "{node_id}"',
            f'type: "{node.node_type}"',
        ]
        if node.name:
            props.append(f'name: "{_escape(node.name)}"')
        label = "Node"

    return f'CREATE ({node_var}:{label} {{{", ".join(props)}}})'


def _make_constant_cypher(
    const: Constant,
    const_var: str,
    node_id: str,
    enum_labels: dict[str, str] | None = None,
) -> str:
    """Generate Cypher CREATE statement for a single constant.

    Stores ALL parser data: type, decoded value, type_desc, label.
    """
    enum_label = _get_enum_value_label(const, enum_labels or {})

    if enum_label:
        value_desc = enum_label
        python_hint = ""
        if " -> Python: " in enum_label:
            parts = enum_label.split(" -> Python: ")
            value_desc = parts[0]
            python_hint = parts[1]
        props = [
            f'id: "{node_id}"',
            f'value: "{_escape(value_desc)}"',
            f'python: "{_escape(python_hint)}"',
            f'type_desc: "{_escape(const.type_desc or "")}"',
        ]
    else:
        val_type, decoded_val = decode_constant(const)
        props = [
            f'id: "{node_id}"',
            f'type: "{val_type}"',
            f'value: "{_escape(decoded_val)}"',
            f'type_desc: "{_escape(const.type_desc or "")}"',
        ]

    if const.label:
        props.append(f'label: "{_escape(const.label)}"')

    return f'CREATE ({const_var}:Constant {{{", ".join(props)}}})'


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

        lines.append(_make_constant_cypher(const, const_var, node_id, bd.enum_labels))
        lines.append(f"CREATE ({vi_var})-[:CONTAINS]->({const_var})")

    return lines


def _generate_operations(
    bd: BlockDiagram,
    vi_var: str,
    node_vars: dict[str, str],
    prefix: str,
    qualified_name: str,
    type_map: dict[int, str] | None = None,
) -> list[str]:
    """Generate Cypher statements for block diagram operations."""
    lines = ["", "// === Block Diagram Operations ==="]

    for node in bd.nodes:
        node_var = f"{prefix}n_{node.uid}"
        node_vars[node.uid] = node_var
        node_id = _make_node_id(qualified_name, node.uid)

        lines.append(_make_operation_cypher(node, node_var, node_id, type_map))
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


def _generate_terminal_nodes(
    bd: BlockDiagram,
    node_vars: dict[str, str],
    term_vars: dict[str, str],
    prefix: str,
    qualified_name: str,
    type_map: dict[int, str] | None = None,
) -> list[str]:
    """Generate Terminal nodes and HAS_TERMINAL edges.

    Creates a Terminal node for each terminal in the block diagram,
    and links it to its parent node (Primitive, SubVI, Constant, etc.).

    Args:
        bd: Parsed block diagram
        node_vars: Mapping of node UIDs to Cypher variable names
        term_vars: Output mapping of terminal UIDs to Cypher variable names
        prefix: Prefix for variable names
        qualified_name: Qualified VI name for composite IDs
        type_map: Mapping of TypeID -> type name for resolution

    Returns:
        List of Cypher CREATE statements
    """
    type_map = type_map or {}
    lines = ["", "// === Terminals ==="]

    for term_uid, info in bd.terminal_info.items():
        term_var = f"{prefix}t_{term_uid}"
        term_vars[term_uid] = term_var
        term_id = _make_node_id(qualified_name, term_uid)

        # Terminal label: Input or Output
        direction = "Output" if info.is_output else "Input"
        # Resolve type references
        type_str = resolve_type(info.type_id, type_map) if info.type_id else "unknown"

        name_prop = f', name: "{info.name}"' if info.name else ""
        lines.append(
            f'CREATE ({term_var}:Terminal:{direction} {{'
            f'id: "{term_id}", index: {info.index}, type: "{type_str}"{name_prop}}})'
        )

        # Link terminal to its parent node
        parent_var = node_vars.get(info.parent_uid)
        if parent_var:
            lines.append(f"CREATE ({parent_var})-[:HAS_TERMINAL]->({term_var})")

    return lines


def _generate_dataflow(
    bd: BlockDiagram,
    term_vars: dict[str, str],
) -> list[str]:
    """Generate CONNECTS_TO edges between terminals.

    Wires in LabVIEW connect terminals. This creates edges directly
    between terminal nodes, representing the true data flow.

    Args:
        bd: Parsed block diagram
        term_vars: Mapping of terminal UIDs to Cypher variable names

    Returns:
        List of Cypher CREATE statements
    """
    lines = ["", "// === Data Flow (Terminal Connections) ==="]

    for wire in bd.wires:
        from_var = term_vars.get(wire.from_term)
        to_var = term_vars.get(wire.to_term)

        if from_var and to_var:
            lines.append(f"CREATE ({from_var})-[:CONNECTS_TO]->({to_var})")
        else:
            # Debug: show missing terminals
            if not from_var:
                lines.append(f"// Missing terminal: {wire.from_term}")
            if not to_var:
                lines.append(f"// Missing terminal: {wire.to_term}")

    return lines


def _generate_connector_pane(
    connector_pane: ConnectorPane,
    vi_var: str,
    node_vars: dict[str, str],
    fp_to_bd_terminal: dict[str, str],
    prefix: str,
    qualified_name: str,
) -> list[str]:
    """Generate ConnectorPane node and EXPOSES relationships.

    The connector pane defines which front panel controls/indicators are
    exposed as VI interface terminals (function parameters/returns).

    Args:
        connector_pane: Parsed connector pane from FP XML
        vi_var: Cypher variable name for the VI node
        node_vars: Mapping of node UIDs to Cypher variable names
        fp_to_bd_terminal: Mapping from FP DCO uid to BD terminal uid
        prefix: Prefix for variable names
        qualified_name: Qualified VI name for composite IDs

    Returns:
        List of Cypher CREATE statements
    """
    lines = ["", "// === Connector Pane (VI Interface) ==="]

    cp_var = f"{prefix}cp"
    cp_id = _make_node_id(qualified_name, "connectorPane")

    lines.append(
        f'CREATE ({cp_var}:ConnectorPane {{id: "{cp_id}", '
        f'pattern: {connector_pane.pattern_id}}})'
    )
    lines.append(f"CREATE ({vi_var})-[:HAS_CONNECTOR_PANE]->({cp_var})")

    # Create EXPOSES relationships for each slot
    for slot in connector_pane.slots:
        if not slot.fp_dco_uid:
            continue

        # Find the node variable for this front panel control
        # First check if it's directly in node_vars (from FP parsing)
        ctrl_var = node_vars.get(slot.fp_dco_uid)

        if ctrl_var:
            lines.append(
                f"CREATE ({cp_var})-[:EXPOSES {{slot: {slot.index}}}]->({ctrl_var})"
            )
        else:
            # The FP DCO might not be in node_vars if FP parsing didn't happen
            # In that case, try to link via the BD terminal
            bd_term_uid = fp_to_bd_terminal.get(slot.fp_dco_uid)
            if bd_term_uid and bd_term_uid in node_vars:
                term_var = node_vars[bd_term_uid]
                lines.append(
                    f"CREATE ({cp_var})-[:EXPOSES {{slot: {slot.index}}}]->({term_var})"
                )
            else:
                lines.append(f"// Slot {slot.index}: FP control {slot.fp_dco_uid} not found in graph")

    return lines


def _expand_subvis(
    bd: BlockDiagram,
    node_vars: dict[str, str],
    prefix: str,
    _processed: set[str],
    _search_paths: list[Path],
    type_map: dict[int, str] | None = None,
    path_hints: dict[str, SubVIPathRef] | None = None,
    _created_stubs: set[str] | None = None,
) -> list[str]:
    """Generate Cypher statements for expanded SubVI definitions."""
    subvi_names = [
        (node.uid, node.name)
        for node in bd.nodes
        if node.node_type == "iUse" and node.name
    ]

    if not subvi_names:
        return []

    type_map = type_map or {}
    if _created_stubs is None:
        _created_stubs = set()
    lines = ["", "// === SubVI Definitions ==="]

    for node_uid, subvi_name in subvi_names:
        subvi_path = _find_subvi(subvi_name, _search_paths, path_hints)

        if subvi_path:
            node_var = node_vars.get(node_uid, f"{prefix}n_{node_uid}")
            # Variable name must match what from_vi creates (uses qualified_name)
            subvi_var = _sanitize_name(subvi_name)

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
                    _created_stubs=_created_stubs,
                )

                lines.append(subvi_graph)
                lines.append(f"CREATE ({node_var})-[:CALLS]->({subvi_var})")

            except (RuntimeError, ET.ParseError, OSError) as e:
                lines.append(f"// Failed to extract SubVI: {subvi_name} - {e}")
                # Create stub for failed extraction
                lines.extend(
                    _create_stub_vi(subvi_name, node_uid, node_vars, prefix, bd, type_map, _created_stubs)
                )
        else:
            # SubVI not found - create a stub VI node
            lines.extend(
                _create_stub_vi(subvi_name, node_uid, node_vars, prefix, bd, type_map, _created_stubs)
            )

    return lines


def _create_stub_vi(
    subvi_name: str,
    node_uid: str,
    node_vars: dict[str, str],
    prefix: str,
    bd: BlockDiagram,
    type_map: dict[int, str] | None = None,
    _created_stubs: set[str] | None = None,
) -> list[str]:
    """Create a stub VI node for a missing SubVI.

    The stub captures terminal types from the call site so the LLM
    can generate a NotImplementedError stub with the correct signature.
    """
    lines = []
    subvi_var = _sanitize_name(subvi_name)
    node_var = node_vars.get(node_uid, f"{prefix}n_{node_uid}")
    type_map = type_map or {}
    if _created_stubs is None:
        _created_stubs = set()

    # Collect terminal types from the SubVI node in the calling VI
    # Filter out Void (unwired) terminals
    input_types: list[str] = []
    output_types: list[str] = []

    for term_uid, info in bd.terminal_info.items():
        if info.parent_uid == node_uid:
            # Resolve type using type_map
            term_type = resolve_type(info.type_id, type_map) if info.type_id else "Any"
            # Skip Void (unwired) terminals
            if term_type == "Void":
                continue
            if info.is_output:
                output_types.append(term_type)
            else:
                input_types.append(term_type)

    # Create the stub VI node (MERGE to dedupe by name)
    lines.append(f"// Stub VI: {subvi_name} (not found)")
    lines.append(
        f'MERGE ({subvi_var}:VI:Stub {{'
        f'name: "{subvi_name}"'
        f'}}) '
        f'ON CREATE SET {subvi_var}.is_stub = true, '
        f'{subvi_var}.input_types = {input_types!r}, '
        f'{subvi_var}.output_types = {output_types!r}'
    )
    lines.append(f"MERGE ({node_var})-[:CALLS]->({subvi_var})")

    return lines


def from_vi(
    bd_xml_path: Path | str,
    fp_xml_path: Path | str | None = None,
    main_xml_path: Path | str | None = None,
    expand_subvis: bool = False,
    qualified_name: str | None = None,
    _processed: set[str] | None = None,
    _search_paths: list[Path] | None = None,
    _created_stubs: set[str] | None = None,
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
    connector_pane = None
    if fp_xml_path and fp_xml_path.exists():
        fp = parse_front_panel(fp_xml_path, bd_xml_path)
        connector_pane = parse_connector_pane(fp_xml_path)

    # Get VI name, qualified name, type mappings, and SubVI path hints from metadata
    vi_name = "Unknown VI"
    type_map: dict[int, str] = {}
    path_hints: dict[str, SubVIPathRef] = {}
    if main_xml_path:
        meta = parse_vi_metadata(main_xml_path)
        vi_name = meta.get("name", vi_name)
        # Get qualified_name from VI's own metadata (it knows its library)
        if qualified_name is None:
            qualified_name = meta.get("qualified_name", vi_name)
        # Parse type mappings to resolve TypeID references
        type_map = parse_type_map(main_xml_path)
        # Parse SubVI path hints from LinkSavePathRef elements
        subvi_path_refs = parse_subvi_paths(main_xml_path)
        path_hints = {ref.name: ref for ref in subvi_path_refs}
    else:
        vi_name = bd_xml_path.stem.replace("_BDHb", "")

    # Fall back to vi_name if no qualified_name available
    if qualified_name is None:
        qualified_name = vi_name

    vi_var = _sanitize_name(qualified_name)
    # Prefix for node variables to avoid collisions in hierarchy
    prefix = f"{vi_var}_" if len(_processed) > 1 else ""
    node_vars: dict[str, str] = {}
    term_vars: dict[str, str] = {}  # Terminal UID -> Cypher variable

    lines = [
        "// Unified Cypher graph of LabVIEW VI (front panel + block diagram)",
        f"// VI: {qualified_name}",
        "",
        "// === VI Container ===",
        f'MERGE ({vi_var}:VI {{name: "{qualified_name}"}})',
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
    lines.extend(_generate_operations(bd, vi_var, node_vars, prefix, qualified_name, type_map))

    # If we don't have front panel info, fall back to basic terminal representation
    if not fp:
        lines.extend(_generate_terminals_fallback(bd, bd_xml_path, vi_var, node_vars, prefix, qualified_name))

    # === TERMINALS (graph-native representation) ===
    lines.extend(_generate_terminal_nodes(bd, node_vars, term_vars, prefix, qualified_name, type_map))

    # === DATA FLOW (terminal-to-terminal connections) ===
    lines.extend(_generate_dataflow(bd, term_vars))

    # === CONNECTOR PANE (VI interface) ===
    if connector_pane:
        lines.extend(_generate_connector_pane(
            connector_pane, vi_var, node_vars, fp_to_bd_terminal, prefix, qualified_name
        ))

    # === SUBVI EXPANSION ===
    if expand_subvis:
        if _created_stubs is None:
            _created_stubs = set()
        lines.extend(_expand_subvis(bd, node_vars, prefix, _processed, _search_paths, type_map, path_hints, _created_stubs))

    lines.append("")
    lines.append("// === End of VI Graph ===")

    return "\n".join(lines)


def _find_subvi(
    subvi_name: str,
    search_paths: list[Path],
    path_hints: dict[str, SubVIPathRef] | None = None,
) -> Path | None:
    """Find a SubVI file (.vi).

    Args:
        subvi_name: Name of the SubVI (e.g., "Calculate Test Coverage.vi" or
                    "Library.lvlib:SubVI.vi")
        search_paths: List of directories to search
        path_hints: Optional dict mapping SubVI names to path hints from XML

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

    # First, try using path hints if available
    if path_hints and subvi_name in path_hints:
        hint = path_hints[subvi_name]
        relative_path = hint.get_relative_path()

        for search_path in search_paths:
            # Try user.lib path (OpenG, etc.)
            if hint.is_userlib:
                candidate = search_path / "user.lib" / relative_path
                if candidate.exists():
                    return candidate
                # Also try without user.lib prefix (if search path is already user.lib)
                candidate = search_path / relative_path
                if candidate.exists():
                    return candidate

            # Try vi.lib path (LabVIEW built-ins)
            if hint.is_vilib:
                candidate = search_path / "vi.lib" / relative_path
                if candidate.exists():
                    return candidate
                # Also try without vi.lib prefix
                candidate = search_path / relative_path
                if candidate.exists():
                    return candidate

    # Fall back to standard search
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

    # Handle class wire (udClassDDO) specially - decode &#x00; to "self"
    name = ctrl.name
    if ctrl.control_type == "udClassDDO" and (not name or name == "&#x00;" or "\x00" in name):
        name = "self"

    # Build properties list with ALL available data
    props = [
        f'id: "{node_id}"',
        f'name: "{_escape(name)}"',
        f'type: "{ctrl.control_type}"',
    ]
    if ctrl.type_desc:
        props.append(f'type_desc: "{_escape(ctrl.type_desc)}"')
    if ctrl.default_value:
        props.append(f'default: "{_escape(str(ctrl.default_value))}"')
    if ctrl.enum_values:
        props.append(f'enum_values: {ctrl.enum_values!r}')

    if ctrl.control_type == "stdClust" and ctrl.children:
        # Cluster with children
        lines.append(f'CREATE ({ctrl_var}:{base_label}:Cluster {{{", ".join(props)}}})')

        # Generate child nodes
        for child in ctrl.children:
            child_var = _generate_control_nodes(
                lines, child, vi_var, node_vars, is_input, qualified_name,
                parent_var=ctrl_var, prefix=prefix
            )
            lines.append(f"CREATE ({ctrl_var})-[:CONTAINS]->({child_var})")
    else:
        # Simple control
        lines.append(f'CREATE ({ctrl_var}:{base_label}:{type_label} {{{", ".join(props)}}})')

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

"""VI analysis tools for MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..memory_graph import InMemoryVIGraph
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .schemas import (
    ControlSchema,
    GraphEdgeSchema,
    GraphNodeSchema,
    IndicatorSchema,
    VIAnalysisResult,
)


def analyze_vi(vi_path: str, search_paths: list[str] | None = None) -> VIAnalysisResult:
    """Analyze a VI and return structured data.

    Args:
        vi_path: Path to VI file (.vi) or block diagram XML (*_BDHb.xml)
        search_paths: Optional list of search paths for dependencies

    Returns:
        VIAnalysisResult with complete VI structure
    """
    # Load VI with all dependencies
    graph = InMemoryVIGraph()
    search_path_objs = [Path(p) for p in (search_paths or [])]

    vi_path_obj = Path(vi_path)
    if not vi_path_obj.exists():
        raise FileNotFoundError(f"VI file not found: {vi_path}")

    graph.load_vi(vi_path_obj, expand_subvis=True, search_paths=search_path_objs or None)

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

    # Get VI context
    vi_context = graph.get_vi_context(vi_name)

    # Extract controls with descriptions
    controls = []
    for inp in vi_context.get("inputs", []):
        controls.append(
            ControlSchema(
                name=inp.name or f"input_{inp.slot_index}",
                type=inp.type or "Any",
                default_value=inp.default_value,
                description=_infer_description(inp.name, inp.type, "input"),
                slot_index=inp.slot_index or 0,
            )
        )

    # Extract indicators with descriptions
    indicators = []
    for out in vi_context.get("outputs", []):
        indicators.append(
            IndicatorSchema(
                name=out.name or f"output_{out.slot_index}",
                type=out.type or "Any",
                description=_infer_description(out.name, out.type, "output"),
                slot_index=out.slot_index or 0,
            )
        )

    # Build graph structure
    graph_data = build_graph_structure(vi_name, graph)

    # Generate dependency descriptions
    dependencies = {}
    for op in vi_context.get("operations", []):
        if "SubVI" in op.labels and op.name:
            dep_name = op.name
            if dep_name not in dependencies:  # Avoid duplicates
                dependencies[dep_name] = generate_dependency_description(dep_name, graph)

    # Get execution order
    try:
        execution_order = graph.get_operation_order(vi_name)
    except Exception:
        # If execution order fails, just use empty list
        execution_order = []

    # Generate summary
    summary = _generate_vi_summary(vi_name, controls, indicators, dependencies)

    return VIAnalysisResult(
        vi_name=vi_name,
        summary=summary,
        controls=controls,
        indicators=indicators,
        graph=graph_data,
        dependencies=dependencies,
        execution_order=execution_order,
    )


def generate_dependency_description(subvi_name: str, graph: InMemoryVIGraph) -> str:
    """Generate 1-sentence description of what a SubVI does.

    Priority:
    1. vilib_resolver for known VIs
    2. Infer from VI name
    3. Infer from VI context (inputs/outputs)

    Args:
        subvi_name: Name of the SubVI
        graph: InMemoryVIGraph containing the VI

    Returns:
        One-sentence description
    """
    # First check vilib resolver
    try:
        resolver = get_vilib_resolver()
        vi = resolver.resolve_by_name(subvi_name)
        if vi and vi.description:
            return vi.description
    except Exception:
        pass

    # For stub VIs, infer from name
    if graph.is_stub_vi(subvi_name):
        return _infer_from_name(subvi_name)

    # For loaded VIs, try to infer from context
    try:
        vi_context = graph.get_vi_context(subvi_name)
        return _infer_from_context(subvi_name, vi_context)
    except Exception:
        return _infer_from_name(subvi_name)


def build_graph_structure(vi_name: str, graph: InMemoryVIGraph) -> dict[str, Any]:
    """Build graph nodes and edges for visualization.

    Args:
        vi_name: Name of the VI
        graph: InMemoryVIGraph containing the VI

    Returns:
        Dictionary with "nodes" and "edges" lists
    """
    nodes = []
    edges = []

    # Get VI context
    vi_context = graph.get_vi_context(vi_name)

    # Add input nodes (controls)
    for inp in vi_context.get("inputs", []):
        nodes.append(
            GraphNodeSchema(
                id=inp.id,
                label=f"Control: {inp.name or 'input'}",
                type="control",
                name=inp.name,
            ).model_dump()
        )

    # Add output nodes (indicators)
    for out in vi_context.get("outputs", []):
        nodes.append(
            GraphNodeSchema(
                id=out.id,
                label=f"Indicator: {out.name or 'output'}",
                type="indicator",
                name=out.name,
            ).model_dump()
        )

    # Add operation nodes
    for op in vi_context.get("operations", []):
        if "SubVI" in op.labels:
            label = f"SubVI: {op.name or 'Unknown'}"
            node_type = "subvi"
        elif "Primitive" in op.labels:
            prim_name = op.name or f"prim_{op.primResID}"
            label = f"Primitive: {prim_name}"
            node_type = "primitive"
        else:
            label = f"Operation: {op.name or op.id[:8]}"
            node_type = "operation"

        nodes.append(
            GraphNodeSchema(
                id=op.id,
                label=label,
                type=node_type,
                name=op.name,
                prim_id=op.primResID,
            ).model_dump()
        )

    # Add constant nodes
    for const in vi_context.get("constants", []):
        value_str = str(const.value) if const.value is not None else "None"
        # Truncate long values
        if len(value_str) > 30:
            value_str = value_str[:27] + "..."
        nodes.append(
            GraphNodeSchema(
                id=const.id,
                label=f"Constant: {value_str}",
                type="constant",
                name=value_str,
            ).model_dump()
        )

    # Add edges (wires)
    for wire in vi_context.get("data_flow", []):
        # Build human-readable labels
        from_label = _get_node_label(wire.from_parent_id, wire.from_parent_name, wire.from_parent_labels)
        to_label = _get_node_label(wire.to_parent_id, wire.to_parent_name, wire.to_parent_labels)

        edges.append(
            GraphEdgeSchema(
                from_node=wire.from_terminal_id,
                to_node=wire.to_terminal_id,
                from_label=from_label,
                to_label=to_label,
            ).model_dump()
        )

    return {
        "nodes": nodes,
        "edges": edges,
    }


def _infer_description(name: str | None, type_str: str | None, direction: str) -> str:
    """Infer description from name and type."""
    if not name:
        return f"{direction.capitalize()} parameter"
    type_part = f" ({type_str})" if type_str and type_str != "Any" else ""
    return f"{name}{type_part}"


def _generate_vi_summary(
    vi_name: str,
    controls: list[ControlSchema],
    indicators: list[IndicatorSchema],
    dependencies: dict[str, str],
) -> str:
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


def _infer_from_name(vi_name: str) -> str:
    """Infer description from VI name."""
    # Extract base name without path/extension
    name = Path(vi_name).stem
    if ":" in name:  # Handle qualified names
        name = name.split(":")[-1]

    name_lower = name.lower()

    # Common patterns
    if "get" in name_lower and "system" in name_lower and "directory" in name_lower:
        return "Retrieves OS-specific system directory paths"
    elif "build" in name_lower and "path" in name_lower:
        return "Constructs file path by combining components"
    elif "strip" in name_lower and "path" in name_lower:
        return "Extracts directory or filename from path"
    elif name_lower.startswith("get "):
        return f"Retrieves {name[4:].lower()} information"
    elif name_lower.startswith("set "):
        return f"Sets {name[4:].lower()} value"
    elif name_lower.startswith("build "):
        return f"Constructs {name[6:].lower()}"
    elif name_lower.startswith("create "):
        return f"Creates {name[7:].lower()}"
    elif "error" in name_lower:
        return f"Handles error {name.lower()}"

    return f"Performs {name.lower()} operation"


def _infer_from_context(vi_name: str, vi_context: dict[str, Any]) -> str:
    """Infer description from VI context."""
    name = Path(vi_name).stem
    if ":" in name:
        name = name.split(":")[-1]

    inputs = vi_context.get("inputs", [])
    outputs = vi_context.get("outputs", [])

    if outputs and not inputs:
        return f"Generates {name.lower()} data"
    elif inputs and not outputs:
        return f"Processes {name.lower()} data"
    elif not inputs and not outputs:
        return f"Performs {name.lower()} operation (no I/O)"

    return f"Performs {name.lower()} operation"


def _get_node_label(
    parent_id: str | None, parent_name: str | None, parent_labels: list[str] | None
) -> str | None:
    """Build human-readable label for wire endpoint."""
    if not parent_id:
        return None

    # Use parent name if available
    if parent_name:
        # For SubVIs, just use the name
        if parent_labels and "SubVI" in parent_labels:
            return parent_name
        return parent_name

    # For primitives, use label type
    if parent_labels:
        if "Primitive" in parent_labels:
            return "Primitive"
        if "Constant" in parent_labels:
            return "Constant"

    return parent_id[:8]  # Truncate ID

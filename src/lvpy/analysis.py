"""Core VI analysis logic - works with dataclasses.

This module provides the core analysis functionality that can be used
programmatically or wrapped by CLI scripts. It works with native Python
types and dataclasses, leaving serialization to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .docs.utils import generate_dependency_description
from .graph import InMemoryVIGraph
from .graph_types import Constant, Operation, Terminal, Wire


@dataclass
class ControlInfo:
    """Control (input) information."""

    name: str
    type: str
    default_value: object
    description: str
    slot_index: int


@dataclass
class IndicatorInfo:
    """Indicator (output) information."""

    name: str
    type: str
    description: str
    slot_index: int


@dataclass
class GraphStructure:
    """VI graph structure with dataclass instances."""

    inputs: list[Terminal]
    outputs: list[Terminal]
    operations: list[Operation]
    constants: list[Constant]
    data_flow: list[Wire]


@dataclass
class VIAnalysis:
    """Complete VI analysis with native types."""

    vi_name: str
    summary: str
    controls: list[ControlInfo]
    indicators: list[IndicatorInfo]
    graph: GraphStructure
    dependencies: dict[str, str]
    execution_order: list[str]


def infer_description(name: str | None, type_str: str | None, direction: str) -> str:
    """Infer description from name and type.

    Args:
        name: Parameter name
        type_str: Type string
        direction: 'input' or 'output'

    Returns:
        Human-readable description
    """
    if not name:
        return f"{direction.capitalize()} parameter"
    type_part = f" ({type_str})" if type_str and type_str != "Any" else ""
    return f"{name}{type_part}"


def generate_vi_summary(
    vi_name: str,
    controls: list[ControlInfo],
    indicators: list[IndicatorInfo],
    dependencies: dict[str, str],
) -> str:
    """Generate brief summary of VI.

    Args:
        vi_name: Name of the VI
        controls: List of control information
        indicators: List of indicator information
        dependencies: Dictionary of SubVI dependencies

    Returns:
        Summary string
    """
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
    vi_path: str | Path,
    search_paths: list[str] | None = None,
    expand_subvis: bool = True,
) -> VIAnalysis:
    """Analyze a VI and return structured data with dataclasses.

    This is the core analysis function that works with native types.
    Use this for programmatic access. For JSON output, use scripts/analyze_vi.py.

    Args:
        vi_path: Path to VI file (.vi) or block diagram XML (*_BDHb.xml)
        search_paths: Optional list of search paths for dependencies
        expand_subvis: If True, recursively load all SubVI dependencies
                      (slower but complete). If False, only load this VI
                      (faster but limited cross-references).

    Returns:
        VIAnalysis with complete VI structure as dataclasses
    """
    # Load VI with optional dependency expansion
    graph = InMemoryVIGraph()
    search_path_objs = [Path(p) for p in (search_paths or [])]

    vi_path_obj = Path(vi_path)
    if not vi_path_obj.exists():
        raise FileNotFoundError(f"VI file not found: {vi_path}")

    graph.load_vi(
        vi_path_obj,
        expand_subvis=expand_subvis,
        search_paths=search_path_objs or None,
    )

    # Get main VI name - resolve from path
    if str(vi_path).endswith("_BDHb.xml"):
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

    # Get VI context (returns dataclasses)
    vi_context = graph.get_vi_context(vi_name)

    # Extract controls with descriptions (Terminal dataclasses)
    controls = []
    for inp in vi_context.inputs:
        name = inp.name or f"input_{inp.index}"
        type_str = inp.python_type()
        controls.append(
            ControlInfo(
                name=name,
                type=type_str,
                default_value=inp.default_value,
                description=infer_description(name, type_str, "input"),
                slot_index=inp.index or 0,
            )
        )

    # Extract indicators with descriptions (Terminal dataclasses)
    indicators = []
    for out in vi_context.outputs:
        name = out.name or f"output_{out.index}"
        type_str = out.python_type()
        indicators.append(
            IndicatorInfo(
                name=name,
                type=type_str,
                description=infer_description(name, type_str, "output"),
                slot_index=out.index or 0,
            )
        )

    # Keep dataclasses as-is (no conversion to dict)
    graph_structure = GraphStructure(
        inputs=list(vi_context.inputs),
        outputs=list(vi_context.outputs),
        operations=list(vi_context.operations),
        constants=list(vi_context.constants),
        data_flow=list(vi_context.data_flow),
    )

    # Generate dependency descriptions (Operation dataclasses)
    dependencies = {}
    for op in vi_context.operations:
        if "SubVI" in op.labels and op.name:
            if op.name not in dependencies:  # Avoid duplicates
                dependencies[op.name] = generate_dependency_description(op.name, graph)

    # Get execution order
    try:
        execution_order = graph.get_operation_order(vi_name)
    except Exception:
        execution_order = []

    # Generate summary
    summary = generate_vi_summary(vi_name, controls, indicators, dependencies)

    return VIAnalysis(
        vi_name=vi_name,
        summary=summary,
        controls=controls,
        indicators=indicators,
        graph=graph_structure,
        dependencies=dependencies,
        execution_order=execution_order,
    )

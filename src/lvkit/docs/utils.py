"""Shared utilities for VI documentation generation."""

from __future__ import annotations

from pathlib import Path

from ..graph import InMemoryVIGraph
from ..graph.models import AnyGraphNode, VIContext, VINode
from ..vilib_resolver import get_resolver as get_vilib_resolver


def generate_dependency_description(subvi_name: str, graph: InMemoryVIGraph) -> str:
    """Generate 1-sentence description of what a SubVI does.

    Priority:
    1. vilib_resolver for known VIs
    2. The loaded VI's context (inputs/outputs)

    A VI with neither is described by an empty string.

    Args:
        subvi_name: Name of the SubVI
        graph: InMemoryVIGraph containing the VI

    Returns:
        One-sentence description, or an empty string
    """
    # First check vilib resolver
    try:
        resolver = get_vilib_resolver()
        vi = resolver.resolve_recorded(graph.recorded_path(subvi_name))
        if vi and vi.description:
            return vi.description
    except Exception:
        pass

    # A VI that is not loaded has nothing to describe it
    if graph.is_stub(subvi_name):
        return ""

    # For loaded VIs, try to infer from context
    try:
        vi_context = graph.get_vi_context(subvi_name)
        return _infer_from_context(subvi_name, vi_context, graph)
    except Exception:
        return ""


def _infer_from_context(
    vi_name: str, vi_context: VIContext, graph: InMemoryVIGraph
) -> str:
    """Infer description from VI inputs/outputs."""
    inputs = vi_context.inputs
    outputs = vi_context.outputs

    def _count_subvis(nodes: list[AnyGraphNode]) -> int:
        count = 0
        for node in nodes:
            if isinstance(node, VINode):
                count += 1
            count += _count_subvis(graph.child_nodes(node.id, vi_name))
        return count

    # Count SubVI calls (recursively, at any nesting depth)
    subvi_count = _count_subvis(graph.top_level_nodes(vi_name))

    # Build description based on I/O
    parts = []
    if inputs:
        parts.append(f"{len(inputs)} input(s)")
    if outputs:
        parts.append(f"{len(outputs)} output(s)")
    if subvi_count:
        parts.append(f"calls {subvi_count} SubVI(s)")

    base_name = (
        Path(vi_name).stem.split(":")[-1] if ":" in vi_name else Path(vi_name).stem
    )

    if parts:
        return f"{base_name} - {', '.join(parts)}"
    return f"{base_name} (no I/O)"

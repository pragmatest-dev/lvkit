"""Shared utilities for VI documentation generation."""

from __future__ import annotations

from pathlib import Path

from ..graph import InMemoryVIGraph
from ..graph.models import VIContext
from ..vilib_resolver import get_resolver as get_vilib_resolver


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


def _infer_from_name(vi_name: str) -> str:
    """Infer description from VI name."""
    # Extract base name without path/extension
    name = Path(vi_name).stem
    if ":" in name:  # Handle qualified names
        name = name.split(":")[-1]

    name_lower = name.lower()

    # Common patterns
    if "error" in name_lower and "cluster" in name_lower:
        return "Error handling VI"
    if "build" in name_lower and "path" in name_lower:
        return "Constructs file path from components"
    if "strip" in name_lower and "path" in name_lower:
        return "Separates file path into directory and filename"
    if "get" in name_lower and ("system" in name_lower or "directory" in name_lower):
        return "Retrieves system directory path"
    if "create" in name_lower and ("dir" in name_lower or "folder" in name_lower):
        return "Creates directory if it doesn't exist"
    if "file" in name_lower and "exists" in name_lower:
        return "Checks if file or directory exists"
    if "read" in name_lower and "file" in name_lower:
        return "Reads data from file"
    if "write" in name_lower and "file" in name_lower:
        return "Writes data to file"
    if "delete" in name_lower and ("file" in name_lower or "dir" in name_lower):
        return "Deletes file or directory"
    if "copy" in name_lower and "file" in name_lower:
        return "Copies file to new location"
    if "move" in name_lower and "file" in name_lower:
        return "Moves file to new location"

    # Default: use the VI name itself
    return f"Performs {name.lower()} operation (no I/O)"


def _infer_from_context(vi_name: str, vi_context: VIContext) -> str:
    """Infer description from VI inputs/outputs."""
    inputs = vi_context.inputs
    outputs = vi_context.outputs
    operations = vi_context.operations

    # Count SubVI calls
    subvi_count = sum(1 for op in operations if "SubVI" in op.labels)

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

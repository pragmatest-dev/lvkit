"""Shared utilities for VI documentation generation."""

from __future__ import annotations

from ..graph import InMemoryVIGraph
from ..vilib_resolver import get_resolver as get_vilib_resolver


def generate_dependency_description(subvi_name: str, graph: InMemoryVIGraph) -> str:
    """The description of a SubVI: the vi.lib catalog's for a VI it describes,
    else the documentation text the VI itself carries.

    A VI with neither is described by an empty string.

    Args:
        subvi_name: Name of the SubVI
        graph: InMemoryVIGraph containing the VI

    Returns:
        The description, or an empty string
    """
    vi = get_vilib_resolver().resolve_recorded(graph.recorded_path(subvi_name))
    if vi and vi.description:
        return vi.description

    # A VI that is not loaded has nothing to describe it
    if graph.is_stub(subvi_name):
        return ""

    return graph.get_vi_context(subvi_name).description or ""

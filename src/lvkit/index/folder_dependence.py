"""Whether a VI's facts depend on the library folders a run was given.

A VI's facts are read from the VI itself and from the dependencies its load
resolved. A dependency resolves the same under any folders when the VI records it
relative to itself and the file is there. It resolves through the folders when the
VI records it under a pseudo path (``<userlib>/...``), when it is found on the VI
Search Path instead of at its recorded path, and when it is not found at all (other
folders may find it). The same holds for the controls and classes a VI's types
read their fields from, through the dependencies they record in turn."""

from __future__ import annotations

from ..graph import InMemoryVIGraph
from ..library_folders import is_symbolic_root


def folder_sensitive(graph: InMemoryVIGraph, vi_name: str) -> bool:
    """Whether the facts of ``vi_name``, loaded in ``graph``, depend on the
    graph's library folders (see the module docstring)."""
    seen = {vi_name}
    pending = [vi_name]
    while pending:
        caller = pending.pop()
        for dep in graph.get_vi_dependencies(caller):
            if _resolved_through_folders(graph, dep, caller):
                return True
            if dep not in seen and (graph.is_typedef(dep) or graph.is_class(dep)):
                seen.add(dep)
                pending.append(dep)
    return False


def _resolved_through_folders(graph: InMemoryVIGraph, dep: str, caller: str) -> bool:
    if graph.is_stub(dep):
        return True
    recorded = graph.recorded_ref(dep, caller)
    if (
        recorded is not None
        and recorded.path_tokens
        and is_symbolic_root(recorded.path_tokens[0])
    ):
        return True
    return graph.found_elsewhere(dep, caller) is not None

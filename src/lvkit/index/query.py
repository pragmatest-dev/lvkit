"""Typed graph operations over a loaded index (``list[VIFacts]``).

Cross-VI *traversal* — ``get_callers``/``get_callees``/``blast_radius`` — that
deliberately stays OUT of the SQL surface (:mod:`lvkit.index.sql`): reachability
is a graph walk, not a relational query (design doc §4). Each function takes the
in-memory facts list (from ``store.load()`` or ``build.build_index()``) and
rebuilds a networkx ``DiGraph`` from the ``calls`` rows on the fly via
:func:`build_call_graph`. The relational/*definition* reads (terminals,
constants, symbols, type-uses) are now answered by SQL over the view layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import networkx as nx

from .model import VIFacts


@dataclass
class BlastRadius:
    """Transitive dependents of one VI over the pure call graph."""

    vi_key: str
    dependents: list[str]
    impact_score: int


def get_callers(vis: Sequence[VIFacts], vi_key: str) -> list[str]:
    """Paths of VIs that call ``vi_key`` — pure call edges only (a class
    method's owning class is never a "caller": ownership edges are excluded
    at build time, see ``build.project_vi_facts``)."""
    graph = build_call_graph(vis)
    target = _resolve_vi_key(vis, vi_key)
    if target is None or not graph.has_node(target):
        return []
    return sorted(graph.predecessors(target))


def get_callees(vis: Sequence[VIFacts], vi_key: str) -> list[str]:
    """Paths of VIs that ``vi_key`` calls — pure call edges only."""
    graph = build_call_graph(vis)
    target = _resolve_vi_key(vis, vi_key)
    if target is None or not graph.has_node(target):
        return []
    return sorted(graph.successors(target))


def blast_radius(
    vis: Sequence[VIFacts], vi_key: str, depth: int | None = None,
) -> BlastRadius:
    """Transitive dependents of ``vi_key`` (= "what breaks if I change this?"),
    optionally bounded to ``depth`` hops. ``impact_score`` is always
    ``len(dependents)``."""
    graph = build_call_graph(vis)
    target = _resolve_vi_key(vis, vi_key)
    if target is None or not graph.has_node(target):
        return BlastRadius(vi_key=vi_key, dependents=[], impact_score=0)

    if depth is None:
        dependents = sorted(nx.ancestors(graph, target))
    else:
        reverse = graph.reverse(copy=False)
        lengths = nx.single_source_shortest_path_length(reverse, target, cutoff=depth)
        dependents = sorted(p for p in lengths if p != target)

    return BlastRadius(
        vi_key=target, dependents=dependents, impact_score=len(dependents),
    )


def build_call_graph(vis: Iterable[VIFacts]) -> nx.DiGraph:
    """Rebuild the pure call graph (nodes = VI path) from ``calls`` rows.

    A callee key (a LabVIEW qualified name, e.g.
    ``TestCase.lvclass:run.vi``) resolves to a path when it exactly matches
    another indexed VI's ``qualified_name``, or — when unambiguous — its bare
    ``name``. An ambiguous bare-name match (same-named VIs, the exact
    collision this index exists to disambiguate) is left unresolved rather
    than silently guessed.
    """
    facts = list(vis)
    by_qualified = {f.qualified_name: f.path for f in facts if f.qualified_name}
    by_name: dict[str, list[str]] = {}
    for f in facts:
        by_name.setdefault(f.name, []).append(f.path)
    by_path = {f.path for f in facts}

    graph: nx.DiGraph = nx.DiGraph()
    for f in facts:
        graph.add_node(f.path)
    for f in facts:
        for callee_key in f.calls:
            target = _resolve_callee(callee_key, by_path, by_qualified, by_name)
            if target is not None and target != f.path:
                graph.add_edge(f.path, target)
    return graph


def _resolve_callee(
    callee_key: str,
    by_path: set[str],
    by_qualified: dict[str, str],
    by_name: dict[str, list[str]],
) -> str | None:
    if callee_key in by_path:
        return callee_key
    if callee_key in by_qualified:
        return by_qualified[callee_key]
    leaf = callee_key.rsplit(":", 1)[-1]
    candidates = by_name.get(leaf)
    if candidates and len(candidates) == 1:
        return candidates[0]
    return None


def _resolve_vi_key(vis: Sequence[VIFacts], vi_key: str) -> str | None:
    """Resolve a caller-supplied key (path, qualified name, or unambiguous
    bare name) to the canonical path key."""
    for f in vis:
        if f.path == vi_key:
            return f.path
    for f in vis:
        if f.qualified_name == vi_key:
            return f.path
    matches = [f.path for f in vis if f.name == vi_key]
    if len(matches) == 1:
        return matches[0]
    return None

"""Typed graph operations over a loaded index (``list[VIFacts]``).

Cross-VI transitive *traversal* — ``get_callers``/``get_callees``/
``blast_radius`` — that needs a reachability walk, not a relational query. Each
function takes the in-memory facts list (from ``store.load()`` or
``build.build_index()``) and rebuilds a networkx ``DiGraph`` via
:func:`build_call_graph` — whose edges are the node spine's ``kind='vi'`` rows
(each SubVI-call node's resolved ``callee_path``). Direct callers/callees are
also a one-hop node query in SQL (``WHERE callee_path=…``); these helpers add
the transitive closure. The relational/*definition* reads (terminals, constants,
nodes, type-uses) are answered by SQL over the view layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import networkx as nx

from .model import NodeKind, VIFacts


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
    vis: Sequence[VIFacts],
    vi_key: str,
    depth: int | None = None,
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
        vi_key=target,
        dependents=dependents,
        impact_score=len(dependents),
    )


def build_call_graph(vis: Iterable[VIFacts]) -> nx.DiGraph:
    """Rebuild the pure call graph (nodes = VI path) from the NODE spine.

    The call graph is a slice of the block-diagram nodes: each ``kind='vi'``
    node is a SubVI call site whose ``callee_path`` was resolved to an in-repo
    VI at merge time (:func:`resolve_node_callee_paths` — an external callee
    stays ``None`` and contributes no edge). Multiple call
    sites to the same callee collapse to one edge. Requires ``callee_path`` to
    be populated first — the merge order in ``build.py`` guarantees it.
    """
    facts = list(vis)
    graph: nx.DiGraph = nx.DiGraph()
    for f in facts:
        graph.add_node(f.path)
    for f in facts:
        for n in f.nodes:
            if n.kind is NodeKind.VI and n.callee_path and n.callee_path != f.path:
                graph.add_edge(f.path, n.callee_path)
    return graph


def resolve_node_callee_paths(vis: Iterable[VIFacts]) -> None:
    """Fill each ``kind='vi'`` / ``kind='global_variable'`` node's
    ``callee_path`` with the in-repo VI it refers to.

    A SubVI call's callee, or a global variable's Global VI, is the file its
    caller recorded (``callee_key``); it is the node's ``callee_path`` when that
    file is an indexed VI, else ``None`` (an external or missing file). Mutates
    the facts in place at merge time (needs the whole repo to resolve), exactly
    like ``_recompute_impact`` fills ``impact_score``. Idempotent.

    NOTE this is a per-call-site CONVENIENCE edge on the node spine; the
    authoritative transitive call graph stays :func:`get_callers` /
    :func:`blast_radius` over the ``calls`` table (which also class-qualifies
    dynamic-dispatch callees to their runtime type)."""
    facts = list(vis)
    by_path = {f.path for f in facts}
    for f in facts:
        for n in f.nodes:
            target: str | None = None
            if n.kind in (NodeKind.VI, NodeKind.GLOBAL_VARIABLE):
                target = n.callee_key if n.callee_key in by_path else None
            n.callee_path = target if target and target != f.path else None


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

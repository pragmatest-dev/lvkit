"""Shared test helpers for lvkit tests.

Provides graph and context construction for tests that need CodeGenContext
with a proper graph (required since bind/resolve store var_names on the graph).
"""

from __future__ import annotations

import re
from pathlib import Path

from lvkit.codegen.context import CodeGenContext
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.list_deps import list_deps
from lvkit.models import Terminal


def transitive_closure(
    entry: Path, root: Path, *, include_entry: bool = False
) -> set[Path]:
    """The transitive dependency closure the web extension's
    ``stageDependencyClosure`` BFS mirrors into ``/proj`` — driven by
    ``lvkit.list_deps`` exactly as ``editors/vscode/web/extension.js`` does:
    read one level, discover the next from what was just staged, repeat. All
    paths are resolved (``list_deps`` already returns resolved strings).
    ``include_entry`` seeds the result with ``entry`` itself (the extension
    stages the opened VI too)."""
    entry = entry.resolve()
    closure: set[Path] = {entry} if include_entry else set()
    frontier = [entry]
    while frontier:
        nxt: list[Path] = []
        for f in frontier:
            for dep_str in list_deps(f, search_paths=[root]):
                dep = Path(dep_str).resolve()
                if dep not in closure:
                    closure.add(dep)
                    nxt.append(dep)
        frontier = nxt
    return closure

_RENDER_ID_RE = re.compile(r"lv-[a-z0-9-]*-vi", re.IGNORECASE)


def normalize_render_ids(svg: str) -> str:
    """Normalize the path-derived SVG ids for a web/desktop render byte-parity
    comparison. The root ``id`` and every clip id/ref embed the source file PATH
    (``lv-<pathslug>-vi``, plus ``-cN`` for clips), which legitimately differs
    between a ``/proj``-staged web render and a workspace desktop render — the
    parity guarantee is about render CONTENT, not the path. This mirrors
    ``editors/vscode/build/check-web-parity.sh``'s ``norm()``
    (``sed -E 's/lv-[A-Za-z0-9-]*-vi/lv-ID/g'``, global — rewriting both the
    ``id="…"`` definitions and the ``url(#…)`` references while preserving the
    ``-cN`` clip discriminator). The char class is case-INSENSITIVE because a
    path slug preserves case (e.g. ``JKI-VI-Tester``, ``Test LVKit``); this
    ``re.IGNORECASE`` and the shell's ``[A-Za-z0-9-]`` are kept identical so the
    Python parity tests and the shell CI gate can't drift."""
    return _RENDER_ID_RE.sub("lv-ID", svg)


def make_node(node_id: str, terminal_ids: list[str]) -> PrimitiveNode:
    """Create a graph node with terminals."""
    return PrimitiveNode(
        id=node_id,
        vi="test.vi",
        name=node_id,
        terminals=[
            Terminal(id=tid, index=i, direction="output")
            for i, tid in enumerate(terminal_ids)
        ],
    )


def make_graph_with_terminals(*terminal_ids: str) -> InMemoryVIGraph:
    """Create a graph with nodes that have the given terminals."""
    graph = InMemoryVIGraph()
    for i, tid in enumerate(terminal_ids):
        nid = f"n{i}"
        node = make_node(nid, [tid])
        graph._graph.add_node(nid, node=node)
        graph._term_to_node[tid] = nid
    return graph


def make_graph_with_edge(
    src_tid: str,
    dst_tid: str,
    src_node: str = "p1",
    dst_node: str = "p2",
) -> InMemoryVIGraph:
    """Create a graph with one edge between two nodes."""
    graph = InMemoryVIGraph()
    src = make_node(src_node, [src_tid])
    dst = make_node(dst_node, [dst_tid])
    graph._graph.add_node(src_node, node=src)
    graph._graph.add_node(dst_node, node=dst)
    graph._graph.add_edge(
        src_node,
        dst_node,
        source=WireEnd(terminal_id=src_tid, node_id=src_node),
        dest=WireEnd(terminal_id=dst_tid, node_id=dst_node),
    )
    graph._term_to_node[src_tid] = src_node
    graph._term_to_node[dst_tid] = dst_node
    return graph


def make_ctx(*terminal_ids: str) -> CodeGenContext:
    """Create a CodeGenContext with a graph that has the given terminals."""
    graph = make_graph_with_terminals(*terminal_ids)
    return CodeGenContext(graph=graph)

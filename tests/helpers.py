"""Shared test helpers for lvkit tests.

Provides graph and context construction for tests that need CodeGenContext
with a proper graph (required since bind/resolve store var_names on the graph).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from lvkit.codegen.context import CodeGenContext
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.load_mode import LoadMode
from lvkit.models import Terminal


def transitive_closure(
    entry: Path, root: Path, *, include_entry: bool = False
) -> set[Path]:
    """The MINIMAL dependency closure the web extension's
    ``stageDependencyClosure`` stages into ``/proj`` — mirrored here the SAME
    way the extension does it: MINIMAL-load ``entry`` and ask the loader which
    files that load resolved (``get_dependency_paths``). Over a complete on-disk
    corpus every dependency is present, so a single load names the whole
    closure; the extension reaches the same set incrementally as it fetches
    absent files into ``/proj``. ``include_entry`` seeds the result with
    ``entry`` itself (the extension stages the opened VI too)."""
    entry = entry.resolve()
    graph = InMemoryVIGraph()
    name = graph.load_vi(entry, LoadMode.MINIMAL, search_paths=[root])
    closure = {p.resolve() for p in graph.get_dependency_paths(name or str(entry))}
    if include_entry:
        closure.add(entry)
    return closure


def progressive_closure(entry: Path, root: Path, proj: Path) -> set[Path]:
    """The dependency closure reached by REPLAYING the web extension's
    progressive staging loop against an initially-empty ``/proj`` — the ACTUAL
    absent-then-fetch path, not a single load over the full on-disk tree.

    Desktop/native tests load with every file already present, so they never
    exercise the loader's ABSENT branches (a dep must be NAMED by its recorded
    path AND EDGED to its caller while its file is missing, and its stub key must
    match the key a later present load produces). Those branches are what the web
    depends on and what silently regresses. This mirrors ``stageDependencyClosure``:
    stage only ``entry`` into ``proj``, MINIMAL-load it with ``search_paths=[proj]``,
    fetch every newly-named dependency from ``root`` into ``proj`` preserving
    layout, and repeat until the set stops growing.

    Returns the staged deps as their ORIGINAL ``root`` resolved paths (same shape
    as ``transitive_closure`` for direct comparison) — a converged progressive
    run MUST equal the single-pass closure, or the absent-path logic is broken.
    """
    entry, root = entry.resolve(), root.resolve()
    rel_entry = entry.relative_to(root)
    staged: set[Path] = set()

    def fetch(rel: Path) -> bool:
        src = root / rel
        dst = proj / rel
        if rel in staged or not src.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        staged.add(rel)
        return True

    fetch(rel_entry)
    entry_in_proj = proj / rel_entry
    for _ in range(20):  # bounded; real closures converge in a handful of passes
        graph = InMemoryVIGraph()
        name = graph.load_vi(entry_in_proj, LoadMode.MINIMAL, search_paths=[proj])
        added = 0
        for dep in graph.get_dependency_paths(name or str(entry_in_proj)):
            try:
                rel = dep.resolve().relative_to(proj.resolve())
            except ValueError:
                continue  # a dep that didn't resolve under /proj — skip
            if fetch(rel):
                added += 1
        if added == 0:
            break

    return {(root / rel).resolve() for rel in staged if rel != rel_entry}


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
        vi_path="test.vi",
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


def build_graph(vi_context, vi_name: str, nodes: list):
    """Build an InMemoryVIGraph for a codegen unit test: stub FP terminals for
    ``vi_context``'s inputs/outputs/constants, register the given (already
    graph-native) ``GraphNode``s as ``vi_name``'s node set, and wire up
    ``vi_context.data_flow`` — so a ``build_module`` unit test can pass a real
    graph, since ``build_module`` walks ``graph.top_level_nodes`` (there is no
    more projected ``vi_context.operations`` to build one from).

    Callers build the typed graph nodes directly (``LoopNode``/
    ``CaseStructureNode``/``VINode``/``PrimitiveNode``/... — set ``.parent``/
    ``.frame`` on children before passing them in here; ``register_nodes``
    derives each parent's forward ``children`` list from those back-links).
    """
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.models import PrimitiveNode, WireEnd

    graph = InMemoryVIGraph()
    graph._vi_nodes.setdefault(vi_name, set())

    def _stub(tid: str, direction: str) -> None:
        nid = f"_fp_{tid}"
        if nid in graph._graph:
            return
        graph._graph.add_node(
            nid,
            node=PrimitiveNode(
                id=nid,
                vi_path=vi_name,
                name=nid,
                terminals=[Terminal(id=tid, index=0, direction=direction)],
            ),
        )
        graph._term_to_node[tid] = nid

    for inp in vi_context.inputs:
        if inp.id:
            _stub(inp.id, "output")
    for out in vi_context.outputs:
        if out.id:
            _stub(out.id, "input")
    for const in vi_context.constants:
        if const.id:
            _stub(const.id, "output")

    register_nodes(graph, nodes, vi_name=vi_name)

    for wire in vi_context.data_flow:
        src_tid = wire.from_terminal_id
        dst_tid = wire.to_terminal_id
        src_nid = graph._term_to_node.get(src_tid)
        dst_nid = graph._term_to_node.get(dst_tid)
        if src_nid is None or dst_nid is None:
            continue
        graph._graph.add_edge(
            src_nid,
            dst_nid,
            source=WireEnd(terminal_id=src_tid, node_id=src_nid),
            dest=WireEnd(terminal_id=dst_tid, node_id=dst_nid),
        )

    return graph


def tunnel_terminals(tunnels):
    """Express a list of ``Tunnel`` objects as the paired outer/inner
    ``TunnelTerminal``s a ``StructureNode`` carries, so ``ctx.tunnels`` (which
    reconstructs tunnels from a node's terminals) yields the same set. Lets a
    codegen unit test build a structure ``GraphNode`` with an equivalent
    ``tunnels=[...]`` list.
    """
    from lvkit.models import TunnelTerminal

    out: list = []
    for i, t in enumerate(tunnels):
        common: dict[str, Any] = dict(
            tunnel_type=t.tunnel_type,
            mode=t.mode,
            conditional=t.conditional,
            sr_initialized=t.sr_initialized,
            sr_stack_depth=t.sr_stack_depth,
        )
        out.append(
            TunnelTerminal(
                id=t.outer_terminal_uid,
                index=2 * i,
                direction="input",
                boundary="outer",
                paired_id=t.inner_terminal_uid,
                **common,
            )
        )
        out.append(
            TunnelTerminal(
                id=t.inner_terminal_uid,
                index=2 * i + 1,
                direction="output",
                boundary="inner",
                paired_id=t.outer_terminal_uid,
                **common,
            )
        )
    return out


def register_nodes(graph, nodes, vi_name: str = "test.vi") -> None:
    """Register typed GraphNodes into ``graph`` the way construction does:
    add each node, index its terminals, add it to the VI's node set, and
    populate each structure's forward ``children`` list from the ``parent``
    back-links (sorted by ``_node_order_key``, like the real builder).

    Lets a codegen unit test hand structure emitters real graph nodes to walk
    via ``ctx.child_nodes`` / ``ctx.frame_children``.
    """
    from collections import defaultdict

    from lvkit.graph.core import _node_order_key

    graph._vi_nodes.setdefault(vi_name, set())
    for n in nodes:
        graph._graph.add_node(n.id, node=n)
        graph._vi_nodes[vi_name].add(n.id)
        for t in n.terminals:
            # setdefault: never steal a terminal a prior (e.g. from_wires) node
            # already owns, so existing wire edges keep resolving.
            graph._term_to_node.setdefault(t.id, n.id)

    kids: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        if n.parent:
            kids[n.parent].append(n.id)
    for n in nodes:
        if n.id in kids:
            n.children = sorted(kids[n.id], key=_node_order_key)

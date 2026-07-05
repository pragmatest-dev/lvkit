"""Join the graph (semantics) with the layout (geometry) into a Scene.

This is the one place that reads BOTH the graph and the heap-derived
``Layout`` — everywhere else in the renderer only sees one or the other.
The result, ``Scene``, is a backend-agnostic view model: drawing code never
touches the graph or the raw heap XML again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..graph.core import InMemoryVIGraph
from ..graph.models import (
    AnyGraphNode,
    CaseStructureNode,
    SequenceNode,
    StructureNode,
    Wire,
)
from ..models import CaseFrame, FPTerminal, SequenceFrame, Terminal, TunnelTerminal
from .layout import Layout, Point, Rect, build_layout
from .style import WireStyle, wire_style
from .wire_router import WireRouter

logger = logging.getLogger(__name__)

Frame = CaseFrame | SequenceFrame


@dataclass(frozen=True)
class RenderTerminal:
    """A terminal joined to its on-diagram center point."""

    terminal: Terminal
    center: Point


@dataclass(frozen=True)
class RenderFPTerminal:
    """A front-panel control/indicator terminal placed on the diagram."""

    terminal: FPTerminal
    bounds: Rect
    center: Point


@dataclass(frozen=True)
class RenderNode:
    """A non-structure graph node (primitive, SubVI call, constant, ...)."""

    node: AnyGraphNode
    bounds: Rect
    terminals: list[RenderTerminal] = field(default_factory=list)


@dataclass(frozen=True)
class RenderBorderTerminal:
    """A structure border glyph (loop N/i/cond, case selector, shift reg).

    ``terminal`` is the graph Terminal when one exists (tunnels, case
    selector); it's None for the loop count/index/test DCOs, which the
    graph doesn't model as full Terminal objects today (geometry-only —
    drawn as an undecorated box; see DESIGN.md judgment-call notes).
    """

    terminal: Terminal | None
    bounds: Rect


@dataclass(frozen=True)
class RenderStructure:
    """A structure node (loop, case, sequence, in-place element)."""

    node: StructureNode
    bounds: Rect
    border_terminals: list[RenderBorderTerminal] = field(default_factory=list)
    shown_frame: Frame | None = None


@dataclass(frozen=True)
class RenderWireNet:
    """One source terminal's wires, grouped (a fan-out is one net)."""

    source: Wire | None  # the first wire in the net, for source metadata
    style: WireStyle
    branches: list[list[Point]] = field(default_factory=list)
    junctions: list[Point] = field(default_factory=list)


@dataclass(frozen=True)
class Scene:
    """Backend-agnostic view model for one VI's block diagram."""

    bounds: Rect
    fp_terminals: list[RenderFPTerminal] = field(default_factory=list)
    nodes: list[RenderNode] = field(default_factory=list)
    structures: list[RenderStructure] = field(default_factory=list)
    wire_nets: list[RenderWireNet] = field(default_factory=list)
    icon_png: Path | None = None

    @property
    def obstacles(self) -> list[Rect]:
        """Node rectangles wires should avoid (structures are not obstacles,
        matching how LabVIEW routes wires along/through structure borders)."""
        return [n.bounds for n in self.nodes]


def _strip_prefix(qualified_id: str, vi_name: str) -> str:
    prefix = f"{vi_name}::"
    if qualified_id.startswith(prefix):
        return qualified_id[len(prefix):]
    return qualified_id


def _shown_frame_and_hidden_keys(
    node: StructureNode,
) -> tuple[Frame | None, set[str]]:
    """Pick the single displayed frame for case/stacked-sequence structures.

    Flat sequences show every frame side by side on the real diagram (not
    single-frame), so callers only invoke this for case structures and
    *stacked* sequences.
    """
    if isinstance(node, CaseStructureNode):
        case_frames = node.frames
        if not case_frames:
            return None, set()
        shown_case = next(
            (f for f in case_frames if f.is_default), case_frames[0],
        )
        hidden_case = {
            str(f.selector_value) for f in case_frames if f is not shown_case
        }
        return shown_case, hidden_case
    if isinstance(node, SequenceNode):
        seq_frames = node.frames
        if not seq_frames:
            return None, set()
        shown_seq = seq_frames[0]
        hidden_seq = {str(i) for i in range(len(seq_frames)) if i != 0}
        return shown_seq, hidden_seq
    return None, set()


def _hidden_structures(
    nodes: list[AnyGraphNode],
) -> dict[str, tuple[Frame | None, set[str]]]:
    """Structure id -> (shown frame, hidden frame keys) for case/stacked
    sequences only (flat sequences show every frame)."""
    result: dict[str, tuple[Frame | None, set[str]]] = {}
    for node in nodes:
        if isinstance(node, CaseStructureNode):
            shown, hidden = _shown_frame_and_hidden_keys(node)
            if hidden:
                result[node.id] = (shown, hidden)
        elif isinstance(node, SequenceNode) and node.node_type != "flatSequence":
            shown, hidden = _shown_frame_and_hidden_keys(node)
            if hidden:
                result[node.id] = (shown, hidden)
    return result


def _excluded_node_ids(nodes: list[AnyGraphNode]) -> set[str]:
    """Nodes hidden by the single-frame display policy, incl. transitive
    descendants of an excluded structure (C2)."""
    hidden_by_structure = {
        struct_id: hidden for struct_id, (_shown, hidden) in
        _hidden_structures(nodes).items()
    }

    excluded: set[str] = set()
    by_parent: dict[str, list[str]] = {}
    for node in nodes:
        if node.parent:
            by_parent.setdefault(node.parent, []).append(node.id)
        hidden = hidden_by_structure.get(node.parent or "")
        if hidden is not None and str(node.frame) in hidden:
            excluded.add(node.id)

    frontier = list(excluded)
    while frontier:
        nid = frontier.pop()
        for child_id in by_parent.get(nid, []):
            if child_id not in excluded:
                excluded.add(child_id)
                frontier.append(child_id)
    return excluded


def _render_terminals(
    node: AnyGraphNode, layout: Layout, vi_name: str,
) -> list[RenderTerminal]:
    result: list[RenderTerminal] = []
    for t in node.terminals:
        center = layout.terminal_centers.get(_strip_prefix(t.id, vi_name))
        if center is None:
            logger.debug("no geometry for terminal %s", t.id)
            continue
        result.append(RenderTerminal(terminal=t, center=center))
    return result


def _structure_borders(
    node: StructureNode, layout: Layout, vi_name: str,
) -> list[RenderBorderTerminal]:
    result: list[RenderBorderTerminal] = []
    seen_raw: set[str] = set()
    for t in node.terminals:
        if not (isinstance(t, TunnelTerminal) and t.boundary == "outer"):
            continue
        raw = _strip_prefix(t.id, vi_name)
        rect = layout.node_bounds.get(raw)
        if rect is None:
            continue
        result.append(RenderBorderTerminal(terminal=t, bounds=rect))
        seen_raw.add(raw)

    # Loop count/index/test DCOs: geometry-only (the graph has no Terminal
    # for them today — see DESIGN.md judgment-call notes). Drawn as an
    # undecorated box rather than guessing a glyph/label.
    raw_struct_uid = _strip_prefix(node.id, vi_name)
    for dco_uid in layout.structure_border_uids.get(raw_struct_uid, []):
        if dco_uid in seen_raw:
            continue
        rect = layout.border_terminals.get(dco_uid)
        if rect is not None:
            result.append(RenderBorderTerminal(terminal=None, bounds=rect))
    return result


def _build_wire_nets(
    graph: InMemoryVIGraph,
    vi_name: str,
    layout: Layout,
    excluded: set[str],
    obstacles: list[Rect],
    scene_bounds: Rect,
) -> list[RenderWireNet]:
    wires = [
        w for w in graph.get_wires(vi_name, include_internal=False)
        if w.source.node_id not in excluded and w.dest.node_id not in excluded
    ]

    by_source: dict[str, list[Wire]] = {}
    order: list[str] = []
    for w in wires:
        key = w.source.terminal_id
        if key not in by_source:
            by_source[key] = []
            order.append(key)
        by_source[key].append(w)

    router = WireRouter(obstacles, scene_bounds)
    all_points = list(layout.terminal_centers.values())

    nets: list[RenderWireNet] = []
    for key in order:
        group = by_source[key]
        src_center = layout.terminal_centers.get(_strip_prefix(key, vi_name))
        if src_center is None:
            logger.debug("no geometry for source terminal %s; dropping wire(s)", key)
            continue

        branches: list[list[Point]] = []
        for w in group:
            dst_center = layout.terminal_centers.get(
                _strip_prefix(w.dest.terminal_id, vi_name)
            )
            if dst_center is None:
                logger.debug(
                    "no geometry for dest terminal %s; dropping wire",
                    w.dest.terminal_id,
                )
                continue
            branches.append(router.route(src_center, dst_center, all_points))

        if not branches:
            continue

        source_term = graph.get_terminal(key)
        style = wire_style(source_term.lv_type if source_term else None)
        junctions = [src_center] if len(branches) > 1 else []
        nets.append(RenderWireNet(
            source=group[0], style=style, branches=branches, junctions=junctions,
        ))
    return nets


def build_scene(graph: InMemoryVIGraph, vi_name: str) -> Scene | None:
    """Build a ``Scene`` for one VI by joining graph semantics to heap
    geometry. Returns None (fail-closed) if required geometry is missing —
    callers should fall back to a non-geometric rendering (e.g. Mermaid)."""
    vi_name = graph.resolve_vi_name(vi_name)
    src_path = graph.get_vi_source_path(vi_name)
    if src_path is None:
        logger.warning("render: no source path for VI %r", vi_name)
        return None

    layout = build_layout(src_path)
    all_nodes = graph.iter_nodes(vi_name)
    excluded = _excluded_node_ids(all_nodes)
    hidden_structures = _hidden_structures(all_nodes)

    render_nodes: list[RenderNode] = []
    structures: list[RenderStructure] = []
    missing: list[str] = []

    for node in all_nodes:
        if node.id in excluded:
            continue
        raw_uid = _strip_prefix(node.id, vi_name)
        bounds = layout.node_bounds.get(raw_uid)
        if bounds is None:
            missing.append(node.id)
            continue
        if isinstance(node, StructureNode):
            shown_frame, _hidden = hidden_structures.get(node.id, (None, set()))
            structures.append(RenderStructure(
                node=node,
                bounds=bounds,
                border_terminals=_structure_borders(node, layout, vi_name),
                shown_frame=shown_frame,
            ))
        else:
            render_nodes.append(RenderNode(
                node=node,
                bounds=bounds,
                terminals=_render_terminals(node, layout, vi_name),
            ))

    fp_terminals: list[RenderFPTerminal] = []
    vi_node = graph.get_graph_node(vi_name)
    if vi_node is not None:
        for t in vi_node.terminals:
            if not isinstance(t, FPTerminal):
                continue
            raw_uid = _strip_prefix(t.id, vi_name)
            bounds = layout.node_bounds.get(raw_uid)
            if bounds is None:
                missing.append(t.id)
                continue
            center = layout.terminal_centers.get(raw_uid, (
                (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2,
            ))
            fp_terminals.append(RenderFPTerminal(
                terminal=t, bounds=bounds, center=center,
            ))

    if missing:
        logger.warning(
            "render_vi(%s): missing geometry for %d id(s), declining to "
            "render: %s", vi_name, len(missing), missing,
        )
        return None

    scene_bounds = layout.scene_bounds()
    obstacles = [n.bounds for n in render_nodes]
    wire_nets = _build_wire_nets(
        graph, vi_name, layout, excluded, obstacles, scene_bounds,
    )

    return Scene(
        bounds=scene_bounds,
        fp_terminals=fp_terminals,
        nodes=render_nodes,
        structures=structures,
        wire_nets=wire_nets,
        icon_png=layout.icon_png,
    )

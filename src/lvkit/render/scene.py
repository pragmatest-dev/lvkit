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
    LoopNode,
    SequenceNode,
    StructureNode,
    Wire,
)
from ..models import (
    CaseFrame,
    FPTerminal,
    LVType,
    SequenceFrame,
    Terminal,
    TunnelTerminal,
)
from .glyph import ArithGlyph, Glyph
from .layout import Layout, Point, Rect, build_layout
from .nodes import GlyphContext, resolve_glyph
from .style import WireStyle, numeric_repr, wire_style
from .wire_router import WireRouter, _compress

logger = logging.getLogger(__name__)

Frame = CaseFrame | SequenceFrame

# LabVIEW-internal nodes that are NOT drawn as visible diagram objects. `nMux`
# ("Node Multiplexer") is the compiler's data multiplexer at structure
# boundaries (shift-register / tunnel muxing across frames/iterations) — it
# spans the structure's inner region in the heap but LabVIEW never draws it as a
# box. We skip its glyph; its terminals stay in the layout so wires still route
# to the shift-register / tunnel positions that visually represent it.
_INTERNAL_NODE_TYPES = {"nMux"}


@dataclass(frozen=True)
class RenderTerminal:
    """A terminal joined to its on-diagram center point (and heap rect)."""

    terminal: Terminal
    center: Point
    # The terminal's own heap ``termBounds`` rect (absolute). Kept because some
    # glyphs (arithmetic triangles) are sized/placed from the union of their
    # terminal rects, not the 32x32 node box — see draw.py::draw_node.
    bounds: Rect | None = None


@dataclass(frozen=True)
class RenderFPTerminal:
    """A front-panel control/indicator terminal placed on the diagram."""

    terminal: FPTerminal
    bounds: Rect
    center: Point
    label_visible: bool = True


@dataclass(frozen=True)
class RenderNode:
    """A non-structure graph node (primitive, SubVI call, constant, ...)."""

    node: AnyGraphNode
    bounds: Rect
    # Resolved once here (the graph-driven join point) via the P2 resolver
    # chain (render/nodes.py::resolve_glyph) — draw.py never sees the graph,
    # only this already-resolved Glyph, per the "backend-agnostic view
    # model" rule. resolve_glyph() never returns None (FallbackBoxResolver
    # always succeeds), so this is required, not optional.
    glyph: Glyph
    terminals: list[RenderTerminal] = field(default_factory=list)
    label_visible: bool = True


@dataclass(frozen=True)
class RenderBorderTerminal:
    """A structure border glyph (loop N/i/cond, case selector, shift reg).

    ``terminal`` is the graph Terminal when one exists (N/lMax, shift
    registers, auto-index tunnels, case selector); it's None for the loop
    index/test DCOs (i/cond), which the graph doesn't model as full
    Terminal objects — their existence is instead guaranteed by the
    structure kind (see ``_LOOP_GUARANTEED_KINDS``).

    ``glyph_kind`` is the fixed decoration to draw: "N", "i", "cond",
    "sr_down", "sr_up", "autoindex" (array index/accumulate), "tunnel"
    (last-value passthrough — a filled type-color block), "selector", or None.
    ``color`` is the type color for a filled "tunnel" block.
    """

    terminal: Terminal | None
    bounds: Rect
    glyph_kind: str | None = None
    color: str | None = None


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
    # Receiving-terminal points where source/dest normalized types differ —
    # LabVIEW's implicit-conversion coercion dot (see style.coercion_key).
    coercion_dots: list[Point] = field(default_factory=list)


@dataclass(frozen=True)
class Scene:
    """Backend-agnostic view model for one VI's block diagram."""

    bounds: Rect
    fp_terminals: list[RenderFPTerminal] = field(default_factory=list)
    nodes: list[RenderNode] = field(default_factory=list)
    structures: list[RenderStructure] = field(default_factory=list)
    wire_nets: list[RenderWireNet] = field(default_factory=list)
    coercion_dots: list[Point] = field(default_factory=list)
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
        key = _strip_prefix(t.id, vi_name)
        center = layout.terminal_centers.get(key)
        if center is None:
            logger.debug("no geometry for terminal %s", t.id)
            continue
        result.append(RenderTerminal(
            terminal=t, center=center, bounds=layout.node_bounds.get(key),
        ))
    return result


# TunnelTerminal.tunnel_type -> fixed glyph kind, for border terminals the
# graph DOES model (they're real wireable dataflow terminals: N/lMax takes
# an optional input wire, SR/auto-index carry real data). "selector" also
# matches by name because the graph tags it that way (construction.py)
# rather than always setting tunnel_type="caseSel".
_TUNNEL_GLYPH_KIND = {
    "lMax": "N", "lSR": "sr_down", "rSR": "sr_up",
    "lpTun": "autoindex", "caseSel": "selector",
}

# Border terminals a loop is GUARANTEED to have, purely as a function of
# ``LoopNode.loop_type`` — a For Loop always has N (top-left) and i
# (bottom-left); a While Loop always has i (bottom-left) and a stop
# condition (bottom-right). The graph doesn't model i/cond as Terminal
# objects (they're not wireable inputs), so their existence comes from
# this structure-type table, not from finding a graph Terminal or even a
# successfully-parsed DCO — only their POSITION comes from heap geometry.
_LOOP_GUARANTEED_KINDS: dict[str, tuple[str, ...]] = {
    "forLoop": ("N", "i"),
    "whileLoop": ("i", "cond"),
}


def _structure_borders(
    node: StructureNode, layout: Layout, vi_name: str,
) -> list[RenderBorderTerminal]:
    result: list[RenderBorderTerminal] = []
    consumed: set[str] = set()
    kinds_present: set[str] = set()

    # 1. Border terminals the graph models as real Terminals (N/lMax, shift
    # registers, auto-index tunnels, case selector).
    for t in node.terminals:
        if not (isinstance(t, TunnelTerminal) and t.boundary == "outer"):
            continue
        raw = _strip_prefix(t.id, vi_name)
        rect = layout.node_bounds.get(raw)
        if rect is None:
            continue
        glyph_kind = _TUNNEL_GLYPH_KIND.get(t.tunnel_type)
        if glyph_kind is None and t.name == "selector":
            glyph_kind = "selector"
        if t.tunnel_type == "lpTun":
            # Auto-indexing (array in/accumulate out) -> [ ] brackets;
            # last-value passthrough -> a filled block in the wire type color.
            glyph_kind = ("autoindex" if raw in layout.indexing_tunnels
                          else "tunnel")
        # Tunnels and shift registers carry the WIRE TYPE COLOR in LabVIEW
        # (orange DBL, blue I32, ...) — not a flat gray/white.
        color = (wire_style(t.lv_type).color
                 if glyph_kind in ("autoindex", "tunnel", "sr_down", "sr_up")
                 else None)
        result.append(
            RenderBorderTerminal(
                terminal=t, bounds=rect, glyph_kind=glyph_kind, color=color,
            )
        )
        consumed.add(raw)
        if glyph_kind:
            kinds_present.add(glyph_kind)

    raw_struct_uid = _strip_prefix(node.id, vi_name)
    border_uids = layout.structure_border_uids.get(raw_struct_uid, [])

    # 2. Border glyphs GUARANTEED by the structure kind (i/cond, and N as a
    # defensive fallback) but not modeled as graph Terminals — position
    # only from heap geometry, matched by the DCO's fixed glyph kind.
    if isinstance(node, LoopNode):
        for kind in _LOOP_GUARANTEED_KINDS.get(node.loop_type or "", ()):
            if kind in kinds_present:
                continue  # already rendered via a real graph Terminal (N)
            match = next(
                (u for u in border_uids
                 if u not in consumed and layout.border_terminal_kind.get(u) == kind),
                None,
            )
            if match is None:
                logger.debug(
                    "no heap geometry for guaranteed %r border terminal on "
                    "%s (%s)", kind, node.id, node.loop_type,
                )
                continue
            result.append(RenderBorderTerminal(
                terminal=None, bounds=layout.border_terminals[match],
                glyph_kind=kind,
            ))
            consumed.add(match)
            kinds_present.add(kind)

    # 3. Any leftover border DCO (e.g. a case selector the graph didn't tag,
    # or a kind this structure's type doesn't guarantee) — geometry-only,
    # drawn as an undecorated box rather than a guessed glyph.
    for uid in border_uids:
        if uid in consumed:
            continue
        rect = layout.border_terminals.get(uid)
        if rect is not None:
            result.append(RenderBorderTerminal(
                terminal=None, bounds=rect,
                glyph_kind=layout.border_terminal_kind.get(uid),
            ))
            consumed.add(uid)
    return result


def _wire_carrier_type(
    src_type: LVType | None, dest_types: list[LVType | None],
) -> LVType | None:
    """The type a wire visually carries, reconciled from BOTH endpoints.

    An auto-indexing For-Loop tunnel's inner terminal is typed as the ARRAY it
    indexes, but it emits ONE ELEMENT per iteration. So a wire from an array
    source into a scalar (non-array) destination — i.e. the array's element
    type — carries the element (a THIN wire), not the array. Everywhere else
    the source type already is the carried type.
    """
    if src_type is not None and src_type.kind == "array" \
            and src_type.element_type is not None:
        if any(dt is not None and dt.kind != "array" for dt in dest_types):
            return src_type.element_type
    return src_type


_STUB = 9.0  # length a wire exits/enters a terminal along its edge normal


def _exit_side(direction: str | None, center: Point, bounds: Rect | None) -> Point:
    """Unit normal a wire leaves/enters a terminal on.

    LabVIEW dataflow runs left→right: a node's OUTPUT exits to the RIGHT and an
    INPUT is entered from the LEFT — regardless of where the tiny clickable
    termBounds actually sits (a primitive's output termBounds is near the node
    centre, not its visual apex, so a nearest-edge guess picks the wrong side).
    Only fall back to nearest-edge for terminals with no clear direction."""
    if direction == "output":
        return (1.0, 0.0)
    if direction == "input":
        return (-1.0, 0.0)
    if bounds is None:
        return (1.0, 0.0)
    x1, y1, x2, y2 = bounds
    cx, cy = center
    d = {(1.0, 0.0): x2 - cx, (-1.0, 0.0): cx - x1,
         (0.0, 1.0): y2 - cy, (0.0, -1.0): cy - y1}
    return min(d, key=lambda k: d[k])


def _stub(center: Point, bounds: Rect | None, direction: str | None) -> Point:
    sx, sy = _exit_side(direction, center, bounds)
    return (center[0] + sx * _STUB, center[1] + sy * _STUB)


def _wire_edge_point(
    center: Point, bounds: Rect | None, direction: str | None
) -> Point:
    """The point on a terminal's bounds where its wire attaches — the edge in
    the wire direction (the router's ``_exit_side`` normal), not a hardcoded
    side. A coercion dot sits here: on the primitive's border where the wire
    crosses in, for a terminal of any orientation."""
    if bounds is None:
        return center
    nx, ny = _exit_side(direction, center, bounds)
    x1, y1, x2, y2 = bounds
    cx, cy = center
    if nx < 0:
        return (x1, cy)
    if nx > 0:
        return (x2, cy)
    if ny < 0:
        return (cx, y1)
    return (cx, y2)


def _wire_role(term: Terminal | None, fallback: str) -> str | None:
    """The terminal's role for wire-stub purposes: "output" (exits right) or
    "input" (enters from the left).

    ``Terminal.direction`` is CONNECTOR-PANE direction (from the VI's own
    signature), which is inverted from how the terminal behaves as a wire
    endpoint on THIS VI's own diagram: a control (``direction="input"`` — data
    flows into the VI through it) is drawn on the block diagram as a box that
    a wire leaves, i.e. it acts as an "output" stub; an indicator
    (``direction="output"``) is a box a wire arrives at, i.e. an "input"
    stub. Only ``FPTerminal`` (the VI's own boundary terminals drawn on its
    own diagram) needs this inversion — ordinary node terminals (primitives,
    SubVI calls, structure borders) already use "input"/"output" as their
    visual wire role.
    """
    if term is None:
        return fallback
    if isinstance(term, FPTerminal):
        return "input" if term.is_indicator else "output"
    return term.direction


def _term_owner_bounds(
    graph: InMemoryVIGraph, vi_name: str, layout: Layout,
) -> dict[str, Rect]:
    """raw terminal uid -> its owning node's bounds (for exit-side lookup)."""
    owner: dict[str, Rect] = {}
    for node in graph.iter_nodes(vi_name):
        nb = layout.node_bounds.get(_strip_prefix(node.id, vi_name))
        if nb is None:
            continue
        for t in node.terminals:
            owner[_strip_prefix(t.id, vi_name)] = nb
    vinode = graph.get_graph_node(vi_name)
    if vinode is not None:  # FP terminals own their own box
        for t in vinode.terminals:
            raw = _strip_prefix(t.id, vi_name)
            b = layout.node_bounds.get(raw)
            if b is not None:
                owner[raw] = b
    return owner


def _build_wire_nets(
    graph: InMemoryVIGraph,
    vi_name: str,
    layout: Layout,
    excluded: set[str],
    obstacles: list[Rect],
    scene_bounds: Rect,
) -> list[RenderWireNet]:
    # Exclude ONLY the true internal pass-throughs: a tunnel/shift-register's
    # own outer<->inner pairing (paired_id). Do NOT exclude every same-structure
    # wire — real border-to-border dataflow (e.g. the loop count N feeding a
    # pass-through tunnel to a downstream node) lives between two distinct border
    # terminals of one structure and MUST stay visible.
    paired: set[frozenset[str]] = set()
    for node in graph.iter_nodes(vi_name):
        for t in node.terminals:
            pid = getattr(t, "paired_id", None)
            if pid:
                paired.add(frozenset((t.id, pid)))

    wires = [
        w for w in graph.get_wires(vi_name, include_internal=True)
        if w.source.node_id not in excluded and w.dest.node_id not in excluded
        and frozenset((w.source.terminal_id, w.dest.terminal_id)) not in paired
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
    owner = _term_owner_bounds(graph, vi_name, layout)

    nets: list[RenderWireNet] = []
    for key in order:
        group = by_source[key]
        raw_src = _strip_prefix(key, vi_name)
        src_center = layout.terminal_centers.get(raw_src)
        if src_center is None:
            logger.debug("no geometry for source terminal %s; dropping wire(s)", key)
            continue

        source_term = graph.get_terminal(key)
        src_num = numeric_repr(source_term.lv_type if source_term else None)
        # An output exits RIGHT (dataflow is left->right), not toward whatever
        # edge its tiny termBounds happens to sit near.
        src_dir = _wire_role(source_term, "output")
        src_out = _stub(src_center, owner.get(raw_src), src_dir)

        branches: list[list[Point]] = []
        coercion_dots: list[Point] = []
        dest_types: list[LVType | None] = []
        for w in group:
            raw_dst = _strip_prefix(w.dest.terminal_id, vi_name)
            dst_center = layout.terminal_centers.get(raw_dst)
            if dst_center is None:
                logger.debug(
                    "no geometry for dest terminal %s; dropping wire",
                    w.dest.terminal_id,
                )
                continue
            dest_term = graph.get_terminal(w.dest.terminal_id)
            dest_types.append(dest_term.lv_type if dest_term else None)
            dst_dir = _wire_role(dest_term, "input")
            dst_in = _stub(dst_center, owner.get(raw_dst), dst_dir)  # enter from left
            mid = router.route(src_out, dst_in, all_points)
            # Drop redundant collinear points (the directional stubs are often
            # collinear with the first/last leg) so we don't add kinks LabVIEW
            # wouldn't draw.
            branches.append(_compress([src_center, *mid, dst_center]))

            # Coercion dot ONLY on a numeric-representation change (I32->DBL),
            # never a structural one (array->element at an auto-index tunnel).
            dst_num = numeric_repr(dest_term.lv_type if dest_term else None)
            if src_num is not None and dst_num is not None and src_num != dst_num:
                coercion_dots.append(dst_center)

        if not branches:
            continue

        carrier = _wire_carrier_type(
            source_term.lv_type if source_term else None, dest_types,
        )
        style = wire_style(carrier)
        junctions = [src_center] if len(branches) > 1 else []
        nets.append(RenderWireNet(
            source=group[0], style=style, branches=branches,
            junctions=junctions, coercion_dots=coercion_dots,
        ))
    return nets


_NUMERIC_RANK = {
    "NumInt8": 0, "NumUInt8": 1, "NumInt16": 2, "NumUInt16": 3,
    "NumInt32": 4, "NumUInt32": 5, "NumInt64": 6, "NumUInt64": 7,
    "NumFloat32": 8, "NumFloat64": 9, "NumFloatExt": 10,
    "NumComplex64": 11, "NumComplex128": 12, "NumComplexExt": 13,
}


def _arith_coercion_dots(render_nodes: list[RenderNode]) -> list[Point]:
    """Center points of coerced inputs on arithmetic primitives.

    An arith primitive unifies its numeric inputs to the widest representation;
    LabVIEW marks each narrower input with a red coercion dot. Scoped to
    ``ArithGlyph`` nodes (Add/Subtract/Multiply/Divide/...), where all numeric
    inputs genuinely coerce to one type — unlike e.g. Index Array, whose I32
    index meeting a DBL array is structural, not a coercion.
    """
    dots: list[Point] = []
    for rn in render_nodes:
        if not isinstance(rn.glyph, ArithGlyph):
            continue
        ins = [t for t in rn.terminals if t.terminal.direction == "input"]
        ranks = [
            _NUMERIC_RANK.get(numeric_repr(t.terminal.lv_type) or "") for t in ins
        ]
        present = [r for r in ranks if r is not None]
        if len(present) < 2 or len(set(present)) < 2:
            continue  # need >=2 numeric inputs of DIFFERING width
        top = max(present)
        for t, r in zip(ins, ranks):
            if r is not None and r < top:
                # The coercion dot sits on the primitive's BORDER where the wire
                # crosses in — the terminal's edge on its wire-entry side (same
                # _exit_side normal the router uses), not a hardcoded side.
                role = _wire_role(t.terminal, "input")
                dots.append(_wire_edge_point(t.center, t.bounds, role))
    return dots


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
    glyph_ctx = GlyphContext(graph=graph, vi_name=vi_name)

    render_nodes: list[RenderNode] = []
    structures: list[RenderStructure] = []
    missing: list[str] = []

    for node in all_nodes:
        if node.id in excluded:
            continue
        if node.node_type in _INTERNAL_NODE_TYPES:
            continue  # internal mux node — not a visible diagram object
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
            glyph = resolve_glyph(node, glyph_ctx)
            terminals = _render_terminals(node, layout, vi_name)
            label_visible = raw_uid not in layout.hidden_labels
            render_nodes.append(RenderNode(
                node=node, bounds=bounds, glyph=glyph, terminals=terminals,
                label_visible=label_visible,
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
            label_visible = raw_uid not in layout.hidden_labels
            fp_terminals.append(RenderFPTerminal(
                terminal=t, bounds=bounds, center=center,
                label_visible=label_visible,
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
    coercion_dots = _arith_coercion_dots(render_nodes)

    return Scene(
        bounds=scene_bounds,
        fp_terminals=fp_terminals,
        nodes=render_nodes,
        structures=structures,
        wire_nets=wire_nets,
        coercion_dots=coercion_dots,
        icon_png=layout.icon_png,
    )

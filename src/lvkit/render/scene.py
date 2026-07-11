"""Join the graph (semantics) with the layout (geometry) into a Scene.

This is the one place that reads BOTH the graph and the heap-derived
``Layout`` — everywhere else in the renderer only sees one or the other.
The result, ``Scene``, is a backend-agnostic view model: drawing code never
touches the graph or the raw heap XML again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..graph.core import InMemoryVIGraph
from ..graph.models import (
    AnyGraphNode,
    CaseStructureNode,
    ConstantNode,
    LoopNode,
    SequenceNode,
    StructureNode,
    Wire,
    WireEnd,
)
from ..models import (
    CaseFrame,
    FPTerminal,
    LVType,
    SelectorRange,
    Terminal,
    TunnelTerminal,
    _is_error_cluster,
)
from .backend import SvgBackend
from .glyph import ArithGlyph, CompoundArithGlyph, Glyph, wrap_label
from .lane_pass import BranchCtx, apply_lane_pass
from .layout import Layout, Point, Rect, build_layout
from .nodes import GlyphContext, resolve_glyph, string_const_display
from .style import WireStyle, numeric_repr, type_family, wire_style
from .wire_router import WireRouter, _compress

logger = logging.getLogger(__name__)

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
    # Frame path of the interactive-structure frame this terminal sits in
    # (derived from the node it wires to) — so an indicator placed inside a
    # case/sequence frame hides with that frame instead of showing in all of
    # them. () = base/always-visible (the usual boundary terminal).
    frame_path: FramePath = ()


# Root->leaf (raw structure uid, str(selector_value / frame index)) segments
# naming which interactive-structure frame(s) an item belongs to. () =
# base/always-visible (top level, inside a loop, or inside a flat sequence —
# only CASE structures and STACKED sequences are interactive, see
# _frame_path).
FramePath = tuple[tuple[str, str], ...]


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
    frame_path: FramePath = ()


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
    # Inner tunnels aren't drawn as glyphs today (see _structure_borders) —
    # this field exists for symmetry with the other frame-tagged dataclasses
    # and future inner-tunnel-per-frame work; it is currently always ()
    # since only outer tunnels are emitted.
    frame_path: FramePath = ()


@dataclass(frozen=True)
class RenderStructure:
    """A structure node (loop, case, sequence, in-place element)."""

    node: StructureNode
    bounds: Rect
    border_terminals: list[RenderBorderTerminal] = field(default_factory=list)
    # This structure's OWN interactive-ancestor path (computed from its
    # parent chain) — non-empty when this structure itself sits inside
    # another CASE structure's or STACKED sequence's frame, so its
    # border/chrome draws inside that ancestor's frame group instead of the
    # base layer.
    frame_path: FramePath = ()
    # Raw (VI-name-stripped) uid of this structure — the stable key into
    # Scene.default_frame / Scene.frame_values for selector chrome (case or
    # stacked sequence).
    raw_uid: str = ""
    # Flat-sequence film-strip divider x-positions (absolute), one per
    # inter-frame boundary (frames 1..N-1) — from Layout.sequence_dividers.
    # Empty for every other structure kind.
    dividers: list[float] = field(default_factory=list)


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
    frame_path: FramePath = ()


@dataclass(frozen=True)
class RenderCoercionDot:
    """One arithmetic-primitive coercion dot (scene.py::_arith_coercion_dots),
    frame-tagged like every other scene item so a dot on a hidden case
    frame's node stays hidden until that frame is selected."""

    point: Point
    frame_path: FramePath = ()


@dataclass(frozen=True)
class Scene:
    """Backend-agnostic view model for one VI's block diagram."""

    bounds: Rect
    fp_terminals: list[RenderFPTerminal] = field(default_factory=list)
    nodes: list[RenderNode] = field(default_factory=list)
    structures: list[RenderStructure] = field(default_factory=list)
    wire_nets: list[RenderWireNet] = field(default_factory=list)
    coercion_dots: list[RenderCoercionDot] = field(default_factory=list)
    icon_png: Path | None = None
    # raw struct uid (case or stacked sequence) -> default selector value
    # (str), and -> the ordered list of ALL selector values — the SVG
    # selector chrome's click-to-cycle metadata (see draw.py's lv-selector /
    # __init__.py's JS).
    default_frame: dict[str, str] = field(default_factory=dict)
    frame_values: dict[str, list[str]] = field(default_factory=dict)
    # raw struct uid -> {selector-value identity -> faithful DISPLAY label}
    # (enum item names, "No Error"/"Error", quoted strings, "a, b", "a..b").
    # Keyed by the same identity strings as ``frame_values`` so the JS/frame
    # paths stay stable; only the shown text differs.
    frame_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    # raw struct uid of ERROR-cluster case structures -> {selector value ->
    # True if that frame is the No-Error (green) case, else False (red)}.
    error_frame_no_error: dict[str, dict[str, bool]] = field(default_factory=dict)

    @property
    def obstacles(self) -> list[Rect]:
        """Every rectangle wires should avoid — node boxes AND structure
        footprints (For/While Loop, Case, Sequence, ...). A wire must not run
        over or under a structure it neither connects to nor lives inside, the
        same way it must not cross a node. This is the coarse full-diagram
        view; the router applies per-wire connect/contain/frame exemptions in
        ``_build_wire_nets`` (a structure the wire touches or is enclosed by is
        not an obstacle FOR THAT WIRE)."""
        return [n.bounds for n in self.nodes] + [s.bounds for s in self.structures]


def _strip_prefix(qualified_id: str, vi_name: str) -> str:
    prefix = f"{vi_name}::"
    if qualified_id.startswith(prefix):
        return qualified_id[len(prefix):]
    return qualified_id


def _is_stacked_sequence(node: AnyGraphNode) -> bool:
    return isinstance(node, SequenceNode) and node.node_type != "flatSequence"


def _frame_path(
    node: AnyGraphNode, by_id: dict[str, AnyGraphNode], vi_name: str,
) -> FramePath:
    """Root->leaf ``(raw_struct_uid, str(selector_value / frame index))``
    segments for each interactive-structure ancestor of ``node`` — walks the
    ``parent`` chain.

    Scope is CASE structures and STACKED sequences (roadmap #17 + this
    feature): loops and flat sequences are skipped, so their children always
    get ``()`` (base/always-visible), matching their unchanged
    show-everything rendering.
    """
    segs: list[tuple[str, str]] = []
    cur: AnyGraphNode | None = node
    while cur is not None and cur.parent:
        parent = by_id.get(cur.parent)
        if parent is None:
            break
        if isinstance(parent, CaseStructureNode) or _is_stacked_sequence(parent):
            segs.append((_strip_prefix(parent.id, vi_name), str(cur.frame)))
        cur = parent
    segs.reverse()
    return tuple(segs)


def encode_frame_path(path: FramePath) -> str:
    """``"struct=val;struct2=val2"`` — root->leaf, insertion order (already
    deterministic: built by walking the parent chain of nodes returned in
    sorted, byte-reproducible order by ``iter_nodes``)."""
    return ";".join(f"{s}={v}" for s, v in path)


def _frame_compatible(node_path: FramePath, wire_path: FramePath) -> bool:
    """Whether ``node_path`` content COULD ever be visible at the same time
    as ``wire_path`` content, for SOME assignment of every interactive
    structure's active frame.

    Two frame paths are compatible unless they disagree on the SAME
    structure's required value (e.g. case ``701`` needs ``"1"`` for one and
    ``"2"`` for the other — those two frames are mutually exclusive, so a
    node exclusive to frame 2 can never actually obstruct a wire that's
    only ever drawn in frame 1, no matter how their heap-derived bounding
    boxes happen to overlap). A structure that appears in only one of the
    two paths doesn't constrain anything — some other combination of
    clicks could still bring both into view, so it stays a real obstacle.
    """
    wire_vals = dict(wire_path)
    for struct_id, value in node_path:
        wire_value = wire_vals.get(struct_id)
        if wire_value is not None and wire_value != value:
            return False
    return True


def _is_default_visible(path: FramePath, default_frame: dict[str, str]) -> bool:
    """Whether every segment of ``path`` matches that case's default frame
    (vacuously True for ``()`` — base content stays always-visible/fatal)."""
    return all(default_frame.get(s) == v for s, v in path)


def _format_ranges(ranges: list[SelectorRange], fmt: Callable[[int], str]) -> str:
    """Render a frame's selector ranges the way LabVIEW builds the label:
    singles as ``fmt(v)``, closed ranges as ``a..b``, open ranges as ``a..``
    / ``..b``, joined with ``, `` (e.g. ``1, 3, 5..8``)."""
    parts: list[str] = []
    for r in ranges:
        if r.open_start:
            parts.append(f"..{fmt(r.end)}")
        elif r.open_end:
            parts.append(f"{fmt(r.start)}..")
        elif r.is_single:
            parts.append(fmt(r.start))
        else:
            parts.append(f"{fmt(r.start)}..{fmt(r.end)}")
    return ", ".join(parts)


def _selector_label(frame: CaseFrame, lv_type: LVType | None, is_error: bool) -> str:
    """The faithful case-selector text for one frame, by selector type:
    ``Default``; error cluster → ``No Error``/``Error``; enum → item name(s);
    integer → value(s)/range(s); string → quoted; boolean → ``True``/``False``.
    """
    sv = str(frame.selector_value)
    if is_error:
        # The error-cluster case switches on the status boolean: 0 = no error,
        # anything else is an error (LabVIEW: "No Error" / "Error", plus code
        # ranges like "Error 3..10" since 2019). The Error frame is often the
        # structure's default — LabVIEW still labels it "Error", not "Default",
        # so this precedes the plain-default branch below.
        if sv == "0":
            return "No Error"
        codes = [r for r in frame.selector_ranges if not (r.is_single and r.start == 1)]
        if codes:
            return f"Error {_format_ranges(codes, str)}"
        return "Error"
    if frame.is_default or sv == "Default":
        return "Default"
    if lv_type and lv_type.kind in ("enum", "ring") and lv_type.values \
            and frame.selector_ranges:
        int_to_name = {ev.value: name for name, ev in lv_type.values.items()}
        return _format_ranges(
            frame.selector_ranges, lambda i: int_to_name.get(i, str(i)),
        )
    if frame.selector_ranges:  # integer selector
        return _format_ranges(frame.selector_ranges, str)
    if lv_type and lv_type.underlying_type == "String":
        return f'"{sv}"'
    return sv  # boolean True/False, or an already-display token


def _frame_info(
    nodes: list[AnyGraphNode], vi_name: str, graph: InMemoryVIGraph,
) -> tuple[
    dict[str, str], dict[str, list[str]],
    dict[str, dict[str, str]], dict[str, dict[str, bool]],
]:
    """raw struct uid -> default frame value, -> the ordered list of ALL frame
    values (selector chrome click-to-cycle metadata), -> faithful DISPLAY
    labels per value, and -> for error-cluster cases, which values are the
    No-Error (green) frame.

    Cases key by selector value (with ``is_default``/first-frame fallback);
    stacked sequences key by frame index and default to frame 0 here — but
    ``build_scene`` overrides that with the heap's saved displayed frame
    (``dIdx``), the faithful initial view.
    """
    default_frame: dict[str, str] = {}
    frame_values: dict[str, list[str]] = {}
    frame_labels: dict[str, dict[str, str]] = {}
    error_no_error: dict[str, dict[str, bool]] = {}
    for node in nodes:
        if isinstance(node, CaseStructureNode) and node.frames:
            raw = _strip_prefix(node.id, vi_name)
            frame_values[raw] = [str(f.selector_value) for f in node.frames]
            shown = next((f for f in node.frames if f.is_default), node.frames[0])
            default_frame[raw] = str(shown.selector_value)
            sel_t = graph.get_terminal(node.selector_terminal) \
                if node.selector_terminal else None
            lv_type = sel_t.lv_type if sel_t else None
            is_error = bool(lv_type and _is_error_cluster(lv_type))
            frame_labels[raw] = {
                str(f.selector_value): _selector_label(f, lv_type, is_error)
                for f in node.frames
            }
            if is_error:
                # The No-Error (green) frame is the status==False case, value
                # "0"; every other frame — the "Error" frame, including when it
                # is the structure's default — is red.
                error_no_error[raw] = {
                    str(f.selector_value): str(f.selector_value) == "0"
                    for f in node.frames
                }
        elif (
            isinstance(node, SequenceNode)
            and node.node_type != "flatSequence"
            and node.frames
        ):
            raw = _strip_prefix(node.id, vi_name)
            frame_values[raw] = [str(i) for i in range(len(node.frames))]
            default_frame[raw] = "0"
    return default_frame, frame_values, frame_labels, error_no_error


# String-constant glyph text metrics — keep in sync with
# ConstantGlyph.text_size and glyph._draw_wrapped's pad/line_h.
_CONST_TEXT_SIZE = 9.0
_CONST_PAD = 2.5
_CONST_LINE_H = _CONST_TEXT_SIZE + 2.0


def _string_const_lines(display: str, box_w: float, measure: SvgBackend) -> int:
    """Number of wrapped rows a string constant's DISPLAY text occupies at our
    font in a box of width ``box_w`` — honoring explicit newlines, then greedy
    word-wrap (identical to ConstantGlyph._draw_wrapped, minus the height cap)."""
    avail_w = box_w - 2 * _CONST_PAD
    n = 0
    for seg in display.split("\n"):
        if not seg:
            n += 1
            continue
        if avail_w <= 0:
            n += 1
            continue
        n += max(1, len(wrap_label(seg, avail_w, measure, _CONST_TEXT_SIZE, 100000)))
    return max(1, n)


def _trim_string_const_geom(
    graph: InMemoryVIGraph, vi_name: str, layout: Layout,
) -> tuple[dict[str, Rect], dict[str, Point]]:
    """Trimmed geometry for string-constant boxes (task #27). Anchored at the
    heap TOP-LEFT: x1/y1 and width are untouched; ONLY the bottom edge (y2)
    moves UP to the wrapped-text height at our font (never past the heap
    bottom — we only trim empty vertical space, never grow). The oversized
    heap boxes (LabVIEW's own larger font) otherwise make some interior wires
    unroutable.

    Returns (raw uid -> trimmed node bounds, raw uid -> its output terminal
    center clamped onto the shrunk box) so the router obstacle, the drawn box,
    and the wire attach point all use the same shrunk rect."""
    measure = SvgBackend()
    bounds_out: dict[str, Rect] = {}
    centers_out: dict[str, Point] = {}
    for node in graph.iter_nodes(vi_name):
        if not isinstance(node, ConstantNode) or type_family(node.lv_type) != "string":
            continue
        raw = _strip_prefix(node.id, vi_name)
        b = layout.node_bounds.get(raw)
        if b is None:
            continue
        x1, y1, x2, y2 = b
        raw_val = node.raw_value if node.value is None else node.value
        display = string_const_display(raw_val)
        n = _string_const_lines(display, x2 - x1, measure)
        needed = 2 * _CONST_PAD + n * _CONST_LINE_H
        new_bottom = min(y2, y1 + needed)
        if new_bottom >= y2:
            continue  # nothing to trim
        bounds_out[raw] = (x1, y1, x2, new_bottom)
        # The heap terminal center sits at the box's vertical MIDDLE and does
        # NOT follow the box, so after trimming it would dangle below the
        # shrunk box. Re-anchor it to the NEW box: right edge (x2), vertical
        # middle of the shrunk rect — so the wire attaches to the box's right
        # edge, not empty space. (Output exits right, so x2 is the attach x.)
        centers_out[raw] = (x2, (y1 + new_bottom) / 2)
    return bounds_out, centers_out


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
        if glyph_kind is None:
            # A plain data tunnel (case/sequence border passthrough): LabVIEW
            # draws it as a solid block in the WIRE TYPE COLOR, never a flat
            # gray/white box. Anything else the graph models on the border is
            # already a real, wireable dataflow terminal, so default to tunnel.
            glyph_kind = "tunnel"
        # Tunnels, shift registers, and the selector carry the WIRE TYPE COLOR
        # in LabVIEW (orange DBL, blue I32, mustard error, ...) — not gray/white.
        color = (wire_style(t.lv_type).color
                 if glyph_kind in ("autoindex", "tunnel", "sr_down", "sr_up",
                                   "selector")
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


def _exit_side(
    direction: str | None,
    center: Point,
    bounds: Rect | None,
    border: bool = False,
    toward: Point | None = None,
) -> Point:
    """Unit normal a wire leaves/enters a terminal on.

    LabVIEW dataflow runs left→right: a node's OUTPUT exits to the RIGHT and an
    INPUT is entered from the LEFT — regardless of where the tiny clickable
    termBounds actually sits (a primitive's output termBounds is near the node
    centre, not its visual apex, so a nearest-edge guess picks the wrong side).
    Only fall back to nearest-edge for terminals with no clear direction.

    ``border=True`` is for a STRUCTURE BORDER terminal (tunnel / shift
    register) — those don't follow left→right dataflow direction at all,
    they sit ON one edge of their OWNING STRUCTURE (``bounds`` must be the
    structure's own bounds) and exit perpendicular to it: top/bottom edge →
    vertical normal, left/right edge → horizontal normal.

    A border terminal has an INNER face (toward the structure interior) and
    an OUTER face (away from it) on that same edge. When ``toward`` (the
    wire's OTHER endpoint) is given, the normal is chosen by which face the
    other endpoint is on: an endpoint INSIDE the structure attaches on the
    INNER face (normal points inward, wire stays in the frame); an endpoint
    OUTSIDE attaches on the OUTER face. Without ``toward`` the plain
    nearest-edge (outward) normal is returned.
    """
    if not border:
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
    outward = min(d, key=lambda k: d[k])
    if border and toward is not None:
        inside = x1 <= toward[0] <= x2 and y1 <= toward[1] <= y2
        if inside:
            return (-outward[0], -outward[1])  # inner face: point into the frame
        return outward  # outer face
    return outward


def _stub(
    center: Point,
    bounds: Rect | None,
    direction: str | None,
    border: bool = False,
    obstacles: list[Rect] | None = None,
    owner: Rect | None = None,
    toward: Point | None = None,
) -> Point:
    """The point a wire's own exit/entry stub starts/ends at, ``_STUB`` px
    out from ``center`` along its edge normal.

    ``toward`` (the wire's OTHER endpoint) selects a border terminal's
    inner-vs-outer face — see ``_exit_side``.

    When ``obstacles`` is given (the router's own obstacle list), the stub
    length backs off (9, 7, 5, 3, 1, 0 px) if the full-length stub would
    land inside a DIFFERENT node's box — two sibling nodes occasionally sit
    close enough (even overlapping, e.g. an array/cluster shell drawn
    tightly around its element constants) that a full-length stub pokes
    into the neighbor, making that endpoint geometrically unreachable
    without crossing it. Backing off keeps the stub (and so the routed
    wire) outside every node but its own; at 0px it's just the terminal
    center, always valid.
    """
    sx, sy = _exit_side(direction, center, bounds, border, toward)
    if obstacles is None:
        return (center[0] + sx * _STUB, center[1] + sy * _STUB)
    for length in (_STUB, 7.0, 5.0, 3.0, 1.0, 0.0):
        pt = (center[0] + sx * length, center[1] + sy * length)
        if not _point_in_other_obstacle(pt, obstacles, owner):
            return pt
    return center


def _point_in_other_obstacle(
    pt: Point, obstacles: list[Rect], owner: Rect | None,
) -> bool:
    x, y = pt
    for obstacle in obstacles:
        if obstacle == owner:
            continue
        bx1, by1, bx2, by2 = obstacle
        if bx1 + 1 < x < bx2 - 1 and by1 + 1 < y < by2 - 1:
            return True
    return False


def _wire_edge_point(
    center: Point, bounds: Rect | None, direction: str | None, border: bool = False,
) -> Point:
    """The point on a terminal's bounds where its wire attaches — the edge in
    the wire direction (the router's ``_exit_side`` normal), not a hardcoded
    side. A coercion dot sits here: on the primitive's border where the wire
    crosses in, for a terminal of any orientation."""
    if bounds is None:
        return center
    nx, ny = _exit_side(direction, center, bounds, border)
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


def _wire_path(
    w: Wire, graph: InMemoryVIGraph, by_id: dict[str, AnyGraphNode], vi_name: str,
) -> FramePath:
    """The frame path a wire belongs to, from its endpoints' nodes.

    A wire touching a case structure's or stacked sequence's INNER tunnel
    terminal belongs to that tunnel's owning frame (the tunnel's node IS the
    structure itself, per construction.py — its own ``_frame_path`` gives
    the structure's ANCESTOR path, to which we append this tunnel's own
    frame segment). A
    wire touching an ordinary node belongs to that node's frame path. The
    most-specific (longest/deepest) endpoint wins — wires never span two
    sibling frames, they route through the border tunnel, so the shallower
    endpoint (the tunnel/structure itself) is always a prefix of the other.
    """
    cands: list[FramePath] = []
    for end in (w.source, w.dest):
        node = by_id.get(end.node_id)
        if node is None:
            continue
        term = graph.get_terminal(end.terminal_id)
        if (
            isinstance(term, TunnelTerminal)
            and term.boundary == "inner"
            and term.frame is not None
        ):
            base = _frame_path(node, by_id, vi_name)
            cands.append(base + ((_strip_prefix(node.id, vi_name), str(term.frame)),))
        else:
            cands.append(_frame_path(node, by_id, vi_name))
    return max(cands, key=len) if cands else ()


def _endpoint_containers(
    end: WireEnd, graph: InMemoryVIGraph,
    by_id: dict[str, AnyGraphNode], vi_name: str,
) -> list[str]:
    """Raw uids of the structures whose frame this endpoint lives IN, ordered
    innermost (leaf) → outermost (root).

    An endpoint lives in a structure's frame when:
    * its node is nested inside (the structure is an ANCESTOR via the parent
      chain — interior nodes and arbitrary nesting), or
    * it is one of the structure's own INNER tunnel/shift-register/selector
      terminals (``boundary == "inner"`` — the inner face is on the frame
      side). Inner and outer tunnels often share the same center point, so
      inner-vs-outer is read from the graph terminal, never from geometry.
    """
    node = by_id.get(end.node_id)
    if node is None:
        return []
    out: list[str] = []
    term = graph.get_terminal(end.terminal_id)
    if (
        isinstance(term, TunnelTerminal)
        and term.boundary == "inner"
        and isinstance(node, StructureNode)
    ):
        out.append(_strip_prefix(node.id, vi_name))  # inner face -> inside this frame
    cur = by_id.get(node.parent) if node.parent else None
    while cur is not None:
        if isinstance(cur, StructureNode):
            out.append(_strip_prefix(cur.id, vi_name))
        cur = by_id.get(cur.parent) if cur.parent else None
    return out


def _wire_exempt_structures(
    w: Wire, graph: InMemoryVIGraph, by_id: dict[str, AnyGraphNode], vi_name: str,
) -> frozenset[str]:
    """Raw uids of the structures a wire may legitimately overlap (NOT treated
    as obstacles for it) — every structure EITHER endpoint lives inside (the
    CONTAINS relationship; see ``_endpoint_containers``).

    A structure the wire merely CONNECTS TO on its OUTER face is NOT exempt:
    its interior stays a solid obstacle, so an EXTERNAL wire approaches the
    tunnel from outside and stops at the border (Correction 2 attaches such a
    wire on the OUTER face, reachable by hugging the exterior) instead of
    cutting across the whole box.
    """
    return frozenset(
        _endpoint_containers(w.source, graph, by_id, vi_name)
    ) | frozenset(
        _endpoint_containers(w.dest, graph, by_id, vi_name)
    )


def _innermost_common_container(
    w: Wire, graph: InMemoryVIGraph, by_id: dict[str, AnyGraphNode], vi_name: str,
) -> str | None:
    """Raw uid of the INNERMOST structure that contains BOTH endpoints, or
    None if the wire is not fully contained (e.g. external -> outer tunnel).

    A fully-contained wire must be CONFINED to this structure's interior so
    the router can't path out of the frame and back to dodge an interior
    obstacle. ``_endpoint_containers`` is ordered leaf→root, so the first
    container common to both endpoints is the deepest (nesting handled).
    """
    src = _endpoint_containers(w.source, graph, by_id, vi_name)
    dst = set(_endpoint_containers(w.dest, graph, by_id, vi_name))
    for uid in src:
        if uid in dst:
            return uid
    return None


def _build_wire_nets(
    graph: InMemoryVIGraph,
    vi_name: str,
    layout: Layout,
    render_nodes: list[RenderNode],
    render_structures: list[RenderStructure],
    scene_bounds: Rect,
    by_id: dict[str, AnyGraphNode],
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
        if frozenset((w.source.terminal_id, w.dest.terminal_id)) not in paired
    ]

    # Group by (source terminal, frame path) — a fan-out net whose branches
    # land in different case/stacked-sequence frames splits into one
    # RenderWireNet per frame
    # (junctions recomputed per group below), so hidden-frame branches don't
    # leak into the base/other-frame net.
    by_group: dict[tuple[str, FramePath], list[Wire]] = {}
    order: list[tuple[str, FramePath]] = []
    for w in wires:
        path = _wire_path(w, graph, by_id, vi_name)
        key = (w.source.terminal_id, path)
        if key not in by_group:
            by_group[key] = []
            order.append(key)
        by_group[key].append(w)

    all_points = list(layout.terminal_centers.values())
    owner = _term_owner_bounds(graph, vi_name, layout)

    # Body bounds of every structure, keyed by raw uid — the CONFINEMENT rect
    # for a wire fully contained by it (its interior; the selector banner is
    # above the body and stays outside, so a contained wire can't stray into
    # its own container's banner either).
    struct_body_by_uid = {rs.raw_uid: rs.bounds for rs in render_structures}

    # Obstacles are NODES *and* STRUCTURE footprints (each structure's true heap
    # bounds — the selector sits INSIDE those bounds, so there is no separate
    # banner rect) — a wire must not run over or under a For/While Loop, Case, or
    # Sequence box it has nothing to do with, exactly as it must not cross a node.
    # Routers are built per (frame path, exempt-structure set, confinement rect):
    #   * frame path — a node/structure only in a mutually-exclusive
    #     case/stacked-sequence frame is never on screen at the same time as a
    #     wire outside that frame, so it can't obstruct it even where their
    #     heap boxes overlap (frames reuse screen space). See _frame_compatible.
    #   * exempt structures — CONTAINS-ONLY: a structure is dropped from the
    #     obstacle set only when the wire LIVES INSIDE it (an endpoint node is
    #     nested in it, or the endpoint is its own inner tunnel terminal). A
    #     structure the wire merely connects to on its OUTER face is NOT exempt;
    #     its interior stays a hard obstacle. See _wire_exempt_structures.
    #   * confinement — a fully-contained wire's router is bounded to its
    #     innermost container's interior, so A* can't leave the frame to
    #     detour (see _innermost_common_container).
    # Each key takes few distinct values per VI, so the cache keeps routing
    # cheap despite the per-wire exemption/confinement.
    node_bounds_by_path: dict[FramePath, list[Rect]] = {}
    structs_by_path: dict[FramePath, list[tuple[str, Rect]]] = {}
    routers: dict[
        tuple[FramePath, frozenset[str], Rect | None],
        tuple[WireRouter, list[Rect]],
    ] = {}

    def _router_for(
        path: FramePath, exempt: frozenset[str], confine: Rect | None,
    ) -> tuple[WireRouter, list[Rect]]:
        cached = routers.get((path, exempt, confine))
        if cached is not None:
            return cached
        if path not in node_bounds_by_path:
            node_bounds_by_path[path] = [
                rn.bounds for rn in render_nodes
                if _frame_compatible(rn.frame_path, path)
            ]
            structs_by_path[path] = [
                (rs.raw_uid, rs.bounds)
                for rs in render_structures
                if _frame_compatible(rs.frame_path, path)
            ]
        obstacles = node_bounds_by_path[path] + [
            b for uid, b in structs_by_path[path] if uid not in exempt
        ]
        entry = (WireRouter(obstacles, confine or scene_bounds), obstacles)
        routers[(path, exempt, confine)] = entry
        return entry

    nets: list[RenderWireNet] = []
    # (net index in `nets`, branch index) -> the obstacle/owner/confinement
    # context that branch was routed against, so the lane pass can reject any
    # offset that would violate the router's own obstacle/containment rules.
    branch_ctx: dict[tuple[int, int], BranchCtx] = {}
    for key in order:
        src_key, path = key
        group = by_group[key]
        raw_src = _strip_prefix(src_key, vi_name)
        src_center = layout.terminal_centers.get(raw_src)
        if src_center is None:
            logger.debug(
                "no geometry for source terminal %s; dropping wire(s)", src_key,
            )
            continue

        source_term = graph.get_terminal(src_key)
        src_num = numeric_repr(source_term.lv_type if source_term else None)
        src_owner = owner.get(raw_src)
        # A structure border terminal (tunnel/shift-register) exits
        # perpendicular to whichever edge of its OWNING STRUCTURE it sits
        # on — NOT forced left->right like an ordinary node terminal.
        src_border = isinstance(source_term, TunnelTerminal)
        # An output exits RIGHT (dataflow is left->right), not toward whatever
        # edge its tiny termBounds happens to sit near.
        src_dir = _wire_role(source_term, "output")

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
            exempt = _wire_exempt_structures(w, graph, by_id, vi_name)
            confine_uid = _innermost_common_container(w, graph, by_id, vi_name)
            confine = struct_body_by_uid.get(confine_uid) if confine_uid else None
            router, obstacles = _router_for(path, exempt, confine)
            dest_term = graph.get_terminal(w.dest.terminal_id)
            dest_types.append(dest_term.lv_type if dest_term else None)
            dst_owner = owner.get(raw_dst)
            dst_border = isinstance(dest_term, TunnelTerminal)
            dst_dir = _wire_role(dest_term, "input")
            # A border terminal's stub direction picks the INNER vs OUTER face
            # by which side of the structure the OTHER endpoint sits on
            # (toward=). A plain node's stub is direction-based; toward is
            # ignored, so src_out is identical across branches there.
            src_out = _stub(
                src_center, src_owner, src_dir, src_border, obstacles, src_owner,
                toward=dst_center,
            )
            # enter from the left, unless it's a border terminal (perpendicular
            # to its structure's edge, inner/outer face toward the source)
            dst_in = _stub(
                dst_center, dst_owner, dst_dir, dst_border, obstacles, dst_owner,
                toward=src_center,
            )
            # The router's endpoint-owner exemption lets a wire pass through
            # exactly its own node's box near that endpoint. A TUNNEL/border
            # endpoint's "owner" is the whole STRUCTURE — but its contact
            # point sits ON the border (outside the interior test) and, for an
            # external wire, the interior must stay blocked, so we must NOT
            # hand the router a structure-sized exemption. Pass None for
            # border endpoints (an internal wire's structure is already
            # exempt via _wire_exempt_structures, so it isn't an obstacle
            # anyway); plain node endpoints keep their own-box exemption.
            src_route_owner = None if src_border else src_owner
            dst_route_owner = None if dst_border else dst_owner
            mid = router.route(
                src_out, dst_in, all_points, src_route_owner, dst_route_owner,
            )
            # Drop redundant collinear points (the directional stubs are often
            # collinear with the first/last leg) so we don't add kinks LabVIEW
            # wouldn't draw.
            branches.append(_compress([src_center, *mid, dst_center]))
            # Same obstacle/owner/confinement this branch was routed against,
            # keyed for the lane pass. net_index = len(nets) (this net is
            # appended after the group loop, so it will occupy that slot).
            branch_ctx[(len(nets), len(branches) - 1)] = BranchCtx(
                obstacles=tuple(obstacles),
                owners=tuple(o for o in (src_owner, dst_owner) if o is not None),
                confine=confine,
                src_border=src_border,
                dst_border=dst_border,
            )

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
        # Junction dots are populated below by the lane-assignment pass,
        # from the REBUILT geometry (a real shared trunk run), not guessed
        # here at the source stub.
        nets.append(RenderWireNet(
            source=group[0], style=style, branches=branches,
            coercion_dots=coercion_dots, frame_path=path,
        ))

    # Post-routing interval-coloring lane-assignment pass (see lane_pass.py):
    # nudges only the segments that genuinely conflict with a DIFFERENT
    # net's segment on the same track into separate lanes; same-net branches
    # keep sharing a lane (and get a junction dot where they diverge).
    # Routing itself (above) is unchanged -- this only repositions the
    # already-routed polylines.
    return apply_lane_pass(nets, branch_ctx)


_NUMERIC_RANK = {
    "NumInt8": 0, "NumUInt8": 1, "NumInt16": 2, "NumUInt16": 3,
    "NumInt32": 4, "NumUInt32": 5, "NumInt64": 6, "NumUInt64": 7,
    "NumFloat32": 8, "NumFloat64": 9, "NumFloatExt": 10,
    "NumComplex64": 11, "NumComplex128": 12, "NumComplexExt": 13,
}


def _arith_coercion_dots(render_nodes: list[RenderNode]) -> list[RenderCoercionDot]:
    """Coerced-input dots on arithmetic primitives, tagged with each dot's
    owning node's frame path (see ``RenderCoercionDot``).

    An arith primitive unifies its numeric inputs to the widest representation;
    LabVIEW marks each narrower input with a red coercion dot. Scoped to the
    numeric-unifying glyph families — ``ArithGlyph`` (Add/Subtract/Multiply/
    Divide/... and the six comparisons) and ``CompoundArithGlyph`` (Compound
    Arithmetic, which unifies its N inputs the same way) — where all numeric
    inputs genuinely coerce to one type. Deliberately NOT applied to boxed
    primitives whose inputs differ structurally rather than by coercion (e.g.
    Index Array's I32 index meeting a DBL array, or Scale By Power of 2's
    integer exponent), which would mis-flag a real narrower-but-required input.
    """
    dots: list[RenderCoercionDot] = []
    for rn in render_nodes:
        if not isinstance(rn.glyph, (ArithGlyph, CompoundArithGlyph)):
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
                point = _wire_edge_point(t.center, t.bounds, role)
                dots.append(RenderCoercionDot(point=point, frame_path=rn.frame_path))
    return dots


def _drawn_bounds(
    nodes: list[RenderNode],
    structures: list[RenderStructure],
    fp_terminals: list[RenderFPTerminal],
    wire_nets: list[RenderWireNet],
    pad: float = 30.0,
) -> Rect | None:
    """Padded bbox over everything actually DRAWN — rendered node/structure/
    border-terminal/FP rects plus every routed wire point. The SVG viewBox.
    Excludes unrendered layout rects (see ``layout.scene_bounds``), so the view
    crops to real content. Returns None when nothing is drawn."""
    xs: list[float] = []
    ys: list[float] = []
    for rn in nodes:
        xs += [rn.bounds[0], rn.bounds[2]]
        ys += [rn.bounds[1], rn.bounds[3]]
    for rs in structures:
        for b in (rs.bounds, *(bt.bounds for bt in rs.border_terminals)):
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
    for fp in fp_terminals:
        xs += [fp.bounds[0], fp.bounds[2]]
        ys += [fp.bounds[1], fp.bounds[3]]
    for net in wire_nets:
        for branch in net.branches:
            for x, y in branch:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


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
    # Shrink oversized string-constant boxes to their wrapped-text height
    # (top-left anchored) BEFORE anything consumes geometry, so the drawn box,
    # the router obstacle, and the wire attach point all use the trimmed rect.
    trim_bounds, trim_centers = _trim_string_const_geom(graph, vi_name, layout)
    if trim_bounds:
        layout = replace(
            layout,
            node_bounds={**layout.node_bounds, **trim_bounds},
            terminal_centers={**layout.terminal_centers, **trim_centers},
        )
    all_nodes = graph.iter_nodes(vi_name)
    by_id: dict[str, AnyGraphNode] = {n.id: n for n in all_nodes}
    default_frame, frame_values, frame_labels, error_frame_no_error = _frame_info(
        all_nodes, vi_name, graph,
    )
    glyph_ctx = GlyphContext(graph=graph, vi_name=vi_name)

    render_nodes: list[RenderNode] = []
    structures: list[RenderStructure] = []
    missing: list[str] = []

    for node in all_nodes:
        if node.node_type in _INTERNAL_NODE_TYPES:
            continue  # internal mux node — not a visible diagram object
        raw_uid = _strip_prefix(node.id, vi_name)
        bounds = layout.node_bounds.get(raw_uid)
        fp_path = _frame_path(node, by_id, vi_name)
        if bounds is None:
            # Base/default-frame content stays fatal (fail-closed); a
            # non-default (currently-hidden) case/stacked-sequence frame's
            # missing geometry is best-effort — skip it rather than
            # declining the whole VI.
            if _is_default_visible(fp_path, default_frame):
                missing.append(node.id)
            else:
                logger.debug(
                    "hidden-frame element %s missing geometry; skipping", node.id,
                )
            continue
        if isinstance(node, StructureNode):
            structures.append(RenderStructure(
                node=node,
                bounds=bounds,
                border_terminals=_structure_borders(node, layout, vi_name),
                frame_path=fp_path,
                raw_uid=raw_uid,
                dividers=layout.sequence_dividers.get(raw_uid, []),
            ))
        else:
            glyph = resolve_glyph(node, glyph_ctx)
            terminals = _render_terminals(node, layout, vi_name)
            label_visible = raw_uid not in layout.hidden_labels
            render_nodes.append(RenderNode(
                node=node, bounds=bounds, glyph=glyph, terminals=terminals,
                label_visible=label_visible, frame_path=fp_path,
            ))

    fp_terminals: list[RenderFPTerminal] = []
    vi_node = graph.get_graph_node(vi_name)
    if vi_node is not None:
        # Frame membership for FP terminals (they carry no parent/frame): an
        # indicator/control placed INSIDE a case/sequence frame wires to a node
        # in that frame — inherit that node's (deepest) frame path so it hides
        # with the frame instead of rendering in every frame.
        fp_ids = {t.id for t in vi_node.terminals if isinstance(t, FPTerminal)}
        fp_frame: dict[str, FramePath] = {}
        for w in graph.get_wires(vi_name, include_internal=True):
            for end, other in ((w.source, w.dest), (w.dest, w.source)):
                if end.terminal_id not in fp_ids:
                    continue
                node = by_id.get(other.node_id)
                if node is None:
                    continue
                cand = _frame_path(node, by_id, vi_name)
                if len(cand) > len(fp_frame.get(end.terminal_id, ())):
                    fp_frame[end.terminal_id] = cand

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
                label_visible=label_visible, frame_path=fp_frame.get(t.id, ()),
            ))

    if missing:
        logger.warning(
            "render_vi(%s): missing geometry for %d id(s), declining to "
            "render: %s", vi_name, len(missing), missing,
        )
        return None

    scene_bounds = layout.scene_bounds()

    wire_nets = _build_wire_nets(
        graph, vi_name, layout, render_nodes, structures, scene_bounds, by_id,
    )
    coercion_dots = _arith_coercion_dots(render_nodes)

    # Draw structures OUTERMOST-FIRST (by containment depth). A For Loop border
    # paints its interior with the canvas color (the cascade-card look), so if a
    # containing loop were drawn AFTER a structure inside it (UID order can do
    # that), it would paint OVER that structure — box, selector, contents and
    # all. Sorting by ancestor depth keeps every container behind its contents.
    # Stable sort preserves the deterministic UID order within a depth.
    def _depth(node: AnyGraphNode) -> int:
        d, cur = 0, node.parent
        while cur:
            parent = by_id.get(cur)
            if parent is None:
                break
            d += 1
            cur = parent.parent
        return d

    structures.sort(key=lambda s: _depth(s.node))

    # A stacked sequence saves its displayed frame in the heap (``dIdx``); open
    # on that faithful frame, not frame 0 (often an empty setup frame).
    for node in all_nodes:
        if isinstance(node, SequenceNode) and node.node_type != "flatSequence":
            raw = _strip_prefix(node.id, vi_name)
            shown = layout.sequence_shown_frame.get(raw)
            if shown is not None and str(shown) in frame_values.get(raw, []):
                default_frame[raw] = str(shown)

    # Tight viewBox: the bbox of everything actually DRAWN (rendered elements +
    # routed wires), padded. ``layout.scene_bounds()`` is computed from raw
    # layout rects that include UNRENDERED stray elements, which inflate the
    # canvas with empty margin. This crops the SVG view to the real content.
    # Computed HERE, after routing, so the router still confined wires to the
    # loose ``scene_bounds`` — routes are byte-identical; only the view crops in.
    view_bounds = _drawn_bounds(
        render_nodes, structures, fp_terminals, wire_nets,
    ) or scene_bounds

    return Scene(
        bounds=view_bounds,
        fp_terminals=fp_terminals,
        nodes=render_nodes,
        structures=structures,
        wire_nets=wire_nets,
        coercion_dots=coercion_dots,
        icon_png=layout.icon_png,
        default_frame=default_frame,
        frame_values=frame_values,
        frame_labels=frame_labels,
        error_frame_no_error=error_frame_no_error,
    )

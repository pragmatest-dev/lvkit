"""Scene -> Backend ops.

Node glyphs are resolved once, in the graph-driven join (``scene.py``, via
``nodes.py``'s resolver chain) — ``draw_node`` here just replays whatever
``Glyph`` the scene already carries. Structures, FP terminals, wires, and
coercion dots are still a direct dispatch on graph-sourced kind/type (P2 is
node glyphs only — see DESIGN.md's phasing).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..graph.models import (
    AnyGraphNode,
    CaseStructureNode,
    DisableStructureNode,
    EventStructureNode,
    FormulaNode,
    LocalVariableNode,
    PrimitiveNode,
    SequenceNode,
    VINode,
)
from ..graph.op_walk import _terminal_display_name
from ..models import FPTerminal, LVType, LVTypeKind, Terminal
from ..primitive_resolver import get_resolver as get_prim_resolver
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .backend import Backend, Point
from .glyph import (
    BundleByNameGlyph,
    BundleGlyph,
    CenteredSvgGlyph,
    ClusterConstantGlyph,
    ConstantGlyph,
    ErrorClusterGlyph,
    EventDataGlyph,
    FormulaNodeGlyph,
    IconImageGlyph,
    InlineSvgGlyph,
    UnbundleGlyph,
    VariantGlyph,
    WrappedBoxGlyph,
    fit_label,
    fit_wrapped,
    wrap_label,
)
from .glyphs.terminals.factory import border_terminal_glyph
from .nodes import _CLUSTER_MUX_TYPES, mux_display_name, mux_doc_url
from .scene import (
    RenderBorderTerminal,
    RenderLabel,
    RenderNode,
    RenderStructure,
    RenderTerminal,
    RenderWireNet,
    Scene,
    _exit_side,
    _wire_edge_point,
    _wire_role,
)
from .style import (
    DEFAULT_THEME,
    Theme,
    clean_help_text,
    lv_type_label,
    numeric_sample,
    type_family,
    type_repr,
    wire_style,
)

# LabVIEW's array-control terminal icon always shows a 3-row index display
# (i / j / k) as fixed chrome, independent of the array's real dimensionality.
_ARRAY_INDEX_ROWS = 3


def _is_interactive_structure(node: object) -> bool:
    """Case structures, Disable structures, Event structures, and STACKED
    sequences get selector chrome + per-frame ``lv-frame``/``lv-selector``
    groups; flat sequences (film-strip, every frame always visible) and
    loops do not."""
    return isinstance(
        node,
        (CaseStructureNode, DisableStructureNode, EventStructureNode),
    ) or (isinstance(node, SequenceNode) and node.node_type != "flatSequence")


# A LabVIEW "Not" bubble — a small open circle drawn AT an inverted terminal,
# on the wire side (the side its wire exits/enters), straddling the node's
# border the same way a real "Not" gate bubble sits on a boolean primitive.
_INVERT_BUBBLE_R = 2.75


def _draw_invert_bubbles(node: RenderNode, backend: Backend, theme: Theme) -> None:
    """Draw a bubble at every terminal with ``Terminal.inverted`` set — today
    only Compound Arithmetic sets it, but this is generic over any terminal
    (input or output) so it stays correct if another node type starts
    setting the flag."""
    for rt in node.terminals:
        if not rt.terminal.inverted:
            continue
        role = _wire_role(rt.terminal, "output")
        edge = _wire_edge_point(rt.center, rt.bounds, role)
        nx, ny = _exit_side(role, rt.center, rt.bounds)
        cx = edge[0] + nx * _INVERT_BUBBLE_R
        cy = edge[1] + ny * _INVERT_BUBBLE_R
        backend.circle(
            cx,
            cy,
            _INVERT_BUBBLE_R,
            fill=theme.canvas,
            stroke=theme.prim_stroke,
            stroke_width=1.0,
        )


# A Formula Node's variables are named tunnels on the box border. LabVIEW lays
# them out as two justified COLUMNS — INPUTS flush against the left edge, OUTPUTS
# flush against the right edge (grounded: the heap already clusters input centers
# near the left edge and output centers near the right, and every terminal
# carries a reliable ``direction``). So the side comes from direction (never from
# a nearest-edge guess, which mis-files a top-corner input onto the top edge),
# the vertical position from the terminal's real heap ``center``, and the box
# sits INSIDE the border (its outer edge ON the border line — the border never
# bisects it). The script text is then inset past both columns so code and
# tunnels never overlap. Each box is canvas-filled with a wire-type-colored
# outline + name (a Formula Node's ``vblName`` fontcolor IS its type color, the
# same table ``wire_style`` encodes; cf. ``draw_fp_terminal``).
_FORMULA_TUNNEL_MIN = 12.0  # min box width (short names / no name)
_FORMULA_TUNNEL_PAD_X = 3.0  # horizontal padding around the name
_FORMULA_TUNNEL_PAD_Y = 1.5  # vertical padding around the name
_FORMULA_TUNNEL_GAP = 3.0  # gap between a tunnel column and the script
_FORMULA_TUNNEL_TEXT_SIZE = 7.0


def _draw_formula_node(
    node: RenderNode,
    bounds: tuple[float, float, float, float],
    backend: Backend,
    theme: Theme,
) -> None:
    """Draw a Formula Node: the box, its C source (inset past the tunnel
    columns), then the named input/output tunnels justified to the borders."""
    glyph = node.glyph
    if not isinstance(glyph, FormulaNodeGlyph):
        return
    x1, y1, x2, y2 = bounds
    size = _FORMULA_TUNNEL_TEXT_SIZE
    box_h = size + 2 * _FORMULA_TUNNEL_PAD_Y

    def width(name: str) -> float:
        w = backend.measure_text(name, size) if name else 0.0
        return max(_FORMULA_TUNNEL_MIN, w + 2 * _FORMULA_TUNNEL_PAD_X)

    inputs = [rt for rt in node.terminals if rt.terminal.direction != "output"]
    outputs = [rt for rt in node.terminals if rt.terminal.direction == "output"]
    left_w = max((width(rt.terminal.name or "") for rt in inputs), default=0.0)
    right_w = max((width(rt.terminal.name or "") for rt in outputs), default=0.0)

    glyph.draw_box(backend, bounds, theme)
    left_inset = glyph.pad + (left_w + _FORMULA_TUNNEL_GAP if inputs else 0.0)
    right_inset = glyph.pad + (right_w + _FORMULA_TUNNEL_GAP if outputs else 0.0)
    glyph.draw_script(backend, bounds, theme, left_inset, right_inset)

    for rt in inputs:
        w = width(rt.terminal.name or "")
        _draw_formula_tunnel(backend, theme, rt, x1, x1 + w, box_h, size, y1, y2)
    for rt in outputs:
        w = width(rt.terminal.name or "")
        _draw_formula_tunnel(backend, theme, rt, x2 - w, x2, box_h, size, y1, y2)


def _draw_formula_tunnel(
    backend: Backend,
    theme: Theme,
    rt: RenderTerminal,
    bx1: float,
    bx2: float,
    box_h: float,
    size: float,
    y1: float,
    y2: float,
) -> None:
    """One tunnel box (edges ``bx1``/``bx2`` already justified to a border) at
    the terminal's heap height, clamped inside the box."""
    color = wire_style(rt.terminal.lv_type, theme).color
    cy = min(max(rt.center[1], y1 + box_h / 2), y2 - box_h / 2)
    backend.rect(
        bx1,
        cy - box_h / 2,
        bx2,
        cy + box_h / 2,
        fill=theme.canvas,
        stroke=color,
        stroke_width=1.0,
    )
    name = rt.terminal.name or ""
    if name:
        backend.text(
            (bx1 + bx2) / 2,
            cy + size * 0.34,
            name,
            size,
            anchor="middle",
            fill=color,
        )


# A primitive whose glyph is drawn as one of these keeps its own aspect ratio
# (a real extracted raster icon or a declared/procedural SVG designed for a
# fixed shape), or is a cluster-mux drawer whose heap ``bounds`` IS its true
# drawn size (like a subVI/constant) rather than a shape recovered from
# terminal-span union — those are NOT resized to the terminal span.
#
# The cluster-mux glyphs specifically MUST be exempted: their AGGREGATE
# terminal's termBounds is a real on-diagram wire endpoint for a normal
# Bundle/Unbundle, but for an Event Structure's data/filter node (same
# ``eventDataNode``/nMux shape, see parser/node_types.py) that aggregate face
# is never wired/drawn — LabVIEW stores it as a huge negative sentinel rect
# (e.g. ``(-531, -471, -512, -471)``), which blew the terminal-span union out
# to hundreds of units and rendered as an oversized, mispositioned box
# (task #75) before this exemption. ``EventDataGlyph`` (the Event Data/Filter
# Node's own white named-rows glyph, replacing the tan ``BundleByNameGlyph``
# it used to borrow) inherits the exact same exemption for the same reason.
_OWN_ASPECT_GLYPHS = (
    IconImageGlyph,
    InlineSvgGlyph,
    CenteredSvgGlyph,
    BundleByNameGlyph,
    BundleGlyph,
    UnbundleGlyph,
    EventDataGlyph,
)

# Floor for the recovered icon footprint: a unary primitive (one input, one
# output at the same height) has a terminal-span that collapses to a few px in
# one axis; don't let the glyph shrink below this (centered on the span).
_MIN_GLYPH_EXTENT = 14.0


def _glyph_bounds(node: RenderNode) -> tuple[float, float, float, float]:
    """The rect a node's glyph is ACTUALLY drawn at. For a PRIMITIVE this is the
    union of its terminal ``termBounds`` — the real icon extent LabVIEW draws —
    not the full (often 32x32-padded) clickable box (roadmap #41). So a thin
    primitive (``String Length`` 32x17, ``To Lower Case`` 29x13) renders thin
    instead of square, with the shape flush to its terminals and no guessed
    size. Primitives with their own fixed-aspect art (``_OWN_ASPECT_GLYPHS``)
    keep their node bounds, as do subVIs/constants/local-vars (whose bounds are
    already their true drawn size). Shared with the connector-panel drawer
    (``_draw_connector_panel``) so the panel's terminal geometry (fx/fy) is
    computed against the SAME rect the glyph is actually drawn in."""
    bounds = node.bounds
    if not isinstance(node.node, PrimitiveNode):
        return bounds
    if isinstance(node.glyph, _OWN_ASPECT_GLYPHS):
        return bounds
    rects = [t.bounds for t in node.terminals if t.bounds is not None]
    if not rects:
        return bounds
    ux1 = min(r[0] for r in rects)
    uy1 = min(r[1] for r in rects)
    ux2 = max(r[2] for r in rects)
    uy2 = max(r[3] for r in rects)
    # Floor each axis (centered on the span, capped by the node box) so a
    # same-height in/out pair can't collapse the glyph to a sliver.
    nx1, ny1, nx2, ny2 = node.bounds
    if ux2 - ux1 < _MIN_GLYPH_EXTENT:
        ux1, ux2 = _expand_axis(ux1, ux2, _MIN_GLYPH_EXTENT, nx1, nx2)
    if uy2 - uy1 < _MIN_GLYPH_EXTENT:
        uy1, uy2 = _expand_axis(uy1, uy2, _MIN_GLYPH_EXTENT, ny1, ny2)
    return (ux1, uy1, ux2, uy2)


def _expand_axis(
    lo: float,
    hi: float,
    target: float,
    cap_lo: float,
    cap_hi: float,
) -> tuple[float, float]:
    """Grow ``[lo, hi]`` symmetrically to length ``target`` about its center,
    clamped inside ``[cap_lo, cap_hi]``. Returns the (possibly shifted) span."""
    center = (lo + hi) / 2
    half = target / 2
    lo2, hi2 = center - half, center + half
    if lo2 < cap_lo:
        lo2, hi2 = cap_lo, min(cap_hi, cap_lo + target)
    if hi2 > cap_hi:
        hi2, lo2 = cap_hi, max(cap_lo, cap_hi - target)
    return lo2, hi2


def draw_node(node: RenderNode, backend: Backend, theme: Theme = DEFAULT_THEME) -> None:
    """Draw one node's already-resolved ``Glyph`` (see ``nodes.py``'s
    resolver chain — extending node visuals never touches this function).

    Arithmetic primitives (``ArithGlyph`` — Add/Subtract/Multiply/Divide/
    Increment/Decrement) are drawn at the exact icon rect LabVIEW stores: the
    UNION of the node's terminal ``termBounds`` rects. That union is the real
    triangle — smaller than the 32x32 clickable box and flush to its top-left,
    with the apex on the union's right edge (the output terminal) — so the base
    lands on the input terminals and the apex on the output wire, with no fixed
    guessed size and no leftward drift. Other primitives (e.g. bracket/build-
    array glyphs) are drawn at their node bounds. Real subVI/prim icons and
    constants keep their own bounds."""
    bounds = _glyph_bounds(node)
    # A hover tooltip (SVG <title>) with the node's full identity — the
    # wrapped subVI box shows a possibly-truncated name, so the untruncated
    # name on hover is the payoff (roadmap #12). ``lv-node`` + ``data-node``
    # are the JS hooks (see render/__init__.py's injected hover script) that
    # reveal this same node's ``.lv-help`` connector-panel — drawn separately,
    # in a top-level overlay group, by ``draw_help_overlay`` (see scene.py's
    # ``draw_scene``) so it always paints over every other diagram element,
    # not just this node's later siblings. The native <title> tooltip stays
    # as a text fallback (e.g. no-JS consumers).
    tooltip = _node_tooltip(node.node)
    if tooltip is None and isinstance(node.glyph, ConstantGlyph):
        # A constant's in-box text is fit to the box and may be ellipsized/clipped
        # ("…"). When it is — for ANY type (numeric, string, path, enum) — expose
        # the FULL value on hover so it stays readable. Short constants that show
        # in full get no tooltip (no redundant hover on every literal).
        tooltip = node.glyph.truncated_value(backend, bounds)
    if tooltip is None and isinstance(node.glyph, ClusterConstantGlyph):
        # A cluster constant may be drawn small/collapsed; its field values are
        # then unreadable inline, so show them all on hover.
        tooltip = node.glyph.value_summary or None
    # A resolvable SubVI's own source .vi, relative to the rendered VI's
    # directory (task #76) — an INERT data attribute only: no links, no click
    # JS, no VS Code assumptions here. The VS Code extension injects the
    # click behavior; a standalone .svg or a web page just ignores it. Forces
    # the node's group to open even when it otherwise carries no tooltip.
    group_needed = bool(tooltip) or node.subvi_rel is not None
    if group_needed:
        data = {"node": node.dom_id}
        if node.subvi_rel is not None:
            data["lv-vi-rel"] = node.subvi_rel
        # A resolvable node (primitive / vi.lib VI) links to its NI docs page
        # (opens a new tab) — so the doc_url shown in the tooltip is actually
        # reachable, not just displayed (task #67). Non-resolvable nodes get a
        # plain group.
        backend.begin_group(
            cls="lv-node",
            data=data,
            title=tooltip,
            href=_node_doc_url(node.node),
        )

    if isinstance(node.node, FormulaNode):
        # A Formula Node lays out its own box + gutter-inset script + border
        # tunnels together (the script inset depends on the tunnel column
        # widths), so it takes the whole draw rather than the generic glyph.
        _draw_formula_node(node, bounds, backend, theme)
    else:
        node.glyph.draw(backend, bounds, theme)
        _draw_invert_bubbles(node, backend, theme)

    # Name below the box — ONLY when it isn't already drawn inside: a subVI
    # with no icon now wraps its name into the box (WrappedBoxGlyph), so a
    # second copy below would be redundant. A subVI WITH a real icon keeps its
    # label below, as before.
    if (
        isinstance(node.node, VINode)
        and node.label_visible
        and not isinstance(node.glyph, WrappedBoxGlyph)
    ):
        name = node.node.name or ""
        if name:
            x1, y1, x2, y2 = node.bounds
            backend.text((x1 + x2) / 2, y2 + 9, name, 8.0, fill=theme.text)

    if node.owned_label is not None:
        _draw_owned_label(node.owned_label, backend, theme)

    if group_needed:
        backend.end_group()


# Owned-label (constant free-label) text metrics — left-aligned, drawn top-down
# inside the label's heap rect.
_LABEL_TEXT_SIZE = 8.0
_LABEL_LINE_H = _LABEL_TEXT_SIZE + 2.0


def _draw_owned_label(
    label: RenderLabel,
    backend: Backend,
    theme: Theme,
) -> None:
    """Draw a constant's developer-authored owned label (free text) at its heap
    position — left-aligned, one line per embedded newline (LabVIEW owned labels
    are multi-line). Text-color only, no box; LabVIEW draws these transparent."""
    x1, y1, _, _ = label.bounds
    for i, line in enumerate(label.text.split("\n")):
        backend.text(
            x1,
            y1 + _LABEL_TEXT_SIZE + i * _LABEL_LINE_H,
            line,
            _LABEL_TEXT_SIZE,
            fill=theme.text,
            anchor="start",
        )


def _node_identity(node: AnyGraphNode) -> tuple[str, str | None] | None:
    """A node's (header, description) for context-help purposes — the shared
    lookup behind both the text ``<title>`` tooltip and the visual connector-
    pane panel. Returns None for nodes without a useful identity (constants
    show their value in-box already).

    A local variable MUST get its own header: without one the browser shows
    the nearest ancestor ``<title>`` (the diagram's VI name), so every local
    var would otherwise read as the VI itself.
    """
    header: str | None = None
    if isinstance(node, LocalVariableNode):
        name = node.control_name or node.name
        kind = (
            "Control Reference" if node.node_type == "ctlRefConst" else "Local Variable"
        )
        header = f"{kind}: {name}" if name else kind
    elif isinstance(node, VINode | PrimitiveNode):
        # A VI's fully qualified name (Class.lvclass:vi.vi) disambiguates a bare
        # leaf name; fall back to the plain name when there's no qualifier.
        header = getattr(node, "qualified_name", None) or node.name or None
        # Unresolved primitive: the hover header matches the box — "#<prim_id>"
        # instead of the verbose "unknown_primitive_N" placeholder. The
        # connector-pane panel below still lists every terminal we know.
        if (
            isinstance(node, PrimitiveNode)
            and node.prim_id is not None
            and node.name == f"unknown_primitive_{node.prim_id}"
        ):
            header = f"#{node.prim_id}"
        # Bundle/Unbundle (By Name): the parser's XML-class jargon ("Node
        # Multiplexer"/"Multiplexer"/"Demultiplexer") never belongs in
        # user-facing text — show the directional human name instead.
        if isinstance(node, PrimitiveNode) and node.node_type in _CLUSTER_MUX_TYPES:
            header = mux_display_name(node)
    if header is None:
        return None
    return header, clean_help_text(getattr(node, "description", None))


def _node_tooltip(node: AnyGraphNode) -> str | None:
    """Context-help hover text for a node: its identity, description (when
    known), and its connector pane — every terminal with direction, type, and
    label (when the heap carries one). Returns None for nodes without a useful
    identity (constants show their value in-box already)."""
    ident = _node_identity(node)
    if ident is None:
        return None
    header, desc = ident

    lines = [header]
    if desc:
        lines.append(desc)
    lines.extend(_terminal_help_lines(node))
    doc_url = _node_doc_url(node)
    if doc_url:
        lines.append(doc_url)
    return "\n".join(lines)


def _node_doc_url(node: AnyGraphNode) -> str | None:
    """Public NI docs URL for a resolvable node — a primitive (primitives.json)
    or a vi.lib VI (vilib catalog), both carrying ``doc_url`` since #87. Shown
    as the last line of the hover ``<title>`` (task #67). None when the node has
    no catalog page (unresolved prim, user subVI, structure, constant)."""
    if isinstance(node, PrimitiveNode):
        # Cluster-mux: node-type flavor, but direction-polymorphic — link by the
        # resolved DISPLAY name (Bundle vs Unbundle), not the raw ambiguous name.
        if node.node_type in _CLUSTER_MUX_TYPES:
            return mux_doc_url(node)
        resolver = get_prim_resolver()
        # primResID flavor first (prim_id / name), then the node-type flavor
        # (Build Array, Index Array, Compound Arithmetic, ... resolve_by_node_type).
        resolved = resolver.resolve(prim_id=node.prim_id, name=node.name)
        if resolved is not None and resolved.doc_url:
            return resolved.doc_url
        if node.node_type:
            nt = resolver.resolve_by_node_type(node.node_type)
            if nt is not None and nt.doc_url:
                return nt.doc_url
        return None
    if isinstance(node, VINode):
        entry = get_vilib_resolver().resolve_by_name(node.name or "")
        return entry.doc_url if entry else None
    return None


def _terminal_label(t: Terminal) -> str:
    """A terminal's display label: the resolved def name (``display_name``,
    e.g. "x"/"difference"), else a caller-side ``name`` if any, else
    ``terminal N`` from its connector-pane index."""
    return _terminal_display_name(t) or f"terminal {t.index}"


def _terminal_is_informative(t: Terminal) -> bool:
    """Whether a terminal is worth showing in context help. Skips pure EMPTY
    connector-pane slots — a position with no label AND no meaningful type
    (unwired, unnamed pane slots, e.g. a big DAQmx SubVI's spare terminals);
    any labeled or typed terminal is kept. A ``Void`` terminal is a dead pane
    slot with no data — LabVIEW draws neither a wire stub nor a label for it, so
    it is never shown even if it carries a leftover name."""
    if t.lv_type is not None and lv_type_label(t.lv_type) == "Void":
        return False
    if _terminal_display_name(t):
        return True
    return t.lv_type is not None and lv_type_label(t.lv_type) != "?"


def _terminal_help_lines(node: AnyGraphNode) -> list[str]:
    """The node's connector pane as tooltip lines — inputs then outputs, each
    ``label: type``. Empty pane slots (no label, no type) are omitted; labels
    prefer the resolved def name, then a caller-side name, then ``terminal N``.
    """
    terms = [
        t
        for t in (getattr(node, "terminals", None) or [])
        if _terminal_is_informative(t)
    ]

    def fmt(t: Terminal) -> str:
        ty = lv_type_label(t.lv_type)
        return f"  {_terminal_label(t)}: {ty}"

    ins = sorted((t for t in terms if t.direction == "input"), key=lambda t: t.index)
    outs = sorted((t for t in terms if t.direction == "output"), key=lambda t: t.index)
    out: list[str] = []
    if ins:
        out.append("Inputs:")
        out += [fmt(t) for t in ins]
    if outs:
        out.append("Outputs:")
        out += [fmt(t) for t in outs]
    return out


# --------------------------------------------------------------------- #
# Connector-pane hover panel (roadmap #40 front end) — a labeled MAP of the
# node exactly as drawn on the diagram, not an abstracted in/out list: every
# terminal is placed at its own true ``(fx, fy)`` position on a scaled copy
# of the node's own glyph (from its real heap ``termBounds``, via
# ``_term_side_and_frac``), with a short type-colored stub routed OUTWARD
# from the nearest edge to its label. So an Add's output (apex, fx≈1) gets a
# right-going stub, a bottom-edge terminal gets a downward one — the panel's
# geometry matches the node's, which is the entire point (a wire hitting the
# top-left of a node reads as "the top-left label" in the panel, not
# "somewhere in a stacked input list").
#
# Every panel is drawn in this function into its own small ``<g class=
# "lv-help" data-node="...">``, laid out in LOCAL coordinates near the
# origin (position/visibility is applied at runtime by JS — see
# render/__init__.py's injected hover script). Panels are collected and
# emitted in ONE overlay pass, after every other layer (``draw_help_overlay``,
# called last by ``scene.py``'s ``draw_scene``), so a panel always paints
# over the rest of the diagram regardless of node draw order. The native
# ``<title>`` tooltip (``_node_tooltip``) stays as a text-only fallback.
# --------------------------------------------------------------------- #

_PANE_PAD = 6.0  # panel inner padding
_PANE_TITLE_SIZE = 9.0
_PANE_DESC_SIZE = 7.5
_PANE_DESC_LH = 9.5  # line height for a wrapped description line
_PANE_DESC_MAX_LINES = 6  # cap so a long description can't grow a huge panel
_PANE_LABEL_SIZE = 7.5  # terminal name
_PANE_TYPE_SIZE = 6.5  # terminal type — smaller/lighter, secondary info
_PANE_MAX_LABEL_W = 130.0  # per-terminal name+type truncation width
_PANE_MAX_HEADER_W = 220.0  # title/description truncation width

_PANE_STUB_OUT = 12.0  # straight run out of the icon edge, before a jog
_PANE_STUB_FULL = 20.0  # full stub length, icon edge -> label anchor
_PANE_TEXT_GAP = 3.0  # label anchor -> first glyph of text
_PANE_NT_GAP = 5.0  # gap between a terminal's name and its grey type
_PANE_STUB_MARGIN = _PANE_STUB_FULL + _PANE_TEXT_GAP  # edge -> text start
_PANE_CARD_RX = 3.0  # corner radius of the hover help-panel card

_PANE_ROW_MIN_GAP = 11.0  # min vertical spacing between left/right labels
_PANE_ROW_HALF = _PANE_LABEL_SIZE / 2 + 2.0  # vertical pad around a label row

# The glyph footprint is measured against the 32-unit LabVIEW icon cell: a full
# 32-cell glyph scales to _PANE_ICON_TARGET px, sub-cell glyphs proportionally.
# 48 = 80% of the original 60 px target — a touch smaller than before, but still
# a readable thumbnail rather than a blown-up copy.
_PANE_ICON_CELL = 32.0
_PANE_ICON_TARGET = 48.0
_PANE_ICON_MAX = 72.0  # cap so an oversized icon can't dominate the panel

# fx/fy classification -> the outward unit normal a terminal's stub exits on.
_SIDE_NORMAL: dict[str, Point] = {
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "top": (0.0, -1.0),
    "bottom": (0.0, 1.0),
}


@dataclass(frozen=True)
class _PaneLabel:
    """One terminal's fitted label pieces (name + a smaller/lighter
    ``" (type)"`` suffix) plus its wire-color stub style and its true
    ``(side, frac)`` position on the node's own glyph — see
    ``_term_side_and_frac``."""

    side: str
    frac: float  # fy for left/right, fx for top/bottom — in [0, 1]
    name: str
    name_w: float
    type_str: str
    type_w: float
    color: str
    width: float

    @property
    def total_w(self) -> float:
        return self.name_w + _PANE_NT_GAP + self.type_w


@dataclass
class _PaneTerm:
    """One terminal placed for the help panel's LEFT/RIGHT-only layout: its
    fitted label, the side its label sits on (``left`` for inputs, ``right`` for
    outputs), whether it FOLDS (``top``/``bottom``-attached terminals route
    up-over / down-under so no wire crosses; ``none`` = a straight side leader),
    its icon-local attach point (``ax``,``ay``) and its assigned label row
    (``ly``)."""

    lb: _PaneLabel
    side: str  # "left" | "right"
    fold: str  # "none" | "top" | "bottom"
    ax: float
    ay: float
    ly: float


def _term_side_and_frac(
    rt: RenderTerminal,
    bounds: tuple[float, float, float, float],
) -> tuple[str, float]:
    """A terminal's TRUE side + normalized position along that side, from
    which EDGE of the node's drawn ``bounds`` its own heap ``termBounds`` rect
    lines up against — NOT the nearest edge to its center.

    LabVIEW connector-pane rule (given by the user, verified against heap
    ``termBounds``): a terminal that lines the LEFT or RIGHT edge is wired
    HORIZONTALLY, even when it also sits high or low (a corner terminal — e.g.
    Compound Arithmetic's top-left input, or Select's three left-column
    inputs). Only a terminal strictly BETWEEN the left/right columns — flush to
    the TOP or BOTTOM edge and inset from both sides — wires vertically (e.g.
    Scan From String's top input string). So horizontal wins ties: compare the
    smaller left/right gap to the smaller top/bottom gap; the terminal's rect
    edge, not its center, measures each gap.

    Falls back to a direction-based side (input->left, output->right) at the
    mid-point when the terminal has no ``termBounds`` (no heap geometry) or the
    node's box is degenerate — geometry we don't have, not geometry we guess."""
    x1, y1, x2, y2 = bounds
    bw, bh = x2 - x1, y2 - y1
    if rt.bounds is not None and bw > 0 and bh > 0:
        tx1, ty1, tx2, ty2 = rt.bounds
        # Gap from the terminal's own rect edge to each icon edge; ~0 means the
        # terminal "lines" that edge.
        gl, gr = tx1 - x1, x2 - tx2
        gt, gb = ty1 - y1, y2 - ty2
        tcx, tcy = (tx1 + tx2) / 2, (ty1 + ty2) / 2
        fx = min(1.0, max(0.0, (tcx - x1) / bw))
        fy = min(1.0, max(0.0, (tcy - y1) / bh))
        if min(gl, gr) <= min(gt, gb):  # horizontal wins ties
            return ("left" if gl <= gr else "right"), fy
        return ("top" if gt <= gb else "bottom"), fx
    side = "right" if rt.terminal.direction == "output" else "left"
    return side, 0.5


def _pane_label(
    rt: RenderTerminal, side: str, frac: float, backend: Backend, theme: Theme
) -> _PaneLabel:
    t = rt.terminal
    name = _terminal_label(t)
    # Grey type appended after the name (no parens) — the connector-pane help
    # panel's label style; the name<->type gap is added at draw time.
    type_str = lv_type_label(t.lv_type)
    name_w = backend.measure_text(name, _PANE_LABEL_SIZE)
    type_w = backend.measure_text(type_str, _PANE_TYPE_SIZE)
    if name_w + _PANE_TEXT_GAP + type_w > _PANE_MAX_LABEL_W:
        # Type stays intact (short, meaningful); the name shrinks to fit.
        avail = max(10.0, _PANE_MAX_LABEL_W - type_w)
        name = fit_label(name, avail, backend, _PANE_LABEL_SIZE)
        name_w = backend.measure_text(name, _PANE_LABEL_SIZE)
    style = wire_style(t.lv_type, theme)
    return _PaneLabel(
        side,
        frac,
        name,
        name_w,
        type_str,
        type_w,
        style.color,
        style.width,
    )


def _spread_1d(values: list[float], min_gaps: list[float]) -> list[float]:
    """Nudge sorted ``values`` apart so consecutive entries are at least
    ``min_gaps[i]`` (the gap required BEFORE entry ``i``, i>=1) apart, then
    re-centers the whole run on its original mean so one crowded pair doesn't
    drag the group toward one end. Keeps each label as close as legibly possible
    to its TRUE terminal height. Pure/deterministic (no set/dict iteration) so
    panel layout is byte-reproducible across runs/hash seeds."""
    if not values:
        return []
    out = list(values)
    for i in range(1, len(out)):
        floor = out[i - 1] + min_gaps[i]
        if out[i] < floor:
            out[i] = floor
    shift = sum(v - o for v, o in zip(values, out)) / len(values)
    out = [o + shift for o in out]
    for i in range(1, len(out)):
        floor = out[i - 1] + min_gaps[i]
        if out[i] < floor - 1e-9:
            out[i] = floor
    return out


def _pane_stub_points(edge: Point, side: str, final_perp: float) -> list[Point]:
    """Path points for one terminal's stub, routed like real LabVIEW wiring:
    it ORIGINATES at the terminal's true edge point and runs ORTHOGONALLY
    (horizontal/vertical segments only — never a diagonal) out to its label
    slot. When the label sits exactly on the terminal's own edge fraction the
    stub is a single straight run; when the label was moved to its assigned row
    (straight rows nudged apart on collision, folded rows reach-stacked above/
    below the icon — see ``_layout``) the stub becomes a proper elbow/Z —
    out along the side normal, across to the label's row/column, then in to
    the label anchor. The kinks are expected: that's how LabVIEW wires look,
    and they keep every stub visibly terminating at the REAL geometry even
    when its label is offset. ``final_perp`` is the label's y (left/right
    sides) or x (top/bottom sides) in ``edge``'s local coordinate space."""
    ex, ey = edge
    if side in ("left", "right"):
        nx = _SIDE_NORMAL[side][0]
        x_out = ex + nx * _PANE_STUB_OUT
        x_end = ex + nx * _PANE_STUB_FULL
        if abs(final_perp - ey) < 0.5:
            return [edge, (x_end, ey)]
        # horizontal out, vertical across to the label row, horizontal to anchor
        return [edge, (x_out, ey), (x_out, final_perp), (x_end, final_perp)]
    ny = _SIDE_NORMAL[side][1]
    y_out = ey + ny * _PANE_STUB_OUT
    y_end = ey + ny * _PANE_STUB_FULL
    if abs(final_perp - ex) < 0.5:
        return [edge, (ex, y_end)]
    # vertical out, horizontal across to the label column, vertical to anchor
    return [edge, (ex, y_out), (final_perp, y_out), (final_perp, y_end)]


def _draw_connector_panel(node: RenderNode, backend: Backend, theme: Theme) -> None:
    """Draw one node's hidden, hover-revealed connector panel: a labeled map
    of the node's own glyph with every terminal at its TRUE position (see
    module docstring above). A no-op for nodes without a context-help
    identity (matches ``_node_tooltip``'s gate)."""
    ident = _node_identity(node.node)
    if ident is None:
        return
    header, desc = ident

    bounds = _glyph_bounds(node)
    x1, y1, x2, y2 = bounds
    bw, bh = max(1e-6, x2 - x1), max(1e-6, y2 - y1)

    # Icon size FIRST — terminal attach points are icon-relative. The node's OWN
    # glyph, scaled by a CONSTANT factor keyed to the 32-unit LabVIEW icon cell,
    # so the panel icon is PROPORTIONAL to how much of that cell the glyph fills
    # (a half-cell glyph shows half-size next to a full-cell one, matching the
    # diagram). Aspect ratio is preserved; the MAX cap only stops an oversized
    # subVI/growable icon from dominating the panel.
    scale = _PANE_ICON_TARGET / _PANE_ICON_CELL
    icon_w, icon_h = bw * scale, bh * scale
    if max(icon_w, icon_h) > _PANE_ICON_MAX:
        s = _PANE_ICON_MAX / max(icon_w, icon_h)
        icon_w, icon_h = icon_w * s, icon_h * s

    # Terminals sharing an identical ``termBounds`` rect occupy ONE connector-
    # pane slot: a growable node (e.g. Scan From String) stores the slot once on
    # the input-default half and the wired output half inherits it. Place each
    # co-located terminal by its KNOWN graph direction instead of the shared rect.
    seen_rects: set[tuple[float, float, float, float]] = set()
    shared_rects: set[tuple[float, float, float, float]] = set()
    for rt in node.terminals:
        if rt.bounds is not None:
            (shared_rects if rt.bounds in seen_rects else seen_rects).add(rt.bounds)

    # Context-help layout (matches the connector-pane help panel): EVERY label
    # sits on the LEFT (inputs) or RIGHT (outputs). A terminal attaching at that
    # same side's edge gets a straight leader; one attaching at the TOP or BOTTOM
    # edge is FOLDED to its direction side and STACKED above/below the straight
    # rows, then routed up-over / down-under so no wire ever crosses the icon.
    left_terms: list[_PaneTerm] = []
    right_terms: list[_PaneTerm] = []
    for rt in node.terminals:
        if not _terminal_is_informative(rt.terminal):
            continue
        side, frac = _term_side_and_frac(rt, bounds)
        if rt.bounds is not None and rt.bounds in shared_rects:
            side = "right" if rt.terminal.direction == "output" else "left"
        lb = _pane_label(rt, side, frac, backend, theme)
        if side == "left":
            lside, fold, ax, ay = "left", "none", 0.0, frac * icon_h
        elif side == "right":
            lside, fold, ax, ay = "right", "none", icon_w, frac * icon_h
        else:  # top / bottom edge -> fold to the direction side
            lside = "left" if rt.terminal.direction == "input" else "right"
            fold, ax, ay = side, frac * icon_w, (0.0 if side == "top" else icon_h)
        term = _PaneTerm(lb=lb, side=lside, fold=fold, ax=ax, ay=ay, ly=ay)
        (left_terms if lside == "left" else right_terms).append(term)

    def _layout(terms: list[_PaneTerm]) -> None:
        # Straight (side-attached) rows sit at their TRUE terminal height (only
        # nudged apart when two would collide) — so the layout reads as if EVERY
        # terminal were populated: a row's height is fixed by the terminal, not by
        # how many neighbours happen to be wired, and left/right rows line up.
        # Folded rows sit just ONE gap beyond that block (top-folds above the icon
        # top, bottom-folds below the icon bottom) — close in, never sticking far
        # out, yet clear of the icon so their up-over / down-under wire can't cross.
        straight = sorted((t for t in terms if t.fold == "none"), key=lambda t: t.ay)
        for t, y in zip(
            straight,
            _spread_1d([t.ay for t in straight], [_PANE_ROW_MIN_GAP] * len(straight)),
        ):
            t.ly = y
        # FOLD_STACK_RULE (see connector_pane.py::_classify for the canonical
        # statement): stack folded leaders by REACH (horizontal distance to this
        # side's hub) so they never cross -- the LONGEST reach sits FURTHEST from
        # the icon, clearing the shorter ones' vertical runs. In these PIXEL
        # coords rows are placed OUTWARD from the icon, so each stack sorts
        # ASCENDING (longest reach lands on the outside); the help panel, in
        # normalized coords with top-down placement, sorts the mirror direction.
        # Same invariant, coordinate-specific (sort, placement) pair -- keep in
        # sync. Grep FOLD_STACK_RULE for both homes.
        side = terms[0].side if terms else "left"
        reach = lambda t: t.ax if side == "left" else icon_w - t.ax  # noqa: E731
        base_top = min([t.ly for t in straight] + [0.0]) - _PANE_ROW_MIN_GAP
        ftop = sorted((t for t in terms if t.fold == "top"), key=reach)
        for k, t in enumerate(ftop):
            t.ly = base_top - k * _PANE_ROW_MIN_GAP
        base_bot = max([t.ly for t in straight] + [icon_h]) + _PANE_ROW_MIN_GAP
        fbot = sorted((t for t in terms if t.fold == "bottom"), key=reach)
        for k, t in enumerate(fbot):
            t.ly = base_bot + k * _PANE_ROW_MIN_GAP

    _layout(left_terms)
    _layout(right_terms)
    all_terms = left_terms + right_terms

    # Icon-local diagram extents (icon spans [0,icon_w] x [0,icon_h]); labels sit
    # a stub margin beyond the left/right edges, folded rows above/below.
    left_w = max((t.lb.total_w for t in left_terms), default=0.0)
    right_w = max((t.lb.total_w for t in right_terms), default=0.0)
    x_min = -(_PANE_STUB_MARGIN + left_w) if left_terms else 0.0
    x_max = icon_w + (_PANE_STUB_MARGIN + right_w if right_terms else 0.0)
    diagram_w = x_max - x_min
    lys = [t.ly for t in all_terms]
    y_min = min([0.0] + lys) - _PANE_ROW_HALF
    y_max = max([icon_h] + lys) + _PANE_ROW_HALF
    diagram_h = y_max - y_min

    full_title_w = backend.measure_text(header, _PANE_TITLE_SIZE)
    inner_w = max(diagram_w, min(full_title_w, _PANE_MAX_HEADER_W))
    full_desc_w = backend.measure_text(desc, _PANE_DESC_SIZE) if desc else 0.0
    if desc:
        inner_w = max(inner_w, min(full_desc_w, _PANE_MAX_HEADER_W))
    title_line = (
        header
        if full_title_w <= inner_w
        else fit_label(header, inner_w, backend, _PANE_TITLE_SIZE)
    )
    # Wrap the VI description across as many lines as it needs (capped), so the
    # help box reads like LabVIEW context help instead of one truncated line.
    desc_lines: list[str] = (
        [desc]
        if desc and full_desc_w <= inner_w
        else wrap_label(desc, inner_w, backend, _PANE_DESC_SIZE, _PANE_DESC_MAX_LINES)
        if desc
        else []
    )

    # With a description, reserve an extra gap below it (holding a divider that
    # separates the prose from the icon/terminals diagram below).
    header_h = (
        11.0 + len(desc_lines) * _PANE_DESC_LH + (_PANE_PAD if desc_lines else 0.0)
    )
    panel_w = inner_w + 2 * _PANE_PAD
    panel_h = header_h + _PANE_PAD + diagram_h + _PANE_PAD

    # Everything below is drawn in a small LOCAL coordinate space around the
    # origin — panels are positioned/clamped at runtime by JS (see
    # render/__init__.py), not baked in relative to the node's own position.
    diagram_x0 = _PANE_PAD + max(0.0, (inner_w - diagram_w) / 2)
    icon_x1 = diagram_x0 - x_min
    icon_x2 = icon_x1 + icon_w
    diagram_y0 = header_h + _PANE_PAD
    icon_y1 = diagram_y0 - y_min
    icon_y2 = icon_y1 + icon_h

    # All panel styling (hidden-until-hover, no pointer capture, drop shadow)
    # lives in the .lv-help CSS rule (render/__init__.py _BASE_CSS) — the single
    # source both the in-place reveal and a host's cloned overlay inherit.
    backend.begin_group(cls="lv-help", data={"node": node.dom_id})
    backend.rect(
        0.0,
        0.0,
        panel_w,
        panel_h,
        fill=theme.canvas,
        stroke=theme.struct_border,
        stroke_width=1.0,
        rx=_PANE_CARD_RX,
    )
    cx = panel_w / 2
    # Panel background is theme.canvas — pair with the canvas/default text role.
    backend.text(
        cx, _PANE_PAD + 7.0, title_line, _PANE_TITLE_SIZE, bold=True, fill=theme.text
    )
    for i, desc_line in enumerate(desc_lines):
        backend.text(
            cx,
            _PANE_PAD + 18.0 + i * _PANE_DESC_LH,
            desc_line,
            _PANE_DESC_SIZE,
            fill=theme.text,
        )
    # Divider between the description prose and the icon/terminals diagram.
    if desc_lines:
        sep_y = header_h + _PANE_PAD / 2.0
        backend.line(
            _PANE_PAD,
            sep_y,
            panel_w - _PANE_PAD,
            sep_y,
            stroke=theme.struct_border,
            stroke_width=0.5,
        )

    node.glyph.draw(backend, (icon_x1, icon_y1, icon_x2, icon_y2), theme)

    def _draw_term(t: _PaneTerm) -> None:
        lb = t.lb
        ly = icon_y1 + t.ly  # label row (panel space)
        ex, ey = icon_x1 + t.ax, icon_y1 + t.ay  # true attach point
        if t.fold == "none":
            # Straight left/right leader (an elbow only if its row was spread).
            pts = _pane_stub_points((ex, ey), t.side, ly)
        else:
            # Fold: run vertically off the top/bottom edge to the label row, then
            # horizontally out to the side's label hub — one clean orthogonal jog.
            hub = (
                icon_x1 - _PANE_STUB_FULL
                if t.side == "left"
                else icon_x2 + _PANE_STUB_FULL
            )
            pts = [(ex, ey), (ex, ly), (hub, ly)]
        backend.path(pts, stroke=lb.color, stroke_width=lb.width)
        end_x, end_y = pts[-1]
        # Name adjacent to the wire, grey type beyond it, one fixed gap between
        # them — identical to the connector-pane help panel.
        ty = end_y + _PANE_LABEL_SIZE * 0.35
        if t.side == "left":
            backend.text(
                end_x - _PANE_TEXT_GAP,
                ty,
                lb.type_str,
                _PANE_TYPE_SIZE,
                fill=theme.pane_type_text,
                anchor="end",
            )
            backend.text(
                end_x - _PANE_TEXT_GAP - lb.type_w - _PANE_NT_GAP,
                ty,
                lb.name,
                _PANE_LABEL_SIZE,
                anchor="end",
                fill=theme.text,
            )
        else:
            backend.text(
                end_x + _PANE_TEXT_GAP,
                ty,
                lb.name,
                _PANE_LABEL_SIZE,
                anchor="start",
                fill=theme.text,
            )
            backend.text(
                end_x + _PANE_TEXT_GAP + lb.name_w + _PANE_NT_GAP,
                ty,
                lb.type_str,
                _PANE_TYPE_SIZE,
                fill=theme.pane_type_text,
                anchor="start",
            )

    for t in all_terms:
        _draw_term(t)

    backend.end_group()


def draw_help_overlay(
    nodes: list[RenderNode],
    backend: Backend,
    theme: Theme = DEFAULT_THEME,
) -> None:
    """Draw every node's connector-help panel into ONE top-level overlay
    group, emitted LAST (see ``scene.py``'s ``draw_scene``) so panels always
    paint over every other diagram layer — wires, structures, and every
    node, including ones drawn after their own owner. Each panel starts
    ``visibility:hidden`` (kept in the render tree, not ``display:none``, so
    a hidden panel's ``getBBox()`` still works for the hover script's
    clamped positioning — see render/__init__.py); JS shows exactly one at a
    time, on hover of its matching ``.lv-node``."""
    backend.begin_group(cls="lv-help-overlay")
    for node in nodes:
        _draw_connector_panel(node, backend, theme)
    backend.end_group()


def _draw_border_terminal(
    bt: RenderBorderTerminal,
    backend: Backend,
    theme: Theme,
    frame_value: str | None = None,
) -> None:
    """Draw a structure border glyph from its fixed ``glyph_kind`` (see
    ``scene._structure_borders``) by delegating to the per-kind glyph in
    ``glyphs/terminals/``. ``frame_value`` is the frame this glyph is drawn for
    (an output tunnel unwired in that frame draws with a canvas hole). Hidden
    terminals (loop i/N/cond turned off via "Visible Items") are omitted — the
    terminal stays in the scene so a future "show hidden" toggle can reveal it.
    """
    if bt.hidden:
        return
    glyph = border_terminal_glyph(
        bt.glyph_kind,
        color=bt.color,
        cond_continue=bt.cond_continue,
        unwired_frames=bt.unwired_frames,
    )
    glyph.draw(backend, bt.bounds, theme, frame_value)


# structure's heap bounds (the selector sits within the frame, per LabVIEW — its
# heap termBounds are inside the bounds; there is no band above the top edge).
_CASE_BAR_H = 14.0
_SELECTOR_SIZE = 9.0  # font size of the value + arrows
_SELECTOR_TRI_W = 11.0  # width of the dropdown-triangle zone (case only)
_SELECTOR_ARROW_GAP = 15.0  # horizontal room reserved for each flanking arrow


@dataclass(frozen=True)
class _SelectorGeom:
    """Laid-out pieces of a case/stacked-sequence selector. Per the LabVIEW
    reference, the selector is ONE enclosing box at top-center holding, left to
    right, a ◄ arrow cell | the value (+ ▼ dropdown for a case) | a ► arrow cell,
    with vertical dividers between cells. Computed once from the frame values."""

    outer: tuple[float, float, float, float]  # the single enclosing box
    box: tuple[float, float, float, float]  # the central value cell (inside outer)
    tri: tuple[float, float, float, float] | None  # dropdown ▼ zone (case only)
    text_cx: float  # center-x of the value text (its own zone, left of ▼)
    baseline: float  # shared baseline y for value + arrows
    left_x: float  # ◄ center-x (in the left arrow cell)
    right_x: float  # ► center-x (in the right arrow cell)


def _frame_display(structure: RenderStructure, scene: Scene, value: str) -> str:
    """Selector text for one frame value. A stacked sequence shows the frame
    number WITH the full range — ``N [0..M]`` (e.g. ``2 [0..2]``) — LabVIEW's
    signature sequence label; a case shows its faithful, typed selector label
    (enum item name, ``No Error``/``Error``, quoted string, ``a, b``, ``a..b``,
    ``Default``) resolved in ``scene.frame_labels``, falling back to the raw
    value."""
    if isinstance(structure.node, SequenceNode):  # flat isn't interactive
        values = scene.frame_values.get(structure.raw_uid, [])
        last = len(values) - 1 if values else 0
        return f"{value} [0..{last}]"
    return scene.frame_labels.get(structure.raw_uid, {}).get(value, value)


def _selector_geom(
    structure: RenderStructure,
    scene: Scene,
    *,
    has_dropdown: bool,
    backend: Backend,
) -> _SelectorGeom:
    x1, y1, x2, _ = structure.bounds
    values = scene.frame_values.get(structure.raw_uid, [])
    max_val_w = max(
        (
            backend.measure_text(_frame_display(structure, scene, v), _SELECTOR_SIZE)
            for v in values
        ),
        default=10.0,
    )
    pad = 4.0
    tri_w = _SELECTOR_TRI_W if has_dropdown else 0.0
    arrow_w = 13.0  # width of each flanking ◄ / ► arrow cell
    val_w = max(22.0, max_val_w + 2 * pad + tri_w)  # central value cell (incl. ▼)
    total_w = arrow_w + val_w + arrow_w
    total_w = min(total_w, (x2 - x1) - 6.0)
    cx = (x1 + x2) / 2
    ox1, ox2 = cx - total_w / 2, cx + total_w / 2
    # The enclosing box sits INSIDE the top of the frame (heap termBounds put the
    # selector inside the bounds — no band above the top edge).
    oy1, oy2 = y1 + 1.0, y1 + _CASE_BAR_H - 1.0
    vc1, vc2 = ox1 + arrow_w, ox2 - arrow_w  # value-cell x range
    box = (vc1, oy1, vc2, oy2)
    tri = (vc2 - tri_w, oy1, vc2, oy2) if has_dropdown else None
    text_right = vc2 - tri_w
    baseline = (oy1 + oy2) / 2 + _SELECTOR_SIZE * 0.34
    return _SelectorGeom(
        outer=(ox1, oy1, ox2, oy2),
        box=box,
        tri=tri,
        text_cx=(vc1 + text_right) / 2,
        baseline=baseline,
        left_x=(ox1 + vc1) / 2,
        right_x=(vc2 + ox2) / 2,
    )


def _error_border_color(
    scene: Scene,
    raw_uid: str,
    value: str,
    theme: Theme,
) -> str | None:
    """Green (No Error) / red (Error) border color for an error-cluster case's
    frame, or ``None`` if this structure isn't an error case. LabVIEW colors the
    case border by the shown frame: green for the No-Error case, red otherwise.
    """
    err = scene.error_frame_no_error.get(raw_uid)
    if err is None:
        return None
    return (
        theme.case_no_error_border if err.get(value, False) else theme.case_error_border
    )

def _draw_frame_selector(
    structure: RenderStructure,
    scene: Scene,
    backend: Backend,
    theme: Theme,
) -> None:
    """The selector chrome as separate CLICK TARGETS: a ◄ prev arrow, the value
    box (a ▼ dropdown toggle carrying the frame list — both cases and stacked
    sequences can jump to a frame), and a ► next arrow. The dropdown MENU is
    drawn topmost by ``_draw_frame_menu``; the frame LABEL is drawn on top
    per-frame by ``_draw_frame_value_label`` (a case shows its value, a stacked
    sequence its ``N [0..M]`` frame label)."""
    values = scene.frame_values.get(structure.raw_uid)
    default = scene.default_frame.get(structure.raw_uid)
    if not values or default is None:
        return
    has_dropdown = _is_interactive_structure(structure.node)
    g = _selector_geom(structure, scene, has_dropdown=has_dropdown, backend=backend)
    ox1, oy1, ox2, oy2 = g.outer
    vc1, _, vc2, _ = g.box
    struct = structure.raw_uid

    # One enclosing box, filled with the case-bar role (paired with
    # case_bar_text below), with vertical dividers between the flanking arrow
    # cells and the central value cell — the LabVIEW selector-label look.
    backend.rect(
        ox1,
        oy1,
        ox2,
        oy2,
        fill=theme.case_bar_fill,
        stroke=theme.struct_border,
        stroke_width=0.75,
    )
    backend.line(vc1, oy1, vc1, oy2, stroke=theme.struct_border, stroke_width=0.5)
    backend.line(vc2, oy1, vc2, oy2, stroke=theme.struct_border, stroke_width=0.5)

    def _arrow(action: str, xc: float, glyph: str, cell: tuple[float, float]) -> None:
        cx1, cx2 = cell
        backend.begin_group(
            cls="lv-selector lv-clickable",
            data={"lv-action": action, "lv-struct": struct},
        )
        backend.rect(cx1, oy1, cx2, oy2, fill="transparent", stroke="none")
        backend.text(xc, g.baseline, glyph, _SELECTOR_SIZE, fill=theme.case_bar_text)
        backend.end_group()

    _arrow("prev", g.left_x, "◄", (ox1, vc1))

    # Middle target — carries the frame list the JS controller reads, and (for a
    # case) draws the ▼ dropdown toggle. The value TEXT is drawn per-frame by
    # _draw_frame_value_label.
    box_data = {
        "lv-struct": struct,
        "lv-frames": ";".join(values),
        "lv-default": default,
    }
    if has_dropdown:
        box_data["lv-action"] = "toggle"
    backend.begin_group(
        cls="lv-selector lv-clickable" if has_dropdown else "lv-selector",
        data=box_data,
    )
    backend.rect(vc1, oy1, vc2, oy2, fill="transparent", stroke="none")
    if has_dropdown and g.tri is not None:
        tx1, ty1, tx2, ty2 = g.tri
        backend.line(tx1, ty1, tx1, ty2, stroke="#cccccc", stroke_width=0.5)
        tcx, tcy = (tx1 + tx2) / 2, (ty1 + ty2) / 2
        backend.polygon(
            [(tcx - 3.0, tcy - 1.6), (tcx + 3.0, tcy - 1.6), (tcx, tcy + 2.2)],
            fill=theme.case_bar_text,
        )
    backend.end_group()

    _arrow("next", g.right_x, "►", (vc2, ox2))


_MENU_ROW_H = 13.0


def _draw_frame_menu(
    structure: RenderStructure,
    scene: Scene,
    backend: Backend,
    theme: Theme,
) -> None:
    """An interactive structure's dropdown MENU: one clickable row per frame
    value, stacked below the value box, hidden until the ▼ toggle opens it.
    Drawn in a final topmost pass so it overlays the diagram; clicking a row
    selects that frame (see the JS controller). Runs for every interactive
    structure — both cases and stacked sequences get a menu (see the caller's
    ``_is_interactive_structure`` gate)."""
    values = scene.frame_values.get(structure.raw_uid)
    if not values:
        return
    g = _selector_geom(structure, scene, has_dropdown=True, backend=backend)
    bx1, _by1, bx2, by2 = g.box
    struct = structure.raw_uid
    zone_w = (bx2 - bx1) - 6.0
    backend.begin_group(cls="lv-menu", data={"lv-struct": struct})
    for i, v in enumerate(values):
        ry1 = by2 + i * _MENU_ROW_H
        ry2 = ry1 + _MENU_ROW_H
        backend.begin_group(
            cls="lv-option lv-clickable",
            data={"lv-struct": struct, "lv-value": v},
        )
        # Same case-bar fill as the selector box above, so the row's
        # case_bar_text label pairs with a themed background here too.
        backend.rect(
            bx1,
            ry1,
            bx2,
            ry2,
            fill=theme.case_bar_fill,
            stroke="#999999",
            stroke_width=0.5,
        )
        # The row's DISPLAY is the faithful typed label (enum name, No Error /
        # Error, quoted string, ...); the raw value stays the click identity in
        # ``lv-value`` above so the JS controller still matches frame paths.
        label = _frame_display(structure, scene, v)
        text = (
            label
            if backend.measure_text(label, _SELECTOR_SIZE) <= zone_w
            else fit_label(label, zone_w, backend, _SELECTOR_SIZE)
        )
        backend.text(
            (bx1 + bx2) / 2,
            ry1 + _MENU_ROW_H / 2 + _SELECTOR_SIZE * 0.34,
            text,
            _SELECTOR_SIZE,
            fill=theme.case_bar_text,
        )
        backend.end_group()
    backend.end_group()


def _draw_frame_value_label(
    structure: RenderStructure,
    scene: Scene,
    value: str,
    backend: Backend,
    theme: Theme,
) -> None:
    """The selected frame's label, centered in the selector's text zone (to the
    LEFT of a case's ▼ dropdown, never under it or the arrows). A case shows its
    plain value; a stacked sequence shows ``N [0..M]`` (see _frame_display)."""
    has_dropdown = _is_interactive_structure(structure.node)
    g = _selector_geom(structure, scene, has_dropdown=has_dropdown, backend=backend)
    label = _frame_display(structure, scene, value)
    tri_w = (g.tri[2] - g.tri[0]) if g.tri is not None else 0.0
    zone_w = (g.box[2] - g.box[0]) - tri_w - 4.0
    text = (
        label
        if backend.measure_text(label, _SELECTOR_SIZE) <= zone_w
        else fit_label(label, zone_w, backend, _SELECTOR_SIZE)
    )
    backend.text(g.text_cx, g.baseline, text, _SELECTOR_SIZE, fill=theme.case_bar_text)


_CONTROL_TYPE_FAMILY = {
    "stdNum": "float",
    "stdNumeric": "float",
    "stdDBL": "float",
    "stdSGL": "float",
    "stdEXT": "float",
    "stdI8": "int",
    "stdI16": "int",
    "stdI32": "int",
    "stdI64": "int",
    "stdU8": "int",
    "stdU16": "int",
    "stdU32": "int",
    "stdU64": "int",
    "stdBool": "bool",
    "stdString": "string",
    "stdPath": "path",
    "stdClust": "cluster",
    "stdArray": "array",
    "stdRing": "enum",
    "stdEnum": "enum",
}


# Fallback terminal text when the LVType didn't resolve (control_type only).
_FAMILY_REPR = {
    "float": "DBL",
    "int": "I32",
    "bool": "TF",
    "string": "abc",
    "path": "Path",
    "enum": "Enum",
    "error_cluster": "err",
    "variant": "Var",
    "refnum": "Ref",
}


_INDEX_LETTERS = "ijklmn"

# Below this size (either dimension) the icon-view internals (index/value
# cells, wire port) would overflow a box this small — fall back to just the
# outer rect + type label rather than drawing something illegible.
_FP_MIN_ICON_SIZE = 20.0

# Uniform inset inside a saved FP-terminal label box (all four sides): the box
# auto-grew to text + LabVIEW's internal padding, so this keeps the fitted font
# off every edge (right size) and reproduces LabVIEW's gap in every label
# position — beside a terminal or above/below it.
_FP_LABEL_MARGIN = 3.0


def _fp_type_label(terminal: FPTerminal, scalar_type: LVType | None) -> str:
    """Bottom-center type label: the SCALAR/ELEMENT type repr, e.g. a DBL
    array terminal is labelled "DBL", never "[DBL]" — LabVIEW's icon view
    labels the element type, brackets are structural chrome drawn via the
    index column instead."""
    r = type_repr(scalar_type)
    if r:
        return r
    family = type_family(scalar_type)
    if family == "unknown":
        family = _CONTROL_TYPE_FAMILY.get(terminal.control_type or "", "unknown")
        if family == "array":  # already unwrapped to scalar/element above
            family = "unknown"
    return _FAMILY_REPR.get(family, "")


def _draw_fp_value_cell(
    bounds: tuple[float, float, float, float],
    sample: str | None,
    backend: Backend,
    theme: Theme,
) -> None:
    """The recessed numeric/string value-display cell — skipped entirely
    (drawn nothing) when the type has no representative glyph (Boolean is
    a button, Path/cluster have none)."""
    if not sample:
        return
    x1, y1, x2, y2 = bounds
    if x2 - x1 < 4 or y2 - y1 < 4:
        return
    backend.rect(
        x1, y1, x2, y2, fill=theme.fp_value_fill, stroke="#999999", stroke_width=0.75
    )
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    avail = (x2 - x1) - 3
    fsize = 9.0
    while fsize > 6.0 and backend.measure_text(sample, fsize) > avail:
        fsize -= 0.5
    text = (
        sample
        if backend.measure_text(sample, fsize) <= avail
        else fit_label(sample, avail, backend, fsize)
    )
    backend.text(cx, cy + fsize / 3, text, fsize, fill=theme.fp_value_text)


def _draw_array_index_column(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    dims: int,
    backend: Backend,
    theme: Theme,
) -> float:
    """The array index-display column, one small cell per dimension on the
    LEFT of the panel, labelled with successive index letters (i, j, k, ...)
    when a cell is tall enough for text. Returns the column's right edge, so
    the caller can place the value cell beside it."""
    width = min(10.0, max(1.0, x2 - x1))
    height = (y2 - y1) / dims
    for i in range(dims):
        cy1 = y1 + i * height
        cy2 = cy1 + height
        backend.rect(
            x1,
            cy1,
            x1 + width,
            cy2,
            fill=theme.fp_index_fill,
            stroke="#333333",
            stroke_width=0.5,
        )
        if height >= 5.0:
            letter = _INDEX_LETTERS[i % len(_INDEX_LETTERS)]
            fsize = min(7.0, height - 1.0)
            backend.text(
                x1 + width / 2,
                (cy1 + cy2) / 2 + fsize / 3,
                letter,
                fsize,
                fill=theme.fp_value_text,
            )
    return x1 + width


def draw_fp_terminal(
    terminal: FPTerminal,
    bounds: tuple[float, float, float, float],
    backend: Backend,
    theme: Theme = DEFAULT_THEME,
    label_visible: bool = True,
    label_bounds: tuple[float, float, float, float] | None = None,
) -> None:
    """Draw a control/indicator in LabVIEW's icon view: a grey panel bordered
    in the SCALAR/ELEMENT type's color (thick border = control, thin =
    indicator), a wire-port triangle on the dataflow edge (right for a
    control's output, left for an indicator's input), a bottom-center type
    label, a recessed value-display cell, and — for arrays — an index column
    on the left. The name label stays ABOVE the box, as before."""
    x1, y1, x2, y2 = bounds
    lv_type = terminal.lv_type
    is_array = lv_type is not None and lv_type.kind == LVTypeKind.ARRAY
    scalar_type = lv_type.element_type if lv_type is not None and is_array else lv_type
    color = wire_style(scalar_type, theme).color
    stroke_width = 1.5 if terminal.is_indicator else 3.0

    backend.rect(
        x1, y1, x2, y2, fill=theme.fp_panel, stroke=color, stroke_width=stroke_width
    )

    label = terminal.name or ""
    if label and label_visible:
        if label_bounds is not None:
            # Honor the DEVELOPER-PLACED label from the heap (``label_bounds`` —
            # the fPTerm's own ``<label>`` rect, absolute x/y). The box AUTO-GREW
            # (howGrow) to the text at LabVIEW's font, so it IS the text's own
            # extent: derive the font size that fills it (largest single line
            # fitting the box) rather than a fixed size that leaves the name
            # swimming in white-space. Justify TOWARD the terminal — a label left
            # of the terminal is right-justified (its text ends at the terminal
            # edge), one to the right is left-justified, above/below centered —
            # matching how LabVIEW anchors labels so close terminals don't
            # collide.
            lx1, ly1, lx2, ly2 = label_bounds
            # The box auto-grew to text PLUS LabVIEW's small internal padding, so
            # fitting text edge-to-edge over-sizes the font and butts it against
            # the neighbour. Inset the box by a uniform margin on ALL sides — it
            # holds the fitted font off every edge and reproduces LabVIEW's gap
            # in every label position (left/right of a terminal, above/below).
            m = _FP_LABEL_MARGIN
            size, _, lines = fit_wrapped(
                label,
                (lx2 - lx1) - 2 * m,
                (ly2 - ly1) - 2 * m,
                backend,
                max_size=(ly2 - ly1) - 2 * m,
                max_lines=1,
            )
            shown = lines[0] if lines else label
            ty = (ly1 + ly2) / 2 + size * 0.35
            lcx, tcx = (lx1 + lx2) / 2, (x1 + x2) / 2
            if lcx < tcx - 1:  # label sits LEFT of the terminal
                backend.text(lx2 - m, ty, shown, size, anchor="end", fill=theme.text)
            elif lcx > tcx + 1:  # label sits RIGHT of the terminal
                backend.text(lx1 + m, ty, shown, size, anchor="start", fill=theme.text)
            else:  # centered above/below
                backend.text(lcx, ty, shown, size, fill=theme.text)
        else:
            # No saved rect: the default full name, centered just above the box,
            # overflowing the terminal width, never truncated.
            backend.text((x1 + x2) / 2, y1 - 4, label, 8.0, fill=theme.text)

    type_label = _fp_type_label(terminal, scalar_type)
    compact = x2 - x1 < _FP_MIN_ICON_SIZE or y2 - y1 < _FP_MIN_ICON_SIZE
    if type_label:
        # COMPACT view: too small for the index-column chrome that normally
        # signals an array, so bracket an array's element type — "[DBL]" — to
        # keep the array-ness obvious. The icon view leaves it bare (its index
        # column already reads as the brackets).
        shown = f"[{type_label}]" if compact and is_array else type_label
        backend.text((x1 + x2) / 2, y2 - 2, shown, 7, fill=color, bold=True)

    if compact:
        return  # too small for the wire port / index / value cells

    tri = 5.5
    cy_mid = (y1 + y2) / 2
    # The wire-port triangle sits INSIDE the box (LabVIEW draws it within the
    # control/indicator border, not poking out), always pointing right in the
    # dataflow direction: an indicator's arrow is tucked against the left inner
    # edge (data enters), a control's against the right inner edge (data exits).
    if terminal.is_indicator:
        port = [(x1, cy_mid - tri * 0.6), (x1, cy_mid + tri * 0.6), (x1 + tri, cy_mid)]
    else:
        port = [
            (x2 - tri, cy_mid - tri * 0.6),
            (x2 - tri, cy_mid + tri * 0.6),
            (x2, cy_mid),
        ]
    backend.polygon(port, fill="#ffffff", stroke=color, stroke_width=1.0)

    margin = 5.0  # padding from the box border to the inner cells (per GT)
    value_h = 10.0  # display cells occupy only the upper strip (GT ~10px)
    idx_w = 8.0  # index-column width
    idx_cell_h = 7.0  # per index row
    value_x1, value_y1 = x1 + margin, y1 + margin
    value_x2 = x2 - margin
    value_y2 = value_y1 + value_h

    if is_array:
        idx_bottom = value_y1 + idx_cell_h * _ARRAY_INDEX_ROWS
        value_x1 = (
            _draw_array_index_column(
                value_x1,
                value_y1,
                value_x1 + idx_w,
                idx_bottom,
                _ARRAY_INDEX_ROWS,
                backend,
                theme,
            )
            + 2.0
        )

    value_bounds = (value_x1, value_y1, value_x2, value_y2)
    fam = type_family(scalar_type)
    if fam == "error_cluster":
        ErrorClusterGlyph().draw(backend, value_bounds, theme)
    elif fam == "variant":
        VariantGlyph().draw(backend, value_bounds, theme)
    else:
        sample = numeric_sample(scalar_type)
        _draw_fp_value_cell(value_bounds, sample, backend, theme)


def _draw_layer_coercion_dots(
    nets: list[RenderWireNet],
    dots: list[Point],
    backend: Backend,
    theme: Theme,
) -> None:
    for net in nets:
        for dx, dy in net.coercion_dots:
            backend.circle(
                dx, dy, 2.0, fill=theme.coercion_dot, stroke="#ffffff", stroke_width=0.5
            )
    for dx, dy in dots:
        backend.circle(
            dx, dy, 2.0, fill=theme.coercion_dot, stroke="#ffffff", stroke_width=0.5
        )

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
    LocalVariableNode,
    PrimitiveNode,
    SequenceNode,
    VINode,
)
from ..models import FPTerminal, LVType, Terminal
from .backend import Backend, Point
from .glyph import (
    ArithGlyph,
    ConstantGlyph,
    ErrorClusterGlyph,
    VariantGlyph,
    WrappedBoxGlyph,
    fit_label,
)
from .scene import (
    FramePath,
    RenderBorderTerminal,
    RenderNode,
    RenderStructure,
    RenderTerminal,
    RenderWireNet,
    Scene,
    _exit_side,
    _is_default_visible,
    _wire_edge_point,
    _wire_role,
    encode_frame_path,
)
from .style import (
    DEFAULT_THEME,
    Theme,
    numeric_sample,
    type_family,
    type_repr,
    wire_style,
)

# Structure node_type -> border style key (graph-sourced; see node_type
# values assigned in graph/construction.py / graph/core.py::_NODE_TYPE_NAMES).
_STRUCTURE_STYLE = {
    "forLoop": "forLoop",
    "whileLoop": "whileLoop",
    "caseStruct": "case",
    "select": "case",
    "flatSequence": "flatSequence",
    "seq": "stackedSequence",
    "sequence": "stackedSequence",
}

# LabVIEW's array-control terminal icon always shows a 3-row index display
# (i / j / k) as fixed chrome, independent of the array's real dimensionality.
_ARRAY_INDEX_ROWS = 3


def _is_interactive_structure(node: object) -> bool:
    """Case structures and STACKED sequences get selector chrome + per-frame
    ``lv-frame``/``lv-selector`` groups; flat sequences (film-strip, every
    frame always visible) and loops do not."""
    return isinstance(node, CaseStructureNode) or (
        isinstance(node, SequenceNode) and node.node_type != "flatSequence"
    )


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
            cx, cy, _INVERT_BUBBLE_R,
            fill=theme.canvas, stroke=theme.prim_stroke, stroke_width=1.0,
        )


def _inset(bounds, frac: float = 0.075):
    """Shrink a rect toward its center by ``frac`` on each side. Border-terminal
    termBounds are the clickable region; LabVIEW draws the visible glyph inset
    within it (~15% smaller overall)."""
    x1, y1, x2, y2 = bounds
    dx, dy = (x2 - x1) * frac, (y2 - y1) * frac
    return x1 + dx, y1 + dy, x2 - dx, y2 - dy

def _glyph_bounds(node: RenderNode) -> tuple[float, float, float, float]:
    """The rect a node's glyph is ACTUALLY drawn at — same special case as
    ``draw_node`` for arithmetic primitives (see its docstring): the union of
    terminal ``termBounds``, not the full 32x32 clickable box. Shared with
    the connector-panel drawer (``_draw_connector_panel``) so the panel's
    terminal geometry (fx/fy) is computed against the SAME rect the glyph is
    actually drawn in, not a different one."""
    bounds = node.bounds
    if isinstance(node.glyph, ArithGlyph):
        rects = [t.bounds for t in node.terminals if t.bounds is not None]
        if rects:
            bounds = (
                min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects),
            )
    return bounds


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
    if tooltip is None and isinstance(node.glyph, ConstantGlyph) and node.glyph.value:
        # A string/path constant's in-box text is fit to the box and may be
        # ellipsized ("…"); expose the FULL value on hover so it stays readable.
        if type_family(getattr(node.node, "lv_type", None)) in ("string", "path"):
            tooltip = node.glyph.value
    if tooltip:
        backend.begin_group(cls="lv-node", data={"node": node.node.id}, title=tooltip)

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
            backend.text((x1 + x2) / 2, y2 + 9, name, 8.0)

    if tooltip:
        backend.end_group()


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
        header = f"Local Variable: {name}" if name else "Local Variable"
    elif isinstance(node, VINode | PrimitiveNode):
        header = node.name or None
        # Unresolved primitive: the hover header matches the box — "#<prim_id>"
        # instead of the verbose "unknown_primitive_N" placeholder. The
        # connector-pane panel below still lists every terminal we know.
        if (isinstance(node, PrimitiveNode) and node.prim_id is not None
                and node.name == f"unknown_primitive_{node.prim_id}"):
            header = f"#{node.prim_id}"
    if header is None:
        return None
    desc = getattr(node, "description", None)
    return header, (desc or None)


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
    return "\n".join(lines)


def _terminal_label(t: Terminal) -> str:
    """A terminal's display label: the resolved def name (``display_name``,
    e.g. "x"/"difference"), else a caller-side ``name`` if any, else
    ``terminal N`` from its connector-pane index."""
    return t.display_name or t.name or f"terminal {t.index}"


def _terminal_is_informative(t: Terminal) -> bool:
    """Whether a terminal is worth showing in context help. Skips pure EMPTY
    connector-pane slots — a position with no label AND no meaningful type
    (unwired, unnamed pane slots, e.g. a big DAQmx SubVI's spare terminals);
    any labeled or typed terminal is kept."""
    if t.display_name or t.name:
        return True
    ty = t.lv_type.to_python() if t.lv_type else None
    return ty not in (None, "None", "Any")


def _terminal_help_lines(node: AnyGraphNode) -> list[str]:
    """The node's connector pane as tooltip lines — inputs then outputs, each
    ``label: type``. Empty pane slots (no label, no type) are omitted; labels
    prefer the resolved def name, then a caller-side name, then ``terminal N``.
    """
    terms = [
        t for t in (getattr(node, "terminals", None) or [])
        if _terminal_is_informative(t)
    ]

    def fmt(t: Terminal) -> str:
        ty = t.lv_type.to_python() if t.lv_type else "?"
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

_PANE_PAD = 6.0            # panel inner padding
_PANE_TITLE_SIZE = 9.0
_PANE_DESC_SIZE = 7.5
_PANE_LABEL_SIZE = 7.5      # terminal name
_PANE_TYPE_SIZE = 6.5       # terminal type — smaller/lighter, secondary info
_PANE_TYPE_COLOR = "#777777"
_PANE_MAX_LABEL_W = 130.0   # per-terminal name+type truncation width
_PANE_MAX_HEADER_W = 220.0  # title/description truncation width

_PANE_STUB_OUT = 12.0       # straight run out of the icon edge, before a jog
_PANE_STUB_FULL = 20.0      # full stub length, icon edge -> label anchor
_PANE_TEXT_GAP = 3.0        # label anchor -> first glyph of text
_PANE_STUB_MARGIN = _PANE_STUB_FULL + _PANE_TEXT_GAP  # edge -> text start

_PANE_ROW_MIN_GAP = 11.0    # min vertical spacing between left/right labels
_PANE_ROW_HALF = _PANE_LABEL_SIZE / 2 + 2.0  # vertical pad around a label row
_PANE_COL_PAD = 6.0         # min horizontal padding between top/bottom labels
_PANE_LINE_H = _PANE_LABEL_SIZE + 2.0  # one text line's height (top/bottom)

_PANE_ICON_TARGET = 60.0    # target longer-side px for the scaled icon
_PANE_ICON_MIN = 18.0
_PANE_ICON_MAX = 90.0

# fx/fy classification -> the outward unit normal a terminal's stub exits on.
_SIDE_NORMAL: dict[str, Point] = {
    "left": (-1.0, 0.0), "right": (1.0, 0.0),
    "top": (0.0, -1.0), "bottom": (0.0, 1.0),
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
        return self.name_w + self.type_w


def _term_side_and_frac(
    rt: RenderTerminal, bounds: tuple[float, float, float, float],
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


def _pane_label(rt: RenderTerminal, side: str, frac: float,
                 backend: Backend, theme: Theme) -> _PaneLabel:
    t = rt.terminal
    name = _terminal_label(t)
    type_str = f" ({t.lv_type.to_python() if t.lv_type else '?'})"
    name_w = backend.measure_text(name, _PANE_LABEL_SIZE)
    type_w = backend.measure_text(type_str, _PANE_TYPE_SIZE)
    if name_w + type_w > _PANE_MAX_LABEL_W:
        # Type stays intact (short, meaningful); the name shrinks to fit.
        avail = max(10.0, _PANE_MAX_LABEL_W - type_w)
        name = fit_label(name, avail, backend, _PANE_LABEL_SIZE)
        name_w = backend.measure_text(name, _PANE_LABEL_SIZE)
    style = wire_style(t.lv_type, theme)
    return _PaneLabel(
        side, frac, name, name_w, type_str, type_w, style.color, style.width,
    )


def _spread_1d(values: list[float], min_gaps: list[float]) -> list[float]:
    """Nudge sorted ``values`` apart so consecutive entries are at least
    ``min_gaps[i]`` (the gap required BEFORE entry ``i``, i>=1) apart, then
    re-centers the whole run on its original mean so one crowded pair
    doesn't drag the group toward one end. Pure function of its inputs —
    deterministic (no set/dict iteration, no hashing) so panel layout is
    byte-reproducible across runs/hash seeds."""
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
    stub is a single straight run; when the label was spread apart for
    legibility (see ``_spread_1d``) the stub becomes a proper elbow/Z —
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

    sided: dict[str, list[_PaneLabel]] = {
        "left": [], "right": [], "top": [], "bottom": [],
    }
    # Terminals sharing an identical ``termBounds`` rect occupy ONE connector-
    # pane slot: a growable node (e.g. Scan From String) stores the slot
    # position once on the input-default half, and the wired output half
    # inherits it, so both alias to the same rect. They cannot both sit on that
    # one point and we lack the output's own geometry — so place each co-located
    # terminal by its KNOWN graph direction (input->left, output->right)
    # instead of the shared rect, keeping the geometric ``frac`` for its height
    # along that side. De-collides the pair AND puts the wired output on its
    # correct side.
    seen_rects: set[tuple[float, float, float, float]] = set()
    shared_rects: set[tuple[float, float, float, float]] = set()
    for rt in node.terminals:
        if rt.bounds is not None:
            (shared_rects if rt.bounds in seen_rects else seen_rects).add(rt.bounds)
    for rt in node.terminals:
        if not _terminal_is_informative(rt.terminal):
            continue
        side, frac = _term_side_and_frac(rt, bounds)
        if rt.bounds is not None and rt.bounds in shared_rects:
            side = "right" if rt.terminal.direction == "output" else "left"
        sided[side].append(_pane_label(rt, side, frac, backend, theme))
    for labels in sided.values():
        labels.sort(key=lambda lb: lb.frac)

    # Icon: the node's OWN glyph, uniformly scaled to a legible size,
    # preserving its real aspect ratio (never distorted, never a fixed box).
    long_side = max(bw, bh)
    scale = _PANE_ICON_TARGET / long_side
    icon_w, icon_h = bw * scale, bh * scale
    if max(icon_w, icon_h) > _PANE_ICON_MAX:
        s = _PANE_ICON_MAX / max(icon_w, icon_h)
        icon_w, icon_h = icon_w * s, icon_h * s
    if min(icon_w, icon_h) < _PANE_ICON_MIN:
        s = _PANE_ICON_MIN / max(1e-6, min(icon_w, icon_h))
        icon_w, icon_h = icon_w * s, icon_h * s

    # Left/right: labels stack VERTICALLY at their true icon-relative height
    # (fy * icon_h), separated just enough to stay legible.
    left_y0 = [lb.frac * icon_h for lb in sided["left"]]
    right_y0 = [lb.frac * icon_h for lb in sided["right"]]
    left_y = _spread_1d(left_y0, [_PANE_ROW_MIN_GAP] * len(left_y0))
    right_y = _spread_1d(right_y0, [_PANE_ROW_MIN_GAP] * len(right_y0))
    left_w = max((lb.total_w for lb in sided["left"]), default=0.0)
    right_w = max((lb.total_w for lb in sided["right"]), default=0.0)

    # Top/bottom: labels sit side by side HORIZONTALLY at their true
    # icon-relative x (fx * icon_w), separated by their own text widths.
    top_x0 = [lb.frac * icon_w for lb in sided["top"]]
    bottom_x0 = [lb.frac * icon_w for lb in sided["bottom"]]

    def _col_gaps(labels: list[_PaneLabel]) -> list[float]:
        gaps = [0.0] * len(labels)
        for i in range(1, len(labels)):
            gaps[i] = (labels[i - 1].total_w + labels[i].total_w) / 2 + _PANE_COL_PAD
        return gaps

    top_x = _spread_1d(top_x0, _col_gaps(sided["top"]))
    bottom_x = _spread_1d(bottom_x0, _col_gaps(sided["bottom"]))

    # Unified diagram extents (icon-local frame: icon spans [0,icon_w] x
    # [0,icon_h]) over EVERY side's labels, so the panel is exactly as wide/
    # tall as it needs to be — no wasted margin, nothing clipped.
    xs_min, xs_max = [0.0], [icon_w]
    if sided["left"]:
        xs_min.append(-(_PANE_STUB_MARGIN + left_w))
    if sided["right"]:
        xs_max.append(icon_w + _PANE_STUB_MARGIN + right_w)
    for x, lb in zip(top_x, sided["top"]):
        xs_min.append(x - lb.total_w / 2)
        xs_max.append(x + lb.total_w / 2)
    for x, lb in zip(bottom_x, sided["bottom"]):
        xs_min.append(x - lb.total_w / 2)
        xs_max.append(x + lb.total_w / 2)
    x_min, x_max = min(xs_min), max(xs_max)
    diagram_w = x_max - x_min

    ys_min, ys_max = [0.0], [icon_h]
    if left_y:
        ys_min.append(min(left_y) - _PANE_ROW_HALF)
        ys_max.append(max(left_y) + _PANE_ROW_HALF)
    if right_y:
        ys_min.append(min(right_y) - _PANE_ROW_HALF)
        ys_max.append(max(right_y) + _PANE_ROW_HALF)
    mid_h = max(ys_max) - min(ys_min)
    top_h = (_PANE_STUB_MARGIN + _PANE_LINE_H) if sided["top"] else 0.0
    bottom_h = (_PANE_STUB_MARGIN + _PANE_LINE_H) if sided["bottom"] else 0.0
    diagram_h = top_h + mid_h + bottom_h

    full_title_w = backend.measure_text(header, _PANE_TITLE_SIZE)
    inner_w = max(diagram_w, min(full_title_w, _PANE_MAX_HEADER_W))
    full_desc_w = backend.measure_text(desc, _PANE_DESC_SIZE) if desc else 0.0
    if desc:
        inner_w = max(inner_w, min(full_desc_w, _PANE_MAX_HEADER_W))
    title_line = (
        header if full_title_w <= inner_w
        else fit_label(header, inner_w, backend, _PANE_TITLE_SIZE)
    )
    desc_line = None
    if desc:
        desc_line = (
            desc if full_desc_w <= inner_w
            else fit_label(desc, inner_w, backend, _PANE_DESC_SIZE)
        )

    header_h = 11.0 + (11.0 if desc_line else 0.0)
    panel_w = inner_w + 2 * _PANE_PAD
    panel_h = header_h + _PANE_PAD + diagram_h + _PANE_PAD

    # Everything below is drawn in a small LOCAL coordinate space around the
    # origin — panels are positioned/clamped at runtime by JS (see
    # render/__init__.py), not baked in relative to the node's own position.
    diagram_x0 = _PANE_PAD + max(0.0, (inner_w - diagram_w) / 2)
    icon_x1 = diagram_x0 - x_min
    icon_x2 = icon_x1 + icon_w
    diagram_y0 = header_h + _PANE_PAD
    icon_y1 = diagram_y0 + top_h - min(ys_min)
    icon_y2 = icon_y1 + icon_h

    backend.begin_group(
        cls="lv-help", data={"node": node.node.id},
        style="visibility:hidden;pointer-events:none",
    )
    backend.rect(
        0.0, 0.0, panel_w, panel_h,
        fill=theme.canvas, stroke=theme.struct_border, stroke_width=1.0, rx=3,
    )
    cx = panel_w / 2
    backend.text(cx, _PANE_PAD + 7.0, title_line, _PANE_TITLE_SIZE, bold=True)
    if desc_line:
        backend.text(cx, _PANE_PAD + 18.0, desc_line, _PANE_DESC_SIZE)

    node.glyph.draw(backend, (icon_x1, icon_y1, icon_x2, icon_y2), theme)

    def _draw_side(labels: list[_PaneLabel], side: str, final: list[float]) -> None:
        for lb, perp in zip(labels, final):
            if side == "left":
                edge = (icon_x1, icon_y1 + lb.frac * icon_h)
            elif side == "right":
                edge = (icon_x2, icon_y1 + lb.frac * icon_h)
            elif side == "top":
                edge = (icon_x1 + lb.frac * icon_w, icon_y1)
            else:
                edge = (icon_x1 + lb.frac * icon_w, icon_y2)
            final_perp = perp + (icon_y1 if side in ("left", "right") else icon_x1)
            pts = _pane_stub_points(edge, side, final_perp)
            backend.path(pts, stroke=lb.color, stroke_width=lb.width)
            end_x, end_y = pts[-1]
            if side == "left":
                ty = end_y + _PANE_LABEL_SIZE * 0.35
                backend.text(end_x - _PANE_TEXT_GAP, ty, lb.type_str, _PANE_TYPE_SIZE,
                             fill=_PANE_TYPE_COLOR, anchor="end")
                backend.text(end_x - _PANE_TEXT_GAP - lb.type_w, ty, lb.name,
                             _PANE_LABEL_SIZE, anchor="end")
            elif side == "right":
                ty = end_y + _PANE_LABEL_SIZE * 0.35
                backend.text(end_x + _PANE_TEXT_GAP, ty, lb.name, _PANE_LABEL_SIZE,
                             anchor="start")
                backend.text(end_x + _PANE_TEXT_GAP + lb.name_w, ty, lb.type_str,
                             _PANE_TYPE_SIZE, fill=_PANE_TYPE_COLOR, anchor="start")
            else:
                start_x = end_x - lb.total_w / 2
                ty = (
                    end_y - _PANE_TEXT_GAP if side == "top"
                    else end_y + _PANE_TEXT_GAP + _PANE_LABEL_SIZE * 0.8
                )
                backend.text(start_x, ty, lb.name, _PANE_LABEL_SIZE, anchor="start")
                backend.text(start_x + lb.name_w, ty, lb.type_str, _PANE_TYPE_SIZE,
                             fill=_PANE_TYPE_COLOR, anchor="start")

    _draw_side(sided["left"], "left", left_y)
    _draw_side(sided["right"], "right", right_y)
    _draw_side(sided["top"], "top", top_x)
    _draw_side(sided["bottom"], "bottom", bottom_x)

    backend.end_group()


def draw_help_overlay(
    nodes: list[RenderNode], backend: Backend, theme: Theme = DEFAULT_THEME,
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
    bt: RenderBorderTerminal, backend: Backend, theme: Theme,
) -> None:
    """Draw a structure border glyph from its fixed ``glyph_kind`` — a
    geometry-side decoration (see ``scene._structure_borders``), never
    re-derived from heap class strings here."""
    x1, y1, x2, y2 = _inset(bt.bounds)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    kind = bt.glyph_kind

    if kind in ("N", "i"):
        backend.rect(x1, y1, x2, y2, fill=theme.loop_term_fill,
                     stroke=theme.loop_term, stroke_width=1.5)
        backend.text(cx, cy + 4, kind, 11, fill=theme.loop_term, italic=True)
        return
    if kind == "cond":
        # Judgment call: LabVIEW distinguishes "Stop if True" (red stop
        # circle) from "Continue if True" (green arrow) — that mode isn't
        # captured anywhere in the graph, so this always draws the more
        # common default (Stop if True).
        r = min(x2 - x1, y2 - y1) / 2
        backend.circle(cx, cy, r, fill=theme.cond_stop)
        return
    if kind == "selector":
        # The selector terminal takes the WIRE TYPE COLOR of whatever feeds it
        # (bool -> green, enum -> blue, error cluster -> mustard, ...), not a
        # flat hardcoded green. Neutral pale fill so the "?" stays legible.
        col = bt.color or theme.selector_stroke
        backend.rect(x1, y1, x2, y2, fill=theme.loop_term_fill,
                     stroke=col, stroke_width=1.2)
        backend.text(cx, cy + 4, "?", 10, fill=col)
        return
    if kind in ("sr_down", "sr_up"):
        # Shift register: a type-colored box with a filled triangle glyph.
        col = bt.color or theme.sr_stroke
        backend.rect(x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=col,
                     stroke_width=1.2)
        if kind == "sr_up":
            tri = [(x1 + 2, y2 - 2), (cx, y1 + 2), (x2 - 2, y2 - 2)]
        else:
            tri = [(x1 + 2, y1 + 2), (cx, y2 - 2), (x2 - 2, y1 + 2)]
        backend.polygon(tri, fill=col)
        return
    if kind == "autoindex":
        # Array auto-indexing tunnel: a pale box with a dark border and the
        # element brackets drawn as SHAPES (not text) in the wire type color.
        # The brackets sit padded inside the box with LONG serifs so [ and ]
        # nearly meet top and bottom — reading as a square inside the square.
        col = bt.color or "#333333"
        backend.rect(x1, y1, x2, y2, fill=theme.loop_term_fill,
                     stroke=theme.tunnel_border, stroke_width=1.2)
        # A centered SQUARE inner region (side = min box dimension), padded.
        side = min(x2 - x1, y2 - y1) * (1 - 2 * 0.26)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        lx, rx = mx - side / 2, mx + side / 2
        ty, by2 = my - side / 2, my + side / 2
        sr = side * 0.40   # long serifs: [ and ] nearly close at top/bottom
        backend.path([(lx + sr, ty), (lx, ty), (lx, by2), (lx + sr, by2)],
                     stroke=col, stroke_width=1.3)
        backend.path([(rx - sr, ty), (rx, ty), (rx, by2), (rx - sr, by2)],
                     stroke=col, stroke_width=1.3)
        return
    if kind == "tunnel":
        # Last-value passthrough: a solid block filled in the wire type color.
        fill = bt.color or theme.wire_default
        backend.rect(x1, y1, x2, y2, fill=fill, stroke="#333333", stroke_width=0.75)
        return
        return
    # A border DCO the fixed glyph table doesn't cover — undecorated box
    # rather than a guessed glyph.
    backend.rect(x1, y1, x2, y2, fill="#ffffff", stroke=theme.struct_border,
                 stroke_width=1.0)


def _draw_for_loop_border(x1, y1, x2, y2, backend: Backend, theme: Theme) -> None:
    """The For-Loop's signature stacked-card border — three identical cards
    fanning DOWN-RIGHT from a top-left-aligned front card. Drawn as OUTLINES
    ONLY (no fill) so the whole border sits ON TOP of the wires: the loop edge
    and the card slivers are never notched by wire casing, and there is no fill
    to cover a wire running inside the loop. For each card behind the front, only
    its VISIBLE L is stroked (top-right sliver -> right edge -> bottom edge ->
    bottom-left sliver) — the edges that would fall inside the front card are
    omitted, so no line shows through the front card's interior. The heap bounds
    are the BACKMOST card's bottom-right corner; the front card is (2o x 2o)
    smaller and top-left-aligned (measured against the ground truth)."""
    o = 2.0
    s = theme.struct_border
    w2, h2 = (x2 - x1) - 2 * o, (y2 - y1) - 2 * o
    fx2, fy2 = x1 + w2, y1 + h2  # front card bottom-right
    for k in (2, 1):  # back + mid cards, offset +k*o down-right of the front card
        backend.path(
            [(fx2, y1 + k * o), (fx2 + k * o, y1 + k * o),
             (fx2 + k * o, fy2 + k * o), (x1 + k * o, fy2 + k * o),
             (x1 + k * o, fy2)],
            fill="none", stroke=s, stroke_width=1.2,
        )
    # Front card (loop boundary), top-left-aligned, dog-eared bottom-right.
    f = 6.0
    backend.path(
        [(x1, y1), (fx2, y1), (fx2, fy2 - f), (fx2 - f, fy2), (x1, fy2), (x1, y1)],
        fill="none", stroke=s, stroke_width=1.2,
    )
    backend.path([(fx2, fy2 - f), (fx2 - f, fy2 - f), (fx2 - f, fy2)],
                 stroke=s, stroke_width=1.2)


def _draw_while_loop_border(x1, y1, x2, y2, backend: Backend, theme: Theme) -> None:
    backend.rect(x1, y1, x2, y2, rx=7, fill="none", stroke=theme.struct_border,
                 stroke_width=1.2)


# Height of a case/stacked-sequence selector bar, drawn INSIDE the top of the
# structure's heap bounds (the selector sits within the frame, per LabVIEW — its
# heap termBounds are inside the bounds; there is no band above the top edge).
_CASE_BAR_H = 14.0
_SELECTOR_SIZE = 9.0        # font size of the value + arrows
_SELECTOR_TRI_W = 11.0      # width of the dropdown-triangle zone (case only)
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
    text_cx: float   # center-x of the value text (its own zone, left of ▼)
    baseline: float  # shared baseline y for value + arrows
    left_x: float    # ◄ center-x (in the left arrow cell)
    right_x: float   # ► center-x (in the right arrow cell)


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
    structure: RenderStructure, scene: Scene, *, has_dropdown: bool,
    backend: Backend,
) -> _SelectorGeom:
    x1, y1, x2, _ = structure.bounds
    values = scene.frame_values.get(structure.raw_uid, [])
    max_val_w = max(
        (backend.measure_text(_frame_display(structure, scene, v), _SELECTOR_SIZE)
         for v in values),
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
        outer=(ox1, oy1, ox2, oy2), box=box, tri=tri,
        text_cx=(vc1 + text_right) / 2, baseline=baseline,
        left_x=(ox1 + vc1) / 2, right_x=(vc2 + ox2) / 2,
    )


def _error_border_color(
    scene: Scene, raw_uid: str, value: str, theme: Theme,
) -> str | None:
    """Green (No Error) / red (Error) border color for an error-cluster case's
    frame, or ``None`` if this structure isn't an error case. LabVIEW colors the
    case border by the shown frame: green for the No-Error case, red otherwise.
    """
    err = scene.error_frame_no_error.get(raw_uid)
    if err is None:
        return None
    return (
        theme.case_no_error_border if err.get(value, False)
        else theme.case_error_border
    )


def _draw_frame_border(
    structure: RenderStructure, scene: Scene, backend: Backend, theme: Theme,
) -> None:
    """The interactive structure's outer box only. LabVIEW draws NO header
    band — the selector is a compact ``◄ value ▼ ►`` widget that sits inside
    the top of the frame (drawn by ``_draw_frame_selector``), not a full-width
    bar. The value text lives in the per-frame value-label groups (draw_scene).

    For an ERROR-cluster case the border is colored by the DEFAULT frame here
    (green No Error / red Error) so the static SVG is correct; per-frame colored
    borders drawn in ``draw_scene`` recolor it as the viewer switches frames."""
    x1, y1, x2, y2 = structure.bounds
    default = scene.default_frame.get(structure.raw_uid, "")
    color = _error_border_color(scene, structure.raw_uid, default, theme)
    if color is not None:
        backend.rect(x1, y1, x2, y2, fill="none", stroke=color, stroke_width=1.6)
    else:
        backend.rect(x1, y1, x2, y2, fill="none", stroke=theme.struct_border,
                     stroke_width=1.2)


def _draw_frame_selector(
    structure: RenderStructure, scene: Scene, backend: Backend, theme: Theme,
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

    # One enclosing box (white) with vertical dividers between the flanking
    # arrow cells and the central value cell — the LabVIEW selector-label look.
    backend.rect(ox1, oy1, ox2, oy2, fill="#ffffff",
                 stroke=theme.struct_border, stroke_width=0.75)
    backend.line(vc1, oy1, vc1, oy2, stroke=theme.struct_border, stroke_width=0.5)
    backend.line(vc2, oy1, vc2, oy2, stroke=theme.struct_border, stroke_width=0.5)

    def _arrow(action: str, xc: float, glyph: str, cell: tuple[float, float]) -> None:
        cx1, cx2 = cell
        backend.begin_group(
            cls="lv-selector",
            data={"lv-action": action, "lv-struct": struct},
            style="cursor:pointer",
        )
        backend.rect(cx1, oy1, cx2, oy2, fill="transparent", stroke="none")
        backend.text(xc, g.baseline, glyph, _SELECTOR_SIZE, fill=theme.case_bar_text)
        backend.end_group()

    _arrow("prev", g.left_x, "◄", (ox1, vc1))

    # Middle target — carries the frame list the JS controller reads, and (for a
    # case) draws the ▼ dropdown toggle. The value TEXT is drawn per-frame by
    # _draw_frame_value_label.
    box_data = {
        "lv-struct": struct, "lv-frames": ";".join(values), "lv-default": default,
    }
    if has_dropdown:
        box_data["lv-action"] = "toggle"
    backend.begin_group(
        cls="lv-selector", data=box_data,
        style="cursor:pointer" if has_dropdown else None,
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
    structure: RenderStructure, scene: Scene, backend: Backend, theme: Theme,
) -> None:
    """A case structure's dropdown MENU: one clickable row per frame value,
    stacked below the value box, hidden until the ▼ toggle opens it. Drawn in a
    final topmost pass so it overlays the diagram; clicking a row selects that
    frame (see the JS controller). Only cases get a menu."""
    values = scene.frame_values.get(structure.raw_uid)
    if not values:
        return
    g = _selector_geom(structure, scene, has_dropdown=True, backend=backend)
    bx1, _by1, bx2, by2 = g.box
    struct = structure.raw_uid
    zone_w = (bx2 - bx1) - 6.0
    backend.begin_group(
        cls="lv-menu", data={"lv-struct": struct}, style="display:none",
    )
    for i, v in enumerate(values):
        ry1 = by2 + i * _MENU_ROW_H
        ry2 = ry1 + _MENU_ROW_H
        backend.begin_group(
            cls="lv-option", data={"lv-struct": struct, "lv-value": v},
            style="cursor:pointer",
        )
        backend.rect(bx1, ry1, bx2, ry2, fill="#ffffff",
                     stroke="#999999", stroke_width=0.5)
        # The row's DISPLAY is the faithful typed label (enum name, No Error /
        # Error, quoted string, ...); the raw value stays the click identity in
        # ``lv-value`` above so the JS controller still matches frame paths.
        label = _frame_display(structure, scene, v)
        text = (
            label if backend.measure_text(label, _SELECTOR_SIZE) <= zone_w
            else fit_label(label, zone_w, backend, _SELECTOR_SIZE)
        )
        backend.text((bx1 + bx2) / 2, ry1 + _MENU_ROW_H / 2 + _SELECTOR_SIZE * 0.34,
                     text, _SELECTOR_SIZE, fill=theme.case_bar_text)
        backend.end_group()
    backend.end_group()


def _draw_frame_value_label(
    structure: RenderStructure, scene: Scene, value: str,
    backend: Backend, theme: Theme,
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
        label if backend.measure_text(label, _SELECTOR_SIZE) <= zone_w
        else fit_label(label, zone_w, backend, _SELECTOR_SIZE)
    )
    backend.text(g.text_cx, g.baseline, text, _SELECTOR_SIZE, fill=theme.case_bar_text)


def _draw_sequence_border(
    structure: RenderStructure, backend: Backend, theme: Theme,
) -> None:
    """Flat sequence: outer box + top/bottom rails, plus a vertical divider
    line at each inter-frame boundary (the film-strip look)."""
    x1, y1, x2, y2 = structure.bounds
    backend.rect(x1, y1, x2, y2, fill="none", stroke=theme.struct_border,
                 stroke_width=1.2)
    backend.line(x1, y1 + 4, x2, y1 + 4, stroke=theme.struct_border, stroke_width=1)
    backend.line(x1, y2 - 4, x2, y2 - 4, stroke=theme.struct_border, stroke_width=1)
    for dx in structure.dividers:
        backend.line(dx, y1, dx, y2, stroke=theme.struct_border, stroke_width=1)


def draw_structure(
    structure: RenderStructure, scene: Scene, backend: Backend,
    theme: Theme = DEFAULT_THEME,
) -> None:
    """A structure's border (and film-strip/selector chrome), drawn AFTER wires
    so it sits over the wire casing and is never notched by it. Every structure
    is outline-only (``fill="none"``) — including the For-Loop, whose stacked
    cards are stroked as visible outlines — so nothing here can cover a wire that
    runs inside the structure."""
    x1, y1, x2, y2 = structure.bounds
    kind = _STRUCTURE_STYLE.get(structure.node.node_type or "", "generic")
    if kind == "forLoop":
        _draw_for_loop_border(x1, y1, x2, y2, backend, theme)
    elif kind == "whileLoop":
        _draw_while_loop_border(x1, y1, x2, y2, backend, theme)
    elif kind in ("case", "stackedSequence"):
        _draw_frame_border(structure, scene, backend, theme)
    elif kind == "flatSequence":
        _draw_sequence_border(structure, backend, theme)
    else:
        # In Place Element Structure, event structure, or anything else —
        # a plain border (matches the prior renderer).
        backend.rect(x1, y1, x2, y2, fill="none", stroke=theme.struct_border,
                     stroke_width=1.2)
    # Border terminals (N/i/cond, tunnels, shift registers, selector) are NOT
    # drawn here — draw_scene paints them AFTER wires so a wire is never drawn
    # on top of a boundary terminal (it butts against it, like a VI's terminal).


# Fallback family lookup when a control's LVType didn't resolve — the raw
# ddo "class" string (FPTerminal.control_type) is a mechanical lookup, not
# a guess, matching the dispatch tables already used by type_defaults.py.
_CONTROL_TYPE_FAMILY = {
    "stdNum": "float", "stdNumeric": "float", "stdDBL": "float",
    "stdSGL": "float", "stdEXT": "float",
    "stdI8": "int", "stdI16": "int", "stdI32": "int", "stdI64": "int",
    "stdU8": "int", "stdU16": "int", "stdU32": "int", "stdU64": "int",
    "stdBool": "bool",
    "stdString": "string",
    "stdPath": "path",
    "stdClust": "cluster",
    "stdArray": "array",
    "stdRing": "enum", "stdEnum": "enum",
}


# Fallback terminal text when the LVType didn't resolve (control_type only).
_FAMILY_REPR = {
    "float": "DBL", "int": "I32", "bool": "TF", "string": "abc",
    "path": "Path", "enum": "Enum",
    "error_cluster": "err", "variant": "Var",
}


_INDEX_LETTERS = "ijklmn"

# Below this size (either dimension) the icon-view internals (index/value
# cells, wire port) would overflow a box this small — fall back to just the
# outer rect + type label rather than drawing something illegible.
_FP_MIN_ICON_SIZE = 20.0


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
    bounds: tuple[float, float, float, float], sample: str | None,
    backend: Backend, theme: Theme,
) -> None:
    """The recessed numeric/string value-display cell — skipped entirely
    (drawn nothing) when the type has no representative glyph (Boolean is
    a button, Path/cluster have none)."""
    if not sample:
        return
    x1, y1, x2, y2 = bounds
    if x2 - x1 < 4 or y2 - y1 < 4:
        return
    backend.rect(x1, y1, x2, y2, fill=theme.fp_value_fill,
                 stroke="#999999", stroke_width=0.75)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    avail = (x2 - x1) - 3
    fsize = 9.0
    while fsize > 6.0 and backend.measure_text(sample, fsize) > avail:
        fsize -= 0.5
    text = sample if backend.measure_text(sample, fsize) <= avail \
        else fit_label(sample, avail, backend, fsize)
    backend.text(cx, cy + fsize / 3, text, fsize, fill=theme.fp_value_text)


def _draw_array_index_column(
    x1: float, y1: float, x2: float, y2: float, dims: int,
    backend: Backend, theme: Theme,
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
        backend.rect(x1, cy1, x1 + width, cy2, fill=theme.fp_index_fill,
                     stroke="#333333", stroke_width=0.5)
        if height >= 5.0:
            letter = _INDEX_LETTERS[i % len(_INDEX_LETTERS)]
            fsize = min(7.0, height - 1.0)
            backend.text(x1 + width / 2, (cy1 + cy2) / 2 + fsize / 3, letter, fsize,
                         fill=theme.fp_value_text)
    return x1 + width


def draw_fp_terminal(
    terminal: FPTerminal, bounds: tuple[float, float, float, float],
    backend: Backend, theme: Theme = DEFAULT_THEME,
    label_visible: bool = True,
) -> None:
    """Draw a control/indicator in LabVIEW's icon view: a grey panel bordered
    in the SCALAR/ELEMENT type's color (thick border = control, thin =
    indicator), a wire-port triangle on the dataflow edge (right for a
    control's output, left for an indicator's input), a bottom-center type
    label, a recessed value-display cell, and — for arrays — an index column
    on the left. The name label stays ABOVE the box, as before."""
    x1, y1, x2, y2 = bounds
    lv_type = terminal.lv_type
    is_array = lv_type is not None and lv_type.kind == "array"
    scalar_type = lv_type.element_type if lv_type is not None and is_array else lv_type
    color = wire_style(scalar_type, theme).color
    stroke_width = 1.5 if terminal.is_indicator else 3.0

    backend.rect(x1, y1, x2, y2, fill=theme.fp_panel,
                 stroke=color, stroke_width=stroke_width)

    label = terminal.name or ""
    if label and label_visible:
        size = 8.0
        # LabVIEW default: the FULL control/indicator name sits ABOVE the box,
        # centered, overflowing the terminal width — never truncated.
        backend.text((x1 + x2) / 2, y1 - 4, label, size)

    type_label = _fp_type_label(terminal, scalar_type)
    if type_label:
        backend.text((x1 + x2) / 2, y2 - 2, type_label, 7, fill=color, bold=True)

    if x2 - x1 < _FP_MIN_ICON_SIZE or y2 - y1 < _FP_MIN_ICON_SIZE:
        return  # too small for the wire port / index / value cells

    tri = 5.5
    cy_mid = (y1 + y2) / 2
    # The wire-port triangle sits INSIDE the box (LabVIEW draws it within the
    # control/indicator border, not poking out), always pointing right in the
    # dataflow direction: an indicator's arrow is tucked against the left inner
    # edge (data enters), a control's against the right inner edge (data exits).
    if terminal.is_indicator:
        port = [(x1, cy_mid - tri * 0.6), (x1, cy_mid + tri * 0.6),
                (x1 + tri, cy_mid)]
    else:
        port = [(x2 - tri, cy_mid - tri * 0.6), (x2 - tri, cy_mid + tri * 0.6),
                (x2, cy_mid)]
    backend.polygon(port, fill="#ffffff", stroke=color, stroke_width=1.0)

    margin = 5.0       # padding from the box border to the inner cells (per GT)
    value_h = 10.0     # display cells occupy only the upper strip (GT ~10px)
    idx_w = 8.0        # index-column width
    idx_cell_h = 7.0   # per index row
    value_x1, value_y1 = x1 + margin, y1 + margin
    value_x2 = x2 - margin
    value_y2 = value_y1 + value_h

    if is_array:
        idx_bottom = value_y1 + idx_cell_h * _ARRAY_INDEX_ROWS
        value_x1 = _draw_array_index_column(
            value_x1, value_y1, value_x1 + idx_w, idx_bottom,
            _ARRAY_INDEX_ROWS, backend, theme,
        ) + 2.0

    value_bounds = (value_x1, value_y1, value_x2, value_y2)
    fam = type_family(scalar_type)
    if fam == "error_cluster":
        ErrorClusterGlyph().draw(backend, value_bounds, theme)
    elif fam == "variant":
        VariantGlyph().draw(backend, value_bounds, theme)
    else:
        sample = numeric_sample(scalar_type)
        _draw_fp_value_cell(value_bounds, sample, backend, theme)


def _draw_layer_content(
    structures: list[RenderStructure], nets: list[RenderWireNet],
    nodes: list[RenderNode], scene: Scene, backend: Backend, theme: Theme,
) -> None:
    """One layer, in three stacked passes: wires -> structure OUTLINES +
    boundary terminals -> nodes. Reused for the base layer and each frame group.

    Every structure is outline-only (no fill — even the For-Loop's stacked
    cards), so the whole border is drawn OVER the wires: the wire's casing never
    notches an outline, and a tunnel sits on top of the wire it receives, while
    nothing covers a wire that runs inside a structure. Casing is painted per-NET
    (all of a net's casings, then all its colors) so the net's own trunk stays
    solid while the NEXT net's casing breaks the prior net's color at an
    orthogonal crossing (see ``Theme.wire_casing``)."""
    # Pass 1 — wires (per-net casing then color).
    casing = theme.wire_casing
    for net in nets:
        if casing > 0:
            for branch in net.branches:
                backend.path(branch, stroke=theme.canvas,
                             stroke_width=net.style.width + 2 * casing)
        for branch in net.branches:
            backend.path(branch, stroke=net.style.color, stroke_width=net.style.width)
        for jx, jy in net.junctions:
            backend.circle(jx, jy, 3.0, fill=net.style.color)

    # Pass 2 — structure OUTLINES + selector chrome, then boundary terminals
    # (tunnels/SR/N-i), all ON TOP of the wires.
    for structure in structures:
        draw_structure(structure, scene, backend, theme)
        if _is_interactive_structure(structure.node):
            _draw_frame_selector(structure, scene, backend, theme)
    for structure in structures:
        for bt in structure.border_terminals:
            _draw_border_terminal(bt, backend, theme)

    # Pass 3 — nodes on top.
    for node in nodes:
        draw_node(node, backend, theme)


def _draw_layer_coercion_dots(
    nets: list[RenderWireNet], dots: list[Point], backend: Backend, theme: Theme,
) -> None:
    for net in nets:
        for dx, dy in net.coercion_dots:
            backend.circle(dx, dy, 2.0, fill=theme.coercion_dot,
                            stroke="#ffffff", stroke_width=0.5)
    for dx, dy in dots:
        backend.circle(dx, dy, 2.0, fill=theme.coercion_dot,
                        stroke="#ffffff", stroke_width=0.5)


def draw_scene(scene: Scene, backend: Backend, theme: Theme = DEFAULT_THEME) -> None:
    """Draw an entire scene: canvas, base-layer structures/wires/nodes/FP
    terminals/coercion dots (unchanged order — byte-identical for VIs with
    no case structures), then one ``lv-frame`` group per distinct case-frame
    path (all frames rendered; only the default-selected one starts visible
    — see roadmap #17), then each case's clickable selector-value labels."""
    x1, y1, x2, y2 = scene.bounds
    backend.rect(x1, y1, x2, y2, fill=theme.canvas)

    # A case/stacked-seq structure's tunnels live on its border and are drawn
    # once with the (base-or-ancestor-layer) structure. But that structure's
    # OWN frames' inner wires draw in LATER ``lv-frame`` groups, on top. So each
    # frame layer re-draws its ENCLOSING structure's border terminals (tunnels/
    # selector) after its inner content — the container draws its tunnels on top
    # of its own inner wires, not the other way round. Keyed by raw_uid, which
    # equals a frame path's leaf struct-uid (both strip the vi-name prefix).
    by_raw_uid = {s.raw_uid: s for s in scene.structures if s.raw_uid}

    base_structures = [s for s in scene.structures if not s.frame_path]
    base_nets = [n for n in scene.wire_nets if not n.frame_path]
    base_nodes = [n for n in scene.nodes if not n.frame_path]
    base_dots = [d.point for d in scene.coercion_dots if not d.frame_path]
    base_fps = [fp for fp in scene.fp_terminals if not fp.frame_path]

    _draw_layer_content(base_structures, base_nets, base_nodes, scene, backend, theme)

    for fp in base_fps:
        draw_fp_terminal(fp.terminal, fp.bounds, backend, theme, fp.label_visible)

    _draw_layer_coercion_dots(base_nets, base_dots, backend, theme)

    paths: set[FramePath] = set()
    for s in scene.structures:
        if s.frame_path:
            paths.add(s.frame_path)
    for n in scene.nodes:
        if n.frame_path:
            paths.add(n.frame_path)
    for net in scene.wire_nets:
        if net.frame_path:
            paths.add(net.frame_path)
    for d in scene.coercion_dots:
        if d.frame_path:
            paths.add(d.frame_path)
    for fp in scene.fp_terminals:
        if fp.frame_path:
            paths.add(fp.frame_path)

    for path in sorted(paths, key=encode_frame_path):
        structures = [s for s in scene.structures if s.frame_path == path]
        nets = [n for n in scene.wire_nets if n.frame_path == path]
        nodes = [n for n in scene.nodes if n.frame_path == path]
        dots = [d.point for d in scene.coercion_dots if d.frame_path == path]
        fps = [fp for fp in scene.fp_terminals if fp.frame_path == path]
        visible = _is_default_visible(path, scene.default_frame)
        backend.begin_group(
            cls="lv-frame",
            data={"path": encode_frame_path(path)},
            style=None if visible else "display:none",
        )
        _draw_layer_content(structures, nets, nodes, scene, backend, theme)
        # The container draws its own tunnels on top of this frame's inner wires.
        enclosing = by_raw_uid.get(path[-1][0])
        if enclosing is not None:
            for bt in enclosing.border_terminals:
                _draw_border_terminal(bt, backend, theme)
        for fp in fps:
            draw_fp_terminal(fp.terminal, fp.bounds, backend, theme, fp.label_visible)
        _draw_layer_coercion_dots(nets, dots, backend, theme)
        backend.end_group()

    # Dedicated single-segment value-label groups (see _draw_frame_border /
    # DECISION 2): one per (struct, frame value), so each case's
    # ``◄ value ▼ ►`` (or stacked sequence's ``◄ index ►``) label flips
    # independently of content nesting.
    for structure in scene.structures:
        if not _is_interactive_structure(structure.node):
            continue
        values = scene.frame_values.get(structure.raw_uid, [])
        default = scene.default_frame.get(structure.raw_uid)
        for value in values:
            # Full compositional path: this case's own ancestor frame_path
            # PLUS its own (struct, value) segment. A nested case's label
            # must hide when an ancestor case is on a different frame (its
            # box is hidden), not just when its own selector differs — a
            # bare single-segment path would leak a floating label. The JS
            # controller ANDs every segment, so ancestors gate it correctly.
            label_path: FramePath = structure.frame_path + (
                (structure.raw_uid, value),
            )
            visible = value == default and _is_default_visible(
                structure.frame_path, scene.default_frame,
            )
            # pointer-events:none so a click on the value text falls through to
            # the .lv-selector overlay beneath (which is drawn earlier) — the
            # whole selector stays clickable, value digits included.
            style = (
                "pointer-events:none" if visible
                else "display:none;pointer-events:none"
            )
            backend.begin_group(
                cls="lv-frame",
                data={"path": encode_frame_path(label_path)},
                style=style,
            )
            _draw_frame_value_label(structure, scene, value, backend, theme)
            # Error-cluster case: recolor the whole frame border green (No
            # Error) / red (Error) with the shown frame. Drawn in this per-frame
            # group so it flips with the selector; overlays the static default
            # border painted by draw_structure.
            err_color = _error_border_color(
                scene, structure.raw_uid, value, theme,
            )
            if err_color is not None:
                bx1, by1, bx2, by2 = structure.bounds
                backend.rect(bx1, by1, bx2, by2, fill="none", stroke=err_color,
                             stroke_width=1.6)
            backend.end_group()

    # Dropdown menus LAST so they overlay the whole diagram when opened (they
    # are display:none until the ▼ toggle shows them). Cases + stacked seqs.
    for structure in scene.structures:
        if _is_interactive_structure(structure.node) and scene.frame_values.get(
            structure.raw_uid,
        ):
            _draw_frame_menu(structure, scene, backend, theme)

    # Connector-help panels go ABSOLUTE LAST, in one overlay group, over every
    # other layer (base + all frame groups + menus) — see draw_help_overlay's
    # docstring. scene.nodes is every RenderNode regardless of frame_path (one
    # panel per node, not re-emitted per frame — a node inside a currently
    # hidden case frame is itself hidden, so its panel simply never receives
    # a hover event; no duplication needed).
    draw_help_overlay(scene.nodes, backend, theme)

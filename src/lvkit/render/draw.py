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
    bounds = node.bounds
    if isinstance(node.glyph, ArithGlyph):
        rects = [t.bounds for t in node.terminals if t.bounds is not None]
        if rects:
            bounds = (
                min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects),
            )
    # A hover tooltip (SVG <title>) with the node's full identity — the
    # wrapped subVI box shows a possibly-truncated name, so the untruncated
    # name on hover is the payoff (roadmap #12).
    tooltip = _node_tooltip(node.node)
    if tooltip:
        backend.begin_group(title=tooltip)

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


def _node_tooltip(node: AnyGraphNode) -> str | None:
    """Context-help hover text for a node: its identity, description (when
    known), and its connector pane — every terminal with direction, type, and
    label (when the heap carries one). Returns None for nodes without a useful
    identity (constants show their value in-box already).

    A local variable MUST get its own title: without one the browser shows the
    nearest ancestor ``<title>`` (the diagram's VI name), so every local var
    would otherwise read as the VI itself.
    """
    header: str | None = None
    if isinstance(node, LocalVariableNode):
        name = node.control_name or node.name
        header = f"Local Variable: {name}" if name else "Local Variable"
    elif isinstance(node, VINode | PrimitiveNode):
        header = node.name or None
    if header is None:
        return None

    lines = [header]
    desc = getattr(node, "description", None)
    if desc:
        lines.append(desc)
    lines.extend(_terminal_help_lines(node))
    return "\n".join(lines)


def _terminal_help_lines(node: AnyGraphNode) -> list[str]:
    """The node's connector pane as tooltip lines — inputs then outputs, each
    ``label: type`` (label omitted → ``terminal N`` from its index). Types come
    straight from the heap's stored terminal types (the FULL pane, wired or
    not); labels are shown only when LabVIEW actually stored one."""
    terms = getattr(node, "terminals", None) or []

    def fmt(t: Terminal) -> str:
        ty = t.lv_type.to_python() if t.lv_type else "?"
        label = t.display_name or t.name or f"terminal {t.index}"
        return f"  {label}: {ty}"

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
    signature sequence label; a case shows its plain selector value."""
    if isinstance(structure.node, SequenceNode):  # flat isn't interactive
        values = scene.frame_values.get(structure.raw_uid, [])
        last = len(values) - 1 if values else 0
        return f"{value} [0..{last}]"
    return value


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


def _draw_frame_border(
    structure: RenderStructure, backend: Backend, theme: Theme,
) -> None:
    """The interactive structure's outer box only. LabVIEW draws NO header
    band — the selector is a compact ``◄ value ▼ ►`` widget that sits inside
    the top of the frame (drawn by ``_draw_frame_selector``), not a full-width
    bar. The value text lives in the per-frame value-label groups (draw_scene)."""
    x1, y1, x2, y2 = structure.bounds
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
        text = (
            v if backend.measure_text(v, _SELECTOR_SIZE) <= zone_w
            else fit_label(v, zone_w, backend, _SELECTOR_SIZE)
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
    structure: RenderStructure, backend: Backend, theme: Theme = DEFAULT_THEME,
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
        _draw_frame_border(structure, backend, theme)
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
        draw_structure(structure, backend, theme)
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
            backend.end_group()

    # Dropdown menus LAST so they overlay the whole diagram when opened (they
    # are display:none until the ▼ toggle shows them). Cases + stacked seqs.
    for structure in scene.structures:
        if _is_interactive_structure(structure.node) and scene.frame_values.get(
            structure.raw_uid,
        ):
            _draw_frame_menu(structure, scene, backend, theme)

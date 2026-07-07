"""Scene -> Backend ops.

Node glyphs are resolved once, in the graph-driven join (``scene.py``, via
``nodes.py``'s resolver chain) — ``draw_node`` here just replays whatever
``Glyph`` the scene already carries. Structures, FP terminals, wires, and
coercion dots are still a direct dispatch on graph-sourced kind/type (P2 is
node glyphs only — see DESIGN.md's phasing).
"""

from __future__ import annotations

from ..graph.models import VINode
from ..models import CaseFrame, FPTerminal, LVType
from .backend import Backend
from .glyph import ArithGlyph, ErrorClusterGlyph, VariantGlyph, fit_label
from .scene import RenderBorderTerminal, RenderNode, RenderStructure, Scene
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
    node.glyph.draw(backend, bounds, theme)

    if isinstance(node.node, VINode) and node.label_visible:
        name = node.node.name or ""
        if name:
            x1, y1, x2, y2 = node.bounds
            backend.text((x1 + x2) / 2, y2 + 9, name, 8.0)


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
    """The For-Loop's signature border: a cascade of THREE identical rectangles
    stacked down-right like cards. The heap ``bounds`` (x1..x2, y1..y2) is the
    OVERALL bbox = the BACKMOST card's bottom-right corner (measured against the
    ground truth: the three right-edge lines sit at 238/240/242 with 242 = the
    bbox edge). So the cards are inset INWARD from the bbox — the front card is
    top-left-aligned and (2o x 2o) smaller — not offset outward past x2/y2 where
    they would collide with a node just right of the loop. Front card is drawn
    last, canvas-filled, with a dog-eared bottom-right corner."""
    o = 2.0
    s = theme.struct_border
    w2, h2 = (x2 - x1) - 2 * o, (y2 - y1) - 2 * o
    for k in (2, 1):  # back + mid cards, top-left at (x1+k*o, y1+k*o)
        ox, oy = x1 + k * o, y1 + k * o
        backend.rect(ox, oy, ox + w2, oy + h2,
                     fill=theme.canvas, stroke=s, stroke_width=1.2)
    # Front card (loop boundary), top-left-aligned, dog-eared bottom-right.
    fx2, fy2 = x1 + w2, y1 + h2
    f = 6.0
    backend.path(
        [(x1, y1), (fx2, y1), (fx2, fy2 - f), (fx2 - f, fy2), (x1, fy2), (x1, y1)],
        fill=theme.canvas, stroke=s, stroke_width=1.2,
    )
    backend.path([(fx2, fy2 - f), (fx2 - f, fy2 - f), (fx2 - f, fy2)],
                 stroke=s, stroke_width=1.2)


def _draw_while_loop_border(x1, y1, x2, y2, backend: Backend, theme: Theme) -> None:
    backend.rect(x1, y1, x2, y2, rx=7, fill="none", stroke=theme.struct_border,
                 stroke_width=1.2)


def _draw_case_border(
    structure: RenderStructure, backend: Backend, theme: Theme,
) -> None:
    x1, y1, x2, y2 = structure.bounds
    bar_h = 14.0
    backend.rect(x1, y1, x2, y2, fill="none", stroke=theme.struct_border,
                 stroke_width=1.2)
    backend.rect(x1, y1 - bar_h, x2, y1, fill=theme.case_bar_fill,
                 stroke=theme.struct_border, stroke_width=1)
    shown = structure.shown_frame
    label = str(shown.selector_value) if isinstance(shown, CaseFrame) else ""
    backend.text(
        (x1 + x2) / 2, y1 - 3.5, f"◄ {label} ▼ ►", 9,
        fill=theme.case_bar_text,
    )


def _draw_sequence_border(x1, y1, x2, y2, backend: Backend, theme: Theme) -> None:
    backend.rect(x1, y1, x2, y2, fill="none", stroke=theme.struct_border,
                 stroke_width=1.2)
    backend.line(x1, y1 + 4, x2, y1 + 4, stroke=theme.struct_border, stroke_width=1)
    backend.line(x1, y2 - 4, x2, y2 - 4, stroke=theme.struct_border, stroke_width=1)


def draw_structure(
    structure: RenderStructure, backend: Backend, theme: Theme = DEFAULT_THEME,
) -> None:
    x1, y1, x2, y2 = structure.bounds
    kind = _STRUCTURE_STYLE.get(structure.node.node_type or "", "generic")
    if kind == "forLoop":
        _draw_for_loop_border(x1, y1, x2, y2, backend, theme)
    elif kind == "whileLoop":
        _draw_while_loop_border(x1, y1, x2, y2, backend, theme)
    elif kind == "case":
        _draw_case_border(structure, backend, theme)
    elif kind == "flatSequence":
        _draw_sequence_border(x1, y1, x2, y2, backend, theme)
    else:
        # Stacked sequence, In Place Element Structure, event structure,
        # or anything else — a plain border (matches the prior renderer).
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


def draw_scene(scene: Scene, backend: Backend, theme: Theme = DEFAULT_THEME) -> None:
    """Draw an entire scene: canvas, structures, wires, nodes, FP terminals,
    then coercion dots last — they mark a TERMINAL point, which (like a wire
    stub) can sit inside a node's own bounds, so they must be topmost to stay
    visible rather than getting covered by the node/FP-terminal fill drawn
    after wires."""
    x1, y1, x2, y2 = scene.bounds
    backend.rect(x1, y1, x2, y2, fill=theme.canvas)

    for structure in scene.structures:
        draw_structure(structure, backend, theme)

    for net in scene.wire_nets:
        for branch in net.branches:
            backend.path(branch, stroke=net.style.color, stroke_width=net.style.width)
        for jx, jy in net.junctions:
            backend.circle(jx, jy, 2.5, fill=net.style.color)

    # Boundary terminals ON TOP of wires — a tunnel/shift-register/N-i sits on
    # the structure border and the wire butts against it, never over it.
    for structure in scene.structures:
        for bt in structure.border_terminals:
            _draw_border_terminal(bt, backend, theme)

    for node in scene.nodes:
        draw_node(node, backend, theme)

    for fp in scene.fp_terminals:
        draw_fp_terminal(fp.terminal, fp.bounds, backend, theme, fp.label_visible)

    for net in scene.wire_nets:
        for dx, dy in net.coercion_dots:
            backend.circle(dx, dy, 2.0, fill=theme.coercion_dot,
                            stroke="#ffffff", stroke_width=0.5)

    for dx, dy in scene.coercion_dots:
        backend.circle(dx, dy, 2.0, fill=theme.coercion_dot,
                        stroke="#ffffff", stroke_width=0.5)

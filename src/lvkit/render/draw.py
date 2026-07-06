"""Scene -> Backend ops.

Node glyphs are resolved once, in the graph-driven join (``scene.py``, via
``nodes.py``'s resolver chain) — ``draw_node`` here just replays whatever
``Glyph`` the scene already carries. Structures, FP terminals, wires, and
coercion dots are still a direct dispatch on graph-sourced kind/type (P2 is
node glyphs only — see DESIGN.md's phasing).
"""

from __future__ import annotations

from ..models import CaseFrame, FPTerminal, LVType
from .backend import Backend
from .glyph import ArithGlyph, fit_label
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
    array glyphs) are drawn at their terminal-center extent. Real subVI/prim
    icons and constants keep their own bounds."""
    bounds = node.bounds
    if isinstance(node.glyph, ArithGlyph):
        rects = [t.bounds for t in node.terminals if t.bounds is not None]
        if rects:
            bounds = (
                min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects),
            )
    elif getattr(node.node, "kind", None) == "primitive" and len(node.terminals) >= 2:
        xs = [t.center[0] for t in node.terminals]
        ys = [t.center[1] for t in node.terminals]
        m = 3.0
        ib = (
            max(node.bounds[0], min(xs)), max(node.bounds[1], min(ys) - m),
            min(node.bounds[2], max(xs)), min(node.bounds[3], max(ys) + m),
        )
        if ib[2] - ib[0] > 4 and ib[3] - ib[1] > 4:
            bounds = ib
    node.glyph.draw(backend, bounds, theme)


def _draw_border_terminal(
    bt: RenderBorderTerminal, backend: Backend, theme: Theme,
) -> None:
    """Draw a structure border glyph from its fixed ``glyph_kind`` — a
    geometry-side decoration (see ``scene._structure_borders``), never
    re-derived from heap class strings here."""
    x1, y1, x2, y2 = bt.bounds
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    kind = bt.glyph_kind

    if kind in ("N", "i"):
        backend.rect(x1, y1, x2, y2, fill=theme.loop_term)
        backend.text(cx, cy + 4, kind, 11, fill="#ffffff", italic=True)
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
        backend.rect(x1, y1, x2, y2, fill=theme.selector_fill,
                     stroke=theme.selector_stroke, stroke_width=1.2)
        backend.text(cx, cy + 4, "?", 10, fill=theme.selector_text)
        return
    if kind in ("sr_down", "sr_up"):
        # Shift register: a type-colored box with a filled triangle glyph.
        col = bt.color or theme.sr_stroke
        backend.rect(x1, y1, x2, y2, fill=theme.term_fill, stroke=col,
                     stroke_width=1.5)
        if kind == "sr_up":
            tri = [(x1 + 2, y2 - 2), (cx, y1 + 2), (x2 - 2, y2 - 2)]
        else:
            tri = [(x1 + 2, y1 + 2), (cx, y2 - 2), (x2 - 2, y1 + 2)]
        backend.polygon(tri, fill=col)
        return
    if kind == "autoindex":
        # Array indexing / accumulation: a type-colored box with array brackets.
        col = bt.color or "#333333"
        backend.rect(x1 - 2, y1 - 2, x2 + 2, y2 + 2, fill=theme.term_fill,
                     stroke=col, stroke_width=1.2)
        backend.text(cx, cy + 4, "[ ]", 9, fill=col)
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
    """The For-Loop's signature border: a cascade of THREE identical rectangles,
    each offset by (o, o) down-right like a stack of cards. The front card (the
    loop boundary, top-left) is drawn last and canvas-filled so it masks the
    back cards' overlap, leaving three parallel lines on the right and bottom."""
    o = 4.0
    s = theme.struct_border
    for k in (2, 1):  # back cards — plain rects, offset by k*(o, o)
        dx = dy = k * o
        backend.rect(x1 + dx, y1 + dy, x2 + dx, y2 + dy,
                     fill=theme.canvas, stroke=s, stroke_width=2)
    # Front card (loop boundary) with a dog-eared bottom-right corner.
    f = 8.0
    backend.path(
        [(x1, y1), (x2, y1), (x2, y2 - f), (x2 - f, y2), (x1, y2), (x1, y1)],
        fill=theme.canvas, stroke=s, stroke_width=2,
    )
    backend.path([(x2, y2 - f), (x2 - f, y2 - f), (x2 - f, y2)],
                 stroke=s, stroke_width=1.2)


def _draw_while_loop_border(x1, y1, x2, y2, backend: Backend, theme: Theme) -> None:
    backend.rect(x1, y1, x2, y2, rx=7, fill="none", stroke=theme.struct_border,
                 stroke_width=2)


def _draw_case_border(
    structure: RenderStructure, backend: Backend, theme: Theme,
) -> None:
    x1, y1, x2, y2 = structure.bounds
    bar_h = 14.0
    backend.rect(x1, y1, x2, y2, fill="none", stroke=theme.struct_border,
                 stroke_width=2)
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
                 stroke_width=2)
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
                     stroke_width=2)
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
    backend.text(cx, cy + 3, fit_label(sample, (x2 - x1) - 2, backend, 9), 9,
                 fill=theme.fp_value_text)


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
        if height >= 8.0:
            letter = _INDEX_LETTERS[i % len(_INDEX_LETTERS)]
            backend.text(x1 + width / 2, (cy1 + cy2) / 2 + 3, letter, 7,
                         fill=theme.fp_value_text)
    return x1 + width


def draw_fp_terminal(
    terminal: FPTerminal, bounds: tuple[float, float, float, float],
    backend: Backend, theme: Theme = DEFAULT_THEME,
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

    backend.rect(x1, y1, x2, y2, rx=2, fill=theme.fp_panel,
                 stroke=color, stroke_width=stroke_width)

    label = terminal.name or ""
    if label:
        size = 8.0
        # LabVIEW default: control/indicator name label sits ABOVE the box.
        backend.text(
            (x1 + x2) / 2, y1 - 4,
            fit_label(label, max(x2 - x1, 40.0), backend, size), size,
        )

    type_label = _fp_type_label(terminal, scalar_type)
    if type_label:
        backend.text((x1 + x2) / 2, y2 - 2, type_label, 7, fill=color, bold=True)

    if x2 - x1 < _FP_MIN_ICON_SIZE or y2 - y1 < _FP_MIN_ICON_SIZE:
        return  # too small for the wire port / index / value cells

    tri = 5.5
    cy_mid = (y1 + y2) / 2
    if terminal.is_indicator:
        # Data enters on the left.
        port = [(x1 - tri, cy_mid - tri * 0.6), (x1 - tri, cy_mid + tri * 0.6),
                (x1, cy_mid)]
    else:
        # Data exits on the right.
        port = [(x2, cy_mid - tri * 0.6), (x2, cy_mid + tri * 0.6),
                (x2 + tri, cy_mid)]
    backend.polygon(port, fill="#ffffff", stroke=color, stroke_width=1.0)

    margin = 3.0
    bottom_reserve = 9.0  # room for the bottom-center type label
    value_x1, value_y1 = x1 + margin, y1 + margin
    value_x2, value_y2 = x2 - margin, y2 - margin - bottom_reserve

    if is_array:
        dims = (lv_type.dimensions or 1) if lv_type is not None else 1
        value_x1 = _draw_array_index_column(
            value_x1, value_y1, value_x1 + 10.0, value_y2, dims, backend, theme,
        ) + 1.0

    sample = numeric_sample(scalar_type)
    value_bounds = (value_x1, value_y1, value_x2, value_y2)
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
        draw_fp_terminal(fp.terminal, fp.bounds, backend, theme)

    for net in scene.wire_nets:
        for dx, dy in net.coercion_dots:
            backend.circle(dx, dy, 2.0, fill=theme.coercion_dot,
                            stroke="#ffffff", stroke_width=0.5)

"""Plain dispatch-dict drawer: Scene -> Backend ops.

P0 scope: a flat dispatch table keyed by graph node kind, reproducing the
prior renderer's look while sourcing every label/glyph choice from the
GRAPH (never from heap XML class strings — see DESIGN.md's "graph owns
semantics" principle). This migrates to a resolver chain in P2; until then
"add a visual" means adding a branch here.
"""

from __future__ import annotations

from ..graph.models import (
    ConstantNode,
    FormulaNode,
    PrimitiveNode,
    VINode,
)
from ..models import CaseFrame, FPTerminal
from .backend import Backend
from .icons import icon_data_uri
from .scene import RenderBorderTerminal, RenderNode, RenderStructure, Scene
from .style import DEFAULT_THEME, Theme, type_family, wire_style

_ARITH_SYMBOL = {
    "Add": "+", "Subtract": "−", "Multiply": "×", "Divide": "÷",
    "Increment": "+1", "Decrement": "-1",
}

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


def _fit_label(text: str, width: float, backend: Backend, size: float) -> str:
    """Truncate a label to fit ``width`` px, using the backend's own text
    measurement (not a fixed px/char heuristic — S7)."""
    if not text:
        return ""
    if backend.measure_text(text, size) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid] + "…"
        if backend.measure_text(candidate, size) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…" if lo else "…"


def _draw_primitive(
    node: RenderNode, backend: Backend, theme: Theme,
) -> None:
    gnode = node.node
    assert isinstance(gnode, PrimitiveNode)
    x1, y1, x2, y2 = node.bounds
    sym = _ARITH_SYMBOL.get(gnode.operation or gnode.name or "")
    if sym:
        backend.polygon(
            [(x1, y1), (x2, (y1 + y2) / 2), (x1, y2)],
            fill=theme.prim_fill, stroke=theme.prim_stroke, stroke_width=1.5,
        )
        backend.text(x1 + (x2 - x1) * 0.32, (y1 + y2) / 2 + 5, sym, 15)
        return
    _draw_labeled_box(
        x1, y1, x2, y2, gnode.name or "?", backend, theme.prim_fill,
        theme.prim_stroke, 1.0,
    )


def _draw_labeled_box(
    x1: float, y1: float, x2: float, y2: float, label: str,
    backend: Backend, fill: str, stroke: str, stroke_width: float,
) -> None:
    backend.rect(x1, y1, x2, y2, rx=2, fill=fill, stroke=stroke,
                 stroke_width=stroke_width)
    size = 8.0
    backend.text(
        (x1 + x2) / 2, (y1 + y2) / 2 + 3,
        _fit_label(label, x2 - x1, backend, size), size,
    )


def _draw_subvi(node: RenderNode, backend: Backend, theme: Theme) -> None:
    gnode = node.node
    assert isinstance(gnode, VINode)
    x1, y1, x2, y2 = node.bounds
    # P0 has no per-call icon source (only the VI's own connector-pane icon
    # is drawn, as a corner decoration — see draw_scene). SubVI calls always
    # render as a labeled box; real icon extraction is a P2/P4 item.
    _draw_labeled_box(
        x1, y1, x2, y2, gnode.name or "SubVI", backend,
        theme.subvi_fill, theme.subvi_stroke, 1.5,
    )


def _draw_constant(node: RenderNode, backend: Backend, theme: Theme) -> None:
    gnode = node.node
    assert isinstance(gnode, ConstantNode)
    x1, y1, x2, y2 = node.bounds
    color = wire_style(gnode.lv_type, theme).color
    backend.rect(x1, y1, x2, y2, rx=2, fill=theme.term_fill,
                 stroke=color, stroke_width=2)
    value = gnode.raw_value if gnode.value is None else str(gnode.value)
    if value:
        size = 9.0
        backend.text(
            (x1 + x2) / 2, (y1 + y2) / 2 + 3,
            _fit_label(value, x2 - x1, backend, size), size,
        )


def _draw_formula(node: RenderNode, backend: Backend, theme: Theme) -> None:
    gnode = node.node
    assert isinstance(gnode, FormulaNode)
    x1, y1, x2, y2 = node.bounds
    _draw_labeled_box(
        x1, y1, x2, y2, gnode.name or "Formula", backend,
        theme.prim_fill, theme.prim_stroke, 1.5,
    )


def _draw_generic_node(node: RenderNode, backend: Backend, theme: Theme) -> None:
    gnode = node.node
    x1, y1, x2, y2 = node.bounds
    label = gnode.name or gnode.node_type or "?"
    _draw_labeled_box(x1, y1, x2, y2, label, backend, theme.prim_fill,
                       theme.prim_stroke, 1.0)


_NODE_DRAWERS = {
    PrimitiveNode: _draw_primitive,
    VINode: _draw_subvi,
    ConstantNode: _draw_constant,
    FormulaNode: _draw_formula,
}


def draw_node(node: RenderNode, backend: Backend, theme: Theme = DEFAULT_THEME) -> None:
    drawer = _NODE_DRAWERS.get(type(node.node), _draw_generic_node)
    drawer(node, backend, theme)


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
        arrow = "▼" if kind == "sr_down" else "▲"
        backend.rect(x1, y1, x2, y2, fill=theme.sr_fill, stroke=theme.sr_stroke)
        backend.text(cx, cy + 4, arrow, 10)
        return
    if kind == "autoindex":
        backend.rect(x1 - 2, y1 - 2, x2 + 2, y2 + 2, fill="#ffffff",
                     stroke="#333333", stroke_width=1.2)
        backend.text(cx, cy + 4, "[ ]", 9)
        return
    # A border DCO the fixed glyph table doesn't cover — undecorated box
    # rather than a guessed glyph.
    backend.rect(x1, y1, x2, y2, fill="#ffffff", stroke=theme.struct_border,
                 stroke_width=1.0)


def _draw_for_loop_border(x1, y1, x2, y2, backend: Backend, theme: Theme) -> None:
    o = 3.0
    backend.rect(x1, y1, x2, y2, fill="none", stroke=theme.struct_border,
                 stroke_width=2)
    backend.path([(x1 - o, y1 + 8), (x1 - o, y1 - o), (x1 + 8, y1 - o)],
                 stroke=theme.struct_border, stroke_width=2)
    backend.path([(x2 + o, y2 - 8), (x2 + o, y2 + o), (x2 - 8, y2 + o)],
                 stroke=theme.struct_border, stroke_width=2)


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
    for bt in structure.border_terminals:
        _draw_border_terminal(bt, backend, theme)


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


def _fp_glyph_family(terminal: FPTerminal) -> str:
    family = type_family(terminal.lv_type)
    if family != "unknown":
        return family
    return _CONTROL_TYPE_FAMILY.get(terminal.control_type or "", "unknown")


def _draw_fp_glyph(
    family: str, bounds: tuple[float, float, float, float],
    backend: Backend, color: str,
) -> None:
    """Draw the small recognizable LabVIEW terminal glyph INSIDE a
    control/indicator box — kept tasteful and small, not a full icon set
    (that's P2's resolver chain)."""
    x1, y1, x2, y2 = bounds
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    w, h = x2 - x1, y2 - y1
    size = 9.0
    if family == "float":
        backend.text(cx, cy + 3, _fit_label("1.23", w - 4, backend, size), size,
                     fill=color)
    elif family == "int":
        backend.text(cx, cy + 3, _fit_label("123", w - 4, backend, size), size,
                     fill=color)
    elif family == "bool":
        r = max(2.0, min(w, h) * 0.22)
        backend.circle(cx, cy, r, fill=color, stroke="#333333", stroke_width=0.75)
    elif family == "string":
        backend.text(cx, cy + 3, "abc", size, fill=color, italic=True)
    elif family == "path":
        backend.text(cx, cy + 3, _fit_label("Path", w - 4, backend, 8.0), 8.0,
                     fill=color)
    elif family == "enum":
        backend.text(cx, cy + 3, "▾", size, fill=color)
    elif family in ("cluster", "error_cluster"):
        backend.text(cx, cy + 3, "{}", size, fill=color)
    elif family == "array":
        backend.text(cx, cy + 3, "[ ]", size, fill=color)
    # "unknown" -> no inner glyph; the colored border alone is the signal.


def draw_fp_terminal(
    terminal: FPTerminal, bounds: tuple[float, float, float, float],
    backend: Backend, theme: Theme = DEFAULT_THEME,
) -> None:
    x1, y1, x2, y2 = bounds
    color = wire_style(terminal.lv_type, theme).color
    stroke_width = 1.5 if terminal.is_indicator else 3.0
    backend.rect(x1, y1, x2, y2, rx=2, fill=theme.term_fill,
                 stroke=color, stroke_width=stroke_width)
    _draw_fp_glyph(_fp_glyph_family(terminal), bounds, backend, color)
    label = terminal.name or ""
    if label:
        size = 8.0
        below_y = y2 + 10
        backend.text(
            (x1 + x2) / 2, below_y,
            _fit_label(label, max(x2 - x1, 40.0), backend, size), size,
        )


def draw_scene(scene: Scene, backend: Backend, theme: Theme = DEFAULT_THEME) -> None:
    """Draw an entire scene: canvas, structures, wires, nodes, FP terminals,
    the VI's own connector-pane icon as a corner decoration, then coercion
    dots last — they mark a TERMINAL point, which (like a wire stub) can
    sit inside a node's own bounds, so they must be topmost to stay visible
    rather than getting covered by the node/FP-terminal fill drawn after
    wires."""
    x1, y1, x2, y2 = scene.bounds
    backend.rect(x1, y1, x2, y2, fill=theme.canvas)

    for structure in scene.structures:
        draw_structure(structure, backend, theme)

    for net in scene.wire_nets:
        for branch in net.branches:
            backend.path(branch, stroke=net.style.color, stroke_width=net.style.width)
        for jx, jy in net.junctions:
            backend.circle(jx, jy, 2.5, fill=net.style.color)

    for node in scene.nodes:
        draw_node(node, backend, theme)

    for fp in scene.fp_terminals:
        draw_fp_terminal(fp.terminal, fp.bounds, backend, theme)

    if scene.icon_png:
        uri = icon_data_uri(scene.icon_png)
        if uri:
            backend.image(uri, x1 + 5, y1 + 5, 32, 32, opacity=0.9)

    for net in scene.wire_nets:
        for dx, dy in net.coercion_dots:
            backend.circle(dx, dy, 2.0, fill=theme.coercion_dot,
                            stroke="#ffffff", stroke_width=0.5)

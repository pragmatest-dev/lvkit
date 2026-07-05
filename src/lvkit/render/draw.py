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
from .style import DEFAULT_THEME, Theme

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
    backend.rect(x1, y1, x2, y2, rx=2, fill=theme.term_fill,
                 stroke=theme.wire_float, stroke_width=2)
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
    x1, y1, x2, y2 = bt.bounds
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    t = bt.terminal
    tunnel_type = getattr(t, "tunnel_type", None)
    name = (t.name or "") if t else ""

    if tunnel_type == "lMax":
        backend.rect(x1, y1, x2, y2, fill=theme.loop_term)
        backend.text(cx, cy + 4, "N", 11, fill="#ffffff", italic=True)
        return
    if tunnel_type == "caseSel" or name == "selector":
        backend.rect(x1, y1, x2, y2, fill=theme.selector_fill,
                     stroke=theme.selector_stroke, stroke_width=1.2)
        backend.text(cx, cy + 4, "?", 10, fill=theme.selector_text)
        return
    if tunnel_type in ("lSR", "rSR"):
        arrow = "▼" if tunnel_type == "lSR" else "▲"
        backend.rect(x1, y1, x2, y2, fill=theme.sr_fill, stroke=theme.sr_stroke)
        backend.text(cx, cy + 4, arrow, 10)
        return
    if tunnel_type == "lpTun":
        backend.rect(x1 - 2, y1 - 2, x2 + 2, y2 + 2, fill="#ffffff",
                     stroke="#333333", stroke_width=1.2)
        backend.text(cx, cy + 4, "[ ]", 9)
        return
    # Geometry-only border DCO the graph doesn't model semantically (loop
    # iteration count / while-loop stop condition) — undecorated box.
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


def draw_fp_terminal(
    terminal: FPTerminal, bounds: tuple[float, float, float, float],
    backend: Backend, theme: Theme = DEFAULT_THEME,
) -> None:
    x1, y1, x2, y2 = bounds
    backend.rect(x1, y1, x2, y2, rx=2, fill=theme.term_fill,
                 stroke=theme.wire_float, stroke_width=3)
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
    then the VI's own connector-pane icon as a corner decoration."""
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

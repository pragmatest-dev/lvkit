"""Compose a ``DiagramScene`` into a self-contained SVG string.

Draws LabVIEW-faithful primitives: the pale diagram canvas, structure borders
(For-Loop cascade, While-Loop, sequence), border terminals (N / i / conditional /
shift registers), constants, front-panel terminals, primitive triangles, and
type-colored orthogonal wires routed by :class:`WireRouter`.

This is Phase-1 scope: it establishes the pipeline. The prebuilt-glyph resolver
chain and per-type wire coloring grow on top of it (see the lv-renderer PLAN).
"""

from __future__ import annotations

import base64
from xml.sax.saxutils import escape

from .heap_scene import DiagramScene, SceneBorderTerminal, SceneNode, SceneStructure
from .wire_router import Point, WireRouter, path_d

# LabVIEW palette
CANVAS = "#fbfbf5"
WIRE_DBL = "#e8821e"          # double-precision float wire (default)
STRUCT_BORDER = "#9b9b73"     # loop/structure border (muted olive-gray)
PRIM_FILL = "#fff6d8"
PRIM_STROKE = "#b07d10"
TERM_FILL = "#fff3e2"
LOOP_TERM = "#1f3fbf"         # N / i terminal blue
COND_STOP = "#c62828"         # conditional terminal red

_ARITH_SYMBOL = {
    "Add": "+", "Subtract": "−", "Multiply": "×", "Divide": "÷",
    "Increment": "+1", "Decrement": "-1",
}


def _rect(x1: float, y1: float, x2: float, y2: float, **attrs: str) -> str:
    a = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return (f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{x2-x1:.1f}" '
            f'height="{y2-y1:.1f}" {a}/>')


def _text(x: float, y: float, s: str, size: float, **attrs: str) -> str:
    a = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'text-anchor="middle" {a}>{escape(s)}</text>')


def _for_loop_border(x1: float, y1: float, x2: float, y2: float) -> list[str]:
    """For-Loop: the recognizable stacked-cascade border (offset double edge)."""
    o = 3.0
    return [
        _rect(x1, y1, x2, y2, fill="none", stroke=STRUCT_BORDER, stroke_width="2"),
        # cascade hint: offset partial edges at top-left and bottom-right
        f'<path d="M{x1-o:.1f},{y1+8:.1f} L{x1-o:.1f},{y1-o:.1f} '
        f'L{x1+8:.1f},{y1-o:.1f}" fill="none" stroke="{STRUCT_BORDER}" '
        f'stroke-width="2"/>',
        f'<path d="M{x2+o:.1f},{y2-8:.1f} L{x2+o:.1f},{y2+o:.1f} '
        f'L{x2-8:.1f},{y2+o:.1f}" fill="none" stroke="{STRUCT_BORDER}" '
        f'stroke-width="2"/>',
    ]


def _while_loop_border(x1: float, y1: float, x2: float, y2: float) -> list[str]:
    """While-Loop: rounded border with a wrapped-arrow hint at bottom-left."""
    out = [
        f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{x2-x1:.1f}" height="{y2-y1:.1f}" '
        f'rx="6" fill="none" stroke="{STRUCT_BORDER}" stroke-width="2"/>',
    ]
    return out


def _structure(struct: SceneStructure) -> list[str]:
    x1, y1, x2, y2 = struct.bounds
    if struct.kind == "forLoop":
        out = _for_loop_border(x1, y1, x2, y2)
    elif struct.kind == "whileLoop":
        out = _while_loop_border(x1, y1, x2, y2)
    else:
        out = [_rect(x1, y1, x2, y2, fill="none", stroke=STRUCT_BORDER,
                     stroke_width="2")]
    for bt in struct.border_terms:
        out += _border_terminal(bt)
    return out


def _border_terminal(bt: SceneBorderTerminal) -> list[str]:
    x1, y1, x2, y2 = bt.bounds
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    if bt.kind in ("N", "i"):
        return [
            _rect(x1, y1, x2, y2, fill=LOOP_TERM),
            _text(cx, cy + 4, bt.kind, 11, fill="#fff", font_style="italic"),
        ]
    if bt.kind == "cond":
        r = min(x2 - x1, y2 - y1) / 2
        return [
            _rect(x1, y1, x2, y2, fill="#fff", stroke=COND_STOP, stroke_width="1.5"),
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r*0.6:.1f}" fill="none" '
            f'stroke="{COND_STOP}" stroke-width="2"/>',
        ]
    if bt.kind == "autoindex":
        return [
            _rect(x1 - 2, y1 - 2, x2 + 2, y2 + 2, fill="#fff", stroke="#333",
                  stroke_width="1.2"),
            _text(cx, cy + 4, "[ ]", 9),
        ]
    # shift register
    arrow = "▼" if bt.kind == "sr_down" else "▲"
    return [
        _rect(x1, y1, x2, y2, fill="#cfcfcf", stroke="#555"),
        _text(cx, cy + 4, arrow, 10),
    ]


def _primitive(node: SceneNode) -> list[str]:
    x1, y1, x2, y2 = node.bounds
    sym = _ARITH_SYMBOL.get(node.name)
    if sym:
        return [
            f'<polygon points="{x1:.1f},{y1:.1f} {x2:.1f},{(y1+y2)/2:.1f} '
            f'{x1:.1f},{y2:.1f}" fill="{PRIM_FILL}" stroke="{PRIM_STROKE}" '
            f'stroke-width="1.5"/>',
            _text(x1 + (x2 - x1) * 0.32, (y1 + y2) / 2 + 5, sym, 15),
        ]
    return [
        _rect(x1, y1, x2, y2, fill=PRIM_FILL, stroke=PRIM_STROKE),
        _text((x1 + x2) / 2, (y1 + y2) / 2 + 3, node.name, 8),
    ]


def _node(node: SceneNode) -> list[str]:
    x1, y1, x2, y2 = node.bounds
    if node.kind == "primitive":
        return _primitive(node)
    if node.kind == "subvi" and node.icon_png and node.icon_png.exists():
        data = base64.b64encode(node.icon_png.read_bytes()).decode()
        return [f'<image href="data:image/png;base64,{data}" x="{x1:.1f}" '
                f'y="{y1:.1f}" width="{x2-x1:.1f}" height="{y2-y1:.1f}"/>']
    if node.kind == "constant":
        return [
            _rect(x1, y1, x2, y2, rx="2", fill=TERM_FILL, stroke=WIRE_DBL,
                  stroke_width="2"),
        ]
    if node.kind == "fpterm":
        return [_rect(x1, y1, x2, y2, rx="2", fill=TERM_FILL, stroke=WIRE_DBL,
                      stroke_width="3")]
    return [_rect(x1, y1, x2, y2, fill=PRIM_FILL, stroke=PRIM_STROKE)]


def scene_to_svg(scene: DiagramScene, wire_color: str = WIRE_DBL) -> str:
    x1, y1, x2, y2 = scene.bounds
    w, h = x2 - x1, y2 - y1
    router = WireRouter(scene.obstacles, scene.bounds)
    endpoints = scene.endpoints

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x1:.0f} {y1:.0f} '
        f'{w:.0f} {h:.0f}" font-family="sans-serif">',
        _rect(x1, y1, x2, y2, fill=CANVAS),
    ]
    for struct in scene.structures:
        out += _structure(struct)
    for wire in scene.wires:
        pairs = zip(wire.endpoints, wire.endpoints[1:])
        for a, b in pairs:
            route: list[Point] = router.route(a, b, endpoints)
            out.append(f'<path d="{path_d(route)}" fill="none" stroke="{wire_color}" '
                       f'stroke-width="2.5" stroke-linejoin="round"/>')
    for node in scene.nodes:
        out += _node(node)
    if scene.icon_png and scene.icon_png.exists():
        data = base64.b64encode(scene.icon_png.read_bytes()).decode()
        out.append(f'<image href="data:image/png;base64,{data}" x="{x1+5:.1f}" '
                   f'y="{y1+5:.1f}" width="32" height="32" opacity="0.9"/>')
    out.append("</svg>")
    return "\n".join(out)

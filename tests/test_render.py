"""Tests for the faithful block-diagram renderer (lvkit.render).

Unit tests over the pure geometry/SVG functions with synthetic scenes — no VI
files required (the repo's sample VIs are local-only).
"""

from __future__ import annotations

import pytest

from lvkit.render.heap_scene import (
    DiagramScene,
    SceneBorderTerminal,
    SceneNode,
    SceneStructure,
    SceneWire,
)
from lvkit.render.svg import scene_to_svg
from lvkit.render.wire_router import RouterConfig, WireRouter, _compress, path_d

# --------------------------------------------------------------------------- #
# Wire router
# --------------------------------------------------------------------------- #


def _router(obstacles=None):
    return WireRouter(obstacles or [], bounds=(0.0, 0.0, 200.0, 200.0))


def test_straight_route_when_aligned_and_clear():
    r = _router()
    a, b = (10.0, 50.0), (100.0, 50.0)
    route = r.route(a, b, endpoints=[a, b])
    assert route[0] == (10.0, 50.0)
    assert route[-1] == (100.0, 50.0)
    # aligned + clear → a single straight segment (no bends)
    assert len(route) == 2


def test_elbow_route_when_offset_and_clear():
    r = _router()
    a, b = (10.0, 20.0), (100.0, 80.0)
    route = r.route(a, b, endpoints=[a, b])
    assert route[0] == (10.0, 20.0)
    assert route[-1] == (100.0, 80.0)
    # endpoints offset in both axes → at least one bend
    assert len(route) >= 3


def test_router_avoids_obstacle():
    # A node squarely between two aligned terminals forces a detour.
    obstacle = (40.0, 40.0, 70.0, 60.0)
    r = WireRouter([obstacle], bounds=(0.0, 0.0, 200.0, 120.0),
                   config=RouterConfig(grid=2))
    p1, p2 = (10.0, 50.0), (120.0, 50.0)
    route = r.route(p1, p2, endpoints=[p1, p2])

    def crosses_obstacle(pts) -> bool:
        bx1, by1, bx2, by2 = obstacle
        for i in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            steps = int(max(abs(x2 - x1), abs(y2 - y1))) + 1
            for s in range(1, steps):
                x = x1 + (x2 - x1) * s / steps
                y = y1 + (y2 - y1) * s / steps
                if bx1 + 1 < x < bx2 - 1 and by1 + 1 < y < by2 - 1:
                    return True
        return False

    assert route[0] == p1 and route[-1] == p2
    assert not crosses_obstacle(route)


def test_compress_drops_collinear_points():
    pts = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (10.0, 5.0)]
    assert _compress(pts) == [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)]


def test_path_d_format():
    assert path_d([(1.0, 2.0), (3.0, 4.0)]) == "M1.0,2.0 L3.0,4.0"


# --------------------------------------------------------------------------- #
# SVG composition
# --------------------------------------------------------------------------- #


def _demo_scene() -> DiagramScene:
    return DiagramScene(
        bounds=(0.0, 0.0, 200.0, 150.0),
        nodes=[
            SceneNode(kind="primitive", bounds=(80.0, 60.0, 110.0, 90.0), name="Add",
                      prim_res_id="1050"),
            SceneNode(kind="fpterm", bounds=(10.0, 65.0, 40.0, 85.0), name=""),
        ],
        structures=[
            SceneStructure(
                kind="forLoop", bounds=(60.0, 40.0, 160.0, 120.0),
                border_terms=[
                    SceneBorderTerminal(kind="N", bounds=(62.0, 42.0, 74.0, 54.0)),
                    SceneBorderTerminal(kind="i", bounds=(62.0, 106.0, 74.0, 118.0)),
                ],
            )
        ],
        wires=[SceneWire(endpoints=[(40.0, 75.0), (80.0, 75.0)])],
    )


def test_scene_to_svg_is_valid_and_complete():
    svg = scene_to_svg(_demo_scene())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert 'viewBox="0 0 200 150"' in svg
    # Add primitive → triangle with the '+' symbol
    assert "<polygon" in svg
    assert ">+<" in svg
    # For-Loop border + N/i terminals
    assert "<path" in svg  # cascade border edges and/or wires
    assert ">N<" in svg and ">i<" in svg


def test_scene_to_svg_wire_uses_type_color():
    svg = scene_to_svg(_demo_scene(), wire_color="#123456")
    assert "#123456" in svg


def test_empty_scene_renders():
    svg = scene_to_svg(DiagramScene(bounds=(0.0, 0.0, 10.0, 10.0)))
    assert "<svg" in svg and "</svg>" in svg


def test_case_structure_renders_selector_bar_and_terminal():
    scene = DiagramScene(
        bounds=(0.0, 0.0, 120.0, 120.0),
        structures=[
            SceneStructure(
                kind="case", bounds=(20.0, 30.0, 100.0, 100.0), label="True",
                border_terms=[
                    SceneBorderTerminal(kind="selector",
                                        bounds=(16.0, 60.0, 28.0, 72.0)),
                ],
            )
        ],
    )
    svg = scene_to_svg(scene)
    assert "True" in svg          # selector label bar
    assert ">?<" in svg           # case selector terminal glyph


def test_subvi_without_icon_is_labeled_box():
    scene = DiagramScene(
        bounds=(0.0, 0.0, 100.0, 60.0),
        nodes=[SceneNode(kind="subvi", bounds=(20.0, 20.0, 90.0, 44.0),
                         name="Parse XML")],
    )
    svg = scene_to_svg(scene)
    assert "<rect" in svg
    assert "Parse XML" in svg  # 70px-wide box fits the label


def test_generic_node_renders_with_label():
    scene = DiagramScene(
        bounds=(0.0, 0.0, 100.0, 60.0),
        nodes=[SceneNode(kind="node", bounds=(20.0, 20.0, 60.0, 40.0),
                         name="Property")],
    )
    svg = scene_to_svg(scene)
    assert "Property" in svg


def test_long_label_is_truncated():
    from lvkit.render.svg import _fit_label

    assert _fit_label("VeryLongSubViName", 20.0).endswith("…")
    assert _fit_label("short", 100.0) == "short"


def test_sequence_border_has_filmstrip_lines():
    scene = DiagramScene(
        bounds=(0.0, 0.0, 120.0, 120.0),
        structures=[SceneStructure(kind="flatSequence",
                                   bounds=(20.0, 20.0, 100.0, 100.0))],
    )
    svg = scene_to_svg(scene)
    assert "<line" in svg


# --------------------------------------------------------------------------- #
# Icon transparency (best-effort, Pillow)
# --------------------------------------------------------------------------- #


def test_icon_data_uri_missing_file_returns_none(tmp_path):
    from lvkit.render.icons import icon_data_uri

    assert icon_data_uri(tmp_path / "nope.png") is None


def test_knockout_keeps_interior_white_drops_exterior():
    import io

    pytest.importorskip("PIL")
    from PIL import Image

    from lvkit.render.icons import _knockout_white_border

    # 5x5 white image with a black ring and a single interior white pixel.
    img = Image.new("RGBA", (5, 5), (255, 255, 255, 255))
    for i in range(5):
        img.putpixel((i, 1), (0, 0, 0, 255))
        img.putpixel((i, 3), (0, 0, 0, 255))
        img.putpixel((1, i), (0, 0, 0, 255))
        img.putpixel((3, i), (0, 0, 0, 255))
    # center (2,2) stays white — enclosed by the ring
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    out = Image.open(io.BytesIO(_knockout_white_border(buf.getvalue()))).convert("RGBA")
    assert out.getpixel((0, 0))[3] == 0    # exterior corner → transparent
    assert out.getpixel((2, 2))[3] == 255  # interior white → preserved
    assert out.getpixel((1, 1))[3] == 255  # black ring → opaque

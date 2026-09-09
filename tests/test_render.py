"""Tests for the graph-driven block-diagram renderer (lvkit.render).

Wire-router and icon-transparency tests are pure unit tests (no VI files
needed). The scene-join, single-frame-policy, and corpus/determinism tests
load real sample VIs — the repo's sample VIs are local-only (gitignored), so
these skip gracefully when a given file isn't present.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import (
    CaseStructureNode,
    ConstantNode,
    LocalVariableNode,
    LoopNode,
    PrimitiveNode,
    SequenceNode,
    StructureNode,
)
from lvkit.graph.op_walk import _format_ranges, _selector_label
from lvkit.models import FPTerminal, LVType, LVTypeKind
from lvkit.parser.layout import Layout, Point, Rect, build_layout
from lvkit.parser.wire_table import FAITHFUL_WIRE_TABLE, decode_signal
from lvkit.render import render_vi, render_vi_file
from lvkit.render.backend import SvgBackend
from lvkit.render.draw import draw_fp_terminal, draw_node
from lvkit.render.glyph import CompoundArithGlyph
from lvkit.render.nodes import _format_numeric_const, string_const_display
from lvkit.render.scene import (
    Scene,
    _exit_side,
    _frame_compatible,
    _frame_info,
    _structure_borders,
    _trim_string_const_geom,
    build_scene,
    encode_frame_path,
)
from lvkit.render.style import DEFAULT_THEME, coercion_key, wire_style
from lvkit.render.wire_router import RouterConfig, WireRouter, _compress, path_d

# --------------------------------------------------------------------------- #
# Case-selector faithful labels (#16) — pure functions over CaseFrame + LVType
# --------------------------------------------------------------------------- #


def _frame(value, ranges=(), is_default=False):
    from lvkit.models import CaseFrame, SelectorRange

    return CaseFrame(
        selector_value=value,
        is_default=is_default,
        selector_ranges=[SelectorRange(start=s, end=e) for s, e in ranges],
    )


def _enum_type(names):
    from lvkit.models import EnumValue, LVType

    return LVType(
        kind=LVTypeKind.ENUM,
        underlying_type="UnitUInt16",
        values={n: EnumValue(value=i) for i, n in enumerate(names)},
    )


def test_format_ranges_single_range_and_list():
    from lvkit.models import SelectorRange

    single = [SelectorRange(start=3, end=3)]
    rng = [SelectorRange(start=3, end=10)]
    lst = [
        SelectorRange(start=1, end=1),
        SelectorRange(start=3, end=3),
        SelectorRange(start=5, end=8),
    ]
    assert _format_ranges(single, str) == "3"
    assert _format_ranges(rng, str) == "3..10"
    assert _format_ranges(lst, str) == "1, 3, 5..8"


def test_selector_label_boolean_and_default():
    from lvkit.models import LVType

    bool_t = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    assert _selector_label(_frame("True"), bool_t, False) == "True"
    assert _selector_label(_frame("False"), bool_t, False) == "False"
    assert (
        _selector_label(_frame("Default", is_default=True), bool_t, False) == "Default"
    )


def test_selector_label_enum_names_ranges_and_list():
    names = ["Digital Input", "Digital Output", "Voltage Input", "PWM"]
    t = _enum_type(names)
    assert _selector_label(_frame("3", [(3, 3)]), t, False) == "PWM"
    # Two contiguous values have no middle to elide -> list both, comma-separated.
    assert (
        _selector_label(_frame("0", [(0, 1)]), t, False)
        == "Digital Input, Digital Output"
    )
    # Three-plus contiguous -> "first..last" (the middle member is elided).
    assert (
        _selector_label(_frame("0", [(0, 2)]), t, False)
        == "Digital Input..Voltage Input"
    )
    # Non-contiguous match values are always comma-listed.
    assert (
        _selector_label(_frame("0", [(0, 0), (2, 2)]), t, False)
        == "Digital Input, Voltage Input"
    )


def test_selector_label_string_quoted():
    from lvkit.models import LVType

    t = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    assert _selector_label(_frame("Stop"), t, False) == '"Stop"'


def test_selector_label_error_no_error_and_error():
    from lvkit.models import LVType

    t = LVType(kind=LVTypeKind.CLUSTER)
    assert _selector_label(_frame("0", [(0, 0)]), t, True) == "No Error"
    assert _selector_label(_frame("1", [(1, 1)]), t, True) == "Error"
    # Error frame that is the structure's default is still "Error", not "Default"
    assert _selector_label(_frame("Default", is_default=True), t, True) == "Error"


# --------------------------------------------------------------------------- #
# Coercion dots on numeric-unifying primitives (#11) — ArithGlyph AND
# CompoundArithGlyph, but not boxed/structural primitives.
# --------------------------------------------------------------------------- #


def _num_input(index, repr_name):
    from lvkit.models import LVType, Terminal
    from lvkit.render.scene import RenderTerminal

    t = Terminal(
        id=f"t{index}",
        index=index,
        direction="input",
        lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=repr_name),
    )
    return RenderTerminal(
        terminal=t,
        center=(10.0, 10.0 * index),
        bounds=(5.0, 10.0 * index - 4, 15.0, 10.0 * index + 4),
    )


def _arith_node(glyph, input_reprs):
    from lvkit.graph.models import PrimitiveNode
    from lvkit.render.scene import RenderNode

    return RenderNode(
        node=PrimitiveNode(id="n1", name="op", vi_path="v"),
        bounds=(0.0, 0.0, 20.0, 40.0),
        glyph=glyph,
        terminals=[_num_input(i + 1, r) for i, r in enumerate(input_reprs)],
    )


def test_coercion_dot_on_compound_arith_mixed_widths():
    """Compound Arithmetic (CompoundArithGlyph) now gets a coercion dot on its
    narrower input when its numeric inputs differ in width — #11."""
    from lvkit.render.glyph import ArithGlyph, CompoundArithGlyph
    from lvkit.render.scene import _arith_coercion_dots

    cpd = _arith_node(CompoundArithGlyph("+", num_inputs=2), ["NumInt32", "NumFloat64"])
    assert len(_arith_coercion_dots([cpd])) == 1

    arith = _arith_node(ArithGlyph("+"), ["NumInt32", "NumFloat64"])
    assert len(_arith_coercion_dots([arith])) == 1


def test_no_coercion_dot_when_widths_match():
    from lvkit.render.glyph import CompoundArithGlyph
    from lvkit.render.scene import _arith_coercion_dots

    cpd = _arith_node(
        CompoundArithGlyph("+", num_inputs=2), ["NumFloat64", "NumFloat64"]
    )
    assert _arith_coercion_dots([cpd]) == []


def test_no_coercion_dot_on_boxed_primitive():
    """A boxed primitive (WrappedBoxGlyph) is never coercion-dotted even with
    differing numeric inputs — its inputs may differ structurally (e.g. an
    index or exponent), not by coercion."""
    from lvkit.render.glyph import WrappedBoxGlyph
    from lvkit.render.scene import _arith_coercion_dots

    boxed = _arith_node(
        WrappedBoxGlyph("Scale By Power Of 2"), ["NumFloat64", "NumInt32"]
    )
    assert _arith_coercion_dots([boxed]) == []


# --------------------------------------------------------------------------- #
# Primitive icon sizing from termBounds (#41) — a primitive's glyph is drawn at
# the union of its terminal termBounds (its true icon extent), not the padded
# ~32x32 clickable box.
# --------------------------------------------------------------------------- #


def _term(index, direction, bounds):
    from lvkit.models import Terminal
    from lvkit.render.scene import RenderTerminal

    t = Terminal(id=f"t{index}", index=index, direction=direction)
    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2
    return RenderTerminal(terminal=t, center=(cx, cy), bounds=bounds)


def _prim_render_node(glyph, terminals, node_bounds=(0.0, 0.0, 32.0, 32.0)):
    from lvkit.graph.models import PrimitiveNode
    from lvkit.render.scene import RenderNode

    return RenderNode(
        node=PrimitiveNode(id="p1", name="String Length", vi_path="v"),
        bounds=node_bounds,
        glyph=glyph,
        terminals=terminals,
    )


def test_primitive_glyph_sized_to_termbounds_union():
    """A thin primitive whose clickable box is padded to 32x32 is drawn at the
    union of its terminal termBounds — a thin wide rect, not a square."""
    from lvkit.render.draw import _glyph_bounds
    from lvkit.render.glyph import WrappedBoxGlyph

    node = _prim_render_node(
        WrappedBoxGlyph("String Length"),
        [
            _term(0, "input", (0.0, 8.0, 4.0, 12.0)),
            _term(1, "output", (28.0, 20.0, 32.0, 24.0)),
        ],
    )
    assert _glyph_bounds(node) == (0.0, 8.0, 32.0, 24.0)  # 32x16, not 32x32


def test_primitive_glyph_floor_prevents_sliver():
    """A unary primitive with its in/out terminals at the SAME height would give
    a near-zero-height union; the axis is floored (centered) so the glyph can't
    collapse to a sliver."""
    from lvkit.render.draw import _MIN_GLYPH_EXTENT, _glyph_bounds
    from lvkit.render.glyph import WrappedBoxGlyph

    node = _prim_render_node(
        WrappedBoxGlyph("Not"),
        [
            _term(0, "input", (0.0, 14.0, 4.0, 18.0)),
            _term(1, "output", (28.0, 14.0, 32.0, 18.0)),
        ],
    )
    x1, y1, x2, y2 = _glyph_bounds(node)
    assert (x1, x2) == (0.0, 32.0)
    assert y2 - y1 == _MIN_GLYPH_EXTENT  # floored, not 4px
    assert (y1 + y2) / 2 == 16.0  # centered on the terminal span


def test_primitive_with_own_aspect_icon_keeps_node_bounds():
    """A primitive drawn with real/declared art (InlineSvgGlyph) keeps its node
    bounds — only the shape/box glyphs are resized to the terminal span."""
    from lvkit.render.draw import _glyph_bounds
    from lvkit.render.glyph import InlineSvgGlyph

    node = _prim_render_node(
        InlineSvgGlyph("<g/>"),
        [
            _term(0, "input", (0.0, 8.0, 4.0, 12.0)),
            _term(1, "output", (28.0, 20.0, 32.0, 24.0)),
        ],
    )
    assert _glyph_bounds(node) == (0.0, 0.0, 32.0, 32.0)


def test_substring_type_colors_as_string_not_unknown():
    """A ``SubString`` primitive (e.g. Search/Split String's outputs, a shift
    register carrying one) is a string for color/glyph purposes — it must map
    to the string family/color, not fall through to grey ``unknown`` (#80)."""
    from lvkit.models import LVType
    from lvkit.render.style import DEFAULT_THEME, type_family, wire_style

    sub = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="SubString")
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    assert type_family(sub) == "string"
    assert wire_style(sub).color == wire_style(string).color
    assert wire_style(sub).color != DEFAULT_THEME.wire_default


def test_while_loop_conditional_terminal_color_by_polarity():
    """The conditional terminal draws RED for Stop-if-True and GREEN for
    Continue-if-True (#57), keyed on ``cond_continue`` (heap bit 16)."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.draw import _draw_border_terminal
    from lvkit.render.scene import RenderBorderTerminal
    from lvkit.render.style import DEFAULT_THEME

    def _svg(cond_continue):
        bt = RenderBorderTerminal(
            terminal=None,
            bounds=(0.0, 0.0, 14.0, 14.0),
            glyph_kind="cond",
            cond_continue=cond_continue,
        )
        b = SvgBackend()
        _draw_border_terminal(bt, b, DEFAULT_THEME)
        return b.render((0.0, 0.0, 14.0, 14.0))

    stop = _svg(False)
    cont = _svg(True)
    assert DEFAULT_THEME.cond_stop in stop
    assert DEFAULT_THEME.cond_continue not in stop
    assert DEFAULT_THEME.cond_continue in cont
    assert DEFAULT_THEME.cond_stop not in cont


def test_non_primitive_keeps_node_bounds():
    """A subVI (VINode) name-box is never shrunk to the terminal span — its
    bounds are already its true drawn size and it needs room for the name."""
    from lvkit.graph.models import VINode
    from lvkit.render.draw import _glyph_bounds
    from lvkit.render.glyph import WrappedBoxGlyph
    from lvkit.render.scene import RenderNode

    node = RenderNode(
        node=VINode(id="v1", name="My SubVI", vi_path="v"),
        bounds=(0.0, 0.0, 32.0, 32.0),
        glyph=WrappedBoxGlyph("My SubVI"),
        terminals=[_term(0, "input", (0.0, 8.0, 4.0, 12.0))],
    )
    assert _glyph_bounds(node) == (0.0, 0.0, 32.0, 32.0)


# --------------------------------------------------------------------------- #
# Wire router (pure geometry — unaffected by the graph-driven rewrite)
# --------------------------------------------------------------------------- #


def _router(obstacles=None):
    return WireRouter(obstacles or [], bounds=(0.0, 0.0, 200.0, 200.0))


def test_straight_route_when_aligned_and_clear():
    r = _router()
    a, b = (10.0, 50.0), (100.0, 50.0)
    route = r.route(a, b, endpoints=[a, b])
    assert route[0] == (10.0, 50.0)
    assert route[-1] == (100.0, 50.0)
    assert len(route) == 2


def test_elbow_route_when_offset_and_clear():
    r = _router()
    a, b = (10.0, 20.0), (100.0, 80.0)
    route = r.route(a, b, endpoints=[a, b])
    assert route[0] == (10.0, 20.0)
    assert route[-1] == (100.0, 80.0)
    assert len(route) >= 3


def test_router_avoids_obstacle():
    obstacle = (40.0, 40.0, 70.0, 60.0)
    r = WireRouter([obstacle], bounds=(0.0, 0.0, 200.0, 120.0), config=RouterConfig())
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


def test_router_routes_around_third_node_directly_between_endpoints():
    """A wire between two nodes with a THIRD node sitting directly on the
    straight line between them must detour around it — no segment may pass
    through the third node's interior. Distinct from
    ``test_router_avoids_obstacle``: the blocking obstacle here is itself a
    real node with its own owner-adjacent geometry (not a bare rect off to
    one side of a simple straight shot), closer to the dense-diagram shape
    that regressed in practice.
    """
    src_node = (0.0, 40.0, 20.0, 60.0)
    blocker_node = (55.0, 30.0, 85.0, 70.0)
    dst_node = (120.0, 40.0, 140.0, 60.0)
    obstacles = [src_node, blocker_node, dst_node]
    r = WireRouter(
        obstacles,
        bounds=(0.0, 0.0, 200.0, 120.0),
        config=RouterConfig(),
    )
    p1, p2 = (20.0, 50.0), (120.0, 50.0)  # straight line runs through blocker_node
    route = r.route(p1, p2, endpoints=[p1, p2], p1_owner=src_node, p2_owner=dst_node)

    def crosses(obstacle, pts) -> bool:
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
    assert not crosses(blocker_node, route)


def test_compress_drops_collinear_points():
    pts = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (10.0, 5.0)]
    assert _compress(pts) == [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)]


def test_path_d_format():
    assert path_d([(1.0, 2.0), (3.0, 4.0)]) == "M1.0,2.0 L3.0,4.0"


# --------------------------------------------------------------------------- #
# Icon transparency (best-effort, Pillow) — unaffected by the rewrite
# --------------------------------------------------------------------------- #


def test_icon_data_uri_missing_file_returns_none(tmp_path):
    from lvkit.render.icons import icon_data_uri

    assert icon_data_uri(tmp_path / "nope.png") is None


def test_knockout_keeps_interior_white_drops_exterior():
    import io

    pytest.importorskip("PIL")
    from PIL import Image

    from lvkit.render.icons import _knockout_white_border

    img = Image.new("RGBA", (5, 5), (255, 255, 255, 255))
    for i in range(5):
        img.putpixel((i, 1), (0, 0, 0, 255))
        img.putpixel((i, 3), (0, 0, 0, 255))
        img.putpixel((1, i), (0, 0, 0, 255))
        img.putpixel((3, i), (0, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    out = Image.open(io.BytesIO(_knockout_white_border(buf.getvalue()))).convert("RGBA")
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((2, 2))[3] == 255
    assert out.getpixel((1, 1))[3] == 255


# --------------------------------------------------------------------------- #
# Backend text measurement (used by label-fit truncation, not a px/char guess)
# --------------------------------------------------------------------------- #


def test_cluster_constant_collapses_when_box_too_small_for_field_rows():
    """A cluster constant whose real heap bounds are too small to fit legible
    ``name: value`` rows drops the field-NAME labels but still draws the field
    VALUES (never a blank box; names move to the hover tooltip); a comfortably-
    sized one draws the full inline rows — task #50. The switch is driven purely
    by the drawn ``bounds`` (LabVIEW's ground-truth size), not a heap
    "collapsed" flag (none exists)."""
    from lvkit.render.glyph import ClusterConstantGlyph
    from lvkit.render.style import DEFAULT_THEME

    class _Dot:
        def draw(self, backend, bounds, theme):  # noqa: ANN001
            x1, y1, x2, y2 = bounds
            backend.text((x1 + x2) / 2, (y1 + y2) / 2, "V", 7.0)

    glyph = ClusterConstantGlyph(
        fields=(("Horizontal", _Dot()), ("Vertical", _Dot())),
        value_summary="Horizontal: 0\nVertical: 0",
    )

    big = SvgBackend()
    glyph.draw(big, (0.0, 0.0, 90.0, 42.0), DEFAULT_THEME)
    big_svg = big.render((0.0, 0.0, 90.0, 42.0))
    assert "Horizontal" in big_svg and "Vertical" in big_svg  # inline rows shown

    small = SvgBackend()
    glyph.draw(small, (0.0, 0.0, 17.0, 42.0), DEFAULT_THEME)
    small_svg = small.render((0.0, 0.0, 17.0, 42.0))
    # Too small for name+value rows: the field NAME labels drop off (names move
    # to the hover tooltip), but the real field VALUE glyphs are STILL drawn —
    # a cluster shows its members (a one-boolean cluster shows its boolean),
    # never a generic mixed-element icon that misstates the field count/types.
    assert "Horizontal" not in small_svg and "Vertical" not in small_svg
    assert ">V<" in small_svg  # the field value glyphs are drawn, fit to the box


def test_bundle_connector_panel_aggregate_column_is_legible():
    """The hover connector panel keeps a Bundle's aggregate cluster column at the
    diagram glyph's real proportion (no distortion), and grows the WHOLE panel
    ICON so that thin sliver is still legible. Regression for the "context-help
    columns too small" report — asserts the drawn aggregate column reaches the
    _PANE_BUNDLE_AGG_MIN width instead of collapsing to a ~2px sliver."""
    import re

    from lvkit.graph.models import PrimitiveNode
    from lvkit.models import LVType, LVTypeKind, Terminal
    from lvkit.render.draw import _PANE_BUNDLE_AGG_MIN, _draw_connector_panel
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.scene import RenderNode, RenderTerminal
    from lvkit.render.style import DEFAULT_THEME

    diagram = (0.0, 0.0, 126.0, 69.0)  # a real DCAF-proportioned Bundle node
    agg_box = (110.0, 0.0, 126.0, 69.0)  # 16px aggregate region, flush right
    clust = LVType(kind=LVTypeKind.CLUSTER)

    def rt(direction: str, bounds, name: str) -> RenderTerminal:
        return RenderTerminal(
            terminal=Terminal(
                id=name,
                index=0,
                direction=direction,
                nmux_role="agg",
                lv_type=clust,
                name=name,
            ),
            center=((bounds[0] + bounds[2]) / 2, 34.5),
            bounds=bounds,
        )

    node = RenderNode(
        node=PrimitiveNode(id="n", vi_path="v", node_type="nMux"),
        bounds=diagram,
        glyph=BundleByNameGlyph(names=("a", "b"), bundling=True),
        terminals=[rt("input", agg_box, "in"), rt("output", agg_box, "out")],
    )
    b = SvgBackend()
    _draw_connector_panel(node, b, DEFAULT_THEME)
    svg = b.render((0.0, 0.0, 400.0, 200.0))
    # The aggregate column is the tan (prim_fill) rect; the name cells are the
    # canvas-filled ones. It must reach the legibility floor.
    agg_widths = [
        float(w)
        for w in re.findall(
            r'<rect [^>]* width="([-\d.]+)"[^>]* fill="#fff6d8"', svg
        )
    ] or [
        float(w)
        for w in re.findall(
            r'<rect [^>]* fill="#fff6d8"[^>]* width="([-\d.]+)"', svg
        )
    ]
    assert agg_widths, "no aggregate cluster column drawn in the panel"
    assert max(agg_widths) >= _PANE_BUNDLE_AGG_MIN - 1.0, (
        f"panel aggregate column too thin: {agg_widths}"
    )


def test_context_help_type_text_matches_reference_screenshot():
    """``style.context_help_type_text`` — the LabVIEW Context-Help-style
    VERBOSE type description ("Cluster hover" task). Boolean/I32/String are
    verified CHARACTER-FOR-CHARACTER against a real Context Help screenshot
    (an error cluster's status/code/source fields); the other integer
    widths/float precisions are the same pattern extrapolated
    mathematically (two's-complement / IEEE-754), not independently
    screenshot-verified — documented as such in the function's own
    docstring."""
    from lvkit.models import LVType, LVTypeKind
    from lvkit.render.style import context_help_type_text

    def scalar(ut: str) -> LVType:
        return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=ut)

    # Verified against the reference screenshot.
    assert context_help_type_text(scalar("Boolean")) == "boolean (TRUE or FALSE)"
    assert (
        context_help_type_text(scalar("NumInt32"))
        == "long [32-bit integer (-2147483648 to 2147483647)]"
    )
    assert context_help_type_text(scalar("String")) == "string"

    # Extrapolated (same pattern, not individually screenshot-verified).
    assert "8-bit" in context_help_type_text(scalar("NumInt8"))
    assert "16-bit" in context_help_type_text(scalar("NumInt16"))
    assert "0 to 255" in context_help_type_text(scalar("NumUInt8"))
    assert "double-precision" in context_help_type_text(scalar("NumFloat64"))

    # Non-scalar fallbacks: honest, short, never a guessed elaborate format.
    enum_t = LVType(kind=LVTypeKind.ENUM, underlying_type="UnitUInt16")
    assert context_help_type_text(enum_t) == "enum"
    refnum_t = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="Queue"
    )
    assert context_help_type_text(refnum_t) == "Queue refnum"
    arr_t = LVType(
        kind=LVTypeKind.ARRAY, element_type=scalar("NumFloat64"), dimensions=1
    )
    assert context_help_type_text(arr_t) == (
        "array of double [64-bit real (double-precision) floating point]"
    )
    assert context_help_type_text(None) == "?"


def test_cluster_type_rows_recurses_with_indent_and_element_count():
    """``draw._cluster_type_rows`` builds the hierarchical tree
    ("Cluster hover" task, reference images: an error cluster's field list
    and a nested-cluster example): one row per field, a nested cluster
    field reads "name (cluster of N elements)" (singular "element" for
    N==1, verified from the reference) and recurses ONE indent level
    deeper for its own fields."""
    from lvkit.models import ClusterField, LVType, LVTypeKind
    from lvkit.render.draw import _cluster_type_rows
    from lvkit.render.style import DEFAULT_THEME

    bool_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    int_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    inner = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="status", type=bool_type)],
    )
    outer = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="a", type=int_type),
            ClusterField(name="nested", type=inner),
        ],
    )
    rows = _cluster_type_rows("MyCluster", outer, 0, DEFAULT_THEME)
    assert [r.indent for r in rows] == [0, 1, 1, 2]
    assert rows[0].text == "MyCluster (cluster of 2 elements)"
    assert rows[0].mark_text == ""  # a cluster row has no single-token mnemonic
    assert rows[1].text == "a (long [32-bit integer (-2147483648 to 2147483647)])"
    assert rows[1].mark_text == "I32"
    assert rows[2].text == "nested (cluster of 1 element)"  # singular
    assert rows[3].text == "status (boolean (TRUE or FALSE))"
    assert rows[3].indent == 2  # nested one level deeper than "nested" itself

    # An unresolved field type still gets an honest row, never dropped.
    unresolved = LVType(
        kind=LVTypeKind.CLUSTER, fields=[ClusterField(name="mystery", type=None)]
    )
    rows2 = _cluster_type_rows("X", unresolved, 0, DEFAULT_THEME)
    assert rows2[1].text == "mystery (?)"


def test_draw_cluster_type_panel_renders_tree_with_colored_marks():
    """``_draw_cluster_type_panel`` draws a cluster CONSTANT's hover panel
    as a "Data type" header + the hierarchical field/type tree, each row's
    mini type mark in that field's OWN wire color (never colored body
    text — the name+type text stays ``theme.text``). A non-cluster
    constant, or any non-constant node, gets no panel at all."""
    from lvkit.graph.models import ConstantNode, PrimitiveNode
    from lvkit.models import ClusterField, LVType, LVTypeKind
    from lvkit.render.draw import _draw_cluster_type_panel
    from lvkit.render.glyph import ConstantGlyph
    from lvkit.render.scene import RenderNode
    from lvkit.render.style import DEFAULT_THEME, wire_style

    error_cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(
                name="status",
                type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
            ),
            ClusterField(
                name="code",
                type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
            ),
            ClusterField(
                name="source",
                type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
            ),
        ],
    )
    node = RenderNode(
        node=ConstantNode(id="v::441", vi_path="v", lv_type=error_cluster),
        bounds=(0.0, 0.0, 40.0, 20.0),
        glyph=ConstantGlyph("", "#000"),
        dom_id="441",
    )
    b = SvgBackend()
    _draw_cluster_type_panel(node, b, DEFAULT_THEME)
    svg = b.render((0.0, 0.0, 400.0, 200.0))
    assert 'class="lv-help" data-node="441"' in svg
    assert ">Data type<" in svg
    assert "(cluster of 3 elements)" in svg
    assert ">status (boolean (TRUE or FALSE))<" in svg
    assert ">code (long [32-bit integer (-2147483648 to 2147483647)])<" in svg
    assert ">source (string)<" in svg
    # Mini marks: text + color both the field's OWN wire style, never
    # colored body text.
    bool_color = wire_style(error_cluster.fields[0].type).color
    assert f'fill="{bool_color}">TF<' in svg
    assert f'stroke="{bool_color}"' in svg

    # A non-cluster constant gets no panel at all.
    scalar_node = RenderNode(
        node=ConstantNode(
            id="v::5",
            vi_path="v",
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
        ),
        bounds=(0.0, 0.0, 20.0, 20.0),
        glyph=ConstantGlyph("", "#000"),
        dom_id="5",
    )
    b2 = SvgBackend()
    _draw_cluster_type_panel(scalar_node, b2, DEFAULT_THEME)
    assert b2.render((0.0, 0.0, 100.0, 100.0)).count("<g") == 0

    # A non-constant node (even one wired to a cluster type) gets no panel
    # either — this dialog is scoped to cluster CONSTANTS.
    prim_node = RenderNode(
        node=PrimitiveNode(id="v::9", vi_path="v", node_type="prim"),
        bounds=(0.0, 0.0, 20.0, 20.0),
        glyph=ConstantGlyph("", "#000"),
        dom_id="9",
    )
    b3 = SvgBackend()
    _draw_cluster_type_panel(prim_node, b3, DEFAULT_THEME)
    assert b3.render((0.0, 0.0, 100.0, 100.0)).count("<g") == 0


def test_gtr_error_cluster_constant_hover_panel_matches_reference():
    """Real-corpus check ("Cluster hover" task): GTR's bare error-cluster
    constant (heap uid 441, fields status/code/source) gets a
    Context-Help-style tree hover panel — verified against the reference
    screenshot's exact wording for all 3 fields."""
    from lvkit.render.draw import _draw_cluster_type_panel
    from lvkit.render.scene import RenderNode, build_scene
    from lvkit.render.style import DEFAULT_THEME

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (
            rn
            for rn in scene.nodes
            if isinstance(rn, RenderNode) and rn.node.id.endswith("::441")
        ),
        None,
    )
    assert target is not None, "GTR's error-cluster constant (uid 441) not in scene"
    b = SvgBackend()
    _draw_cluster_type_panel(target, b, DEFAULT_THEME)
    svg = b.render((0.0, 0.0, 600.0, 400.0))
    assert "(cluster of 3 elements)" in svg
    assert ">status (boolean (TRUE or FALSE))<" in svg
    assert ">code (long [32-bit integer (-2147483648 to 2147483647)])<" in svg
    assert ">source (string)<" in svg


def test_bundle_aggregate_split_into_interior_input_and_edge_output():
    """A Bundle-By-Name's input + output aggregate terminals share ONE heap DCO
    box (the output owns it; the input aliases it). ``_reposition_mux_terminals``
    splits that real box in half so the connector panel can tell them apart: the
    OUTPUT keeps the exposed EDGE half (right, here), the INPUT takes the
    INTERIOR half — which ``_term_side_and_frac`` then reads as the TOP entry.
    The wire endpoints (centers) are left at the real heap anchor: the on-diagram
    aggregate wire is drawn from LabVIEW's own decoded geometry, never moved."""
    from lvkit.models import Terminal
    from lvkit.render.scene import RenderTerminal, _reposition_mux_terminals

    node_bounds = (100.0, 200.0, 226.0, 269.0)  # left..right = 100..226
    shared = (218.0, 200.0, 226.0, 269.0)  # 8px column flush to the RIGHT edge

    def rt(direction):
        return RenderTerminal(
            terminal=Terminal(
                id=direction, index=0, direction=direction, nmux_role="agg"
            ),
            center=(222.0, 234.5),  # both collapsed onto the shared box center
            bounds=shared,
        )

    out = {
        t.terminal.direction: t
        for t in _reposition_mux_terminals([rt("input"), rt("output")], node_bounds)
    }
    # Output keeps its own recorded box (edge column); the input takes the
    # equal-width interior column immediately to its left (the empty gap).
    assert out["output"].bounds == (218.0, 200.0, 226.0, 269.0)
    assert out["input"].bounds == (210.0, 200.0, 218.0, 269.0)
    # Centers here are untouched; the wire-anchor correction lives in layout
    # (``_reanchor_nmux_aggregate_inputs``), applied before wire decode.
    assert out["input"].center == (222.0, 234.5)
    assert out["output"].center == (222.0, 234.5)


def test_collapsed_cluster_constant_draws_icon_not_members():
    """A cluster constant flagged COLLAPSED ("View As Icon", DDO objFlags bit
    0x10000000) draws the compact cluster icon, never its members — LabVIEW
    can't shrink a value's natural height, so a small box is a collapse, not
    squashed content. An expanded cluster still draws its members."""
    from lvkit.render.glyph import ClusterConstantGlyph
    from lvkit.render.style import DEFAULT_THEME

    class _Dot:
        def draw(self, backend, bounds, theme):  # noqa: ANN001
            x1, y1, x2, y2 = bounds
            backend.text((x1 + x2) / 2, (y1 + y2) / 2, "V", 7.0)

    fields = (("a", _Dot()), ("b", _Dot()), ("c", _Dot()))
    box = (0.0, 0.0, 90.0, 60.0)

    collapsed = SvgBackend()
    ClusterConstantGlyph(fields=fields, collapsed=True).draw(
        collapsed, box, DEFAULT_THEME
    )
    csvg = collapsed.render(box)
    assert ">V<" not in csvg  # members NOT drawn when collapsed
    assert csvg.count("<rect") >= 3  # shell + element squares (the icon)

    expanded = SvgBackend()
    ClusterConstantGlyph(fields=fields, collapsed=False).draw(
        expanded, box, DEFAULT_THEME
    )
    assert ">V<" in expanded.render(box)  # members ARE drawn when expanded


def test_array_constant_renders_indexed_cells_not_raw_repr():
    """An array constant draws an index control + a column of the elements' own
    value glyphs (the indexed element at top), with a greyed past-end cell and
    the interactive ``lv-array`` carrier — never the raw ``[…]`` Python list."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyph import ArrayConstantGlyph, ConstantGlyph
    from lvkit.render.nodes import _array_const_values
    from lvkit.render.style import DEFAULT_THEME

    # Value parsing: the repr string is parsed back to a list.
    assert _array_const_values("['a', 'b', 'c']") == ["a", "b", "c"]
    assert _array_const_values("[0, 1, 2]") == [0, 1, 2]
    assert _array_const_values("not-a-list") == []

    elems = tuple(
        ConstantGlyph(s, "#e05fa0", multiline=True) for s in ("Alpha", "Beta")
    )
    glyph = ArrayConstantGlyph(
        elements=elems, element_color="#e05fa0", struct_uid="v::9"
    )
    b = SvgBackend()
    bounds = (0.0, 0.0, 140.0, 90.0)
    glyph.draw(b, bounds, DEFAULT_THEME)
    svg = b.render(bounds)
    assert "Alpha" in svg and "Beta" in svg  # real element value glyphs
    assert ">0<" in svg  # the index readout
    assert "['" not in svg and "[0," not in svg  # never the raw list repr
    # The interactive contract the array controller JS reads: the carrier's
    # length + per-row height, the translatable column, the index readout, and
    # the ▲/▼ prev/next click targets — all keyed by the same struct uid.
    assert 'data-lv-len="2"' in svg and "data-lv-cellh" in svg
    assert 'class="lv-array-col"' in svg
    assert 'class="lv-array-index"' in svg
    assert 'data-lv-action="prev"' in svg and 'data-lv-action="next"' in svg
    assert svg.count('data-lv-struct="v::9"') >= 3  # carrier + col + index all keyed


def test_empty_array_shows_disabled_default_element_not_blank_rect():
    """LabVIEW never shows a blank/greyed rect for an unset array row — it
    shows the element TYPE's own DEFAULT-valued control, disabled. An EMPTY
    array (``elements == ()``) therefore shows ONE disabled default element
    at index 0 (plus further dimmed rows filling the viewport); a NON-empty
    array's real elements are drawn normally (undimmed) and only rows PAST
    the real elements get the disabled default. ``default_element=None``
    (an older caller) keeps the old flat grey rect, unchanged."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyph import ArrayConstantGlyph, ConstantGlyph
    from lvkit.render.style import DEFAULT_THEME

    default = ConstantGlyph("", "#e05fa0", multiline=True)

    class _Marked:
        """A distinctive glyph so we can tell a REAL element apart from the
        default in the rendered SVG."""

        def draw(self, backend, bounds, theme):  # noqa: ANN001
            x1, y1, x2, y2 = bounds
            backend.text((x1 + x2) / 2, (y1 + y2) / 2, "REAL", 7.0)

    bounds = (0.0, 0.0, 140.0, 90.0)  # 90 / 18 = 5 visible rows

    # An EMPTY array: default_element draws at index 0 (and further rows),
    # washed with the existing lv-disabled-mask convention (never a blank
    # theme.fp_panel rect).
    empty_glyph = ArrayConstantGlyph(
        elements=(), element_color="#e05fa0", struct_uid="v::1",
        default_element=default,
    )
    b1 = SvgBackend()
    empty_glyph.draw(b1, bounds, DEFAULT_THEME)
    svg1 = b1.render(bounds)
    assert 'class="lv-disabled-mask"' in svg1
    assert DEFAULT_THEME.disabled_mask in svg1
    assert DEFAULT_THEME.fp_panel not in svg1  # never the old blank-cell fill

    # A NON-empty array: the real element is undimmed; rows past it get the
    # disabled default.
    populated_glyph = ArrayConstantGlyph(
        elements=(_Marked(),), element_color="#e05fa0", struct_uid="v::2",
        default_element=default,
    )
    b2 = SvgBackend()
    populated_glyph.draw(b2, bounds, DEFAULT_THEME)
    svg2 = b2.render(bounds)
    assert ">REAL<" in svg2
    assert 'class="lv-disabled-mask"' in svg2  # past-end rows still disabled

    # default_element=None (back-compat): old flat grey cell, unchanged.
    legacy_glyph = ArrayConstantGlyph(
        elements=(), element_color="#e05fa0", struct_uid="v::3"
    )
    b3 = SvgBackend()
    legacy_glyph.draw(b3, bounds, DEFAULT_THEME)
    svg3 = b3.render(bounds)
    assert 'class="lv-disabled-mask"' not in svg3
    assert DEFAULT_THEME.fp_panel in svg3

    # Regression: a box small enough for exactly ONE visible row (visible ==
    # 1, e.g. GTR's "SuitesRunning") must still draw that one disabled
    # default row — ``total + max(0, visible - 1)`` (0 + 0 == 0) used to
    # draw ZERO rows for an empty array here; the fix is ``max(total, 1)``.
    small_bounds = (0.0, 0.0, 80.0, 24.0)  # tiny box -> exactly 1 visible row
    tiny_glyph = ArrayConstantGlyph(
        elements=(), element_color="#e05fa0", struct_uid="v::4",
        default_element=default,
    )
    b4 = SvgBackend()
    tiny_glyph.draw(b4, small_bounds, DEFAULT_THEME)
    svg4 = b4.render(small_bounds)
    assert 'data-lv-visible="1"' in svg4  # confirms this exercises the bug
    assert 'class="lv-disabled-mask"' in svg4, (
        "an empty array with exactly 1 visible row must still draw its "
        "disabled default element at index 0, not zero rows"
    )


def test_local_variable_glyph_badge_and_read_write_border_weight():
    """A Local Variable node draws the ▶ badge (a filled triangle polygon) that
    tells it apart from a same-shaped constant box, with a BOLD border on both
    read and write and the badge on the dataflow side (read=right, write=left)
    — task #48, grounded in NI's Local Variable node image."""
    import re

    from lvkit.render.glyph import LocalVariableGlyph, WrappedBoxGlyph
    from lvkit.render.style import DEFAULT_THEME

    read = SvgBackend()
    LocalVariableGlyph("flag", is_write=False).draw(read, (0, 0, 90, 25), DEFAULT_THEME)
    read_svg = read.render((0, 0, 90, 25))
    write = SvgBackend()
    LocalVariableGlyph("flag", is_write=True).draw(write, (0, 0, 90, 25), DEFAULT_THEME)
    write_svg = write.render((0, 0, 90, 25))

    # Badge present on both (a <polygon> — the ▶ triangle), both BOLD-bordered.
    assert "<polygon" in read_svg and "<polygon" in write_svg
    assert 'stroke-width="2.5"' in read_svg and 'stroke-width="2.5"' in write_svg

    # Badge is on the dataflow side: read's ▶ is right of write's ▶.
    def _badge_x(svg):
        return float(re.search(r'points="([\d.]+),', svg).group(1))

    assert _badge_x(read_svg) > _badge_x(write_svg)

    # A plain constant/subVI box has NO badge — that's the whole distinction.
    plain = SvgBackend()
    WrappedBoxGlyph("Boolean", "const_fill", "localvar_stroke", 1.0).draw(
        plain, (0, 0, 90, 25), DEFAULT_THEME
    )
    assert "<polygon" not in plain.render((0, 0, 90, 25))


def test_formula_node_glyph_draws_monospace_script_text():
    """A Formula Node's box draws its ``script`` as monospace, multi-line,
    left-aligned text (task #61) — leading blank lines (common in the decoded
    XML) are stripped, interior lines are kept verbatim and one per source
    line."""
    from lvkit.render.glyph import FormulaNodeGlyph
    from lvkit.render.style import DEFAULT_THEME

    script = "\n\n  x = a + b;\n\n  y = x * 2;\n"
    backend = SvgBackend()
    FormulaNodeGlyph(script).draw(backend, (0.0, 0.0, 200.0, 100.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 200.0, 100.0))

    assert "x = a + b;" in svg
    assert "y = x * 2;" in svg
    assert 'font-family="monospace"' in svg
    # The box itself is drawn as a flat structure-styled rect.
    assert "<rect" in svg


def test_measure_text_grows_with_length_and_size():
    backend = SvgBackend()
    short = backend.measure_text("hi", 10)
    long = backend.measure_text("a longer label", 10)
    assert long > short
    assert backend.measure_text("hi", 20) > backend.measure_text("hi", 10)


# --------------------------------------------------------------------------- #
# Graph-driven scene join — real sample VIs (skip gracefully if unavailable)
# --------------------------------------------------------------------------- #

GROUND_TRUTH_VI = Path(".tmp/array average 1.vi")

# A VI with two captioned numeric constants: an I32 "Index" = 1 (decimal) and a
# hex-format U32 = 0x02 labelled "0x02 => Open Templates for Editing…" — the
# fixture for #59 (value + hex format) and #77 (value-box shrink + owned label).
CONST_LABEL_VI = Path(
    ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/appcontrol/"
    "appcontrol.llb/Current VIs Reference__ogtk.vi"
)
OPENG_SEARCH = [Path(".lvkit/cache/samples/OpenG/extracted")]

# Small, pre-verified samples covering: plain leaf VI, SubVI calls, a Case
# structure, and a Case structure NESTED inside another structure.
CASE_VI = Path(
    ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi"
)  # noqa: E501
NESTED_CASE_VI = Path(
    ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/variantconfig/"  # noqa: E501
    "variantconfig.llb/Write Panel to INI__ogtk.vi"
)
# A case genuinely nested inside another case's frame (not just a case inside
# a loop) — its scene has 2+ segment compound frame paths.
NESTED_CASE_CONTENT_VI = Path(
    ".lvkit/cache/samples/JKI-EasyXML/Source/Fast Parser/XML Loop Stack Recursion.vi"
)
# Flat (film-strip) sequence with 3 side-by-side frames (verified: frame 0/1
# node x-ranges don't overlap, has dividers, has 5 SubVI calls for the hover-
# tooltip test).
FLAT_SEQ_VI = Path(
    ".lvkit/cache/samples/JKI-EasyXML/Source/Fast Parser/"
    "test TCX read (installed 71).vi"
)
# Stacked (interactive) sequence with 3 overlapping frames.
STACKED_SEQ_VI = Path(
    ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/string/"
    "string.llb/Number to Proper Engl Text__ogtk.vi"
)
# The Graphical Test Runner's main UI — the only corpus VI carrying BUILT-IN
# reference constants ("This Application" / "This VI": ctlRefConst nodes with no
# ddo_uid, referencing a VI-server object rather than a front-panel control).
# These were once dropped from the graph, leaving their wires (drawn from the
# compressed wire table) floating into empty space — a render "void". Fixture
# for that regression (see test_builtin_reference_constants_render).
BUILTIN_REF_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner - Main UI - .vi"
)
CORPUS_VIS = [
    Path(
        ".lvkit/cache/samples/JKI-VI-Tester/source/Utilities/Get LV Class Members from Path.vi"  # noqa: E501
    ),
    Path(
        ".lvkit/cache/samples/JKI-EasyXML/Source/JKI Reuse Candidates/"
        "Is an Error__JKI Error Handling.vi"
    ),
    NESTED_CASE_CONTENT_VI,
    CASE_VI,
    Path(".lvkit/cache/samples/JKI-VI-Tester/source/Prototype/Test Project/Method1.vi"),
    Path(
        ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/array/array.llb/"  # noqa: E501
        "Reorder 1D Array2 (LVObject)__ogtk.vi"
    ),
    Path(
        ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/comparison/"  # noqa: E501
        "comparison.llb/U16 Changed__ogtk.vi"
    ),
    NESTED_CASE_VI,
]


def _load_graph(path: Path) -> tuple[InMemoryVIGraph, str] | None:
    """Load a sample VI into a fresh graph. Returns None if the file is
    missing or fails to load (extraction/parsing gaps unrelated to
    rendering) — corpus tests skip these rather than failing."""
    if not path.exists():
        return None
    graph = InMemoryVIGraph()
    try:
        graph.load_vi(path, mode=LoadMode.NONE)
    except Exception:
        return None
    return graph, graph.resolve_vi_name(path.name)


def _require_ground_truth() -> tuple[InMemoryVIGraph, str]:
    loaded = _load_graph(GROUND_TRUTH_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {GROUND_TRUTH_VI}")
    return loaded


def test_iter_nodes_excludes_vi_definition_node():
    graph, vi = _require_ground_truth()
    nodes = graph.iter_nodes(vi)
    assert nodes
    assert all(n.id != vi for n in nodes)


def test_get_terminal_resolves_a_known_terminal():
    graph, vi = _require_ground_truth()
    wires = graph.get_wires(vi, include_internal=True)
    assert wires
    term = graph.get_terminal(wires[0].source.terminal_id)
    assert term is not None
    assert term.id == wires[0].source.terminal_id


def test_get_wires_default_excludes_internal_self_loops():
    graph, vi = _require_ground_truth()
    external = graph.get_wires(vi)
    full = graph.get_wires(vi, include_internal=True)
    assert len(external) <= len(full)
    assert all(w.source.node_id != w.dest.node_id for w in external)
    # The ground-truth VI has a For-Loop with tunnels/shift registers, so
    # there genuinely are internal self-loop edges to exclude.
    assert len(full) > len(external)


def test_build_scene_joins_graph_and_geometry():
    graph, vi = _require_ground_truth()
    scene = build_scene(graph, vi)
    assert scene is not None

    all_nodes = graph.iter_nodes(vi)
    assert len(scene.nodes) + len(scene.structures) <= len(all_nodes)

    # FP terminal labels come from the graph (Terminal.name), not heap XML.
    fp_names = {fp.terminal.name for fp in scene.fp_terminals}
    assert "Array" in fp_names
    assert any(n and n.startswith("Array average") for n in fp_names)

    # A For-Loop structure is present and joined to real geometry.
    assert any(
        isinstance(s.node, StructureNode) and s.node.node_type == "forLoop"
        for s in scene.structures
    )

    # Wires are external-only and every branch is a real routed polyline.
    assert scene.wire_nets
    for net in scene.wire_nets:
        assert net.branches
        for branch in net.branches:
            assert len(branch) >= 2


def test_empty_string_field_is_empty_not_a_watermark():
    """An empty/unset string is genuinely EMPTY — an empty string control,
    NOT a dimmed "abc" watermark (the maintainer: "empty string default
    isn't a watermark, it's just empty"). A SET string shows its real value."""
    from lvkit.models import LVType
    from lvkit.render.glyph import ConstantGlyph
    from lvkit.render.nodes import _leaf_const_glyph
    from lvkit.render.style import DEFAULT_THEME

    string_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")

    empty = _leaf_const_glyph(string_type, raw=None)
    assert isinstance(empty, ConstantGlyph)
    assert empty.value == ""
    backend = SvgBackend()
    empty.draw(backend, (0.0, 0.0, 40.0, 20.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 40.0, 20.0))
    assert ">abc<" not in svg  # no watermark

    shown = _leaf_const_glyph(string_type, raw="'hi'")
    assert isinstance(shown, ConstantGlyph)
    assert shown.value == "hi"

    real = _leaf_const_glyph(string_type, raw="'hello'")
    assert isinstance(real, ConstantGlyph)
    assert real.value == "hello"
    assert real.dim is False


def test_path_field_draws_folder_mark_even_when_empty():
    """A Path constant/field draws a ``PathGlyph`` — a folder mark, plus the
    real path text when set — never a blank rectangle, even when unset."""
    from lvkit.models import LVType
    from lvkit.render.glyph import PathGlyph
    from lvkit.render.nodes import _leaf_const_glyph
    from lvkit.render.style import DEFAULT_THEME, wire_style

    path_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")

    empty = _leaf_const_glyph(path_type, raw=None)
    assert isinstance(empty, PathGlyph)
    assert empty.value == ""
    backend = SvgBackend()
    bounds = (0.0, 0.0, 21.0, 19.0)
    empty.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert "<polygon" in svg  # the folder mark draws even though empty
    assert empty.color == wire_style(path_type).color

    real = _leaf_const_glyph(path_type, raw="'C:\\\\data.txt'")
    assert isinstance(real, PathGlyph)
    assert real.value == "C:\\data.txt"


def test_class_refnum_constant_labeled_by_class_name_not_refnum():
    """A CLASS/LVObject constant (underlying ``Refnum`` WITH a ``classname``)
    draws a ``ClassGlyph`` (cube + CLASS NAME) — never the parser's
    placeholder raw value (``"Refnum(1)"``), never a generic "Refnum", and
    never the ``RefnumGlyph`` dog-ear (a class isn't LabVIEW's visual
    "reference" grammar — it's a class instance; issue #45's class-field
    fix). The constant/label path keys on ``underlying_type`` + ``classname``,
    independent of the wire family. A class refnum is NOT the ``refnum``
    family (that is reserved for GENERIC refs); with no custom wire style it
    draws the default grey class chain, not a reference wire. Same
    class-name rule shared with terminal labels — ``style.lv_type_label``."""
    from lvkit.models import LVType
    from lvkit.render.glyph import ClassGlyph
    from lvkit.render.nodes import _leaf_const_glyph
    from lvkit.render.style import lv_type_label, type_family

    cls = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname="NI DAQmx.lvlib:DAQmx Module Configuration.lvclass",
    )
    assert type_family(cls) == "unknown"  # a class refnum is NOT the refnum family
    assert lv_type_label(cls) == "DAQmx Module Configuration.lvclass"

    glyph = _leaf_const_glyph(cls, raw="Refnum(1)")
    assert isinstance(glyph, ClassGlyph)
    # The class's real short name — never the parser's placeholder raw value.
    assert glyph.name == "DAQmx Module Configuration.lvclass"

    # A GENERIC refnum constant (no classname) draws the RefnumGlyph dog-ear
    # + kind symbol — LabVIEW's visual reference grammar — keyed by its own
    # ``ref_type`` ("Occurrence" here), never the placeholder raw value or
    # bare wrapped text.
    from lvkit.render.glyph import RefnumGlyph

    gen = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="Occurrence"
    )
    assert type_family(gen) == "refnum"
    assert lv_type_label(gen) == "Occurrence Refnum"
    gen_glyph = _leaf_const_glyph(gen, raw="Refnum(1)")
    assert isinstance(gen_glyph, RefnumGlyph)
    assert gen_glyph.kind == "Occurrence"
    assert gen_glyph.terminal is None  # no registered payload


def test_io_name_tag_is_reference_wire_and_unresolved_class_is_grey_chain():
    """A LabVIEW I/O-name type (``Tag`` — DAQmx physical channel / task name,
    VISA resource name) draws the reference wire. But an UNRESOLVED class refnum
    (a ``classname`` with no wire style anywhere in its ancestry — its
    ``.lvclass``/parent unfound) is NOT a refnum wire: it draws the default GREY
    CHAIN (grey = unresolved, chain = still visibly a class), thicker per array
    dimension. A class that DOES set a wire style keeps it (``wire_style``
    returns before the family/default is consulted)."""
    import dataclasses

    from lvkit.models import LVType, WireStyle
    from lvkit.render.style import DEFAULT_THEME, type_family, wire_style

    tag = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Tag")
    assert type_family(tag) == "refnum"
    assert wire_style(tag).color == DEFAULT_THEME.wire_refnum

    tag_arr = LVType(kind=LVTypeKind.ARRAY, element_type=tag)
    assert wire_style(tag_arr).color == DEFAULT_THEME.wire_refnum

    unresolved_class = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", classname="Foo.lvclass"
    )
    ws = wire_style(unresolved_class)
    assert ws.color == DEFAULT_THEME.wire_default  # grey, not refnum
    assert ws.fill_pattern and len(set(ws.fill_pattern)) == 1  # a (uniform) CHAIN
    assert type_family(unresolved_class) == "unknown"  # NOT the refnum family
    # Gauge matched to what REAL class pens decode to across the corpus (total
    # ``Width`` 3.0, core 1.0) so an unresolved class is the same size as a
    # resolved one, not the thin scalar wire.
    assert ws.width == pytest.approx(3.0)
    assert ws.core_width == pytest.approx(1.0)

    # An array OF the unresolved class inherits the grey chain, widened the same
    # per-dimension amount EVERY array gets (generic bolding, chain preserved) —
    # no class-specific branch: each dimension adds a fixed step to width & core.
    arr = LVType(kind=LVTypeKind.ARRAY, element_type=unresolved_class)
    arr_ws = wire_style(arr)
    assert arr_ws.color == DEFAULT_THEME.wire_default
    assert arr_ws.fill_pattern == ws.fill_pattern  # element pattern carried through
    step = arr_ws.width - ws.width
    assert step > 0
    assert arr_ws.core_width == pytest.approx((ws.core_width or 0.0) + step)

    arr2 = LVType(kind=LVTypeKind.ARRAY, element_type=arr)  # 2-D
    arr2_ws = wire_style(arr2)
    assert arr2_ws.width == pytest.approx(ws.width + 2 * step)  # one step per dim
    assert arr2_ws.fill_pattern == ws.fill_pattern

    styled = dataclasses.replace(
        unresolved_class, wire_style=WireStyle(color="#abcdef", width=2.0)
    )
    assert wire_style(styled).color == "#abcdef"


def test_builtin_reference_constants_render():
    """A built-in reference constant ("This Application" / "This VI":
    ``ctlRefConst`` with no ddo_uid) references a VI-server object, not a
    front-panel control. It is still a real on-diagram object with an output
    wire, so it MUST be modelled and drawn — earlier it was silently skipped in
    the graph builder, so its wire (drawn from the compressed wire table) landed
    on a terminal with no node box: a render "void" with a wire floating into
    empty space. Assert both such constants now reach the scene as drawn nodes.
    """
    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded

    # Built-in refs are ctlRefConst graph nodes with no resolved FP control
    # (control_terminal_id is None) — the exact nodes once dropped.
    builtin_refs = [
        n
        for n in graph.iter_nodes(vi)
        if getattr(n, "node_type", None) == "ctlRefConst"
        and getattr(n, "control_terminal_id", None) is None
    ]
    assert builtin_refs, "expected built-in reference constants in the graph"

    scene = build_scene(graph, vi)
    assert scene is not None
    drawn_ctlref = {
        rn.node.name
        for rn in scene.nodes
        if getattr(rn.node, "node_type", None) == "ctlRefConst"
    }
    # The two built-in server refs this VI wires into a Bundle-By-Name must draw.
    assert "This Application" in drawn_ctlref
    assert "This VI" in drawn_ctlref

    # Every built-in ref graph node has a drawn RenderNode (with a terminal for
    # its wire to land on) — no silent drop, no floating wire.
    drawn_ids = {rn.node.id for rn in scene.nodes}
    for ref in builtin_refs:
        assert ref.id in drawn_ids, f"built-in ref {ref.id} ({ref.name}) not drawn"
        rn = next(r for r in scene.nodes if r.node.id == ref.id)
        assert rn.terminals, f"built-in ref {ref.name} drawn without a terminal"


# The GTR Main UI's "SMUI Template App Data" cluster constant — a 23-field
# private-data cluster whose heap box (issue #45) is the typedef's oversized
# front-panel layout, ~1031px tall. Identified by its TERM uid (stable across
# reparses; the constant itself carries no name).
_SMUI_CLUSTER_TERM_UID = "14625"


def test_cluster_constant_scene_uses_real_field_geometry():
    """The SMUI cluster constant's scene node carries REAL per-field geometry
    (issue #45 reopened): every field maps inside the constant's own drawn
    box, heights vary (no uniform-row stretch), and the output terminal sits
    at that REAL box's own center — where the heap's wire actually
    attaches — because the box is no longer synthetically compacted."""
    from lvkit.render.glyph import ClusterConstantGlyph

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (rn for rn in scene.nodes if rn.node.id == f"{vi}::{_SMUI_CLUSTER_TERM_UID}"),
        None,
    )
    assert target is not None, "SMUI Template App Data cluster constant not in scene"
    assert isinstance(target.glyph, ClusterConstantGlyph)
    cg = target.glyph.cluster_geom
    assert cg is not None, "no real cluster geometry reached the glyph"
    assert {f.name for f in cg.fields} == {name for name, _ in target.glyph.fields}

    bx1, by1, bx2, by2 = target.bounds
    # The constant is drawn at its own real box, so the fit-scale is 1.0 —
    # a field's on-screen rect is its (0, 0)-relative rect plus the box origin.
    assert cg.width == bx2 - bx1
    assert cg.height == by2 - by1
    for g in cg.fields:
        vx1, vy1, vx2, vy2 = g.value_rect
        assert bx1 - 1e-6 <= bx1 + vx1 and bx1 + vx2 <= bx2 + 1e-6, g
        assert by1 - 1e-6 <= by1 + vy1 and by1 + vy2 <= by2 + 1e-6, g

    heights = {round(g.value_rect[3] - g.value_rect[1], 3) for g in cg.fields}
    assert len(heights) > 1  # real field heights vary — not a uniform row stretch

    # The box is the REAL heap box (~1031px tall for 23 fields) — no longer
    # shrunk to a synthetic n * row_height stack.
    assert (by2 - by1) > 900

    output_term = next(t for t in target.terminals if t.terminal.direction == "output")
    assert output_term.center == ((bx1 + bx2) / 2, (by1 + by2) / 2)


# GTR Main UI's "SuitesRunning" — an EMPTY array of STRINGS (heap ddo uid
# 14774, term uid 14773) — the scalar-element half of the disabled-default
# extension (the array-of-cluster half is covered by
# test_array_of_cluster_constant_uses_real_element_geometry).
_SUITES_RUNNING_TERM_UID = "14773"


def test_empty_scalar_array_constant_shows_disabled_default_element():
    """"SuitesRunning" (an empty array of strings) must show a DISABLED
    default-valued element — a real empty-string constant glyph, dimmed —
    never a blank grey box (the disabled-default extension)."""
    from lvkit.render.glyph import ArrayConstantGlyph, ConstantGlyph

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (
            rn
            for rn in scene.nodes
            if rn.node.id == f"{vi}::{_SUITES_RUNNING_TERM_UID}"
        ),
        None,
    )
    assert target is not None, "SuitesRunning array constant not in scene"
    assert isinstance(target.glyph, ArrayConstantGlyph)
    assert target.glyph.elements == ()  # genuinely empty
    assert isinstance(target.glyph.default_element, ConstantGlyph)


def test_data_typed_refnum_field_draws_compact_or_expanded_type_never_value_cluster():
    """EVERY refnum field draws as a ``RefnumGlyph`` (dog-ear + kind symbol +
    a TERMINAL showing the registered payload's TYPE) — NEVER a value
    ``ClusterConstantGlyph`` (the payload is a TYPE, never editable field
    VALUES — issue #45's refnum trigger). Which TERMINAL a given field gets
    is the heap's own recorded per-field state (``layout.ClusterFieldGeom.
    refnum_expanded``/``refnum_payload`` — real, verified BDHb bounds, never
    a size guess or a re-flow):

    - COMPACT: a ``TypeTerminalGlyph`` badge — "TextStream" (Queue of
      STRING, ``text == "abc"``) and "Current VI's Refnum" (no registered
      payload at all — ``terminal is None``, frame + kind symbol only).
    - EXPANDED: "ResultChangedRef", a User Event refnum recorded in the
      heap at its real ~206px-tall expanded size — its terminal is a
      ``DimmedGlyph`` wrapping the payload's own REAL recursively-composed
      ``ClusterConstantGlyph`` (real per-field elements, never flattened
      "name: type" text and never the payload's actual F/0/testPass VALUE
      glyphs), positioned at the heap's own recorded placement
      (``terminal_rect``)."""
    from lvkit.render.glyph import ClusterConstantGlyph, DimmedGlyph, RefnumGlyph
    from lvkit.render.style import wire_style

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (rn for rn in scene.nodes if rn.node.id == f"{vi}::{_SMUI_CLUSTER_TERM_UID}"),
        None,
    )
    assert target is not None
    assert isinstance(target.glyph, ClusterConstantGlyph)

    # "ResultChangedRef" is recorded EXPANDED in the heap (multiCosm
    # index=1, h≈206) — its registered event-data cluster's TYPE draws
    # inline, dimmed, never as a nested value cluster.
    result_changed_ref = next(
        g for name, g in target.glyph.fields if name == "ResultChangedRef"
    )
    assert isinstance(result_changed_ref, RefnumGlyph), (
        "every refnum field, expanded or compact, must draw as RefnumGlyph "
        "(dog-ear + kind symbol + terminal), never a value cluster"
    )
    assert not isinstance(result_changed_ref, ClusterConstantGlyph)
    assert isinstance(result_changed_ref.terminal, DimmedGlyph), (
        "an EXPANDED refnum's terminal must be the payload's real elements, "
        "dimmed — never a compact badge"
    )
    assert isinstance(result_changed_ref.terminal.inner, ClusterConstantGlyph)
    smui_node = next(
        n for n in graph.iter_nodes(vi) if n.id.endswith("::14625")
    )
    assert isinstance(smui_node, ConstantNode)
    assert smui_node.lv_type is not None and smui_node.lv_type.fields is not None
    field = next(f for f in smui_node.lv_type.fields if f.name == "ResultChangedRef")
    assert field.type is not None and field.type.element_type is not None
    payload_fields = field.type.element_type.fields
    assert payload_fields is not None
    assert [name for name, _ in result_changed_ref.terminal.inner.fields] == [
        f.name for f in payload_fields
    ]
    assert result_changed_ref.terminal_rect is not None
    # The dog-ear/kind-symbol border is the REFNUM's own wire color (it
    # still reads as a refnum box).
    assert result_changed_ref.border_color == wire_style(field.type).color
    # The payload cluster's own nested "test error" field is a GENUINE value
    # cluster (its OWN kind is CLUSTER) — it still draws real box-in-box
    # geometry inside the dimmed terminal, per rule 6 (TYPE-KIND CORRECT).
    test_error = next(
        g
        for name, g in result_changed_ref.terminal.inner.fields
        if name == "test error"
    )
    assert isinstance(test_error, ClusterConstantGlyph)

    # A refnum field with NO registered payload (e.g. "Current VI's Refnum",
    # a plain LVObjCtl refnum) still draws the dog-ear + kind-symbol frame
    # (EVERY refnum reads as a refnum) but with no terminal at all — never a
    # ClusterConstantGlyph.
    current_vi_ref = next(
        g for name, g in target.glyph.fields if name == "Current VI's Refnum"
    )
    assert isinstance(current_vi_ref, RefnumGlyph)
    assert current_vi_ref.terminal is None
    assert not isinstance(current_vi_ref, ClusterConstantGlyph)

    # "TextStream" — a Queue of STRING — is recorded COMPACT in the heap: a
    # compact TypeTerminalGlyph badge with a real scalar mnemonic, never a
    # cluster glyph and never the expanded (DimmedGlyph) terminal — that's
    # for cluster payloads only.
    text_stream = next(g for name, g in target.glyph.fields if name == "TextStream")
    assert isinstance(text_stream, RefnumGlyph)
    assert not isinstance(text_stream, ClusterConstantGlyph)
    assert not isinstance(text_stream.terminal, DimmedGlyph)
    assert text_stream.terminal is not None
    assert text_stream.terminal.text == "abc"


# TestResult_Init.vi's array-of-clusters constant: a "failure" cluster array
# (heap ddo uid 502, indArr -> typeDef 569 -> stdClust 573), with a NESTED
# cluster field ("error" -> status/code/source) — see issue #45's array-of-
# clusters extension. Identified by its TERM uid (graph node id "...::633").
_ARRAY_OF_CLUSTER_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestResult/"
    "TestResult_Init.vi"
)
_ARRAY_OF_CLUSTER_TERM_UID = "633"


def test_array_of_cluster_constant_uses_real_element_geometry():
    """An array constant whose ELEMENT is a cluster gets the element's REAL
    heap geometry reaching the glyph (issue #45's array extension):
    ``ArrayConstantGlyph.cell_w``/``cell_h`` are the element's own real
    natural size (54x110, verified against the heap — ``typeDef`` ddo 569 /
    ``stdClust`` 573's own ``<bounds>``), not the synthetic fixed row height.

    This VI's array constant is its default/initializer value — genuinely
    EMPTY (``node.value == "[]"``, verified: TestResult_Init.vi initializes
    an empty failures array) — LabVIEW itself shows an empty array constant
    as a DISABLED default-valued element, never blank/greyed rows (the
    disabled-default extension): ``default_element`` is a REAL nested
    ``ClusterConstantGlyph`` at the same real geometry, with default field
    values — including the genuinely NESTED "error" cluster
    (status/code/source), a true box-in-box, not flattened text."""
    from lvkit.render.glyph import ArrayConstantGlyph, ClusterConstantGlyph

    loaded = _load_graph(_ARRAY_OF_CLUSTER_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {_ARRAY_OF_CLUSTER_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (
            rn
            for rn in scene.nodes
            if rn.node.id == f"{vi}::{_ARRAY_OF_CLUSTER_TERM_UID}"
        ),
        None,
    )
    assert target is not None, "array-of-cluster constant not in scene"
    assert isinstance(target.glyph, ArrayConstantGlyph)
    assert target.glyph.cell_w == 54.0
    assert target.glyph.cell_h == 110.0
    assert target.glyph.elements == ()  # genuinely empty default value

    default = target.glyph.default_element
    assert isinstance(default, ClusterConstantGlyph), (
        "an empty array-of-cluster constant must show a real disabled "
        "default CLUSTER element, not a blank rect"
    )
    real_cluster_geom = default.cluster_geom
    assert real_cluster_geom is not None
    assert real_cluster_geom.width == 54.0
    assert real_cluster_geom.height == 110.0
    assert {f.name for f in real_cluster_geom.fields} == {
        name for name, _ in default.fields
    }

    # The "error" field is a genuinely NESTED cluster (status/code/source) —
    # its glyph must be a real nested ClusterConstantGlyph, not flattened text.
    error_field = next(fg for fg in real_cluster_geom.fields if fg.name == "error")
    assert error_field.nested is not None
    assert {nf.name for nf in error_field.nested.fields} == {
        "status", "code", "source",
    }
    error_glyph = next(g for name, g in default.fields if name == "error")
    assert isinstance(error_glyph, ClusterConstantGlyph)
    assert error_glyph.cluster_geom is error_field.nested


def test_wire_color_from_source_terminal_type():
    graph, vi = _require_ground_truth()
    scene = build_scene(graph, vi)
    assert scene is not None
    assert scene.wire_nets
    colors = {net.style.color for net in scene.wire_nets}
    # Most wires here carry a DBL (float) array element -> orange.
    assert DEFAULT_THEME.wire_float in colors
    # The loop's auto-index tunnel carries an Int32 count -> blue, distinct
    # from the default. Colors really do come from the source terminal's
    # LVType, not a single flat wire color.
    assert DEFAULT_THEME.wire_int in colors


def test_render_vi_file_matches_ground_truth_shape():
    if not GROUND_TRUTH_VI.exists():
        pytest.skip(f"sample VI not available: {GROUND_TRUTH_VI}")
    svg = render_vi_file(GROUND_TRUTH_VI)
    assert svg is not None
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "Array" in svg
    assert "0.0" in svg  # ConstantNode.value
    assert ">N<" in svg  # loop count border terminal, from the graph tunnel
    assert ">+<" in svg and ">÷<" in svg  # Add / Divide primitives
    assert DEFAULT_THEME.wire_float in svg


def test_project_local_subvi_hover_shows_real_terminal_names():
    """A project-local SubVI call's connector-pane hover must read the callee's
    real FP control labels (``message``, ``error in (no error)``, ``TestCase
    in``, ...), never the ``terminal N`` index fallback.

    Regression for two linked bugs that made a class-METHOD SubVI lose its
    param names:
      1. ``resolve_against`` resolved a member VI's LinkSavePathRef (tokens
         describe the OWNING ``.lvclass``, member filename carried separately)
         to the class file itself, so ``extract_vi_xml`` failed → the SubVI was
         stubbed with no connector pane. (loading.py member-VI redirect)
      2. ``_connect_subvi_calls`` — which copies callee labels onto the call
         terminals — was gated ``if iuse_to_qname:``, so a caller whose LIvi
         records no iUse→qname map (empty dict) skipped enrichment entirely.
         (construction.py: run whenever there are SubVI calls)

    ``fail.vi`` is a method of ``TestCase.lvclass`` called by both
    ``failUnless.vi`` (empty iuse map — bug 2) and ``failUnlessEqual.vi``.
    """
    root = Path(".lvkit/cache/samples/JKI-VI-Tester")
    callers = [
        root / "source/Classes/TestCase/failUnless.vi",
        root / "source/Classes/TestCase/failUnlessEqual.vi",
    ]
    if not all(c.exists() for c in callers):
        pytest.skip("JKI-VI-Tester sample corpus not available")

    for caller in callers:
        svg = render_vi_file(
            caller,
            mode=LoadMode.MINIMAL,
            search_paths=[root],
        )
        assert svg is not None
        # The fail.vi SubVI hover <title> block. The title line is the callee's
        # FULLY QUALIFIED name (e.g. "TestCase.lvclass:fail.vi"), so allow an
        # optional Class.lvclass:/lib: qualifier prefix before "fail.vi".
        m = re.search(r"<title>[^<>\n]*fail\.vi\n(.*?)</title>", svg, re.DOTALL)
        assert m is not None, f"no fail.vi hover title in {caller.name}"
        block = m.group(1)
        # Real connector-pane control labels, not "terminal N".
        assert "message" in block
        assert "error in" in block
        assert "TestCase in" in block
        assert "TestCase out" in block
        assert not re.search(r"terminal \d", block), (
            f"fail.vi hover still shows a 'terminal N' fallback in "
            f"{caller.name}:\n{block}"
        )


def test_constant_value_box_shrinks_past_caption_and_renders_owned_label():
    """A captioned numeric constant draws a COMPACT value box (not the
    caption-inflated DDO bounds) plus its VISIBLE owned label as free text —
    task #77, built on the #59 value+format extraction. A caption whose HIDDEN
    bit (objFlags 0x8) is set — LabVIEW's auto-created terminal-name label like
    "Index"/"offset (0)" — must NOT render. The hex constant also proves the #59
    radix format (0x02 -> ``x2``) survives the normal re-extraction path."""
    if not CONST_LABEL_VI.exists():
        pytest.skip(f"sample VI not available: {CONST_LABEL_VI}")
    graph = InMemoryVIGraph()
    graph.load_vi(CONST_LABEL_VI, mode=LoadMode.NONE, search_paths=OPENG_SEARCH)
    name = graph.resolve_vi_name(CONST_LABEL_VI.name)
    scene = build_scene(graph, name)

    by_val = {
        rn.node.value: rn for rn in scene.nodes if isinstance(rn.node, ConstantNode)
    }
    assert {"1", "2"} <= by_val.keys()
    hexc = by_val["2"]
    # Compact value cell, NOT the ~139x69 caption-inflated DDO box.
    assert hexc.bounds[2] - hexc.bounds[0] < 40
    assert hexc.bounds[3] - hexc.bounds[1] < 40
    # The VISIBLE caption (0x8 clear) carries the developer's free text.
    assert hexc.owned_label is not None
    assert "Open Templates" in hexc.owned_label.text
    # The "1" constant's caption is "Index" with the HIDDEN bit (0x8) set — an
    # auto-created terminal-name label LabVIEW hides, so it must NOT surface.
    assert by_val["1"].owned_label is None

    svg = render_vi_file(
        CONST_LABEL_VI,
        mode=LoadMode.NONE,
        search_paths=OPENG_SEARCH,
    )
    assert svg is not None
    assert ">x2<" in svg  # #59 hex radix value survives re-extraction
    assert "Open Templates" in svg  # visible owned label rendered
    # The hidden auto-label "Index" (0x8 on the "1" constant) must NOT surface.
    # A legit "Index Array" node name now exists and wraps to two <text> lines
    # "Index"/"Array" in its box — so a leaked caption is a LONE "Index" text
    # with no adjacent "Array" line, not any occurrence of the substring.
    assert not re.search(r">Index</text>(?!\s*<text[^>]*>Array</text>)", svg)


def test_render_vi_file_determinism_same_process():
    if not GROUND_TRUTH_VI.exists():
        pytest.skip(f"sample VI not available: {GROUND_TRUTH_VI}")
    first = render_vi_file(GROUND_TRUTH_VI)
    second = render_vi_file(GROUND_TRUTH_VI)
    assert first is not None and second is not None
    assert first == second


def test_render_vi_file_determinism_across_hash_seeds():
    """Node/wire ordering must not depend on PYTHONHASHSEED — set membership
    in the graph (_vi_nodes) is hash-randomized between processes, so a
    real determinism check needs two separate interpreters."""
    if not GROUND_TRUTH_VI.exists():
        pytest.skip(f"sample VI not available: {GROUND_TRUTH_VI}")

    script = (
        "import hashlib\n"
        "from lvkit.render import render_vi_file\n"
        f"svg = render_vi_file({str(GROUND_TRUTH_VI)!r})\n"
        "assert svg is not None\n"
        "print(hashlib.sha256(svg.encode()).hexdigest())\n"
    )
    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        digests.append(result.stdout.strip())
    assert digests[0] == digests[1]


def test_case_structures_render_all_frames_not_just_shown():
    """Roadmap #17: every case frame's nodes are now IN the scene (tagged
    with a frame path), not excluded — the single-frame policy inverted for
    case structures (stacked sequences got the same treatment separately —
    see test_stacked_sequence_svg_has_lv_frame_and_selector)."""
    loaded = _load_graph(CASE_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {CASE_VI}")
    graph, vi = loaded

    all_nodes = graph.iter_nodes(vi)
    case_nodes = [n for n in all_nodes if isinstance(n, CaseStructureNode)]
    assert case_nodes, "expected sample to contain a Case structure"

    total_frame_children = sum(
        len(frame.inner_node_uids) for c in case_nodes for frame in c.frames
    )
    if total_frame_children == 0:
        pytest.skip("sample's case structure(s) have no frame children")

    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("sample lacks required diagram geometry")

    # Non-default frames' nodes are now present (tagged with a non-empty
    # frame_path), not wholesale-excluded — a hidden frame's individual node
    # may still be dropped if it genuinely lacks heap geometry (the relaxed
    # fail-closed rule skips-with-log rather than aborting), so this checks
    # "at least one survives and is frame-tagged", not "every single one".
    any_non_default_visible = False
    for c in case_nodes:
        if not c.frames:
            continue
        shown = next((f for f in c.frames if f.is_default), c.frames[0])
        for frame in c.frames:
            if frame is shown or not frame.inner_node_uids:
                continue
            for uid in frame.inner_node_uids:
                qid = f"{vi}::{uid}"
                render_node = next(
                    (n for n in scene.nodes if n.node.id == qid),
                    None,
                )
                if render_node is not None:
                    assert render_node.frame_path
                    any_non_default_visible = True
    assert any_non_default_visible, (
        "expected at least one non-default case-frame node to survive into "
        "the scene, frame-tagged"
    )


def test_case_svg_has_lv_frame_groups_one_visible_per_struct():
    loaded = _load_graph(CASE_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {CASE_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None or not scene.frame_values:
        pytest.skip("sample has no interactive case structures")
    svg = render_vi(graph, vi)
    assert svg is not None
    assert '<g class="lv-frame" data-path="' in svg
    assert '<g class="lv-selector' in svg  # may carry lv-clickable too
    assert "data-lv-frames=" in svg and "data-lv-default=" in svg
    # a case has a real dropdown: a hidden menu with clickable options/values
    assert 'class="lv-menu"' in svg
    assert 'class="lv-option' in svg and "data-lv-value=" in svg
    assert 'data-lv-action="toggle"' in svg

    # Off-frame groups are hidden by the lv-frame-hidden CLASS (never an inline
    # display:none the controller couldn't later clear — see draw.py).
    for raw, values in scene.frame_values.items():
        visible = hidden = 0
        for value in values:
            path_attr = f"{raw}={value}"
            pattern = (
                r'<g class="(lv-frame[^"]*)" data-path="' + re.escape(path_attr) + r'">'
            )
            matches = re.findall(pattern, svg)
            assert matches, f"no lv-frame group for {path_attr}"
            for cls in matches:
                if "lv-frame-hidden" in cls:
                    hidden += 1
                else:
                    visible += 1
        assert visible >= 1
        assert hidden == visible * (len(values) - 1) if len(values) > 1 else True


def test_nested_case_svg_has_compound_data_path():
    """A case genuinely nested inside another case's frame produces a
    ``data-path`` with TWO ``struct=val`` segments (root->leaf), so nested
    cases compose without needing nested DOM (roadmap #17)."""
    loaded = _load_graph(NESTED_CASE_CONTENT_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {NESTED_CASE_CONTENT_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("sample lacks required diagram geometry")

    compound = [
        p
        for p in {n.frame_path for n in scene.nodes if len(n.frame_path) >= 2}
        | {s.frame_path for s in scene.structures if len(s.frame_path) >= 2}
    ]
    if not compound:
        pytest.skip("sample has no genuinely nested case frame in this scene")

    svg = render_vi(graph, vi)
    assert svg is not None
    path = compound[0]
    encoded = encode_frame_path(path)
    assert f'data-path="{encoded}"' in svg
    assert encoded.count("=") >= 2  # at least two struct=val segments


def test_loop_only_vi_has_no_frame_groups():
    """Regression guard: a VI with no interactive structures (no case, no
    stacked sequence — just a For-Loop) produces no lv-frame / lv-selector
    groups at all."""
    if not GROUND_TRUTH_VI.exists():
        pytest.skip(f"sample VI not available: {GROUND_TRUTH_VI}")
    svg = render_vi_file(GROUND_TRUTH_VI)
    assert svg is not None
    # (The inline frame-controller <script> mentions the CSS class names in
    # its JS source even when unused, so check for the actual SVG elements —
    # class="lv-frame"/"lv-selector" — not a bare substring match.)
    assert 'class="lv-frame"' not in svg
    assert 'class="lv-selector"' not in svg


def test_flat_sequence_frames_tile_and_have_dividers():
    """Roadmap: a flat (film-strip) sequence's frames must no longer overlap
    at one origin — each frame's nodes tile by the heap-recorded per-frame
    x/y offset, and the structure records divider x-positions between
    frames. Flat sequences stay non-interactive (no lv-frame/lv-selector for
    their own content — see _frame_path scope)."""
    loaded = _load_graph(FLAT_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {FLAT_SEQ_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("sample lacks required diagram geometry")

    flat = [
        s
        for s in scene.structures
        if isinstance(s.node, SequenceNode) and s.node.node_type == "flatSequence"
    ]
    if not flat:
        pytest.skip("sample has no flat sequence in this scene")
    structure = flat[0]
    assert structure.dividers, "expected inter-frame divider positions"

    # Frame 0 vs frame 1's inner nodes must NOT land at the same x — the
    # whole point of tiling. inner_node_uids is per SequenceFrame.
    frames = structure.node.frames  # type: ignore[attr-defined]
    assert len(frames) >= 2
    node_by_uid = {n.node.id: n for n in scene.nodes}

    def _x_centers(frame_idx: int) -> list[float]:
        frame = frames[frame_idx]
        xs = []
        for uid in frame.inner_node_uids:
            qid = f"{vi}::{uid}"
            rn = node_by_uid.get(qid)
            if rn is not None:
                x1, _y1, x2, _y2 = rn.bounds
                xs.append((x1 + x2) / 2)
        return xs

    xs0 = _x_centers(0)
    xs1 = _x_centers(1)
    if not xs0 or not xs1:
        pytest.skip("frame 0/1 have no renderable inner nodes in this scene")
    assert max(xs0) < min(xs1) or max(xs1) < min(xs0), (
        "frame 0 and frame 1 node x-ranges overlap — tiling offset not applied"
    )

    svg = render_vi_file(FLAT_SEQ_VI, mode=LoadMode.NONE)
    assert svg is not None
    assert svg.count("<line") >= len(structure.dividers)


def test_sequence_tunnels_get_geometry_and_render():
    """A sequence's tunnel terminals live in each ``sequenceFrame``'s (or the
    stacked sequence's) own termList — layout must map them so they render as
    border terminals. Regression: they previously got no geometry (their
    frame termList was never walked / the graph modeled them under a nested
    alias uid), so no tunnel drew and every wire through them was dropped."""
    for path in (FLAT_SEQ_VI, STACKED_SEQ_VI):
        loaded = _load_graph(path)
        if loaded is None:
            continue
        graph, vi = loaded
        scene = build_scene(graph, vi)
        if scene is None:
            continue
        seqs = [s for s in scene.structures if isinstance(s.node, SequenceNode)]
        if not seqs:
            continue
        total_borders = sum(len(s.border_terminals) for s in seqs)
        assert total_borders > 0, (
            f"{path.name}: sequence(s) rendered zero tunnel border terminals"
        )
        return  # at least one sample present and asserted
    pytest.skip("no sequence sample VI available")


def test_stacked_sequence_svg_has_lv_frame_and_selector():
    """Stacked sequences are now interactive (roadmap: generalized from the
    #17 case machinery): every frame renders (tagged with a frame path),
    with a clickable ``◄ index ►`` selector, only the default (frame 0)
    starting visible."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {STACKED_SEQ_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("sample lacks required diagram geometry")

    stacked = [
        s
        for s in scene.structures
        if isinstance(s.node, SequenceNode)
        and s.node.node_type != "flatSequence"
        and scene.frame_values.get(s.raw_uid)
    ]
    if not stacked:
        pytest.skip("sample has no interactive stacked sequence in this scene")

    svg = render_vi(graph, vi)
    assert svg is not None
    assert 'class="lv-frame"' in svg
    assert 'class="lv-selector' in svg  # may carry lv-clickable too
    assert "◄ ►" in svg or "►" in svg  # no ▼ (that's the case affordance)

    for structure in stacked:
        values = scene.frame_values[structure.raw_uid]
        default = scene.default_frame.get(structure.raw_uid)
        visible = hidden = 0
        for value in values:
            path_attr = f"{structure.raw_uid}={value}"
            pattern = (
                r'<g class="(lv-frame[^"]*)" data-path="' + re.escape(path_attr) + r'">'
            )
            matches = re.findall(pattern, svg)
            assert matches, f"no lv-frame group for {path_attr}"
            for cls in matches:
                if "lv-frame-hidden" in cls:
                    hidden += 1
                else:
                    visible += 1
        assert visible >= 1
        # This VI's stacked sequence carries an OUT-OF-RANGE ``dIdx`` (a legacy
        # global-diagram ordinal, not a local frame index), so the parser's
        # range check rejects it and the initial view falls back to frame 0.
        # (An in-range ``dIdx`` DOES drive the displayed frame — see #30.)
        assert default == "0"


def test_render_vi_file_determinism_across_hash_seeds_stacked_seq():
    """Same determinism guarantee as the case-VI version, extended to a VI
    with an interactive stacked sequence."""
    if not STACKED_SEQ_VI.exists():
        pytest.skip(f"sample VI not available: {STACKED_SEQ_VI}")

    script = (
        "import hashlib\n"
        "from lvkit.graph.loading import LoadMode\n"
        "from lvkit.render import render_vi_file\n"
        f"svg = render_vi_file({str(STACKED_SEQ_VI)!r}, mode=LoadMode.NONE)\n"
        "assert svg is not None\n"
        "print(hashlib.sha256(svg.encode()).hexdigest())\n"
    )
    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        digests.append(result.stdout.strip())
    assert digests[0] == digests[1]


def test_render_vi_file_determinism_across_hash_seeds_case_vi():
    """Same determinism guarantee as
    test_render_vi_file_determinism_across_hash_seeds, extended to a VI with
    interactive case structures (frame-path partitioning must not depend on
    hash-randomized set iteration either)."""
    if not CASE_VI.exists():
        pytest.skip(f"sample VI not available: {CASE_VI}")

    script = (
        "import hashlib\n"
        "from lvkit.graph.loading import LoadMode\n"
        "from lvkit.render import render_vi_file\n"
        f"svg = render_vi_file({str(CASE_VI)!r}, mode=LoadMode.NONE)\n"
        "assert svg is not None\n"
        "print(hashlib.sha256(svg.encode()).hexdigest())\n"
    )
    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        digests.append(result.stdout.strip())
    assert digests[0] == digests[1]


# --------------------------------------------------------------------------- #
# Compound Arithmetic (cpdArith): real-bounds rectangle + operator symbol +
# per-terminal invert ("Not") bubbles.
# --------------------------------------------------------------------------- #


def test_compound_arithmetic_renders_box_with_invert_bubble():
    """The stacked-sequence sample's Compound Arithmetic node (id 802) has
    2 Boolean input terminals, operation "add", and one inverted input
    terminal — the SVG must show a CompoundArithGlyph rectangle (not the old
    hexagon/triangle fallback), sized to the node's REAL heap bounds (not a
    fixed 32x32 or a union-of-terminals triangle), split by a vertical
    divider into a right operator cell and a left area with one horizontal
    divider per input row (2 inputs -> 1 horizontal divider), plus exactly
    one invert-bubble circle for the single inverted terminal.

    Its operation (from objFlags bits 16-18) is AND on Boolean terminals, so
    the symbol is "∧" (see
    codegen/nodes/compound.py::generate_compound_arith's boolean-context
    translation, mirrored by render for this same node)."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {STACKED_SEQ_VI}")
    graph, vi = loaded

    cpd_nodes = [
        n
        for n in graph.iter_nodes(vi)
        if isinstance(n, PrimitiveNode) and n.node_type == "cpdArith"
    ]
    if not cpd_nodes:
        pytest.skip("sample has no cpdArith node")
    node = cpd_nodes[0]
    inverted_terminals = [t for t in node.terminals if t.inverted]
    assert inverted_terminals, (
        "expected sample cpdArith node to have an inverted terminal"
    )
    num_inputs = sum(1 for t in node.terminals if t.direction == "input")
    assert num_inputs == 2, "expected sample cpdArith node to have 2 inputs"

    scene = build_scene(graph, vi)
    assert scene is not None
    render_node = next(rn for rn in scene.nodes if rn.node.id == node.id)
    assert isinstance(render_node.glyph, CompoundArithGlyph)
    assert render_node.glyph.num_inputs == num_inputs
    # The glyph draws at the node's OWN heap bounds — not a fixed size and not
    # the ArithGlyph union-of-terminal-bounds special case in draw_node.
    x1, y1, x2, y2 = render_node.bounds
    assert x2 > x1 and y2 > y1

    svg = render_vi(graph, vi)
    assert svg is not None
    # Context-help title: node identity on the first line, then its connector
    # pane (terminal types) below — so match the identity line, not the whole
    # (now multi-line) title.
    assert "<title>Compound Arithmetic\n" in svg
    # Boolean-context operator symbol: this node's AND mode renders "∧".
    assert ">∧<" in svg

    # Isolate JUST this node's draw output to count its own invert bubbles
    # (other unrelated nodes in this VI, e.g. Format/Scan String, also carry
    # inverted=True terminals — see models.py's DCO objFlags bit 16 — so a
    # whole-SVG circle count would over-count) and its divider lines (1
    # vertical operator-cell divider + num_inputs-1 horizontal row dividers).
    # draw_node() only draws the always-visible node markup now — its hidden
    # connector-help panel (which redraws this same glyph a second time, as
    # its small central icon) is drawn separately, in one top-level overlay
    # pass (draw.py's draw_help_overlay, called by draw_scene) — so there's
    # nothing to slice off before counting here.
    backend = SvgBackend()
    draw_node(render_node, backend, DEFAULT_THEME)
    node_svg = backend.render(render_node.bounds)
    stroke = f'stroke="{DEFAULT_THEME.prim_stroke}"'
    fill = f'fill="{DEFAULT_THEME.canvas}"'
    bubbles = re.findall(
        r"<circle[^>]*" + re.escape(fill) + r"[^>]*" + re.escape(stroke) + r"[^>]*/>",
        node_svg,
    )
    assert len(bubbles) == len(inverted_terminals)
    dividers = re.findall(r"<line[^>]*/>", node_svg)
    assert len(dividers) == 1 + (num_inputs - 1)


def test_render_vi_file_determinism_cpdarith_invert_bubble():
    """Determinism across hash seeds for the invert-bubble geometry
    specifically — terminal iteration/bubble placement must not depend on
    hash-randomized ordering."""
    if not STACKED_SEQ_VI.exists():
        pytest.skip(f"sample VI not available: {STACKED_SEQ_VI}")

    script = (
        "import hashlib\n"
        "from lvkit.graph.loading import LoadMode\n"
        "from lvkit.render import render_vi_file\n"
        f"svg = render_vi_file({str(STACKED_SEQ_VI)!r}, mode=LoadMode.NONE)\n"
        "assert svg is not None\n"
        "print(hashlib.sha256(svg.encode()).hexdigest())\n"
    )
    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        digests.append(result.stdout.strip())
    assert digests[0] == digests[1]


# --------------------------------------------------------------------------- #
# Corpus acceptance: render a varied sample of real VIs, no exceptions,
# scene counts bounded by the graph, wires external-only.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("vi_path", CORPUS_VIS, ids=[str(p) for p in CORPUS_VIS])
def test_corpus_renders_without_exceptions(vi_path: Path):
    loaded = _load_graph(vi_path)
    if loaded is None:
        pytest.skip(f"sample VI not available or failed to load: {vi_path}")
    graph, vi = loaded

    all_nodes = graph.iter_nodes(vi)
    # Upper bound: the renderer draws every wire EXCEPT the paired outer<->inner
    # tunnel/SR pass-throughs, so all wires (incl. internal) is the ceiling —
    # real border-to-border dataflow (e.g. loop N -> tunnel) is drawn.
    all_wires = graph.get_wires(vi, include_internal=True)

    # This is the acceptance bar: render must not raise. A None return
    # (fail-closed, missing geometry) is an accepted outcome, not a failure.
    svg = render_vi(graph, vi)

    if svg is None:
        return

    built = build_scene(graph, vi)
    assert built is not None
    assert len(built.nodes) + len(built.structures) <= len(all_nodes)
    rendered_branches = sum(len(net.branches) for net in built.wire_nets)
    assert rendered_branches <= len(all_wires)
    for net in built.wire_nets:
        for branch in net.branches:
            assert len(branch) >= 2


def _count_non_endpoint_crossings(scene: Scene) -> int:
    """How many routed wire segments pass through the interior of a node
    that is NOT that wire's own source/dest node. Obstacles are scoped per
    wire net's own frame path (``_frame_compatible``) — a node exclusive to
    a mutually-exclusive case/stacked-sequence frame is never visible at
    the same time as a wire outside that frame, so it isn't a real
    obstacle for it even if their heap-derived boxes overlap."""
    crossings = 0
    for net in scene.wire_nets:
        obstacles = [
            n.bounds
            for n in scene.nodes
            if _frame_compatible(n.frame_path, net.frame_path)
        ]
        for branch in net.branches:
            src, dst = branch[0], branch[-1]
            for i in range(len(branch) - 1):
                (x1, y1), (x2, y2) = branch[i], branch[i + 1]
                steps = int(max(abs(x2 - x1), abs(y2 - y1)) / 2) + 1
                for s in range(1, steps):
                    x = x1 + (x2 - x1) * s / steps
                    y = y1 + (y2 - y1) * s / steps
                    for bx1, by1, bx2, by2 in obstacles:
                        if not (bx1 + 1 < x < bx2 - 1 and by1 + 1 < y < by2 - 1):
                            continue
                        near_src = bx1 <= src[0] <= bx2 and by1 <= src[1] <= by2
                        near_dst = bx1 <= dst[0] <= bx2 and by1 <= dst[1] <= by2
                        if near_src or near_dst:
                            continue
                        crossings += 1
                        break
    return crossings


def test_no_wire_segment_crosses_a_non_endpoint_node():
    """Regression for the router cutting through unrelated nodes: every
    routed wire segment must clear every node it doesn't start or end at."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available or failed to load: {STACKED_SEQ_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("scene build failed (missing geometry)")
    assert _count_non_endpoint_crossings(scene) == 0


def _inside(pt: Point, rect: Rect) -> bool:
    x, y = pt
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2


def _on_border(pt: Point, struct) -> bool:
    return any(_inside(pt, bt.bounds) for bt in struct.border_terminals)


def _count_structure_crossings(scene: Scene) -> int:
    """Routed wire segments passing through the INTERIOR of a STRUCTURE the
    wire does not live INSIDE — INCLUDING a structure it merely CONNECTS TO
    via an outer tunnel. A wire is contained (allowed inside) only when BOTH
    endpoints lie within the structure bounds (an interior node, or an inner
    tunnel on the border); if even one endpoint is outside, the wire connects
    from outside (or is unrelated) and must stop at the border. The single
    border/tunnel contact zone is excluded so a legitimate outer-face
    attachment isn't miscounted. Frame-aware."""
    crossings = 0
    for net in scene.wire_nets:
        structs = [
            s
            for s in scene.structures
            if _frame_compatible(s.frame_path, net.frame_path)
        ]
        for branch in net.branches:
            a, b = branch[0], branch[-1]
            blockers = [
                s
                for s in structs
                if not (_inside(a, s.bounds) and _inside(b, s.bounds))
            ]
            for i in range(len(branch) - 1):
                (x1, y1), (x2, y2) = branch[i], branch[i + 1]
                steps = int(max(abs(x2 - x1), abs(y2 - y1)) / 2) + 1
                hit = False
                for st in range(1, steps):
                    x = x1 + (x2 - x1) * st / steps
                    y = y1 + (y2 - y1) * st / steps
                    for s in blockers:
                        bx1, by1, bx2, by2 = s.bounds
                        if (
                            bx1 + 1 < x < bx2 - 1
                            and by1 + 1 < y < by2 - 1
                            and not _on_border((x, y), s)
                        ):
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    crossings += 1
    return crossings


def _count_tunnel_face_mismatches(scene: Scene) -> tuple[int, int]:
    """For every branch endpoint sitting on a structure border terminal, the
    stub must leave toward the INNER face when the other endpoint is inside
    the structure, the OUTER face when outside. Returns (mismatches, total)."""
    mismatches = 0
    total = 0
    for net in scene.wire_nets:
        for branch in net.branches:
            if len(branch) < 2:
                continue
            a, b = branch[0], branch[-1]
            for end, other, nxt in (
                (a, b, branch[1]),
                (b, a, branch[-2]),
            ):
                for s in scene.structures:
                    on_border = any(
                        _inside(end, bt.bounds) for bt in s.border_terminals
                    )
                    if not on_border:
                        continue
                    dx, dy = nxt[0] - end[0], nxt[1] - end[1]
                    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                        continue
                    total += 1
                    sx1, sy1, sx2, sy2 = s.bounds
                    cx, cy = (sx1 + sx2) / 2, (sy1 + sy2) / 2
                    other_inside = _inside(other, s.bounds)
                    if abs(dx) >= abs(dy):
                        stub_inward = (cx - end[0]) * dx > 0
                    else:
                        stub_inward = (cy - end[1]) * dy > 0
                    if stub_inward != other_inside:
                        mismatches += 1
                    break
    return mismatches, total


def test_endpoint_containers_resolves_fp_terminal_endpoint():
    """``_endpoint_containers`` (task #53/#72 coercion-dot fix): an endpoint
    whose terminal is an ``FPTerminal`` — a front-panel control's on-diagram
    glyph referenced from inside a frame — is NOT a graph node (see
    ``_fp_terminal_frame_path``'s docstring), so ``by_id.get(end.node_id)``
    can't find it. Before the fix this silently returned ``[]`` (no
    containment at all); it must now resolve the SAME structural chain
    ``_wire_path`` already uses for this terminal kind.

    Real-corpus case: GTR's "Slide" DBL front-panel terminal (referenced
    from inside the "UI: Initialize" event frame, structure uid 36) is fed
    by a "100" constant (uid 17153) living inside structure 48, itself
    inside 36. The wire's destination endpoint must now report ``['36']``
    (not ``[]``), so the innermost COMMON container is 36 (shared with the
    source's ``['36', '48']``) instead of ``None``."""
    from lvkit.render.scene import _containment_of, _innermost_common_container

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    by_id = {n.id: n for n in graph.iter_nodes(vi)}

    wires = [
        w
        for w in graph.get_wires(vi, include_internal=True)
        if w.source.terminal_id.endswith("::17153")
    ]
    assert wires, "GTR's 'Slide' constant (uid 17153) wire not found"
    w = wires[0]

    assert _containment_of(w.source, graph, by_id, vi) == ["36", "48"]
    assert _containment_of(w.dest, graph, by_id, vi) == ["36"]
    assert _innermost_common_container(w, graph, by_id, vi) == "36"


def test_gtr_slide_coercion_dot_frame_scoped_not_root():
    """Real-corpus check (task #53/#72): GTR's "Slide" ``100``->DBL coercion
    dot's ``RenderWireNet`` now gets a real ``container_uid`` (36, the
    owning "UI: Initialize" event frame's structure) instead of ``None``
    (the root diagram) — so ``composite.build_render_tree`` buckets it into
    that frame's ``lv-frame`` toggle/clip group instead of drawing it
    unconditionally at the top level, where it used to float independent of
    whichever frame was actually selected."""
    from lvkit.render.composite import build_render_tree

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("scene build failed (missing geometry)")

    target = next(
        (
            net
            for net in scene.wire_nets
            if net.source is not None
            and net.source.source.terminal_id.endswith("::17153")
        ),
        None,
    )
    assert target is not None, "GTR's Slide coercion-dot wire net not in scene"
    assert target.coercion_dots  # this IS the numeric-coercion wire
    assert target.container_uid == "36"
    assert target.container_uid is not None  # never root -- would escape its frame

    tree = build_render_tree(scene)
    assert not any(
        pt in tree.content.dots for pt in target.coercion_dots
    ), "the Slide coercion dot must NOT be in the root diagram's unconditional dots"


def test_no_wire_segment_crosses_a_structure_it_is_not_inside():
    """Structures ARE obstacles: a wire must route AROUND the interior of any
    For/While Loop, Case, or Sequence box it does not LIVE INSIDE — including
    one it merely CONNECTS TO via an outer tunnel (the user's reported
    float-over-For-Loop and shift-register-over-stacked-sequence cases). A
    wire only lives inside a structure when BOTH endpoints are within it
    (interior node or inner tunnel); an external wire stops at the outer
    border. Contain/frame exemptions keep legitimate interior wiring intact."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available or failed to load: {STACKED_SEQ_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("scene build failed (missing geometry)")
    crossings = _count_structure_crossings(scene)
    # Regression guard (task #68, resolved): an outer-scope wire in this VI's
    # containing While Loop used to cross a Case gated to a frame of the inner
    # stacked sequence — the router's per-frame obstacle set didn't treat an
    # unconstrained outer wire as an obstacle for every possible frame. The
    # visibility-graph router now routes it correctly (verified 0 crossings with
    # faithful routing both on AND off), so this is tightened back to == 0.
    assert crossings == 0, (
        f"expected 0 structure crossings, got {crossings} — a wire is routing "
        "through the interior of a structure it doesn't live inside (regression)"
    )


def test_tunnel_wires_attach_on_the_face_toward_their_other_endpoint():
    """A tunnel/border terminal attaches on its INNER face when the wire's
    other endpoint is inside the structure, OUTER face when outside — checked
    across every tunnel-touching wire in the VI, not one instance."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available or failed to load: {STACKED_SEQ_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("scene build failed (missing geometry)")
    mismatches, total = _count_tunnel_face_mismatches(scene)
    assert total > 0  # the sample really does exercise tunnels
    assert mismatches == 0


def _count_contained_wire_escapes(graph, vi: str, scene: Scene) -> int:
    """A wire fully CONTAINED by a structure (per the graph's
    ``_innermost_common_container`` — the same relation the router confines
    on) must have no segment outside that container's body. Maps each routed
    branch back to its wire (by dest-center) to look up the true container —
    geometry alone can't tell an outer tunnel sitting on the border (endpoint
    inside the bbox but living OUTSIDE the frame) from real containment."""
    from lvkit.render.scene import (
        _innermost_common_container,
        _strip_prefix,
        _wire_path,
    )

    src = graph.get_vi_source_path(vi)
    layout = build_layout(src)
    by_id = {n.id: n for n in graph.iter_nodes(vi)}
    body = {rs.raw_uid: rs.bounds for rs in scene.structures}

    paired: set = set()
    for node in graph.iter_nodes(vi):
        for t in node.terminals:
            pid = getattr(t, "paired_id", None)
            if pid:
                paired.add(frozenset((t.id, pid)))
    by_group: dict = {}
    for w in graph.get_wires(vi, include_internal=True):
        if frozenset((w.source.terminal_id, w.dest.terminal_id)) in paired:
            continue
        path = _wire_path(w, graph, by_id, vi)
        by_group.setdefault((w.source.terminal_id, path), []).append(w)

    escapes = 0
    for net in scene.wire_nets:
        if net.source is None:
            continue
        group = by_group.get((net.source.source.terminal_id, net.frame_path), [])
        for branch in net.branches:
            dst = branch[-1]
            match = None
            for w in group:
                c = layout.terminal_centers.get(_strip_prefix(w.dest.terminal_id, vi))
                if c and abs(c[0] - dst[0]) < 2 and abs(c[1] - dst[1]) < 2:
                    match = w
                    break
            if match is None:
                continue
            uid = _innermost_common_container(match, graph, by_id, vi)
            rect = body.get(uid) if uid else None
            if rect is None:
                continue
            if any(_dist_outside(pt, rect) > 1.5 for pt in branch):
                escapes += 1
    return escapes


def _dist_outside(pt: Point, rect: Rect) -> float:
    x, y = pt
    x1, y1, x2, y2 = rect
    dx = max(x1 - x, 0.0, x - x2)
    dy = max(y1 - y, 0.0, y - y2)
    return max(dx, dy)


def test_contained_wires_stay_inside_their_structure():
    """A fully-contained wire (both endpoints inside a structure) is confined to
    that structure's interior — it never routes out of the frame and back to
    dodge an interior obstacle (regression: the increment wire inside a For
    Loop leaving the loop around a tall string constant)."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available or failed to load: {STACKED_SEQ_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("scene build failed (missing geometry)")
    assert _count_contained_wire_escapes(graph, vi, scene) == 0


def test_string_const_display_strips_quotes_and_unescapes():
    # Parser stores '...' with \\ and \' escaped; display shows the bare text.
    assert string_const_display("'hello'") == "hello"
    assert string_const_display("'it\\'s'") == "it's"
    assert string_const_display("'a\\\\b'") == "a\\b"
    assert string_const_display("'line1\nline2'") == "line1\nline2"  # newlines kept
    assert string_const_display("novalue") == "novalue"  # non-quoted passthrough
    assert string_const_display(None) == ""


def test_format_numeric_const_hex_octal_binary_decimal():
    # LabVIEW prefixes a non-decimal numeric constant with a lowercase
    # letter (x/o/b), uppercase hex digits, no zero-padding at precision 0 —
    # verified against the "Current VIs Reference.vi" corpus VI's own label
    # ("0x02 => ...") documenting its own constant's value (task #59).
    u8 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt8")
    assert _format_numeric_const(u8, "31", "%.0x") == "x1F"
    u16 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt16")
    assert _format_numeric_const(u16, "237", "%.0b") == "b11101101"
    i32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    assert _format_numeric_const(i32, "2", "%.0x") == "x2"
    # No format string (LabVIEW default decimal) -> caller falls back.
    assert _format_numeric_const(i32, "31", None) is None


def test_format_numeric_const_negative_twos_complement_by_bit_width():
    # A negative value hex-displayed shows the type's own two's-complement
    # bit pattern (I16 -1 -> xFFFF), not a Python-style "-x1".
    i16 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt16")
    assert _format_numeric_const(i16, "-1", "%.0x") == "xFFFF"
    i32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    assert _format_numeric_const(i32, "-1", "%.0x") == "xFFFFFFFF"
    # Unknown/unresolved type: can't determine the bit width to
    # two's-complement against — don't guess, fall back instead.
    assert _format_numeric_const(None, "-1", "%.0x") is None


def test_format_numeric_const_float_precision():
    dbl = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
    assert _format_numeric_const(dbl, "1.9375", "%.2f") == "1.94"
    assert _format_numeric_const(dbl, "3.0", "%.1f") == "3.0"
    assert _format_numeric_const(dbl, "31", "%.0f") == "31"


def test_format_numeric_const_unrecognized_format_falls_back():
    # LabVIEW's timestamp format ('%<...>T') and other specs this function
    # doesn't understand return None rather than a guessed rendering.
    i32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    assert _format_numeric_const(i32, "5", "%<%.3X\n%x>T") is None
    assert _format_numeric_const(i32, "5", "%#_13g") is None
    assert _format_numeric_const(i32, "5", None) is None
    assert _format_numeric_const(i32, "5", "") is None


def test_string_constant_boxes_trimmed_top_left_anchored():
    """String-constant boxes shrink to their wrapped-text height, anchored at
    the heap top-left (x1/y1/width unchanged, only y2 moves up, never past the
    heap bottom), and the output terminal re-anchors to the shrunk box's right
    edge at its vertical middle."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available or failed to load: {STACKED_SEQ_VI}")
    graph, vi = loaded
    src = graph.get_vi_source_path(vi)
    if src is None:
        pytest.skip("no source path")
    layout = build_layout(src)
    trim_bounds, trim_centers = _trim_string_const_geom(graph, vi, layout)
    if not trim_bounds:
        # The corpus VI's exact content is version-pinned to whatever OpenG
        # publishes anonymously (SourceForge tops out at 4.x — see
        # docs/test-corpus-sources.md); this construct isn't guaranteed to
        # survive every version. Skip rather than fail when this specific
        # fixture doesn't happen to have multi-line string constants.
        pytest.skip("sample has no multi-line string constants in this version")
    for raw, (nx1, ny1, nx2, ny2) in trim_bounds.items():
        ox1, oy1, ox2, oy2 = layout.node_bounds[raw]
        assert (nx1, ny1, nx2) == (ox1, oy1, ox2)  # top-left + width unchanged
        assert oy1 < ny2 <= oy2  # bottom moved up, not past heap
        cx, cy = trim_centers[raw]
        assert cx == nx2  # terminal on the right edge
        assert ny1 <= cy <= ny2  # ... within the shrunk box
        assert abs(cy - (ny1 + ny2) / 2) < 0.51  # ... at its vertical middle


# --------------------------------------------------------------------------- #
# P1: wire color table + coercion key (pure unit tests, no VI needed)
# --------------------------------------------------------------------------- #


def test_wire_style_covers_full_type_table():
    dbl = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
    i32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    boolean = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    path = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
    cluster = LVType(kind=LVTypeKind.CLUSTER, fields=[])
    error_cluster = LVType(
        kind=LVTypeKind.CLUSTER, typedef_name="Error Cluster", fields=[]
    )

    assert wire_style(dbl).color == DEFAULT_THEME.wire_float
    assert wire_style(i32).color == DEFAULT_THEME.wire_int
    assert wire_style(boolean).color == DEFAULT_THEME.wire_bool
    assert wire_style(string).color == DEFAULT_THEME.wire_string
    assert wire_style(path).color == DEFAULT_THEME.wire_path
    assert wire_style(cluster).color == DEFAULT_THEME.wire_cluster
    assert wire_style(error_cluster).color == DEFAULT_THEME.wire_error
    # Distinct colors, not a table that collapsed onto one fallback.
    colors = {
        wire_style(t).color
        for t in (dbl, i32, boolean, string, path, cluster, error_cluster)
    }
    assert len(colors) == 7


def test_wire_style_array_width_scales_with_dimensions():
    scalar = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
    arr_1d = LVType(kind=LVTypeKind.ARRAY, dimensions=1, element_type=scalar)
    arr_2d = LVType(kind=LVTypeKind.ARRAY, dimensions=2, element_type=scalar)

    base = wire_style(scalar).width
    # Array wires are drawn markedly bolder than the scalar element, thicker
    # still per dimension.
    assert wire_style(arr_1d).width > base
    assert wire_style(arr_2d).width > wire_style(arr_1d).width
    # Color is inherited from the element type, not a fixed array color.
    assert wire_style(arr_1d).color == wire_style(scalar).color


def test_coercion_key_ignores_provenance_but_catches_type_mismatch():
    a = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64", description="foo"
    )
    b = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64", description="bar"
    )
    c = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")

    assert coercion_key(a) == coercion_key(b)
    assert coercion_key(a) != coercion_key(c)
    assert coercion_key(None) is None

    arr_a = LVType(kind=LVTypeKind.ARRAY, dimensions=1, element_type=a)
    arr_c = LVType(kind=LVTypeKind.ARRAY, dimensions=1, element_type=c)
    assert coercion_key(arr_a) != coercion_key(arr_c)


# --------------------------------------------------------------------------- #
# P1: front-panel control/indicator glyphs (pure unit tests via SvgBackend)
# --------------------------------------------------------------------------- #


def _render_fp_terminal(lv_type: LVType | None, is_indicator: bool = False) -> str:
    backend = SvgBackend()
    terminal = FPTerminal(
        id="vi::1",
        index=0,
        direction="input",
        name="X",
        lv_type=lv_type,
        is_indicator=is_indicator,
    )
    draw_fp_terminal(terminal, (0.0, 0.0, 40.0, 40.0), backend)
    return backend.render((0.0, 0.0, 40.0, 40.0))


def test_numeric_control_glyph_is_type_repr():
    # Real LabVIEW data-type terminals show the type name, e.g. DBL / I32.
    svg = _render_fp_terminal(
        LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
    )
    assert ">DBL<" in svg
    svg_int = _render_fp_terminal(
        LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    )
    assert ">I32<" in svg_int


def test_array_control_glyph_is_icon_view():
    # An array-of-DBL control terminal shows the ELEMENT type label (DBL,
    # not "[DBL]" — icon view has no bracket text) plus an index column
    # cell on the left, matching LabVIEW's icon-view chrome. The index
    # cell is now confined to the ground-truth-measured upper strip
    # (~7px tall for a single dimension), but the small index cell still
    # shows its letter — matching the ground truth's small grey index
    # block with a readable letter — so this checks for both the cell
    # itself (its fill color) and the rendered "i" glyph.
    arr = LVType(
        kind=LVTypeKind.ARRAY,
        dimensions=1,
        element_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
    )
    svg = _render_fp_terminal(arr)
    assert "[DBL]" not in svg
    assert ">DBL<" in svg
    assert DEFAULT_THEME.fp_index_fill in svg
    assert ">i<" in svg


def test_numeric_control_shows_value_sample():
    svg = _render_fp_terminal(
        LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
    )
    assert "1.23" in svg


def test_boolean_control_glyph_is_tf():
    svg = _render_fp_terminal(
        LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    )
    assert ">TF<" in svg


def test_string_control_glyph_present():
    svg = _render_fp_terminal(
        LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    )
    assert "abc" in svg


def test_control_and_indicator_share_border_and_differ_by_port_arrow():
    """A control and an indicator draw the SAME double thin (1.0) border — the
    old heavy-control / thin-indicator distinction is gone. DIRECTION is carried
    only by the solid wire-port arrow: a control's sits at its RIGHT edge, an
    indicator's at its LEFT, both pointing right (issue #85)."""
    import re

    ty = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
    control_svg = _render_fp_terminal(ty, is_indicator=False)
    indicator_svg = _render_fp_terminal(ty, is_indicator=True)

    for svg in (control_svg, indicator_svg):
        # No heavy control outline; the box is a DOUBLE thin border (outer +
        # inner, both 1.0) — identical for control and indicator.
        assert 'stroke-width="2.0"' not in svg
        assert svg.count('stroke-width="1.0"') >= 2

    def arrow_cx(svg: str) -> float:
        m = re.search(r'<polygon points="([^"]+)"', svg)
        assert m is not None, "no wire-port arrow polygon drawn"
        xs = [float(p.split(",")[0]) for p in m.group(1).split()]
        return sum(xs) / len(xs)

    # Same 40-wide box: the control's arrow is on the RIGHT half, the
    # indicator's on the LEFT — that alone tells them apart.
    assert arrow_cx(control_svg) > 20.0
    assert arrow_cx(indicator_svg) < 20.0


# --------------------------------------------------------------------------- #
# Structure border-terminal exit direction: vertical on top/bottom edges,
# horizontal on left/right edges (tunnels/shift-registers only — ordinary
# node terminals stay forced left->right dataflow).
# --------------------------------------------------------------------------- #


def test_border_terminal_on_top_edge_gets_vertical_exit_normal():
    structure_bounds = (0.0, 0.0, 200.0, 100.0)
    center = (100.0, 2.0)  # near the top edge
    normal = _exit_side(None, center, structure_bounds, border=True)
    assert normal == (0.0, -1.0)


def test_border_terminal_on_bottom_edge_gets_vertical_exit_normal():
    structure_bounds = (0.0, 0.0, 200.0, 100.0)
    center = (100.0, 98.0)  # near the bottom edge
    normal = _exit_side(None, center, structure_bounds, border=True)
    assert normal == (0.0, 1.0)


def test_border_terminal_on_left_edge_gets_horizontal_exit_normal():
    structure_bounds = (0.0, 0.0, 200.0, 100.0)
    center = (2.0, 50.0)  # near the left edge
    normal = _exit_side(None, center, structure_bounds, border=True)
    assert normal == (-1.0, 0.0)


def test_border_terminal_on_right_edge_gets_horizontal_exit_normal():
    structure_bounds = (0.0, 0.0, 200.0, 100.0)
    center = (198.0, 50.0)  # near the right edge
    normal = _exit_side(None, center, structure_bounds, border=True)
    assert normal == (1.0, 0.0)


def test_non_border_terminal_ignores_edge_and_uses_dataflow_direction():
    # An ordinary (non-border) terminal keeps forced left->right dataflow
    # even when it happens to sit closer to the top/bottom edge.
    structure_bounds = (0.0, 0.0, 200.0, 100.0)
    center = (100.0, 2.0)
    assert _exit_side("output", center, structure_bounds, border=False) == (1.0, 0.0)
    assert _exit_side("input", center, structure_bounds, border=False) == (-1.0, 0.0)


def test_border_terminal_attaches_inner_face_when_other_endpoint_inside():
    # Left-edge tunnel; the wire's other endpoint is INSIDE the structure ->
    # attach on the INNER face: the stub points inward (+x, into the frame),
    # so the wire never leaves the frame to come back in.
    structure_bounds = (0.0, 0.0, 200.0, 100.0)
    center = (2.0, 50.0)  # on the left edge
    inside_pt = (150.0, 50.0)
    normal = _exit_side(None, center, structure_bounds, border=True, toward=inside_pt)
    assert normal == (1.0, 0.0)


def test_border_terminal_attaches_outer_face_when_other_endpoint_outside():
    # Same left-edge tunnel; the other endpoint is OUTSIDE (to the left) ->
    # attach on the OUTER face: the stub points outward (-x).
    structure_bounds = (0.0, 0.0, 200.0, 100.0)
    center = (2.0, 50.0)
    outside_pt = (-40.0, 50.0)
    normal = _exit_side(None, center, structure_bounds, border=True, toward=outside_pt)
    assert normal == (-1.0, 0.0)


def test_border_terminal_top_edge_inner_face_points_down_into_frame():
    # Top-edge tunnel, other endpoint inside -> inner (downward) face.
    structure_bounds = (0.0, 0.0, 200.0, 100.0)
    center = (100.0, 2.0)
    inside_pt = (100.0, 60.0)
    normal = _exit_side(None, center, structure_bounds, border=True, toward=inside_pt)
    assert normal == (0.0, 1.0)
    outside_pt = (100.0, -30.0)
    normal_out = _exit_side(
        None,
        center,
        structure_bounds,
        border=True,
        toward=outside_pt,
    )
    assert normal_out == (0.0, -1.0)


# --------------------------------------------------------------------------- #
# P1: structure border-terminal glyphs (N/i/cond guaranteed by loop_type)
# --------------------------------------------------------------------------- #


def test_for_loop_guarantees_N_and_i_glyphs_from_layout_geometry():
    node = LoopNode(
        id="vi::43",
        vi_path="vi",
        node_type="forLoop",
        loop_type="forLoop",
        terminals=[],
    )
    layout = Layout(
        border_terminals={"i_uid": (0.0, 0.0, 10.0, 10.0)},
        border_terminal_kind={"i_uid": "i"},
        structure_border_uids={"43": ["i_uid"]},
    )
    borders = _structure_borders(node, layout, "vi")
    kinds = {b.glyph_kind for b in borders}
    assert "i" in kinds
    # N has no heap geometry in this synthetic layout -> defensively skipped,
    # not guessed.
    assert "N" not in kinds


def test_while_loop_guarantees_i_and_cond_glyphs():
    node = LoopNode(
        id="vi::7",
        vi_path="vi",
        node_type="whileLoop",
        loop_type="whileLoop",
        terminals=[],
    )
    layout = Layout(
        border_terminals={
            "i_uid": (0.0, 0.0, 10.0, 10.0),
            "cond_uid": (20.0, 0.0, 30.0, 10.0),
        },
        border_terminal_kind={"i_uid": "i", "cond_uid": "cond"},
        structure_border_uids={"7": ["i_uid", "cond_uid"]},
    )
    borders = _structure_borders(node, layout, "vi")
    kinds = {b.glyph_kind for b in borders}
    assert kinds == {"i", "cond"}
    assert all(b.terminal is None for b in borders)


def test_for_loop_border_glyphs_on_ground_truth_vi():
    graph, vi = _require_ground_truth()
    scene = build_scene(graph, vi)
    assert scene is not None
    loop = next(
        s
        for s in scene.structures
        if isinstance(s.node, StructureNode) and s.node.node_type == "forLoop"
    )
    kinds = {bt.glyph_kind for bt in loop.border_terminals}
    assert "N" in kinds
    assert "i" in kinds


# --------------------------------------------------------------------------- #
# P1: coercion dots on the ground-truth VI's own auto-index -> DBL path
# --------------------------------------------------------------------------- #


def test_coercion_is_numeric_representation_only():
    # A coercion dot marks a numeric-representation change (I32 -> DBL),
    # NOT a structural change (array -> element at an auto-index tunnel).
    from lvkit.render.style import numeric_repr

    i32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    dbl = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
    arr_dbl = LVType(kind=LVTypeKind.ARRAY, dimensions=1, element_type=dbl)
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")

    def coerces(a, b):
        ra, rb = numeric_repr(a), numeric_repr(b)
        return ra is not None and rb is not None and ra != rb

    assert coerces(i32, dbl)  # real coercion
    assert not coerces(dbl, dbl)  # same type
    assert not coerces(arr_dbl, dbl)  # auto-index (array->element) is NOT coercion
    assert not coerces(string, dbl)  # non-numeric, no dot


# --------------------------------------------------------------------------- #
# SubVI-without-icon: name wrapped inside the box + hover tooltip
# --------------------------------------------------------------------------- #


def test_wrap_label_greedy_and_hard_break():
    from lvkit.render.glyph import wrap_label

    b = SvgBackend()
    # multi-word wraps to several lines, none over the width
    lines = wrap_label("DAQmx Create Virtual Channel.vi", 34, b, 6.0, 4)
    assert 2 <= len(lines) <= 4
    assert all(b.measure_text(ln, 6.0) <= 34 for ln in lines)
    # a single word wider than the box is hard-broken, not left overflowing
    broken = wrap_label("Supercalifragilistic", 20, b, 7.0, 4)
    assert len(broken) >= 2
    assert all(b.measure_text(ln, 7.0) <= 20 for ln in broken if not ln.endswith("…"))


def test_wrapped_box_glyph_fits_full_name_by_shrinking():
    from lvkit.render.glyph import WrappedBoxGlyph

    b = SvgBackend()
    WrappedBoxGlyph("DAQmx Create Virtual Channel.vi").draw(
        b,
        (0.0, 0.0, 32.0, 32.0),
        DEFAULT_THEME,
    )
    svg = b.render((0.0, 0.0, 32.0, 32.0))
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert len([t for t in texts if t]) >= 3  # wrapped to multiple lines
    assert "…" not in "".join(texts)  # full name, no truncation
    joined = "".join(texts)
    for word in ("DAQmx", "Create", "Virtual", "Channel"):
        assert word in joined
    # every line stays inside the box vertically
    ys = [float(m) for m in re.findall(r'<text[^>]*y="([0-9.]+)"', svg)]
    assert ys and all(0.0 <= y <= 32.0 for y in ys)


def test_wrapped_box_glyph_ellipsizes_when_truly_impossible():
    from lvkit.render.glyph import WrappedBoxGlyph

    b = SvgBackend()
    WrappedBoxGlyph("x" * 200).draw(b, (0.0, 0.0, 18.0, 18.0), DEFAULT_THEME)
    assert "…" in b.render((0.0, 0.0, 18.0, 18.0))


def test_subvi_nodes_get_hover_tooltip_titles():
    """SubVI nodes carry a <title> (hover tooltip) with their full name —
    roadmap #12. Uses the flat-seq sample (five SubVI calls, incl. "Parse
    XML.vi" inside the flat sequence's frames)."""
    loaded = _load_graph(FLAT_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {FLAT_SEQ_VI}")
    graph, vi = loaded
    svg = render_vi(graph, vi)
    if svg is None:
        pytest.skip("sample lacks required diagram geometry")
    # the SubVI name appears as a tooltip even though it's wrapped in the box
    # (root <title> is the VI name; "<title>Parse XML" can only be a node)
    assert "<title>Parse XML" in svg
    # Every tooltip-carrying node group also carries the "lv-node" class +
    # "data-node" id (see render/__init__.py's injected hover script) that
    # reveals its connector-help panel — replaces the bare "<g>" this used
    # to be. (data-* attrs are sorted by key — see backend.begin_group — so a
    # node may carry up to three: "data-help" (task #50, the HTML viewers'
    # constant-size tooltip text), a resolvable SubVI's "data-lv-vi-rel"
    # (task #76), and "data-node" — in any sort order; match 1–3 rather than
    # assuming a count or order.)
    assert re.search(
        r'<g class="lv-node"(?:\s+data-[\w-]+="[^"]*"){1,3}>\s*<title>Parse XML',
        svg,
    )
    assert 'data-node="' in svg


def test_node_tooltip_includes_doc_url_for_resolved_primitive():
    """A primitive that resolves to an NI docs page surfaces that URL as the
    last line of its hover <title> (task #67). Add (1050) has a doc_url."""
    from lvkit.render.draw import _node_doc_url, _node_tooltip

    add = PrimitiveNode(id="a", vi_path="V", name="Add", prim_id=1050, terminals=[])
    url = _node_doc_url(add)
    assert url is not None and url.endswith("/functions/add.html")
    tip = _node_tooltip(add)
    assert tip is not None and url in tip.splitlines()


def test_node_tooltip_has_no_doc_url_when_unresolved():
    """An unknown primitive (no catalog page) adds no URL line."""
    from lvkit.render.draw import _node_doc_url

    unknown = PrimitiveNode(
        id="u", vi_path="V", name="mystery", prim_id=999999, terminals=[]
    )
    assert _node_doc_url(unknown) is None


def test_case_vi_svg_is_well_formed_xml_standalone():
    """A case/stacked-seq VI embeds an inline <script> whose JS contains '<'
    (e.g. `i < n`). Opened as a standalone .svg the browser parses strict XML,
    so the script body must be CDATA-guarded — assert the whole document
    parses as XML (regression for 'StartTag: invalid element name')."""
    import xml.etree.ElementTree as ET

    loaded = _load_graph(CASE_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {CASE_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None or not scene.frame_values:
        pytest.skip("sample has no interactive structures (no inline script)")
    svg = render_vi(graph, vi)
    assert svg is not None and "<script>" in svg
    ET.fromstring(svg)  # raises ParseError if the script broke XML well-formedness


LOCALVAR_VI = Path(
    ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/string/"
    "string.llb/Number to Proper Engl Text__ogtk.vi"
)


def test_gref_local_variable_wires_to_correct_control():
    """gRef (Local Variable) nodes must be POSITIONED graph nodes (their own
    icon, their own heap bounds) that wire to the control their paramIdx
    names — which indexes the VI's FULL front-panel control list, not the
    connector-pane slots. A local variable is NOT a passthrough alias to the
    referenced control's FP terminal: aliasing produced wires that appeared
    to originate at the (possibly cross-frame, hidden) FP terminal instead of
    the local variable's own diagram position.

    Regression: two case selectors fed by local variables of non-conpane
    boolean indicators were unwired (parser dropped gRef entirely), then
    later mis-wired to the wrong control (conpane-slot resolution) or to the
    wrong SOURCE terminal (FP-terminal aliasing instead of the gRef's own
    node)."""
    loaded = _load_graph(LOCALVAR_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {LOCALVAR_VI}")
    graph, vi = loaded
    by_id: dict[str, tuple[object, object]] = {}
    for n in graph.iter_nodes(vi):
        for t in n.terminals:
            by_id[t.id] = (n, t)
    vinode = graph.get_graph_node(vi)
    for t in vinode.terminals:
        by_id[t.id] = (vinode, t)

    def source(sel_raw: str) -> tuple[object, object] | tuple[None, None]:
        for w in graph.get_wires(vi, include_internal=True):
            if w.dest.terminal_id == f"{vi}::{sel_raw}":
                return by_id.get(w.source.terminal_id, (None, None))
        return None, None

    # These two selectors were unwired before gRef support; now they resolve
    # to a real LocalVariableNode (their OWN diagram position), which in turn
    # references the boolean indicators their local variables name (paramIdx
    # 3 / 1) — NOT the indicators' own FP terminal directly.
    for sel_raw, expected_control in (("284", "hasTensPlace"), ("834", "hasOnesPlace")):
        node, term = source(sel_raw)
        assert isinstance(node, LocalVariableNode), (
            f"selector {sel_raw} source is {type(node).__name__}, expected "
            f"LocalVariableNode (a positioned diagram node, not an FP-terminal "
            f"alias)"
        )
        assert node.control_name == expected_control
        assert term.name == expected_control


def test_scanf_nodes_parsed_and_wired():
    """Scan From String (class="scanf") is a variable-terminal operation like
    printf. It was missing from the parser allowlist, so it was never turned
    into a graph node and EVERY wire from a scanf output was silently dropped
    in graph construction (the sink terminal appeared unwired). Regression:
    four scanf->prim(1510) I32 connections vanished from this VI's diagram."""
    loaded = _load_graph(LOCALVAR_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {LOCALVAR_VI}")
    graph, vi = loaded
    scanfs = [n for n in graph.iter_nodes(vi) if n.node_type == "scanf"]
    assert len(scanfs) == 4, f"expected 4 scanf nodes, got {len(scanfs)}"

    wired = set()
    for w in graph.get_wires(vi, include_internal=True):
        wired.add(w.source.terminal_id)
        wired.add(w.dest.terminal_id)
    # The four I32 inputs previously left unwired by the dropped scanf wires.
    for raw in ("910", "1095", "1314", "1358"):
        assert f"{vi}::{raw}" in wired, f"terminal {raw} still unwired"


# --------------------------------------------------------------------------- #
# Clean-room ORIGINAL primitive glyphs (roadmap #14): Bundle/Unbundle +
# comparison triangles, resolved AHEAD of the pixel-matched icon assets.
# These are pure unit tests over synthetic graph nodes (no VI files needed),
# so they run even where the local-only sample corpus is absent.
# --------------------------------------------------------------------------- #


def _prim(node_type, name=None, dirs=(), roles=()):
    """A synthetic PrimitiveNode with one terminal per direction in ``dirs``.
    ``roles``, if given, is a parallel sequence of ``nmux_role`` values
    ("agg"/"list"/None) for a Bundle/Unbundle-family node."""
    from lvkit.models import Terminal

    terms = [
        Terminal(
            id=f"t{i}",
            index=i,
            direction=d,
            nmux_role=roles[i] if i < len(roles) else None,
        )
        for i, d in enumerate(dirs)
    ]
    return PrimitiveNode(
        id="n0",
        vi_path="V",
        name=name,
        node_type=node_type,
        terminals=terms,
    )


def _ctx():
    from lvkit.render.nodes import GlyphContext

    return GlyphContext(graph=InMemoryVIGraph(), vi_name="V")


def test_bundle_glyph_for_many_in_one_out_mux():
    """An nMux/mux/demux with N field (list) inputs and one aggregate output
    is a Bundle — field count comes from ``nmux_role=="list"`` terminals, not
    a raw input/output count (the aggregate terminal is never a field)."""
    from lvkit.render.glyph import BundleGlyph
    from lvkit.render.nodes import resolve_glyph

    node = _prim(
        "mux",
        "Bundle",
        dirs=("input", "input", "input", "output"),
        roles=("list", "list", "list", "agg"),
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, BundleGlyph)
    assert glyph.num_fields == 3


def test_unbundle_glyph_for_one_in_many_out_mux():
    """A 1-aggregate-input / N-field-output mux is an Unbundle (mirror of
    Bundle)."""
    from lvkit.render.glyph import UnbundleGlyph
    from lvkit.render.nodes import resolve_glyph

    node = _prim(
        "demux",
        "Unbundle",
        dirs=("input", "output", "output", "output", "output"),
        roles=("agg", "list", "list", "list", "list"),
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, UnbundleGlyph)
    assert glyph.num_fields == 4


def test_single_field_bundle_is_not_dropped():
    """A single-field Bundle/Unbundle must still draw with num_fields=1 — N=1
    is not special-cased into a fallback box (this was bug #1: a 1-field
    Unbundle used to fall through to a labeled 'Node Multiplexer' box)."""
    from lvkit.render.glyph import UnbundleGlyph
    from lvkit.render.nodes import resolve_glyph

    node = _prim(
        "demux",
        "Unbundle",
        dirs=("input", "output"),
        roles=("agg", "list"),
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, UnbundleGlyph)
    assert glyph.num_fields == 1


def test_reposition_mux_leaves_fields_and_lone_unbundle_aggregate_alone():
    """``_reposition_mux_terminals`` touches ONLY a Bundle's two shared-box
    aggregate terminals (splitting them into interior-input / edge-output
    halves). FIELD (``list``) terminals — whose wires are drawn from LabVIEW's
    own faithful geometry and covered by the opaque name cells — and a lone
    Unbundle aggregate, which already lines an edge on its own, are returned
    unchanged. Nothing here moves an on-diagram wire endpoint."""
    from lvkit.models import Terminal
    from lvkit.render.scene import RenderTerminal, _reposition_mux_terminals

    bounds = (100.0, 400.0, 200.0, 460.0)

    def rt(direction, role, box):
        return RenderTerminal(
            terminal=Terminal(id="t", index=0, direction=direction, nmux_role=role),
            center=((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
            bounds=box,
        )

    # A single Unbundle aggregate (input at the LEFT edge) + a field output:
    # only two aggs sharing a box trigger the split, so both are untouched.
    lone = rt("input", "agg", (100.0, 400.0, 116.0, 460.0))
    field = rt("output", "list", (116.0, 405.0, 200.0, 422.0))
    out = _reposition_mux_terminals([lone, field], bounds)
    assert out[0].bounds == lone.bounds  # lone aggregate not split
    assert out[1].bounds == field.bounds  # field terminal untouched
    assert out[1].center == field.center


def test_cluster_constant_draws_real_field_geometry_not_uniform_rows():
    """``ClusterConstantGlyph`` with real geometry (``cluster_geom``) draws
    each field at its OWN heap rect — real size, real position — instead of
    the equal-height-row fallback (issue #45 reopened: the box used to be
    SHRUNK to a synthetic ``n * row_height`` stack, disconnecting it from its
    real wire). ``cluster_geom``'s field rects are relative to the cluster's
    own (0, 0) origin at its NATIVE (``width``/``height``) size — here that
    size exactly matches the drawn box, so the fit-scale is 1.0 (pure
    translation); ``test_nested_cluster_field_draws_as_real_box_in_box``
    covers the scale != 1.0 case."""
    from lvkit.parser.layout import ClusterFieldGeom, ClusterGeom
    from lvkit.render.glyph import ClusterConstantGlyph
    from lvkit.render.style import DEFAULT_THEME

    class _Dot:
        def draw(self, backend, bounds, theme):  # noqa: ANN001
            x1, y1, x2, y2 = bounds
            backend.text((x1 + x2) / 2, (y1 + y2) / 2, "V", 7.0)

    fields = (("Alpha", _Dot()), ("Beta", _Dot()))
    box = (0.0, 0.0, 100.0, 200.0)  # a REAL, uncompacted heap box
    cg = ClusterGeom(
        width=100.0,
        height=200.0,
        fields=(
            ClusterFieldGeom(
                "Alpha",
                value_rect=(5.0, 10.0, 90.0, 40.0),
                label_rect=(5.0, 0.0, 40.0, 9.0),
            ),
            # A hidden caption (label_rect None) draws NO label text.
            ClusterFieldGeom(
                "Beta", value_rect=(5.0, 120.0, 90.0, 190.0), label_rect=None
            ),
        ),
    )
    glyph = ClusterConstantGlyph(
        fields=fields, cluster_geom=cg, border_color="#ff00ff"
    )

    backend = SvgBackend()
    glyph.draw(backend, box, DEFAULT_THEME)
    svg = backend.render(box)
    assert "Alpha" in svg  # visible caption drawn
    assert "Beta" not in svg  # hidden caption NOT drawn
    assert svg.count(">V<") == 2  # both field values drawn, at their own rects
    # A field NAME label is a name, not a value — it draws in the normal
    # label text color, never the cluster's own wire/border color (issue #45
    # extension: labels used to draw in ``border``, reading as cluster pink).
    label_fill = re.search(r'fill="([^"]+)"[^>]*>Alpha<', svg)
    assert label_fill is not None
    assert label_fill.group(1) == DEFAULT_THEME.text
    assert label_fill.group(1) != "#ff00ff"

    # Missing geometry for ANY field falls back to the equal-height rows
    # (never a partial mix of real + guessed positions).
    partial = ClusterConstantGlyph(
        fields=fields,
        cluster_geom=ClusterGeom(width=100.0, height=200.0, fields=(cg.fields[0],)),
    )
    fb_backend = SvgBackend()
    partial.draw(fb_backend, box, DEFAULT_THEME)
    fb_svg = fb_backend.render(box)
    assert "Alpha" in fb_svg and "Beta" in fb_svg  # fallback still shows both


def test_nested_cluster_field_draws_as_real_box_in_box():
    """A cluster field that is ITSELF a ``ClusterConstantGlyph`` (a nested
    cluster field, composed recursively by ``nodes._cluster_value_glyph``)
    draws its OWN real box-in-box geometry, fit into whatever rect the
    PARENT maps it to — a real scale != 1.0 case (the nested cluster's own
    native size differs from the parent-assigned rect, e.g. a NESTED field
    inside a cluster whose own extent-normalize scale isn't 1.0). Regression
    for the pre-existing gap: composing a nested-cluster FIELD used to fall
    through to a flattened text glyph instead of a real nested box."""
    from lvkit.parser.layout import ClusterFieldGeom, ClusterGeom
    from lvkit.render.glyph import ClusterConstantGlyph
    from lvkit.render.style import DEFAULT_THEME

    class _Dot:
        def draw(self, backend, bounds, theme):  # noqa: ANN001
            x1, y1, x2, y2 = bounds
            backend.text((x1 + x2) / 2, (y1 + y2) / 2, "V", 7.0)

    # The nested cluster's OWN native size (20x40) is NOT the same as the
    # rect the parent will map it to (10x20 below) — a real 0.5x fit-scale.
    nested_geom = ClusterGeom(
        width=20.0,
        height=40.0,
        fields=(
            ClusterFieldGeom("x", value_rect=(0.0, 0.0, 20.0, 20.0), label_rect=None),
            ClusterFieldGeom("y", value_rect=(0.0, 20.0, 20.0, 40.0), label_rect=None),
        ),
    )
    nested_glyph = ClusterConstantGlyph(
        fields=(("x", _Dot()), ("y", _Dot())), cluster_geom=nested_geom
    )

    outer_box = (0.0, 0.0, 100.0, 20.0)
    outer_geom = ClusterGeom(
        width=100.0,
        height=20.0,
        fields=(
            ClusterFieldGeom(
                "Inner",
                value_rect=(50.0, 0.0, 60.0, 20.0),  # -> abs (50,0,60,20): 10x20
                label_rect=None,
                nested=nested_geom,
            ),
        ),
    )
    outer_glyph = ClusterConstantGlyph(
        fields=(("Inner", nested_glyph),), cluster_geom=outer_geom
    )

    backend = SvgBackend()
    outer_glyph.draw(backend, outer_box, DEFAULT_THEME)
    svg = backend.render(outer_box)
    # Both sub-field values of the NESTED cluster actually drew (real
    # box-in-box, not flattened text) — a rect for the nested border plus
    # two "V" texts, all inside the outer box.
    assert svg.count(">V<") == 2
    xs = [float(x) for x in re.findall(r'<rect[^>]*\sx="([-\d.]+)"', svg)]
    widths = [float(w) for w in re.findall(r'<rect[^>]*\swidth="([-\d.]+)"', svg)]
    assert xs and widths
    # Every drawn rect (outer box + nested border) stays within [50, 60] on x
    # for anything belonging to the nested cluster's OWN box — spot-check the
    # narrowest rect (the nested cluster's own border) is <= 10px wide (its
    # fitted width) and sits at x >= 50 (inside the parent's assigned slot).
    nested_rects = [(x, w) for x, w in zip(xs, widths) if w <= 10.0 + 1e-6]
    assert nested_rects
    assert all(x >= 50.0 - 1e-6 for x, _ in nested_rects)


def test_refnum_glyph_draws_dogear_kind_symbol_and_compact_terminal():
    """``RefnumGlyph`` draws a dog-ear frame (a folded-corner box — LabVIEW's
    own visual grammar for "this is a reference"), a kind symbol (from
    ``ref_type`` — a distinct clean-room mark per known kind, generic
    fallback otherwise), and — for a COMPACT refnum — a small
    ``TypeTerminalGlyph`` badge in the corner holding the payload's type
    mnemonic."""
    from lvkit.render.glyph import RefnumGlyph, TypeTerminalGlyph
    from lvkit.render.style import DEFAULT_THEME

    bounds = (0.0, 0.0, 76.0, 48.0)

    scalar = RefnumGlyph(
        kind="Queue",
        border_color="#007f7f",
        terminal=TypeTerminalGlyph("abc", "#e05fa0"),
    )
    b1 = SvgBackend()
    scalar.draw(b1, bounds, DEFAULT_THEME)
    svg1 = b1.render(bounds)
    assert "<polygon" in svg1  # the dog-ear frame (corner-cut outline)
    assert ">abc<" in svg1  # the compact terminal badge

    # A cluster payload has no single-token mnemonic — an empty-text badge
    # still draws its DASHED-pink chrome box, just no text. The border is
    # the FIXED terminal chrome color (verified against 57/58/59 — both a
    # string and a class payload draw the SAME dashed pink box), never the
    # payload's own wire color.
    cluster = RefnumGlyph(
        kind="UserEvent",
        border_color="#007f7f",
        terminal=TypeTerminalGlyph("", "#a88d1e"),
    )
    b2 = SvgBackend()
    cluster.draw(b2, bounds, DEFAULT_THEME)
    svg2 = b2.render(bounds)
    assert f'stroke="{DEFAULT_THEME.refnum_terminal_border}"' in svg2
    assert 'stroke-dasharray="2,1.5"' in svg2

    # No registered payload at all: the frame + kind symbol draw, no
    # terminal badge.
    plain = RefnumGlyph(kind=None, border_color="#007f7f", terminal=None)
    b3 = SvgBackend()
    plain.draw(b3, bounds, DEFAULT_THEME)
    svg3 = b3.render(bounds)
    assert "<polygon" in svg3  # dog-ear still draws
    # No terminal badge rect beyond the frame + kind-symbol shapes — no text
    # at all (no mnemonic, no field content).
    assert "<text" not in svg3


def test_refnum_glyph_expanded_terminal_positions_at_real_heap_offset():
    """An EXPANDED refnum's ``terminal_rect`` (0..1 FRACTIONS of the
    refnum's own box — ``layout.RefnumPayload.offset``) places the terminal
    content at the heap's own recorded sub-rect, not a centered/guessed
    position — and the content itself (a ``DimmedGlyph``-wrapped real
    ``ClusterConstantGlyph``) draws its real elements, never flattened
    "name: type" text."""
    from lvkit.render.glyph import ClusterConstantGlyph, DimmedGlyph, RefnumGlyph
    from lvkit.render.style import DEFAULT_THEME

    class _Dot:
        def draw(self, backend, bounds, theme):  # noqa: ANN001
            x1, y1, x2, y2 = bounds
            backend.text((x1 + x2) / 2, (y1 + y2) / 2, "V", 7.0)

    inner = ClusterConstantGlyph(
        fields=(("a", _Dot()),), border_color="#007f7f",
    )
    bounds = (0.0, 0.0, 100.0, 200.0)
    # The payload sits at a real heap sub-offset — e.g. left-inset, not
    # filling the whole refnum box (matching GTR's real "ResultChangedRef":
    # offset x∈[0.28, 0.95], y∈[0.02, 0.98] of its own native box).
    terminal_rect = (0.28, 0.02, 0.95, 0.98)
    glyph = RefnumGlyph(
        kind="UserEvent",
        border_color="#007f7f",
        terminal=DimmedGlyph(inner),
        terminal_rect=terminal_rect,
    )
    backend = SvgBackend()
    glyph.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert ">V<" in svg  # the payload's real element glyph drew
    assert "lv-disabled-mask" in svg  # dimmed, never full-strength

    fx1, fy1, fx2, fy2 = terminal_rect
    expect_x1, expect_y1 = bounds[0] + fx1 * 100.0, bounds[1] + fy1 * 200.0
    expect_x2, expect_y2 = bounds[0] + fx2 * 100.0, bounds[1] + fy2 * 200.0
    # The DimmedGlyph's own mask rect sits at exactly the mapped terminal
    # rect — the real heap-derived placement, not a centered guess.
    mask_rect = re.search(
        r'<g class="lv-disabled-mask">\s*<rect x="([-\d.]+)" y="([-\d.]+)" '
        r'width="([-\d.]+)" height="([-\d.]+)"',
        svg,
    )
    assert mask_rect is not None
    mx1, my1, mw, mh = (float(v) for v in mask_rect.groups())
    assert abs(mx1 - expect_x1) < 0.5
    assert abs(my1 - expect_y1) < 0.5
    assert abs((mx1 + mw) - expect_x2) < 0.5
    assert abs((my1 + mh) - expect_y2) < 0.5


def test_class_glyph_shrinks_to_fit_instead_of_ellipsis_clipping():
    """A class name that doesn't fit its field's real box at the default
    size NEVER hard-clips to a mid-word "…" — it shrinks (and, if still too
    wide, wraps to a second line) instead, like ``ConstantGlyph``'s own
    ``fit=True`` mode. Regression for the real GTR SMUI-cluster case: a
    48x48 class field (the real heap box size for e.g. "test"/"TestResult")
    used to hard-clip "LabVIEW Object" to "LabVIEW O…"."""
    from lvkit.render.glyph import ClassGlyph
    from lvkit.render.style import DEFAULT_THEME

    bounds = (0.0, 0.0, 48.0, 48.0)  # the real GTR heap box size
    backend = SvgBackend()
    glyph = ClassGlyph("LabVIEW Object", "#555555")
    glyph.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert "…" not in svg
    assert "LabVIEW Object" in svg  # the FULL name, not a truncated prefix
    # The box itself is UNCHANGED — never re-widened/inflated to fit text.
    assert 'width="46.8" height="46.8"' in svg

    # A name too long to fit even shrunk to one line wraps to a second
    # line rather than clipping.
    backend2 = SvgBackend()
    long_glyph = ClassGlyph("SomeVeryLongClassNameIndeed", "#555555")
    long_glyph.draw(backend2, bounds, DEFAULT_THEME)
    svg2 = backend2.render(bounds)
    assert svg2.count("<text") == 2  # wrapped onto 2 lines
    assert "…" not in svg2  # still no ellipsis -- wrapping preferred over clipping


def test_class_glyph_draws_cube_and_short_name():
    """``ClassGlyph`` draws a cube motif (LabVIEW draws a class as a cube)
    plus the class's own short name — never bare text."""
    from lvkit.render.glyph import ClassGlyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = ClassGlyph("TestResult.lvclass", "#555555")
    bounds = (0.0, 0.0, 60.0, 60.0)
    backend = SvgBackend()
    glyph.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert svg.count("<polygon") >= 3  # the 3 cube faces
    assert 'stroke="#555555"' in svg
    assert "TestResult" in svg  # name drawn (possibly ellipsized)


def test_class_glyph_mini_form_below_size_threshold():
    """A ``ClassGlyph`` drawn into a SMALL box (below the legibility floor
    for a border + cube + name) uses a distinct MINI form — just the cube,
    scaled up, no border and no name — never the full-size glyph shrunk
    (LabVIEW draws a genuinely different compact representation at small
    sizes, not the same control squashed)."""
    from lvkit.render.glyph import ClassGlyph
    from lvkit.render.style import DEFAULT_THEME

    small_bounds = (0.0, 0.0, 20.0, 20.0)
    backend = SvgBackend()
    ClassGlyph("Foo.lvclass", "#555555").draw(backend, small_bounds, DEFAULT_THEME)
    svg = backend.render(small_bounds)
    assert svg.count("<polygon") >= 3  # the cube still draws
    assert "<rect" not in svg  # no border chrome at mini size
    assert "Foo" not in svg  # no name text at mini size

    large_bounds = (0.0, 0.0, 60.0, 60.0)
    backend2 = SvgBackend()
    ClassGlyph("Foo.lvclass", "#555555").draw(backend2, large_bounds, DEFAULT_THEME)
    svg2 = backend2.render(large_bounds)
    assert "<rect" in svg2  # full form: border chrome present
    assert "Foo" in svg2  # full form: name present


def test_refnum_glyph_mini_form_below_size_threshold():
    """A ``RefnumGlyph`` drawn into a SMALL box (e.g. GTR's real "menubar"
    field, 21x27) uses a distinct MINI form — a plain bordered box holding
    just the kind symbol, no dog-ear cut and no terminal — never the full
    dog-ear+terminal glyph shrunk to fit."""
    from lvkit.render.glyph import RefnumGlyph, TypeTerminalGlyph
    from lvkit.render.style import DEFAULT_THEME

    small_bounds = (0.0, 0.0, 21.0, 27.0)  # GTR's real "menubar" field size
    backend = SvgBackend()
    RefnumGlyph(
        kind="Menu", border_color="#007f7f", terminal=TypeTerminalGlyph("", "#e05fa0")
    ).draw(backend, small_bounds, DEFAULT_THEME)
    svg = backend.render(small_bounds)
    assert "<polygon" not in svg  # no dog-ear cut-corner outline at mini size
    assert "<rect" in svg  # the plain mini-form border still draws

    large_bounds = (0.0, 0.0, 76.0, 48.0)  # GTR's real "TextStream" field size
    backend2 = SvgBackend()
    RefnumGlyph(
        kind="Queue",
        border_color="#007f7f",
        terminal=TypeTerminalGlyph("abc", "#e05fa0"),
    ).draw(backend2, large_bounds, DEFAULT_THEME)
    svg2 = backend2.render(large_bounds)
    assert "<polygon" in svg2  # the dog-ear cut-corner outline draws
    assert ">abc<" in svg2  # the full terminal badge draws


def test_dimmed_glyph_draws_inner_then_overlay_mask():
    """``DimmedGlyph`` draws ``inner``'s REAL content first, then the
    established ``lv-disabled-mask`` translucent wash over it — never a
    substitute for the real shape."""
    from lvkit.render.glyph import DimmedGlyph
    from lvkit.render.style import DEFAULT_THEME

    class _Marked:
        def draw(self, backend, bounds, theme):  # noqa: ANN001
            backend.rect(*bounds, fill="none", stroke="#4a9c3e", stroke_width=1.0)

    bounds = (0.0, 0.0, 40.0, 20.0)
    backend = SvgBackend()
    DimmedGlyph(_Marked()).draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert 'stroke="#4a9c3e"' in svg  # the real inner shape drew
    assert "lv-disabled-mask" in svg  # the overlay wash drew too


def test_path_glyph_draws_folder_mark_even_when_empty():
    """``PathGlyph`` always draws a folder-mark outline — a path control is
    visually identifiable even with no path text set, never a featureless
    colored rectangle (acceptance-gate rule 4: no blank boxes)."""
    from lvkit.render.glyph import PathGlyph
    from lvkit.render.style import DEFAULT_THEME

    empty = PathGlyph("", "#1f8a8a")
    backend = SvgBackend()
    empty.draw(backend, (0.0, 0.0, 21.0, 19.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 21.0, 19.0))
    assert svg.count("<polygon") == 1  # the folder outline, even unset
    assert "<text" not in svg  # no path text to show

    full = PathGlyph("C:\\data\\log.txt", "#1f8a8a")
    b2 = SvgBackend()
    full.draw(b2, (0.0, 0.0, 120.0, 30.0), DEFAULT_THEME)
    svg2 = b2.render((0.0, 0.0, 120.0, 30.0))
    assert "<polygon" in svg2  # folder mark
    assert "log.txt" in svg2  # and the real path text


def test_empty_string_constant_is_empty_not_placeholder():
    """A genuinely empty/unset string constant is EMPTY (an empty string
    control), never an "abc" watermark — the maintainer: "empty string
    default isn't a watermark, it's just empty"."""
    from lvkit.render.glyph import ConstantGlyph
    from lvkit.render.nodes import _leaf_const_glyph

    string_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    glyph = _leaf_const_glyph(string_type, raw=None)
    assert isinstance(glyph, ConstantGlyph)
    assert glyph.value == ""

    # A SET string still shows its real value.
    real = _leaf_const_glyph(string_type, raw="'hello'")
    assert isinstance(real, ConstantGlyph)
    assert real.value == "hello"


def test_timestamp_constant_shows_two_line_time_date_not_bare_float():
    """A Timestamp constant (heap ddo class ``absTime`` — the graph records
    it as ``underlying_type="MeasureData"``, ``measure_flavor="TimeStamp"``,
    verified against GTR's real "StartTestTime" field) used to draw a bare
    ``"0.0"`` for an unset field — reads as a number, not a Timestamp
    control. It now shows LabVIEW's own two-line time-over-date display
    (task #66): a 12-hour time-with-milliseconds line over a date line,
    MULTILINE (never fit/centered — the exact box-width-independent split
    matters more than centering; see ``_format_timestamp_lines``). Verified
    against the maintainer's reference image #73: the unset/epoch default is
    exactly ``"12:00:00.000 AM"`` / ``"1/1/1904"``."""
    from lvkit.render.glyph import ConstantGlyph
    from lvkit.render.nodes import _leaf_const_glyph

    ts_type = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="MeasureData",
        measure_flavor="TimeStamp",
    )
    unset = _leaf_const_glyph(ts_type, raw=None)
    assert isinstance(unset, ConstantGlyph)
    assert unset.multiline is True
    assert unset.value == "12:00:00.000 AM\n1/1/1904"
    assert unset.color == wire_style(ts_type).color == "#893000"

    # A genuinely SET value (parser.vi's default-data decoder emits
    # "Timestamp(<secs>)") formats the same two-line way, not just the
    # epoch — e.g. 90061 seconds past the LabVIEW epoch is 1904-01-02
    # 01:01:01.
    real = _leaf_const_glyph(ts_type, raw="Timestamp(90061)")
    assert isinstance(real, ConstantGlyph)
    assert real.value == "01:01:01.000 AM\n1/2/1904"

    # A DIFFERENT MeasureData flavor (a waveform, not a timestamp) is NOT
    # touched by this branch — out of this fix's scope — and keeps the
    # generic grey "unknown" wire, not the timestamp color.
    waveform_type = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="MeasureData",
        measure_flavor="Float64Waveform",
    )
    wf = _leaf_const_glyph(waveform_type, raw=None)
    assert isinstance(wf, ConstantGlyph)
    assert "AM" not in wf.value and "PM" not in wf.value
    assert wire_style(waveform_type).color != "#893000"


def test_timestamp_type_family_and_terminal_mark():
    """``MeasureData``/``TimeStamp`` gets its OWN ``type_family`` bucket
    ("timestamp") and ``wire_style`` color (task #66) instead of falling to
    the generic grey "unknown" — the color is heap-recorded (the ``absTime``
    control's own ``cosm`` border ``fgColor`` ``00893000``, consistent
    across two independent corpus instances) and confirmed by the
    maintainer's reference images #73/#75 (both the constant box border and
    a real timestamp wire draw this same dark red-brown). ``type_repr``
    gives it a clean-room "TS" mnemonic (no NI-doc source for a canonical
    short form) — keyed on ``measure_flavor``, so a DIFFERENT MeasureData
    flavor (waveform) is untouched, never mislabeled "TS"."""
    from lvkit.render.style import type_family, type_repr

    ts_type = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="MeasureData",
        measure_flavor="TimeStamp",
    )
    assert type_family(ts_type) == "timestamp"
    assert type_repr(ts_type) == "TS"
    assert wire_style(ts_type).color == "#893000"

    waveform_type = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="MeasureData",
        measure_flavor="Float64Waveform",
    )
    assert type_family(waveform_type) == "unknown"
    assert type_repr(waveform_type) == ""


def test_refnum_glyph_mini_form_for_small_boxes():
    """A refnum drawn in a small box (below ``_MINI_MAX_W``/``_MINI_MAX_H`` —
    e.g. GTR's real "menubar" field, 21x27) uses a DISTINCT MINI form (a
    plain bordered box holding just the kind symbol, scaled up) — never the
    full dog-ear+terminal glyph shrunk (acceptance-gate rule 4: compact
    form, not the same glyph stretched/shrunk)."""
    from lvkit.render.glyph import RefnumGlyph, TypeTerminalGlyph
    from lvkit.render.style import DEFAULT_THEME

    small = RefnumGlyph(
        kind="Menu",
        border_color="#007f7f",
        terminal=TypeTerminalGlyph("abc", "#e05fa0"),
    )
    bounds = (0.0, 0.0, 21.0, 27.0)
    backend = SvgBackend()
    small.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert "<polygon" not in svg  # no dog-ear cut, no kind-symbol polygon
    assert "<text" not in svg  # no terminal badge text at this size

    # The SAME glyph at a large box draws the full dog-ear form instead.
    full_bounds = (0.0, 0.0, 76.0, 48.0)
    b2 = SvgBackend()
    small.draw(b2, full_bounds, DEFAULT_THEME)
    svg2 = b2.render(full_bounds)
    assert "<polygon" in svg2  # the dog-ear frame
    assert ">abc<" in svg2  # the compact terminal badge


def test_class_glyph_mini_form_for_small_boxes():
    """A class field drawn in a small box uses the MINI form (just the cube,
    no border rect, no name text) instead of the full form shrunk."""
    from lvkit.render.glyph import ClassGlyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = ClassGlyph("TestResult.lvclass", "#555555")
    small_bounds = (0.0, 0.0, 20.0, 20.0)
    backend = SvgBackend()
    glyph.draw(backend, small_bounds, DEFAULT_THEME)
    svg = backend.render(small_bounds)
    assert "<text" not in svg  # no name at this size
    assert svg.count("<polygon") >= 3  # just the cube faces, no border rect
    assert "<rect" not in svg


def test_array_typed_cluster_field_draws_real_elements_not_blank_box():
    """A cluster FIELD whose OWN type is an ARRAY used to fall through to
    the generic leaf glyph (no array case there) and draw a blank box —
    ``_cluster_value_glyph`` now composes it as a real ``ArrayConstantGlyph``
    (index control + a real default element, e.g. a folder mark for an
    array of paths), never a featureless rectangle."""
    from lvkit.models import ClusterField
    from lvkit.render.glyph import ArrayConstantGlyph, PathGlyph
    from lvkit.render.nodes import _cluster_value_glyph

    path_array_type = LVType(
        kind=LVTypeKind.ARRAY,
        element_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path"),
        dimensions=1,
    )
    outer_type = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="Paths", type=path_array_type)],
    )
    glyph = _cluster_value_glyph(outer_type, None, False, None)
    (name, field_glyph), = glyph.fields
    assert name == "Paths"
    assert isinstance(field_glyph, ArrayConstantGlyph)
    assert field_glyph.elements == ()  # unset field -> genuinely empty array
    assert isinstance(field_glyph.default_element, PathGlyph)


def test_refnum_field_with_cluster_element_type_draws_compact_badge():
    """A cluster FIELD whose own type is NOT a cluster (a ``refnum``, e.g. a
    User Event) but whose ``element_type`` IS one — the registered
    event-data type LabVIEW shows for a data-typed refnum — draws a
    ``RefnumGlyph``, NEVER a nested ``ClusterConstantGlyph`` expanding the
    payload's fields (issue #45's refnum trigger). With NO heap geometry to
    consult (``cluster_geom=None`` — no ``ClusterFieldGeom.refnum_expanded``
    bit available), the field is never treated as expanded, so its terminal
    is always the COMPACT ``TypeTerminalGlyph`` badge. The cluster payload
    has no single-token mnemonic, so the badge shows no text, only the
    payload's own wire color."""
    from lvkit.models import ClusterField
    from lvkit.render.glyph import (
        ClusterConstantGlyph,
        RefnumGlyph,
        TypeTerminalGlyph,
    )
    from lvkit.render.nodes import _cluster_value_glyph
    from lvkit.render.style import wire_style

    inner_cluster_type = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="status", type=LVType(kind=LVTypeKind.PRIMITIVE))],
    )
    refnum_type = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UserEvent",
        element_type=inner_cluster_type,
    )
    outer_type = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="EventRef", type=refnum_type)],
    )

    glyph = _cluster_value_glyph(outer_type, None, False, None)
    assert isinstance(glyph, ClusterConstantGlyph)
    (name, field_glyph), = glyph.fields
    assert name == "EventRef"
    assert isinstance(field_glyph, RefnumGlyph)
    assert field_glyph.kind == "UserEvent"
    assert not isinstance(field_glyph, ClusterConstantGlyph)
    assert isinstance(field_glyph.terminal, TypeTerminalGlyph)
    assert field_glyph.terminal.text == ""
    assert field_glyph.terminal.color == wire_style(inner_cluster_type).color

    # A registered SCALAR payload (e.g. a string) shows its real mnemonic.
    string_refnum_type = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="Queue",
        element_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
    )
    scalar_outer = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="Q", type=string_refnum_type)],
    )
    glyph2 = _cluster_value_glyph(scalar_outer, None, False, None)
    assert isinstance(glyph2, ClusterConstantGlyph)
    (name2, field_glyph2), = glyph2.fields
    assert name2 == "Q"
    assert isinstance(field_glyph2, RefnumGlyph)
    assert isinstance(field_glyph2.terminal, TypeTerminalGlyph)
    assert field_glyph2.terminal.text == "abc"

    # An ordinary refnum with NO registered payload draws the frame + kind
    # symbol with no terminal at all — never reinterpreted as a cluster or
    # wrapped with an empty badge.
    plain_refnum_type = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="LVObjCtl"
    )
    plain_outer = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="Plain", type=plain_refnum_type)],
    )
    glyph3 = _cluster_value_glyph(plain_outer, None, False, None)
    assert isinstance(glyph3, ClusterConstantGlyph)
    (name3, field_glyph3), = glyph3.fields
    assert name3 == "Plain"
    assert isinstance(field_glyph3, RefnumGlyph)
    assert field_glyph3.kind == "LVObjCtl"
    assert field_glyph3.terminal is None
    assert not isinstance(field_glyph3, ClusterConstantGlyph)


def test_pass_through_mux_is_not_a_bundle_glyph():
    """A 1-in/1-out mux with NO field (list) terminals — both terminals are
    the aggregate — is a structure-boundary pass-through, not an
    assemble/disassemble; it must NOT get a Bundle/Unbundle glyph."""
    from lvkit.render.glyph import BundleGlyph, UnbundleGlyph
    from lvkit.render.nodes import resolve_glyph

    node = _prim(
        "nMux",
        "Bundle/Unbundle By Name",
        dirs=("input", "output"),
        roles=("agg", "agg"),
    )
    glyph = resolve_glyph(node, _ctx())
    assert not isinstance(glyph, (BundleGlyph, UnbundleGlyph))


def test_bundle_by_name_never_dropped_without_field_names():
    """A real Bundle/Unbundle By Name (has ``list`` field terminals) is NEVER
    treated as an invisible boundary muxer — even when its cluster type's field
    NAMES don't resolve (e.g. a typedef whose VCTP failed to serialize). Only a
    fieldless compiler shift-register/tunnel muxer is skipped; otherwise the node
    would become a blank hole (the MasterAcquisition regression). The
    discriminator is the presence of ``list`` terminals, NOT field resolution."""
    from lvkit.render.scene import _is_boundary_mux

    g = InMemoryVIGraph()
    real = _prim(
        "nMux",
        "Bundle/Unbundle By Name",
        dirs=("input", "output"),
        roles=("agg", "list"),
    )
    assert _is_boundary_mux(real, g) is False
    compiler = _prim(
        "nMux",
        "Bundle/Unbundle By Name",
        dirs=("input", "output"),
        roles=("agg", "agg"),
    )
    assert _is_boundary_mux(compiler, g) is True


def test_bundle_by_name_falls_back_to_bracketed_index_labels():
    """When the wired cluster's field NAMES are unavailable, each accessed field
    is labelled by its INDEX in brackets — ``[0]``, ``[3]`` — never bare, so an
    index can't be mistaken for a field literally named "0"."""
    from lvkit.models import Terminal
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.nodes import _bundle_by_name_glyph

    terms = [
        Terminal(id="agg", index=0, direction="output", nmux_role="agg"),
        Terminal(
            id="f0",
            index=1,
            direction="input",
            nmux_role="list",
            nmux_field_index=0,
        ),
        Terminal(
            id="f1",
            index=2,
            direction="input",
            nmux_role="list",
            nmux_field_index=3,
        ),
    ]
    node = PrimitiveNode(
        id="n0",
        vi_path="V",
        name="Bundle/Unbundle By Name",
        node_type="nMux",
        terminals=terms,
    )
    glyph = _bundle_by_name_glyph(node, InMemoryVIGraph())
    assert isinstance(glyph, BundleByNameGlyph)
    assert glyph.names == ("[0]", "[3]")
    assert glyph.bundling is True  # field terminals are inputs -> bundling


# --------------------------------------------------------------------------- #
# In Place Element Structure (IPES) border nodes — decomposeClusterNode is
# structurally identical to nMux (same dcoAgg/dcoList/poser shape, see
# parser/node_types.py::DecomposeClusterHandler) and gets the SAME Bundle/
# Unbundle BY NAME treatment; decomposeMatchNode is a genuine whole-value
# pass-through (no field split) and gets its own small arrow glyph. Neither
# may ever show the internal "decompose" heap jargon in a user-facing name.
# --------------------------------------------------------------------------- #


def test_bundle_unbundle_name_shared_helper():
    """``lvkit.models.bundle_unbundle_name`` is the ONE field-direction rule
    both ``render.nodes.mux_display_name`` (render header) and
    ``graph.construction`` (the ``decomposeClusterNode`` rename that feeds
    describe/netlist) call — verify it directly at the source."""
    from lvkit.models import Terminal, bundle_unbundle_name

    agg = Terminal(id="agg", index=0, direction="input", nmux_role="agg")
    fields_out = [
        Terminal(id="f0", index=1, direction="output", nmux_role="list"),
    ]
    fields_in = [
        Terminal(id="f0", index=1, direction="input", nmux_role="list"),
    ]
    assert bundle_unbundle_name([agg, *fields_out]) == "Unbundle"
    assert bundle_unbundle_name([agg, *fields_in]) == "Bundle"
    assert bundle_unbundle_name([agg, *fields_out], by_name=True) == (
        "Unbundle By Name"
    )
    assert bundle_unbundle_name([agg, *fields_in], by_name=True) == ("Bundle By Name")
    # No field (``list``-role) terminals at all — nothing to key direction
    # off; the caller decides the fallback.
    assert bundle_unbundle_name([agg]) is None


def test_decompose_cluster_node_graph_name_is_direction_aware():
    """``graph/construction.py`` renames a ``decomposeClusterNode`` graph node
    to the direction-correct Bundle/Unbundle By Name AFTER nMux-style
    terminal-role enrichment — the same ``graph_node.name`` describe/netlist
    read, so those surfaces never see "Decompose Cluster" either."""
    from lvkit.models import Terminal, bundle_unbundle_name

    decompose_half = PrimitiveNode(
        id="n0",
        vi_path="V",
        name="Bundle/Unbundle By Name",
        node_type="decomposeClusterNode",
        terminals=[
            Terminal(id="agg", index=0, direction="input", nmux_role="agg"),
            Terminal(id="f0", index=1, direction="output", nmux_role="list"),
        ],
    )
    recompose_half = PrimitiveNode(
        id="n1",
        vi_path="V",
        name="Bundle/Unbundle By Name",
        node_type="decomposeClusterNode",
        terminals=[
            Terminal(id="f0", index=0, direction="input", nmux_role="list"),
            Terminal(id="agg", index=1, direction="output", nmux_role="agg"),
        ],
    )
    # Exercises the exact same rename construction.py applies once terminal
    # roles are enriched (isinstance(node, SelectNode) block).
    renamed = bundle_unbundle_name(decompose_half.terminals, by_name=True)
    if renamed is not None:
        decompose_half.name = renamed
    renamed = bundle_unbundle_name(recompose_half.terminals, by_name=True)
    if renamed is not None:
        recompose_half.name = renamed
    assert decompose_half.name == "Unbundle By Name"
    assert recompose_half.name == "Bundle By Name"
    assert "decompose" not in decompose_half.name.lower()
    assert "decompose" not in recompose_half.name.lower()


def test_decompose_cluster_node_resolves_to_unbundle_by_name():
    """The IPES cluster border node's DECOMPOSE half (fields are OUTPUTS) is
    Unbundle By Name — same field-direction rule as a real nMux."""
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.nodes import mux_display_name, resolve_glyph

    node = _prim(
        "decomposeClusterNode",
        "Bundle/Unbundle By Name",
        dirs=("input", "output", "output"),
        roles=("agg", "list", "list"),
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, BundleByNameGlyph)
    assert mux_display_name(node) == "Unbundle By Name"


def test_decompose_cluster_node_resolves_to_bundle_by_name():
    """The IPES cluster border node's RECOMPOSE half (fields are INPUTS) is
    Bundle By Name."""
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.nodes import mux_display_name, resolve_glyph

    node = _prim(
        "decomposeClusterNode",
        "Bundle/Unbundle By Name",
        dirs=("input", "input", "output"),
        roles=("list", "list", "agg"),
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, BundleByNameGlyph)
    assert mux_display_name(node) == "Bundle By Name"


def test_decompose_match_node_resolves_to_in_place_element_glyph():
    """decomposeMatchNode (the IPES's generic whole-value pass-through — the
    corpus shows exactly 2 terminals, no dcoAgg/dcoList field shape at all)
    draws as the clean-room ``InPlaceElementGlyph``, identically on both the
    input-side and output-side border node."""
    from lvkit.render.glyph import InPlaceElementGlyph
    from lvkit.render.nodes import resolve_glyph

    in_side = _prim(
        "decomposeMatchNode",
        "In Place Element",
        dirs=("input", "output"),
    )
    out_side = _prim(
        "decomposeMatchNode",
        "In Place Element",
        dirs=("input", "output"),
    )
    assert isinstance(resolve_glyph(in_side, _ctx()), InPlaceElementGlyph)
    assert isinstance(resolve_glyph(out_side, _ctx()), InPlaceElementGlyph)


@pytest.mark.parametrize(
    "node_type",
    [
        "decomposeClusterNode",
        "decomposeArrayNode",
        "decomposeDataValRefNode",
        "decomposeMatchNode",
    ],
)
def test_no_decompose_jargon_in_parser_display_name(node_type):
    """No IPES border-node handler's ``display_name`` (the parser-level
    fallback used when the heap carries no explicit label — see
    ``NodeTypeHandler._extract_common``) may contain the internal "decompose"
    word. This is the single static source ``node.name`` starts from before
    any direction-aware graph-layer rename."""
    from lvkit.parser.node_types import _HANDLERS

    handler = next(h for h in _HANDLERS if h.xml_class == node_type)
    assert "decompose" not in handler.display_name.lower()


def test_no_decompose_jargon_in_resolved_render_names():
    """Neither the Bundle/Unbundle-By-Name direction name nor the generic
    In-Place-Element fallback label ever contains "decompose" — the render
    header (``mux_display_name``), a describe/netlist-facing name, and the
    FallbackBoxResolver's labeled-box text all read from the same values."""
    from lvkit.render.glyph import WrappedBoxGlyph
    from lvkit.render.nodes import mux_display_name, resolve_glyph

    cluster_decompose = _prim(
        "decomposeClusterNode",
        "Bundle/Unbundle By Name",
        dirs=("input", "output"),
        roles=("agg", "list"),
    )
    assert "decompose" not in mux_display_name(cluster_decompose).lower()

    for node_type in ("decomposeArrayNode", "decomposeDataValRefNode"):
        fallback = _prim(node_type, "In Place Element", dirs=())
        glyph = resolve_glyph(fallback, _ctx())
        assert isinstance(glyph, WrappedBoxGlyph)
        assert "decompose" not in glyph.label.lower()
        assert glyph.label == "In Place Element"


def _typed_term(index, direction, lv_type):
    from lvkit.models import Terminal

    return Terminal(id=f"t{index}", index=index, direction=direction, lv_type=lv_type)


def test_inplace_border_name_dvr_read_vs_write():
    """The DVR IPES border tiles are told apart purely by which side the
    ``DataValueRef`` refnum sits on: input -> the read (deref) tile, output ->
    the write (store-back) tile. No name jargon, no guessing."""
    from lvkit.models import LVType, inplace_border_name

    dvr = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="DataValueRef"
    )
    cluster = LVType(kind=LVTypeKind.CLUSTER, underlying_type="Cluster")
    read = [_typed_term(0, "input", dvr), _typed_term(1, "output", cluster)]
    write = [_typed_term(0, "input", cluster), _typed_term(1, "output", dvr)]
    assert inplace_border_name("decomposeDataValRefNode", read) == "DVR Read"
    assert inplace_border_name("decomposeDataValRefNode", write) == "DVR Write"


def test_inplace_border_name_array_index_vs_replace():
    """The array IPES border tiles: the replace (write) tile hands an array
    back OUT (array-kind output); the index (read) tile only indexes an element
    out. Keyed on the presence of an array-typed OUTPUT terminal."""
    from lvkit.models import LVType, inplace_border_name

    arr = LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
    )
    elem = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    index = [_typed_term(0, "input", arr), _typed_term(1, "output", elem)]
    replace = [_typed_term(0, "input", arr), _typed_term(1, "output", arr)]
    assert inplace_border_name("decomposeArrayNode", index) == "Array Index"
    assert inplace_border_name("decomposeArrayNode", replace) == "Array Replace"


def test_inplace_border_name_other_types_untouched():
    """A node type the helper doesn't own returns None (leave its name alone)."""
    from lvkit.models import inplace_border_name

    assert inplace_border_name("decomposeMatchNode", []) is None
    assert inplace_border_name("mux", []) is None


def test_describe_in_place_operation_never_shows_raw_node_type():
    """``describe._describe_single_op``'s generic ``case _:`` fallback prints
    ``f"{name} [{node_type}]"`` for any node kind without a dedicated
    one-liner — which used to leak "decomposeRecomposeStructure" verbatim for
    an unlabeled In Place Element Structure. InPlaceNode (and
    DisableStructureNode/EventStructureNode, same catch-all) now get their
    own case that returns the already-faithful ``name`` untouched."""
    from lvkit.graph.core import InMemoryVIGraph
    from lvkit.graph.describe import _describe_single_op
    from lvkit.graph.models import InPlaceNode

    graph = InMemoryVIGraph()

    unlabeled = InPlaceNode(
        id="V::1",
        vi_path="V",
        name="In Place Element",
        node_type="decomposeRecomposeStructure",
    )
    assert _describe_single_op(graph, "V", unlabeled) == "In Place Element"

    labeled = InPlaceNode(
        id="V::2",
        vi_path="V",
        name="write multiple elements.vi",
        node_type="decomposeRecomposeStructure",
    )
    result = _describe_single_op(graph, "V", labeled)
    assert result == "write multiple elements.vi"
    assert "decompose" not in result.lower()


def test_comparison_primitives_use_the_arith_triangle():
    """The comparison functions reuse the borderless arithmetic triangle with
    their own symbol (same shape LabVIEW draws them with)."""
    from lvkit.render.glyph import ArithGlyph
    from lvkit.render.nodes import resolve_glyph

    expected = {
        "Equal?": "=",
        "Not Equal?": "≠",
        "Greater?": ">",
        "Less?": "<",
        "Greater Or Equal?": "≥",
        "Less Or Equal?": "≤",
    }
    for name, symbol in expected.items():
        node = _prim("prim", name, dirs=("input", "input", "output"))
        glyph = resolve_glyph(node, _ctx())
        assert isinstance(glyph, ArithGlyph), name
        assert glyph.symbol == symbol, name


def test_original_glyphs_lead_the_resolver_chain():
    """Clean-room OriginalGlyphResolver leads the chain so our own glyphs win,
    and the NI-derived PDF-icon resolver is gone entirely (no licensed art)."""
    from lvkit.render import nodes as nodes_mod
    from lvkit.render.nodes import _RESOLVERS, OriginalGlyphResolver

    types = [type(r) for r in _RESOLVERS]
    assert types[0] is OriginalGlyphResolver
    assert not hasattr(nodes_mod, "PdfIconResolver")


def test_bundle_and_unbundle_draw_mirrored_split_boxes():
    """Bundle draws element rows on the LEFT + arrow cell on the RIGHT;
    Unbundle mirrors it (arrow cell LEFT, rows RIGHT). Both draw the shared
    split-box skeleton: a rect, one vertical divider, num_fields-1 row
    dividers, and the direction arrow — at the node's own bounds."""
    from lvkit.render.glyph import BundleGlyph, UnbundleGlyph

    bounds = (0.0, 0.0, 60.0, 40.0)
    for glyph in (BundleGlyph(num_fields=3), UnbundleGlyph(num_fields=3)):
        b = SvgBackend()
        glyph.draw(b, bounds, DEFAULT_THEME)
        svg = b.render(bounds)
        assert svg.count("<rect") == 1
        # 1 vertical divider + (num_fields - 1) row dividers = 3 lines.
        assert len(re.findall(r"<line", svg)) == 1 + (3 - 1)
        assert "▶" in svg
    # The arrow sits on OPPOSITE halves for bundle vs unbundle (mirror).
    bb = SvgBackend()
    BundleGlyph(num_fields=3).draw(bb, bounds, DEFAULT_THEME)
    ub = SvgBackend()
    UnbundleGlyph(num_fields=3).draw(ub, bounds, DEFAULT_THEME)

    def arrow_x(svg):
        m = re.search(r'<text x="([\d.]+)"[^>]*>▶<', svg)
        return float(m.group(1))

    assert arrow_x(bb.render(bounds)) > 30.0  # right half
    assert arrow_x(ub.render(bounds)) < 30.0  # left half


def test_entry_edge_point_seats_dot_on_wire_side():
    """A coercion dot on a border terminal sits on the edge the wire ENTERS
    (toward the source), never the terminal's center."""
    from lvkit.render.scene import _entry_edge_point

    center = (100.0, 100.0)
    bounds = (90.0, 92.0, 110.0, 108.0)  # 20x16 box around the center
    # wire coming from the left -> left edge (x1), same y as center
    assert _entry_edge_point(center, bounds, (10.0, 100.0)) == (90.0, 100.0)
    # from the right -> right edge (x2)
    assert _entry_edge_point(center, bounds, (400.0, 100.0)) == (110.0, 100.0)
    # from above -> top edge (y1)
    assert _entry_edge_point(center, bounds, (100.0, 10.0)) == (100.0, 92.0)
    # no bounds -> center (unchanged)
    assert _entry_edge_point(center, None, (10.0, 100.0)) == center


def test_string_constant_has_full_text_tooltip():
    """A string/path constant whose in-box text may be ellipsized carries the
    FULL value as a native <title> tooltip so it stays readable on hover."""
    from lvkit.graph.models import ConstantNode
    from lvkit.models import LVType
    from lvkit.render.draw import draw_node
    from lvkit.render.glyph import ConstantGlyph
    from lvkit.render.scene import RenderNode

    full = "a very long string constant that will not fit in this little box"
    node = ConstantNode(
        id="c1",
        vi_path="v",
        value=full,
        lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
    )
    rn = RenderNode(
        node=node,
        bounds=(0.0, 0.0, 40.0, 16.0),
        glyph=ConstantGlyph(full, "#000000", multiline=True),
    )
    b = SvgBackend()
    draw_node(rn, b)
    svg = b.render((0.0, 0.0, 40.0, 16.0))
    assert f"<title>{full}</title>" in svg


def test_bundle_by_name_glyph_draws_field_names():
    """Bundle/Unbundle By Name draws one row per field, each LABELED with the
    field name, AND the cluster-direction arrow cell (task #89) — matching
    LabVIEW's real Bundle/Unbundle By Name glyph."""
    from lvkit.render.glyph import BundleByNameGlyph

    bounds = (0.0, 0.0, 90.0, 60.0)
    b = SvgBackend()
    BundleByNameGlyph(names=("level", "parent name", "xml index")).draw(
        b,
        bounds,
        DEFAULT_THEME,
    )
    svg = b.render(bounds)
    for name in ("level", "parent name", "xml index"):
        assert name in svg
    # The direction arrow is a proper rightward arrow: a triangular head
    # (<polygon>) OVER a shaft (<line>). The shaft is what keeps it from reading
    # as a bare "play" triangle.
    assert "<polygon" in svg
    assert "<line" in svg


def test_bundle_by_name_arrow_sits_on_cluster_column_and_points_right():
    """The direction arrow sits in the cluster column — RIGHT for Bundle By Name
    (fields in -> cluster out), LEFT for Unbundle By Name — and points RIGHT in
    BOTH cases (data assembles/extracts left-to-right). The head is a filled
    ``<polygon>`` triangle whose apex (max x) is to the right of its base."""
    from lvkit.render.glyph import BundleByNameGlyph

    bounds = (0.0, 0.0, 100.0, 40.0)

    def head_pts(bundling: bool) -> list[tuple[float, float]]:
        b = SvgBackend()
        BundleByNameGlyph(names=("a", "b"), bundling=bundling).draw(
            b, bounds, DEFAULT_THEME
        )
        m = re.search(r'<polygon points="([^"]+)"', b.render(bounds))
        assert m is not None
        return [
            (float(x), float(y))
            for x, y in re.findall(r"([-\d.]+),([-\d.]+)", m.group(1))
        ]

    for bundling, side in ((True, "right"), (False, "left")):
        pts = head_pts(bundling)
        xs = [p[0] for p in pts]
        apex_x = max(xs)  # the single rightmost vertex is the tip
        base_x = min(xs)
        assert apex_x > base_x  # points RIGHT for both directions
        mean_x = sum(xs) / len(xs)
        if side == "right":
            assert mean_x > 50.0  # Bundle: cluster column on the RIGHT
        else:
            assert mean_x < 50.0  # Unbundle: cluster column on the LEFT


def test_nmux_renders_as_bundle_by_name_and_skips_boundary_mux():
    """nMux with named fields renders as a By-Name box with the field names
    resolved from the wired cluster type; a 1-in/1-out nMux boundary muxer is
    still skipped (no box)."""
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.scene import _is_boundary_mux

    loaded = _load_graph(
        Path(
            ".lvkit/cache/samples/JKI-EasyXML/Source/Fast Parser/"
            "XML Loop Stack Recursion.vi"
        )
    )
    if loaded is None:
        pytest.skip("sample VI not available")
    graph, vi = loaded
    nmux = {
        n.id.split("::")[-1]: n
        for n in graph.iter_nodes(vi)
        if getattr(n, "node_type", None) == "nMux"
    }
    scene = build_scene(graph, vi)
    assert scene is not None
    rn = {r.node.id.split("::")[-1]: r for r in scene.nodes}

    # 1969: 1-in/11-out Unbundle By Name — drawn, with resolved field names.
    if "1969" in nmux:
        assert "1969" in rn, "By-Name nMux must be rendered, not dropped"
        glyph = rn["1969"].glyph
        assert isinstance(glyph, BundleByNameGlyph)
        assert "xml index" in glyph.names and "pretty print level" in glyph.names
    # A single-field nMux is STILL a visible By-Name access — it must render
    # (not be dropped as a hole), and only a nMux with no named-field cluster
    # counts as an invisible boundary muxer.
    if "1854" in nmux:
        assert "1854" in rn, "single-field By-Name nMux must be drawn"
        assert isinstance(rn["1854"].glyph, BundleByNameGlyph)
        assert rn["1854"].glyph.names == ("status",)
    for node in nmux.values():
        agg = next((t for t in node.terminals if t.nmux_role == "agg"), None)
        has_fields = bool(agg and agg.lv_type and agg.lv_type.fields)
        assert _is_boundary_mux(node, graph) is not has_fields


def test_nmux_on_class_private_data_draws_field_name():
    """An nMux unbundling an LVOOP class's own private data (the "this" refnum,
    not an anonymous cluster) must ALSO render as a By-Name box with the real
    field name — not fall back to the compact glyph, and not be dropped as a
    boundary mux. Field names for a class type live only in dep_graph (never
    inline on the terminal's ``lv_type.fields``), so this exercises the
    ``graph.get_type_fields()`` path through both ``_is_boundary_mux`` and
    ``_bundle_by_name_glyph`` (task #56)."""
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.scene import _is_boundary_mux

    testresult_dir = Path(
        ".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestResult"
    )  # noqa: E501
    vi_path = testresult_dir / "GetTestsRun.vi"
    if not vi_path.exists():
        pytest.skip("sample VI not available")

    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), search_paths=[testresult_dir.parent])
    vi_name = "TestResult.lvclass:GetTestsRun.vi"

    nmux = [
        n for n in graph.iter_nodes(vi_name) if getattr(n, "node_type", None) == "nMux"
    ]
    assert nmux, "expected an nMux node on the class private-data unbundle"
    agg = next((t for t in nmux[0].terminals if t.nmux_role == "agg"), None)
    assert agg is not None and agg.lv_type is not None
    # The agg terminal is the class type itself (via the "this" refnum) — it
    # carries NO inline fields; they only resolve through dep_graph.
    assert agg.lv_type.classname == "TestResult.lvclass"
    assert not agg.lv_type.fields

    assert not _is_boundary_mux(nmux[0], graph), (
        "class private-data nMux must be a visible By-Name box, not a "
        "dropped boundary muxer"
    )

    scene = build_scene(graph, vi_name)
    assert scene is not None
    rn = {r.node.id.split("::")[-1]: r for r in scene.nodes}
    raw_uid = nmux[0].id.split("::")[-1]
    assert raw_uid in rn, "class private-data nMux must be drawn"
    glyph = rn[raw_uid].glyph
    assert isinstance(glyph, BundleByNameGlyph)
    assert glyph.names == ("testsRun",)


def _daqmx_runtime_paths() -> tuple[Path, Path] | None:
    """``(input.vi, class-dir)`` for the DCAF-DAQModule sample, or None if
    the sample isn't pulled locally."""
    module_dir = Path(
        ".lvkit/cache/samples/DCAF-DAQModule/source/module/execution",
    )
    vi_path = module_dir / "input.vi"
    if not vi_path.exists():
        return None
    return vi_path, module_dir


def test_parse_private_data_fields_is_authoritative_to_the_class():
    """``structure._parse_private_data_fields`` must return the NAMED class's
    own private-data fields, not the first "class private data" match found
    in directory-glob order — which can belong to a completely different
    class referenced as a parameter by an unrelated method VI. Regression
    for the DAQmx Module Runtime corpus bug: blind first-match returned
    ``instance ID``/``key-value pairs`` (a different class) instead of this
    class's real ``DAQ Tasks``/``Channel Indeces``. Also exercises the
    case-insensitive class-name match: the type reference is
    ``DAQmx Module Runtime.lvclass`` but the file on disk is
    ``Daqmx Module runtime.lvclass``.

    Sibling method VIs of the SAME class can carry slightly different
    private-data snapshots (LabVIEW inlines a per-method copy, saved
    whenever that method was last edited) — this only pins that the WRONG
    CLASS is never matched, not that a specific sibling method wins the
    directory scan. ``_bundle_by_name_glyph`` additionally prefers the
    CURRENTLY-RENDERED VI's own copy over this dep_graph result specifically
    to sidestep such cross-method drift — see
    ``test_bundle_by_name_resolves_nested_class_private_data_field_names``.
    """
    from lvkit.structure import _parse_private_data_fields

    paths = _daqmx_runtime_paths()
    if paths is None:
        pytest.skip("sample VI not available")
    _vi_path, module_dir = paths
    lvclass_path = module_dir / "Daqmx Module runtime.lvclass"
    if not lvclass_path.exists():
        pytest.skip("sample .lvclass not available")

    fields = _parse_private_data_fields(lvclass_path)
    names = [f.name for f in fields]
    assert names == ["DAQ Tasks", "Channel Indeces"]
    assert names != ["instance ID", "key-value pairs"]  # the wrong-class bug
    assert [sf.name for sf in fields[0].sub_fields] == [
        "AI Task",
        "AO Task",
        "DI Task",
        "DO Task",
        "PWM Task",
    ]
    index_group = [sf.name for sf in fields[1].sub_fields]
    assert index_group[:4] == ["AI Index", "AO Index", "DI Index", "DO Index"]


def test_resolve_nmux_field_name_flattens_nested_clusters_leaf_first():
    """In LEAF-only mode (IPES ``decomposeClusterNode`` / event data),
    ``_resolve_nmux_field_name`` flattens a NESTED private-data cluster
    LEAF-first — an intermediate sub-cluster (``DAQ Tasks``, ``Channel
    Indeces``) is never itself an addressable flat slot. Reproduces the exact
    corpus shape/indices (task: DAQmx Module Runtime decompose, indices 7/1/9/3
    -> DI Index/AO Task/PWM Freq Index/DO Task) without needing the sample VI on
    disk. Bundle-By-Name (``nMux``) counts sub-clusters too — see the full-mode
    assertions below and ``test_resolve_nmux_field_name_full_mode_...``."""
    from lvkit.graph.op_walk import _resolve_nmux_field_name
    from lvkit.models import ClusterField

    def sub(name: str) -> ClusterField:
        return ClusterField(name=name, type=None)

    daq_tasks = ClusterField(
        name="DAQ Tasks",
        type=LVType(
            kind=LVTypeKind.CLUSTER,
            fields=[
                sub(n)
                for n in (
                    "AI Task",
                    "AO Task",
                    "DI Task",
                    "DO Task",
                    "PWM Task",
                )
            ],
        ),
    )
    channel_indeces = ClusterField(
        name="Channel Indeces",
        type=LVType(
            kind=LVTypeKind.CLUSTER,
            fields=[
                sub(n)
                for n in (
                    "AI Index",
                    "AO Index",
                    "DI Index",
                    "DO Index",
                    "PWM Freq Index",
                    "PWM DC Index",
                )
            ],
        ),
    )
    fields = [daq_tasks, channel_indeces]

    assert _resolve_nmux_field_name(7, fields, leaf_only=True) == "DI Index"
    assert _resolve_nmux_field_name(1, fields, leaf_only=True) == "AO Task"
    assert _resolve_nmux_field_name(9, fields, leaf_only=True) == "PWM Freq Index"
    assert _resolve_nmux_field_name(3, fields, leaf_only=True) == "DO Task"
    # Out of range (beyond every leaf) -> no name, caller falls back further.
    assert _resolve_nmux_field_name(99, fields, leaf_only=True) is None
    # No field index at all -> no name.
    assert _resolve_nmux_field_name(None, fields, leaf_only=True) is None


def test_resolve_nmux_field_name_full_mode_addresses_whole_subclusters():
    """In FULL mode (Bundle/Unbundle By Name ``nMux``) the flat ``<i>`` index
    counts EVERY node depth-first, so a whole sub-cluster is its own addressable
    slot — the same shape a LEAF-only flatten cannot reach. Mirrors the DCAF
    ``Create Test Configuration`` Bundle node: index 0 is the first sub-cluster
    itself, and its own leaf is index 1 (leaf-only would put that leaf at 0)."""
    from lvkit.graph.op_walk import _nmux_index_leaf_only, _resolve_nmux_field_name
    from lvkit.models import ClusterField, Terminal

    inner = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="leaf", type=None)],
    )
    fields = [ClusterField(name="Sub", type=inner)]
    # FULL: slot 0 = the sub-cluster, slot 1 = its leaf.
    assert _resolve_nmux_field_name(0, fields, leaf_only=False) == "Sub"
    assert _resolve_nmux_field_name(1, fields, leaf_only=False) == "leaf"
    # LEAF-only: the sub-cluster is NOT a slot — only its leaf, at 0.
    assert _resolve_nmux_field_name(0, fields, leaf_only=True) == "leaf"
    assert _resolve_nmux_field_name(1, fields, leaf_only=True) is None

    # The discriminator: full-mode is ONLY an ``nMux`` over a plain (non-class)
    # cluster/typedef. Class private data (``classname``) stays leaf even under
    # ``nMux``; IPES decompose / event data are always leaf.
    def agg(classname: str | None) -> Terminal:
        return Terminal(
            id="a",
            index=0,
            direction="input",
            nmux_role="agg",
            lv_type=LVType(kind=LVTypeKind.CLUSTER, classname=classname),
        )

    assert _nmux_index_leaf_only("nMux", agg(None)) is False
    assert _nmux_index_leaf_only("nMux", agg("Foo.lvclass")) is True
    assert _nmux_index_leaf_only("decomposeClusterNode", agg(None)) is True
    assert _nmux_index_leaf_only("eventDataNode", agg(None)) is True


def test_nested_bundle_field_tooltip_shows_dotted_path():
    """A NESTED Bundle/Unbundle-By-Name field carries its FULL dotted path for
    the hover ``<title>`` tooltip (which never truncates), while the terse leaf
    label the glyph row + connector panel show is unchanged. A top-level field
    (the sub-cluster itself) gets no path."""
    from lvkit.graph.op_walk import _resolve_nmux_field_path
    from lvkit.models import ClusterField, Terminal
    from lvkit.render.draw import _terminal_help_lines, _terminal_label

    inner = LVType(
        kind=LVTypeKind.CLUSTER, fields=[ClusterField(name="leaf", type=None)]
    )
    fields = [ClusterField(name="Sub", type=inner)]
    # Full mode: the nested leaf keeps its dotted path; the sub-cluster itself
    # (a top-level slot) has none.
    assert _resolve_nmux_field_path(1, fields, leaf_only=False) == "Sub.leaf"
    assert _resolve_nmux_field_path(0, fields, leaf_only=False) is None
    # Leaf-only mode (decompose/event): the sub-cluster is not a slot, but its
    # leaf at 0 still carries its full nested path for the tooltip.
    assert _resolve_nmux_field_path(0, fields, leaf_only=True) == "Sub.leaf"

    t = Terminal(
        id="f",
        index=0,
        direction="output",
        nmux_role="list",
        nmux_field_index=1,
        display_name="leaf",
        field_path="Sub.leaf",
        lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
    )
    node = PrimitiveNode(id="n", vi_path="v", node_type="nMux", terminals=[t])
    # Tooltip lines show the FULL dotted path...
    assert any("Sub.leaf" in ln for ln in _terminal_help_lines(node))
    # ...while the terse label (glyph row / connector panel) stays the leaf.
    assert _terminal_label(t) == "leaf"


def test_resolve_nmux_field_name_falls_through_multiple_sources():
    """Each row is resolved independently across BOTH sources in priority
    order: the first source that has a name for THIS index wins, even if
    an earlier source is present but doesn't cover the index (or is
    empty)."""
    from lvkit.graph.op_walk import _resolve_nmux_field_name
    from lvkit.models import ClusterField

    own = [ClusterField(name="onlyInOwn", type=None)]
    dep = [
        ClusterField(name="first", type=None),
        ClusterField(name="second", type=None),
    ]
    # index 0 resolves from the first (own) source.
    assert _resolve_nmux_field_name(0, own, dep, leaf_only=True) == "onlyInOwn"
    # index 1 is out of range for own, falls through to dep.
    assert _resolve_nmux_field_name(1, own, dep, leaf_only=True) == "second"
    # Empty own source -> straight to dep.
    assert _resolve_nmux_field_name(1, [], dep, leaf_only=True) == "second"
    # Neither source covers it.
    assert _resolve_nmux_field_name(5, own, dep, leaf_only=True) is None


def test_bundle_by_name_resolves_nested_class_private_data_field_names():
    """End-to-end: the IPES ``decomposeClusterNode`` border nodes on the real
    DCAF-DAQModule ``input.vi`` (a method of ``Daqmx Module runtime.lvclass``)
    must resolve their accessed private-data fields (indices 7/1/9/3) to the
    real nested member names, not ``[7]``/``[1]``/``[9]``/``[3]`` brackets."""
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.nodes import _bundle_by_name_glyph

    paths = _daqmx_runtime_paths()
    if paths is None:
        pytest.skip("sample VI not available")
    vi_path, module_dir = paths

    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), search_paths=[module_dir.parent])
    vi_name = graph.resolve_vi_name(vi_path.name)

    decompose_nodes = [
        n
        for n in graph.iter_nodes(vi_name)
        if getattr(n, "node_type", None) == "decomposeClusterNode"
    ]
    assert decompose_nodes, "expected decomposeClusterNode border nodes"
    for node in decompose_nodes:
        glyph = _bundle_by_name_glyph(node, graph)
        assert isinstance(glyph, BundleByNameGlyph)
        assert glyph.names == ("DI Index", "AO Task", "PWM Freq Index", "DO Task")
        assert not any(n.startswith("[") for n in glyph.names)


def test_bundle_by_name_resolves_class_private_data_with_no_class_on_disk():
    """The SAME resolution must work when the owning ``.lvclass`` (and its
    sibling method VIs) are UNAVAILABLE at render time — e.g. only
    ``input.vi`` itself was copied out of the project — so the dep_graph
    class-fields path can never resolve at all. The VI's OWN inline
    "Cluster of class private data" copy is embedded in its own extracted
    XML regardless of what else is on disk, so field names still resolve."""
    import shutil
    import tempfile

    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.nodes import _bundle_by_name_glyph

    paths = _daqmx_runtime_paths()
    if paths is None:
        pytest.skip("sample VI not available")
    vi_path, _module_dir = paths

    with tempfile.TemporaryDirectory() as tmp:
        isolated_vi = Path(tmp) / vi_path.name
        shutil.copy(vi_path, isolated_vi)

        graph = InMemoryVIGraph()
        graph.load_vi(str(isolated_vi))  # only the VI itself exists in `tmp`
        vi_name = graph.resolve_vi_name(isolated_vi.name)
        assert not graph.get_class_fields("DAQmx Module Runtime.lvclass"), (
            "this test's premise is that the owning class is NOT reachable "
            "(a stub with no real fields, at best)"
        )

        decompose_nodes = [
            n
            for n in graph.iter_nodes(vi_name)
            if getattr(n, "node_type", None) == "decomposeClusterNode"
        ]
        assert decompose_nodes
        for node in decompose_nodes:
            glyph = _bundle_by_name_glyph(node, graph)
            assert isinstance(glyph, BundleByNameGlyph)
            assert glyph.names == (
                "DI Index",
                "AO Task",
                "PWM Freq Index",
                "DO Task",
            )


def test_bundle_by_name_prefers_own_vi_copy_over_stale_dep_graph_snapshot():
    """Sibling method VIs of the same class can carry slightly different
    private-data snapshots (LabVIEW inlines a copy per method, saved
    whenever that method was last edited). The dep_graph's
    ``get_class_fields`` reflects whichever sibling method matched first in
    ``_parse_private_data_fields``'s directory scan — which, for this
    corpus class, is a STALE snapshot missing "PWM DC Index" and calling
    the other field "PWM Index" instead of "PWM Freq Index".
    ``_bundle_by_name_glyph`` must still render ``input.vi``'s OWN, correct
    field name for its real field index — proof the own-VI-copy fallback is
    tried FIRST, not merely as a last resort when dep_graph is unavailable."""
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.nodes import _bundle_by_name_glyph

    paths = _daqmx_runtime_paths()
    if paths is None:
        pytest.skip("sample VI not available")
    vi_path, _module_dir = paths

    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path))  # default search -> class resolves too
    vi_name = graph.resolve_vi_name(vi_path.name)

    dep_fields = graph.get_class_fields("DAQmx Module Runtime.lvclass")
    assert dep_fields is not None, "the class DOES resolve (case-insensitive)"
    channel_indeces = next(f for f in dep_fields if f.name == "Channel Indeces")
    dep_subnames = [sf.name for sf in channel_indeces.type.fields]
    assert "PWM Freq Index" not in dep_subnames, (
        "this test's premise is a stale sibling-method snapshot in dep_graph"
    )

    decompose_nodes = [
        n
        for n in graph.iter_nodes(vi_name)
        if getattr(n, "node_type", None) == "decomposeClusterNode"
    ]
    assert decompose_nodes
    for node in decompose_nodes:
        glyph = _bundle_by_name_glyph(node, graph)
        assert isinstance(glyph, BundleByNameGlyph)
        # input.vi's OWN copy wins over the stale dep_graph snapshot.
        assert glyph.names == ("DI Index", "AO Task", "PWM Freq Index", "DO Task")


def test_bundle_by_name_attaches_resolved_names_to_terminals_for_the_panel():
    """The resolved field name must reach the field TERMINAL's
    ``display_name`` (not just the glyph row) so the hover connector-panel
    (``render/draw.py``'s ``_terminal_label``/``_pane_label``) shows the
    FULL, untruncated name — "PWM Freq Index", not the glyph's
    width-truncated "PWM Freq I…". Single source of truth: both surfaces
    must agree exactly."""
    from lvkit.render.draw import _terminal_label
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.nodes import _bundle_by_name_glyph

    paths = _daqmx_runtime_paths()
    if paths is None:
        pytest.skip("sample VI not available")
    vi_path, module_dir = paths

    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), search_paths=[module_dir.parent])
    vi_name = graph.resolve_vi_name(vi_path.name)

    decompose_nodes = [
        n
        for n in graph.iter_nodes(vi_name)
        if getattr(n, "node_type", None) == "decomposeClusterNode"
    ]
    assert decompose_nodes
    for node in decompose_nodes:
        glyph = _bundle_by_name_glyph(node, graph)
        assert isinstance(glyph, BundleByNameGlyph)
        assert glyph.names == ("DI Index", "AO Task", "PWM Freq Index", "DO Task")

        field_terms = sorted(
            (t for t in node.terminals if t.nmux_role == "list"),
            key=lambda t: t.index,
        )
        assert [_terminal_label(t) for t in field_terms] == [
            "DI Index",
            "AO Task",
            "PWM Freq Index",
            "DO Task",
        ]
        # The panel label is the FULL name, never the glyph's ellipsis-cut
        # form, and never the generic index-based fallback.
        assert all("…" not in _terminal_label(t) for t in field_terms)
        assert all(not _terminal_label(t).startswith("terminal ") for t in field_terms)


def test_resolve_bundle_by_name_labels_only_attaches_real_names():
    """``_resolve_bundle_by_name_labels`` must set ``display_name`` ONLY when
    a field source actually resolves a name — never for the ``t.name``
    fallback (redundant: ``_terminal_display_name`` already falls back to
    ``t.name`` on its own) nor for the bracketed-index last resort (a
    bracket isn't a field name; the panel keeps its own ``terminal N``
    default for that row)."""
    from lvkit.models import ClusterField, Terminal
    from lvkit.render.nodes import _resolve_bundle_by_name_labels

    fields = [ClusterField(name="widgetCount", type=None)]
    resolved = Terminal(
        id="f0",
        index=0,
        direction="output",
        nmux_role="list",
        nmux_field_index=0,
    )
    named_by_terminal = Terminal(
        id="f1",
        index=1,
        direction="output",
        nmux_role="list",
        nmux_field_index=None,
        name="caller side name",
    )
    unresolved = Terminal(
        id="f2",
        index=2,
        direction="output",
        nmux_role="list",
        nmux_field_index=5,
    )
    field_terms = [resolved, named_by_terminal, unresolved]

    names = _resolve_bundle_by_name_labels(field_terms, fields, leaf_only=False)
    assert names == ("widgetCount", "caller side name", "[5]")
    assert resolved.display_name == "widgetCount"
    assert named_by_terminal.display_name is None
    assert unresolved.display_name is None


def test_bundle_by_name_falls_back_to_dep_graph_when_vi_source_unknown():
    """When the VI's own source path isn't known to the graph (so the
    inline-copy fallback can't run at all — e.g. a synthetic/in-memory
    node), a class-typed aggregate still resolves through the dep_graph
    class fields (``get_class_fields``) alone. Additive/no-regression check
    for the JKI-VI-Tester-style path, independent of any sample VI on
    disk."""
    from lvkit.models import ClusterField, Terminal
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.nodes import _bundle_by_name_glyph

    graph = InMemoryVIGraph()
    assert graph.get_vi_source_path("V") is None  # never loaded -> no source
    graph._dep_graph.add_node(
        "Synthetic.lvclass",
        node_type="class",
        fields=[ClusterField(name="widgetCount", type=None)],
        parent_class=None,
    )

    terms = [
        Terminal(
            id="agg",
            index=0,
            direction="input",
            nmux_role="agg",
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, classname="Synthetic.lvclass"),
        ),
        Terminal(
            id="f0",
            index=1,
            direction="output",
            nmux_role="list",
            nmux_field_index=0,
        ),
    ]
    node = PrimitiveNode(
        id="n0",
        vi_path="V",
        name="Bundle/Unbundle By Name",
        node_type="nMux",
        terminals=terms,
    )
    glyph = _bundle_by_name_glyph(node, graph)
    assert isinstance(glyph, BundleByNameGlyph)
    assert glyph.names == ("widgetCount",)


# ---------------------------------------------------------------------------
# Case displayed-frame — the initial frame shown in the SVG is the one LabVIEW
# last displayed (dataspace), not just the default/frame-0 fallback (#81).
# ---------------------------------------------------------------------------


def _case_node_with_frames(displayed_frame):
    from lvkit.models import CaseFrame

    frames = [
        CaseFrame(selector_value="a"),
        CaseFrame(selector_value="b"),
        CaseFrame(selector_value="c", is_default=True),
    ]
    return CaseStructureNode(
        id="vi::case1",
        vi_path="vi",
        name="Case",
        node_type="select",
        frames=frames,
        selector_terminal=None,
        displayed_frame=displayed_frame,
    )


class TestCaseDisplayedFrame:
    def test_displayed_frame_selects_initial(self):
        node = _case_node_with_frames(displayed_frame=1)
        default_frame, _, _, _ = _frame_info([node], "vi", None)
        assert default_frame["case1"] == "b"

    def test_no_displayed_frame_falls_back_to_default(self):
        node = _case_node_with_frames(displayed_frame=None)
        default_frame, _, _, _ = _frame_info([node], "vi", None)
        assert default_frame["case1"] == "c"  # the is_default frame

    def test_out_of_range_displayed_frame_falls_back(self):
        node = _case_node_with_frames(displayed_frame=9)
        default_frame, _, _, _ = _frame_info([node], "vi", None)
        assert default_frame["case1"] == "c"


# ---------------------------------------------------------------------------
# "A=a" Case Insensitive Match badge (#58) — bottom-left of a string case.
# ---------------------------------------------------------------------------


def _draw_case_border_svg(case_insensitive: bool) -> str:
    from lvkit.render.glyphs.structures.case import CaseGlyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = CaseGlyph(case_insensitive=case_insensitive)
    backend = SvgBackend()
    glyph.draw(backend, (0.0, 0.0, 120.0, 90.0), DEFAULT_THEME)
    return backend.render((0.0, 0.0, 120.0, 90.0))


class TestCaseInsensitiveBadge:
    def test_badge_drawn_when_case_insensitive(self):
        assert "A=a" in _draw_case_border_svg(True)

    def test_no_badge_when_case_sensitive(self):
        assert "A=a" not in _draw_case_border_svg(False)


# ---------------------------------------------------------------------------
# "Use Default If Unwired" — output tunnel drawn hollow when a case frame
# leaves it unwired (per-frame inner-tunnel wiredness).
# ---------------------------------------------------------------------------


def _case_with_output_tunnel(frames: list[str], wired: list[str]):
    """A case output tunnel with one inner per frame; ``wired`` names the frames
    whose inner IS a wire destination."""
    from lvkit.models import TunnelTerminal

    outer = TunnelTerminal(
        id="vi::out", index=0, direction="output", tunnel_type="csTun", boundary="outer"
    )
    inners = [
        TunnelTerminal(
            id=f"vi::in_{f}",
            index=0,
            direction="input",
            tunnel_type="csTun",
            boundary="inner",
            paired_id="vi::out",
            frame=f,
        )
        for f in frames
    ]
    node = CaseStructureNode(
        id="vi::c",
        vi_path="vi",
        name="Case",
        node_type="select",
        terminals=[outer, *inners],
    )
    wired_dest = frozenset(f"vi::in_{f}" for f in wired)
    return node, wired_dest


class TestDefaultIfUnwired:
    def _bt(self, frames, wired):
        from lvkit.render.scene import _structure_borders

        node, wired_dest = _case_with_output_tunnel(frames, wired)
        layout = Layout(node_bounds={"out": (10.0, 10.0, 19.0, 19.0)})
        bts = _structure_borders(node, layout, "vi", wired_dest)
        return next(b for b in bts if b.glyph_kind == "tunnel")

    def test_unwired_frames_collected(self):
        bt = self._bt(frames=["a", "b", "c"], wired=["a", "c"])
        assert bt.unwired_frames == frozenset({"b"})

    def test_all_wired_is_empty(self):
        bt = self._bt(frames=["a", "b"], wired=["a", "b"])
        assert bt.unwired_frames == frozenset()

    def _draw(self, unwired_frames, frame_value):
        from lvkit.render.draw import _draw_border_terminal
        from lvkit.render.scene import RenderBorderTerminal
        from lvkit.render.style import DEFAULT_THEME

        bt = RenderBorderTerminal(
            terminal=None,
            bounds=(0.0, 0.0, 12.0, 12.0),
            glyph_kind="tunnel",
            color="#e05fa0",
            unwired_frames=frozenset(unwired_frames),
        )
        backend = SvgBackend()
        _draw_border_terminal(bt, backend, DEFAULT_THEME, frame_value)
        return backend.render((0.0, 0.0, 12.0, 12.0))

    def test_hole_only_in_unwired_frame(self):
        from lvkit.render.style import DEFAULT_THEME

        # In the unwired frame -> canvas hole punched.
        assert DEFAULT_THEME.canvas in self._draw({"b"}, frame_value="b")
        # In a wired frame -> solid, no hole.
        assert DEFAULT_THEME.canvas not in self._draw({"b"}, frame_value="a")


# ---------------------------------------------------------------------------
# compressedWireTable decoding (task #84) — validated corpus fixtures.
# ---------------------------------------------------------------------------


def _ortho(path: list[Point]) -> bool:
    return all(
        abs(p[0] - q[0]) < 0.01 or abs(p[1] - q[1]) < 0.01
        for p, q in zip(path, path[1:])
    )


def test_faithful_wire_table_default_on():
    # Task #84: faithful compressedWireTable routing is the default rendered
    # output. Set False for the pure auto-router baseline (A/B testing).
    assert FAITHFUL_WIRE_TABLE is True


def _chain(blob, start, end):
    """decode_signal for the 1-leaf (2-endpoint) case -> the single mid list."""
    res = decode_signal(blob, start, [end])
    return None if res is None else res[0]


def test_decode_signal_straight_wire_has_no_bends():
    assert _chain("0208", (10.0, 20.0), (60.0, 20.0)) == []


def test_decode_signal_single_sink_fanout_layout_returns_none():
    # A fan-out-layout blob handed to the 1-sink path can't be a chain -> None.
    assert _chain("0400080503200B18", (0.0, 0.0), (100.0, 100.0)) is None


def test_decode_signal_single_vertex_is_direct_connection():
    # Blob "01" = vertex count 1 (no segments): a degenerate/zero-length wire
    # whose two terminals resolve to the same center. Decodes to no bends so
    # the scene connects the endpoints directly instead of invoking the router.
    # (62 such signals in MasterAcquisitionFile_PCO_IOS.vi, all endpoints
    # coincident -- see #76.)
    assert _chain("01", (-1214.5, -390.5), (-1214.5, -390.5)) == []


def test_decode_signal_one_bend_l_shape():
    # "2D U32 Array Changed__ogtk" corpus wire, uids 99->110.
    start, end = (314.0, 175.0), (346.0, 162.0)
    mid = _chain("0308011F", start, end)
    assert mid is not None
    assert len(mid) == 1
    assert _ortho([start, *mid, end])


def test_decode_signal_two_bend_z_shape():
    # "2D U32 Array Changed__ogtk" corpus wire, uids 78->84.
    start, end = (181.0, 145.0), (264.5, 164.5)
    mid = _chain("04040000114F", start, end)
    assert mid is not None
    assert len(mid) == 2
    assert _ortho([start, *mid, end])


def test_decode_signal_two_sink_tree():
    # V=4, dir0=E, tokens [05 branch, 03 pop], lengths [32, 11, 24]:
    # E32 to a junction, tap S11 -> sink A, resume E24 -> sink B.
    mids = decode_signal("0400080503200b18", (0.0, 0.0), [(32.0, 11.0), (56.0, 0.0)])
    assert mids is not None
    assert len(mids) == 2
    for mid, sink in zip(mids, [(32.0, 11.0), (56.0, 0.0)]):
        assert _ortho([(0.0, 0.0), *mid, sink])
    assert mids[0] == [(32.0, 0.0)]  # L-shape via the junction
    assert mids[1] == []  # straight run along the trunk


def test_decode_signal_three_way_source_branch():
    # Compound dir0=0x0D (E|S|N) => the SOURCE itself is a 3-way junction: seg0
    # leaves in one bit direction and the other two leave as deferred branches.
    # Real 6-sink signal from MasterAcquisitionFile_PCO_IOS.vi (#76); before the
    # N-way generalization this returned None (len(bits)!=2) and fell to the
    # router. Every branch must decode to a clean orthogonal run to its sink.
    blob = "0E000D000300000505050003030303FF03D5190CFF012C1ABF22199898989844"
    src = (-2253.0, 497.0)
    sinks = [
        (-2228.5, -484.0),
        (-2185.5, 497.0),
        (-1801.5, 725.5),
        (-1801.5, 760.0),
        (-1801.5, 785.0),
        (-1801.5, 534.5),
    ]
    mids = decode_signal(blob, src, sinks)
    assert mids is not None
    assert len(mids) == len(sinks)
    for mid, sink in zip(mids, sinks):
        assert _ortho([src, *mid, sink])


def test_decode_signal_comb_fanout_early_prune():
    # A 12-sink "comb" (8 consecutive BRANCH taps off one trunk) from
    # MasterAcquisitionFile_PCO_IOS.vi: ~3^15 blind fork paths blow the search
    # budget, so it fell to the router (part of the #76 hang). The exact-endpoint
    # early-prune collapses the search; every branch must reach its known sink.
    blob = (
        "1900080700030600050505050505050500030303030303030303179DFF034F188CFF"
        "051AC6FF02DEFF0134A967E951BCFF0244FF01C6FF0196FF01C7FF01C8FF01B1FF01"
        "CDFF0158FF01F0EC"
    )
    src = (-2610.5, -413.5)
    sinks = [
        (-2294.5, 2156.5),
        (-2272.0, 2325.0),
        (-2273.0, 2428.0),
        (-2322.0, 2661.0),
        (-2231.5, 916.5),
        (-2274.0, 2742.0),
        (-2147.5, 2930.5),
        (-2351.5, -389.5),
        (-2267.0, 1848.0),
        (-2384.0, 1114.0),
        (-1740.5, -570.5),
    ]
    mids = decode_signal(blob, src, sinks)
    assert mids is not None
    assert len(mids) == len(sinks)
    for mid, sink in zip(mids, sinks):
        assert _ortho([src, *mid, sink])


def test_decode_signal_turned_trunk_comb_backward_neg_perp():
    # A 6-tooth comb whose trunk turns SOUTH before the taps: each 0x07 fork's
    # base immediate N would point BACKWARD up the southbound trunk, so it is
    # remapped to the trunk's negative-axis perpendicular (W) and taps west to its
    # sink while the trunk continues S. This is the deterministic turned-trunk fork
    # rule (docs/wire-compression-format.md "Layout B") — it must decode with NO
    # fork search. Real signal from MasterAcquisitionFile_PCO_IOS.vi.
    blob = (
        "190008060103000700000307000003070000030700000301000016FF018DFF02AF9929"
        "FF02621B1864971B133A971613FF0144FF025E1B16FF0119FF02551C14"
    )
    src = (605.0, 1765.0)
    sinks = [
        (-59.5, 1368.5),
        (648.0, 1933.0),
        (196.0, 2315.0),
        (203.0, 2597.0),
        (648.0, 1986.0),
        (194.0, 1833.0),
    ]
    from lvkit.parser.wire_table import _decode_tree_deterministic

    mids = _decode_tree_deterministic(blob, src, sinks)
    assert mids is not None, "turned-trunk comb must decode deterministically"
    assert len(mids) == len(sinks)
    for mid, sink in zip(mids, sinks):
        assert _ortho([src, *mid, sink])


def test_decode_signal_straight_through_terminal_tap():
    # b[1]=0x01 marks a straight-through terminal tap: a sink sits ON the path
    # (the 0x02 token continues straight through it). Both sinks must resolve to
    # clean orthogonal branches -- the mid-wire tap is a prefix of the trunk.
    sinks = [(18.0, 0.0), (27.0, -15.0)]
    mids = decode_signal("0501000802010012040f05", (0.0, 0.0), sinks)
    assert mids is not None
    assert len(mids) == 2
    for mid, sink in zip(mids, sinks):
        assert _ortho([(0.0, 0.0), *mid, sink])
    assert mids[0] == []  # sink on the trunk -> straight branch, no bends


def test_decode_signal_escaped_length():
    # A long segment (>=256 px) is stored as 0xff hi lo, so the blob is longer
    # than the old 2V-2 invariant. V=4, dir0=E, signs 00 00, lengths [9, 0x0464].
    start, end = (0.0, 0.0), (1124.0, 50.0)
    mid = _chain("0408000009ff0464", start, end)
    assert mid is not None
    assert len(mid) == 2
    # first bend advances East by 9 px, staying on the source row
    assert mid[0] == (9.0, 0.0)
    assert _ortho([start, *mid, end])


@pytest.mark.needs_samples
def test_layout_wire_by_uid_drives_faithful_render():
    # End-to-end: every drawn wire's faithful geometry is keyed by its sink uid,
    # and the whole VI renders WITHOUT invoking the auto-router (deterministic,
    # heap-faithful). Regression guard for the unified uid-driven decoder (#76).
    from lvkit.parser.layout import build_layout
    from lvkit.render import scene as scene_mod

    layout = build_layout(NESTED_CASE_VI)
    assert layout.wire_by_uid, "no faithful wire geometry resolved"
    # keys are terminal uids, values are bend polylines (lists of points)
    for uid, mid in layout.wire_by_uid.items():
        assert isinstance(uid, str)
        assert all(len(p) == 2 for p in mid)

    orig = scene_mod.WireRouter.route
    calls = [0]

    def _counted(self, *a, **k):
        calls[0] += 1
        return orig(self, *a, **k)

    scene_mod.WireRouter.route = _counted
    try:
        svg = render_vi_file(NESTED_CASE_VI, mode=LoadMode.NONE)
    finally:
        scene_mod.WireRouter.route = orig
    assert svg is not None
    assert calls[0] == 0  # fully faithful: no router fallback for this VI


def test_mux_doc_url_resolves_by_field_direction():
    """A direction-polymorphic cluster-mux node links by its RESOLVED display
    name, not its ambiguous raw name: fields-out -> Unbundle By Name,
    fields-in -> Bundle By Name (task #67)."""
    from lvkit.render.draw import _node_doc_url

    unbundle = _prim(
        "nMux",
        "Bundle/Unbundle By Name",
        dirs=("input", "output", "output"),
        roles=("agg", "list", "list"),
    )
    assert _node_doc_url(unbundle).endswith("/functions/unbundle-by-name.html")
    bundle = _prim(
        "nMux",
        "Bundle/Unbundle By Name",
        dirs=("output", "input", "input"),
        roles=("agg", "list", "list"),
    )
    assert _node_doc_url(bundle).endswith("/functions/bundle-by-name.html")


def test_node_type_flavor_carries_doc_url():
    """resolve_by_node_type surfaces doc_url so node-type primitives (no
    primResID: Build Array, Compound Arithmetic, ...) link to NI docs (#67)."""
    from lvkit.primitive_resolver import get_resolver

    r = get_resolver()
    assert r.resolve_by_node_type("aBuild").doc_url.endswith(
        "/functions/build-array.html"
    )
    assert r.resolve_by_node_type("cpdArith").doc_url.endswith(
        "/functions/compound-arithmetic.html"
    )


def test_refnum_wire_is_dark_green_not_grey():
    """Generic refnums (VI reference, driver/DAQ/VISA session, queue, ...) use
    LabVIEW's reference-wire color. An LVOOP class instance is also a Refnum but
    carries a classname: it is NOT a reference wire — a class that sets its own
    wire style draws in that, and an UNRESOLVED class (no style in its ancestry)
    draws the default grey class chain, never the reference wire."""
    import dataclasses

    from lvkit.models import WireStyle
    from lvkit.render.style import type_family

    generic_ref = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="DataLogRefnum"
    )
    assert type_family(generic_ref) == "refnum"
    assert wire_style(generic_ref).color == DEFAULT_THEME.wire_refnum
    assert wire_style(generic_ref).color != DEFAULT_THEME.wire_default

    lvoop = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname="Camera.lvclass",
    )
    # Unresolved class -> the default grey class chain, NOT the reference wire.
    assert type_family(lvoop) == "unknown"
    lvoop_ws = wire_style(lvoop)
    assert lvoop_ws.color == DEFAULT_THEME.wire_default and bool(lvoop_ws.fill_pattern)

    # A class that DOES carry a wire style draws in that, not the grey chain.
    styled = dataclasses.replace(
        lvoop, wire_style=WireStyle(color="#123456", width=2.0)
    )
    assert wire_style(styled).color == "#123456"


def test_property_node_glyph_shows_named_rows_with_read_write():
    """A Property Node draws one row per accessed property, labelled with the
    property NAME (from node.properties) and marked read (value out) vs write
    (value in) by the value terminal's direction — not a blank "Property Node"
    box. Reference (Refnum) and error-cluster terminals are excluded from the
    rows via the STRUCTURAL dcoList correlation (``property_value_terminal_ids``
    -- see ``op_walk.correlate_property_terminals``), not a type-based guess:
    a property's own value can itself be Refnum-typed (a real corpus case,
    see test_netlist.py's Library:Project coverage), so only the ids the
    parser's dcoList actually names are treated as property values."""
    from lvkit.models import PropertyDef, Terminal
    from lvkit.render.glyph import PropertyNodeGlyph
    from lvkit.render.nodes import _property_node_glyph

    def term(idx, direction, ut):
        return Terminal(
            id=f"VI::{idx}",
            index=idx,
            direction=direction,
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=ut),
        )

    node = PrimitiveNode(
        id="VI::9",
        vi_path="VI",
        node_type="propNode",
        name="Property Node",
        object_name="VI",
        properties=[PropertyDef(name="Value"), PropertyDef(name="Visible")],
        terminals=[
            term(0, "input", "Refnum"),  # reference in  (excluded)
            term(1, "output", "Refnum"),  # reference out (excluded)
            term(2, "input", "NumFloat64"),  # Value  -> write
            term(3, "output", "Boolean"),  # Visible -> read
        ],
        property_value_terminal_ids=["VI::2", "VI::3"],
    )
    glyph = _property_node_glyph(node)
    assert isinstance(glyph, PropertyNodeGlyph)
    assert glyph.rows == (("Value", False), ("Visible", True))
    assert glyph.class_name == "VI"  # header names the object class

    # The resolved property NAME is pinned onto its VALUE terminal's
    # display_name, so the hover connector-panel shows it (not "terminal N") —
    # same single-source pattern as Bundle-By-Name.
    assert node.terminals[2].display_name == "Value"  # idx 2 value term
    assert node.terminals[3].display_name == "Visible"  # idx 3 value term
    assert node.terminals[0].display_name is None  # reference term untouched

    # No properties -> None, so the caller falls back to the plain box.
    empty = PrimitiveNode(
        id="VI::10", vi_path="VI", node_type="propNode", name="Property Node"
    )
    assert _property_node_glyph(empty) is None


def test_implicit_property_node_shows_target_name_and_type_color_bar():
    """An IMPLICIT property node (task #51 / reference image #69) — one
    permanently bound to a specific front-panel control, discriminated by
    ``bound_control_uid`` (see ``parser.node_types.PropertyNode``'s class
    docstring; NEVER inferred from the label text or wiring) — draws the
    BOUND CONTROL's own name as its header (from the propNode's own heap
    ``<label>``, never the object class) plus a TYPE-COLOR BAR in that
    control's wire color. An EXPLICIT node (no ``bound_control_uid``) is
    completely unaffected — same assertions as the sibling explicit test
    above."""
    from lvkit.models import PropertyDef, Terminal
    from lvkit.render.glyph import PropertyNodeGlyph
    from lvkit.render.nodes import _property_node_glyph
    from lvkit.render.style import wire_style

    def term(idx, direction, ut):
        return Terminal(
            id=f"VI::{idx}",
            index=idx,
            direction=direction,
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=ut),
        )

    bool_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    node = PrimitiveNode(
        id="VI::16295",
        vi_path="VI",
        node_type="propNode",
        name="Abort",
        label="Abort",
        object_name="Bool",
        properties=[PropertyDef(name="Disabled")],
        terminals=[
            term(0, "input", "Refnum"),  # reference in (unwired, implicit)
            term(1, "output", "Refnum"),  # reference out
            term(2, "input", "NumUInt8"),  # Disabled -> write
        ],
        property_value_terminal_ids=["VI::2"],
        bound_control_uid="10449",
        bound_control_type=bool_type,
    )
    glyph = _property_node_glyph(node)
    assert isinstance(glyph, PropertyNodeGlyph)
    assert glyph.is_implicit is True
    assert glyph.target_name == "Abort"
    assert glyph.bar_color == wire_style(bool_type).color

    # An implicit node whose bound control's class isn't reconstructible
    # (bound_control_type is None) still shows the target name, just no bar
    # — never a guessed color. PrimitiveNode is a pydantic model, not a
    # dataclass -- model_copy(update=...) is its "replace".
    unresolved = node.model_copy(update={"id": "VI::16296", "bound_control_type": None})
    glyph2 = _property_node_glyph(unresolved)
    assert isinstance(glyph2, PropertyNodeGlyph)
    assert glyph2.is_implicit is True
    assert glyph2.target_name == "Abort"
    assert glyph2.bar_color is None

    # EXPLICIT (no bound_control_uid): unaffected, same as the plain
    # class-header form verified above.
    explicit = node.model_copy(
        update={
            "id": "VI::1110",
            "label": None,
            "bound_control_uid": "",
            "bound_control_type": None,
        }
    )
    glyph3 = _property_node_glyph(explicit)
    assert isinstance(glyph3, PropertyNodeGlyph)
    assert glyph3.is_implicit is False
    assert glyph3.target_name == ""
    assert glyph3.bar_color is None
    assert glyph3.class_name == "Bool"


def test_property_node_glyph_draws_bar_only_when_implicit():
    """``PropertyNodeGlyph.draw()`` draws the header text (target name when
    implicit, ``⚙ <class>`` otherwise) and, ONLY for an implicit node with a
    resolved ``bar_color``, a solid color bar under the header — never for
    an explicit node, and never a guessed color when unresolved."""
    from lvkit.render.glyph import PropertyNodeGlyph
    from lvkit.render.style import DEFAULT_THEME

    bounds = (0.0, 0.0, 90.0, 68.0)

    implicit = PropertyNodeGlyph(
        rows=(("Disabled", False),),
        class_name="Bool",
        is_implicit=True,
        target_name="Abort",
        bar_color="#4a9c3e",
    )
    b1 = SvgBackend()
    implicit.draw(b1, bounds, DEFAULT_THEME)
    svg1 = b1.render(bounds)
    assert ">Abort<" in svg1
    assert "⚙" not in svg1  # target-name header, never the class gear icon
    assert 'fill="#4a9c3e"' in svg1  # the type-color bar

    explicit = PropertyNodeGlyph(
        rows=(("Disabled", False),), class_name="Bool", is_implicit=False,
    )
    b2 = SvgBackend()
    explicit.draw(b2, bounds, DEFAULT_THEME)
    svg2 = b2.render(bounds)
    assert "⚙ Bool" in svg2
    assert 'fill="#4a9c3e"' not in svg2

    # Implicit but unresolved bar_color: target name shows, no bar drawn.
    unresolved = PropertyNodeGlyph(
        rows=(("Disabled", False),),
        class_name="Bool",
        is_implicit=True,
        target_name="Abort",
        bar_color=None,
    )
    b3 = SvgBackend()
    unresolved.draw(b3, bounds, DEFAULT_THEME)
    svg3 = b3.render(bounds)
    assert ">Abort<" in svg3
    assert svg3.count("<rect") == 1  # the outer box only -- no bar rect drawn


def test_gtr_abort_property_node_renders_implicit_with_color_bar():
    """Real-corpus check (task #51 / reference image #69): GTR's "Abort"
    boolean property node (heap uid 16295) renders IMPLICIT — header
    "Abort" (the bound control's own name), a green (Boolean) type-color
    bar, never the "⚙ Bool" class header."""
    from lvkit.render.glyph import PropertyNodeGlyph
    from lvkit.render.scene import RenderNode, build_scene
    from lvkit.render.style import wire_style

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (
            rn
            for rn in scene.nodes
            if isinstance(rn, RenderNode) and rn.node.id.endswith("::16295")
        ),
        None,
    )
    assert target is not None, "GTR's Abort property node not in scene"
    assert isinstance(target.glyph, PropertyNodeGlyph)
    assert target.glyph.is_implicit is True
    assert target.glyph.target_name == "Abort"
    bool_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    assert target.glyph.bar_color == wire_style(bool_type).color
    assert target.glyph.rows == (("Disabled", False),)


def test_explicit_property_node_in_corpus_keeps_class_header():
    """Real-corpus check: "Set Front Panel Object Control Value.vi"'s
    VI-reference property node (heap uid 1110) is EXPLICIT (no bound-control
    ``<ddo>``) and renders EXACTLY as before — "⚙ VI" class header, no
    target name, no color bar."""
    from lvkit.render.glyph import PropertyNodeGlyph
    from lvkit.render.scene import RenderNode, build_scene

    explicit_vi = Path(
        ".lvkit/cache/samples/JKI-VI-Tester/source/Utilities/"
        "Set Front Panel Object Control Value.vi"
    )
    loaded = _load_graph(explicit_vi)
    if loaded is None:
        pytest.skip(f"sample VI not available: {explicit_vi}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (
            rn
            for rn in scene.nodes
            if isinstance(rn, RenderNode) and rn.node.id.endswith("::1110")
        ),
        None,
    )
    assert target is not None, "explicit VI-reference property node not in scene"
    assert isinstance(target.glyph, PropertyNodeGlyph)
    assert target.glyph.is_implicit is False
    assert target.glyph.target_name == ""
    assert target.glyph.bar_color is None
    assert target.glyph.class_name == "VI"


def test_implicit_invoke_node_shows_target_name_and_type_color_bar():
    """An IMPLICIT invoke node (task #51/#55 extension, reference image #72)
    — one permanently bound to a specific front-panel control, discriminated
    by ``bound_control_uid`` (see ``parser.node_types.InvokeNode``'s class
    docstring; NEVER inferred from the label text or wiring) — draws the
    BOUND CONTROL's own name as its header (from the invoke node's own heap
    ``<label>``, never the object class) plus a TYPE-COLOR BAR in that
    control's wire color. An EXPLICIT node (no ``bound_control_uid``) is
    completely unaffected — mirrors the property-node behavior exactly."""
    from lvkit.models import Terminal
    from lvkit.render.glyph import InvokeNodeGlyph
    from lvkit.render.nodes import _invoke_node_glyph
    from lvkit.render.style import wire_style

    def term(idx, direction, ut):
        return Terminal(
            id=f"VI::{idx}",
            index=idx,
            direction=direction,
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=ut),
        )

    bool_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    node = PrimitiveNode(
        id="VI::11387",
        vi_path="VI",
        node_type="invokeNode",
        name="Test Hierarchy Tree",
        label="Test Hierarchy Tree",
        object_name="Tree (strict)",
        method_name="Custom Item Symbols.Revert Symbols",
        terminals=[
            term(0, "output", "Void"),  # method select-slot (never an arrow)
            term(1, "output", "Void"),  # return value (unused -> no arrow)
        ],
        invoke_row_terminal_ids=["VI::0", "VI::1"],
        bound_control_uid="12",
        bound_control_type=bool_type,
    )
    glyph = _invoke_node_glyph(node)
    assert isinstance(glyph, InvokeNodeGlyph)
    assert glyph.is_implicit is True
    assert glyph.target_name == "Test Hierarchy Tree"
    assert glyph.bar_color == wire_style(bool_type).color

    # An implicit node whose bound control's class isn't reconstructible
    # (bound_control_type is None, e.g. GTR's real "treeControl") still
    # shows the target name, just no bar — never a guessed color.
    unresolved = node.model_copy(update={"id": "VI::11388", "bound_control_type": None})
    glyph2 = _invoke_node_glyph(unresolved)
    assert isinstance(glyph2, InvokeNodeGlyph)
    assert glyph2.is_implicit is True
    assert glyph2.target_name == "Test Hierarchy Tree"
    assert glyph2.bar_color is None

    # EXPLICIT (no bound_control_uid): unaffected, same as the plain
    # class-header form.
    explicit = node.model_copy(
        update={
            "id": "VI::982",
            "label": None,
            "object_name": "VI",
            "bound_control_uid": "",
            "bound_control_type": None,
        }
    )
    glyph3 = _invoke_node_glyph(explicit)
    assert isinstance(glyph3, InvokeNodeGlyph)
    assert glyph3.is_implicit is False
    assert glyph3.target_name == ""
    assert glyph3.bar_color is None
    assert glyph3.class_name == "VI"


def test_invoke_node_glyph_draws_bar_only_when_implicit():
    """``InvokeNodeGlyph.draw()`` draws the header text (target name when
    implicit, ``⚙ <class>`` otherwise) and, ONLY for an implicit node with a
    resolved ``bar_color``, a solid color bar under the header — never for
    an explicit node, and never a guessed color when unresolved."""
    from lvkit.render.glyph import InvokeNodeGlyph
    from lvkit.render.style import DEFAULT_THEME

    bounds = (0.0, 0.0, 130.0, 68.0)

    implicit = InvokeNodeGlyph(
        method="Revert Symbols",
        rows=(),
        class_name="Tree (strict)",
        is_implicit=True,
        target_name="Test Hierarchy Tree",
        bar_color="#4a9c3e",
    )
    b1 = SvgBackend()
    implicit.draw(b1, bounds, DEFAULT_THEME)
    svg1 = b1.render(bounds)
    assert ">Test Hierarchy Tree<" in svg1
    assert "⚙" not in svg1  # target-name header, never the class gear icon
    assert 'fill="#4a9c3e"' in svg1  # the type-color bar

    explicit = InvokeNodeGlyph(
        method="Revert Symbols", rows=(), class_name="Tree (strict)", is_implicit=False,
    )
    b2 = SvgBackend()
    explicit.draw(b2, bounds, DEFAULT_THEME)
    svg2 = b2.render(bounds)
    assert "⚙ Tree (strict)" in svg2
    assert 'fill="#4a9c3e"' not in svg2

    # Implicit but unresolved bar_color: target name shows, no bar drawn.
    unresolved = InvokeNodeGlyph(
        method="Revert Symbols",
        rows=(),
        class_name="Tree (strict)",
        is_implicit=True,
        target_name="Test Hierarchy Tree",
        bar_color=None,
    )
    b3 = SvgBackend()
    unresolved.draw(b3, bounds, DEFAULT_THEME)
    svg3 = b3.render(bounds)
    assert ">Test Hierarchy Tree<" in svg3
    assert svg3.count("<rect") == 1  # the outer box only -- no bar rect drawn


def test_gtr_test_hierarchy_tree_invoke_node_renders_implicit():
    """Real-corpus check (task #51/#55 extension, reference image #72): GTR's
    "Test Hierarchy Tree" invoke node (heap uid 11387, method "Custom Item
    Symbols.Revert Symbols") renders IMPLICIT — header "Test Hierarchy Tree"
    (the bound control's own name), never the "⚙ Tree (strict)" class
    header. Its bound control (heap class ``treeControl``) isn't a class
    ``reconstruct_control_lvtype`` models, so no color bar — never a guessed
    color."""
    from lvkit.render.glyph import InvokeNodeGlyph
    from lvkit.render.scene import RenderNode, build_scene

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (
            rn
            for rn in scene.nodes
            if isinstance(rn, RenderNode) and rn.node.id.endswith("::11387")
        ),
        None,
    )
    assert target is not None, "GTR's Test Hierarchy Tree invoke node not in scene"
    assert isinstance(target.glyph, InvokeNodeGlyph)
    assert target.glyph.is_implicit is True
    assert target.glyph.target_name == "Test Hierarchy Tree"
    assert target.glyph.bar_color is None
    assert target.glyph.method == "Custom Item Symbols.Revert Symbols"


def test_explicit_invoke_node_in_corpus_keeps_class_header():
    """Real-corpus check: GTR's "FP.Open" VI-reference invoke node (heap uid
    982) is EXPLICIT (no bound-control ``<ddo>``) and renders EXACTLY as
    before — "⚙ VI" class header, no target name, no color bar."""
    from lvkit.render.glyph import InvokeNodeGlyph
    from lvkit.render.scene import RenderNode, build_scene

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (
            rn
            for rn in scene.nodes
            if isinstance(rn, RenderNode) and rn.node.id.endswith("::982")
        ),
        None,
    )
    assert target is not None, "explicit FP.Open invoke node not in scene"
    assert isinstance(target.glyph, InvokeNodeGlyph)
    assert target.glyph.is_implicit is False
    assert target.glyph.target_name == ""
    assert target.glyph.bar_color is None
    assert target.glyph.class_name == "VI"


def test_event_reg_node_glyph_draws_growable_rows_and_grow_handle():
    """``_event_reg_node_glyph``/``EventRegNodeGlyph`` (task #56): a
    Register-For-Events node draws a header naming this node's own
    heap-recorded name (never "eventRegNode", the raw class), one "event N"
    row per registered source (always a LEFT input arrow — an event source
    is registered ON, never read back), and a "▼" grow-handle on the LAST
    row. No properties -> None, so the caller falls back to the plain box."""
    from lvkit.models import Terminal
    from lvkit.render.glyph import EventRegNodeGlyph
    from lvkit.render.nodes import _event_reg_node_glyph
    from lvkit.render.style import DEFAULT_THEME

    def term(idx, direction, ut, ref_type=None):
        return Terminal(
            id=f"VI::{idx}",
            index=idx,
            direction=direction,
            lv_type=LVType(
                kind=LVTypeKind.PRIMITIVE, underlying_type=ut, ref_type=ref_type
            ),
        )

    node = PrimitiveNode(
        id="VI::700",
        vi_path="VI",
        node_type="eventRegNode",
        name="Reg Events",
        object_name="Reg Events",
        terminals=[
            term(0, "input", "Refnum", ref_type="EventReg"),
            term(1, "output", "Refnum", ref_type="EventReg"),
            term(2, "input", "Cluster"),
            term(3, "output", "Cluster"),
            term(4, "input", "Refnum", ref_type="UserEvent"),
            term(5, "input", "Refnum", ref_type="UserEvent"),
        ],
        event_row_terminal_ids=["VI::4", "VI::5"],
    )
    glyph = _event_reg_node_glyph(node)
    assert isinstance(glyph, EventRegNodeGlyph)
    assert glyph.row_count == 2
    assert glyph.class_name == "Reg Events"
    # The resolved row label is pinned onto each row's own terminal, same
    # hover-tooltip pattern as PropertyNode's property names.
    assert node.terminals[4].display_name == "event 1"
    assert node.terminals[5].display_name == "event 2"

    bounds = (0.0, 0.0, 100.0, 60.0)
    backend = SvgBackend()
    glyph.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert "⚙ Reg Events" in svg
    assert ">event 1<" in svg and ">event 2<" in svg
    assert svg.count(">▼<") == 1  # grow handle on the LAST row only

    # No growable rows -> None (falls back to the plain labeled box).
    empty = PrimitiveNode(
        id="VI::701", vi_path="VI", node_type="eventRegNode", name="Reg Events"
    )
    assert _event_reg_node_glyph(empty) is None


def test_gtr_eventreg_node_renders_with_real_name_and_row_count():
    """Real-corpus check (task #56 / reference image #68): GTR's
    "eventRegNode" (heap uid 11756) renders with its OWN heap-recorded name
    ("Reg Events", from ``<nodeName>``, never the raw "eventRegNode" class)
    and exactly 1 growable "event N" row — the real per-VI registered-event
    count from the heap's ``<dcoList>``, not a hard-coded guess."""
    from lvkit.render.glyph import EventRegNodeGlyph
    from lvkit.render.scene import RenderNode, build_scene

    loaded = _load_graph(BUILTIN_REF_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {BUILTIN_REF_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    assert scene is not None

    target = next(
        (
            rn
            for rn in scene.nodes
            if isinstance(rn, RenderNode) and rn.node.id.endswith("::11756")
        ),
        None,
    )
    assert target is not None, "GTR's eventRegNode not in scene"
    assert isinstance(target.glyph, EventRegNodeGlyph)
    assert target.glyph.class_name == "Reg Events"
    assert target.glyph.row_count == 1


def test_compact_array_terminal_brackets_element_type():
    """A COMPACT FP terminal (too small for the array index-column chrome)
    brackets the element type — "[DBL]" — so array-ness stays obvious; the
    full icon view leaves it bare (its index column already reads as brackets)."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.draw import draw_fp_terminal

    arr = LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
        dimensions=1,
    )
    t = FPTerminal(
        id="x",
        index=0,
        direction="output",
        name="data",
        lv_type=arr,
        is_indicator=False,
        control_type="stdNum",
    )

    b = SvgBackend()
    draw_fp_terminal(t, (0, 0, 16, 16), b)
    compact = b.render((0, 0, 20, 20))
    assert "[DBL]" in compact and ">DBL<" not in compact

    b2 = SvgBackend()
    draw_fp_terminal(t, (0, 0, 60, 50), b2)
    icon = b2.render((0, 0, 60, 50))
    assert ">DBL<" in icon and "[DBL]" not in icon


def test_interactive_false_drops_script_and_root_id_keeps_data_attrs():
    """``render_vi(..., interactive=False)`` (increment 1 of the diff-viewer
    plan) must carry NO ``<script>`` and NO root ``<svg id=...>`` — so two
    such SVGs can be inlined on one page with zero id collision and no
    dueling JS controllers — while the ``data-node``/``data-lv-struct``/
    ``data-lv-frames``/``data-lv-default``/``data-path`` attributes (drawn by
    ``draw.py`` during scene-drawing, not by the scripts) survive untouched.
    The default (``interactive=True``, and omitting the kwarg entirely) must
    stay byte-identical to each other — no regression to the existing
    interactive behavior."""
    loaded = _load_graph(CASE_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {CASE_VI}")
    graph, vi = loaded

    default_svg = render_vi(graph, vi)
    explicit_true_svg = render_vi(graph, vi, interactive=True)
    noninteractive_svg = render_vi(graph, vi, interactive=False)
    assert default_svg is not None and noninteractive_svg is not None
    # Omitting the kwarg must be byte-identical to passing interactive=True
    # explicitly -- no accidental change to the default rendering path.
    assert default_svg == explicit_true_svg

    assert "<script" in default_svg
    assert re.search(r"<svg[^>]*\sid=", default_svg)
    assert "<script" not in noninteractive_svg
    assert not re.search(r"<svg[^>]*\sid=", noninteractive_svg)

    for svg in (default_svg, noninteractive_svg):
        assert "data-node" in svg
        assert "data-lv-struct" in svg
        assert "data-lv-frames" in svg
        assert "data-lv-default" in svg
        assert "data-path" in svg


def test_id_less_renders_have_collision_free_clip_ids():
    """Two ``interactive=False`` SVGs inlined on ONE page (the diff viewer's
    before/after panes) must not share any ``<clipPath>`` id. If they did, the
    second pane's ``clip-path="url(#id)"`` would bind to the FIRST pane's
    clipPath and clip its structures to the wrong geometry -- the diff viewer's
    "white voids". An id-less render carries no ``root_id`` scope, so the
    backend derives the clip-id prefix from the SVG's own content; two distinct
    diagrams then get distinct prefixes. The sibling test above asserts the
    docstring's "zero id collision" claim only for the ROOT ``<svg id>`` -- it
    never checked the clip ids, which is exactly how the collision shipped."""
    a = _load_graph(CASE_VI)
    b = _load_graph(NESTED_CASE_VI)
    if a is None or b is None:
        pytest.skip("sample structure VIs not available")
    svg_a = render_vi(a[0], a[1], interactive=False)
    svg_b = render_vi(b[0], b[1], interactive=False)
    assert svg_a is not None and svg_b is not None
    ids_a = set(re.findall(r'<clipPath id="([^"]+)"', svg_a))
    ids_b = set(re.findall(r'<clipPath id="([^"]+)"', svg_b))
    # both fixtures contain structures, so each emits content-clip <defs>
    assert ids_a and ids_b, "expected both structure VIs to emit <clipPath> defs"
    # no shared id -> inlining both panes on one page cannot collide
    assert ids_a.isdisjoint(ids_b), (
        f"clip-path id collision across id-less renders: {sorted(ids_a & ids_b)}"
    )


def test_dark_palette_covers_every_wire_color():
    """Every wire_* (and other themed) color in the Theme must have a dark-mode
    entry in DARK_PALETTE, else theme_style_block() raises and the web/gallery
    render dies. Guards against adding a wire color (e.g. wire_refnum) without
    its dark variant."""
    from lvkit.render.theme_web import DARK_PALETTE, theme_style_block

    css = theme_style_block()  # raises if any themed color lacks a dark entry
    assert "--lv-wire-refnum" in css
    assert "wire_refnum" in DARK_PALETTE


def _theme_mode_graph() -> tuple[InMemoryVIGraph, str]:
    """A loaded graph for the theme-mode SVG tests, or skip if unavailable."""
    loaded = _load_graph(GROUND_TRUTH_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {GROUND_TRUTH_VI}")
    return loaded


def test_theme_mode_light_is_byte_identical_and_var_free():
    """``theme_mode='light'`` (the default) must be byte-identical to omitting
    the kwarg — the raw-hex legacy output — carrying NO css-var references and
    NO injected dark palette. This is the determinism contract."""
    graph, vi = _theme_mode_graph()
    default_svg = render_vi(graph, vi)
    light_svg = render_vi(graph, vi, theme_mode="light")
    assert default_svg is not None
    assert default_svg == light_svg  # explicit light == default
    assert "var(--lv-" not in light_svg  # no css-var recoloring
    assert "@media" not in light_svg  # no injected palette block
    assert ":root{" not in light_svg


def test_theme_mode_dark_embeds_css_var_theme_and_dark_palette():
    """``theme_mode='dark'`` renders with the css-var theme (colors become
    ``var(--lv-*, <light-hex>)``) AND embeds a ``:root`` block that sets those
    vars to the DARK_PALETTE values UNCONDITIONALLY (a standalone .svg opens
    dark, no media query)."""
    from lvkit.render.theme_web import DARK_PALETTE

    graph, vi = _theme_mode_graph()
    dark_svg = render_vi(graph, vi, theme_mode="dark")
    assert dark_svg is not None
    assert "var(--lv-" in dark_svg  # css-var theme in use
    assert ":root{" in dark_svg
    assert f"--lv-prim-fill: {DARK_PALETTE['prim_fill']};" in dark_svg
    # dark = unconditional, so NO prefers-color-scheme wrapper.
    assert "@media" not in dark_svg


def test_theme_mode_auto_wraps_dark_palette_in_media_query():
    """``theme_mode='auto'`` renders with the css-var theme and emits BOTH a
    ``@media (prefers-color-scheme: dark){ :root:not([data-theme]) }`` block
    (follow the OS/editor preference while no explicit override is set) AND a
    ``:root[data-theme="dark"]`` rule (force dark at runtime). The media rule
    is gated on ``:not([data-theme])`` so ``data-theme="light"`` falls back to
    the light css-var defaults."""
    from lvkit.render.theme_web import DARK_PALETTE

    graph, vi = _theme_mode_graph()
    auto_svg = render_vi(graph, vi, theme_mode="auto")
    assert auto_svg is not None
    assert "var(--lv-" in auto_svg
    assert "@media (prefers-color-scheme: dark)" in auto_svg
    # the media rule is gated so an explicit data-theme override wins
    assert ":root:not([data-theme])" in auto_svg
    # a forced-dark rule for data-theme="dark", outside the media query
    assert ':root[data-theme="dark"]' in auto_svg
    assert f"--lv-prim-fill: {DARK_PALETTE['prim_fill']};" in auto_svg


def test_embedded_dark_css_rejects_bad_mode():
    """The embedded-palette helper is only defined for dark/auto — 'light' (or
    anything else) must raise, since light injects nothing at all."""
    from lvkit.render.theme_web import embedded_dark_css

    with pytest.raises(ValueError):
        embedded_dark_css("light")

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
from lvkit.graph.models import (
    CaseStructureNode,
    ConstantNode,
    LocalVariableNode,
    LoopNode,
    PrimitiveNode,
    SequenceNode,
    StructureNode,
)
from lvkit.models import FPTerminal, LVType
from lvkit.render import render_vi, render_vi_file
from lvkit.render.backend import SvgBackend
from lvkit.render.draw import draw_fp_terminal, draw_node
from lvkit.render.glyph import CompoundArithGlyph
from lvkit.render.layout import Layout, Point, Rect, build_layout
from lvkit.render.nodes import _format_numeric_const, string_const_display
from lvkit.render.scene import (
    Scene,
    _exit_side,
    _format_ranges,
    _frame_compatible,
    _frame_info,
    _selector_label,
    _structure_borders,
    _trim_string_const_geom,
    build_scene,
    encode_frame_path,
)
from lvkit.render.style import DEFAULT_THEME, coercion_key, wire_style
from lvkit.render.wire_router import RouterConfig, WireRouter, _compress, path_d
from lvkit.render.wire_table import FAITHFUL_WIRE_TABLE, decode_signal

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
        kind="enum",
        underlying_type="UnitUInt16",
        values={n: EnumValue(value=i) for i, n in enumerate(names)},
    )


def test_format_ranges_single_range_and_list():
    from lvkit.models import SelectorRange

    single = [SelectorRange(start=3, end=3)]
    rng = [SelectorRange(start=3, end=10)]
    lst = [SelectorRange(start=1, end=1), SelectorRange(start=3, end=3),
           SelectorRange(start=5, end=8)]
    assert _format_ranges(single, str) == "3"
    assert _format_ranges(rng, str) == "3..10"
    assert _format_ranges(lst, str) == "1, 3, 5..8"


def test_selector_label_boolean_and_default():
    from lvkit.models import LVType

    bool_t = LVType(kind="primitive", underlying_type="Boolean")
    assert _selector_label(_frame("True"), bool_t, False) == "True"
    assert _selector_label(_frame("False"), bool_t, False) == "False"
    assert _selector_label(_frame("Default", is_default=True), bool_t, False) \
        == "Default"


def test_selector_label_enum_names_ranges_and_list():
    names = ["Digital Input", "Digital Output", "Voltage Input", "PWM"]
    t = _enum_type(names)
    assert _selector_label(_frame("3", [(3, 3)]), t, False) == "PWM"
    assert _selector_label(_frame("0", [(0, 1)]), t, False) \
        == "Digital Input..Digital Output"
    assert _selector_label(_frame("0", [(0, 0), (2, 2)]), t, False) \
        == "Digital Input, Voltage Input"


def test_selector_label_string_quoted():
    from lvkit.models import LVType

    t = LVType(kind="primitive", underlying_type="String")
    assert _selector_label(_frame("Stop"), t, False) == '"Stop"'


def test_selector_label_error_no_error_and_error():
    from lvkit.models import LVType

    t = LVType(kind="cluster")
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
        id=f"t{index}", index=index, direction="input",
        lv_type=LVType(kind="primitive", underlying_type=repr_name),
    )
    return RenderTerminal(terminal=t, center=(10.0, 10.0 * index),
                          bounds=(5.0, 10.0 * index - 4, 15.0, 10.0 * index + 4))


def _arith_node(glyph, input_reprs):
    from lvkit.graph.models import PrimitiveNode
    from lvkit.render.scene import RenderNode

    return RenderNode(
        node=PrimitiveNode(id="n1", name="op", vi="v"),
        bounds=(0.0, 0.0, 20.0, 40.0),
        glyph=glyph,
        terminals=[_num_input(i + 1, r) for i, r in enumerate(input_reprs)],
    )


def test_coercion_dot_on_compound_arith_mixed_widths():
    """Compound Arithmetic (CompoundArithGlyph) now gets a coercion dot on its
    narrower input when its numeric inputs differ in width — #11."""
    from lvkit.render.glyph import ArithGlyph, CompoundArithGlyph
    from lvkit.render.scene import _arith_coercion_dots

    cpd = _arith_node(CompoundArithGlyph("+", num_inputs=2),
                      ["NumInt32", "NumFloat64"])
    assert len(_arith_coercion_dots([cpd])) == 1

    arith = _arith_node(ArithGlyph("+"), ["NumInt32", "NumFloat64"])
    assert len(_arith_coercion_dots([arith])) == 1


def test_no_coercion_dot_when_widths_match():
    from lvkit.render.glyph import CompoundArithGlyph
    from lvkit.render.scene import _arith_coercion_dots

    cpd = _arith_node(CompoundArithGlyph("+", num_inputs=2),
                      ["NumFloat64", "NumFloat64"])
    assert _arith_coercion_dots([cpd]) == []


def test_no_coercion_dot_on_boxed_primitive():
    """A boxed primitive (WrappedBoxGlyph) is never coercion-dotted even with
    differing numeric inputs — its inputs may differ structurally (e.g. an
    index or exponent), not by coercion."""
    from lvkit.render.glyph import WrappedBoxGlyph
    from lvkit.render.scene import _arith_coercion_dots

    boxed = _arith_node(WrappedBoxGlyph("Scale By Power Of 2"),
                        ["NumFloat64", "NumInt32"])
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
        node=PrimitiveNode(id="p1", name="String Length", vi="v"),
        bounds=node_bounds, glyph=glyph, terminals=terminals,
    )


def test_primitive_glyph_sized_to_termbounds_union():
    """A thin primitive whose clickable box is padded to 32x32 is drawn at the
    union of its terminal termBounds — a thin wide rect, not a square."""
    from lvkit.render.draw import _glyph_bounds
    from lvkit.render.glyph import WrappedBoxGlyph

    node = _prim_render_node(
        WrappedBoxGlyph("String Length"),
        [_term(0, "input", (0.0, 8.0, 4.0, 12.0)),
         _term(1, "output", (28.0, 20.0, 32.0, 24.0))],
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
        [_term(0, "input", (0.0, 14.0, 4.0, 18.0)),
         _term(1, "output", (28.0, 14.0, 32.0, 18.0))],
    )
    x1, y1, x2, y2 = _glyph_bounds(node)
    assert (x1, x2) == (0.0, 32.0)
    assert y2 - y1 == _MIN_GLYPH_EXTENT   # floored, not 4px
    assert (y1 + y2) / 2 == 16.0          # centered on the terminal span


def test_primitive_with_own_aspect_icon_keeps_node_bounds():
    """A primitive drawn with real/declared art (InlineSvgGlyph) keeps its node
    bounds — only the shape/box glyphs are resized to the terminal span."""
    from lvkit.render.draw import _glyph_bounds
    from lvkit.render.glyph import InlineSvgGlyph

    node = _prim_render_node(
        InlineSvgGlyph("<g/>"),
        [_term(0, "input", (0.0, 8.0, 4.0, 12.0)),
         _term(1, "output", (28.0, 20.0, 32.0, 24.0))],
    )
    assert _glyph_bounds(node) == (0.0, 0.0, 32.0, 32.0)


def test_substring_type_colors_as_string_not_unknown():
    """A ``SubString`` primitive (e.g. Search/Split String's outputs, a shift
    register carrying one) is a string for color/glyph purposes — it must map
    to the string family/color, not fall through to grey ``unknown`` (#80)."""
    from lvkit.models import LVType
    from lvkit.render.style import DEFAULT_THEME, type_family, wire_style

    sub = LVType(kind="primitive", underlying_type="SubString")
    string = LVType(kind="primitive", underlying_type="String")
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
            terminal=None, bounds=(0.0, 0.0, 14.0, 14.0),
            glyph_kind="cond", cond_continue=cond_continue,
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
        node=VINode(id="v1", name="My SubVI", vi="v"),
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
    r = WireRouter([obstacle], bounds=(0.0, 0.0, 200.0, 120.0),
                   config=RouterConfig())
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
        obstacles, bounds=(0.0, 0.0, 200.0, 120.0), config=RouterConfig(),
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
    # Too small for labeled rows: field NAMES are dropped, but both field
    # VALUE glyphs (the _Dot "V") are still drawn — never a blank box.
    assert "Horizontal" not in small_svg and "Vertical" not in small_svg
    assert "<rect" in small_svg
    assert small_svg.count(">V<") == 2


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
        plain, (0, 0, 90, 25), DEFAULT_THEME)
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
    "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/appcontrol/"
    "appcontrol.llb/Current VIs Reference__ogtk.vi"
)
OPENG_SEARCH = [Path("samples/OpenG/extracted")]

# Small, pre-verified samples covering: plain leaf VI, SubVI calls, a Case
# structure, and a Case structure NESTED inside another structure.
CASE_VI = Path("samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi")
NESTED_CASE_VI = Path(
    "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/variantconfig/"
    "variantconfig.llb/Write Panel to INI__ogtk.vi"
)
# A case genuinely nested inside another case's frame (not just a case inside
# a loop) — its scene has 2+ segment compound frame paths.
NESTED_CASE_CONTENT_VI = Path(
    "samples/JKI-EasyXML/Source/Fast Parser/XML Loop Stack Recursion.vi"
)
# Flat (film-strip) sequence with 3 side-by-side frames (verified: frame 0/1
# node x-ranges don't overlap, has dividers, has 5 SubVI calls for the hover-
# tooltip test).
FLAT_SEQ_VI = Path(
    "samples/JKI-EasyXML/Source/Fast Parser/"
    "test TCX read (installed 71).vi"
)
# Stacked (interactive) sequence with 3 overlapping frames.
STACKED_SEQ_VI = Path(
    "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/string/"
    "string.llb/Number to Proper Engl Text__ogtk.vi"
)
CORPUS_VIS = [
    Path("samples/JKI-VI-Tester/source/Utilities/Get LV Class Members from Path.vi"),
    Path(
        "samples/JKI-EasyXML/Source/JKI Reuse Candidates/"
        "Is an Error__JKI Error Handling.vi"
    ),
    NESTED_CASE_CONTENT_VI,
    CASE_VI,
    Path("samples/JKI-VI-Tester/source/Prototype/Test Project/Method1.vi"),
    Path(
        "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/array/array.llb/"
        "Reorder 1D Array2 (LVObject)__ogtk.vi"
    ),
    Path(
        "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/comparison/"
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
        graph.load_vi(path, expand_subvis=False)
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
    graph.load_vi(CONST_LABEL_VI, expand_subvis=False, search_paths=OPENG_SEARCH)
    name = graph.resolve_vi_name(CONST_LABEL_VI.name)
    scene = build_scene(graph, name)

    by_val = {
        rn.node.value: rn
        for rn in scene.nodes
        if isinstance(rn.node, ConstantNode)
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
        CONST_LABEL_VI, expand_subvis=False, search_paths=OPENG_SEARCH,
    )
    assert svg is not None
    assert ">x2<" in svg              # #59 hex radix value survives re-extraction
    assert "Open Templates" in svg   # visible owned label rendered
    assert "Index" not in svg        # hidden auto-label NOT rendered


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
            capture_output=True, text=True, timeout=60,
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
                    (n for n in scene.nodes if n.node.id == qid), None,
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
    assert '<g class="lv-selector"' in svg
    assert "data-lv-frames=" in svg and "data-lv-default=" in svg
    # a case has a real dropdown: a hidden menu with clickable options/values
    assert 'class="lv-menu"' in svg
    assert 'class="lv-option"' in svg and "data-lv-value=" in svg
    assert 'data-lv-action="toggle"' in svg

    for raw, values in scene.frame_values.items():
        visible = hidden = 0
        for value in values:
            path_attr = f'{raw}={value}'
            pattern = (
                r'<g class="lv-frame" data-path="' + re.escape(path_attr)
                + r'"( style="([^"]*)")?>'
            )
            matches = re.findall(pattern, svg)
            assert matches, f"no lv-frame group for {path_attr}"
            for _whole, style in matches:
                if "display:none" in style:
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
        p for p in
        {n.frame_path for n in scene.nodes if len(n.frame_path) >= 2}
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
        s for s in scene.structures
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

    svg = render_vi_file(FLAT_SEQ_VI, expand_subvis=False)
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
        s for s in scene.structures
        if isinstance(s.node, SequenceNode) and s.node.node_type != "flatSequence"
        and scene.frame_values.get(s.raw_uid)
    ]
    if not stacked:
        pytest.skip("sample has no interactive stacked sequence in this scene")

    svg = render_vi(graph, vi)
    assert svg is not None
    assert 'class="lv-frame"' in svg
    assert 'class="lv-selector"' in svg
    assert "◄ ►" in svg or "►" in svg  # no ▼ (that's the case affordance)

    for structure in stacked:
        values = scene.frame_values[structure.raw_uid]
        default = scene.default_frame.get(structure.raw_uid)
        visible = hidden = 0
        for value in values:
            path_attr = f"{structure.raw_uid}={value}"
            pattern = (
                r'<g class="lv-frame" data-path="' + re.escape(path_attr)
                + r'"( style="([^"]*)")?>'
            )
            matches = re.findall(pattern, svg)
            assert matches, f"no lv-frame group for {path_attr}"
            for _whole, style in matches:
                if "display:none" in style:
                    hidden += 1
                else:
                    visible += 1
        assert visible >= 1
        # Stacked sequences open on frame 0. The heap ``dIdx`` is NOT a frame
        # index (it resolves to a diagram outside the structure's own frames),
        # so it cannot drive the initial view — see #81.
        assert default == "0"


def test_render_vi_file_determinism_across_hash_seeds_stacked_seq():
    """Same determinism guarantee as the case-VI version, extended to a VI
    with an interactive stacked sequence."""
    if not STACKED_SEQ_VI.exists():
        pytest.skip(f"sample VI not available: {STACKED_SEQ_VI}")

    script = (
        "import hashlib\n"
        "from lvkit.render import render_vi_file\n"
        f"svg = render_vi_file({str(STACKED_SEQ_VI)!r}, expand_subvis=False)\n"
        "assert svg is not None\n"
        "print(hashlib.sha256(svg.encode()).hexdigest())\n"
    )
    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, timeout=60,
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
        "from lvkit.render import render_vi_file\n"
        f"svg = render_vi_file({str(CASE_VI)!r}, expand_subvis=False)\n"
        "assert svg is not None\n"
        "print(hashlib.sha256(svg.encode()).hexdigest())\n"
    )
    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, timeout=60,
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

    Its terminals are Boolean, so LabVIEW's "add" mode is logical OR — the
    symbol is "∨", not the raw "+" (see
    codegen/nodes/compound.py::generate_compound_arith's boolean-context
    translation, mirrored by render for this same node)."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {STACKED_SEQ_VI}")
    graph, vi = loaded

    cpd_nodes = [
        n for n in graph.iter_nodes(vi)
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
    # Boolean-context operator symbol: "add" on Booleans is logical OR.
    assert ">∨<" in svg

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
        r'<circle[^>]*' + re.escape(fill) + r'[^>]*' + re.escape(stroke) + r'[^>]*/>',
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
        "from lvkit.render import render_vi_file\n"
        f"svg = render_vi_file({str(STACKED_SEQ_VI)!r}, expand_subvis=False)\n"
        "assert svg is not None\n"
        "print(hashlib.sha256(svg.encode()).hexdigest())\n"
    )
    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, timeout=60,
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
            n.bounds for n in scene.nodes
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
            s for s in scene.structures
            if _frame_compatible(s.frame_path, net.frame_path)
        ]
        for branch in net.branches:
            a, b = branch[0], branch[-1]
            blockers = [
                s for s in structs
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
                        if (bx1 + 1 < x < bx2 - 1 and by1 + 1 < y < by2 - 1
                                and not _on_border((x, y), s)):
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
    u8 = LVType(kind="primitive", underlying_type="NumUInt8")
    assert _format_numeric_const(u8, "31", "%.0x") == "x1F"
    u16 = LVType(kind="primitive", underlying_type="NumUInt16")
    assert _format_numeric_const(u16, "237", "%.0b") == "b11101101"
    i32 = LVType(kind="primitive", underlying_type="NumInt32")
    assert _format_numeric_const(i32, "2", "%.0x") == "x2"
    # No format string (LabVIEW default decimal) -> caller falls back.
    assert _format_numeric_const(i32, "31", None) is None


def test_format_numeric_const_negative_twos_complement_by_bit_width():
    # A negative value hex-displayed shows the type's own two's-complement
    # bit pattern (I16 -1 -> xFFFF), not a Python-style "-x1".
    i16 = LVType(kind="primitive", underlying_type="NumInt16")
    assert _format_numeric_const(i16, "-1", "%.0x") == "xFFFF"
    i32 = LVType(kind="primitive", underlying_type="NumInt32")
    assert _format_numeric_const(i32, "-1", "%.0x") == "xFFFFFFFF"
    # Unknown/unresolved type: can't determine the bit width to
    # two's-complement against — don't guess, fall back instead.
    assert _format_numeric_const(None, "-1", "%.0x") is None


def test_format_numeric_const_float_precision():
    dbl = LVType(kind="primitive", underlying_type="NumFloat64")
    assert _format_numeric_const(dbl, "1.9375", "%.2f") == "1.94"
    assert _format_numeric_const(dbl, "3.0", "%.1f") == "3.0"
    assert _format_numeric_const(dbl, "31", "%.0f") == "31"


def test_format_numeric_const_unrecognized_format_falls_back():
    # LabVIEW's timestamp format ('%<...>T') and other specs this function
    # doesn't understand return None rather than a guessed rendering.
    i32 = LVType(kind="primitive", underlying_type="NumInt32")
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
        assert oy1 < ny2 <= oy2                     # bottom moved up, not past heap
        cx, cy = trim_centers[raw]
        assert cx == nx2                            # terminal on the right edge
        assert ny1 <= cy <= ny2                     # ... within the shrunk box
        assert abs(cy - (ny1 + ny2) / 2) < 0.51     # ... at its vertical middle


# --------------------------------------------------------------------------- #
# P1: wire color table + coercion key (pure unit tests, no VI needed)
# --------------------------------------------------------------------------- #


def test_wire_style_covers_full_type_table():
    dbl = LVType(kind="primitive", underlying_type="NumFloat64")
    i32 = LVType(kind="primitive", underlying_type="NumInt32")
    boolean = LVType(kind="primitive", underlying_type="Boolean")
    string = LVType(kind="primitive", underlying_type="String")
    path = LVType(kind="primitive", underlying_type="Path")
    cluster = LVType(kind="cluster", fields=[])
    error_cluster = LVType(kind="cluster", typedef_name="Error Cluster", fields=[])

    assert wire_style(dbl).color == DEFAULT_THEME.wire_float
    assert wire_style(i32).color == DEFAULT_THEME.wire_int
    assert wire_style(boolean).color == DEFAULT_THEME.wire_bool
    assert wire_style(string).color == DEFAULT_THEME.wire_string
    assert wire_style(path).color == DEFAULT_THEME.wire_path
    assert wire_style(cluster).color == DEFAULT_THEME.wire_cluster
    assert wire_style(error_cluster).color == DEFAULT_THEME.wire_error
    # Distinct colors, not a table that collapsed onto one fallback.
    colors = {
        wire_style(t).color for t in
        (dbl, i32, boolean, string, path, cluster, error_cluster)
    }
    assert len(colors) == 7


def test_wire_style_array_width_scales_with_dimensions():
    scalar = LVType(kind="primitive", underlying_type="NumFloat64")
    arr_1d = LVType(kind="array", dimensions=1, element_type=scalar)
    arr_2d = LVType(kind="array", dimensions=2, element_type=scalar)

    base = wire_style(scalar).width
    # Array wires are drawn markedly bolder than the scalar element, thicker
    # still per dimension.
    assert wire_style(arr_1d).width > base
    assert wire_style(arr_2d).width > wire_style(arr_1d).width
    # Color is inherited from the element type, not a fixed array color.
    assert wire_style(arr_1d).color == wire_style(scalar).color


def test_coercion_key_ignores_provenance_but_catches_type_mismatch():
    a = LVType(kind="primitive", underlying_type="NumFloat64", description="foo")
    b = LVType(kind="primitive", underlying_type="NumFloat64", description="bar")
    c = LVType(kind="primitive", underlying_type="NumInt32")

    assert coercion_key(a) == coercion_key(b)
    assert coercion_key(a) != coercion_key(c)
    assert coercion_key(None) is None

    arr_a = LVType(kind="array", dimensions=1, element_type=a)
    arr_c = LVType(kind="array", dimensions=1, element_type=c)
    assert coercion_key(arr_a) != coercion_key(arr_c)


# --------------------------------------------------------------------------- #
# P1: front-panel control/indicator glyphs (pure unit tests via SvgBackend)
# --------------------------------------------------------------------------- #


def _render_fp_terminal(lv_type: LVType | None, is_indicator: bool = False) -> str:
    backend = SvgBackend()
    terminal = FPTerminal(
        id="vi::1", index=0, direction="input", name="X",
        lv_type=lv_type, is_indicator=is_indicator,
    )
    draw_fp_terminal(terminal, (0.0, 0.0, 40.0, 40.0), backend)
    return backend.render((0.0, 0.0, 40.0, 40.0))


def test_numeric_control_glyph_is_type_repr():
    # Real LabVIEW data-type terminals show the type name, e.g. DBL / I32.
    svg = _render_fp_terminal(LVType(kind="primitive", underlying_type="NumFloat64"))
    assert ">DBL<" in svg
    svg_int = _render_fp_terminal(LVType(kind="primitive", underlying_type="NumInt32"))
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
    arr = LVType(kind="array", dimensions=1,
                 element_type=LVType(kind="primitive", underlying_type="NumFloat64"))
    svg = _render_fp_terminal(arr)
    assert "[DBL]" not in svg
    assert ">DBL<" in svg
    assert DEFAULT_THEME.fp_index_fill in svg
    assert ">i<" in svg


def test_numeric_control_shows_value_sample():
    svg = _render_fp_terminal(LVType(kind="primitive", underlying_type="NumFloat64"))
    assert "1.23" in svg


def test_boolean_control_glyph_is_tf():
    svg = _render_fp_terminal(LVType(kind="primitive", underlying_type="Boolean"))
    assert ">TF<" in svg


def test_string_control_glyph_present():
    svg = _render_fp_terminal(LVType(kind="primitive", underlying_type="String"))
    assert "abc" in svg


def test_control_border_thicker_than_indicator_border():
    control_svg = _render_fp_terminal(
        LVType(kind="primitive", underlying_type="NumFloat64"), is_indicator=False,
    )
    indicator_svg = _render_fp_terminal(
        LVType(kind="primitive", underlying_type="NumFloat64"), is_indicator=True,
    )
    assert 'stroke-width="3.0"' in control_svg
    assert 'stroke-width="1.5"' in indicator_svg


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
        None, center, structure_bounds, border=True, toward=outside_pt,
    )
    assert normal_out == (0.0, -1.0)


# --------------------------------------------------------------------------- #
# P1: structure border-terminal glyphs (N/i/cond guaranteed by loop_type)
# --------------------------------------------------------------------------- #


def test_for_loop_guarantees_N_and_i_glyphs_from_layout_geometry():
    node = LoopNode(
        id="vi::43", vi="vi", node_type="forLoop", loop_type="forLoop", terminals=[],
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
        id="vi::7", vi="vi", node_type="whileLoop", loop_type="whileLoop",
        terminals=[],
    )
    layout = Layout(
        border_terminals={
            "i_uid": (0.0, 0.0, 10.0, 10.0), "cond_uid": (20.0, 0.0, 30.0, 10.0),
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
        s for s in scene.structures
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

    i32 = LVType(kind="primitive", underlying_type="NumInt32")
    dbl = LVType(kind="primitive", underlying_type="NumFloat64")
    arr_dbl = LVType(kind="array", dimensions=1, element_type=dbl)
    string = LVType(kind="primitive", underlying_type="String")

    def coerces(a, b):
        ra, rb = numeric_repr(a), numeric_repr(b)
        return ra is not None and rb is not None and ra != rb

    assert coerces(i32, dbl)          # real coercion
    assert not coerces(dbl, dbl)      # same type
    assert not coerces(arr_dbl, dbl)  # auto-index (array->element) is NOT coercion
    assert not coerces(string, dbl)   # non-numeric, no dot


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
        b, (0.0, 0.0, 32.0, 32.0), DEFAULT_THEME,
    )
    svg = b.render((0.0, 0.0, 32.0, 32.0))
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert len([t for t in texts if t]) >= 3          # wrapped to multiple lines
    assert "…" not in "".join(texts)             # full name, no truncation
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
    # to be.
    assert re.search(r'<g class="lv-node" data-node="[^"]*">\s*<title>Parse XML', svg)


def test_node_tooltip_includes_doc_url_for_resolved_primitive():
    """A primitive that resolves to an NI docs page surfaces that URL as the
    last line of its hover <title> (task #67). Add (1050) has a doc_url."""
    from lvkit.render.draw import _node_doc_url, _node_tooltip
    add = PrimitiveNode(id="a", vi="V", name="Add", prim_id=1050, terminals=[])
    url = _node_doc_url(add)
    assert url is not None and url.endswith("/functions/add.html")
    tip = _node_tooltip(add)
    assert tip is not None and url in tip.splitlines()


def test_node_tooltip_has_no_doc_url_when_unresolved():
    """An unknown primitive (no catalog page) adds no URL line."""
    from lvkit.render.draw import _node_doc_url
    unknown = PrimitiveNode(id="u", vi="V", name="mystery", prim_id=999999,
                            terminals=[])
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
    "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/string/"
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
            id=f"t{i}", index=i, direction=d,
            nmux_role=roles[i] if i < len(roles) else None,
        )
        for i, d in enumerate(dirs)
    ]
    return PrimitiveNode(
        id="n0", vi="V", name=name, node_type=node_type, terminals=terms,
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
        "mux", "Bundle",
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
        "demux", "Unbundle",
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
        "demux", "Unbundle", dirs=("input", "output"), roles=("agg", "list"),
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, UnbundleGlyph)
    assert glyph.num_fields == 1


def test_pass_through_mux_is_not_a_bundle_glyph():
    """A 1-in/1-out mux with NO field (list) terminals — both terminals are
    the aggregate — is a structure-boundary pass-through, not an
    assemble/disassemble; it must NOT get a Bundle/Unbundle glyph."""
    from lvkit.render.glyph import BundleGlyph, UnbundleGlyph
    from lvkit.render.nodes import resolve_glyph
    node = _prim(
        "nMux", "Bundle/Unbundle By Name",
        dirs=("input", "output"), roles=("agg", "agg"),
    )
    glyph = resolve_glyph(node, _ctx())
    assert not isinstance(glyph, (BundleGlyph, UnbundleGlyph))


def test_comparison_primitives_use_the_arith_triangle():
    """The comparison functions reuse the borderless arithmetic triangle with
    their own symbol (same shape LabVIEW draws them with)."""
    from lvkit.render.glyph import ArithGlyph
    from lvkit.render.nodes import resolve_glyph
    expected = {
        "Equal?": "=", "Not Equal?": "≠", "Greater?": ">", "Less?": "<",
        "Greater Or Equal?": "≥", "Less Or Equal?": "≤",
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
        id="c1", vi="v", value=full,
        lv_type=LVType(kind="primitive", underlying_type="String"),
    )
    rn = RenderNode(node=node, bounds=(0.0, 0.0, 40.0, 16.0),
                    glyph=ConstantGlyph(full, "#000000", multiline=True))
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
        b, bounds, DEFAULT_THEME,
    )
    svg = b.render(bounds)
    for name in ("level", "parent name", "xml index"):
        assert name in svg
    assert "▶" in svg  # By Name carries the same direction arrow as positional


def test_bundle_by_name_arrow_side_follows_direction():
    """The arrow cell sits on the cluster side: RIGHT for Bundle By Name
    (fields in -> cluster out), LEFT for Unbundle By Name (task #89)."""
    from lvkit.render.glyph import BundleByNameGlyph
    bounds = (0.0, 0.0, 100.0, 40.0)

    def arrow_x(bundling: bool) -> float:
        b = SvgBackend()
        BundleByNameGlyph(names=("a", "b"), bundling=bundling).draw(
            b, bounds, DEFAULT_THEME)
        m = re.search(r'<text x="([\d.]+)"[^>]*>▶</text>', b.render(bounds))
        assert m is not None
        return float(m.group(1))

    assert arrow_x(True) > 50.0    # Bundle By Name -> arrow on the RIGHT half
    assert arrow_x(False) < 50.0   # Unbundle By Name -> arrow on the LEFT half


def test_nmux_renders_as_bundle_by_name_and_skips_boundary_mux():
    """nMux with named fields renders as a By-Name box with the field names
    resolved from the wired cluster type; a 1-in/1-out nMux boundary muxer is
    still skipped (no box)."""
    from lvkit.render.glyph import BundleByNameGlyph
    from lvkit.render.scene import _is_boundary_mux

    loaded = _load_graph(Path("samples/JKI-EasyXML/Source/Fast Parser/"
                              "XML Loop Stack Recursion.vi"))
    if loaded is None:
        pytest.skip("sample VI not available")
    graph, vi = loaded
    nmux = {n.id.split("::")[-1]: n for n in graph.iter_nodes(vi)
            if getattr(n, "node_type", None) == "nMux"}
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

    testresult_dir = Path("samples/JKI-VI-Tester/source/Classes/TestResult")
    vi_path = testresult_dir / "GetTestsRun.vi"
    if not vi_path.exists():
        pytest.skip("sample VI not available")

    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), search_paths=[testresult_dir.parent])
    vi_name = "TestResult.lvclass:GetTestsRun.vi"

    nmux = [n for n in graph.iter_nodes(vi_name)
            if getattr(n, "node_type", None) == "nMux"]
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
        id="vi::case1", vi="vi", name="Case", node_type="select",
        frames=frames, selector_terminal=None,
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
    from lvkit.render.draw import draw_structure
    from lvkit.render.scene import RenderStructure
    node = CaseStructureNode(
        id="vi::c", vi="vi", name="Case", node_type="select",
        frames=[], case_insensitive=case_insensitive,
    )
    struct = RenderStructure(node=node, bounds=(0.0, 0.0, 120.0, 90.0),
                             raw_uid="c")
    scene = Scene(bounds=(0.0, 0.0, 120.0, 90.0), structures=[struct])
    backend = SvgBackend()
    draw_structure(struct, scene, backend)
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
    outer = TunnelTerminal(id="vi::out", index=0, direction="output",
                           tunnel_type="csTun", boundary="outer")
    inners = [
        TunnelTerminal(id=f"vi::in_{f}", index=0, direction="input",
                       tunnel_type="csTun", boundary="inner",
                       paired_id="vi::out", frame=f)
        for f in frames
    ]
    node = CaseStructureNode(id="vi::c", vi="vi", name="Case", node_type="select",
                             terminals=[outer, *inners])
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
            terminal=None, bounds=(0.0, 0.0, 12.0, 12.0), glyph_kind="tunnel",
            color="#e05fa0", unwired_frames=frozenset(unwired_frames),
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
        (-2294.5, 2156.5), (-2272.0, 2325.0), (-2273.0, 2428.0), (-2322.0, 2661.0),
        (-2231.5, 916.5), (-2274.0, 2742.0), (-2147.5, 2930.5), (-2351.5, -389.5),
        (-2267.0, 1848.0), (-2384.0, 1114.0), (-1740.5, -570.5),
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
        (-59.5, 1368.5), (648.0, 1933.0), (196.0, 2315.0),
        (203.0, 2597.0), (648.0, 1986.0), (194.0, 1833.0),
    ]
    from lvkit.render.wire_table import _decode_tree_deterministic

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


def test_layout_wire_by_uid_drives_faithful_render():
    # End-to-end: every drawn wire's faithful geometry is keyed by its sink uid,
    # and the whole VI renders WITHOUT invoking the auto-router (deterministic,
    # heap-faithful). Regression guard for the unified uid-driven decoder (#76).
    from lvkit.render import scene as scene_mod
    from lvkit.render.layout import build_layout

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
        svg = render_vi_file(NESTED_CASE_VI, expand_subvis=False)
    finally:
        scene_mod.WireRouter.route = orig
    assert svg is not None
    assert calls[0] == 0  # fully faithful: no router fallback for this VI


def test_mux_doc_url_resolves_by_field_direction():
    """A direction-polymorphic cluster-mux node links by its RESOLVED display
    name, not its ambiguous raw name: fields-out -> Unbundle By Name,
    fields-in -> Bundle By Name (task #67)."""
    from lvkit.render.draw import _node_doc_url
    unbundle = _prim("nMux", "Bundle/Unbundle By Name",
                     dirs=("input", "output", "output"),
                     roles=("agg", "list", "list"))
    assert _node_doc_url(unbundle).endswith("/functions/unbundle-by-name.html")
    bundle = _prim("nMux", "Bundle/Unbundle By Name",
                   dirs=("output", "input", "input"),
                   roles=("agg", "list", "list"))
    assert _node_doc_url(bundle).endswith("/functions/bundle-by-name.html")


def test_node_type_flavor_carries_doc_url():
    """resolve_by_node_type surfaces doc_url so node-type primitives (no
    primResID: Build Array, Compound Arithmetic, ...) link to NI docs (#67)."""
    from lvkit.primitive_resolver import get_resolver
    r = get_resolver()
    assert r.resolve_by_node_type("aBuild").doc_url.endswith(
        "/functions/build-array.html")
    assert r.resolve_by_node_type("cpdArith").doc_url.endswith(
        "/functions/compound-arithmetic.html")

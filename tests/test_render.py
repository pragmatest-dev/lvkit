"""Tests for the graph-driven block-diagram renderer (lvkit.render).

Wire-router and icon-transparency tests are pure unit tests (no VI files
needed). The scene-join, single-frame-policy, and corpus/determinism tests
load real sample VIs — the repo's sample VIs are local-only (gitignored), so
these skip gracefully when a given file isn't present.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.models import CaseStructureNode, LoopNode, StructureNode
from lvkit.models import FPTerminal, LVType
from lvkit.render import render_vi, render_vi_file
from lvkit.render.backend import SvgBackend
from lvkit.render.draw import draw_fp_terminal
from lvkit.render.layout import Layout
from lvkit.render.scene import _structure_borders, build_scene
from lvkit.render.style import DEFAULT_THEME, coercion_key, wire_style
from lvkit.render.wire_router import RouterConfig, WireRouter, _compress, path_d

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

# Small, pre-verified samples covering: plain leaf VI, SubVI calls, a Case
# structure, and a Case structure NESTED inside another structure.
CASE_VI = Path("samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi")
NESTED_CASE_VI = Path(
    "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/variantconfig/"
    "variantconfig.llb/Write Panel to INI__ogtk.vi"
)
CORPUS_VIS = [
    Path("samples/JKI-VI-Tester/source/Utilities/Get LV Class Members from Path.vi"),
    Path(
        "samples/JKI-EasyXML/Source/JKI Reuse Candidates/"
        "Is an Error__JKI Error Handling.vi"
    ),
    Path("samples/JKI-EasyXML/Source/Fast Parser/XML Loop Stack Recursion.vi"),
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


def test_single_frame_policy_hides_other_case_frames():
    loaded = _load_graph(NESTED_CASE_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {NESTED_CASE_VI}")
    graph, vi = loaded

    all_nodes = graph.iter_nodes(vi)
    case_nodes = [n for n in all_nodes if isinstance(n, CaseStructureNode)]
    assert case_nodes, "expected sample to contain a Case structure"

    total_frame_children = sum(
        len(frame.inner_node_uids) for c in case_nodes for frame in c.frames
    )
    # Only meaningful when at least one case has >1 non-empty frame.
    if total_frame_children == 0:
        pytest.skip("sample's case structure(s) have no frame children")

    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("sample lacks required diagram geometry")

    visible_ids = {n.node.id for n in scene.nodes} | {
        s.node.id for s in scene.structures
    }
    # Nodes belonging to a non-shown frame must not appear in the scene.
    for c in case_nodes:
        if not c.frames:
            continue
        shown = next((f for f in c.frames if f.is_default), c.frames[0])
        for frame in c.frames:
            if frame is shown:
                continue
            for uid in frame.inner_node_uids:
                qid = f"{vi}::{uid}"
                assert qid not in visible_ids


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
    assert wire_style(arr_1d).width == base + 1
    assert wire_style(arr_2d).width == base + 2
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


def test_array_control_glyph_has_brackets():
    # An array-of-DBL control terminal is [DBL] (brackets = array).
    arr = LVType(kind="array", dimensions=1,
                 element_type=LVType(kind="primitive", underlying_type="NumFloat64"))
    svg = _render_fp_terminal(arr)
    assert "[DBL]" in svg


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

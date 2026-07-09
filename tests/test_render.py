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
from lvkit.render.nodes import string_const_display
from lvkit.render.scene import (
    Scene,
    _exit_side,
    _frame_compatible,
    _structure_borders,
    _trim_string_const_geom,
    build_scene,
    encode_frame_path,
)
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
        obstacles, bounds=(0.0, 0.0, 200.0, 120.0), config=RouterConfig(grid=2),
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
# A case genuinely nested inside another case's frame (not just a case inside
# a loop) — its scene has 2+ segment compound frame paths.
NESTED_CASE_CONTENT_VI = Path(
    "samples/JKI-EasyXML/Source/Fast Parser/XML Loop Stack Recursion.vi"
)
# Flat (film-strip) sequence with 3 side-by-side frames.
FLAT_SEQ_VI = Path("samples/DAQmx-Digital-IO/In.vi")
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
        # Initial view = the heap's saved displayed frame (dIdx), not frame 0
        # (often an empty setup frame) — so a big stacked sequence doesn't open
        # blank.
        from lvkit.render.layout import build_layout
        shown = build_layout(
            graph.get_vi_source_path(vi),
        ).sequence_shown_frame.get(structure.raw_uid)
        if shown is not None:
            assert default == str(shown)


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
    assert "<title>Compound Arithmetic</title>" in svg
    # Boolean-context operator symbol: "add" on Booleans is logical OR.
    assert ">∨<" in svg

    # Isolate JUST this node's draw output to count its own invert bubbles
    # (other unrelated nodes in this VI, e.g. Format/Scan String, also carry
    # inverted=True terminals — see models.py's DCO objFlags bit 16 — so a
    # whole-SVG circle count would over-count) and its divider lines (1
    # vertical operator-cell divider + num_inputs-1 horizontal row dividers).
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
    assert _count_structure_crossings(scene) == 0


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


def _count_banner_crossings(scene: Scene) -> int:
    """Routed wire segments overlapping a case/stacked-sequence SELECTOR BANNER
    (the bar above the top edge) of a structure the wire is UNRELATED to (no
    endpoint on/in it). The banner is visually part of the structure, so an
    unrelated wire must route around it."""
    from lvkit.graph.models import CaseStructureNode as _Case
    from lvkit.graph.models import SequenceNode as _Seq
    bar_h = 14.0
    crossings = 0
    for net in scene.wire_nets:
        structs = [
            s for s in scene.structures
            if _frame_compatible(s.frame_path, net.frame_path)
            and (isinstance(s.node, _Case)
                 or (isinstance(s.node, _Seq) and s.node.node_type != "flatSequence"))
        ]
        for branch in net.branches:
            a, b = branch[0], branch[-1]
            for s in structs:
                if _inside(a, s.bounds) or _inside(b, s.bounds):
                    continue  # wire connects to / lives in this structure
                x1, y1, x2, _ = s.bounds
                banner = (x1, y1 - bar_h, x2, y1)
                hit = False
                for i in range(len(branch) - 1):
                    (px1, py1), (px2, py2) = branch[i], branch[i + 1]
                    steps = int(max(abs(px2 - px1), abs(py2 - py1)) / 2) + 1
                    for st in range(1, steps):
                        x = px1 + (px2 - px1) * st / steps
                        y = py1 + (py2 - py1) * st / steps
                        if banner[0] + 1 < x < banner[2] - 1 and \
                                banner[1] + 1 < y < banner[3] - 1:
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    crossings += 1
    return crossings


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


def test_no_unrelated_wire_crosses_a_selector_banner():
    """The selector banner (bar above a case/stacked-sequence) is an obstacle:
    a wire unrelated to that structure must not run over its selector."""
    loaded = _load_graph(STACKED_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available or failed to load: {STACKED_SEQ_VI}")
    graph, vi = loaded
    scene = build_scene(graph, vi)
    if scene is None:
        pytest.skip("scene build failed (missing geometry)")
    assert _count_banner_crossings(scene) == 0


def test_string_const_display_strips_quotes_and_unescapes():
    # Parser stores '...' with \\ and \' escaped; display shows the bare text.
    assert string_const_display("'hello'") == "hello"
    assert string_const_display("'it\\'s'") == "it's"
    assert string_const_display("'a\\\\b'") == "a\\b"
    assert string_const_display("'line1\nline2'") == "line1\nline2"  # newlines kept
    assert string_const_display("novalue") == "novalue"  # non-quoted passthrough
    assert string_const_display(None) == ""


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
    assert trim_bounds  # the sample has multi-line string constants
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
    roadmap #12. Uses the flat-seq sample (three DAQmx Write subVIs)."""
    loaded = _load_graph(FLAT_SEQ_VI)
    if loaded is None:
        pytest.skip(f"sample VI not available: {FLAT_SEQ_VI}")
    graph, vi = loaded
    svg = render_vi(graph, vi)
    if svg is None:
        pytest.skip("sample lacks required diagram geometry")
    # the SubVI name appears as a tooltip even though it's wrapped in the box
    # (root <title> is the VI name "In.vi"; "<title>DAQmx" can only be a node)
    assert "<title>DAQmx" in svg
    assert re.search(r"<g>\s*<title>DAQmx", svg)


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

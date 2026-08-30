"""Regression tests over the committed issue-reproduction corpus.

Each test renders / describes / inspects a minimal repro that a user attached to
a GitHub issue (kept under ``tests/corpus/issues/<N>/``, Apache-2.0 — see that
dir's README) and asserts the corrected behaviour, so a fixed bug stays fixed.
These fixtures are IN-REPO, so unlike the ``needs_samples`` corpus tests they
always run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import (
    CaseStructureNode,
    DisableStructureNode,
    LabelNode,
    LoopNode,
    SequenceNode,
)
from lvkit.models import DisableStructureKind, TunnelMode, TunnelTerminal
from lvkit.render import render_vi_file
from lvkit.render.scene import _frame_info, _structure_borders
from tests.helpers import normalize_render_ids, transitive_closure

_CORPUS = Path(__file__).resolve().parent / "corpus" / "issues"


def _fixture_vis() -> list:
    """Every ``.vi`` under the committed issue corpus, paired with its issue
    directory (used as the search-path root so a multi-VI project resolves)."""
    params = []
    for issue_dir in sorted(p for p in _CORPUS.iterdir() if p.is_dir()):
        for vi in sorted(issue_dir.rglob("*.vi")):
            params.append(pytest.param(vi, issue_dir, id=f"{issue_dir.name}-{vi.stem}"))
    return params


def _load(rel: str) -> tuple[InMemoryVIGraph, str]:
    vi = _CORPUS / rel
    graph = InMemoryVIGraph()
    graph.load_vi(vi, mode=LoadMode.MINIMAL, search_paths=[vi.parent], layout=True)
    return graph, graph.resolve_vi_name(vi.name)


def test_issue29_web_closure_render_matches_full_tree(tmp_path: Path) -> None:
    """The web (Pyodide) extension stages only a VI's dependency CLOSURE into
    ``/proj`` before rendering — not the whole workspace — so that closure must
    contain everything the render reads, or the web render degrades vs desktop.

    Issue 29's ``Test.vi`` references two same-named ``Do.vi`` (in ``Lib1`` and
    ``Lib2``) via ``LinkSavePathRef`` but has an EMPTY SubVI call table, so a
    closure keyed off the call table (``subvi_qualified_names``) misses them and
    draws the SubVIs as bare boxes. Assert that rendering from the MINIMAL
    dependency closure alone is byte-identical (content) to rendering from the
    full tree.
    """
    root = (_CORPUS / "29" / "Test LVKit").resolve()
    entry = root / "Lib2" / "Class" / "Test.vi"

    closure = transitive_closure(entry, root)
    assert "Do.vi" in {p.name for p in closure}, (
        f"closure {sorted(p.name for p in closure)} missing the referenced Do.vi "
        "— the web render would understage vs desktop"
    )

    # Stage ONLY {entry} + its closure into a temp /proj-like tree.
    for src in [entry, *closure]:
        dst = tmp_path / src.resolve().relative_to(root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())

    svg_closure = render_vi_file(
        tmp_path / entry.relative_to(root), search_paths=[tmp_path]
    )
    svg_full = render_vi_file(entry, search_paths=[root])
    assert svg_closure and svg_full, "expected both renders to produce a diagram"

    assert normalize_render_ids(svg_closure) == normalize_render_ids(svg_full), (
        "web closure render differs from the full-tree (desktop) render — "
        "the MINIMAL dependency closure understaged this VI's dependencies"
    )


def test_issue36_bundle_unbundle_and_waveform_field_names():
    """#36: every named bundle/unbundle / component drawer resolves a real field
    name — no bracketed-index fallbacks.

    (A) A drawer selecting cluster field index 0 has its ``<i>``/``<index>``
    element OMITTED by LabVIEW; lvkit used to fall back to the drawer's list
    position, so the reporter's ``Name`` field (index 0, at list slot 2) rendered
    as a second ``size``. (B) Waveform / digital-data component nodes resolve
    their built-in component names (``t0/dt/Y/attributes``, ``data/transitions``)
    from the MeasureData flavor instead of showing ``[N]`` indices.
    """
    graph, vi = _load("36/bundle-unbundle-names.vi")
    resolved: set[str] = set()
    unresolved: list[tuple[str | None, int | None]] = []
    for node in graph.iter_nodes(vi):
        nt = str(getattr(node, "node_type", None))
        if nt not in ("nMux", "decomposeClusterNode"):
            continue
        for term in node.terminals:
            if getattr(term, "nmux_role", None) != "list":
                continue
            if term.display_name:
                resolved.add(term.display_name)
            else:
                unresolved.append((node.name, term.nmux_field_index))
    # (A) the custom cluster's index-0 'Name'; (B) the waveform + digital-data
    # component names — all resolved, none left as an index fallback.
    expected = {"Name", "size", "t0", "dt", "Y", "attributes", "data", "transitions"}
    assert expected <= resolved, f"missing field names {expected - resolved}"
    assert not unresolved, f"drawers left as an index fallback: {unresolved}"


def test_issue30_case_and_sequence_open_on_saved_visible_frame():
    """#30: a Case Structure and a Stacked Sequence saved with a non-first frame
    showing must open on that frame, not frame 0.

    The displayed frame is the structure node's heap ``dIdx`` when it is a valid
    local index (``0 <= dIdx < n_frames``); an out-of-range ``dIdx`` is a legacy
    global-diagram ordinal and is rejected (frame-0 fallback). Both structures in
    this repro carry ``dIdx=1`` (their 2nd frame).
    """
    graph, vi = _load("30/visible-frame-case-sequence.vi")
    seen: dict[str, int | None] = {}
    for node in graph.iter_nodes(vi):
        if isinstance(node, CaseStructureNode):
            seen["case"] = node.displayed_frame
        elif isinstance(node, SequenceNode) and node.node_type != "flatSequence":
            seen["sequence"] = node.displayed_frame
    assert seen == {"case": 1, "sequence": 1}, seen


def test_issue30_conditional_disable_opens_on_saved_visible_frame():
    """#30 (issue's second comment): a Conditional Disable Structure saved while
    viewing a non-enabled frame must open on that SAVED VISIBLE frame (heap
    ``dIdx``), not the enabled subdiagram.

    Both structures here carry ``dIdx=1`` (their 2nd frame). One (uid ending 42)
    has that frame *disabled* in the IDE (``RUN_TIME_ENGINE==True``) with no
    ``activeDiag`` — before the fix it fell back to frame 0. The visible frame
    (``dIdx``) is now preferred over the enabled one.
    """
    graph, vi = _load("30/visible-frame-conditional-disable.vi")
    nodes = [n for n in graph.iter_nodes(vi) if isinstance(n, DisableStructureNode)]
    assert len(nodes) == 2
    assert all(n.displayed_frame == 1 for n in nodes), [
        n.displayed_frame for n in nodes
    ]
    # The renderer opens both on frame index 1 (the saved visible frame).
    default_frame, frame_values, _, _ = _frame_info(
        list(graph.iter_nodes(vi)), vi, graph
    )
    for n in nodes:
        raw = n.id.split("::")[-1]
        shown = default_frame[raw]
        assert frame_values[raw].index(shown) == 1, (raw, shown)


def test_issue37_tunnel_wires_have_no_subpixel_jog():
    """#37: a wire LabVIEW drew straight through a structure's border tunnel must
    RENDER straight -- our auto-router's lane pass must not bend it.

    The repro wires ``Numeric`` (I32) straight through a For Loop's input and
    output tunnels to ``Numeric 2``. The tunnels' 9px-tall termBounds centre at
    y.5 while the front-panel terminals' 16px-tall bounds centre at y.0 -- a real
    0.5px offset baked into the VI. LabVIEW draws the wire straight across it;
    lvkit used to snap the decoded-straight wire orthogonal and inject a mid-Z
    jog at each tunnel (the reporter's two circled bends). Decoded wires now
    bypass the auto-route lane pass ENTIRELY, and even the fallback router no
    longer bends a wire to bridge a sub-pixel offset.
    """
    from lvkit.render import build_scene

    graph, vi = _load("37/structure-tunnel-wire-bends.vi")
    scene = build_scene(graph, vi)
    assert scene is not None

    def _sub_pixel_jogs(branch: list[tuple[float, float]]):
        # A jog is an interior vertex whose SHORTER adjacent segment is under a
        # pixel -- the exact signature of a wire bent to bridge a sub-pixel gap.
        out = []
        for i in range(1, len(branch) - 1):
            a, b, c = branch[i - 1], branch[i], branch[i + 1]
            la = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            lc = ((c[0] - b[0]) ** 2 + (c[1] - b[1]) ** 2) ** 0.5
            if 0 < min(la, lc) < 1.0:
                out.append((a, b, c))
        return out

    branches = [br for net in scene.wire_nets for br in net.branches]
    assert branches, "the repro really does have wires"
    jogs = [seg for br in branches for seg in _sub_pixel_jogs(br)]
    assert not jogs, f"wires bent by a sub-pixel jog (issue #37): {jogs}"


def test_issue33_enum_coerces_like_its_underlying_int():
    """#33: an enum wired to a differently-sized numeric input takes a coercion
    dot, exactly like its underlying unsigned int would.

    The repro wires a U16 ``Enum`` and a plain ``U32`` into two ``Initialize
    Array`` ``dimension size`` inputs (``I32``). The U32 kept its dot but the
    enum silently dropped it — ``numeric_repr`` treated an enum as non-numeric.
    The decision keys off the UNDERLYING width (``UnitUInt16`` -> ``NumUInt16``),
    NOT "enums always coerce": a same-width enum would not dot. Here U16 != I32,
    so both wires dot (the "not consistent" bug).
    """
    from lvkit.models import LVType, LVTypeKind
    from lvkit.render import build_scene
    from lvkit.render.style import numeric_repr

    # The fix's core: an enum reports its underlying integer's numeric repr.
    enum_u16 = LVType(kind=LVTypeKind.ENUM, underlying_type="UnitUInt16")
    assert numeric_repr(enum_u16) == "NumUInt16"
    # ...so a same-width target would NOT coerce, a different width WOULD.
    assert numeric_repr(enum_u16) != numeric_repr(
        LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    )

    # And the repro renders the dot: the enum-sourced wire into the I32 size
    # input carries a coercion dot, just like the U32-sourced one (consistency is
    # the whole bug).
    graph, vi = _load("33/coercion-dot-consistency.vi")
    scene = build_scene(graph, vi)
    assert scene is not None
    enum_dotted: list[bool] = []
    uint_dotted: list[bool] = []
    for net in scene.wire_nets:
        if net.source is None:
            continue
        lt = getattr(graph.get_terminal(net.source.source.terminal_id), "lv_type", None)
        if lt is None:
            continue
        if lt.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
            enum_dotted.append(bool(net.coercion_dots))
        elif numeric_repr(lt) == "NumUInt32":  # the plain U32 control
            uint_dotted.append(bool(net.coercion_dots))
    assert enum_dotted and all(enum_dotted), "enum wire into I32 size input has no dot"
    assert uint_dotted and all(uint_dotted), "U32 wire regressed (no coercion dot)"


def test_issue32_free_labels_are_modeled_and_rendered():
    """#32 (Phase 1): a block-diagram free label (comment) is a first-class
    ``LabelNode`` — carried in the graph, surfaced by ``describe`` (so its prose
    reaches an agent), and rendered at its own bounds in zPlaneList paint order.

    The repro has two "Hello World!" free labels (and a rectangle + arrow
    decoration — decorations are Phase 2, still unrendered). Each label's heap
    uid is already in ``Layout.z_order`` (the zPlaneList walk records it), so it
    interleaves with real nodes in LabVIEW's own back-to-front order for free.
    """
    from lvkit.graph.describe import describe_vi

    graph, vi = _load("32/comments-and-decorations.vi")
    labels = [n for n in graph.iter_nodes(vi) if isinstance(n, LabelNode)]
    assert len(labels) == 2
    assert all(lbl.text == "Hello World!" for lbl in labels), [
        lbl.text for lbl in labels
    ]

    # Geometry + z-order come from layout, keyed by the label's heap uid.
    layout = graph.get_layout(vi)
    assert layout is not None
    for lbl in labels:
        raw = lbl.id.rsplit("::", 1)[-1]
        assert raw in layout.node_bounds, f"label {raw} has no bounds"
        assert raw in layout.z_order, f"label {raw} missing from z_order"

    # Rendered: both label texts appear in the SVG.
    vi_path = _CORPUS / "32" / "comments-and-decorations.vi"
    svg = render_vi_file(vi_path, search_paths=[vi_path.parent])
    assert svg is not None and svg.count("Hello World!") == 2

    # Semantic: the comment text is surfaced for an agent reading the VI.
    described = describe_vi(graph, vi)
    assert "Hello World!" in described


def test_transparent_free_label_renders_no_box():
    """A LabVIEW transparent free label (alpha byte ``01`` — e.g. ``01000000`` /
    ``0100000A``) renders as TEXT ONLY, no background box.

    Regression: the alpha flag was stripped and the meaningless trailing RGB
    (usually ``000000``) was painted as a solid BLACK rectangle over the diagram
    and even over wires (seen on real JKI/OpenG VIs full of transparent
    comments). Opaque colored labels (alpha ``00``) still keep their box.
    """
    from lvkit.render.glyphs.nodes.label import LabelGlyph
    from lvkit.render.style import DEFAULT_THEME

    def fill(bg: str) -> str | None:
        return LabelGlyph(text="x", bg_color=bg)._css_fill(DEFAULT_THEME)

    assert fill("00FFFEA0") == "#FFFEA0"  # opaque colored → keeps its box
    assert fill("01000000") is None  # transparent → no box (was solid black)
    assert fill("0100000A") is None  # transparent over a wire


def test_own_colored_label_keeps_dark_text():
    """A free label with its OWN opaque colour is a LIGHT swatch whatever the
    diagram theme, so its text stays a FIXED dark — following ``theme.text`` (light
    in dark mode) made the peach "Subroutine Priority" label invisible. Only a
    transparent label (text over the diagram) or the neutral-canvas fallback
    follows the theme."""
    from lvkit.render.glyphs.nodes.label import _LABEL_OWN_TEXT, LabelGlyph
    from lvkit.render.style import DEFAULT_THEME

    def text_fill(bg: str | None) -> str:
        return LabelGlyph(text="x", bg_color=bg)._text_fill(DEFAULT_THEME)

    assert text_fill("00FFFEA0") == _LABEL_OWN_TEXT  # own opaque colour → fixed dark
    assert text_fill("01000000") == DEFAULT_THEME.text  # transparent → follow theme
    assert text_fill(None) == DEFAULT_THEME.text  # no colour (canvas) → follow theme


def test_issue32_decorations_are_rendered():
    """#32 (Phase 2): block-diagram decorations (cosm shapes) render as clean-room
    glyphs — z-ordered with nodes, but never graph nodes. The repro has a Flat
    Frame (ImageResID -35) and a Thick Line with Arrow (-502).
    """
    from lvkit.render import build_scene

    graph, vi = _load("32/comments-and-decorations.vi")
    scene = build_scene(graph, vi)
    assert scene is not None
    # Decorations are NOT graph nodes (they carry no meaning).
    from lvkit.graph.models import LabelNode  # (labels ARE nodes; decos are not)

    assert not any(
        type(n).__name__ in ("FrameGlyph", "ArrowGlyph") for n in graph.iter_nodes(vi)
    )
    kinds = sorted(type(d.glyph).__name__ for d in scene.decorations)
    assert kinds == ["ArrowGlyph", "FrameGlyph"], kinds
    # Each decoration interleaves with nodes by z-order (its uid has a rank).
    for d in scene.decorations:
        assert d.dom_id in scene.z_order, d.dom_id
    # A label is still a graph node (Phase 1) — the two model paths coexist.
    assert any(isinstance(n, LabelNode) for n in graph.iter_nodes(vi))
    # Rendered: the arrowhead polygon appears in the SVG.
    vi_path = _CORPUS / "32" / "comments-and-decorations.vi"
    svg = render_vi_file(vi_path, search_paths=[vi_path.parent])
    assert svg is not None and "<polygon" in svg


@pytest.mark.parametrize("vi,issue_dir", _fixture_vis())
def test_issue_fixture_renders(vi: Path, issue_dir: Path):
    """Every committed issue-repro VI loads and renders to a non-empty SVG --
    a crash/regression guard over the whole issue corpus. (Bug-specific
    behaviour is asserted by the dedicated per-issue tests below.)"""
    svg = render_vi_file(vi, search_paths=[issue_dir])
    assert svg is not None, f"render returned None for {vi.name}"
    assert "<svg" in svg[:500]
    assert len(svg) > 200


def test_issue31_disable_structure_frame_names():
    """#31: disable-family frame labels are data-driven from ``activeDiag`` +
    the detected subtype -- no ``Frame N`` placeholders on the labelable ones.

    Diagram Disable -> Enabled/Disabled; Conditional Disable -> each frame's own
    decoded condition (empty => Default); Type Specialization -> bare
    storage-order ``[i]`` (LabVIEW's ``[N]`` + Accepted/Declined/Ignored are a
    compile result it does not persist, so we don't fabricate them).
    """
    graph, vi = _load("31/disable-structure-frame-names.vi")
    # Ordered (not set) per subtype — so a duplicate label or an
    # Enabled/Disabled inversion actually fails, not just a missing name.
    labels_by_kind = {
        node.kind: [str(f.selector_value) for f in node.frames]
        for node in graph.iter_nodes(vi)
        if isinstance(node, DisableStructureNode)
    }
    assert labels_by_kind[DisableStructureKind.DIAGRAM] == ["Enabled", "Disabled"]
    assert labels_by_kind[DisableStructureKind.CONDITIONAL] == [
        "Default",
        "RUN_TIME_ENGINE==False",
    ]
    assert labels_by_kind[DisableStructureKind.TYPE_SPEC] == ["[0]", "[1]", "[2]"]


def test_issue38_loop_tunnel_glyph_from_mode():
    """#38: a loop tunnel's border glyph is chosen from its parsed
    ``TunnelMode`` (single source of truth), not a binary indexing flag.

    The repro For Loop has three OUTPUT tunnels — a last-value ``Numeric``
    (``PASSTHROUGH`` here), an auto-indexing ``Array`` (``INDEXING``), and an
    auto-concatenating ``Array 2`` (``CONCATENATING``). Before the fix the
    concatenating tunnel was drawn identically to the indexing one (``[ ]``
    brackets); now each mode maps to a distinct glyph, and ``concatenate`` is
    reserved for output tunnels.
    """
    graph, vi = _load("38/auto-concatenating-tunnel.vi")
    loop = next(n for n in graph.iter_nodes(vi) if isinstance(n, LoopNode))
    layout = graph.get_layout(vi)
    assert layout is not None
    glyph_by_mode: dict[tuple[TunnelMode | None, str], str | None] = {}
    for b in _structure_borders(loop, layout, vi):
        t = b.terminal
        if isinstance(t, TunnelTerminal) and t.tunnel_type == "lpTun":
            glyph_by_mode[(t.mode, t.direction)] = b.glyph_kind
    assert glyph_by_mode[(TunnelMode.CONCATENATING, "output")] == "concatenate"
    assert glyph_by_mode[(TunnelMode.INDEXING, "output")] == "autoindex"
    assert glyph_by_mode[(TunnelMode.PASSTHROUGH, "output")] == "tunnel"


def test_issue38_fixture_renders():
    """The #38 repro loads and renders to a non-empty SVG (crash guard)."""
    vi = _CORPUS / "38" / "auto-concatenating-tunnel.vi"
    svg = render_vi_file(vi, search_paths=[vi.parent])
    assert svg is not None and "<svg" in svg[:500] and len(svg) > 200


def test_issue34_hidden_iteration_terminal_omitted():
    """#34: a loop's iteration terminal (``i``) hidden via "Visible Items" is
    carried on the graph and omitted by the renderer; shown terminals still draw.

    Per-terminal visibility is objFlags bit ``0x800000`` on the terminal's inner
    ``sRN`` ``<term>``. Both loops in this repro hide their ``i`` (and nothing
    else). The graph records the hidden KIND on
    ``LoopNode.hidden_border_terminals``; the renderer tags that glyph ``hidden``
    (draw.py skips it), while the visible ``N`` / stop glyphs render normally.
    """
    graph, vi = _load("34/hidden-iteration-terminal.vi")
    loops = [n for n in graph.iter_nodes(vi) if isinstance(n, LoopNode)]
    assert len(loops) == 2
    assert all(n.hidden_border_terminals == frozenset({"i"}) for n in loops), [
        sorted(n.hidden_border_terminals) for n in loops
    ]
    layout = graph.get_layout(vi)
    assert layout is not None
    hidden_kinds: set[str | None] = set()
    shown_kinds: set[str | None] = set()
    for n in loops:
        for b in _structure_borders(n, layout, vi):
            (hidden_kinds if b.hidden else shown_kinds).add(b.glyph_kind)
    # `i` is hidden on both loops; the visible N (for) and stop (while) are not.
    assert "i" in hidden_kinds and "i" not in shown_kinds
    assert {"N", "cond"} & shown_kinds
    assert not ({"N", "cond"} & hidden_kinds)


def test_issue34_fixture_renders():
    """The #34 repro loads and renders to a non-empty SVG (crash guard)."""
    vi = _CORPUS / "34" / "hidden-iteration-terminal.vi"
    svg = render_vi_file(vi, search_paths=[vi.parent])
    assert svg is not None and "<svg" in svg[:500] and len(svg) > 200


def test_issue35_structures_occlude_by_zorder():
    """#35/#39: overlapping structures occlude by zPlaneList paint order.

    The repro has two overlapping For loops (a big one containing a nested loop,
    and a small top-left one) plus a ``0 -> Numeric`` wire that runs behind the
    big loop. LabVIEW draws the small loop LAST (front of the root zPlaneList),
    so it occludes the big loop's corner. The composite render tree must:

    * nest the inner loop under the big loop (graph containment), and
    * order the two root-level loops so the frontmost (highest ``z_order``) is
      drawn AFTER the backmost's whole subtree — asserted on the TREE, not
      pixels — with each structure painting an OPAQUE body.
    """
    from lvkit.render import build_scene
    from lvkit.render.composite import StructureObject, build_render_tree

    graph, vi = _load("35/objects-hidden-by-structures.vi")
    scene = build_scene(graph, vi)
    assert scene is not None

    # Two root-level (parent-less) structures overlap; the inner one is nested.
    root_structs = [s for s in scene.structures if s.node.parent is None]
    assert len(root_structs) == 2, "expected two overlapping root loops"
    nested = [s for s in scene.structures if s.node.parent is not None]
    assert nested, "expected a nested loop inside the big loop"

    def overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    a, b = root_structs
    assert overlap(a.bounds, b.bounds), "the two root loops must overlap"

    # Draw order on the tree: LabVIEW's zPlaneList is FRONT-to-back, so the
    # FRONTMOST loop has the LOWER z_order (document index 0) and must be emitted
    # AFTER the backmost, so its opaque body occludes the backmost's corner.
    tree = build_render_tree(scene)
    order = [
        c.rs.raw_uid
        for c in tree.content.children
        if isinstance(c, StructureObject)
    ]
    assert set(order) == {a.raw_uid, b.raw_uid}
    a_front = scene.z_order[a.raw_uid] < scene.z_order[b.raw_uid]
    front = a.raw_uid if a_front else b.raw_uid
    back = b.raw_uid if a_front else a.raw_uid
    assert order.index(front) > order.index(back), (
        "frontmost (lower z_order / zPlaneList index 0) structure must draw last"
    )

    # The nested loop is a child of one of the root loops (containment nesting),
    # not a sibling at the root.
    assert nested[0].raw_uid not in order

    # Opaque bodies actually reach the SVG (the fix): more canvas-colored FILLS
    # than the single backdrop rect.
    vi_path = _CORPUS / "35" / "objects-hidden-by-structures.vi"
    svg = render_vi_file(vi_path, search_paths=[vi_path.parent])
    assert svg is not None
    assert svg.count('fill="#fbfbf5"') > 3


def test_issue39_overlapping_nodes_occlude_by_zorder():
    """#39: overlapping NODES occlude by zPlaneList paint order.

    The repro overlaps two ``Open/Create/Replace File`` subVIs (distinct from
    #35, whose repro overlaps STRUCTURES). LabVIEW draws diagram children in
    ``zPlaneList`` order (front object last), so the frontmost node occludes the
    corner of the one behind it. The composite tree must order the two
    overlapping root nodes so the frontmost (lower ``z_order``) draws LAST.
    """
    from lvkit.render import build_scene
    from lvkit.render.composite import NodeObject, build_render_tree

    graph, vi = _load("39/zorder-not-respected.vi")
    scene = build_scene(graph, vi)
    assert scene is not None

    def overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    # A pair of root-level nodes whose boxes overlap (the two file subVIs), both
    # with a known paint rank.
    root_nodes = [n for n in scene.nodes if n.node.parent is None]
    pair = next(
        (
            (a, b)
            for i, a in enumerate(root_nodes)
            for b in root_nodes[i + 1 :]
            if overlap(a.bounds, b.bounds)
        ),
        None,
    )
    assert pair is not None, "expected two overlapping nodes in the #39 repro"
    a, b = pair
    assert a.dom_id in scene.z_order and b.dom_id in scene.z_order

    tree = build_render_tree(scene)
    order = [c.rn.dom_id for c in tree.content.children if isinstance(c, NodeObject)]
    a_front = scene.z_order[a.dom_id] < scene.z_order[b.dom_id]
    front = a.dom_id if a_front else b.dom_id
    back = b.dom_id if a_front else a.dom_id
    assert order.index(front) > order.index(back), (
        "frontmost (lower z_order) node must draw last so it occludes the back one"
    )

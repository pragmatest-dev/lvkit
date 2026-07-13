"""Tests for the P2 node glyph resolver chain (lvkit.render.nodes/glyph).

Covers resolver ORDER (json beats generated beats fallback), the
fallback-always-succeeds guarantee, the seeded JSON ``icon`` example, and
that known built-ins (arithmetic, constants) still resolve through the
generated-glyph path. Real-sample assertions reuse the same skip-gracefully
pattern as ``test_render.py`` — the repo's sample VIs are local-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.models import ConstantNode, InPlaceNode, PrimitiveNode, VINode
from lvkit.models import LVType, Terminal
from lvkit.primitive_resolver import NodeIcon, PrimitiveEntry, ResolvedPrimitive
from lvkit.render.backend import SvgBackend
from lvkit.render.glyph import (
    ArithGlyph,
    BooleanConstantGlyph,
    CompoundArithGlyph,
    ConstantGlyph,
    WrappedBoxGlyph,
)
from lvkit.render.nodes import (
    ExtractedIconResolver,
    FallbackBoxResolver,
    GeneratedGlyphResolver,
    GlyphContext,
    JsonGlyphResolver,
    resolve_glyph,
)
from lvkit.render.scene import build_scene
from lvkit.render.style import DEFAULT_THEME
from lvkit.vilib_resolver import VIEntry

GROUND_TRUTH_VI = Path(".tmp/array average 1.vi")


def _ctx() -> GlyphContext:
    return GlyphContext(graph=InMemoryVIGraph(), vi_name="vi")


# --------------------------------------------------------------------------- #
# Backward-compatible schema: icon is optional everywhere it was added.
# --------------------------------------------------------------------------- #


def test_primitive_entry_tolerates_missing_icon():
    entry = PrimitiveEntry.model_validate({"name": "Add"})
    assert entry.icon is None


def test_resolved_primitive_tolerates_missing_icon():
    resolved = ResolvedPrimitive(name="Add")
    assert resolved.icon is None


def test_vi_entry_tolerates_missing_icon():
    entry = VIEntry.model_validate({"name": "Foo.vi"})
    assert entry.icon is None


def test_node_icon_accepts_inline_svg_and_size():
    icon = NodeIcon.model_validate({"svg": "<circle/>", "size": [24, 16]})
    assert icon.svg == "<circle/>"
    assert icon.size == (24, 16)
    assert icon.file is None


# --------------------------------------------------------------------------- #
# Resolver ORDER: json beats generated beats fallback.
# --------------------------------------------------------------------------- #


def test_generated_glyph_wins_for_cpdarith():
    """cpdArith (Compound Arithmetic) has NO declared JSON icon — it must
    resolve via GeneratedGlyphResolver's dedicated CompoundArithGlyph (a
    real-bounds rectangle + operator symbol), not a JsonGlyphResolver
    icon (there is none) and not the generic labeled-box fallback."""
    node = PrimitiveNode(
        id="vi::1", vi="vi", name="Compound Arithmetic", node_type="cpdArith",
        operation="add", terminals=[],
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, CompoundArithGlyph)
    assert glyph.operation == "add"


def test_generated_glyph_wins_over_fallback_for_known_arithmetic():
    """Add (prim_id 1050 — the SAME id the ground-truth VI's Add node uses)
    has no JSON icon, so it must resolve via GeneratedGlyphResolver's
    triangle, not fall through to FallbackBoxResolver's plain box. This
    also proves the seeded cpdArith icon does NOT leak onto normal Add
    nodes by prim_id (which would break P1 visual equivalence)."""
    node = PrimitiveNode(
        id="vi::2", vi="vi", name="Add", node_type="prim", prim_id=1050,
        terminals=[],
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, ArithGlyph)
    assert glyph.symbol == "+"


def test_constant_node_resolves_via_generated_glyph():
    node = ConstantNode(
        id="vi::3", vi="vi", value=0.0,
        lv_type=LVType(kind="primitive", underlying_type="NumFloat64"),
        terminals=[],
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, ConstantGlyph)
    assert glyph.value == "0"  # LabVIEW omits trailing ".0" for whole floats
    assert glyph.color == DEFAULT_THEME.wire_float


def test_boolean_constant_resolves_to_boolean_glyph():
    from lvkit.render.nodes import _bool_value

    bool_t = LVType(kind="primitive", underlying_type="Boolean")
    true_node = ConstantNode(id="vi::t", vi="vi", value="True", lv_type=bool_t,
                             terminals=[])
    false_node = ConstantNode(id="vi::f", vi="vi", value="0000", lv_type=bool_t,
                              terminals=[])
    gt = resolve_glyph(true_node, _ctx())
    gf = resolve_glyph(false_node, _ctx())
    assert isinstance(gt, BooleanConstantGlyph) and gt.value is True
    assert isinstance(gf, BooleanConstantGlyph) and gf.value is False
    # token parsing: real bool, T/1/true -> True; anything else -> False
    assert _bool_value(True) and _bool_value("T") and _bool_value("1")
    assert not _bool_value("0000") and not _bool_value("False") and not _bool_value(0)


def test_boolean_constant_glyph_true_vs_false_render():
    b = SvgBackend()
    BooleanConstantGlyph(True).draw(b, (0.0, 0.0, 16.0, 14.0), DEFAULT_THEME)
    true_svg = b.render((0.0, 0.0, 16.0, 14.0))
    b2 = SvgBackend()
    BooleanConstantGlyph(False).draw(b2, (0.0, 0.0, 16.0, 14.0), DEFAULT_THEME)
    false_svg = b2.render((0.0, 0.0, 16.0, 14.0))
    green = DEFAULT_THEME.wire_bool
    # True: green fill + white bezel + white T
    assert f'fill="{green}"' in true_svg and 'stroke="#ffffff"' in true_svg
    assert ">T</text>" in true_svg
    # False: white fill + green outline + green F
    assert 'fill="#ffffff"' in false_svg and f'stroke="{green}"' in false_svg
    assert ">F</text>" in false_svg
    # centered SQUARE (side = min(w,h) = 14), not stretched to the 16 width
    assert 'width="14.0"' in true_svg


def test_string_constant_wraps_full_text_no_ellipsis():
    """A multi-line string constant fills its (content-sized) box with the full
    text word-wrapped and honoring explicit newlines — NOT collapsed to one
    ellipsized line. The box here is tall, as LabVIEW sizes these word lists."""
    b = SvgBackend()
    value = "\none\ntwo\nthree\nfour\nfive"
    ConstantGlyph(value, "#e05fa0", multiline=True).draw(
        b, (0.0, 0.0, 70.0, 120.0), DEFAULT_THEME,
    )
    svg = b.render((0.0, 0.0, 70.0, 120.0))
    # Every word shows as its own line; nothing is truncated with an ellipsis.
    for word in ("one", "two", "three", "four", "five"):
        assert f">{word}</text>" in svg
    assert "…" not in svg
    # Wrapped lines are left-aligned (start), not centered.
    assert 'text-anchor="start"' in svg


def test_scalar_constant_stays_single_line_centered():
    b = SvgBackend()
    ConstantGlyph("42", "#1f3fbf").draw(b, (0.0, 0.0, 30.0, 16.0), DEFAULT_THEME)
    svg = b.render((0.0, 0.0, 30.0, 16.0))
    assert ">42</text>" in svg
    assert 'text-anchor="middle"' in svg


def test_fallback_always_returns_a_glyph_for_an_unhandled_node_kind():
    """No resolver targets structure nodes (scene.py never calls
    resolve_glyph on them — they go through draw_structure instead), so
    calling the chain directly on one exercises the "truly unknown node"
    path end to end: every earlier resolver falls through, and
    FallbackBoxResolver still succeeds."""
    node = InPlaceNode(id="vi::4", vi="vi", node_type="ipes", terminals=[])
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, WrappedBoxGlyph)


def test_fallback_resolver_never_returns_none_directly():
    node = InPlaceNode(id="vi::5", vi="vi", node_type="ipes", terminals=[])
    glyph = FallbackBoxResolver().resolve(node, _ctx())
    assert glyph is not None


# --------------------------------------------------------------------------- #
# Individual resolvers: fall-through and fail-soft behavior.
# --------------------------------------------------------------------------- #


def test_json_glyph_resolver_falls_through_when_no_icon_declared():
    node = PrimitiveNode(
        id="vi::6", vi="vi", name="Add", node_type="prim", prim_id=1050,
        terminals=[],
    )
    assert JsonGlyphResolver().resolve(node, _ctx()) is None


def test_json_glyph_resolver_falls_through_for_non_primitive_non_vi_nodes():
    node = ConstantNode(id="vi::7", vi="vi", terminals=[])
    assert JsonGlyphResolver().resolve(node, _ctx()) is None


def test_generated_glyph_resolver_falls_through_for_structure_nodes():
    node = InPlaceNode(id="vi::8", vi="vi", node_type="ipes", terminals=[])
    assert GeneratedGlyphResolver().resolve(node, _ctx()) is None


def test_extracted_icon_resolver_is_fail_soft_when_subvi_not_locatable():
    """A SubVI call whose source isn't loaded in the graph and has no
    (resolvable) qualified_path must fall through cheaply — no exception,
    no subprocess extraction attempt."""
    node = VINode(id="vi::9", vi="vi", name="Some Unresolved SubVI.vi", terminals=[])
    assert ExtractedIconResolver().resolve(node, _ctx()) is None


def test_extracted_icon_resolver_ignores_non_vi_nodes():
    node = PrimitiveNode(id="vi::10", vi="vi", prim_id=1050, terminals=[])
    assert ExtractedIconResolver().resolve(node, _ctx()) is None


# --------------------------------------------------------------------------- #
# CompoundArithGlyph: a real-bounds rectangle (not a fixed-size icon), with
# the operator symbol for the node's operation ("or" is the default when
# ``operation`` isn't set, matching GeneratedGlyphResolver._primitive_glyph).
# --------------------------------------------------------------------------- #


def test_compound_arith_glyph_renders_at_real_bounds_with_default_operation():
    node = PrimitiveNode(
        id="vi::11", vi="vi", name="Compound Arithmetic", node_type="cpdArith",
        terminals=[],
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, CompoundArithGlyph)
    bounds = (0.0, 0.0, 40.0, 30.0)
    backend = SvgBackend()
    glyph.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    # A plain rectangle sized to the PASSED-IN bounds — not a fixed 24x16
    # icon — so a wider/taller node (more inputs) grows the box, not a fixed
    # asset.
    assert 'width="40.0" height="30.0"' in svg
    assert ">∨<" in svg  # default "or" symbol when operation is unset


def test_cpd_arith_boolean_add_renders_as_logical_or():
    """A Boolean-terminal cpdArith node with operation "add" is LabVIEW's
    logical OR (see codegen/nodes/compound.py::generate_compound_arith),
    so the glyph must show 'or' (drawn as ``∨``), not the raw '+' — mirrors
    node 802 in the stacked_sequence sample."""
    bool_t = LVType(kind="primitive", underlying_type="Boolean")
    node = PrimitiveNode(
        id="vi::20", vi="vi", name="Compound Arithmetic", node_type="cpdArith",
        operation="add",
        terminals=[
            Terminal(id="vi::20::0", index=0, direction="input", lv_type=bool_t),
            Terminal(id="vi::20::1", index=1, direction="input", lv_type=bool_t),
            Terminal(id="vi::20::2", index=2, direction="output", lv_type=bool_t),
        ],
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, CompoundArithGlyph)
    assert glyph.operation == "or"
    assert glyph.num_inputs == 2  # matches node 802: 2 boolean inputs
    bounds = (0.0, 0.0, 24.0, 17.0)
    backend = SvgBackend()
    glyph.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert ">∨<" in svg
    # 1 vertical operator-cell divider + (num_inputs - 1) horizontal row
    # dividers = 2 lines total for a 2-input node.
    assert len(re.findall(r"<line[^>]*/>", svg)) == 2


def test_cpd_arith_numeric_add_still_renders_as_plus():
    """A NUMERIC-terminal cpdArith node with operation "add" is ordinary
    arithmetic addition — the glyph must keep the '+' symbol."""
    num_t = LVType(kind="primitive", underlying_type="NumFloat64")
    node = PrimitiveNode(
        id="vi::21", vi="vi", name="Compound Arithmetic", node_type="cpdArith",
        operation="add",
        terminals=[
            Terminal(id="vi::21::0", index=0, direction="input", lv_type=num_t),
            Terminal(id="vi::21::1", index=1, direction="input", lv_type=num_t),
            Terminal(id="vi::21::2", index=2, direction="output", lv_type=num_t),
        ],
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, CompoundArithGlyph)
    assert glyph.operation == "add"


def test_cpd_arith_three_inputs_draws_two_horizontal_dividers():
    """A 3-input cpdArith node draws 1 vertical operator-cell divider plus
    2 horizontal row dividers (one per input boundary) = 3 lines total."""
    num_t = LVType(kind="primitive", underlying_type="NumFloat64")
    node = PrimitiveNode(
        id="vi::22", vi="vi", name="Compound Arithmetic", node_type="cpdArith",
        operation="add",
        terminals=[
            Terminal(id="vi::22::0", index=0, direction="input", lv_type=num_t),
            Terminal(id="vi::22::1", index=1, direction="input", lv_type=num_t),
            Terminal(id="vi::22::2", index=2, direction="input", lv_type=num_t),
            Terminal(id="vi::22::3", index=3, direction="output", lv_type=num_t),
        ],
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, CompoundArithGlyph)
    assert glyph.num_inputs == 3
    bounds = (0.0, 0.0, 40.0, 30.0)
    backend = SvgBackend()
    glyph.draw(backend, bounds, DEFAULT_THEME)
    svg = backend.render(bounds)
    assert len(re.findall(r"<line[^>]*/>", svg)) == 3
    assert ">+<" in svg


# --------------------------------------------------------------------------- #
# Real sample: the ground-truth VI's Add still resolves through the
# generated arithmetic glyph (visual-equivalence guard for P1 -> P2).
# --------------------------------------------------------------------------- #


def test_ground_truth_add_still_resolves_to_arith_glyph():
    if not GROUND_TRUTH_VI.exists():
        pytest.skip(f"sample VI not available: {GROUND_TRUTH_VI}")
    graph = InMemoryVIGraph()
    graph.load_vi(GROUND_TRUTH_VI, expand_subvis=False)
    vi = graph.resolve_vi_name(GROUND_TRUTH_VI.name)
    scene = build_scene(graph, vi)
    assert scene is not None
    add_nodes = [
        n for n in scene.nodes
        if isinstance(n.node, PrimitiveNode) and n.node.name == "Add"
    ]
    assert add_nodes
    assert all(isinstance(n.glyph, ArithGlyph) for n in add_nodes)


# --------------------------------------------------------------------------- #
# Node-as-link: a resolvable node's group is wrapped in an <a> to its NI docs
# (task #67) so the doc_url is clickable, not just shown in the <title>.
# --------------------------------------------------------------------------- #


def test_backend_group_href_wraps_in_anchor():
    b = SvgBackend()
    b.begin_group(cls="lv-node", href="https://ni.com/docs/x.html", title="X")
    b.rect(0, 0, 1, 1, fill="#000")
    b.end_group()
    svg = "".join(b._elements)
    assert '<a href="https://ni.com/docs/x.html" target="_blank" rel="noopener">' in svg
    assert svg.index("<a ") < svg.index('<g class="lv-node"')
    assert svg.rstrip().endswith("</a>")
    assert svg.count("<a ") == svg.count("</a>") == 1


def test_backend_group_without_href_has_no_anchor():
    b = SvgBackend()
    b.begin_group(cls="lv-node")
    b.end_group()
    assert "<a " not in "".join(b._elements)


def test_backend_nested_groups_balance_anchors():
    """An inner (anchor-less) group closes its </g> before the outer </a>."""
    b = SvgBackend()
    b.begin_group(href="u")       # outer: opens <a>
    b.begin_group(cls="inner")    # inner: no anchor
    b.end_group()
    b.end_group()
    assert "".join(b._elements) == (
        '<a href="u" target="_blank" rel="noopener"><g>'
        '<g class="inner"></g></g></a>'
    )

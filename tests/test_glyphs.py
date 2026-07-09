"""Tests for the P2 node glyph resolver chain (lvkit.render.nodes/glyph).

Covers resolver ORDER (json beats generated beats fallback), the
fallback-always-succeeds guarantee, the seeded JSON ``icon`` example, and
that known built-ins (arithmetic, constants) still resolve through the
generated-glyph path. Real-sample assertions reuse the same skip-gracefully
pattern as ``test_render.py`` — the repo's sample VIs are local-only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.models import ConstantNode, InPlaceNode, PrimitiveNode, VINode
from lvkit.models import LVType
from lvkit.primitive_resolver import NodeIcon, PrimitiveEntry, ResolvedPrimitive
from lvkit.render.backend import SvgBackend
from lvkit.render.glyph import (
    ArithGlyph,
    BooleanConstantGlyph,
    ConstantGlyph,
    InlineSvgGlyph,
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


def test_json_glyph_wins_over_generated_for_declared_icon():
    """cpdArith (Compound Arithmetic) has a real seeded icon in
    data/primitives.json (node_types.cpdArith.icon) — the resolver chain
    must pick it over GeneratedGlyphResolver's generic labeled box."""
    node = PrimitiveNode(
        id="vi::1", vi="vi", name="Compound Arithmetic", node_type="cpdArith",
        operation="add", terminals=[],
    )
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, InlineSvgGlyph)
    assert "Σ" in glyph.fragment
    assert glyph.size == (24, 16)


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
# The seeded JSON icon example actually renders.
# --------------------------------------------------------------------------- #


def test_seeded_json_icon_renders_as_nested_svg():
    node = PrimitiveNode(
        id="vi::11", vi="vi", name="Compound Arithmetic", node_type="cpdArith",
        terminals=[],
    )
    glyph = resolve_glyph(node, _ctx())
    backend = SvgBackend()
    glyph.draw(backend, (0.0, 0.0, 40.0, 30.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 40.0, 30.0))
    assert "<svg x=" in svg
    assert 'viewBox="0 0 24 16"' in svg
    assert "Σ" in svg


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

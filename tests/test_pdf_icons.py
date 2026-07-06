"""Tests for the PDF-extracted primitive icon resolver (Fidelity step 5).

Real assets live under ``src/lvkit/data/glyphs/`` (built by
``scripts/extract_lv_icons.py`` from the reference manual, a build-time-only
input). Tests that need a specific extracted asset SKIP gracefully if it
isn't present (same pattern as ``test_glyphs.py``'s ground-truth-VI tests) —
the extractor's coverage varies with the manual, so tests must be robust to
whichever assets actually got extracted, not hardcode an exact roster.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, VINode
from lvkit.render.backend import SvgBackend
from lvkit.render.glyph import ArithGlyph, CenteredSvgGlyph, IconImageGlyph
from lvkit.render.nodes import (
    _RESOLVERS,
    ExtractedIconResolver,
    GeneratedGlyphResolver,
    GlyphContext,
    JsonGlyphResolver,
    PdfIconResolver,
    resolve_glyph,
)

GLYPHS_DIR = Path(__file__).parent.parent / "src" / "lvkit" / "data" / "glyphs"
MANIFEST = GLYPHS_DIR / "_manifest.json"


def _ctx() -> GlyphContext:
    return GlyphContext(graph=InMemoryVIGraph(), vi_name="vi")


def _first_ok_manifest_entry() -> dict | None:
    if not MANIFEST.is_file():
        return None
    for entry in json.loads(MANIFEST.read_text()):
        if entry["status"] == "ok":
            return entry
    return None


# --------------------------------------------------------------------------- #
# Chain order: ExtractedIcon, then JsonGlyph, then PdfIcon, then Generated.
# --------------------------------------------------------------------------- #


def test_resolver_chain_order():
    kinds = [type(r) for r in _RESOLVERS]
    assert (
        kinds.index(ExtractedIconResolver)
        < kinds.index(JsonGlyphResolver)
        < kinds.index(PdfIconResolver)
        < kinds.index(GeneratedGlyphResolver)
    )


def test_json_glyph_wins_over_pdf_icon_for_declared_icon():
    """cpdArith has a real seeded JSON icon (data/primitives.json
    node_types.cpdArith.icon) — the chain must pick it even though
    PdfIconResolver comes right after JsonGlyphResolver."""
    node = PrimitiveNode(
        id="vi::json1", vi="vi", name="Compound Arithmetic",
        node_type="cpdArith", operation="add", terminals=[],
    )
    glyph = resolve_glyph(node, _ctx())
    assert not isinstance(glyph, IconImageGlyph)


# --------------------------------------------------------------------------- #
# PdfIconResolver: fall-through and asset-hit behavior.
# --------------------------------------------------------------------------- #


def test_pdf_icon_resolver_ignores_non_primitive_nodes():
    node = VINode(id="vi::pdf1", vi="vi", name="Some SubVI.vi", terminals=[])
    assert PdfIconResolver().resolve(node, _ctx()) is None


def test_pdf_icon_resolver_returns_none_for_unknown_prim_id():
    node = PrimitiveNode(
        id="vi::pdf2", vi="vi", name="Totally Unknown Thing",
        node_type="prim", prim_id=999_999_999, terminals=[],
    )
    assert PdfIconResolver().resolve(node, _ctx()) is None


def test_pdf_icon_resolver_returns_icon_image_glyph_for_extracted_asset():
    """PdfIconResolver now prefers the vectorized ``.svg`` asset (pixel-
    faithful, drawn at natural size) over the raw ``.png`` when both exist
    for the same stem — only stems without an SVG still fall back to
    ``IconImageGlyph``."""
    entry = _first_ok_manifest_entry()
    if entry is None:
        pytest.skip("no extracted PDF icons available; run "
                     "scripts/extract_lv_icons.py first")
    if entry["node_type"]:
        node = PrimitiveNode(
            id="vi::pdf3", vi="vi", name=entry["name"],
            node_type=entry["node_type"], terminals=[],
        )
    else:
        node = PrimitiveNode(
            id="vi::pdf3", vi="vi", name=entry["name"], node_type="prim",
            prim_id=int(entry["prim_id"]), terminals=[],
        )
    glyph = PdfIconResolver().resolve(node, _ctx())
    stem = Path(entry["asset"]).stem
    svg_asset = GLYPHS_DIR / f"{stem}.svg"
    if svg_asset.is_file():
        assert isinstance(glyph, CenteredSvgGlyph)
    else:
        assert isinstance(glyph, IconImageGlyph)
        assert glyph.icon_path.name == entry["asset"]


def test_borderless_add_has_no_pdf_asset_and_falls_to_arith_triangle():
    """Add (prim_id 1050) is a borderless triangle in the reference manual —
    the extractor must never have produced an asset for it, and the full
    chain must still land on GeneratedGlyphResolver's triangle."""
    node = PrimitiveNode(
        id="vi::pdf4", vi="vi", name="Add", node_type="prim", prim_id=1050,
        terminals=[],
    )
    assert PdfIconResolver().resolve(node, _ctx()) is None
    glyph = resolve_glyph(node, _ctx())
    assert isinstance(glyph, ArithGlyph)


def test_index_array_prefers_node_type_asset_over_shared_prim_id():
    """aIndx ("Index Array") is assigned prim_id=1809 at parse time (see
    parser/node_types.py), which is the SAME primResID primitives.json
    separately and correctly labels "Array Size" for the generic "prim"
    class (see extract_lv_icons.py module docstring). PdfIconResolver must
    check the node_type-keyed asset first so Index Array can never inherit
    Array Size's icon (or vice versa) purely because they share a number."""
    asset = GLYPHS_DIR / "prim_nt_aIndx.png"
    if not asset.is_file():
        pytest.skip("Index Array icon not extracted")
    node = PrimitiveNode(
        id="vi::pdf5", vi="vi", name="Index Array", node_type="aIndx",
        prim_id=1809, terminals=[],
    )
    glyph = PdfIconResolver().resolve(node, _ctx())
    svg_asset = GLYPHS_DIR / "prim_nt_aIndx.svg"
    if svg_asset.is_file():
        assert isinstance(glyph, CenteredSvgGlyph)
    else:
        assert isinstance(glyph, IconImageGlyph)
        assert glyph.icon_path == asset


@pytest.mark.parametrize("node_type,display_name", [
    ("aBuild", "Build Array"),
    ("aIndx", "Index Array"),
    ("aInit", "Initialize Array"),
])
def test_boxed_array_primitives_resolve_to_extracted_icons(node_type, display_name):
    asset = GLYPHS_DIR / f"prim_nt_{node_type}.png"
    if not asset.is_file():
        pytest.skip(f"{display_name} icon not extracted")
    node = PrimitiveNode(
        id=f"vi::{node_type}", vi="vi", name=display_name,
        node_type=node_type, terminals=[],
    )
    glyph = resolve_glyph(node, _ctx())
    svg_asset = GLYPHS_DIR / f"prim_nt_{node_type}.svg"
    if svg_asset.is_file():
        assert isinstance(glyph, CenteredSvgGlyph)
    else:
        assert isinstance(glyph, IconImageGlyph)
        assert glyph.icon_path == asset


# --------------------------------------------------------------------------- #
# SvgBackend: pixelated raster scaling for the 32x32-ish icon assets.
# --------------------------------------------------------------------------- #


def test_svg_backend_image_is_pixelated():
    backend = SvgBackend()
    backend.image("data:image/png;base64,AA==", 0, 0, 32, 32)
    svg = backend.render((0.0, 0.0, 32.0, 32.0))
    assert "image-rendering: pixelated" in svg

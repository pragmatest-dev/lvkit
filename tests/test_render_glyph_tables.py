"""The primitive glyph tables are keyed by primResID; each ID is the function the
catalog says it is."""

from __future__ import annotations

import pytest

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import resolve_primitive
from lvkit.render import nodes as render_nodes
from lvkit.render.glyphs.nodes.arith import ArithGlyph

_EXPECTED_NAMES = {
    "_ARITH_SYMBOL": {
        1050: "Add",
        3915: "Add",
        1051: "Subtract",
        3916: "Subtract",
        1052: "Multiply",
        3917: "Multiply",
        1053: "Divide",
        3918: "Divide",
        1057: "Increment",
        1058: "Decrement",
        1911: "And Array Elements",
        1910: "Or Array Elements",
        1903: "Add Array Elements",
        1904: "Multiply Array Elements",
        1516: "Select",
    },
    "_COMPARE_SYMBOL": {
        1102: "Equal?",
        1105: "Not Equal?",
        1110: "Greater?",
        1111: "Less?",
        1103: "Greater Or Equal?",
        1104: "Less Or Equal?",
        1113: "Equal To 0?",
        1116: "Not Equal To 0?",
        1117: "Greater Than 0?",
        1118: "Less Than 0?",
        1114: "Greater Or Equal To 0?",
        1115: "Less Or Equal To 0?",
    },
    "_BOOLEAN_GATE": {1061: "And", 1062: "Or", 1064: "Not"},
    "_ARRAY_GLYPH": {
        1809: "Array Size",
        1900: "Reverse 1D Array",
        1901: "Search 1D Array",
        1120: "Sort 1D Array",
        1908: "Split 1D Array",
    },
    "_CONVERTER_ABBR": {
        1142: "To Long Integer",
        1141: "To Word Integer",
        1140: "To Byte Integer",
        1143: "To Unsigned Byte Integer",
        1144: "To Unsigned Word Integer",
        1147: "To Double Precision Float",
        1167: "Boolean To (0,1)",
    },
}


@pytest.mark.parametrize("table", sorted(_EXPECTED_NAMES))
def test_each_id_is_the_expected_function(table: str) -> None:
    ids = getattr(render_nodes, table)
    assert set(ids) == set(_EXPECTED_NAMES[table])
    for prim_id, name in _EXPECTED_NAMES[table].items():
        resolved = resolve_primitive(prim_id=prim_id)
        assert resolved is not None and resolved.name == name, prim_id


def _node(node_type: str, prim_id: int, name: str) -> PrimitiveNode:
    return PrimitiveNode(
        id="vi::1", vi_path="vi", node_type=node_type, prim_id=prim_id, name=name
    )


def test_glyph_follows_prim_id_not_name() -> None:
    glyph = render_nodes.GeneratedGlyphResolver._primitive_glyph  # noqa: SLF001
    # A plain primitive is drawn from its ID, whatever its name says.
    assert isinstance(glyph(_node("prim", 1050, "Renamed")), ArithGlyph)
    # The same name on a node without that ID gets no arithmetic triangle.
    assert not isinstance(glyph(_node("prim", 99999, "Add")), ArithGlyph)
    # A specialized node class sharing the ID is not the plain primitive.
    assert not isinstance(glyph(_node("aIndx", 1809, "Index Array")), ArithGlyph)


def test_comparison_glyph_follows_prim_id_not_name() -> None:
    resolve = render_nodes.OriginalGlyphResolver().resolve
    assert isinstance(resolve(_node("prim", 1102, "Renamed"), None), ArithGlyph)  # type: ignore[arg-type]
    assert resolve(_node("prim", 99999, "Equal?"), None) is None  # type: ignore[arg-type]
    assert resolve(_node("aIndx", 1102, "Equal?"), None) is None  # type: ignore[arg-type]

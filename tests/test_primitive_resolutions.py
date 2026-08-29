"""Regression lock for primitive resolutions a real user VI depends on.

These primResIDs were each mislabeled or unknown and were resolved (by type +
consumer context) while making a Formula-Node calibration VI convert and run
end to end. They are plain data in primitives.json — trivial to revert by
accident — so this test pins the identities, the key python_code, and the
Build Array routing. No user IP: only resID → function-identity facts.

Four of these (1057, 1809, 1903, 1904) were ALL mislabeled "Array Size"; only
1809 is really Array Size. The collision guard below stops a future edit from
re-merging them.

UPDATE (#59, 2026-08-27): 1057's onward identification as "Absolute Value"
(pinned below as of this docstring's original writing) has since been
corrected to "Increment" -- nodes.json's VI-Scripting export gives 1057's real
output terminal as literally "x+1", proving the earlier context-based
resolution had the right ALGORITHM (abs-value feeding an increment for a
pixel-span computation) attached to the wrong primResID. The real
Absolute-Value primitive in that calibration VI's pane is not yet
re-identified. 1057's python_code was dropped pending that, then restored
once corpus-confirmed (pylabview terminal_info) as its own real function --
`in_1 + 1`, the genuine Increment semantics -- not the old Absolute-Value
code, and not a stand-in for the calibration VI's still-unidentified abs-value
step.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.primitive_resolver import get_resolver

PRIMS = Path(__file__).resolve().parents[1] / "src/lvkit/data/primitives.json"

EXPECTED_NAMES = {
    1809: "Array Size",
    1903: "Add Array Elements",
    1904: "Multiply Array Elements",
    1057: "Increment",  # was "Absolute Value" -- corrected per nodes.json, #59
    1049: "Sign",
    1140: "To Byte Integer",
    # The 2-input arithmetic block: 1050 Add, 1051 Subtract, 1052 Multiply,
    # 1053 Divide. 1050 was previously mis-IDed as "Build Array" (it was an
    # Add broadcasting a scalar over an array); 1052 was a duplicate "Subtract".
    1050: "Add",
    1051: "Subtract",
    1052: "Multiply",
    1053: "Divide",
}


@pytest.mark.parametrize("prim_id,name", EXPECTED_NAMES.items())
def test_primitive_resolves_to_expected_function(prim_id, name):
    resolved = get_resolver().resolve(prim_id=prim_id)
    assert resolved is not None, f"prim {prim_id} did not resolve"
    assert resolved.name == name, (
        f"prim {prim_id} resolved to {resolved.name!r}, expected {name!r}"
    )


def test_array_size_collision_does_not_return():
    """Only 1809 is Array Size; the others must never regress back to it
    (and 1809 must not regress to the old 'Index Array' mislabel)."""
    res = get_resolver()
    assert res.resolve(prim_id=1809).name == "Array Size"
    for prim_id in (1049, 1057, 1903, 1904):
        assert res.resolve(prim_id=prim_id).name != "Array Size"


def test_key_python_code_semantics():
    res = get_resolver()
    # Array Size = count; reductions = sum / product (element-typed)
    assert "len(in_1)" in str(res.resolve(prim_id=1809).python_code)
    assert "sum(in_1)" in str(res.resolve(prim_id=1903).python_code)
    assert "prod(in_1)" in str(res.resolve(prim_id=1904).python_code)
    # 1057 no longer carries "abs(in_1)" -- it was corrected from the
    # mislabeled "Absolute Value" to its real identity "Increment" (#59), and
    # its own python_code ("in_1 + 1") was restored once corpus-confirmed.
    assert "in_1 + 1" in str(res.resolve(prim_id=1057).python_code)
    # Sign -> +/-1, To Byte Integer -> saturating I8
    assert "(in_1 > 0) - (in_1 < 0)" in str(res.resolve(prim_id=1049).python_code)
    assert "127" in str(res.resolve(prim_id=1140).python_code)
    # 2-input arithmetic: Add / Subtract / Multiply / Divide (operators give
    # LV's type coercion for free; Divide is true division -> always float).
    # Operand order follows the connector pane, not the NI doc listing: the
    # index space is Bottom->Top, so `x` (the documented UPPER input) is in_2
    # and `y` is in_1 -- hence `in_2 - in_1` for x-y. Add and Multiply are
    # commutative, so their formulas keep their original operand order.
    # See tests/test_primitive_positional_order.py for the invariant.
    assert "in_1 + in_2" in str(res.resolve(prim_id=1050).python_code)
    assert "in_2 - in_1" in str(res.resolve(prim_id=1051).python_code)
    assert "in_1 * in_2" in str(res.resolve(prim_id=1052).python_code)
    assert "in_2 / in_1" in str(res.resolve(prim_id=1053).python_code)


def test_paren_if_compound_preserves_operand_precedence():
    """An inlined compound operand must be parenthesized so its precedence
    survives string substitution into an operator template (task #86)."""
    from lvkit.codegen.nodes.primitive import _paren_if_compound

    assert _paren_if_compound("z | x") == "(z | x)"  # BinOp -> wrapped
    assert _paren_if_compound("~x") == "(~x)"  # UnaryOp -> wrapped
    assert _paren_if_compound("a and b") == "(a and b)"  # BoolOp -> wrapped
    assert _paren_if_compound("a < b") == "(a < b)"  # Compare -> wrapped
    assert _paren_if_compound("x") == "x"  # Name -> unchanged
    assert _paren_if_compound("f(a)") == "f(a)"  # Call -> unchanged
    assert _paren_if_compound("a.b[0]") == "a.b[0]"  # Subscript -> unchanged


def test_boolean_logic_prims_have_integer_bitwise_variant():
    """And/Or carry a python_code_int template so integer operands emit bitwise
    &/| (LabVIEW polymorphism). Not is handled in codegen (needs a width mask)."""
    res = get_resolver()
    # 1061 = And, 1062 = Or (resIDs verified against MD5 F/G/I dataflow; the
    # names were previously swapped).
    assert res.resolve(prim_id=1061).python_code_int == {"result": "in_1 & in_2"}
    assert res.resolve(prim_id=1062).python_code_int == {"result": "in_1 | in_2"}
    # bool path unchanged (idiomatic logical operators)
    assert "in_1 and in_2" in str(res.resolve(prim_id=1061).python_code)
    assert "in_1 or in_2" in str(res.resolve(prim_id=1062).python_code)


def test_numeric_primitives_are_elementwise():
    res = get_resolver()
    # Add, Subtract, Multiply, Sign all broadcast over arrays
    for prim_id in (1050, 1051, 1052, 1049):
        assert res.resolve(prim_id=prim_id).elementwise is True


def test_comparison_to_zero_block_verified_members():
    """Task #107: the comparison-to-0 members pinned from full-corpus dataflow.
    1113 (=0) and 1118 (<0) are proven; 1114 (>=0) is proven via search-result
    'found' idioms (offset -1 when not found, hit at index 0 valid). 1115 (<=0)
    and 1117 (>0) are the best-supported reads but NOT corpus-separable from ==0
    / !=0 respectively (identical on non-negative feeders)."""
    res = get_resolver()
    assert res.resolve(prim_id=1113).name == "Equal To 0?"
    assert res.resolve(prim_id=1114).name == "Greater Or Equal To 0?"
    assert res.resolve(prim_id=1118).name == "Less Than 0?"
    assert "in_1 >= 0" in str(res.resolve(prim_id=1114).python_code)
    assert "in_1 < 0" in str(res.resolve(prim_id=1118).python_code)
    # 1114 is the UNARY to-0 form (one data input), not the binary comparison.
    in_terms = [t for t in res.resolve(prim_id=1114).terminals if t.direction == "in"]
    assert len(in_terms) == 1


def test_1116_is_not_equal_to_zero():
    """1116 was previously mislabeled 'Call Chain' / kept as a flagged
    placeholder because its corpus feeders looked boolean/cluster, not
    numeric. That finding was itself wrong: nodes.json's VI-Scripting export
    (#59) gives 1116's real pane -- output terminal literally named 'x != 0?'
    (Boolean) over a scalar double_float input 'x' -- confirming it as Not
    Equal To 0?, one of the comparison-to-0 family alongside 1113/1114/1118
    (see test_comparison_to_zero_block_verified_members). It was corrected
    from the nodes.json ground truth, dropping the old placeholder along with
    its now-superseded python_code -- codegen re-derivation is a separate
    follow-on, so it resolves unverified with no python_code yet."""
    res = get_resolver()
    r = res.resolve(prim_id=1116)
    assert r is not None
    assert r.name == "Not Equal To 0?"
    assert r.confidence != "placeholder"
    assert not r.python_code

    entry = json.loads(PRIMS.read_text())["primitives"]["1116"]
    assert entry.get("verified") is False
    assert "python_code" not in entry
    assert "placeholder" not in entry


def test_1901_search_vs_delete_collision():
    """primResID 1901 is shared by two XML classes (task #108): class='prim'
    (166 corpus nodes) = Search 1D Array; class='aDelete' (expandable) = Delete
    From Array. The prim-id entry must be Search (aDelete is resolved by
    node_type, independently). Was mislabeled 'Delete From Array' for prim."""
    res = get_resolver()
    assert res.resolve(prim_id=1901).name == "Search 1D Array"
    assert "-1" in str(res.resolve(prim_id=1901).python_code)  # not-found sentinel
    # Delete From Array survives via the node_type section, not prim-id 1901.
    assert res.resolve_by_node_type("aDelete").name == "Delete From Array"


def test_1142_is_to_long_integer():
    """primResID 1142 was a generic unverified 'Type Conversion' placeholder.
    Full-corpus dataflow shows its output is ALWAYS NumInt32 regardless of input
    type (U8/I16/U16/F64/I64/I32 all -> I32) — the coerce-to-fixed-type signature
    of To Long Integer (I32), sitting in the To-<T> family (1140 I8, 1141 I16).
    Must round ties-to-even and saturate to the I32 range (matches 1140's style).
    """
    res = get_resolver()
    r = res.resolve(prim_id=1142)
    assert r.name == "To Long Integer"
    assert [t.type for t in r.terminals if t.direction == "out"] == ["NumInt32"]
    code = str(r.python_code)
    # ties-to-even rounding + saturation to the signed 32-bit range
    for value, expected in [(2.5, 2), (3.5, 4), (3e9, 2147483647), (-3e9, -2147483648)]:
        assert eval(code, {"in_1": value}) == expected


def test_specialized_node_types_carry_no_counter_indicated_primresid():
    """A specialized-XML-class handler (aDelete/aIndx/subset) must NOT hand-assign
    a primResID that belongs to a DIFFERENT plain-`prim` function. Codegen resolves
    node_type before primResID, so node-type wins — keeping both was a latent trap
    (1901=Search 1D Array, 1809=Array Size, 1516=Select all mean something else).
    These resolve fully via the node_types section, so their primResID must be None.
    """
    from lvkit.parser.node_types import NODE_HANDLERS

    res = get_resolver()
    for xml_class, expected in (
        ("aDelete", "Delete From Array"),
        ("aIndx", "Index Array"),
        ("subset", "Array Subset"),
    ):
        handler = NODE_HANDLERS[xml_class]
        assert handler.prim_res_id is None, (
            f"{xml_class} must not carry a counter-indicated primResID"
        )
        # It still resolves — by node_type, independently of any primResID.
        assert res.resolve_by_node_type(xml_class).name == expected


def test_arithmetic_block_is_not_build_array():
    """The 2-input arithmetic block must never resolve back to Build Array —
    1050 was mis-IDed that way (a scalar+array Add read as concatenation).
    Real Build Array is the expandable 'aBuild' node class, not a plain prim."""
    res = get_resolver()
    for prim_id in (1050, 1051, 1052, 1053):
        assert res.resolve(prim_id=prim_id).name != "Build Array"

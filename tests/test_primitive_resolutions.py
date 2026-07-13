"""Regression lock for primitive resolutions a real user VI depends on.

These primResIDs were each mislabeled or unknown and were resolved (by type +
consumer context) while making a Formula-Node calibration VI convert and run
end to end. They are plain data in primitives.json — trivial to revert by
accident — so this test pins the identities, the key python_code, and the
Build Array routing. No user IP: only resID → function-identity facts.

Four of these (1057, 1809, 1903, 1904) were ALL mislabeled "Array Size"; only
1809 is really Array Size. The collision guard below stops a future edit from
re-merging them.
"""

from __future__ import annotations

import pytest

from lvkit.primitive_resolver import get_resolver

EXPECTED_NAMES = {
    1809: "Array Size",
    1903: "Add Array Elements",
    1904: "Multiply Array Elements",
    1057: "Absolute Value",
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
    assert "abs(in_1)" in str(res.resolve(prim_id=1057).python_code)
    # Sign -> +/-1, To Byte Integer -> saturating I8
    assert "(in_1 > 0) - (in_1 < 0)" in str(res.resolve(prim_id=1049).python_code)
    assert "127" in str(res.resolve(prim_id=1140).python_code)
    # 2-input arithmetic: Add / Subtract / Multiply / Divide (operators give
    # LV's type coercion for free; Divide is true division -> always float).
    assert "in_1 + in_2" in str(res.resolve(prim_id=1050).python_code)
    assert "in_1 - in_2" in str(res.resolve(prim_id=1051).python_code)
    assert "in_1 * in_2" in str(res.resolve(prim_id=1052).python_code)
    assert "in_1 / in_2" in str(res.resolve(prim_id=1053).python_code)


def test_paren_if_compound_preserves_operand_precedence():
    """An inlined compound operand must be parenthesized so its precedence
    survives string substitution into an operator template (task #86)."""
    from lvkit.codegen.nodes.primitive import _paren_if_compound
    assert _paren_if_compound("z | x") == "(z | x)"   # BinOp -> wrapped
    assert _paren_if_compound("~x") == "(~x)"         # UnaryOp -> wrapped
    assert _paren_if_compound("a and b") == "(a and b)"  # BoolOp -> wrapped
    assert _paren_if_compound("a < b") == "(a < b)"   # Compare -> wrapped
    assert _paren_if_compound("x") == "x"             # Name -> unchanged
    assert _paren_if_compound("f(a)") == "f(a)"       # Call -> unchanged
    assert _paren_if_compound("a.b[0]") == "a.b[0]"   # Subscript -> unchanged


def test_boolean_logic_prims_have_integer_bitwise_variant():
    """And/Or carry a python_code_int template so integer operands emit bitwise
    &/| (LabVIEW polymorphism). Not is handled in codegen (needs a width mask)."""
    res = get_resolver()
    assert res.resolve(prim_id=1062).python_code_int == {"result": "in_1 & in_2"}
    assert res.resolve(prim_id=1061).python_code_int == {"result": "in_1 | in_2"}
    # bool path unchanged (idiomatic logical operators)
    assert "in_1 and in_2" in str(res.resolve(prim_id=1062).python_code)
    assert "in_1 or in_2" in str(res.resolve(prim_id=1061).python_code)


def test_numeric_primitives_are_elementwise():
    res = get_resolver()
    # Add, Subtract, Multiply, Sign all broadcast over arrays
    for prim_id in (1050, 1051, 1052, 1049):
        assert res.resolve(prim_id=prim_id).elementwise is True


def test_arithmetic_block_is_not_build_array():
    """The 2-input arithmetic block must never resolve back to Build Array —
    1050 was mis-IDed that way (a scalar+array Add read as concatenation).
    Real Build Array is the expandable 'aBuild' node class, not a plain prim."""
    res = get_resolver()
    for prim_id in (1050, 1051, 1052, 1053):
        assert res.resolve(prim_id=prim_id).name != "Build Array"

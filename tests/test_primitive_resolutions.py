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

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import _generate_primitive
from lvkit.models import LVType, PrimitiveOperation, Terminal
from lvkit.primitive_resolver import get_resolver

EXPECTED_NAMES = {
    1809: "Array Size",
    1903: "Add Array Elements",
    1904: "Multiply Array Elements",
    1057: "Absolute Value",
    1049: "Sign",
    1140: "To Byte Integer",
    1051: "Subtract",
    1050: "Build Array",
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


def test_numeric_primitives_are_elementwise():
    res = get_resolver()
    for prim_id in (1051, 1049):  # Subtract, Sign broadcast over arrays
        assert res.resolve(prim_id=prim_id).elementwise is True


def test_build_array_prim_1050_concatenates_not_nests():
    """prim 1050 (plain Build Array) must route to the concatenating handler
    ([scalar] + array), not nest into [scalar, array]."""
    arr = LVType(kind="array", underlying_type="Array",
                 element_type=LVType(kind="primitive", underlying_type="NumFloat64"))
    dbl = LVType(kind="primitive", underlying_type="NumFloat64")
    op = PrimitiveOperation(
        id="vi::1", name="Build Array", labels=["Primitive"],
        node_type="prim", primResID=1050,
        terminals=[
            Terminal(id="o", index=0, direction="output", name="appended", lv_type=arr),
            Terminal(id="s", index=1, direction="input", name="scalar", lv_type=dbl),
            Terminal(id="a", index=2, direction="input", name="arr", lv_type=arr),
        ],
    )
    frag = _generate_primitive(op, CodeGenContext(vi_name="vi"))
    import ast
    mod = ast.Module(body=list(frag.statements), type_ignores=[])
    ast.fix_missing_locations(mod)
    src = ast.unparse(mod)
    # The concatenating handler wraps the scalar and joins with '+';
    # the (wrong) nesting path emits a 2-element list literal `[x, y]`.
    assert " + " in src, f"expected concatenation, got: {src}"
    assert "[None, None]" not in src

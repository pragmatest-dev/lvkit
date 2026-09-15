"""Regression test for issue #96: ``_decode_element``'s array/cluster recursion
must not re-copy the remaining payload on every recursive call.

``_decode_element`` used to recurse with ``data[idx:]`` where ``data`` was a
plain ``bytes`` object -- each such slice COPIES the remaining bytes, so
decoding an N-element array did O(N) copies of O(N) bytes each: O(N^2)
overall. A real VI with a 65,160,056-byte NumInt32 array default made
``parse_vi()`` effectively hang inside one parallel-parse worker. The fix
converts ``data`` to a ``memoryview`` once at the top of ``_decode_element``
so every recursive ``data[idx:]`` is a non-copying subview.
"""

from __future__ import annotations

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.parser import vi as _vi
from lvkit.parser.vi import _decode_element

_INT32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _int32_array_type() -> LVType:
    return LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=_INT32)


def _build_int32_array_buf(values: list[int]) -> bytes:
    buf = len(values).to_bytes(4, "big")
    for v in values:
        buf += v.to_bytes(4, "big", signed=True)
    return buf


def test_int32_array_decodes_correct_values():
    """Small correctness case: a 3-element NumInt32 array."""
    buf = _build_int32_array_buf([1, 2, 3])
    val, consumed = _decode_element(buf, _int32_array_type())
    assert val == "[1, 2, 3]"
    assert consumed == len(buf)


def test_cluster_containing_array_decodes_correct_values():
    """A cluster containing an array field stays byte-aligned across the
    array recursion (the field that used to be desynced by a copy bug)."""
    cluster_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(name="tag", type=_INT32),
            ClusterField(name="values", type=_int32_array_type()),
            ClusterField(name="trailer", type=_INT32),
        ],
    )
    array_buf = _build_int32_array_buf([10, 20, 30])
    buf = (99).to_bytes(4, "big", signed=True) + array_buf
    buf += (7).to_bytes(4, "big", signed=True)

    val, consumed = _decode_element(buf, cluster_type)
    assert val is not None
    assert "'tag': 99" in val
    assert "'values': [10, 20, 30]" in val
    assert "'trailer': 7" in val
    assert consumed == len(buf)


def test_array_recursion_slices_noncopying_views(monkeypatch):
    """The array recursion must reach each element through a NON-COPYING view of
    the one input buffer, never a fresh ``bytes`` copy of the remaining payload.

    That property -- not a wall clock -- is what makes the decode O(N) instead of
    O(N^2): a per-element ``bytes`` copy is O(len-so-far), summing to O(N^2); a
    ``memoryview`` slice is O(1). A timing bound would be flaky (machine- and
    load-dependent) and couldn't actually distinguish O(N) from O(N^2). Instead we
    assert the mechanism directly: every recursive ``_decode_element`` call for an
    element receives a ``memoryview`` whose ``.obj`` is the ORIGINAL buffer (a
    slice shares its root object; a copy would be a distinct ``bytes``).
    """
    buf = _build_int32_array_buf([10, 20, 30, 40, 50])

    seen: list[object] = []
    real = _vi._decode_element

    def _spy(data, elem_type):
        seen.append(data)
        return real(data, elem_type)

    # Patch the module global so the array branch's internal recursive call (which
    # resolves ``_decode_element`` through the module namespace) hits the spy.
    monkeypatch.setattr(_vi, "_decode_element", _spy)

    val, consumed = _vi._decode_element(buf, _int32_array_type())

    # Correctness is unchanged (the fix only avoids copying).
    assert val == "[10, 20, 30, 40, 50]"
    assert consumed == len(buf)

    # seen[0] is the top-level array call (the raw buffer); seen[1:] are the five
    # per-element decodes. Each element decode must be a memoryview VIEW of ``buf``.
    element_calls = seen[1:]
    assert len(element_calls) == 5, "expected one recursive decode per element"
    views = [d for d in element_calls if isinstance(d, memoryview)]
    assert len(views) == len(element_calls), (
        "an element decode received a non-view (a bytes copy) — the O(N^2) "
        "re-copy regression is back (see issue #96)"
    )
    assert all(v.obj is buf for v in views), (
        "an element view does not share the original buffer — payload was copied"
    )

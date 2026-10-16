"""A by-name drawer's field index counts every sub-cluster as its own slot
(#134), in codegen exactly as in the display resolver."""

from __future__ import annotations

from lvkit.codegen.nodes.nmux import _field_expr, _field_name
from lvkit.graph.op_walk import _resolve_nmux_field_name
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal


def _leaf(name: str) -> ClusterField:
    return ClusterField(name=name, type=None)


# Private data shaped like the report: leaves, then a nested sub-cluster, then
# more leaves. Full flatten: 0 F0, 1 F1, 2 S, 3 s1, 4 s2, 5 T.
FIELDS = [
    _leaf("F0"),
    _leaf("F1"),
    ClusterField(
        name="S",
        type=LVType(kind=LVTypeKind.CLUSTER, fields=[_leaf("s1"), _leaf("s2")]),
    ),
    _leaf("T"),
]


def _drawer(index: int) -> Terminal:
    return Terminal(
        id=f"d{index}",
        index=1,
        direction="output",
        nmux_role="list",
        nmux_field_index=index,
    )


def test_fields_after_a_subcluster_resolve_to_their_own_slot():
    assert [_resolve_nmux_field_name(i, FIELDS) for i in range(6)] == [
        "F0",
        "F1",
        "S",
        "s1",
        "s2",
        "T",
    ]


def test_codegen_reads_the_same_slot_as_the_display():
    # Index 3 is below the top-level field count (4), so it must still be read
    # as a flattened slot, not as top-level field 3 ("T").
    assert _field_name(_drawer(3), FIELDS) == "s1"
    # Python identifiers are lowercased by to_var_name.
    assert _field_expr(_drawer(3), "obj", FIELDS) == "obj.s.s1"
    assert _field_expr(_drawer(2), "obj", FIELDS) == "obj.s"
    assert _field_expr(_drawer(5), "obj", FIELDS) == "obj.t"

"""A by-name drawer's field index counts every sub-cluster as its own slot
(#134), in codegen exactly as in the display resolver."""

from __future__ import annotations

from lvkit.codegen.cluster_members import ClusterShape, member_path, read_member
from lvkit.graph.op_walk import _resolve_nmux_field_name
from lvkit.models import ClusterField, LVType, LVTypeKind

_NAMED = LVType(kind=LVTypeKind.CLUSTER, typedef_name="T.ctl")


def _leaf(name: str) -> ClusterField:
    return ClusterField(name=name, type=None)


# Private data shaped like the report: leaves, then a nested sub-cluster, then
# more leaves. Full flatten: 0 F0, 1 F1, 2 S, 3 s1, 4 s2, 5 T.
FIELDS = [
    _leaf("F0"),
    _leaf("F1"),
    ClusterField(
        name="S",
        type=LVType(
            kind=LVTypeKind.CLUSTER,
            typedef_name="S.ctl",
            fields=[_leaf("s1"), _leaf("s2")],
        ),
    ),
    _leaf("T"),
]


def _read(index: int) -> str:
    path = member_path(index, ClusterShape(_NAMED, FIELDS))
    assert path is not None
    return read_member("obj", path)


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
    assert _read(3) == "obj.s.s1"
    assert _read(2) == "obj.s"
    assert _read(5) == "obj.t"

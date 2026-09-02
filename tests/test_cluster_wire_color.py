"""The cluster wire color rule: a cluster is BROWN only when every field is
numeric/boolean (a fixed-size numeric cluster); any string/array/refnum/etc.
field makes it a variable-size "common" cluster -> PINK (NI's rule). Fields
unknown (resolved lazily) keep the brown default."""

from __future__ import annotations

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.render.style import DEFAULT_THEME, type_family, wire_style


def _prim(underlying: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=underlying)


def _cluster(*fields: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[ClusterField(f"f{i}", t) for i, t in enumerate(fields)],
    )


def test_all_numeric_cluster_is_brown() -> None:
    c = _cluster(_prim("NumInt32"), _prim("NumFloat64"), _prim("Boolean"))
    assert type_family(c) == "cluster"
    assert wire_style(c).color == DEFAULT_THEME.wire_cluster


def test_cluster_with_string_field_is_pink() -> None:
    c = _cluster(_prim("NumInt32"), _prim("String"))
    assert type_family(c) == "cluster_mixed"
    assert wire_style(c).color == DEFAULT_THEME.wire_cluster_mixed


def test_cluster_with_array_field_is_pink() -> None:
    arr = LVType(kind=LVTypeKind.ARRAY, element_type=_prim("NumFloat64"))
    assert type_family(_cluster(_prim("NumInt32"), arr)) == "cluster_mixed"


def test_nested_all_numeric_cluster_stays_brown() -> None:
    inner = _cluster(_prim("NumInt32"), _prim("NumFloat64"))
    assert type_family(_cluster(_prim("NumInt32"), inner)) == "cluster"


def test_fields_unknown_defaults_to_brown() -> None:
    assert type_family(LVType(kind=LVTypeKind.CLUSTER, underlying_type="Cluster")) == (
        "cluster"
    )

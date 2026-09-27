"""``TypeCatalog``: structural type identity for the index."""

from __future__ import annotations

from lvkit.index.types import TypeCatalog
from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind

_DBL = LVType(LVTypeKind.PRIMITIVE, underlying_type="DBL")
_STR = LVType(LVTypeKind.PRIMITIVE, underlying_type="String")


def _cluster(*fields: tuple[str, LVType | None], name: str | None = None) -> LVType:
    return LVType(
        LVTypeKind.CLUSTER,
        typedef_name=name,
        fields=[ClusterField(n, t) for n, t in fields],
    )


def _id(lv_type: LVType) -> str | None:
    return TypeCatalog().add(lv_type)


def test_the_same_structure_has_the_same_id_wherever_it_occurs() -> None:
    other_dbl = LVType(LVTypeKind.PRIMITIVE, underlying_type="DBL")  # a distinct object
    assert _id(_cluster(("a", _DBL), ("b", _STR))) == _id(
        _cluster(("a", other_dbl), ("b", _STR))
    )


def test_a_different_field_name_type_or_order_is_a_different_type() -> None:
    base = _id(_cluster(("a", _DBL), ("b", _STR)))
    assert _id(_cluster(("a", _DBL), ("c", _STR))) != base  # renamed
    assert _id(_cluster(("a", _DBL), ("b", _DBL))) != base  # retyped
    assert _id(_cluster(("b", _STR), ("a", _DBL))) != base  # reordered


def test_a_name_is_not_identity() -> None:
    same_name_a = _cluster(("x", _DBL), name="Config.ctl")
    same_name_b = _cluster(("x", _STR), name="Config.ctl")
    assert _id(same_name_a) != _id(same_name_b)
    # and the name is part of the id: the same structure under another name differs
    assert _id(same_name_a) != _id(_cluster(("x", _DBL), name="Other.ctl"))


def test_enum_items_are_part_of_the_id_in_value_order() -> None:
    def enum(**items: int) -> LVType:
        return LVType(
            LVTypeKind.ENUM, values={n: EnumValue(v) for n, v in items.items()}
        )

    assert _id(enum(Read=0, Write=1)) == _id(enum(Write=1, Read=0))  # dict order
    assert _id(enum(Read=0, Write=1)) != _id(enum(Read=0, Write=2))
    assert _id(enum(Read=0, Write=1)) != _id(enum(Read=0, Stop=1))


def test_arrays_hash_their_element_and_dimensions() -> None:
    one = LVType(LVTypeKind.ARRAY, element_type=_DBL, dimensions=1)
    two = LVType(LVTypeKind.ARRAY, element_type=_DBL, dimensions=2)
    assert _id(one) != _id(two)
    assert _id(one) != _id(LVType(LVTypeKind.ARRAY, element_type=_STR, dimensions=1))


def test_the_catalog_records_each_part_once_with_its_structure() -> None:
    catalog = TypeCatalog()
    inner = _cluster(("r", _DBL))
    outer = _cluster(("in1", inner), ("in2", inner), ("s", _STR))
    root = catalog.add(outer)
    assert root is not None
    fact = catalog.types[root]
    assert [f.name for f in fact.fields] == ["in1", "in2", "s"]
    assert fact.fields[0].type_id == fact.fields[1].type_id  # one shared inner type
    assert len(catalog.types) == 4  # outer, inner, DBL, String
    assert catalog.types[fact.fields[0].type_id].fields[0].name == "r"  # type: ignore[index]


def test_a_missing_type_has_no_id_and_an_unresolved_field_is_kept() -> None:
    assert _id(None) is None  # type: ignore[arg-type]
    catalog = TypeCatalog()
    root = catalog.add(_cluster(("gone", None)))
    assert root is not None
    assert catalog.types[root].fields[0].type_id is None


def test_a_typedef_reference_is_resolved_through_the_graph_callback() -> None:
    reference = LVType(LVTypeKind.TYPEDEF_REF, typedef_name="Config.ctl")
    resolved = TypeCatalog(
        lambda t: [ClusterField("x", _DBL)] if t.typedef_name == "Config.ctl" else None
    )
    root = resolved.add(reference)
    assert root is not None and [f.name for f in resolved.types[root].fields] == ["x"]
    # without a resolver the same reference is a different (fieldless) type
    assert TypeCatalog().add(reference) != root


def test_a_type_nested_in_itself_terminates() -> None:
    loop = _cluster(("v", _DBL), name="Loop.ctl")
    loop.fields = [ClusterField("v", _DBL), ClusterField("self", loop)]
    root = TypeCatalog().add(loop)
    assert root is not None


def test_a_freed_type_never_lends_its_memo_to_another() -> None:
    catalog = TypeCatalog()
    dbl, string = _id(_DBL), _id(_STR)
    for i in range(2000):
        transient = LVType(
            LVTypeKind.PRIMITIVE, underlying_type="DBL" if i % 2 else "String"
        )
        assert catalog.add(transient) == (dbl if i % 2 else string)
        del transient


def test_a_type_in_a_cycle_has_the_same_id_whichever_end_is_reached_first() -> None:
    a = _cluster(("v", _DBL), name="A.ctl")
    b = _cluster(("v", _STR), name="B.ctl")
    a.fields = [ClusterField("b", b)]
    b.fields = [ClusterField("a", a)]
    first_a = TypeCatalog()
    first_a.add(a)
    only_b = TypeCatalog().add(b)
    assert first_a.add(b) == only_b

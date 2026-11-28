"""Variant To Data into a cluster or an array converts the variant's value down to
the wired type (NI: ``data`` "is the variant changed to the data type specified by
type"): each member of a cluster and each element of an array to its own type, a
member that is a variant read as its value. Tiny handmade diagrams
(``native_vi.py``), generated and executed."""

from __future__ import annotations

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.helpers import error_cluster_type, generated_variants
from tests.native_vi import array, generate, node, scalar

VARIANT_TO_DATA = 8003
VARIANT = scalar("Variant")
DBL = scalar("NumFloat64")
I32 = scalar("NumInt32")
INCOMPATIBLE = (
    "The data type of the variant is not compatible with the data type wired "
    "to the type input."
)


def cluster(*types: LVType) -> LVType:
    return LVType(
        LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[ClusterField(f"f{i}", t) for i, t in enumerate(types)],
    )


def _convert(data_type: LVType):
    terminals = [
        (0, "input", error_cluster_type()),
        (3, "output", error_cluster_type()),
        (8, "input", VARIANT),
        (9, "input", data_type),
        (11, "output", data_type),
    ]
    return generate(
        {"v": VARIANT},
        {"d": data_type, "error_out": error_cluster_type()},
        [node("vd", VARIANT_TO_DATA, terminals)],
        [("fp:v", "vd:8"), ("vd:11", "fp:d"), ("vd:3", "fp:error_out")],
    )


def _variants():
    variants = generated_variants()
    return (
        variants,
        lambda value, name: variants.Variant(
            value, getattr(variants, f"LV_{name}")
        ),
        lambda code, value: variants.Variant(
            value, variants.LVType(code, "held")
        ),
    )


def test_a_variant_holding_a_cluster_of_variants_is_the_typed_cluster() -> None:
    """The shape JDP's Array of Variants to Cluster.vi builds: a cluster whose
    members are variants, one of an array of variants (each a cluster)."""
    target = cluster(array(cluster(DBL, I32)), DBL)
    run = _convert(target)
    assert "convert_to_type(v, " in run.source
    _, typed, held = _variants()
    items = held(
        64,
        [
            held(80, (typed(1.5, "DBL"), typed(2.5, "DBL"))),
            held(80, (typed(0.5, "DBL"), typed(7, "I32"))),
        ],
    )
    result = run.function(held(80, (items, typed(1, "I32"))))
    assert result.d == ([(1.5, 2), (0.5, 7)], 1.0)
    assert isinstance(result.d[1], float)
    assert result.error_out is None


def test_an_array_of_clusters_with_variant_fields() -> None:
    run = _convert(array(cluster(DBL, I32)))
    _, typed, held = _variants()
    result = run.function(
        held(
            64,
            [
                held(80, (typed(1, "I32"), typed(2.5, "DBL"))),
                (typed(2.0, "DBL"), 3),
            ],
        )
    )
    assert result.d == [(1.0, 2), (2.0, 3)]
    assert isinstance(result.d[0][0], float) and isinstance(result.d[0][1], int)


def test_a_nested_array_of_variants() -> None:
    """A two-dimensional array: its rows are lists, its elements variants."""
    run = _convert(array(DBL, rank=2))
    _, typed, held = _variants()
    given = held(
        64, [[typed(1, "I32"), typed(2.5, "DBL")], [typed(3, "I32"), 4.0], []]
    )
    assert run.function(given).d == [[1.0, 2.5], [3.0, 4.0], []]


def test_a_variant_member_stays_a_variant_and_a_bare_value_becomes_one() -> None:
    """The converter to a type with a Variant member (a hand-written type string:
    a cluster of a Variant and an I32): a variant is kept, a Boolean, a double or
    a string is made a variant of its own type."""
    variants, typed, _ = _variants()
    target = variants.LVType(80, "cluster", (14, 80, 2, 4, 83, 4, 3))
    given = typed("a", "String")
    assert variants.convert_to_type((given, 5.0), target) == (given, 5)
    assert variants.convert_to_type(("b", 1), target) == (typed("b", "String"), 1)
    assert variants.convert_to_type((True, 1), target)[0] == typed(True, "TF")


def test_a_variant_of_a_variant_is_read_through_to_its_data() -> None:
    """A cluster member read as a variant of its own type (a Variant) holds the
    member variant; Variant To Data reads through it."""
    run = _convert(cluster(DBL, array(I32)))
    _, typed, held = _variants()
    given = held(
        80,
        (
            held(83, typed(1.5, "DBL")),
            held(64, [held(83, typed(2, "I32")), typed(3, "I32")]),
        ),
    )
    assert run.function(given).d == (1.5, [2, 3])


def test_a_variant_that_does_not_fit_is_error_91() -> None:
    run = _convert(cluster(DBL, I32))
    _, typed, held = _variants()
    for given in (
        held(80, (typed("not a number", "String"), typed(1, "I32"))),
        held(80, (typed(1.0, "DBL"),)),  # one member short
        held(64, [typed(1.0, "DBL")]),  # an array, not a cluster
        held(80, (held(80, ()), typed(1, "I32"))),  # a member that is a cluster
        _variants()[0].Variant(),  # empty
    ):
        error = run.function(given).error_out
        assert (error.code, error.source) == (91, "Variant To Data")
        assert error.message == INCOMPATIBLE
    # An element of an array that does not fit.
    arrays = _convert(array(DBL))
    error = arrays.function(held(64, [typed(1.0, "DBL"), typed("s", "String")]))
    assert error.error_out.code == 91 and error.d == []

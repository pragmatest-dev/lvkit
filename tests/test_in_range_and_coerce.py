"""In Range and Coerce (primitive 1127) runs as Python on a scalar and, element
by element, on an array.

NI (In Range and Coerce): "you can wire an array to x and two scalars to upper
limit and lower limit"; "In Compare Elements mode, the data type structure of
In Range? matches the data type structure of x, with each scalar replaced by a
Boolean value"; coerced(x) is x, or the lower or upper limit when x is outside;
"If the lower limit value is greater than the upper limit value, In Range? is
always FALSE and LabVIEW switches the lower limit value and the upper limit
value internally before computing coerced(x)"."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.codegen.nodes.ops.in_range_and_coerce import (
    INCLUDE_LOWER_LIMIT,
    INCLUDE_UPPER_LIMIT,
)
from lvkit.graph import InMemoryVIGraph
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.timestamp_value import TIMESTAMP_FLAVOR, timestamp_from_parts
from tests.native_vi import array, scalar
from tests.primitive_caller import run_primitive
from tests.vilib_caller import BOOL, DBL

TIMESTAMP = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)
BOOL_ARRAY = array("Boolean")
DBL_ARRAY = array("NumFloat64")


def _cluster(*fields, named: bool = False) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Limits" if named else None,
        fields=[ClusterField(name=name, type=type_) for name, type_ in fields],
    )


def _run(lower, x, upper, *, array_limits: bool = False, flags: int = 0):
    x_is_array = isinstance(x, list)
    limit_type = DBL_ARRAY if array_limits else DBL
    pane = [
        (0, "output", BOOL_ARRAY if x_is_array else BOOL),
        (1, "output", DBL_ARRAY if x_is_array else DBL),
        (2, "input", limit_type),
        (3, "input", DBL_ARRAY if x_is_array else DBL),
        (4, "input", limit_type),
    ]
    inputs = {
        "lower": (2, limit_type),
        "x": (3, DBL_ARRAY if x_is_array else DBL),
        "upper": (4, limit_type),
    }
    outputs = {
        "in_range": (0, BOOL_ARRAY if x_is_array else BOOL),
        "coerced": (1, DBL_ARRAY if x_is_array else DBL),
    }
    return run_primitive(
        1127,
        pane,
        inputs,
        outputs,
        {"lower": lower, "x": x, "upper": upper},
        name="InRange",
        obj_flags=flags,
    )[1]


def test_scalar_in_range_is_unchanged() -> None:
    result = _run(48.0, 50.0, 57.0)
    assert result.in_range is True
    assert result.coerced == 50.0


@pytest.mark.parametrize(
    ("flags", "at_lower", "at_upper"),
    [
        (0, True, False),
        (INCLUDE_LOWER_LIMIT, True, False),
        (INCLUDE_LOWER_LIMIT | INCLUDE_UPPER_LIMIT, True, True),
        (INCLUDE_UPPER_LIMIT, False, True),
    ],
    ids=["no-flags-default", "lower-only", "both", "upper-only"],
)
def test_each_limit_is_included_as_the_node_records(
    flags: int, at_lower: bool, at_upper: bool
) -> None:
    assert _run(48.0, 48.0, 57.0, flags=flags).in_range is at_lower
    assert _run(48.0, 57.0, 57.0, flags=flags).in_range is at_upper
    assert _run(48.0, 52.0, 57.0, flags=flags).in_range is True
    # coerced(x) clamps to the exact limits whatever the flags say.
    assert _run(48.0, 48.0, 57.0, flags=flags).coerced == 48.0
    assert _run(48.0, 57.0, 57.0, flags=flags).coerced == 57.0


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (INCLUDE_LOWER_LIMIT, [False, True, True, False, False]),
        (
            INCLUDE_LOWER_LIMIT | INCLUDE_UPPER_LIMIT,
            [False, True, True, True, False],
        ),
    ],
    ids=["upper-excluded", "upper-included"],
)
def test_an_array_applies_the_flags_to_every_element(
    flags: int, expected: list[bool]
) -> None:
    result = _run(48.0, [47.0, 48.0, 52.0, 57.0, 58.0], 57.0, flags=flags)
    assert result.in_range == expected


def test_swapped_limits_are_never_in_range_with_any_flags() -> None:
    both = INCLUDE_LOWER_LIMIT | INCLUDE_UPPER_LIMIT
    assert _run(57.0, 52.0, 48.0, flags=both).in_range is False
    assert _run(57.0, 52.0, 48.0, flags=both).coerced == 52.0


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ("", 0),
        ("<objFlags>2097152</objFlags>", 2097152),
        ("<objFlags>3145728</objFlags>", 3145728),
    ],
)
def test_node_records_its_flags(tmp_path: Path, flags: str, expected: int) -> None:
    terms = "".join(
        f'<SL__arrayElement class="term" uid="{uid}">'
        f'<dco class="parm" uid="{uid + 1}"><parmIndex>{index}</parmIndex>'
        "</dco></SL__arrayElement>"
        for uid, index in ((11, 2), (13, 3), (15, 4))
    )
    bd_xml = tmp_path / "IR_BDHb.xml"
    bd_xml.write_text(
        "<?xml version='1.0' encoding='utf-8'?>"
        '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
        '<zPlaneList elements="0" /><nodeList elements="1">'
        '<SL__arrayElement class="prim" uid="10"><primResID>1127</primResID>'
        f'{flags}<termList elements="3">{terms}</termList></SL__arrayElement>'
        '</nodeList><signalList elements="0" /></root></SL__rootObject>',
        encoding="utf-8",
    )
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    name = graph.list_vis()[0]
    (node,) = [
        n for n in graph.top_level_nodes(name) if getattr(n, "prim_id", 0) == 1127
    ]
    assert node.obj_flags == expected  # type: ignore[attr-defined]


def test_scalar_outside_values_are_coerced() -> None:
    assert _run(48.0, 47.0, 57.0).in_range is False
    assert _run(48.0, 47.0, 57.0).coerced == 48.0
    assert _run(48.0, 60.0, 57.0).coerced == 57.0


def test_swapped_limits_are_never_in_range_and_coerce_between_them() -> None:
    result = _run(57.0, 60.0, 48.0)
    assert result.in_range is False
    assert result.coerced == 57.0


def test_array_x_with_scalar_limits_is_element_wise() -> None:
    result = _run(48.0, [47.0, 48.0, 52.0, 57.0, 58.0], 57.0)
    assert result.in_range == [False, True, True, False, False]
    assert result.coerced == [48.0, 48.0, 52.0, 57.0, 57.0]


def test_array_x_with_array_limits_pairs_the_elements() -> None:
    result = _run(
        [0.0, 10.0, 20.0],
        [-5.0, 15.0, 30.0],
        [5.0, 12.0, 25.0],
        array_limits=True,
    )
    assert result.in_range == [False, False, False]
    assert result.coerced == [0.0, 12.0, 25.0]


def test_the_generated_array_code_is_a_comprehension() -> None:
    pane = [
        (0, "output", BOOL_ARRAY),
        (1, "output", DBL_ARRAY),
        (2, "input", DBL),
        (3, "input", DBL_ARRAY),
        (4, "input", DBL),
    ]
    source, _ = run_primitive(
        1127,
        pane,
        {"lower": (2, DBL), "x": (3, DBL_ARRAY), "upper": (4, DBL)},
        {"in_range": (0, BOOL_ARRAY)},
        {"lower": 48.0, "x": [1.0], "upper": 57.0},
        name="InRange",
    )
    assert "for _e0 in x" in source


def _run_shapes(x_type, limit_types, result_type, lower, x, upper, flags=0):
    """A node whose x, lower and upper have the given types; ``result_type`` is
    the type of In Range? and ``x_type`` that of coerced(x)."""
    lower_type, upper_type = limit_types
    pane = [
        (0, "output", result_type),
        (1, "output", x_type),
        (2, "input", lower_type),
        (3, "input", x_type),
        (4, "input", upper_type),
    ]
    return run_primitive(
        1127,
        pane,
        {"lower": (2, lower_type), "x": (3, x_type), "upper": (4, upper_type)},
        {"in_range": (0, result_type), "coerced": (1, x_type)},
        {"lower": lower, "x": x, "upper": upper},
        name="InRange",
        obj_flags=flags,
    )


def _run_typed(lv_type, lower, x, upper, *, flags: int = 0, gaps=None):
    pane = [
        (0, "output", BOOL),
        (1, "output", lv_type),
        (2, "input", lv_type),
        (3, "input", lv_type),
        (4, "input", lv_type),
    ]
    return run_primitive(
        1127,
        pane,
        {"lower": (2, lv_type), "x": (3, lv_type), "upper": (4, lv_type)},
        {"in_range": (0, BOOL), "coerced": (1, lv_type)},
        {"lower": lower, "x": x, "upper": upper},
        name="InRange",
        obj_flags=flags,
        gaps=gaps,
    )[1]


def test_a_string_is_ordered_by_character_code() -> None:
    both = INCLUDE_LOWER_LIMIT | INCLUDE_UPPER_LIMIT
    inside = _run_typed(scalar("String"), "b", "bz", "c", flags=both)
    assert inside.in_range is True
    assert inside.coerced == "bz"
    outside = _run_typed(scalar("String"), "b", "a", "c", flags=both)
    assert outside.in_range is False
    assert outside.coerced == "b"
    # Upper case sorts below lower case by ASCII code.
    assert _run_typed(scalar("String"), "a", "B", "c").in_range is False


def test_an_unsigned_byte_is_ordered_as_an_integer() -> None:
    result = _run_typed(scalar("NumUInt8"), 10, 200, 100)
    assert result.in_range is False
    assert result.coerced == 100
    assert _run_typed(scalar("NumUInt8"), 10, 50, 100).in_range is True


def test_an_enum_is_ordered_by_its_number() -> None:
    enum = LVType(kind=LVTypeKind.ENUM, underlying_type="NumUInt16")
    result = _run_typed(enum, 12, 25, 21)
    assert result.in_range is False
    assert result.coerced == 21
    assert _run_typed(enum, 12, 15, 21).in_range is True
    # The upper limit is excluded by default.
    assert _run_typed(enum, 12, 21, 21).in_range is False


def test_a_boolean_is_ordered_false_below_true() -> None:
    both = INCLUDE_LOWER_LIMIT | INCLUDE_UPPER_LIMIT
    assert _run_typed(BOOL, False, True, True, flags=both).in_range is True
    assert _run_typed(BOOL, True, False, True, flags=both).in_range is False
    assert _run_typed(BOOL, True, False, True, flags=both).coerced is True


def test_a_time_stamp_is_ordered_by_time() -> None:
    t = lambda seconds: timestamp_from_parts(seconds, 0)  # noqa: E731
    result = _run_typed(TIMESTAMP, t(100), t(500), t(300))
    assert result.in_range is False
    assert result.coerced == t(300)
    assert _run_typed(TIMESTAMP, t(100), t(200), t(300)).in_range is True


@pytest.mark.parametrize(
    ("lv_type", "reason"),
    [
        (scalar("NumComplex128"), "complex input"),
        (scalar("Path"), "path input"),
        (LVType(kind=LVTypeKind.CLUSTER), "cluster input"),
        (_cluster(("a", DBL), ("p", scalar("Path"))), "path input"),
    ],
    ids=["complex", "path", "empty-cluster", "cluster-with-a-path"],
)
def test_types_NI_does_not_order_are_a_gap(lv_type, reason) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError) as raised:
        _run_typed(lv_type, None, None, None, gaps=gaps)
    assert reason in str(raised.value)
    assert [g.kind for g in gaps]


# A cluster is compared in Compare Elements mode (NI, Using Comparison Functions:
# functions "compare an array or cluster of a data type to a scalar of the same
# data type and produce an array or cluster of Boolean values").
X_TYPE = _cluster(("a", DBL), ("n", scalar("NumInt32")))
FLAGS_TYPE = _cluster(("a", BOOL), ("n", BOOL))
BOTH = INCLUDE_LOWER_LIMIT | INCLUDE_UPPER_LIMIT


def test_a_cluster_x_with_scalar_limits_is_compared_field_by_field() -> None:
    _, result = _run_shapes(
        X_TYPE, (DBL, DBL), FLAGS_TYPE, 0.0, (5.0, 20), 10.0, flags=BOTH
    )
    assert result.in_range == (True, False)
    assert result.coerced == (5.0, 10.0)


def test_a_cluster_x_with_cluster_limits_pairs_the_fields() -> None:
    _, result = _run_shapes(
        X_TYPE, (X_TYPE, X_TYPE), FLAGS_TYPE, (0.0, 1), (5.0, 5), (4.0, 7)
    )
    assert result.in_range == (False, True)
    assert result.coerced == (4.0, 5)


def test_the_include_upper_flag_applies_to_a_field_at_the_limit() -> None:
    for flags, expected in ((INCLUDE_LOWER_LIMIT, False), (BOTH, True)):
        _, result = _run_shapes(
            X_TYPE, (DBL, DBL), FLAGS_TYPE, 0.0, (3.0, 10), 10.0, flags=flags
        )
        assert result.in_range == (True, expected)
        assert result.coerced == (3.0, 10)


def test_swapped_cluster_limits_are_never_in_range_and_coerce_between_them() -> None:
    _, result = _run_shapes(
        X_TYPE, (DBL, DBL), FLAGS_TYPE, 10.0, (5.0, 5), 0.0, flags=BOTH
    )
    assert result.in_range == (False, False)
    assert result.coerced == (5.0, 5)


def test_a_named_cluster_comes_back_as_a_named_cluster() -> None:
    named = _cluster(("a", DBL), ("b", DBL), named=True)
    flags_type = _cluster(("a", BOOL), ("b", BOOL), named=True)
    _, result = _run_shapes(
        named,
        (named, named),
        flags_type,
        SimpleNamespace(a=0.0, b=0.0),
        SimpleNamespace(a=5.0, b=50.0),
        SimpleNamespace(a=10.0, b=10.0),
    )
    assert result.in_range == SimpleNamespace(a=True, b=False)
    assert result.coerced == SimpleNamespace(a=5.0, b=10.0)


def test_in_range_is_built_in_the_type_of_its_output() -> None:
    named = _cluster(("a", DBL), ("b", DBL), named=True)
    _, result = _run_shapes(
        named,
        (DBL, DBL),
        _cluster(("a", BOOL), ("b", BOOL)),
        0.0,
        SimpleNamespace(a=5.0, b=50.0),
        10.0,
    )
    assert result.in_range == (True, False)
    assert result.coerced == SimpleNamespace(a=5.0, b=10.0)


def test_an_array_of_clusters_is_compared_in_every_element() -> None:
    array_x = array(X_TYPE)
    _, result = _run_shapes(
        array_x,
        (DBL, DBL),
        array(FLAGS_TYPE),
        0.0,
        [(1.0, 2), (20.0, 3)],
        10.0,
    )
    assert result.in_range == [(True, True), (False, True)]
    assert result.coerced == [(1.0, 2), (10.0, 3)]


def test_an_array_in_a_cluster_is_compared_element_by_element() -> None:
    inner = _cluster(("v", DBL_ARRAY), ("k", DBL))
    flags_type = _cluster(("v", BOOL_ARRAY), ("k", BOOL))
    _, result = _run_shapes(
        inner, (DBL, DBL), flags_type, 0.0, ([1.0, 20.0], 5.0), 10.0
    )
    assert result.in_range == ([True, False], True)
    assert result.coerced == ([1.0, 10.0], 5.0)


def test_clusters_of_different_shape_are_a_gap() -> None:
    other = _cluster(("a", DBL))
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError) as raised:
        run_primitive(
            1127,
            [
                (0, "output", FLAGS_TYPE),
                (1, "output", X_TYPE),
                (2, "input", other),
                (3, "input", X_TYPE),
                (4, "input", DBL),
            ],
            {"lower": (2, other), "x": (3, X_TYPE), "upper": (4, DBL)},
            {"in_range": (0, FLAGS_TYPE), "coerced": (1, X_TYPE)},
            {"lower": (0.0,), "x": (1.0, 2), "upper": 5.0},
            name="InRange",
            gaps=gaps,
        )
    assert "same shape" in str(raised.value)

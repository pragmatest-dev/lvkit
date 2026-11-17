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

import pytest

from lvkit.codegen.nodes.ops.in_range_and_coerce import (
    INCLUDE_LOWER_LIMIT,
    INCLUDE_UPPER_LIMIT,
)
from lvkit.graph import InMemoryVIGraph
from tests.native_vi import array
from tests.primitive_caller import run_primitive
from tests.vilib_caller import BOOL, DBL

BOOL_ARRAY = array("Boolean")
DBL_ARRAY = array("NumFloat64")


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
        '</nodeList><signalList elements="0" /></root></SL__rootObject>'
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

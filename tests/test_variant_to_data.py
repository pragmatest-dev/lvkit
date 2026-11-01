"""Variant To Data converts the variant's value to the type wired to ``type``: a
number to any numeric type, coerced the way LabVIEW coerces numbers; any other
value that is not of the data type raises ``LabVIEWError`` code 91. Tiny
handmade diagrams (``native_vi.py``), generated and executed."""

from __future__ import annotations

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.helpers import error_cluster_type
from tests.native_vi import generate, node, scalar

VARIANT_TO_DATA = 8003
VARIANT = scalar("Variant")


def _convert(data_type: LVType):
    """A VI that converts its variant input ``v`` to ``data_type`` (its output
    ``d``), in the LabVIEW 11+ numbering."""
    terminals = [
        (0, "input", error_cluster_type()),
        (3, "output", error_cluster_type()),
        (8, "input", VARIANT),
        (9, "input", data_type),
        (11, "output", data_type),
    ]
    return generate(
        {"v": VARIANT},
        {"d": data_type},
        [node("vd", VARIANT_TO_DATA, terminals)],
        [("fp:v", "vd:8"), ("vd:11", "fp:d")],
    )


def test_a_number_converts_to_an_integer_type() -> None:
    run = _convert(scalar("NumInt32"))
    assert "data = round(v)" in run.source
    assert "from .errors import" not in run.source or "LabVIEWError" in run.source
    assert run.function(2.5).d == 2  # ties to even, as LabVIEW rounds
    assert run.function(3.7).d == 4
    assert run.function(7).d == 7


def test_a_number_converts_to_a_floating_point_type() -> None:
    run = _convert(scalar("NumFloat64"))
    result = run.function(3).d
    assert result == 3.0 and isinstance(result, float)


def _labview_error(run, value: object) -> Exception:
    with pytest.raises(Exception) as caught:
        run.function(value)
    error = caught.value
    assert type(error).__name__ == "LabVIEWError"
    return error


def test_a_string_into_a_numeric_type_raises_91() -> None:
    run = _convert(scalar("NumInt32"))
    error = _labview_error(run, "7")
    assert error.code == 91
    assert error.source == "Variant To Data"
    assert error.message == (
        "The data type of the variant is not compatible with the data type wired "
        "to the type input."
    )
    _labview_error(run, True)  # a Boolean is not a number


def test_a_cluster_mismatch_raises_91() -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="a", type=scalar("NumInt32"))],
    )
    run = _convert(cluster)
    assert _labview_error(run, "not a cluster").code == 91
    assert run.function((1,)).d == (1,)


def test_a_matching_value_passes_and_a_variant_type_takes_anything() -> None:
    run = _convert(scalar("String"))
    assert run.function("x").d == "x"
    assert _labview_error(run, 1).code == 91
    assert _convert(VARIANT).function([1, "a"]).d == [1, "a"]

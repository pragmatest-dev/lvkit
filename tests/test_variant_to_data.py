"""Variant To Data converts the variant's value to the type wired to ``type``: a
number to any numeric type, coerced the way LabVIEW coerces numbers; any other
value that is not of the data type is NI's general error 91, the node's error
out, with ``data`` at its type's default (``native_failure``). With the error
out unwired the failure stays the Python ``TypeError``. Tiny handmade diagrams
(``native_vi.py``), generated and executed."""

from __future__ import annotations

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.helpers import error_cluster_type
from tests.native_vi import generate, node, scalar

VARIANT_TO_DATA = 8003
VARIANT = scalar("Variant")
INCOMPATIBLE = (
    "The data type of the variant is not compatible with the data type wired "
    "to the type input."
)
I32 = scalar("NumInt32")


def _convert(data_type: LVType, *, error_out: bool = True):
    """A VI that converts its variant input ``v`` to ``data_type`` (its output
    ``d``), in the LabVIEW 11+ numbering; with ``error_out`` the node's error out
    is the VI's ``error_out``."""
    terminals = [
        (0, "input", error_cluster_type()),
        (3, "output", error_cluster_type()),
        (8, "input", VARIANT),
        (9, "input", data_type),
        (11, "output", data_type),
    ]
    outputs = {"d": data_type}
    wires = [("fp:v", "vd:8"), ("vd:11", "fp:d")]
    if error_out:
        outputs["error_out"] = error_cluster_type()
        wires.append(("vd:3", "fp:error_out"))
    return generate(
        {"v": VARIANT},
        outputs,
        [node("vd", VARIANT_TO_DATA, terminals)],
        wires,
    )


def test_a_number_converts_to_an_integer_type() -> None:
    run = _convert(scalar("NumInt32"))
    assert "data = round(v)" in run.source
    assert run.function(2.5).d == 2  # ties to even, as LabVIEW rounds
    assert run.function(3.7).d == 4
    assert (run.function(7).d, run.function(7).error_out) == (7, None)


def test_a_number_converts_to_a_floating_point_type() -> None:
    run = _convert(scalar("NumFloat64"))
    result = run.function(3).d
    assert result == 3.0 and isinstance(result, float)


def _error_91(run, value: object, default: object) -> None:
    result = run.function(value)
    assert result.d == default
    error = result.error_out
    assert (error.code, error.status, error.source) == (91, True, "Variant To Data")
    assert error.message == INCOMPATIBLE


def test_a_string_into_a_numeric_type_is_error_91() -> None:
    run = _convert(scalar("NumInt32"))
    assert "except TypeError as exception:" in run.source
    _error_91(run, "7", 0)
    _error_91(run, True, 0)  # a Boolean is not a number


def test_a_cluster_mismatch_is_error_91() -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="a", type=scalar("NumInt32"))],
    )
    run = _convert(cluster)
    assert run.function("not a cluster").error_out.code == 91
    assert run.function((1,)).d == (1,)


def test_a_matching_value_passes_and_a_variant_type_takes_anything() -> None:
    run = _convert(scalar("String"))
    assert run.function("x").d == "x"
    _error_91(run, 1, "")
    assert _convert(VARIANT).function([1, "a"]).d == [1, "a"]


def test_with_the_error_out_unwired_the_failure_stays_a_type_error() -> None:
    run = _convert(scalar("NumInt32"), error_out=False)
    with pytest.raises(TypeError, match="not compatible"):
        run.function("7")


def test_a_value_computed_by_another_node_reaches_a_type_with_nothing_to_check() -> (
    None
):
    """The variant is a sum (an expression, not a variable) and the data type
    takes any value (a variant, a refnum): the data is that value, never a name
    nothing assigns."""
    terminals = [
        (0, "input", error_cluster_type()),
        (3, "output", error_cluster_type()),
        (8, "input", VARIANT),
        (9, "input", VARIANT),
        (11, "output", VARIANT),
    ]
    add = node("add", 1050, [(0, "output", I32), (1, "input", I32), (2, "input", I32)])
    run = generate(
        {"x": I32, "y": I32},
        {"d": VARIANT},
        [add, node("vd", VARIANT_TO_DATA, terminals)],
        [("fp:x", "add:2"), ("fp:y", "add:1"), ("add:0", "vd:8"), ("vd:11", "fp:d")],
    )
    assert "variant" not in run.source
    assert run.function(3, 4).d == 7

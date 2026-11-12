"""Variant To Data converts the value of a ``Variant`` to the type wired to
``type``, judging it by the LabVIEW type the variant records: a number variant to
any numeric type, coerced the way LabVIEW coerces numbers; any other variant whose
type is not the data type, and an empty variant, is NI's general error 91, the
node's error out, with ``data`` at its type's default (``native_failure``). With
the error out unwired the failure stays the Python ``TypeError``. Tiny handmade
diagrams (``native_vi.py``), generated and executed."""

from __future__ import annotations

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.error_flow import own_error_terminals
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.helpers import error_cluster_type, generated_variants
from tests.native_vi import generate, node, scalar

VARIANT_TO_DATA = 8003
VARIANT = scalar("Variant")
INCOMPATIBLE = (
    "The data type of the variant is not compatible with the data type wired "
    "to the type input."
)
I32 = scalar("NumInt32")


def variant(value: object = None, type_name: str | None = None):
    """The ``Variant`` a caller passes: ``value`` of the type constant
    ``type_name`` of the generated ``variants`` module; empty with no type."""
    variants = generated_variants()
    if type_name is None:
        return variants.Variant()
    return variants.Variant(value, getattr(variants, type_name))


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
    assert "data = round(v.value)" in run.source
    assert run.function(variant(2.5, "DBL")).d == 2  # ties to even, as LabVIEW rounds
    assert run.function(variant(3.7, "DBL")).d == 4
    result = run.function(variant(7, "I32"))
    assert (result.d, result.error_out) == (7, None)
    # Any number representation converts: a 64-bit unsigned integer, an extended float.
    assert run.function(variant(9, "U64")).d == 9
    assert run.function(variant(1.5, "EXT")).d == 2


def test_a_number_converts_to_a_floating_point_type() -> None:
    run = _convert(scalar("NumFloat64"))
    result = run.function(variant(3, "I32")).d
    assert result == 3.0 and isinstance(result, float)
    # JKI failUnlessEqual reads an extended-precision number from a double.
    extended = _convert(scalar("NumFloatExt")).function(variant(1.5, "DBL"))
    assert (extended.d, extended.error_out) == (1.5, None)


def _error_91(run, given: object, default: object) -> None:
    result = run.function(given)
    assert result.d == default
    error = result.error_out
    assert (error.code, error.status, error.source) == (91, True, "Variant To Data")
    assert error.message == INCOMPATIBLE


def test_a_string_into_a_numeric_type_is_error_91() -> None:
    run = _convert(scalar("NumInt32"))
    assert "except TypeError as exception:" in run.source
    _error_91(run, variant("7", "String"), 0)
    _error_91(run, variant(True, "TF"), 0)  # a Boolean is not a number
    _error_91(run, variant(), 0)  # an empty variant has no type


def test_a_cluster_mismatch_is_error_91() -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="a", type=scalar("NumInt32"))],
    )
    run = _convert(cluster)
    assert run.function(variant("not a cluster", "String")).error_out.code == 91
    variants = generated_variants()
    held = variants.Variant((1,), variants.LVType(80, "cluster"))
    assert run.function(held).d == (1,)


def test_a_matching_value_passes_and_a_variant_type_takes_anything() -> None:
    run = _convert(scalar("String"))
    assert run.function(variant("x", "String")).d == "x"
    _error_91(run, variant(1, "I32"), "")
    # A Variant data type takes the variant as it is.
    given = variant([1, "a"], "String")
    assert _convert(VARIANT).function(given).d is given


def test_with_the_error_out_unwired_the_failure_stays_a_type_error() -> None:
    run = _convert(scalar("NumInt32"), error_out=False)
    with pytest.raises(TypeError, match="not compatible"):
        run.function(variant("7", "String"))


def test_a_value_computed_by_another_node_reaches_a_type_with_nothing_to_check() -> (
    None
):
    """The variant is made of a sum (an expression, not a variable) and the data
    type is a variant: the data is that ``Variant``, never a name nothing
    assigns."""
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
    assert run.function(3, 4).d == variant(7, "I32")


def _error_typed():
    """A VI whose Variant To Data takes an error cluster on ``type``: the
    ``data`` output is the error value, the node's error in is ``error_in``."""
    terminals = [
        (0, "input", error_cluster_type()),
        (3, "output", error_cluster_type()),
        (8, "input", VARIANT),
        (9, "input", error_cluster_type()),
        (11, "output", error_cluster_type()),
    ]
    vd = node("vd", VARIANT_TO_DATA, terminals)
    run = generate(
        {"v": VARIANT, "error_in": error_cluster_type()},
        {"vi_error_out": error_cluster_type(), "error_out": error_cluster_type()},
        [vd],
        [
            ("fp:v", "vd:8"),
            ("fp:error_in", "vd:0"),
            ("fp:error_in", "vd:9"),
            ("vd:11", "fp:vi_error_out"),
            ("vd:3", "fp:error_out"),
        ],
    )
    return run, vd


def _error_class(run):
    return run.module.__dict__["LabVIEWError"]


def _error_variant(error: object):
    """A variant holding an error cluster (or no error)."""
    variants = generated_variants()
    return variants.Variant(error, variants.LVType(80, "Error"))


def test_an_error_cluster_type_is_data_and_the_error_value_is_returned() -> None:
    run, _ = _error_typed()
    error_class = _error_class(run)
    given = error_class(code=7, status=True, source="x")
    result = run.function(_error_variant(given), None)
    assert result.vi_error_out is given
    assert result.error_out is None
    result = run.function(_error_variant(None), None)
    assert (result.vi_error_out, result.error_out) == (None, None)


def test_an_incoming_error_skips_the_node_with_an_error_cluster_type() -> None:
    run, _ = _error_typed()
    error_class = _error_class(run)
    incoming = error_class(code=5, status=True, source="y")
    given = _error_variant(error_class(code=7, status=True, source="x"))
    result = run.function(given, incoming)
    assert result.error_out is incoming
    assert result.vi_error_out is None


def test_a_non_error_value_into_an_error_cluster_type_is_error_91() -> None:
    run, _ = _error_typed()
    result = run.function(variant("text", "String"), None)
    error = result.error_out
    assert (error.code, error.status, error.source) == (91, True, "Variant To Data")
    assert result.vi_error_out is None


def test_own_error_terminals_leave_out_the_wire_typed_terminals() -> None:
    _, vd = _error_typed()
    error_in, error_outs = own_error_terminals(vd, CodeGenContext())
    assert error_in is not None and error_in.id == "vd:0"
    assert [t.id for t in error_outs] == ["vd:3"]


def test_an_incoming_error_skips_a_node_with_a_numeric_type() -> None:
    terminals = [
        (0, "input", error_cluster_type()),
        (3, "output", error_cluster_type()),
        (8, "input", VARIANT),
        (9, "input", I32),
        (11, "output", I32),
    ]
    run = generate(
        {"v": VARIANT, "error_in": error_cluster_type()},
        {"d": I32, "error_out": error_cluster_type()},
        [node("vd", VARIANT_TO_DATA, terminals)],
        [
            ("fp:v", "vd:8"),
            ("fp:error_in", "vd:0"),
            ("vd:11", "fp:d"),
            ("vd:3", "fp:error_out"),
        ],
    )
    incoming = run.module.__dict__["LabVIEWError"](code=5, status=True, source="y")
    result = run.function(variant(7, "I32"), incoming)
    assert result.error_out is incoming
    assert result.d == 0


def _check_only(data_type: LVType):
    """Variant To Data with ``data`` unwired: only its error out is read."""
    terminals = [
        (0, "input", error_cluster_type()),
        (3, "output", error_cluster_type()),
        (8, "input", VARIANT),
        (9, "input", data_type),
        (11, "output", data_type),
    ]
    return generate(
        {"v": VARIANT},
        {"error_out": error_cluster_type()},
        [node("vd", VARIANT_TO_DATA, terminals)],
        [("fp:v", "vd:8"), ("vd:3", "fp:error_out")],
    )


def test_with_data_unwired_the_type_check_still_runs() -> None:
    run = _check_only(I32)
    assert run.function(variant(3, "I32")).error_out is None
    error = run.function(variant("text", "String")).error_out
    assert (error.code, error.status, error.source) == (91, True, "Variant To Data")


def test_with_data_unwired_a_cluster_type_check_still_runs() -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="a", type=scalar("NumInt32"))],
    )
    run = _check_only(cluster)
    variants = generated_variants()
    held = variants.Variant((1,), variants.LVType(80, "cluster"))
    assert run.function(held).error_out is None
    assert run.function(variant("x", "String")).error_out.code == 91

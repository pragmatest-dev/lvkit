"""A waveform in generated Python is a ``nitypes`` waveform: Bundle and Unbundle By
Name build and read ``AnalogWaveform`` / ``ComplexWaveform`` values, a waveform
constant builds the same object, and a waveform with no ``nitypes`` form is a gap
with its reason. Each case runs the generated Python."""

from __future__ import annotations

import numpy
import pytest
from nitypes.bintime import DateTime, TimeDelta, TimeValueTuple
from nitypes.waveform import AnalogWaveform, ComplexWaveform, Timing

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType, LVTypeKind, Terminal, VariantData
from lvkit.timestamp_value import (
    EPOCH_1904,
    TIMESTAMP_FLAVOR,
    timestamp_from_parts,
)
from tests.helpers import generated_variants
from tests.native_vi import array, generate, node, scalar

TIMESTAMP = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)
DBL = scalar("NumFloat64")
VARIANT = scalar("LVVariant")

# The stored field index of each component (``data/measure_data.json``).
T0, DT, Y, ATTRIBUTES = 0, 1, 2, 7


def waveform(flavor: str) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="MeasureData",
        measure_flavor=flavor,
    )


def _components(node_id: str, direction: str, fields: dict[int, LVType]):
    return [
        Terminal(
            id=f"{node_id}:{10 + index}",
            index=10 + index,
            direction=direction,
            nmux_role="list",
            nmux_field_index=index,
            lv_type=lv_type,
        )
        for index, lv_type in fields.items()
    ]


def _bundle(wave: LVType, fields: dict[int, LVType], *, existing: bool = False):
    slots = [(0, "output", wave)] + ([(1, "input", wave)] if existing else [])
    bundle = node("bd", None, slots, node_type="nMux")
    for terminal in bundle.terminals:
        terminal.nmux_role = "agg"
    bundle.terminals.extend(_components("bd", "input", fields))
    return bundle


def _unbundle(wave: LVType, fields: dict[int, LVType]):
    unbundle = node("ub", None, [(0, "input", wave)], node_type="nMux")
    unbundle.terminals[0].nmux_role = "agg"
    unbundle.terminals.extend(_components("ub", "output", fields))
    return unbundle


def _round_trip(flavor: str, element: str, samples: list, gaps=None):
    """Build a waveform from controls and unbundle it again."""
    wave = waveform(flavor)
    y_type = array(element)
    wires = [
        ("fp:t0", "bd:10"),
        ("fp:dt", "bd:11"),
        ("fp:y", "bd:12"),
        ("bd:0", "ub:0"),
        ("bd:0", "fp:wf"),
        ("ub:10", "fp:t0_out"),
        ("ub:11", "fp:dt_out"),
        ("ub:12", "fp:y_out"),
    ]
    return generate(
        {"t0": TIMESTAMP, "dt": DBL, "y": y_type},
        {"wf": wave, "t0_out": TIMESTAMP, "dt_out": DBL, "y_out": y_type},
        [
            _bundle(wave, {T0: TIMESTAMP, DT: DBL, Y: y_type}),
            _unbundle(wave, {T0: TIMESTAMP, DT: DBL, Y: y_type}),
        ],
        wires,
        gaps=gaps,
    )


# A LabVIEW timestamp of whole seconds and a fraction of 2^-64 s.
_T0 = timestamp_from_parts(3_000_000_000, 12345678901234567)


@pytest.mark.parametrize(
    ("flavor", "element", "dtype", "samples"),
    [
        ("Int8Waveform", "NumInt8", numpy.int8, [1, -2]),
        ("Int16Waveform", "NumInt16", numpy.int16, [1, -2]),
        ("Int32Waveform", "NumInt32", numpy.int32, [1, -2]),
        ("Int64Waveform", "NumInt64", numpy.int64, [1, -2]),
        ("UInt8Waveform", "NumUInt8", numpy.uint8, [1, 2]),
        ("UInt16Waveform", "NumUInt16", numpy.uint16, [1, 2]),
        ("UInt32Waveform", "NumUInt32", numpy.uint32, [1, 2]),
        ("UInt64Waveform", "NumUInt64", numpy.uint64, [1, 2]),
        ("Float32Waveform", "NumFloat32", numpy.float32, [0.5, -1.5]),
        ("Float64Waveform", "NumFloat64", numpy.float64, [0.1, -1.5]),
        ("Complex64Waveform", "NumComplex64", numpy.complex64, [1 + 2j, 3j]),
        ("Complex128Waveform", "NumComplex128", numpy.complex128, [1 + 2j, 0.1j]),
    ],
)
def test_a_waveform_of_each_flavor_builds_and_unbundles_exactly(
    flavor, element, dtype, samples
) -> None:
    generated = _round_trip(flavor, element, samples)
    result = generated.function(_T0, 0.25, samples)
    expected_class = ComplexWaveform if "Complex" in flavor else AnalogWaveform
    assert type(result.wf) is expected_class
    assert result.wf.dtype == numpy.dtype(dtype)
    assert result.wf.raw_data.tolist() == numpy.array(samples, dtype).tolist()
    assert result.t0_out == _T0
    assert result.dt_out == 0.25
    assert result.y_out == numpy.array(samples, dtype).tolist()
    assert isinstance(result.y_out, list)


def test_the_start_time_is_exact_to_a_64_64_bintime() -> None:
    generated = _round_trip("Float64Waveform", "NumFloat64", [])
    result = generated.function(_T0, 0.5, [])
    start = result.wf.timing.start_time
    assert start.to_tuple() == (3_000_000_000, 12345678901234567)
    assert result.t0_out == _T0
    assert result.wf.timing.sample_interval.total_seconds() == 0.5


def test_a_new_waveform_with_nothing_wired_takes_the_defaults_of_its_types() -> None:
    wave = waveform("Float64Waveform")
    generated = generate(
        {"y": array("NumFloat64")},
        {"wf": wave},
        [_bundle(wave, {Y: array("NumFloat64")})],
        [("fp:y", "bd:12"), ("bd:0", "fp:wf")],
    )
    result = generated.function([]).wf
    assert result.raw_data.tolist() == []
    assert result.timing.start_time.to_tuple() == (0, 0)
    assert result.timing.sample_interval.total_seconds() == 0.0


def test_a_bundle_on_a_waveform_changes_only_the_wired_components() -> None:
    wave = waveform("Int16Waveform")
    y_type = array("NumInt16")
    generated = generate(
        {"wf": wave, "dt": DBL},
        {"out": wave, "t0": TIMESTAMP, "y": y_type},
        [
            _bundle(wave, {DT: DBL}, existing=True),
            _unbundle(wave, {T0: TIMESTAMP, Y: y_type}),
        ],
        [
            ("fp:wf", "bd:1"),
            ("fp:dt", "bd:11"),
            ("bd:0", "fp:out"),
            ("bd:0", "ub:0"),
            ("ub:10", "fp:t0"),
            ("ub:12", "fp:y"),
        ],
    )
    original = AnalogWaveform.from_array_1d(
        [4, 5, 6],
        numpy.int16,
        timing=Timing.create_with_regular_interval(
            TimeDelta(1.0), DateTime.from_tuple(TimeValueTuple(7, 2**63))
        ),
        extended_properties={"NI_ChannelName": "ai0"},
    )
    result = generated.function(original, 0.5)
    assert result.out.timing.sample_interval.total_seconds() == 0.5
    assert result.out.raw_data.tolist() == [4, 5, 6]
    assert result.y == [4, 5, 6]
    assert result.out.timing.start_time.to_tuple() == (7, 2**63)
    assert result.out.channel_name == "ai0"
    assert original.timing.sample_interval.total_seconds() == 1.0
    assert result.t0 == timestamp_from_parts(7, 2**63)


def test_a_waveform_with_no_timing_reads_the_defaults_of_t0_and_dt() -> None:
    wave = waveform("Float64Waveform")
    generated = generate(
        {"wf": wave},
        {"t0": TIMESTAMP, "dt": DBL},
        [_unbundle(wave, {T0: TIMESTAMP, DT: DBL})],
        [("fp:wf", "ub:0"), ("ub:10", "fp:t0"), ("ub:11", "fp:dt")],
    )
    result = generated.function(AnalogWaveform.from_array_1d([1.0], numpy.float64))
    assert result.t0 == EPOCH_1904
    assert result.dt == 0.0


def test_the_attributes_of_a_variant_are_the_extended_properties() -> None:
    wave = waveform("Float64Waveform")
    generated = generate(
        {"attributes": VARIANT},
        {"wf": wave, "attributes_out": VARIANT},
        [
            _bundle(wave, {ATTRIBUTES: VARIANT}),
            _unbundle(wave, {ATTRIBUTES: VARIANT}),
        ],
        [
            ("fp:attributes", "bd:17"),
            ("bd:0", "ub:0"),
            ("bd:0", "fp:wf"),
            ("ub:17", "fp:attributes_out"),
        ],
    )
    variants = generated_variants()
    attributes = variants.Variant(
        None,
        None,
        {
            "NI_ChannelName": variants.Variant("ai0", variants.LV_String),
            "gain": variants.Variant(2.5, variants.LV_DBL),
            "on": variants.Variant(True, variants.LV_TF),
        },
    )
    result = generated.function(attributes)
    assert dict(result.wf.extended_properties) == {
        "NI_ChannelName": "ai0",
        "gain": 2.5,
        "on": True,
    }
    assert result.wf.channel_name == "ai0"
    read = result.attributes_out.attributes
    assert {k: (v.value, v.type) for k, v in read.items()} == {
        "NI_ChannelName": ("ai0", variants.LV_String),
        "gain": (2.5, variants.LV_DBL),
        "on": (True, variants.LV_TF),
    }


def test_an_attribute_that_is_not_a_boolean_number_or_string_raises() -> None:
    wave = waveform("Float64Waveform")
    generated = generate(
        {"attributes": VARIANT},
        {"wf": wave},
        [_bundle(wave, {ATTRIBUTES: VARIANT})],
        [("fp:attributes", "bd:17"), ("bd:0", "fp:wf")],
    )
    variants = generated_variants()
    attributes = variants.Variant(
        None, None, {"samples": variants.Variant([1.0, 2.0], None)}
    )
    with pytest.raises(NotImplementedError, match="'samples' holds list"):
        generated.function(attributes)


def test_an_integer_attribute_read_back_raises_because_its_type_is_unknown() -> None:
    wave = waveform("Float64Waveform")
    generated = generate(
        {"wf": wave},
        {"attributes": VARIANT},
        [_unbundle(wave, {ATTRIBUTES: VARIANT})],
        [("fp:wf", "ub:0"), ("ub:17", "fp:attributes")],
    )
    holder = AnalogWaveform.from_array_1d(
        [1.0], numpy.float64, extended_properties={"count": 3}
    )
    with pytest.raises(NotImplementedError, match="'count' holds 3"):
        generated.function(holder)


@pytest.mark.parametrize(
    ("flavor", "reason"),
    [
        ("FloatExtWaveform", "extended-precision float"),
        ("ComplexExtWaveform", "extended-precision complex"),
        ("DigitalWaveform", "has no transitions"),
    ],
)
def test_a_flavor_with_no_nitypes_form_is_a_gap_with_its_reason(flavor, reason) -> None:
    wave = waveform(flavor)
    gaps: list[Gap] = []
    generated = generate(
        {"dt": DBL},
        {"wf": wave},
        [_bundle(wave, {DT: DBL})],
        [("fp:dt", "bd:11"), ("bd:0", "fp:wf")],
        gaps=gaps,
    )
    (gap,) = gaps
    assert f"A {flavor} is not converted" in gap.reason
    assert reason in gap.reason
    with pytest.raises(NotImplementedError, match=reason):
        generated.function(1.0)


def test_digital_data_is_a_gap() -> None:
    wave = waveform("Digitaldata")
    gaps: list[Gap] = []
    generate(
        {"wf": wave},
        {"t": array("NumUInt32")},
        [_unbundle(wave, {0: array("NumUInt32")})],
        [("fp:wf", "ub:0"), ("ub:10", "fp:t")],
        gaps=gaps,
    )
    (gap,) = gaps
    assert "A Digitaldata is not converted" in gap.reason
    assert "no transitions" in gap.reason


def _constant(flavor: str, y: list[str], attributes=()):
    return {
        "t0": _T0,
        "dt": "0.25",
        "Y": y,
        "error": {"status": "False", "code": "0", "source": "''"},
        "attributes": VariantData(None, None, attributes),
    }


def test_a_waveform_constant_is_a_nitypes_waveform() -> None:
    wave = waveform("Int16Waveform")
    value = _constant("Int16Waveform", ["1", "2", "3"])
    generated = generate(
        {},
        {"wf": wave},
        [],
        [("c1", "fp:wf")],
        constants={"c1": ("", wave, value)},
    )
    result = generated.function().wf
    assert type(result) is AnalogWaveform
    assert result.dtype == numpy.dtype(numpy.int16)
    assert result.raw_data.tolist() == [1, 2, 3]
    assert result.timing.start_time.to_tuple() == (3_000_000_000, 12345678901234567)
    assert result.timing.sample_interval.total_seconds() == 0.25


def test_a_waveform_constant_with_attributes_has_extended_properties() -> None:
    wave = waveform("Float64Waveform")
    string = scalar("String")
    value = _constant(
        "Float64Waveform",
        [],
        attributes=(("NI_ChannelName", VariantData(string, "'ai0'")),),
    )
    generated = generate(
        {}, {"wf": wave}, [], [("c1", "fp:wf")], constants={"c1": ("", wave, value)}
    )
    assert generated.function().wf.channel_name == "ai0"


def test_a_waveform_constant_with_an_array_attribute_is_a_gap() -> None:
    wave = waveform("Float64Waveform")
    value = _constant(
        "Float64Waveform",
        [],
        attributes=(("samples", VariantData(array("NumFloat64"), ["1.0"])),),
    )
    gaps: list[Gap] = []
    generate(
        {},
        {"wf": wave},
        [],
        [("c1", "fp:wf")],
        constants={"c1": ("", wave, value)},
        gaps=gaps,
    )
    (gap,) = gaps
    assert "nitypes keeps only a Boolean, a number or a string" in gap.reason


def test_the_waveform_imports_are_listed_in_requirements(tmp_path) -> None:
    from lvkit.codegen.package_requirements import PackageRequirements

    (tmp_path / "m.py").write_text(
        "import nitypes.waveform\nimport numpy\n", encoding="utf-8"
    )
    requirements = PackageRequirements()
    requirements.add_imported(tmp_path)
    assert requirements.build() == "nitypes\nnumpy\n"


def test_a_parameter_named_waveform_is_still_the_input_after_a_bundle() -> None:
    wave = waveform("Int16Waveform")
    y_type = array("NumInt16")
    generated = generate(
        {"waveform": wave, "dt": DBL},
        {"out": wave, "y": y_type},
        [_bundle(wave, {DT: DBL}, existing=True), _unbundle(wave, {Y: y_type})],
        [
            ("fp:waveform", "bd:1"),
            ("fp:dt", "bd:11"),
            ("bd:0", "fp:out"),
            ("fp:waveform", "ub:0"),
            ("ub:12", "fp:y"),
        ],
    )
    original = AnalogWaveform.from_array_1d([4, 5], numpy.int16)
    result = generated.function(original, 0.5)
    assert result.out is not original
    assert result.out.timing.sample_interval.total_seconds() == 0.5
    assert result.y == [4, 5]

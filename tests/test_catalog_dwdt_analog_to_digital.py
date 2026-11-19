"""DWDT Analog to Digital: the caller diagram is generated from the catalog entry
the recorded path names (the pane is the one the LabVIEW-DAQ callers record) and
its Python is run.

NI, "DWDT Analog to Digital VI": ``resolution`` is the number of bits,
``full-scale range (pk-pk)`` the difference between the maximum and minimum (2
by default), and ``data format`` offset binary ("The largest negative full-scale
value is represented by all zeros, and the largest positive full-scale value is
represented by all ones. Zero-scale is represented by a one (MSB) followed by all
zeros") or 2's complement ("similar to offset binary, but the MSB is inverted").
NI's example: "The 8 bits can represent a peak-to-peak value of 10", so a step is
the full-scale range over 2^resolution. NI leaves open (searched forums.ni.com,
NI's docs and the SignalExpress Convert Analog to Digital page, none says): the
rounding of a value between two steps (round to nearest, ties to even, as
LabVIEW's own integer conversion does), a value outside the range (clipped to the
all zeros / all ones code) and the unsigned binary mapping (a run-time
``NotImplementedError``). The columns of the digital waveform are the bits, most
significant first, as nitypes holds them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy
import pytest
from nitypes.bintime import TimeDelta
from nitypes.waveform import AnalogWaveform, DigitalWaveform, Timing

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.native_vi import scalar
from tests.vilib_caller import BOOL, DBL, I32, STRING, run_catalog_call

ANALOG = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor="Float64Waveform",
)
DIGITAL = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor="DigitalWaveform",
)
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=I32),
        ClusterField(name="source", type=STRING),
    ],
)
FORMAT = scalar("UnitUInt16")
RECORDED = ["<vilib>", "Waveform", "DWDTOps.llb", "DWDT Analog to Digital.vi"]
PANE = [
    (0, "output", ERROR),
    (1, "output", DBL),
    (2, "output", I32),
    (3, "output", DIGITAL),
    (6, "input", FORMAT),
    (7, "input", BOOL),
    (8, "input", ERROR),
    (9, "input", DBL),
    (10, "input", I32),
    (11, "input", ANALOG),
]


def _convert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    samples: list[float],
    resolution: int | None = 3,
    full_scale: float | None = 8.0,
    data_format: int | None = None,
) -> Any:
    inputs: dict[str, Any] = {"wave": (11, ANALOG)}
    timing = Timing.create_with_regular_interval(TimeDelta(0.25))
    arguments: dict[str, Any] = {
        "wave": AnalogWaveform.from_array_1d(samples, numpy.float64, timing=timing)
    }
    for name, index, lv_type, value in (
        ("bits", 10, I32, resolution),
        ("scale", 9, DBL, full_scale),
        ("data_format", 6, FORMAT, data_format),
    ):
        if value is not None:
            inputs[name] = (index, lv_type)
            arguments[name] = value
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        RECORDED,
        PANE,
        inputs,
        {
            "digital": (3, DIGITAL),
            "bits_out": (2, I32),
            "scale_out": (1, DBL),
            "error": (0, ERROR),
        },
        arguments,
        runtime_not_implemented=True,
    )
    return result


def _codes(wave: DigitalWaveform) -> list[int]:
    """Each sample's lines read as one number, the first column the top bit."""
    return [int("".join(str(int(b)) for b in row), 2) for row in wave.data.tolist()]


def test_offset_binary_maps_the_range_to_all_zeros_through_all_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 3 bits over 8 volts: a step is 1 volt, zero volts is 100 (4).
    result = _convert(tmp_path, monkeypatch, [-4.0, -3.0, 0.0, 1.0, 3.0, 4.0])
    assert _codes(result.digital) == [0, 1, 4, 5, 7, 7]
    assert result.digital.data.shape == (6, 3)
    assert result.digital.data.tolist()[2] == [1, 0, 0]
    assert (result.bits_out, result.scale_out) == (3, 8.0)
    assert result.error is None


def test_the_digital_waveform_keeps_the_analog_waveforms_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _convert(tmp_path, monkeypatch, [0.0, 1.0])
    assert result.digital.timing.sample_interval.total_seconds() == 0.25


def test_a_value_between_two_steps_rounds_to_the_nearest_and_clips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _convert(tmp_path, monkeypatch, [0.4, 0.6, 0.5, 1.5, -100.0, 100.0])
    assert _codes(result.digital) == [4, 5, 4, 6, 0, 7]


def test_twos_complement_inverts_the_most_significant_bit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _convert(
        tmp_path, monkeypatch, [-4.0, -1.0, 0.0, 3.0], data_format=2
    )
    assert _codes(result.digital) == [4, 7, 0, 3]


def test_the_defaults_are_16_bits_over_2_volts_in_offset_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _convert(
        tmp_path,
        monkeypatch,
        [-1.0, 0.0, 0.5],
        resolution=None,
        full_scale=None,
    )
    assert (result.bits_out, result.scale_out) == (16, 2.0)
    assert _codes(result.digital) == [0, 32768, 49152]


def test_unsigned_binary_is_refused_at_run_time_naming_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A run-time check of an unusual input names the value it got (a message
    # built at run time), unlike a gap, which is warned about at generation.
    with pytest.raises(NotImplementedError, match=r"data format 0 \(unsigned binary\)"):
        _convert(
            tmp_path,
            monkeypatch,
            [0.0],
            data_format=0,
        )

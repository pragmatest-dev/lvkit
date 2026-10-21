"""The MeasureData table: one data file gives each flavor's component names and its
flattened value layout, validated against pylabview's flavor and type codes."""

from __future__ import annotations

import json

import pylabview.LVdatatype as lv_datatype
import pytest

from lvkit import measure_data
from lvkit.measure_data import (
    measure_data_field_name,
    measure_data_value_type,
)
from lvkit.models import LVType, LVTypeKind
from lvkit.parser.vi import _decode_element

_EMPTY_VARIANT = bytes.fromhex("0900800000000001000400000001000000000000")


def _u32(*values: int) -> bytes:
    return b"".join(v.to_bytes(4, "big") for v in values)


def _measure(flavor: str) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="MeasureData",
        measure_flavor=flavor,
    )


def _waveform_bytes(sample: bytes, count: int) -> bytes:
    return (
        bytes(16)  # t0
        + bytes(8)  # dt
        + _u32(count)
        + sample * count
        + bytes([0])  # error status
        + _u32(0)  # error code
        + _u32(0)  # error source
        + _EMPTY_VARIANT
    )


def test_every_flavor_in_the_data_file_is_a_pylabview_flavor():
    raw = json.loads(
        (measure_data._bundled_data_dir() / "measure_data.json").read_text("utf-8")
    )
    names = {m.name for m in lv_datatype.MEASURE_DATA_FLAVOR}
    assert set(raw["flavors"]) <= names


@pytest.mark.parametrize(
    ("flavor", "sample"),
    [
        ("Int8Waveform", b"\x01"),
        ("Int16Waveform", b"\x00\x01"),
        ("UInt32Waveform", b"\x00\x00\x00\x01"),
        ("Float32Waveform", bytes(4)),
        ("Float64Waveform", bytes(8)),
        ("Complex64Waveform", bytes(8)),
        ("Complex128Waveform", bytes(16)),
        ("ComplexExtWaveform", bytes(32)),
    ],
)
def test_an_analog_waveform_value_is_read_to_its_end(flavor, sample):
    data = _waveform_bytes(sample, 3)
    value, consumed, structured = _decode_element(data, _measure(flavor))
    assert consumed == len(data)
    assert list(structured) == ["t0", "dt", "Y", "error", "attributes"]
    assert len(structured["Y"]) == 3


def test_a_waveform_and_its_component_names_come_from_the_same_entry():
    assert measure_data_field_name("Float64Waveform", 2) == "Y"
    assert measure_data_value_type("Float64Waveform") is not None


def test_old_float64_waveform_has_names_but_no_known_value_layout():
    assert measure_data_field_name("OldFloat64Waveform", 0) == "t0"
    assert measure_data_value_type("OldFloat64Waveform") is None
    assert _decode_element(bytes(64), _measure("OldFloat64Waveform")) == (
        None,
        0,
        None,
    )


def test_digital_data_value_reads_its_transitions_and_table():
    data = _u32(2, 5, 9) + _u32(1, 2) + bytes([3, 4])
    value, consumed, structured = _decode_element(data, _measure("Digitaldata"))
    assert consumed == len(data)
    assert structured["transitions"] == ["5", "9"]
    assert structured["data"] == [["3", "4"]]


def test_dynamic_data_is_an_array_of_float64_waveforms():
    data = _u32(1) + _waveform_bytes(bytes(8), 2)
    _, consumed, structured = _decode_element(data, _measure("Dynamicdata"))
    assert consumed == len(data)
    assert len(structured["waveforms"]) == 1


def test_an_invalid_data_file_raises(tmp_path, monkeypatch):
    bad = {
        "layouts": {},
        "value_layouts": {},
        "flavors": {"Int8Waveform": {"value": {"layout": "missing"}}},
    }
    (tmp_path / "measure_data.json").write_text(json.dumps(bad))
    monkeypatch.setattr(measure_data, "_bundled_data_dir", lambda: tmp_path)
    monkeypatch.setattr(measure_data, "_TABLE", None)
    with pytest.raises(ValueError, match="undefined layout"):
        measure_data.measure_data_value_type("Int8Waveform")

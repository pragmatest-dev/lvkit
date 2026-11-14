"""DWDT Digital to Boolean Array and DWDT Boolean Array to Digital: each caller
diagram is generated from the catalog entry the recorded path names (the pane is
the one the LabVIEW-DAQ callers record) and its Python is run. A 2D Boolean array
has a row per sample and a column per line, the way the callers index it (an
Index Array with the row index wired gives the lines of one sample); the order of
the lines in a row is NI's ``mode`` (0 LSB first, the default, 1 MSB first). The
state values are NI's table of digital data states: 0 is 0, 1 is 1, 2 is Z, 3 is
L, 4 is H, 5 is X, 6 is T, 7 is V."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy
import pytest
from nitypes.waveform import DigitalWaveform

from lvkit.codegen.gaps import Gap
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.native_vi import array, scalar
from tests.vilib_caller import BOOL, DBL, I32, STRING, run_catalog_call

DIGITAL_WAVEFORM = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor="DigitalWaveform",
)
# LabVIEW's error cluster: exactly status, code and source.
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=I32),
        ClusterField(name="source", type=STRING),
    ],
)
BOOLEAN_ARRAY = array("Boolean", 2)
MODE = scalar("UnitUInt16")

TO_BOOLEAN = ["<vilib>", "Waveform", "DWDTOps.llb", "DWDT Digital to Boolean Array.vi"]
TO_DIGITAL = ["<vilib>", "Waveform", "DWDTOps.llb", "DWDT Boolean Array to Digital.vi"]

TO_BOOLEAN_PANE = [
    (0, "output", ERROR),
    (3, "output", BOOLEAN_ARRAY),
    (8, "input", ERROR),
    (9, "input", MODE),
    (10, "input", MODE),
    (11, "input", DIGITAL_WAVEFORM),
]
TO_DIGITAL_PANE = [
    (0, "output", ERROR),
    (3, "output", DIGITAL_WAVEFORM),
    (5, "input", BOOL),
    (8, "input", ERROR),
    (9, "input", MODE),
    (10, "input", DBL),
    (11, "input", BOOLEAN_ARRAY),
]


def _waveform(rows: list[list[int]]) -> DigitalWaveform:
    """A digital waveform of the sample rows ``rows``, each with the most
    significant line first, as LabVIEW's digital data holds them."""
    lines = numpy.array(rows, dtype=numpy.uint8).reshape(len(rows), len(rows[0]))
    return DigitalWaveform.from_lines(lines, numpy.uint8)


def _to_boolean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[list[int]],
    mode: int | None = None,
    response: int | None = None,
) -> Any:
    inputs: dict[str, Any] = {"wave": (11, DIGITAL_WAVEFORM)}
    arguments: dict[str, Any] = {"wave": _waveform(rows)}
    if mode is not None:
        inputs["mode"] = (9, MODE)
        arguments["mode"] = mode
    if response is not None:
        inputs["response"] = (10, MODE)
        arguments["response"] = response
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        TO_BOOLEAN,
        TO_BOOLEAN_PANE,
        inputs,
        {"flags": (3, BOOLEAN_ARRAY), "error": (0, ERROR)},
        arguments,
    )
    return result


def _to_digital(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[list[bool]],
    rate: float,
    mode: int | None = None,
) -> DigitalWaveform:
    inputs: dict[str, Any] = {"flags": (11, BOOLEAN_ARRAY), "rate": (10, DBL)}
    arguments: dict[str, Any] = {"flags": flags, "rate": rate}
    if mode is not None:
        inputs["mode"] = (9, MODE)
        arguments["mode"] = mode
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        TO_DIGITAL,
        TO_DIGITAL_PANE,
        inputs,
        {"wave": (3, DIGITAL_WAVEFORM)},
        arguments,
    )
    return result.wave


# Two samples of three lines: the row is line 2, line 1, line 0.
ROWS = [[1, 0, 0], [0, 1, 1]]


def test_digital_to_boolean_is_a_row_per_sample_lsb_first_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _to_boolean(tmp_path, monkeypatch, ROWS)
    assert result.flags == [[False, False, True], [True, True, False]]
    assert result.error is None


def test_digital_to_boolean_msb_first_keeps_the_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _to_boolean(tmp_path, monkeypatch, ROWS, mode=1)
    assert result.flags == [[True, False, False], [False, True, True]]


def test_digital_to_boolean_reads_h_as_true_and_l_as_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _to_boolean(tmp_path, monkeypatch, [[4, 3, 1, 0]], mode=1)
    assert result.flags == [[True, False, True, False]]


def test_digital_to_boolean_warn_converts_other_states_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _to_boolean(tmp_path, monkeypatch, [[1, 2, 5, 6, 7]], mode=1, response=0)
    assert result.flags == [[True, False, False, False, False]]


def test_digital_to_boolean_fail_returns_empty_and_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _to_boolean(tmp_path, monkeypatch, [[1, 2]], response=1)
    assert result.flags == []
    assert result.error is not None
    assert result.error.status is True
    assert result.error.code == 42
    assert result.error.source == "DWDT Digital to Boolean Array"


def test_digital_to_boolean_fail_accepts_the_four_boolean_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _to_boolean(tmp_path, monkeypatch, [[0, 1, 3, 4]], mode=1, response=1)
    assert result.flags == [[False, True, False, True]]
    assert result.error is None


def test_boolean_array_to_digital_lsb_first_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wave = _to_digital(
        tmp_path, monkeypatch, [[True, False, False], [False, True, True]], 1000.0
    )
    assert wave.data.tolist() == [[0, 0, 1], [1, 1, 0]]
    assert wave.timing.sample_interval.total_seconds() == pytest.approx(0.001)


def test_boolean_array_to_digital_msb_first_keeps_the_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wave = _to_digital(
        tmp_path, monkeypatch, [[True, False, False], [False, True, True]], 10.0, 1
    )
    assert wave.data.tolist() == [[1, 0, 0], [0, 1, 1]]


def test_the_two_conversions_round_trip_in_both_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for mode in (0, 1):
        flags = [[True, True, False, False], [False, True, False, True]]
        wave = _to_digital(tmp_path, monkeypatch, flags, 100.0, mode)
        back = _to_boolean(tmp_path, monkeypatch, wave.data.tolist(), mode=mode)
        assert back.flags == flags


def test_an_empty_boolean_array_is_a_waveform_with_no_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wave = _to_digital(tmp_path, monkeypatch, [], 100.0)
    assert wave.data.shape == (0, 0)


def test_boolean_array_to_digital_with_no_sample_rate_wired_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NI documents no default for sample rate, so a call that leaves it unwired
    (as the LabVIEW-DAQ callers do) is a gap rather than a guessed rate."""
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            TO_DIGITAL,
            TO_DIGITAL_PANE,
            {"flags": (11, BOOLEAN_ARRAY)},
            {"wave": (3, DIGITAL_WAVEFORM)},
            {"flags": [[True]]},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert [gap.kind for gap in gaps] == ["unknown_default"]

"""A timestamp is a UTC ``hightime.datetime``, exact to LabVIEW's 2**-64 second:
decoded from the VI, written into generated code as constants and defaults, made
by the functions that make a time, and combined by Add and Subtract."""

from __future__ import annotations

import datetime
from typing import Any

import hightime
import pytest

from lvkit.codegen.ast_utils import (
    parameter_default_expr_of_type,
    parse_expr,
    type_default_expr,
)
from lvkit.models import LVType, LVTypeKind, plain_structured
from lvkit.parser.vi import _decode_element
from lvkit.timestamp_value import (
    EPOCH_1904,
    TIMESTAMP_FLAVOR,
    timestamp_parts,
)
from tests.error_diagrams import (
    I32,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)
from tests.prim_vi import Pin, generate, scalar

TIMESTAMP = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)
DBL = scalar("NumFloat64")
ERR = scalar("ErrorCluster")

ADD = 1050
SUBTRACT = 1051
TO_DOUBLE = 1147
TO_TIME_STAMP = 5560
GET_DATE_TIME = 1303
FORMAT_DATE_TIME = 8100
FILE_INFO = 8082


def _flattened(seconds: int, fraction: int) -> bytes:
    return seconds.to_bytes(8, "big", signed=True) + fraction.to_bytes(8, "big")


def _later(seconds: float) -> hightime.datetime:
    return EPOCH_1904 + hightime.timedelta(seconds=seconds)


# -- parser ---------------------------------------------------------------


def test_a_decoded_timestamp_is_the_exact_utc_datetime() -> None:
    text, consumed, value = _decode_element(_flattened(3_800_000_000, 2**63), TIMESTAMP)
    assert value == EPOCH_1904 + hightime.timedelta(seconds=3_800_000_000) + (
        hightime.timedelta(seconds=0.5)
    )
    assert isinstance(value, hightime.datetime)
    assert value.tzinfo is datetime.UTC
    assert (text, consumed) == ("Timestamp(3800000000.500000)", 16)


def test_the_smallest_fraction_survives_a_round_trip() -> None:
    _, _, value = _decode_element(_flattened(0, 1), TIMESTAMP)
    assert isinstance(value, hightime.datetime)
    assert value.yoctosecond == 54210
    assert timestamp_parts(value) == (0, 1)


def test_the_largest_fraction_and_a_date_before_the_epoch_round_trip() -> None:
    _, _, value = _decode_element(_flattened(-5, 2**64 - 1), TIMESTAMP)
    assert isinstance(value, hightime.datetime)
    assert timestamp_parts(value) == (-5, 2**64 - 1)


def test_a_front_panel_default_still_shows_its_text() -> None:
    _, _, value = _decode_element(_flattened(3_800_000_000, 2**63), TIMESTAMP)
    assert plain_structured({"t": value}) == {"t": "Timestamp(3800000000.500000)"}


# -- defaults ---------------------------------------------------------------


def test_the_default_of_a_timestamp_is_the_1904_epoch() -> None:
    import ast

    for expression in (
        type_default_expr(TIMESTAMP),
        parameter_default_expr_of_type(TIMESTAMP),
    ):
        source = ast.unparse(expression)
        assert source == "hightime.datetime(1904, 1, 1, tzinfo=UTC)"
        assert eval(source, {"hightime": hightime, "UTC": datetime.UTC}) == (  # noqa: S307
            EPOCH_1904
        )
    assert parse_expr("None")


# -- functions -------------------------------------------------------------


def _binary(prim_id: int, name: str, x: LVType, y: LVType, result: LVType):
    return generate(
        prim_id,
        name,
        [
            Pin(2, "input", x, source="x"),
            Pin(1, "input", y, source="y"),
            Pin(0, "output", result, sink="out"),
        ],
        {"x": x, "y": y},
        {"out": result},
    )


def test_a_timestamp_minus_a_timestamp_is_seconds() -> None:
    source, fn = _binary(SUBTRACT, "Subtract", TIMESTAMP, TIMESTAMP, DBL)
    assert "in_" not in source
    assert "(x - y).total_seconds()" in source
    assert fn(_later(10.5), _later(4)).out == 6.5


def test_a_timestamp_minus_a_number_is_an_earlier_timestamp() -> None:
    source, fn = _binary(SUBTRACT, "Subtract", TIMESTAMP, DBL, TIMESTAMP)
    assert "x - hightime.timedelta(seconds=y)" in source
    assert fn(_later(10), 2.5).out == _later(7.5)


def test_a_timestamp_plus_a_number_is_a_later_timestamp() -> None:
    source, fn = _binary(ADD, "Add", TIMESTAMP, DBL, TIMESTAMP)
    assert "x + hightime.timedelta(seconds=y)" in source
    assert fn(_later(10), 2.5).out == _later(12.5)


def test_a_number_plus_a_timestamp_is_a_later_timestamp() -> None:
    source, fn = _binary(ADD, "Add", DBL, TIMESTAMP, TIMESTAMP)
    assert "y + hightime.timedelta(seconds=x)" in source
    assert fn(2.5, _later(10)).out == _later(12.5)


def test_numbers_add_and_subtract_as_before() -> None:
    source, fn = _binary(ADD, "Add", DBL, DBL, DBL)
    assert "out=y + x" in source
    assert "hightime" not in source
    assert fn(1.5, 2).out == 3.5
    source, fn = _binary(SUBTRACT, "Subtract", DBL, DBL, DBL)
    assert "out=x - y" in source
    assert "hightime" not in source
    assert fn(5, 2).out == 3


def test_a_timestamp_as_a_double_is_the_seconds_since_1904() -> None:
    _, fn = generate(
        TO_DOUBLE,
        "To Double",
        [
            Pin(1, "input", TIMESTAMP, source="x"),
            Pin(0, "output", DBL, sink="out"),
        ],
        {"x": TIMESTAMP},
        {"out": DBL},
    )
    assert fn(EPOCH_1904).out == 0.0
    assert fn(_later(86400.5)).out == 86400.5


def test_a_number_to_time_stamp_is_that_many_seconds_after_1904() -> None:
    _, fn = generate(
        TO_TIME_STAMP,
        "To Time Stamp",
        [
            Pin(1, "input", DBL, source="x"),
            Pin(0, "output", TIMESTAMP, sink="out"),
        ],
        {"x": DBL},
        {"out": TIMESTAMP},
    )
    assert fn(0).out == EPOCH_1904
    assert fn(2.5).out == _later(2.5)


def test_get_date_time_in_seconds_is_now_in_utc() -> None:
    _, fn = generate(
        GET_DATE_TIME,
        "Get Date/Time In Seconds",
        [Pin(0, "output", TIMESTAMP, sink="out")],
        {},
        {"out": TIMESTAMP},
    )
    before = datetime.datetime.now(datetime.UTC)
    value = fn().out
    assert isinstance(value, hightime.datetime)
    assert value.tzinfo is datetime.UTC
    assert before <= value <= datetime.datetime.now(datetime.UTC)


def test_format_date_time_string_runs_on_the_timestamp() -> None:
    string = scalar("String")
    _, fn = generate(
        FORMAT_DATE_TIME,
        "Format Date/Time String",
        [
            Pin(2, "input", TIMESTAMP, source="when"),
            Pin(3, "input", string, source="format"),
            Pin(0, "output", string, sink="out"),
        ],
        {"when": TIMESTAMP, "format": string},
        {"out": string},
    )
    assert fn(_later(86400), "%Y-%m-%d %H:%M:%S").out == "1904-01-02 00:00:00"


def test_the_last_modified_time_of_a_file_is_a_utc_timestamp(tmp_path: Any) -> None:
    path_type = scalar("Path")
    file = tmp_path / "f.txt"
    file.write_text("x")
    _, fn = generate(
        FILE_INFO,
        "File Info",
        [
            Pin(8, "input", ERR, source=None),
            Pin(11, "input", path_type, source="path"),
            Pin(0, "output", ERR, sink=None),
            Pin(1, "output", TIMESTAMP, sink="out"),
        ],
        {"path": path_type},
        {"out": TIMESTAMP},
    )
    value = fn(str(file)).out
    assert isinstance(value, hightime.datetime)
    assert value.tzinfo is datetime.UTC
    assert value.timestamp() == pytest.approx(file.stat().st_mtime, abs=1e-5)


# -- the generated package -------------------------------------------------


def test_a_package_that_holds_a_timestamp_lists_hightime(
    project: Project,  # noqa: F811
) -> None:
    name = "Stamp.vi"
    vi = Vi(name)
    stamp = vi.add_type('<TypeDesc Type="MeasureData" Flavor="TimeStamp" />')
    when = vi.control("when", stamp)
    out = vi.indicator("out", stamp)
    vi.wire(when, out)
    project.add(vi)
    package = project.generate(name)
    assert (package.root / "requirements.txt").read_text() == "hightime\n"
    assert package.function(_later(3)).out == _later(3)


def test_a_package_without_a_timestamp_does_not_list_hightime(
    project: Project,  # noqa: F811
) -> None:
    name = "Plain.vi"
    vi = Vi(name)
    x = vi.control("x", I32)
    out = vi.indicator("out", I32)
    vi.wire(x, out)
    project.add(vi)
    package = project.generate(name)
    assert not (package.root / "requirements.txt").exists()

"""Format Date/Time String's ``UTC format``: TRUE prints Universal Time, FALSE (the
default, also when unwired) prints the computer's time zone (NI's page: "UTC format
specifies if the output string is in Universal Time or in the configured time zone
for the computer. If TRUE, date/time string is in Universal Time. The default is
FALSE."). Local time from 1970 on is the operating system's; before 1970 it is the
computer's current offset, the rule LV70DateRecToTimeStamp uses. The generated
Python is run."""

from __future__ import annotations

import datetime
import sys
import time

import hightime
import pytest

from lvkit.models import LVType, LVTypeKind
from lvkit.timestamp_value import TIMESTAMP_FLAVOR
from tests.prim_vi import Pin, generate, scalar

TIMESTAMP = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)
STRING = scalar("String")
BOOL = scalar("Boolean")
FORMAT = "%Y-%m-%d %H:%M:%S"

needs_posix_tz = pytest.mark.skipif(
    sys.platform == "win32", reason="time.tzset is POSIX only"
)


def _stamp(*parts: int) -> hightime.datetime:
    return hightime.datetime(*parts, tzinfo=datetime.UTC)


def _format(utc: str | bool | None):
    """The generated function: ``utc`` is None (unwired), a bool (constant) or
    "wired" (a control)."""
    pins = [
        Pin(2, "input", TIMESTAMP, source="when"),
        Pin(3, "input", STRING, source="format"),
        Pin(0, "output", STRING, sink="out"),
    ]
    inputs = {"when": TIMESTAMP, "format": STRING}
    if utc == "wired":
        pins.append(Pin(1, "input", BOOL, source="utc"))
        inputs["utc"] = BOOL
    elif utc is None:
        pins.append(Pin(1, "input", BOOL))
    else:
        pins.append(Pin(1, "input", BOOL, constant=str(utc)))
    return generate(8100, "Format Date/Time String", pins, inputs, {"out": STRING})[1]


@pytest.fixture
def time_zone(monkeypatch: pytest.MonkeyPatch):
    def use(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield use
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


@needs_posix_tz
def test_utc_format_true_prints_universal_time(time_zone) -> None:
    time_zone("EST5EDT,M3.2.0,M11.1.0")
    assert _format(True)(_stamp(2020, 1, 15, 12, 0, 0), FORMAT).out == (
        "2020-01-15 12:00:00"
    )


@needs_posix_tz
def test_utc_format_false_prints_the_computers_time_zone(time_zone) -> None:
    time_zone("EST5EDT,M3.2.0,M11.1.0")
    assert _format(False)(_stamp(2020, 1, 15, 12, 0, 0), FORMAT).out == (
        "2020-01-15 07:00:00"
    )
    # daylight saving time of that date
    assert _format(False)(_stamp(2020, 7, 15, 12, 0, 0), FORMAT).out == (
        "2020-07-15 08:00:00"
    )


@needs_posix_tz
def test_an_unwired_utc_format_is_false(time_zone) -> None:
    time_zone("EST5EDT,M3.2.0,M11.1.0")
    assert _format(None)(_stamp(2020, 1, 15, 12, 0, 0), FORMAT).out == (
        "2020-01-15 07:00:00"
    )


@needs_posix_tz
def test_a_wired_utc_format_chooses_at_run_time(time_zone) -> None:
    time_zone("EST5EDT,M3.2.0,M11.1.0")
    function = _format("wired")
    when = _stamp(2020, 1, 15, 12, 0, 0)
    assert function(when, FORMAT, True).out == "2020-01-15 12:00:00"
    assert function(when, FORMAT, False).out == "2020-01-15 07:00:00"


@needs_posix_tz
def test_before_1970_local_time_uses_the_current_offset(time_zone) -> None:
    time_zone("EST5EDT,M3.2.0,M11.1.0")
    offset = datetime.timedelta(seconds=time.localtime().tm_gmtoff)
    expected = (datetime.datetime(1904, 7, 1, 12) + offset).strftime(FORMAT)
    assert _format(False)(_stamp(1904, 7, 1, 12, 0, 0), FORMAT).out == expected


def test_before_1970_runs_on_every_os() -> None:
    offset = datetime.timedelta(seconds=time.localtime().tm_gmtoff)
    expected = (datetime.datetime(1904, 1, 1) + offset).strftime(FORMAT)
    assert _format(False)(_stamp(1904, 1, 1, 0, 0, 0), FORMAT).out == expected
    assert _format(True)(_stamp(1904, 1, 1, 0, 0, 0), FORMAT).out == (
        "1904-01-01 00:00:00"
    )

"""Format Date/Time String's time format codes, written as NI's "Format Codes for the
Time Format String" gives them (not as Python's ``strftime`` reads them): the
generated Python is run. NI's examples: ``%y%m%d`` writes January 30, 2014 as
140130; ``%<digit>u`` is fractional seconds that "accounts for the decimal point"
(forum: ``%H:%M:%S%3u`` writes 23:24:00.343); the ``#`` modifier removes leading
zeros; a code the function does not recognise is returned literally."""

from __future__ import annotations

import datetime

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


def _write():
    """The generated function of a UTC Format Date/Time String over a time stamp
    and a format string."""
    pins = [
        Pin(2, "input", TIMESTAMP, source="when"),
        Pin(3, "input", STRING, source="format"),
        Pin(1, "input", scalar("Boolean"), constant="True"),
        Pin(0, "output", STRING, sink="out"),
    ]
    inputs = {"when": TIMESTAMP, "format": STRING}
    return generate(8100, "Format Date/Time String", pins, inputs, {"out": STRING})[1]


def _stamp(*parts: int, **fractions: int) -> hightime.datetime:
    return hightime.datetime(*parts, tzinfo=datetime.UTC, **fractions)


@pytest.mark.parametrize(
    ("when", "code", "expected"),
    [
        (_stamp(2014, 1, 30, 14, 5, 6), "%y%m%d", "140130"),
        (_stamp(2014, 1, 30, 14, 5, 6), "%Y-%m-%d %H:%M:%S", "2014-01-30 14:05:06"),
        (_stamp(2014, 1, 30, 14, 5, 6), "%I:%M %p", "02:05 PM"),
        (_stamp(2014, 1, 30, 0, 5, 6), "%I", "12"),
        (_stamp(2014, 1, 30, 14, 5, 6), "%j", "030"),
        (_stamp(2014, 1, 30, 14, 5, 6), "%w", "4"),
        (_stamp(2014, 1, 5, 14, 5, 6), "%w", "0"),
        (_stamp(2014, 1, 30, 14, 5, 6), "%U %W", "04 04"),
        (_stamp(2014, 1, 30, 14, 5, 6), "%a %A %b %B", "Thu Thursday Jan January"),
        (_stamp(2014, 1, 3, 4, 5, 6), "%#d %#H %#I %#m %#M %#S", "3 4 4 1 5 6"),
        (_stamp(2014, 1, 3, 4, 5, 6), "%#j %#y %#Y %#w %#U", "3 14 2014 5 0"),
        (_stamp(1904, 1, 1, 0, 0, 0), "%Y %y", "1904 04"),
        # a code that is not a format code is returned as it is written
        (_stamp(2014, 1, 30, 14, 5, 6), "%q %s %% 100%", "%q %s %% 100%"),
        (_stamp(2014, 1, 30, 14, 5, 6), "a-%H-b", "a-14-b"),
    ],
)
def test_codes_write_as_ni_documents_them(when, code, expected) -> None:
    assert _write()(when, code).out == expected


def test_fractional_seconds_write_the_separator_and_truncate() -> None:
    when = _stamp(2014, 1, 30, 23, 24, 0, 343999, femtosecond=123456789)
    function = _write()
    assert function(when, "%H:%M:%S%3u").out == "23:24:00.343"
    assert function(when, "%1u").out == ".3"
    assert function(when, "%6u").out == ".343999"
    assert function(when, "%9u").out == ".343999123"
    assert function(when, "%12u").out == ".343999123456"
    assert function(_stamp(2014, 1, 30, 23, 24, 0), "%3u").out == ".000"


def test_locale_codes_are_the_operating_systems() -> None:
    when = _stamp(2014, 1, 30, 14, 5, 6)
    plain = datetime.datetime(2014, 1, 30, 14, 5, 6)
    function = _write()
    assert function(when, "%x").out == plain.strftime("%x")
    assert function(when, "%X").out == plain.strftime("%X")
    assert function(when, "%c").out == plain.strftime("%c")
    assert function(when, "").out == plain.strftime("%c")
    assert function(when, "%Z").out == "UTC"


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (datetime.timedelta(hours=-7), "-07:00:00"),
        (datetime.timedelta(hours=5, minutes=30), "05:30:00"),
        (datetime.timedelta(0), "00:00:00"),
    ],
)
def test_z_writes_the_stamps_utc_offset_signed_only_when_negative(
    offset: datetime.timedelta, expected: str
) -> None:
    when = hightime.datetime(2015, 9, 4, 8, 17, 16, tzinfo=datetime.timezone(offset))
    assert _write()(when, "%z").out == expected
    assert _write()(when, "%#z").out == expected


@pytest.mark.parametrize("code", ["%.1x", "%.2x", "%#X"])
def test_a_code_with_no_conversion_raises_naming_it(code: str) -> None:
    with pytest.raises(NotImplementedError, match=code):
        _write()(_stamp(2014, 1, 30, 14, 5, 6), "at " + code)

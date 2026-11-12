"""A LabVIEW timestamp as a UTC ``hightime.datetime``, exact to LabVIEW's 2^-64 s.

LabVIEW stores a timestamp as an I64 count of whole seconds since 1904-01-01
00:00:00 UTC plus a U64 count of 2^-64 second fractions (NI, "How LabVIEW Stores
Data in Memory"). ``hightime.datetime`` is NI's datetime with yoctosecond
resolution, the type NI's ``nidaqmx`` returns for a timestamp, converted the same
way here.
"""

from __future__ import annotations

from datetime import UTC

import hightime
import pylabview.LVdatatype as _lv_datatype  # type: ignore[import-untyped]

# The flavor of a timestamp, which is read directly (not as a cluster of parts).
TIMESTAMP_FLAVOR: str = _lv_datatype.MEASURE_DATA_FLAVOR.TimeStamp.name

EPOCH_1904 = hightime.datetime(1904, 1, 1, tzinfo=UTC)

# Python source of ``EPOCH_1904``; the module that holds it imports ``hightime``
# and ``UTC`` (``codegen.ast_utils.implicit_imports``).
EPOCH_1904_EXPRESSION = "hightime.datetime(1904, 1, 1, tzinfo=UTC)"

_YOCTOSECONDS_PER_SECOND = 10**24
_FRACTION_UNITS = 2**64


def timestamp_from_parts(seconds: int, fraction: int) -> hightime.datetime | None:
    """The timestamp of ``seconds`` whole seconds since 1904 UTC plus ``fraction``
    units of 2^-64 s; None when the date is outside what ``datetime`` holds."""
    yoctoseconds = (
        _YOCTOSECONDS_PER_SECOND * fraction + _FRACTION_UNITS // 2
    ) // _FRACTION_UNITS
    try:
        return (
            EPOCH_1904
            + hightime.timedelta(seconds=seconds)
            + hightime.timedelta(yoctoseconds=yoctoseconds)
        )
    except OverflowError:
        return None


def timestamp_parts(value: hightime.datetime) -> tuple[int, int]:
    """The whole seconds since 1904 UTC and the 2^-64 s fraction of ``value``;
    the inverse of ``timestamp_from_parts``."""
    delta = value - EPOCH_1904
    seconds = delta.days * 86400 + delta.seconds
    yoctoseconds = (
        delta.microseconds * 10**18 + delta.femtoseconds * 10**9 + delta.yoctoseconds
    )
    fraction = (yoctoseconds * _FRACTION_UNITS + _YOCTOSECONDS_PER_SECOND // 2) // (
        _YOCTOSECONDS_PER_SECOND
    )
    return seconds, fraction


def timestamp_text(seconds: int, fraction: int) -> str:
    """The text a timestamp shows as: ``Timestamp(<seconds>.<microseconds>)``,
    the fraction rounded to microseconds."""
    micros = round(fraction * 1_000_000 / _FRACTION_UNITS)
    if micros >= 1_000_000:
        seconds += 1
        micros = 0
    return f"Timestamp({seconds}.{micros:06d})"


def timestamp_value_text(value: hightime.datetime) -> str:
    """The display text of ``value`` (see ``timestamp_text``)."""
    return timestamp_text(*timestamp_parts(value))


def timestamp_expression(value: hightime.datetime) -> str:
    """Python source of a ``hightime.datetime`` equal to ``value``."""
    parts = [
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
    ]
    keywords = []
    for name in ("microsecond", "femtosecond", "yoctosecond"):
        if getattr(value, name):
            keywords.append(f"{name}={getattr(value, name)}")
    arguments = ", ".join([str(p) for p in parts] + keywords + ["tzinfo=UTC"])
    return f"hightime.datetime({arguments})"

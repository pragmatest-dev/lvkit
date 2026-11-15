"""LV70DateRecToTimeStamp.vi (``<vilib>/_oldvers/_oldvers.llb``): the caller is
generated from the catalog entry and its Python is run.

The pane is the one the caller Get UTC Offset.vi (JKI EasyXML) saves for the call;
the behaviour is NI's Date/Time To Seconds page (a date and time record, with is
UTC FALSE, is in the computer's time zone; DST 0 is standard time, 1 daylight
saving time, -1 lets the system decide; day of week and day of year are
ignored). The caller subtracts the record of 1 January 1904 from 0 to get the
offset of local time from UTC, which only a local-time reading makes meaningful."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.timestamp_value import TIMESTAMP_FLAVOR
from tests.native_vi import generate, node, scalar
from tests.vilib_caller import I32, run_catalog_call

OLDVERS = ["<vilib>", "_oldvers", "_oldvers.llb", "LV70DateRecToTimeStamp.vi"]
TIMESTAMP = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)
RECORD = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name=name, type=I32)
        for name in (
            "second",
            "minute",
            "hour",
            "day of month",
            "month",
            "year",
            "day of week",
            "day of year",
            "is DST",
        )
    ],
)
EPOCH_1970_IN_1904_SECONDS = 2082844800

needs_posix_tz = pytest.mark.skipif(
    sys.platform == "win32", reason="time.tzset is POSIX only"
)


def _to_time_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: tuple[int, ...]
) -> tuple[str, object]:
    return run_catalog_call(
        tmp_path,
        monkeypatch,
        OLDVERS,
        pane=[(0, "output", TIMESTAMP), (1, "input", RECORD)],
        inputs={"date_time_rec": (1, RECORD)},
        outputs={"time_stamp": (0, TIMESTAMP)},
        arguments={"date_time_rec": record},
    )


def _seconds_since_1904(result: object) -> float:
    stamp = result.time_stamp  # type: ignore[attr-defined]
    return stamp.timestamp() + EPOCH_1970_IN_1904_SECONDS


@pytest.fixture
def time_zone(monkeypatch: pytest.MonkeyPatch):
    def use(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield use
    monkeypatch.undo()
    time.tzset()


@needs_posix_tz
def test_the_record_is_local_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, time_zone
) -> None:
    time_zone("UTC+05:00")
    source, result = _to_time_stamp(
        tmp_path, monkeypatch, (30, 45, 12, 15, 6, 2001, 6, 166, 0)
    )
    utc = time.mktime((2001, 6, 15, 12, 45, 30, 0, 0, 0))
    assert _seconds_since_1904(result) == utc + EPOCH_1970_IN_1904_SECONDS
    # 2001-06-15 12:45:30 five hours behind UTC is 17:45:30 UTC
    assert result.time_stamp.isoformat() == "2001-06-15T17:45:30+00:00"
    assert "lvkit" not in source
    assert os.environ["TZ"] == "UTC+05:00"


@needs_posix_tz
def test_the_dst_field_picks_standard_or_daylight_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, time_zone
) -> None:
    time_zone("EST5EDT,M3.2.0,M11.1.0")
    summer = (0, 0, 12, 15, 7, 2001, 1, 1, 0)
    standard = _to_time_stamp(tmp_path, monkeypatch, summer)[1]
    daylight = _to_time_stamp(tmp_path, monkeypatch, (*summer[:8], 1))[1]
    automatic = _to_time_stamp(tmp_path, monkeypatch, (*summer[:8], -1))[1]
    assert standard.time_stamp.isoformat() == "2001-07-15T17:00:00+00:00"
    assert daylight.time_stamp.isoformat() == "2001-07-15T16:00:00+00:00"
    assert automatic.time_stamp == daylight.time_stamp


@needs_posix_tz
def test_day_of_week_and_day_of_year_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, time_zone
) -> None:
    time_zone("UTC")
    one = _to_time_stamp(tmp_path, monkeypatch, (0, 0, 0, 1, 1, 2000, 1, 1, 0))[1]
    other = _to_time_stamp(tmp_path, monkeypatch, (0, 0, 0, 1, 1, 2000, 7, 300, 0))[1]
    assert one.time_stamp == other.time_stamp


@needs_posix_tz
def test_the_record_of_the_epoch_gives_the_offset_from_utc_and_uses_no_lvkit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, time_zone
) -> None:
    """What Get UTC Offset.vi does: the timestamp of 1904-01-01 00:00:00 is the
    seconds the computer's time zone is behind UTC."""
    time_zone("UTC+05:00")
    source, result = _to_time_stamp(
        tmp_path, monkeypatch, (0, 0, 0, 1, 1, 1904, 6, 1, 0)
    )
    assert _seconds_since_1904(result) == 5 * 3600
    assert "lvkit" not in source


@needs_posix_tz
def test_before_1970_uses_the_current_offset_even_where_dst_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, time_zone
) -> None:
    time_zone("EST5EDT,M3.2.0,M11.1.0")
    result = _to_time_stamp(tmp_path, monkeypatch, (0, 0, 0, 1, 7, 1904, 6, 1, 0))[1]
    # 1904-07-01 is 182 days after 1904-01-01 (a leap year); the offset is today's
    assert _seconds_since_1904(result) == 182 * 86400 - time.localtime().tm_gmtoff


def test_1904_record_runs_on_every_os_and_equals_the_current_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _to_time_stamp(tmp_path, monkeypatch, (0, 0, 0, 1, 1, 1904, 6, 1, 0))[1]
    assert _seconds_since_1904(result) == -time.localtime().tm_gmtoff


def test_2020_record_runs_on_every_os(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = (0, 30, 12, 15, 3, 2020, 1, 75, 0)
    result = _to_time_stamp(tmp_path, monkeypatch, record)[1]
    expected = time.mktime((2020, 3, 15, 12, 30, 0, 0, 0, 0))
    assert _seconds_since_1904(result) == expected + EPOCH_1970_IN_1904_SECONDS


def test_a_neighbour_old_version_vi_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError, match="LV70TimeStampToDateRec"):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*OLDVERS[:-1], "LV70TimeStampToDateRec.vi"],
            pane=[(0, "output", RECORD), (1, "input", TIMESTAMP)],
            inputs={"time_stamp": (1, TIMESTAMP)},
            outputs={"date_time_rec": (0, RECORD)},
            arguments={},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert len(gaps) == 1


def test_a_timestamp_into_an_integer_conversion_is_a_gap() -> None:
    """Get UTC Offset.vi wires the timestamp into To Unsigned Long Integer, which
    NI does not document for a timestamp."""
    gaps: list[Gap] = []
    run = generate(
        {"t": TIMESTAMP},
        {"n": scalar("NumUInt32")},
        [
            node(
                "c",
                1145,
                [
                    (0, "output", scalar("NumUInt32")),
                    (1, "input", TIMESTAMP),
                ],
            )
        ],
        [("fp:t", "c:1"), ("c:0", "fp:n")],
        gaps=gaps,
    )
    assert len(gaps) == 1
    assert "does not document the number a timestamp converts to" in gaps[0].message
    with pytest.raises(NotImplementedError):
        run.function(None)

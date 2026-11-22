"""The package's one local-time setting, ``LABVIEW_DAYLIGHT_SAVING`` in the generated
``local_time.py``. True (the default) is LabVIEW's rule: the computer's current
daylight-saving rule for every date from 1970 (NI forum, "Seconds To Date/Time
always converts to the time with current DST status"; JKI EasyXML's own tests
expect it). False uses each date's own historical rule and, in Date/Time To
Seconds, the record's DST field. The generated Python is run, in US Pacific and
Berlin, on a 1976 date (where the two rules differ) and a 2024 date (where they
agree); the setting is read when the code runs."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

from tests.helpers import GENERATED_PACKAGE, install_generated_package
from tests.test_format_date_time_utc import FORMAT, _format, _stamp
from tests.time_zone import needs_posix_tz

needs_zone_data = pytest.mark.skipif(
    not Path("/usr/share/zoneinfo/America/Los_Angeles").exists(),
    reason="needs the system's time zone database",
)

PACIFIC = "America/Los_Angeles"
BERLIN = "Europe/Berlin"


def _local_time_module():
    install_generated_package()
    return sys.modules[f"{GENERATED_PACKAGE}.local_time"]


def _utc_of(record: tuple[int, ...]) -> str:
    seconds = _local_time_module().local_seconds(record)
    stamp = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    return (stamp + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M")


@pytest.fixture
def daylight_saving(monkeypatch: pytest.MonkeyPatch):
    def use(value: bool) -> None:
        monkeypatch.setattr(_local_time_module(), "LABVIEW_DAYLIGHT_SAVING", value)

    return use


def test_the_setting_is_true_by_default_and_documented() -> None:
    module = _local_time_module()
    assert module.LABVIEW_DAYLIGHT_SAVING is True
    assert "LABVIEW_DAYLIGHT_SAVING" in (module.__doc__ or "")


@needs_posix_tz
@needs_zone_data
@pytest.mark.parametrize(
    ("zone", "when", "current_rule", "own_rule"),
    [
        (PACIFIC, (1976, 3, 17, 20, 2, 3), "13:02:03", "12:02:03"),
        (BERLIN, (1976, 3, 30, 12, 0, 0), "14:00:00", "13:00:00"),
        (PACIFIC, (2024, 3, 17, 20, 2, 3), "13:02:03", "13:02:03"),
        (BERLIN, (2024, 3, 20, 12, 0, 0), "13:00:00", "13:00:00"),
    ],
)
def test_format_date_time_string_follows_the_setting(
    time_zone, daylight_saving, zone, when, current_rule, own_rule
) -> None:
    time_zone(zone)
    function = _format(False)
    day = f"{when[0]}-{when[1]:02}-{when[2]:02} "
    assert function(_stamp(*when), FORMAT).out == day + current_rule
    daylight_saving(False)
    assert function(_stamp(*when), FORMAT).out == day + own_rule


@needs_posix_tz
@needs_zone_data
@pytest.mark.parametrize(
    ("zone", "record", "current_rule", "own_rule"),
    [
        # the record's DST field is 0 (standard time): LabVIEW ignores it
        (PACIFIC, (0, 0, 9, 17, 3, 1976, 0, 0, 0), "16:00", "17:00"),
        (BERLIN, (0, 0, 12, 30, 3, 1976, 0, 0, 0), "10:00", "11:00"),
        (PACIFIC, (0, 0, 9, 17, 3, 2024, 0, 0, -1), "16:00", "16:00"),
        (BERLIN, (0, 0, 12, 20, 3, 2024, 0, 0, -1), "11:00", "11:00"),
    ],
)
def test_date_time_to_seconds_follows_the_setting(
    time_zone, daylight_saving, zone, record, current_rule, own_rule
) -> None:
    time_zone(zone)
    day = f"{record[5]}-{record[4]:02}-{record[3]:02} "
    assert _utc_of(record) == day + current_rule
    daylight_saving(False)
    assert _utc_of(record) == day + own_rule


@needs_posix_tz
@needs_zone_data
def test_the_record_dst_field_is_honoured_only_with_the_setting_false(
    time_zone, daylight_saving
) -> None:
    time_zone(PACIFIC)
    record = (0, 0, 9, 15, 7, 2020, 0, 0)
    assert [_utc_of((*record, dst)) for dst in (0, 1, -1)] == [
        "2020-07-15 16:00"
    ] * 3
    daylight_saving(False)
    assert [_utc_of((*record, dst)) for dst in (0, 1, -1)] == [
        "2020-07-15 17:00",
        "2020-07-15 16:00",
        "2020-07-15 16:00",
    ]


@needs_posix_tz
def test_before_1970_is_the_current_offset_with_either_setting(
    time_zone, daylight_saving
) -> None:
    time_zone("EST5EDT,M3.2.0,M11.1.0")
    record = (0, 0, 12, 1, 7, 1904, 0, 0, 0)
    current = _utc_of(record)
    daylight_saving(False)
    assert _utc_of(record) == current

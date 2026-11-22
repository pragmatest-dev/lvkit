"""The computer's time zone, as LabVIEW applies it to a time stamp.

LabVIEW converts between UTC and local time (Date/Time To Seconds, Format Date/Time
String, Scan From String) with the computer's CURRENT daylight-saving rule for
every date from 1970 on: a 1976 date is converted as if today's rule had held
then. A date before 1970 uses the computer's current offset from UTC.

LABVIEW_DAYLIGHT_SAVING is the one setting. True (the default) is LabVIEW's rule: a
year before 2010 is read 28-year cycles later, where the zone data holds the
current rule (the calendar repeats its weekdays and leap years every 28 years from
1901 to 2099, so a rule written as a weekday of a month repeats too). False uses
each date's own historical rule, the zone data of its year, and Date/Time To
Seconds then honours the record's DST field (0 standard time, 1 daylight saving
time, -1 lets the system decide). The value is read when the code runs.

This file is the package's ``local_time.py``: generated modules import
``local_offset`` and ``local_seconds`` from it.
"""

from __future__ import annotations

import calendar
import datetime
import time

# LabVIEW applies today's daylight-saving rule to every date.
# Set False to use each date's own historical rule.
LABVIEW_DAYLIGHT_SAVING = True

# Seconds in 28 years (10227 days) from 1901 to 2099.
_CYCLE_SECONDS = 883612800


def _cycles(year):
    """How many 28-year cycles a year before 2010 moves forward to reach 2010-2037,
    where the zone data holds the current rule; none with the setting False."""
    if not LABVIEW_DAYLIGHT_SAVING:
        return 0
    return max(0, (2037 - year) // 28)


def local_offset(stamp):
    """The ``datetime.timedelta`` the computer's time zone is ahead of UTC at the
    aware time stamp ``stamp``."""
    if stamp.year < 1970:
        return datetime.timedelta(seconds=time.localtime().tm_gmtoff)
    shifted = stamp.replace(year=stamp.year + 28 * _cycles(stamp.year))
    return shifted.astimezone().utcoffset()


def local_seconds(record):
    """The seconds since 1970-01-01 UTC of a date and time record (seconds,
    minutes, hours, day, month, year, day of week, day of year, DST) read as local
    time. ``time.mktime`` raises OverflowError before 1970 on Windows, so those
    dates use ``calendar.timegm`` minus the computer's current offset."""
    second, minute, hour, day, month, year = record[:6]
    if year < 1970:
        return (
            calendar.timegm((year, month, day, hour, minute, second))
            - time.localtime().tm_gmtoff
        )
    cycles = _cycles(year)
    daylight = -1 if LABVIEW_DAYLIGHT_SAVING else record[8]
    local = (year + 28 * cycles, month, day, hour, minute, second, 0, 0, daylight)
    return time.mktime(local) - cycles * _CYCLE_SECONDS

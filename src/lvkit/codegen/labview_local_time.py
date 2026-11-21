"""The offset of the computer's time zone from UTC at a time stamp, as the Python
expression that generated code runs.

NI, Format Date/Time String and Scan From String: a time that is not UTC is "in
the configured time zone for the computer". From 1970 on LabVIEW uses the
computer's CURRENT time zone rule for every year (the old-version VI
LV70DateRecToTimeStamp, the conversion in the other direction, has the same rule:
see ``data/vilib/timing.json``), so a time stamp of 1976 is shifted by whole
28-year cycles into 2010-2037, where the zone data holds the current rule, and
the offset is read there (the Gregorian calendar repeats its weekdays and leap
years every 28 years from 1901 to 2099, so a rule written as a weekday of a month
repeats too). Before 1970 it is the computer's current offset
(``time.localtime().tm_gmtoff``).
"""

from __future__ import annotations


def local_offset_expression(stamp: str) -> str:
    """The expression of the ``datetime.timedelta`` the computer's time zone is
    ahead of UTC at the time stamp ``stamp`` (a name)."""
    year = f"{stamp}.year"
    return (
        f"({stamp}.replace(year={year} + 28 * max(0, (2037 - {year}) // 28))"
        f".astimezone().utcoffset() if {year} >= 1970 else "
        "datetime.timedelta(seconds=time.localtime().tm_gmtoff))"
    )

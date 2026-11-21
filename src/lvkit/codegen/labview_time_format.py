"""The rules Format Date/Time String follows to write a time stamp as text.

NI, "Format Date/Time String Function" (labview-api-ref,
``functions/format-date-time-string.html``): the function "calculates date/time
string by copying time format string and replacing each of the time format codes
with the corresponding values"; "Time format codes (beginning with %) not
recognized by the function as a format code return the character literally"; an
empty format is the default ``%c``; "Time format codes have leading zeros as
necessary to ensure a constant field width. An optional # modifier before the
format code letter removes the leading zeros from the following format codes: %#d,
%#H, %#I, %#j, %#m, %#M, %#s, %#S, %#U, %#w, %#W, %#X, %#y, %#Y"; "The %c, %u, %x,
%X, %z, and %Z format codes depend on operating system locale support".

The codes are the ones of "Format Codes for the Time Format String"
(``vi-lib/string/format-codes-for-the-time-format-string.html``) and "Time-Specific
Format Codes" (labview, ``format-codes-time-format.html``): ``%a %A %b %B %c %d %H
%I %j %m %M %p %S %<digit>u %U %w %W %x %.1x %.2x %X %y %Y %z %Z``. ``%<digit>u``
is "Fractional seconds with digit precision ... This code accounts for the decimal
point in strings that include fractional seconds. When you use this code, you do
not need to add a literal period to the time format string": the text is the
decimal separator and ``digit`` digits of the fraction. A stamp is truncated, not
rounded, to them (forums.ni.com "Timestamp Formatting Truncates Instead of
Rounds", m-p/3782029); ``%H:%M:%S%3u`` writes 23:24:00.343 (forums.ni.com,
"display-time-in-specific-format"). The names, ``%c``, ``%x``, ``%X``, ``%p`` and
``%Z`` are the operating system's for the locale, as NI says.

``%z`` is, in NI's "Time-Specific Format Codes" table, the "difference between
local time and universal time (HH:MM:SS)"; a negative offset is written with its
sign (forums.ni.com, "Convert string to timestamp", td-p/3187200, page 2:
``2015-09-04T08:17:16.082 -07:00:00``) and a positive one without a ``+``, as the
forum request for an always-shown sign records. The stamp's own offset is written
(a UTC stamp gives ``00:00:00``); ``%#z`` is no different.

Two codes have no conversion and raise where they run, naming the code: the long
date formats ``%.1x`` and ``%.2x`` (the operating system's long date patterns are
not in the standard library) and ``%#X`` (which fields lose their zeros is not
stated).

This is plain standard-library Python that calls nothing else: the code generator
copies its source into a generated module (``nodes/ops/format_date_time_string``),
so the text a constant and a wired format string give is the same.
"""

from __future__ import annotations


def _lv_format_time(stamp, time_format):
    """``stamp`` (a ``datetime``, ``hightime.datetime`` for the fraction past the
    microsecond, already in the time zone to write) written with LabVIEW's time
    format codes."""
    import locale
    import re

    fraction = (
        f"{stamp.microsecond:06d}"
        f"{getattr(stamp, 'femtosecond', 0):09d}"
        f"{getattr(stamp, 'yoctosecond', 0):09d}"
    )
    plain = {
        "d": (stamp.day, 2),
        "H": (stamp.hour, 2),
        "I": (stamp.hour % 12 or 12, 2),
        "j": (stamp.timetuple().tm_yday, 3),
        "m": (stamp.month, 2),
        "M": (stamp.minute, 2),
        "S": (stamp.second, 2),
        "y": (stamp.year % 100, 2),
        "Y": (stamp.year, 4),
        "w": ((stamp.weekday() + 1) % 7, 1),
        "U": (int(stamp.strftime("%U")), 2),
        "W": (int(stamp.strftime("%W")), 2),
    }

    def code(found):
        strip = found.group(1) == "#"
        modifier = found.group(2) or ""
        name = found.group(3)
        if modifier.isdigit() and name == "u":
            digits = int(modifier)
            if digits == 0:
                return found.group(0)
            return locale.localeconv()["decimal_point"] + fraction.ljust(
                digits, "0"
            )[:digits]
        if modifier == "" and name in plain:
            value, width = plain[name]
            return str(value) if strip else str(value).zfill(width)
        if modifier == "" and name in "aAbBcpxXZ":
            if strip and name == "X":
                raise NotImplementedError(
                    "Format Date/Time String code " + repr(found.group(0))
                    + " has no conversion: NI does not say which fields lose "
                    "their leading zeros"
                )
            return stamp.strftime("%" + name)
        if modifier in (".1", ".2") and name == "x":
            raise NotImplementedError(
                "Format Date/Time String code " + repr(found.group(0))
                + " has no conversion: the operating system's long date format "
                "is not in the standard library"
            )
        if modifier == "" and name == "z":
            offset = stamp.utcoffset()
            seconds = int(offset.total_seconds()) if offset is not None else 0
            hours, rest = divmod(abs(seconds), 3600)
            return (
                ("-" if seconds < 0 else "")
                + f"{hours:02d}:{rest // 60:02d}:{rest % 60:02d}"
            )
        return found.group(0)

    return re.sub(r"%(#?)(\.\d+|\d+)?(.)", code, time_format or "%c", flags=re.DOTALL)

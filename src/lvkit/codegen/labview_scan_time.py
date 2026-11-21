"""How Scan From String reads a time stamp: the ``%<...>T`` specifier.

NI (Format Specifier Syntax, "Conversion Codes for Time"): ``T`` is absolute time,
the text between ``<`` and ``>`` is the embedded time format whose codes are those
of "Format Codes for the Time Format String", ``^`` reads the time as Universal
Time and without it the time is the computer's local time. NI's example (Scan From
String page) ``Time: %<%H:%M:%S%2u%m/%d/%Y>T`` with the default 1/1/1904 reads
"Time: 23:15:04.25 5/31/2004": a field the format does not name keeps the value
of the default time stamp, and ``%2u`` takes the decimal separator and the
fraction digits.

Read here: ``%Y`` (exactly four digits, a reply on forums.ni.com
"String-to-timestamp", m-p/1893499 area: "%Y is assumed to be exactly four
digits"), ``%y`` (two digits; NI: 00-68 are 2000-2068, 69-99 are 1969-1999),
``%m`` ``%d`` ``%H`` ``%M`` ``%S`` (one or two digits in the range NI gives), and
``%<digit>u`` (the fraction of a second, up to that many digits, after the decimal
separator when the text has one: NI's example above has the separator and no
literal for it; forum posts such as "%H.%M.%S.%3u" put a literal point before it).
Leading white space is not skipped (forums.ni.com "Scan From String Timestamp
format bug and workaround", td-p/4246261). A space in the embedded format matches
any white space.

Local time follows the rule of Format Date/Time String: from 1970 the operating
system's conversion, before 1970 the computer's current offset from UTC. The
codes whose text is locale-specific or that the scan cannot place on the time
(names of days and months, 12-hour clock, day of the year, week numbers,
locale-specific date and time, the zone) are a gap that names the code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .labview_local_time import local_offset_expression

# Code -> (name of its group, the pattern of its text).
_NUMBERS = {
    "Y": ("Y", "[0-9]{4}"),
    "y": ("y", "[0-9]{2}"),
    "m": ("m", "1[0-2]|0?[1-9]"),
    "d": ("d", "3[01]|[12][0-9]|0?[1-9]"),
    "H": ("H", "2[0-3]|[01]?[0-9]"),
    "M": ("M", "[0-5]?[0-9]"),
    "S": ("S", "[0-5]?[0-9]"),
}
_CODE = re.compile(r"%(?:\.(\d))?(\d*)(.)", re.DOTALL)
_LOCAL_OFFSET = local_offset_expression("x")


@dataclass(frozen=True)
class TimeScan:
    """The scan of one ``%<...>T``: the pattern of its text, and the
    expression of the time stamp in which ``{g}`` stands for the text and
    ``{d}`` for the default time stamp."""

    pattern: str
    value: str
    imports: tuple[str, ...]


def time_scan(spec_text: str, embedded: str, utc: bool, separator: str) -> TimeScan:
    """Take the embedded time format ``embedded`` (with its ``<`` and ``>``)
    apart. ``separator`` is the decimal separator a ``%u`` reads. A code that has
    no conversion is a ``ValueError`` giving the reason."""
    body = embedded[1:-1]
    outer = ""
    inner = ""
    seen: set[str] = set()
    index = 0
    while index < len(body):
        if body[index] != "%":
            char = body[index]
            piece = r"\s*" if char == " " else re.escape(char)
            if not (char == " " and outer.endswith(r"\s*")):
                outer += piece
                inner += piece
            index += 1
            continue
        found = _CODE.match(body, index)
        if found is None:
            raise ValueError(_reason(spec_text, "a '%' ending the time format"))
        index = found.end()
        precision, digits, code = found.groups()
        if code == "%" and not precision and not digits:
            outer += "%"
            inner += "%"
            continue
        if code == "u" and not precision:
            if not digits:
                raise ValueError(_reason(spec_text, "%u without its digit count"))
            if not separator:
                raise ValueError(
                    _reason(spec_text, "%u after '%;', the system decimal separator")
                )
            name = "u"
            text = f"{re.escape(separator)}?[0-9]{{1,{digits}}}"
            named = f"{re.escape(separator)}?(?P<u>[0-9]{{1,{digits}}})"
        elif code in _NUMBERS and not precision and not digits:
            name, text = _NUMBERS[code]
            named = f"(?P<{name}>{text})"
        else:
            raise ValueError(_reason(spec_text, f"the code '{found.group(0)}'"))
        if name in seen:
            raise ValueError(_reason(spec_text, f"the code '%{code}' twice"))
        seen.add(name)
        outer += f"(?:{text})"
        inner += named
    if "y" in seen and "Y" in seen:
        raise ValueError(_reason(spec_text, "both '%y' and '%Y'"))
    return TimeScan(
        pattern=outer,
        value=_value(inner, utc, "u" in seen),
        imports=(
            "import re",
            "import time",
            "import datetime",
            "import hightime",
            "from datetime import UTC",
        ),
    )


def _reason(spec_text: str, part: str) -> str:
    return f"Scan From String specifier {spec_text!r} ({part}) has no conversion"


def _value(inner: str, utc: bool, fraction: bool) -> str:
    """The expression of the time stamp: ``m`` is the match of the text, ``d``
    the default time stamp. The default's fields (as local time unless UTC) take
    the ones the text gave, and the result is a UTC ``hightime`` time stamp. The
    braces of the pattern are doubled: the template is ``str.format``-ed with the
    text and the default."""
    fields = (
        "hightime.datetime("
        "(int(g['Y']) if g.get('Y') else "
        "(2000 + int(g['y']) if int(g['y']) <= 68 else 1900 + int(g['y']))"
        " if g.get('y') else b.year), "
        "int(g.get('m') or b.month), int(g.get('d') or b.day), "
        "int(g['H']) if g.get('H') else b.hour, "
        "int(g['M']) if g.get('M') else b.minute, "
        "int(g['S']) if g.get('S') else b.second, "
        f"{_fraction_arguments(fraction)}, tzinfo=UTC)"
    )
    if utc:
        base = "d"
        built = f"(lambda g, b: {fields})(m.groupdict(), {base})"
    else:
        base = (
            "(lambda x: d + datetime.timedelta(seconds="
            f"{_LOCAL_OFFSET}.total_seconds()))(d)"
        )
        local = (
            "(lambda t: t - datetime.timedelta(seconds=(lambda x: "
            f"{_LOCAL_OFFSET}.total_seconds())"
            "(datetime.datetime(t.year, t.month, t.day, t.hour, t.minute, "
            "t.second))))"
        )
        built = f"{local}((lambda g, b: {fields})(m.groupdict(), {base}))"
    body = f"(lambda m, d: {built})(re.fullmatch({inner!r}, {{g}}), {{d}})"
    return (
        body.replace("{", "{{")
        .replace("}", "}}")
        .replace("{{g}}", "{g}")
        .replace("{{d}}", "{d}")
    )


def _fraction_arguments(fraction: bool) -> str:
    """The sub-second keyword arguments of the time stamp: the digits the text
    gave (to the yoctosecond), else the default's."""
    if not fraction:
        return (
            "microsecond=b.microsecond, femtosecond=b.femtosecond, "
            "yoctosecond=b.yoctosecond"
        )
    yocto = "int((g['u'] + '0' * 24)[:24])"
    return (
        f"microsecond={yocto} // 10**18, femtosecond={yocto} // 10**9 % 10**9, "
        f"yoctosecond={yocto} % 10**9"
    )

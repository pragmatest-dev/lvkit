"""The text of one string value of a case frame's label.

A string case frame lists single strings and ranges (``"a".."c"``). Every view
quotes the values of a label one by one with its own quoting; a range is quoted
at each end, so a range is its own type of value, not a single string that
happens to contain ``..``.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import StringRange

RANGE_SEPARATOR = ".."


class StringRangeText(str):
    """One string range of a case frame's label. As a plain string it is the
    unquoted ``start..end`` (an open side is empty); ``start`` and ``end`` are
    the ends, ``None`` for an open side."""

    start: str | None
    end: str | None

    def __new__(cls, start: str | None, end: str | None) -> StringRangeText:
        text = super().__new__(cls, f"{start or ''}{RANGE_SEPARATOR}{end or ''}")
        text.start = start
        text.end = end
        return text

    @classmethod
    def of(cls, string_range: StringRange) -> StringRangeText:
        return cls(string_range.start, string_range.end)


def quote_selector_value(value: str, quote: Callable[[str], str]) -> str:
    """``value`` of a string case frame's label, quoted by ``quote``: a single
    string whole, a range at each end (``"a".."c"``, ``"m"..``, ``.."c"``)."""
    if isinstance(value, StringRangeText):
        start = "" if value.start is None else quote(value.start)
        end = "" if value.end is None else quote(value.end)
        return f"{start}{RANGE_SEPARATOR}{end}"
    return quote(value)

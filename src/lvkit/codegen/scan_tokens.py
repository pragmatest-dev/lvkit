"""Scan String For Tokens: the function written into a generated module that
returns the next token of a string.

NI's page (labview-api-ref ``functions/scan-string-for-tokens.html``): the
function scans ``input string`` from ``offset`` and returns the next token; text
between ``delimiters`` is a token, the delimiters themselves are never returned,
and by default (allow empty tokens FALSE) several adjacent delimiters separate two
tokens like one. An ``operators`` string is a token wherever it appears, "even if
they are not surrounded by delimiters", the longest match wins, and ``token
index`` is its position in ``operators`` (``-1`` for any other token, ``-2`` when
the end of the string is reached). ``offset past token`` is the point after the
token and any trailing delimiters, ``-1`` when the offset is out of the string or
the end was reached. The worked example on that page (``This is a
testLabVIEWstring`` with operators ``NI, asdf, LabVIEW``) gives the tokens ``This``,
``is``, ``a``, ``test`` (offset 14, where the operator starts), ``LabVIEW``
(index 2, offset 21), ``string`` (offset -1) and then an empty token with index -2.

The default delimiters are the space, tab, carriage return and linefeed
characters (the page writes them ``\\s,\\t,\\r,\\n``, LabVIEW's backslash codes
for those characters).
"""

from __future__ import annotations

DEFAULT_DELIMITERS = (" ", "\t", "\r", "\n")


def scan_string_for_tokens(string, offset, delimiters, operators):
    """Returns ``(token index, token string, offset past token)``."""
    end = len(string)
    if offset < 0 or offset > end:
        return -2, "", -1

    def skip_delimiters(position):
        moved = True
        while moved and position < end:
            moved = False
            for delimiter in delimiters:
                if delimiter and string.startswith(delimiter, position):
                    position += len(delimiter)
                    moved = True
                    break
        return position

    def operator_at(position):
        matches = [op for op in operators if op and string.startswith(op, position)]
        return max(matches, key=len) if matches else None

    def is_delimiter_at(position):
        return any(
            delimiter and string.startswith(delimiter, position)
            for delimiter in delimiters
        )

    position = skip_delimiters(offset)
    if position >= end:
        return -2, "", -1
    operator = operator_at(position)
    if operator is not None:
        token, index = operator, list(operators).index(operator)
        position += len(operator)
    else:
        start = position
        while (
            position < end
            and not is_delimiter_at(position)
            and operator_at(position) is None
        ):
            position += 1
        token, index = string[start:position], -1
    position = skip_delimiters(position)
    return index, token, position if position < end else -1

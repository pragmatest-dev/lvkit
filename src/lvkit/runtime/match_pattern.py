"""LabVIEW Match Pattern: its pattern syntax, translated to Python ``re``.

The ``regular expression`` input of LabVIEW's Match Pattern function uses a small
syntax of its own. This module follows NI's public pages:

* Match Pattern Function (labview-api-ref, ``functions/match-pattern.html``)
* Special Characters for Match Pattern (labview,
  ``special-characters-for-match-pattern.html``)

Rules implemented:

* ``.`` matches any single character except a newline; inside ``[]`` it is literal.
* ``*``, ``+``, ``?`` apply to the single preceding character or character class
  and match as many characters as possible. With no such preceding unit they are
  ordinary characters.
* ``[...]`` is a character class. Special characters inside are literal, except
  ``-`` (a range between digits, between lowercase letters or between uppercase
  letters), a leading ``~`` (any character except those listed), a leading ``^``
  (any displayable character, space included, except those listed) and ``\\``.
* ``^`` as the first character of the pattern anchors the match at the offset;
  elsewhere it is an ordinary character.
* ``$`` as the last character of the pattern anchors the match at the end of the
  string; elsewhere it is an ordinary character.
* ``\\`` cancels the meaning of a special character (``\\?``, ``\\.``, ``\\\\``).
  ``\\b`` backspace, ``\\f`` form feed, ``\\n`` newline, ``\\s`` space, ``\\r``
  carriage return, ``\\t`` tab, and ``\\xx`` the character whose hex code ``xx`` is
  two of 0-9 / uppercase A-F. A ``\\`` followed by any other character stands for
  that character, and a ``\\`` ending the pattern stands for a backslash.
* Everything else, parentheses and ``|`` included, is an ordinary character:
  Match Pattern has no grouping, alternation, backreferences or non-greedy
  quantifiers.
"""

from __future__ import annotations

import re as _re

_NAMED_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "s": " ",
    "r": "\r",
    "t": "\t",
}
_HEX_DIGITS = "0123456789ABCDEF"
_QUANTIFIERS = "*+?"
# Everything but control characters: the characters a ``^`` class may match.
_DISPLAYABLE = r"(?=[^\x00-\x1f\x7f])"
_ANY_CHAR = r"[\s\S]"


def _read_escape(pattern: str, i: int) -> tuple[str, int]:
    """Decode the escape whose backslash is at ``pattern[i]``.

    Returns the character it stands for and the index after the escape.
    """
    if i + 1 >= len(pattern):
        return "\\", i + 1
    c = pattern[i + 1]
    if c in _NAMED_ESCAPES:
        return _NAMED_ESCAPES[c], i + 2
    hi, lo = pattern[i + 1], pattern[i + 2 : i + 3]
    if hi in _HEX_DIGITS and lo and lo in _HEX_DIGITS:
        return chr(int(hi + lo, 16)), i + 3
    return c, i + 2


def _range_kind(c: str) -> int:
    """0 for a digit, 1 lowercase, 2 uppercase, -1 for any other character."""
    if "0" <= c <= "9":
        return 0
    if "a" <= c <= "z":
        return 1
    if "A" <= c <= "Z":
        return 2
    return -1


def _read_class(pattern: str, i: int) -> tuple[str, int] | None:
    """Translate the character class opened by ``pattern[i] == "["``.

    Returns the Python text and the index after the closing ``]``, or ``None``
    when the class is never closed.
    """
    j = i + 1
    negation = ""
    if j < len(pattern) and pattern[j] in "~^":
        negation = pattern[j]
        j += 1
    members: list[str] = []
    while j < len(pattern) and pattern[j] != "]":
        if pattern[j] == "\\":
            ch, j = _read_escape(pattern, j)
        else:
            ch, j = pattern[j], j + 1
        is_range = (
            j + 1 < len(pattern)
            and pattern[j] == "-"
            and pattern[j + 1] not in "]\\"
            and _range_kind(ch) >= 0
            and _range_kind(ch) == _range_kind(pattern[j + 1])
            and ch <= pattern[j + 1]
        )
        if is_range:
            members.append(f"{_re.escape(ch)}-{_re.escape(pattern[j + 1])}")
            j += 2
        else:
            members.append(_re.escape(ch))
    if j >= len(pattern):
        return None
    body = "".join(members)
    if negation == "~":
        text = f"[^{body}]" if body else _ANY_CHAR
    elif negation == "^":
        text = f"{_DISPLAYABLE}[^{body}]" if body else _DISPLAYABLE + _ANY_CHAR
    else:
        text = f"[{body}]" if body else r"(?!)"
    return text, j + 1


def to_python_regex(pattern: str) -> str:
    """Translate a Match Pattern ``regular expression`` into a Python ``re`` pattern."""
    units: list[str] = []
    # Whether the last unit is a character or class a quantifier may follow.
    quantifiable = False
    quantified = False
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "^" and i == 0:
            units.append(r"\A")
            quantifiable = False
            i += 1
            continue
        if c == "$" and i == n - 1:
            units.append(r"\Z")
            quantifiable = False
            i += 1
            continue
        if c in _QUANTIFIERS and quantifiable:
            last = units.pop()
            units.append(f"(?:{last}){c}" if quantified else f"{last}{c}")
            quantified = True
            i += 1
            continue
        if c == "[":
            cls = _read_class(pattern, i)
            if cls is not None:
                units.append(cls[0])
                i = cls[1]
                quantifiable, quantified = True, False
                continue
        if c == "\\":
            ch, i = _read_escape(pattern, i)
            units.append(_re.escape(ch))
        elif c == ".":
            units.append(".")
            i += 1
        else:
            units.append(_re.escape(c))
            i += 1
        quantifiable, quantified = True, False
    return "".join(units)


def match_pattern(
    string: str, pattern: str, offset: int = 0
) -> tuple[str, str, str, int]:
    """LabVIEW Match Pattern: ``(before, match, after, offset_past_match)``.

    The search starts ``offset`` characters into ``string`` (a negative offset
    counts as 0). With no match: the whole string, empty, empty, -1.
    """
    start = max(offset, 0)
    m = _re.compile(to_python_regex(pattern)).search(string[start:])
    if m is None:
        return string, "", "", -1
    end = start + m.end()
    return string[: start + m.start()], m.group(), string[end:], end

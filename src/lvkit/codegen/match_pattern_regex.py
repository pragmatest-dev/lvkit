"""LabVIEW's Match Pattern syntax, translated to Python ``re``.

``to_python_regex`` is one self-contained function that needs only ``re``. The
code generator calls it to translate a constant pattern, and writes its source
into a generated module (``function_source``) when a pattern is computed at run
time, so both read the same text.
"""

from __future__ import annotations

import inspect
import re


def to_python_regex(pattern: str) -> str:
    """Translate a Match Pattern ``regular expression`` into a Python ``re`` pattern.

    Follows NI's Match Pattern Function and Special Characters for Match Pattern
    pages:

    * ``.`` matches any character except a newline; inside ``[]`` it is literal.
    * ``*``, ``+``, ``?`` apply to the single preceding character or character
      class and match as many characters as possible; with no such preceding
      unit they are ordinary characters.
    * ``[...]`` is a character class. Special characters inside are literal,
      except ``-`` (a range between digits, between lowercase letters or between
      uppercase letters), a leading ``~`` (any character except those listed), a
      leading ``^`` (any displayable character, space included, except those
      listed) and ``\\``.
    * ``^`` first in the pattern anchors the match at the search start; ``$``
      last anchors it at the end of the string. Elsewhere they are ordinary.
    * ``\\`` cancels a special character; ``\\b`` backspace, ``\\f`` form feed,
      ``\\n`` newline, ``\\s`` space, ``\\r`` carriage return, ``\\t`` tab and
      ``\\xx`` the character whose hex code ``xx`` is two of 0-9 / A-F. Any
      other character after ``\\`` stands for itself; a final ``\\`` is a
      backslash.
    * Everything else, parentheses and ``|`` included, is an ordinary character.
    """
    named_escapes = {"b": "\b", "f": "\f", "n": "\n", "s": " ", "r": "\r", "t": "\t"}
    hex_digits = "0123456789ABCDEF"
    displayable = r"(?=[^\x00-\x1f\x7f])"
    any_char = r"[\s\S]"

    def read_escape(i: int) -> tuple[str, int]:
        """The character the escape at ``pattern[i]`` stands for, and the index
        after it."""
        if i + 1 >= len(pattern):
            return "\\", i + 1
        c = pattern[i + 1]
        if c in named_escapes:
            return named_escapes[c], i + 2
        lo = pattern[i + 2 : i + 3]
        if c in hex_digits and lo and lo in hex_digits:
            return chr(int(c + lo, 16)), i + 3
        return c, i + 2

    def range_kind(c: str) -> int:
        """0 for a digit, 1 lowercase, 2 uppercase, -1 for any other character."""
        if "0" <= c <= "9":
            return 0
        if "a" <= c <= "z":
            return 1
        if "A" <= c <= "Z":
            return 2
        return -1

    def read_class(i: int) -> tuple[str, int] | None:
        """The text of the class opened at ``pattern[i]`` and the index after
        its ``]``, or None when it is never closed."""
        j = i + 1
        negation = ""
        if j < len(pattern) and pattern[j] in "~^":
            negation = pattern[j]
            j += 1
        members: list[str] = []
        while j < len(pattern) and pattern[j] != "]":
            if pattern[j] == "\\":
                ch, j = read_escape(j)
            else:
                ch, j = pattern[j], j + 1
            is_range = (
                j + 1 < len(pattern)
                and pattern[j] == "-"
                and pattern[j + 1] not in "]\\"
                and range_kind(ch) >= 0
                and range_kind(ch) == range_kind(pattern[j + 1])
                and ch <= pattern[j + 1]
            )
            if is_range:
                members.append(f"{re.escape(ch)}-{re.escape(pattern[j + 1])}")
                j += 2
            else:
                members.append(re.escape(ch))
        if j >= len(pattern):
            return None
        body = "".join(members)
        if negation == "~":
            text = f"[^{body}]" if body else any_char
        elif negation == "^":
            text = f"{displayable}[^{body}]" if body else displayable + any_char
        else:
            text = f"[{body}]" if body else r"(?!)"
        return text, j + 1

    units: list[str] = []
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
        if c in "*+?" and quantifiable:
            last = units.pop()
            units.append(f"(?:{last}){c}" if quantified else f"{last}{c}")
            quantified = True
            i += 1
            continue
        if c == "[":
            found = read_class(i)
            if found is not None:
                units.append(found[0])
                i = found[1]
                quantifiable, quantified = True, False
                continue
        if c == "\\":
            ch, i = read_escape(i)
            units.append(re.escape(ch))
        elif c == ".":
            units.append(".")
            i += 1
        else:
            units.append(re.escape(c))
            i += 1
        quantifiable, quantified = True, False
    return "".join(units)


def function_source(name: str) -> str:
    """The source of ``to_python_regex`` defined under ``name``."""
    return inspect.getsource(to_python_regex).replace(
        "def to_python_regex(", f"def {name}(", 1
    )

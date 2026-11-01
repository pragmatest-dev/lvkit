"""LabVIEW's regular-expression mode (PCRE) read as Python ``re``.

Search and Replace String in Regular Expression mode, and Match Regular
Expression, read their pattern as PCRE (NI: "Special Characters for Match Regular
Expression and Search and Replace String"). Each function here is one
self-contained function that needs only ``re``: code generation calls it to
translate a constant pattern, and ``module_function.function_source`` writes its
source into a generated module when the pattern is computed at run time.

A construct Python ``re`` cannot express raises ``ValueError`` naming it.
"""

from __future__ import annotations

import re


def pcre_to_python_regex(pattern: str) -> str:
    """Translate a PCRE ``pattern`` into a Python ``re`` pattern (match it with
    ``re.ASCII``, which makes ``\\w``, ``\\d``, ``\\s`` and ``\\b`` the ASCII sets
    NI documents).

    Python reads these differently from the NI page, so they are rewritten:

    * ``\\c`` matches any control or non-printing character (NI); Python has no
      ``\\c``.
    * ``\\031`` is an octal character and ``\\x3F`` a hexadecimal one (one or two
      digits, or ``\\x{...}``); each becomes the escaped character.
    * ``\\1`` to ``\\9`` is a backreference, never read with a following digit.
    * ``\\e`` is the escape character and ``\\z`` the end of the string.
    * ``{`` not forming ``{n}``, ``{n,}`` or ``{n,m}`` is an ordinary character.
    * ``(?<name>...)`` is a named group.
    * Inside ``[]`` every character is literal except ``^`` first, ``-`` between
      two characters and ``\\``.
    """
    named = "bBdDwWsSnrtfa"
    n = len(pattern)

    def escape(i: int) -> tuple[str, str | None, int]:
        # The pattern text for the escape at pattern[i], the single character it
        # stands for (None for a set or an anchor), and the index after it.
        if i + 1 >= n:
            raise ValueError("a backslash at the end of the pattern")
        c = pattern[i + 1]
        if c in named:
            single = {"n": "\n", "r": "\r", "t": "\t", "f": "\f", "a": "\a"}.get(c)
            return "\\" + c, single, i + 2
        if c == "c":
            return r"[\x00-\x1f\x7f]", None, i + 2
        if c == "e":
            return r"\x1b", "\x1b", i + 2
        if c == "z":
            return r"\Z", None, i + 2
        if c == "Z":
            return r"(?=\n?\Z)", None, i + 2
        if c == "A":
            return r"\A", None, i + 2
        if c in "123456789":
            return f"(?:\\{c})", None, i + 2
        if c == "0":
            digits = re.findall(r"^[0-7]{0,2}", pattern[i + 2 :])[0]
            ch = chr(int(digits or "0", 8))
            return re.escape(ch), ch, i + 2 + len(digits)
        if c == "x":
            braced = re.match(r"\{([0-9A-Fa-f]+)\}", pattern[i + 2 :])
            if braced:
                ch = chr(int(braced.group(1), 16))
                return re.escape(ch), ch, i + 2 + len(braced.group())
            digits = re.findall(r"^[0-9A-Fa-f]{0,2}", pattern[i + 2 :])[0]
            ch = chr(int(digits or "0", 16))
            return re.escape(ch), ch, i + 2 + len(digits)
        if c.isalnum() or c == "_":
            raise ValueError(f"the escape \\{c}")
        return re.escape(c), c, i + 2

    def read_class(i: int) -> tuple[str, int]:
        j = i + 1
        negation = ""
        if pattern[j : j + 1] == "^":
            negation = "^"
            j += 1
        items: list[tuple[str, str | None, bool]] = []
        while j < n and (pattern[j] != "]" or not items):
            if pattern[j] == "\\":
                text, ch, j = escape(j)
                items.append((text, ch, False))
            else:
                items.append((re.escape(pattern[j]), pattern[j], pattern[j] == "-"))
                j += 1
        if j >= n:
            raise ValueError("a [ with no closing ]")
        out: list[str] = []
        k = 0
        while k < len(items):
            text, ch, dash = items[k]
            if (
                ch is not None
                and not dash
                and k + 2 < len(items)
                and items[k + 1][2]
                and items[k + 2][1] is not None
            ):
                high = items[k + 2][1]
                if high is not None and high < ch:
                    raise ValueError("a range whose end is before its start")
                out.append(f"{text}-{items[k + 2][0]}")
                k += 3
                continue
            out.append("\\-" if dash else text)
            k += 1
        return f"[{negation}{''.join(out)}]", j + 1

    units: list[str] = []
    i = 0
    while i < n:
        c = pattern[i]
        if c == "\\":
            text, _, i = escape(i)
            units.append(text)
        elif c == "[":
            text, i = read_class(i)
            units.append(text)
        elif c == "{":
            quantifier = re.match(r"\{\d+(,\d*)?\}", pattern[i:])
            if quantifier:
                units.append(quantifier.group())
                i += len(quantifier.group())
            else:
                units.append("\\{")
                i += 1
        elif pattern.startswith("(?<", i) and pattern[i + 3 : i + 4] not in ("=", "!"):
            units.append("(?P<")
            i += 3
        else:
            units.append(c)
            i += 1
    return "".join(units)


def pcre_to_python_template(replacement: str) -> str:
    """Translate the ``replace string`` of Search and Replace String in Regular
    Expression mode into a Python ``re`` replacement template.

    ``$n`` (one digit) and ``${n}`` insert submatch ``n`` (NI: ``$12`` inserts
    submatch 1 followed by ``2``; ``${12}`` inserts submatch 12), ``\\$`` is a
    literal ``$`` and ``\\\\`` a literal backslash. Any other backslash and any
    other ``$`` stand for themselves.
    """
    out: list[str] = []
    i = 0
    n = len(replacement)
    while i < n:
        c = replacement[i]
        following = replacement[i + 1 : i + 2]
        if c == "\\" and following == "$":
            out.append("$")
            i += 2
        elif c == "\\":
            out.append("\\\\")
            i += 2 if following == "\\" else 1
        elif c == "$" and "0" <= following <= "9" and following:
            out.append(f"\\g<{following}>")
            i += 2
        elif c == "$" and following == "{":
            braced = re.match(r"\{(\d+)\}", replacement[i + 1 :])
            if braced:
                out.append(f"\\g<{braced.group(1)}>")
                i += 1 + len(braced.group())
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)

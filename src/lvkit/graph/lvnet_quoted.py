"""The lvnet quoted-literal reader: scan a double-quoted string literal and
reverse ``lvnet_scalar.lvnet_literal_token``'s escaping, plus the error every
lvnet reader raises. A leaf module, so the settings reader and the line parser
share one scanner.
"""

from __future__ import annotations


class LvnetParseError(ValueError):
    """A line did not fit the lvnet grammar this parser knows -- every §8
    structure kind is now covered (case/for-loop/while-loop/flat-sequence/
    stacked-sequence/diagram-disable/conditional-disable/type-specialization/
    event-structure), so this now fires only on a genuine grammar violation
    (a malformed line, a missing frame, a stray drive line where this scope
    family has no output to drive). Always names the offending line/
    construct -- this parser never silently skips or guesses a shape it
    hasn't verified against real rendered text.
    """


# The exact reverse of ``string_literal.STRING_ESCAPES`` (md §4/§10): every
# two-char escape lvnet's string-literal renderer can emit, mapped back to
# its real character. ``\xHH`` (any OTHER C0 control char) is handled
# separately in ``unescape_lvnet_string`` since it's three chars wide, not
# two.
_LVNET_STRING_UNESCAPES: dict[str, str] = {
    "\\": "\\",
    '"': '"',
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def scan_quoted_literal(text: str, start: int) -> int:
    """Scan an lvnet double-quoted string literal beginning at
    ``text[start] == '"'`` (render_lvnet's own §4/§10 escaping --
    ``render_lvnet.lvnet_literal_token``), honoring backslash escapes, and
    return the index ONE PAST its closing (real, unescaped) quote.

    An escaped ``\\"`` is part of the literal's own text, never mistaken for
    the real close -- so a value containing a literal ``=``/``:``/
    ``default`` substring inside its quotes (a status string like
    ``"5 = 5 is true"``) can never fool a caller's word-based clause
    splitter, and this scan won't stop early on an escaped quote either.
    Raises ``LvnetParseError`` if the quote is never closed on this line --
    a genuine grammar violation, since ``lvnet_literal_token`` escapes
    every control char to a same-line backslash sequence (lvnet never emits
    a literal spanning physical lines).
    """
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 >= n:
                raise LvnetParseError(
                    f"unterminated escape at end of quoted literal: {text[start:]!r}"
                )
            i += 2
            continue
        if ch == '"':
            return i + 1
        i += 1
    raise LvnetParseError(f"unterminated quoted literal: {text[start:]!r}")


def unescape_lvnet_string(token: str) -> str:
    """Reverse ``render_lvnet.lvnet_literal_token``'s string escaping: a
    double-quoted token (``'"foo\\\\nbar"'``) -> its real value (a genuine
    embedded newline). ``token`` must be exactly the ``"..."`` substring,
    quotes included -- callers isolate it first via ``scan_quoted_literal``
    (a bare closing-quote match), so this never needs its own boundary
    search. Raises ``LvnetParseError`` on a malformed/unrecognized escape
    (never silently drops or guesses at one)."""
    if len(token) < 2 or not (token.startswith('"') and token.endswith('"')):
        raise LvnetParseError(f"not a quoted lvnet string literal: {token!r}")
    body = token[1:-1]
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            raise LvnetParseError(
                f"trailing backslash in lvnet string literal: {token!r}"
            )
        esc = body[i + 1]
        if esc == "x":
            hex_digits = body[i + 2 : i + 4]
            if len(hex_digits) != 2:
                raise LvnetParseError(
                    f"malformed \\x escape in lvnet string literal: {token!r}"
                )
            try:
                out.append(chr(int(hex_digits, 16)))
            except ValueError:
                raise LvnetParseError(
                    f"malformed \\x escape in lvnet string literal: {token!r}"
                ) from None
            i += 4
            continue
        real = _LVNET_STRING_UNESCAPES.get(esc)
        if real is None:
            raise LvnetParseError(
                f"unrecognized escape '\\{esc}' in lvnet string literal: {token!r}"
            )
        out.append(real)
        i += 2
    return "".join(out)


def split_top_level_commas(text: str) -> list[str]:
    """Split ``text`` on ``, `` at brace/bracket DEPTH ZERO and OUTSIDE any
    quoted token -- separates an ``enum{...}``'s members or a ``Cluster{...}``'s
    fields (§10) without breaking on a comma inside a NESTED structural type
    (a cluster field whose own type is another ``Cluster{...}``/array), on a
    comma inside a QUOTED member/field NAME (a real LabVIEW name like
    ``"big-endian, network order"`` -- ``lvnet_name_token`` quotes+escapes
    such names, honoring ``\\``-escapes), NOR on a comma with no space after it,
    which a bare name may hold (a decimal comma: ``ring{0,625, 1,25}``)."""
    parts: list[str] = []
    depth = 0
    in_quote = False
    escaped = False
    current: list[str] = []
    for i, ch in enumerate(text):
        current.append(ch)
        if in_quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 0 and text[i + 1 : i + 2] == " ":
            current.pop()  # drop the comma we just appended
            parts.append("".join(current).strip())
            current = []
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts

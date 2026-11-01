r"""A string as a double-quoted literal with backslash escapes (lvnet §4).

The one escape table: ``\\``, ``\"``, ``\n``, ``\r``, ``\t``, and any other C0
control character as ``\xHH``, so a string never spans a physical line.
"""

from __future__ import annotations

STRING_ESCAPES: dict[str, str] = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def quote_string(value: str) -> str:
    """``value`` as a double-quoted, escaped literal."""
    out: list[str] = []
    for ch in value:
        escaped = STRING_ESCAPES.get(ch)
        if escaped is not None:
            out.append(escaped)
        elif ord(ch) < 0x20:
            out.append(f"\\x{ord(ch):02X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'

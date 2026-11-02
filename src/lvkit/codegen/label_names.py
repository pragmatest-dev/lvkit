"""The label a front-panel terminal's Python name is made from.

A label often ends with a parenthetical that states the terminal's default
value (``error in (no error)``, ``enable (T)``, ``count (0)``): guidance for the
person wiring it, not part of a name. The Python name drops that parenthetical
when it states the default the terminal actually has; any other parenthetical,
such as a unit (``timeout (ms)``), stays in the name. A VI's function and every
caller of it name the terminal through ``python_label``, from the same terminal
data, so the keyword a caller passes is the parameter the function takes.
"""

from __future__ import annotations

import ast
import re

from lvkit.models import LVTypeKind, Terminal

_PARENTHETICAL = re.compile(r"\s*\(([^()]*)\)")
_BOOLEAN_TEXT = {"t": True, "true": True, "f": False, "false": False}
# LabVIEW's '\' codes display: the character each one-letter code stands for.
_BACKSLASH_CODES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "s": " ", "t": "\t"}
# A hex code is two of 0-9 and uppercase A-F.
_HEX = "0123456789ABCDEF"


def python_label(term: Terminal) -> str | None:
    """The terminal's label without its last parenthetical when that states the
    terminal's default value; the label itself otherwise (``None`` when it has
    none). A label that is only the parenthetical keeps it."""
    label = term.name
    if not label:
        return label
    found = list(_PARENTHETICAL.finditer(label))
    if not found:
        return label
    last = found[-1]
    rest = (label[: last.start()] + label[last.end() :]).strip()
    if rest and _states_default(term, last.group(1).strip()):
        return rest
    return label


def _states_default(term: Terminal, text: str) -> bool:
    """Whether ``text``, a parenthetical of the terminal's label, is the value
    the terminal holds by default: ``no error`` for an error cluster, T/F/TRUE/
    FALSE for a Boolean, a number for a numeric, the item name for an enum or
    ring, a quoted string for a string or path. A terminal that saves no default
    holds its type's (False, 0, the first item, the empty string)."""
    if term.is_error_cluster:
        return text.lower() == "no error"
    lv_type = term.lv_type
    if lv_type is None:
        return False
    default = term.default_value
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING) and lv_type.values:
        index = _number(default) if default not in (None, "") else 0
        return any(
            item.value == index and name.strip().lower() == text.lower()
            for name, item in lv_type.values.items()
        )
    underlying = lv_type.underlying_type or ""
    if underlying == "Boolean":
        stated = _BOOLEAN_TEXT.get(text.lower())
        value = default is not None and str(default).strip().lower() in ("true", "1")
        return stated is not None and stated == value
    if underlying.startswith("Num"):
        stated_number = _number(text.replace(",", ""))
        value = _number(default) if default not in (None, "") else 0
        return stated_number is not None and stated_number == value
    if underlying in ("String", "Path") and len(text) >= 2:
        if not (text.startswith('"') and text.endswith('"')):
            return False
        return _backslash_decoded(text[1:-1]) == _saved_text(default, underlying)
    return False


def _number(value: object) -> float | None:
    """``value`` as a number, or ``None`` when it is not one."""
    try:
        return float(str(value))
    except ValueError:
        return None


def _saved_text(default: object, underlying: str) -> str | None:
    """The text a string or path terminal holds by default: a saved string
    default is a Python string literal; a path's is its text."""
    if default is None:
        return ""
    if underlying == "Path":
        return str(default)
    try:
        value = ast.literal_eval(str(default))
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _backslash_decoded(text: str) -> str:
    """``text`` as LabVIEW's '\\' codes display reads it: ``\\s`` a space,
    ``\\t`` a tab, ``\\xx`` the character of hex code ``xx``, ``\\\\`` a
    backslash; any other character after a backslash stands for itself."""
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c != "\\" or i + 1 >= len(text):
            out.append(c)
            i += 1
            continue
        code = text[i + 1]
        pair = text[i + 1 : i + 3]
        if len(pair) == 2 and all(ch in _HEX for ch in pair):
            out.append(chr(int(pair, 16)))
            i += 3
        elif code in _BACKSLASH_CODES:
            out.append(_BACKSLASH_CODES[code])
            i += 2
        else:
            out.append(code)
            i += 2
    return "".join(out)

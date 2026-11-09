"""The label a terminal's Python name is made from.

A label often ends with a parenthetical that states the terminal's default
value (``error in (no error)``, ``enable (T)``, ``count (0)``): guidance for the
person wiring it, not part of a name. The Python name drops that parenthetical
when it states the default the terminal actually has; any other parenthetical,
such as a unit (``timeout (ms)``), stays in the name. An enumerated default is
written ``value:item`` (``byte order (0:big-endian, network order)``). A VI's
function and every caller of it name the terminal through ``python_label``, from
the same terminal data, so the keyword a caller passes is the parameter the
function takes. A primitive's catalog terminal names itself the same way through
``catalog_python_label``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from lvkit.models import LVTypeKind, Terminal
from lvkit.primitive_resolver.models import PrimitiveTerminal

_PARENTHETICAL = re.compile(r"\s*\(([^()]*)\)")
_ENUMERATED = re.compile(r"^(-?\d+)\s*:\s*(.+)$")
_BOOLEAN_TEXT = {"t": True, "true": True, "f": False, "false": False}
# LabVIEW's '\' codes display: the character each one-letter code stands for.
_BACKSLASH_CODES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "s": " ", "t": "\t"}
# A hex code is two of 0-9 and uppercase A-F.
_HEX = "0123456789ABCDEF"


@dataclass(frozen=True)
class DefaultFacts:
    """What a terminal's label may state as its default: whether it is an error
    cluster, its underlying type (``Boolean``, ``Num...``, ``String``, ``Path``,
    or ``""`` when unknown), its enum or ring items (name -> value; empty when
    it has none or they are not recorded), whether it is an enum or ring, and
    the value it holds by default (``None``: its type's)."""

    error_cluster: bool = False
    underlying: str = ""
    is_enum: bool = False
    items: dict[str, int] = field(default_factory=dict)
    default: object = None


def _facts_of_terminal(term: Terminal) -> DefaultFacts:
    """The facts of a VI's front-panel terminal."""
    lv_type = term.lv_type
    if lv_type is None:
        return DefaultFacts(error_cluster=term.is_error_cluster)
    is_enum = lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING) and bool(
        lv_type.values
    )
    items = (
        {name.strip(): item.value for name, item in (lv_type.values or {}).items()}
        if is_enum
        else {}
    )
    return DefaultFacts(
        error_cluster=term.is_error_cluster,
        underlying=lv_type.underlying_type or "",
        is_enum=is_enum,
        items=items,
        default=term.default_value,
    )


def _facts_of_catalog(term: PrimitiveTerminal) -> DefaultFacts:
    """The facts of a primitive's catalog terminal, from its loose type string.
    A catalog terminal records a default only when NI documents one, so a
    polymorphic or ``numeric`` terminal states a numeric default only when one is
    recorded."""
    kind = (term.type or "").lower()
    if kind in ("error", "errorcluster"):
        return DefaultFacts(error_cluster=True)
    if kind == "boolean":
        return DefaultFacts(underlying="Boolean", default=term.default)
    if kind == "string":
        return DefaultFacts(underlying="String", default=term.default)
    if kind == "path":
        return DefaultFacts(underlying="Path", default=term.default)
    if kind.startswith(("enum", "unit")):
        return DefaultFacts(
            underlying="Num", is_enum=kind.startswith("enum"), default=term.default
        )
    if kind.startswith("num") and kind != "numeric":
        return DefaultFacts(underlying="Num", default=term.default)
    if kind in ("numeric", "polymorphic") and _number(term.default) is not None:
        return DefaultFacts(underlying="Num", default=term.default)
    return DefaultFacts()


def python_label(term: Terminal) -> str | None:
    """The terminal's label without its last parenthetical when that states the
    terminal's default value; the label itself otherwise (``None`` when it has
    none). A label that is only the parenthetical keeps it."""
    return _without_default(term.name, _facts_of_terminal(term))


def catalog_python_label(term: PrimitiveTerminal) -> str | None:
    """A catalog terminal's label (``name``) as ``python_label`` gives it."""
    return _without_default(term.name, _facts_of_catalog(term))


def _without_default(label: str | None, facts: DefaultFacts) -> str | None:
    if not label:
        return label
    found = list(_PARENTHETICAL.finditer(label))
    if not found:
        return label
    last = found[-1]
    rest = (label[: last.start()] + label[last.end() :]).strip()
    if rest and _states_default(facts, last.group(1).strip()):
        return rest
    return label


def _states_default(facts: DefaultFacts, text: str) -> bool:
    """Whether ``text``, a parenthetical of the terminal's label, is the value
    the terminal holds by default: ``no error`` for an error cluster, T/F/TRUE/
    FALSE for a Boolean, a number for a numeric, the item name (or
    ``value:item``) for an enum or ring, a quoted string for a string or path. A
    terminal that saves no default holds its type's (False, 0, the first item,
    the empty string)."""
    if facts.error_cluster:
        return text.lower() == "no error"
    default = facts.default
    enumerated = _ENUMERATED.match(text)
    if facts.is_enum and facts.items:
        index = _number(default) if default not in (None, "") else 0
        item_text = enumerated.group(2).strip() if enumerated else text
        if enumerated and _number(enumerated.group(1)) != index:
            return False
        return any(
            item.lower() == item_text.lower() and value == index
            for item, value in facts.items.items()
        )
    underlying = facts.underlying
    if underlying == "Boolean":
        stated = _BOOLEAN_TEXT.get(text.lower())
        value = default is not None and str(default).strip().lower() in ("true", "1")
        return stated is not None and stated == value
    if underlying.startswith("Num"):
        value = _number(default) if default not in (None, "") else 0
        if enumerated:
            return _number(enumerated.group(1)) == value
        stated_number = _number(text.replace(",", ""))
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

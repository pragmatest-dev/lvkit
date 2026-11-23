"""The lvnet ``(<settings>)`` group of a node or structure header (§3).

ONE writer and ONE reader for every header kind. A kind's settings are a frozen
dataclass (``netlist_models``); each field is one setting. The writer walks the
dataclass's fields; the reader looks the kind's dataclass up in
``lvnet_grammar.LVNET_SETTINGS_BY_KEYWORD`` and builds it. Whether a trailing
``(...)`` is the settings group is decided by its SHAPE alone (every item is
``name=value``), never by what the names or values say.
"""

from __future__ import annotations

import dataclasses
import re
import typing
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

from .lvnet_grammar import (
    _LVNET_SETTING_ASSIGN,
    _LVNET_SETTING_ITEM_SEP,
    _LVNET_UNWIRED,
    LVNET_SETTINGS_BY_KEYWORD,
)
from .lvnet_quoted import (
    LvnetParseError,
    scan_quoted_literal,
    split_top_level_commas,
    unescape_lvnet_string,
)
from .lvnet_scalar import lvnet_literal_token
from .netlist_models import NodeSettings

_S = TypeVar("_S", bound=NodeSettings)

# A setting item's name: a lowercase hyphenated word, directly followed by ``=``
# (no spaces around it, so ``name = value`` is never a setting).
_SETTING_ITEM_RE = re.compile(
    rf"^([a-z]+(?:-[a-z]+)*){_LVNET_SETTING_ASSIGN}(.*)$", re.S
)
_INT_RE = re.compile(r"^-?\d+$")


def setting_name(field_name: str) -> str:
    """A settings field's name as lvnet and JSON spell it."""
    return field_name.replace("_", "-")


def _setting_fields(
    settings: NodeSettings,
) -> list[tuple[dataclasses.Field[Any], Any]]:
    return [(f, getattr(settings, f.name)) for f in dataclasses.fields(settings)]


def _has_default(field: dataclasses.Field[Any]) -> bool:
    return (
        field.default is not dataclasses.MISSING
        or field.default_factory is not dataclasses.MISSING
    )


def _value_token(value: object) -> str:
    """A setting's value as a §4 literal: ``True``/``False``, a number, a quoted
    string, a bare enum item, or ``?`` for a value the file did not give."""
    if value is None:
        return _LVNET_UNWIRED
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool | int | str):
        return lvnet_literal_token(value)
    raise TypeError(f"not a setting value: {value!r}")


def settings_items(settings: NodeSettings | None) -> list[tuple[str, str]]:
    """The ``(name, value token)`` of each setting that is not at its default, in
    field order."""
    if settings is None:
        return []
    return [
        (setting_name(f.name), _value_token(value))
        for f, value in _setting_fields(settings)
        if not (_has_default(f) and value == f.default)
    ]


def lvnet_settings_group(settings: NodeSettings | None) -> str:
    """`` (a=x, b=y)`` for the settings that are not at their default, in field
    order, or the empty string when there are none."""
    items = [
        f"{name}{_LVNET_SETTING_ASSIGN}{token}"
        for name, token in settings_items(settings)
    ]
    if not items:
        return ""
    return f" ({_LVNET_SETTING_ITEM_SEP.join(items)})"


def settings_changes(
    before: NodeSettings | None, after: NodeSettings | None
) -> list[str]:
    """``name: old -> new`` for each setting whose value differs between two
    settings of one kind (a setting at its default shows as its value)."""
    old = dict(_all_tokens(before))
    return [
        f"{name}: {old[name]} -> {token}"
        for name, token in _all_tokens(after)
        if name in old and old[name] != token
    ]


def _all_tokens(settings: NodeSettings | None) -> list[tuple[str, str]]:
    if settings is None:
        return []
    return [
        (setting_name(f.name), _value_token(value))
        for f, value in _setting_fields(settings)
    ]


def settings_to_json(settings: NodeSettings | None) -> dict[str, Any]:
    """Every setting by its lvnet name, defaults included (``{}`` for a node
    with no settings); an enum item as its text."""
    if settings is None:
        return {}
    return {
        setting_name(f.name): value.value if isinstance(value, Enum) else value
        for f, value in _setting_fields(settings)
    }


def _trailing_group_start(text: str) -> int | None:
    """Index of the ``(`` that opens the parenthesised group ending ``text``
    (outside quotes, balanced, and preceded by a space), or ``None``."""
    if not text.endswith(")"):
        return None
    depth = 0
    in_quote = False
    i = len(text) - 1
    while i >= 0:
        ch = text[i]
        if ch == '"':
            backslashes = 0
            while i - 1 - backslashes >= 0 and text[i - 1 - backslashes] == "\\":
                backslashes += 1
            if backslashes % 2 == 0:
                in_quote = not in_quote
        elif not in_quote:
            if ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
                if depth == 0:
                    return i if i > 0 and text[i - 1] == " " else None
        i -= 1
    return None


def split_trailing_group(text: str) -> tuple[str, str] | None:
    """``text`` as ``(head, group text)`` when it ends in a parenthesised group
    set off by a space, else ``None``. The group text excludes the parentheses."""
    start = _trailing_group_start(text)
    if start is None:
        return None
    return text[: start - 1], text[start + 1 : -1]


def _group_items(group: str) -> list[tuple[str, str]] | None:
    """The ``(name, value text)`` items of a group when EVERY item has the
    ``name=value`` shape, else ``None``: only then is the group a settings slot."""
    parts = split_top_level_commas(group)
    if not parts:
        return None
    items: list[tuple[str, str]] = []
    for part in parts:
        m = _SETTING_ITEM_RE.match(part)
        if m is None:
            return None
        items.append((m.group(1), m.group(2)))
    return items


def split_settings(text: str) -> tuple[str, list[tuple[str, str]] | None]:
    """A header text as ``(head, items)``: the text before its trailing settings
    slot and the slot's ``(name, value text)`` items, or ``(text, None)`` when it
    does not end in one."""
    split = split_trailing_group(text)
    if split is None:
        return text, None
    items = _group_items(split[1])
    if items is None:
        return text, None
    return split[0], items


def ends_in_settings_group(name: str) -> bool:
    """Whether ``name`` spelled bare would END in a settings-shaped group, so a
    reader would take that group for the header's settings; such a name is
    quoted."""
    return split_settings(name)[1] is not None


def _field_types(cls: type[Any]) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _bare_type(annotation: Any) -> Any:
    """``annotation`` without a ``| None`` part."""
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    return args[0] if len(args) == 1 else annotation


def _read_value(annotation: Any, field: dataclasses.Field[Any], text: str) -> Any:
    kind = _bare_type(annotation)
    nullable = type(None) in typing.get_args(annotation)
    if text == _LVNET_UNWIRED and nullable and not _has_default(field):
        return None
    if kind is bool:
        if text in ("True", "False"):
            return text == "True"
    elif kind is int:
        if _INT_RE.match(text):
            return int(text)
    elif kind is str:
        if text.startswith('"') and scan_quoted_literal(text, 0) == len(text):
            return unescape_lvnet_string(text)
    elif isinstance(kind, type) and issubclass(kind, Enum):
        try:
            return kind(text)
        except ValueError:
            pass
    raise ValueError(text)


def read_settings(cls: type[_S], items: list[tuple[str, str]], where: str) -> _S:
    """Build a kind's settings dataclass from a header's ``(name, value text)``
    items. An unknown name, a repeated name, a value of the wrong type or a
    missing setting that has no default is an error naming the kind's allowed
    settings; ``where`` is the header line, for the message."""
    fields = {setting_name(f.name): f for f in dataclasses.fields(cls)}
    types = _field_types(cls)
    allowed = ", ".join(fields)
    values: dict[str, Any] = {}
    for name, text in items:
        field = fields.get(name)
        if field is None:
            raise LvnetParseError(
                f"{where}: unknown setting {name!r}; this header allows: {allowed}"
            )
        if field.name in values:
            raise LvnetParseError(
                f"{where}: setting {name!r} given twice; this header allows: {allowed}"
            )
        try:
            values[field.name] = _read_value(types[field.name], field, text)
        except ValueError:
            raise LvnetParseError(
                f"{where}: setting {name!r} cannot be {text!r}; this header allows: "
                f"{allowed}"
            ) from None
    try:
        return cls(**values)
    except TypeError:
        raise LvnetParseError(
            f"{where}: a required setting is missing; this header allows: {allowed}"
        ) from None


def settings_absent(cls: type[_S]) -> _S | None:
    """The settings of a header with no group: every setting at its default, or
    ``None`` when the kind has a setting without one."""
    try:
        return typing.cast(Callable[[], _S], cls)()
    except TypeError:
        return None


def read_header_settings(
    keyword: str, text: str, where: str
) -> tuple[str, NodeSettings | None]:
    """A header's text split into ``(head, settings)`` for the kind ``keyword``
    names: the text before the trailing ``(<settings>)`` group and the settings
    it holds, or ``(text, None)`` when the text ends in no such group."""
    head, items = split_settings(text)
    if items is None:
        return text, None
    return head, read_settings(LVNET_SETTINGS_BY_KEYWORD[keyword], items, where)

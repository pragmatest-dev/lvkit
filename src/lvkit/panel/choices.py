"""The items of an enum or ring control and the item it starts on.

An item is its label and the value the function takes: the item's number for an
enum, and for a ring the value the ring stores for it (the item's number when it
stores none). The value is a plain ``int``; an enum typedef's class, which the
function's module imports only for its annotations, compares equal to it."""

from __future__ import annotations

from lvkit.models import LVType
from lvkit.parser.models import ParsedFPControl

Items = tuple[tuple[str, int], ...]


def choice_items(control: ParsedFPControl, lv_type: LVType | None) -> Items:
    """The items of the control: those its type defines, else those stored with
    its front panel. Empty when neither has any."""
    if lv_type is not None and lv_type.values:
        pairs = [(label, item.value) for label, item in lv_type.values.items()]
        return tuple(sorted(pairs, key=lambda pair: pair[1]))
    labels = control.enum_values
    values = (
        control.ring_values
        if len(control.ring_values) == len(labels)
        else list(range(len(labels)))
    )
    return tuple(zip(labels, values, strict=True))


def choice_default(items: Items, saved: object) -> int:
    """The value the control starts on: the saved default when it is one of the
    items (by value or by label), else the first item."""
    values = [value for _, value in items]
    if isinstance(saved, str):
        by_label = dict(items)
        if saved.strip('"') in by_label:
            return by_label[saved.strip('"')]
    try:
        number = int(saved)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        number = None
    return number if number in values else values[0]

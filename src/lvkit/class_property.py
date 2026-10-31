"""Which accessor of a property a VI is, from its own connector pane.

A VI listed in a class's ``Property Definition`` folder either reads the property
(it outputs the property's value and takes none) or writes it (it takes the value
as an input). The class wire (the class's own type, in and out) and the error
cluster are not the value."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from lvkit.models import Terminal
from lvkit.type_defaults import _is_class_refnum


class PropertyRole(str, Enum):
    """What a property accessor VI does with the property's value."""

    GETTER = "getter"
    SETTER = "setter"


def value_terminals(terminals: Sequence[Terminal], class_name: str) -> list[Terminal]:
    """The terminals that are neither the class wire nor an error cluster."""
    return [
        t
        for t in terminals
        if not t.is_error_cluster
        and not (t.lv_type is not None and _is_class_refnum(t.lv_type, class_name))
    ]


def property_role(
    inputs: Sequence[Terminal], outputs: Sequence[Terminal], class_name: str
) -> PropertyRole | None:
    """The role of a property accessor VI with connector-pane ``inputs`` and
    ``outputs`` in the class ``class_name``: a setter takes one value, a getter
    takes none and gives one; None for a VI whose pane is neither."""
    value_in = value_terminals(inputs, class_name)
    value_out = value_terminals(outputs, class_name)
    if len(value_in) == 1:
        return PropertyRole.SETTER
    if not value_in and len(value_out) == 1:
        return PropertyRole.GETTER
    return None

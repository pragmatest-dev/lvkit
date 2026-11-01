"""The Python value of a property of a VI Server class's default object.

A Property Node whose reference input is unwired acts on the default object of
its class. A property is listed in ``default_objects.json`` only when NI's
public documentation says what its value is and a Python process can supply it;
generated code reads that expression in place of the property.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path

_PATH = Path(__file__).with_name("default_objects.json")


@dataclass(frozen=True)
class DefaultObjectProperty:
    """One documented property: the expression that is its value (with
    ``{function}`` and ``{callees}`` for the generating VI's own function and the
    functions it calls) and the imports that expression needs."""

    python: str
    imports: tuple[str, ...]


@functools.cache
def _classes() -> dict[str, dict[str, DefaultObjectProperty]]:
    with open(_PATH, encoding="utf-8") as handle:
        classes = json.load(handle)["classes"]
    return {
        class_name: {
            name: DefaultObjectProperty(entry["python"], tuple(entry["imports"]))
            for name, entry in properties.items()
        }
        for class_name, properties in classes.items()
    }


def default_object_property(
    class_name: str, property_name: str
) -> DefaultObjectProperty | None:
    """The value of ``property_name`` on the default object of ``class_name``;
    None when it has no Python value."""
    return _classes().get(class_name, {}).get(property_name)

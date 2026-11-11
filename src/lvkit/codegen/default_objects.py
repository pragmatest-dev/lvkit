"""The Python value of a property of a VI Server class's default object.

A Property Node whose reference input is unwired acts on the default object of
its class. A property is listed in ``default_objects.json`` only when NI's
public documentation says what its value is and a Python process can supply it;
generated code reads that expression in place of the property. A property is
keyed by its code path, the codes of its segments joined by ``.``, because a
Property Node row stores either the short or the long name of a property.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path

_PATH = Path(__file__).with_name("default_objects.json")


@dataclass(frozen=True)
class DefaultObjectProperty:
    """One documented property: NI's name for it, the expression that is its
    value (with ``{function}`` and ``{callees}`` for the generating VI's own
    function and the functions it calls) and the imports that expression needs."""

    name: str
    python: str
    imports: tuple[str, ...]


@functools.cache
def _classes() -> dict[str, dict[str, DefaultObjectProperty]]:
    with open(_PATH, encoding="utf-8") as handle:
        classes = json.load(handle)["classes"]
    return {
        class_name: {
            code_path: DefaultObjectProperty(
                entry["name"], entry["python"], tuple(entry["imports"])
            )
            for code_path, entry in properties.items()
        }
        for class_name, properties in classes.items()
    }


def default_object_property(
    class_name: str, codes: tuple[int, ...]
) -> DefaultObjectProperty | None:
    """The value of the property with segment ``codes`` on the default object of
    ``class_name``; None when it has no Python value."""
    return _classes().get(class_name, {}).get(".".join(map(str, codes)))

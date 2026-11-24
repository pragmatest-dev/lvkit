"""The module a generated package holds for the classes a value is read into.

Reading a flattened LabVIEW class object (Unflatten From String, Flattened String
To Variant, Variant To Data, a deprecated Ctrl Val.Set) finds the class by the
qualified name the data holds, the libraries that own it and then its file
(``Lib.lvlib:Class.lvclass``, NI "Flattened Data", "Classes"). The package writes
that name once into ``lv_classes.py``: ``CLASSES`` is one plain dict from each
qualified class name of the package, sorted, to the module of its generated class
(its dotted path from the package root) and its class name, and a class is
imported with ``importlib.import_module`` only when data holds it. A name the dict
does not have is LabVIEW error 1527, "a LabVIEW class that is not currently loaded
into LabVIEW" (NI, "LabVIEW Object-Oriented Programming Error Codes",
docs-be.ni.com/api/bundle/labview-api-ref/page/errors/
labview-object-oriented-programming-error-codes.html). Writing a class object needs
no lookup: the object's own type names its class.

The module also holds the code that writes and reads the layout NI gives for a
flattened class: ``flatten_class`` and ``unflatten_class`` write and read the
header (the level in the hierarchy, the class name, one version per level) and
call each class's own ``__lv_flatten__`` / ``__lv_unflatten__`` for the cluster of
the private data of that level (the code the class module holds,
``codegen/class_builder/flatten.py``). ``lv_classes.py`` imports nothing from the
package, so importing it can never make an import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from .class_model import python_class_name

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph

CLASSES_MODULE_NAME = "lv_classes"
FLATTEN_CLASS_NAME = "flatten_class"
UNFLATTEN_CLASS_NAME = "unflatten_class"
FLATTEN_ERROR_NAME = "FlattenedClassError"

# A relative import of the package's ``lv_classes`` module (``from .lv_classes
# import``, with more dots in a subpackage) at the start of a line of a module.
_CLASSES_IMPORT = re.compile(rf"^\s*from \.+{CLASSES_MODULE_NAME} import ", re.M)


def imports_classes(source: str) -> bool:
    """Whether the generated module ``source`` imports from the package's
    ``lv_classes`` module."""
    return _CLASSES_IMPORT.search(source) is not None


_CLASSES_TEMPLATE ='''"""The classes of this package a flattened object is read into."""

from __future__ import annotations

import importlib
import struct

# The qualified LabVIEW name of each class -> (its module, dotted from the package
# root, its class name).
CLASSES = $CLASSES


class FlattenedClassError(Exception):
    """LabVIEW error 1401, 1403 or 1527 (``code``) of reading a flattened class."""

    code = 1403


def _fail(code, message):
    error = FlattenedClassError(message)
    error.code = code
    return error


def _class_named(qualified):
    entry = CLASSES.get(qualified)
    if entry is None:
        return None
    return getattr(importlib.import_module('.' + entry[0], __package__), entry[1])


def _levels(cls):
    """The classes of ``cls``'s hierarchy that are LabVIEW classes, oldest first."""
    return [
        c for c in reversed(cls.__mro__) if '__lv_class_name__' in vars(c)
    ]


def flatten_class(value):
    """The flattened form of the class object ``value``: a LabVIEW Object (no class
    of this package) is level zero."""
    cls = type(value)
    chain = _levels(cls)
    if not chain:
        return struct.pack('>I', 0)
    section = b''.join(
        bytes([len(raw)]) + raw
        for raw in (part.encode('latin-1') for part in cls.__lv_class_name__.split(':'))
    ) + b'\\x00'
    section = bytes([len(section)]) + section
    section += b'\\x00' * (-len(section) % 4)
    versions = b''.join(
        struct.pack('>HHHH', *c.__lv_version__) for c in reversed(chain)
    )
    private = b''
    for c in chain:
        data = c.__lv_flatten__(value)
        private += struct.pack('>i', len(data)) + data
    return struct.pack('>I', len(chain)) + section + versions + private


def unflatten_class(data, pos, within=None):
    """``(object, position after it)`` for the flattened class object at ``pos`` of
    ``data``. ``within`` is the qualified name of the class the reader is typed
    for: the data must hold that class, a class derived from it or one it derives
    from."""
    (levels,) = struct.unpack_from('>I', data, pos)
    pos += 4
    if levels == 0:
        return object(), pos
    try:
        length = data[pos]
        content = data[pos + 1:pos + 1 + length]
        parts = []
        cursor = 0
        while content[cursor]:
            size = content[cursor]
            parts.append(content[cursor + 1:cursor + 1 + size].decode('latin-1'))
            cursor += 1 + size
        pos += 1 + length + (-(1 + length) % 4)
        versions = [
            struct.unpack_from('>HHHH', data, pos + 8 * k) for k in range(levels)
        ]
    except (IndexError, struct.error):
        raise _fail(1403, 'the flattened class has no valid name or version list')
    pos += 8 * levels
    qualified = ':'.join(parts)
    cls = _class_named(qualified)
    if cls is None:
        raise _fail(1527, 'the class ' + repr(qualified) + ' is not in the package')
    if within is not None:
        wanted = _class_named(within)
        if wanted is None or not (
            issubclass(cls, wanted) or issubclass(wanted, cls)
        ):
            raise _fail(
                1527,
                'the class ' + repr(qualified) + ' is not ' + repr(within)
                + ', or a class it derives from or that derives from it',
            )
    value = cls()
    chain = _levels(cls)
    if levels != len(chain) or (
        levels > 1 and not all(any(v) for v in versions)
    ):
        raise _fail(
            1403,
            'the flattened ' + qualified + ' has ' + str(levels)
            + ' levels of hierarchy and the class has ' + str(len(chain)),
        )
    if any(v > c.__lv_version__ for v, c in zip(versions, reversed(chain))):
        raise _fail(
            1401,
            'the data of ' + qualified + ' is of a newer version than the class has',
        )
    if levels == 1 and not any(versions[0]):
        return value, pos
    for c in chain:
        try:
            (size,) = struct.unpack_from('>i', data, pos)
            pos += 4
            if size:
                end = c.__lv_unflatten__(value, data, pos)
                if end != pos + size:
                    raise struct.error('the private data has another size')
                pos = end
        except (IndexError, struct.error):
            raise _fail(
                1403, 'the private data of ' + c.__lv_class_name__ + ' does not read'
            )
    return value, pos
'''


def package_class_entries(
    graph: InMemoryVIGraph, module_of: Callable[[str], str | None]
) -> dict[str, tuple[str, str]]:
    """The classes the package has: every loaded class that has a module
    (``module_of`` gives its dotted path from the package root, or None), by its
    qualified name."""
    entries: dict[str, tuple[str, str]] = {}
    for key in graph.list_class_keys():
        module = module_of(key)
        if module is not None:
            entries[graph.vi_display_name(key)] = (module, python_class_name(key))
    return entries


def build_classes_module(classes: Mapping[str, tuple[str, str]]) -> str:
    """The source of ``lv_classes.py`` for the classes of a package: each qualified
    class name -> (its module, dotted from the package root, its class name),
    written sorted by name."""
    ordered = {name: classes[name] for name in sorted(classes)}
    body = "{\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in ordered.items()) + "}"
    return _CLASSES_TEMPLATE.replace("$CLASSES", body)

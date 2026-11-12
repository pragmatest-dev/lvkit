"""The module a generated package holds for the value of a LabVIEW variant.

A variant is one value of three parts: the data, its exact LabVIEW type and its
named attributes (each a variant). The classes are plain data, the same standing
as the ``LabVIEWError`` of an error wire. They are written once into the package
root as ``variants.py`` and imported from there, so a value made in one module
is compared, stored and read in another and a generated package imports nothing
from lvkit. The module also holds one ``LVType`` constant for each scalar type
whose ``LVType`` is fixed (``I32``, ``DBL``, ``String``, ...), written from the
same expression a generated module writes inline for any other type, so the two
cannot differ. ``variants.py`` imports nothing from the package, so importing it
at module level can never make an import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

from lvkit import type_string
from lvkit.conversion_gap import ConversionGap
from lvkit.lv_type_codes import type_descriptor_code
from lvkit.models import LVType, LVTypeKind

VARIANTS_MODULE_NAME = "variants"
VARIANT_NAME = "Variant"
LV_TYPE_NAME = "LVType"

# A relative import of the package's ``variants`` module (``from .variants import
# ...``, with more dots in a subpackage).
_VARIANTS_IMPORT = re.compile(rf"^from \.+{VARIANTS_MODULE_NAME} import ")


def imports_variants(statement: str) -> bool:
    """Whether ``statement`` imports from the package's ``variants`` module."""
    return _VARIANTS_IMPORT.match(statement) is not None


def lv_type_code_and_name(lv_type: LVType) -> tuple[int, str]:
    """The type-descriptor code of ``lv_type`` and LabVIEW's name for it (the
    typedef or class it names, else its descriptor's: ``I32``, ``TF``,
    ``String``, ``Path``, ...); a gap for a type with no code."""
    code = type_descriptor_code(lv_type)
    if code is None:
        raise ConversionGap(
            f"The type {lv_type.underlying_type!r} has no LabVIEW type descriptor "
            "code, so a variant of it cannot record its type"
        )
    return code, lv_type.type_ref() or lv_type.type_descriptor()


def _inline_expression(lv_type: LVType) -> str:
    """``LVType(code, name, type string)`` for ``lv_type``: its type-descriptor
    code, its name and its type string where it has one."""
    code, name = lv_type_code_and_name(lv_type)
    try:
        encoded: tuple[int, ...] | None = tuple(type_string.encode(lv_type))
    except type_string.TypeStringError:
        encoded = None
    return f"{LV_TYPE_NAME}({code}, {name!r}, {encoded!r})"


# The scalar types whose ``LVType`` is fixed, by the type-descriptor name of each.
_SCALAR_TYPE_NAMES = (
    "NumInt8",
    "NumInt16",
    "NumInt32",
    "NumInt64",
    "NumUInt8",
    "NumUInt16",
    "NumUInt32",
    "NumUInt64",
    "NumFloat32",
    "NumFloat64",
    "NumFloatExt",
    "NumComplex64",
    "NumComplex128",
    "NumComplexExt",
    "Boolean",
    "String",
    "Path",
)
# A constant is named by its LabVIEW type name, except where generated code
# already uses that name (``Path`` is ``pathlib.Path``).
_CONSTANT_SPELLING = {"Path": "LVPath"}


def _scalar(name: str) -> LVType:
    return LVType(LVTypeKind.PRIMITIVE, underlying_type=name)


def _constant_name(lv_type: LVType) -> str:
    descriptor = lv_type.type_descriptor()
    return _CONSTANT_SPELLING.get(descriptor, descriptor)


# Each scalar constant's name by the inline expression it stands for.
_CONSTANTS: dict[str, str] = {
    _inline_expression(lv_type): _constant_name(lv_type)
    for lv_type in map(_scalar, _SCALAR_TYPE_NAMES)
}


class TypeExpression(NamedTuple):
    """The expression of an ``LVType`` and the names of ``variants`` it reads."""

    text: str
    names: tuple[str, ...]


def lv_type_expression(lv_type: LVType) -> TypeExpression:
    """The expression of the package's ``LVType`` for ``lv_type``: the name of the
    constant of ``variants`` that is that type, else ``LVType(...)`` written out."""
    inline = _inline_expression(lv_type)
    constant = _CONSTANTS.get(inline)
    if constant is not None:
        return TypeExpression(constant, (constant,))
    return TypeExpression(inline, (LV_TYPE_NAME,))


def variants_import(prefix: str, names: Iterable[str]) -> str:
    """``from <prefix>variants import <names>``: ``Variant`` and the given names,
    each once, in order."""
    unique = sorted({VARIANT_NAME, *names})
    return f"from {prefix}{VARIANTS_MODULE_NAME} import {', '.join(unique)}"


_CLASSES_SOURCE = '''"""The value of a LabVIEW variant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LVType:
    """A LabVIEW type: the type-descriptor ``code``, its ``name`` for messages
    and, where it has one, the bytes of its type string."""

    code: int
    name: str
    type_string: tuple[int, ...] | None = None


@dataclass
class Variant:
    """A variant: the data, its type (None for an empty variant) and its named
    attributes, each a variant."""

    value: Any = None
    type: LVType | None = None
    attributes: dict[str, Variant] = field(default_factory=dict)

    def __copy__(self) -> Variant:
        return Variant(self.value, self.type, dict(self.attributes))
'''


def _module_source() -> str:
    lines = [f"{name} = {inline}" for inline, name in _CONSTANTS.items()]
    return _CLASSES_SOURCE + "\n\n" + "\n".join(lines) + "\n"


VARIANTS_MODULE_SOURCE = _module_source()

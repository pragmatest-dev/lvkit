"""The module a generated package holds for the value of a LabVIEW variant.

A variant is one value of three parts: the data, its exact LabVIEW type and its
named attributes (each a variant). The classes are plain data, the same standing
as the ``LabVIEWError`` of an error wire. They are written once into the package
root as ``variants.py`` and imported from there, so a value made in one module
is compared, stored and read in another and a generated package imports nothing
from lvkit. The module also holds one ``LVType`` constant for each scalar type
whose ``LVType`` is fixed (``LV_I32``, ``LV_DBL``, ``LV_String``, ...) and one for
the error cluster (``LV_Error``), written from the same expression a generated
module writes inline for any other type, so the two cannot differ.
``variants.py`` imports nothing from the package, so importing it at module level
can never make an import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

from lvkit import type_string
from lvkit.conversion_gap import ConversionGap
from lvkit.lv_type_codes import type_descriptor_code
from lvkit.models import ClusterField, LVType, LVTypeKind

from .flat_reader import flat_reader_source
from .flat_writer import flat_writer_source
from .type_reader import type_reader_source

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


def _inline_expression(lv_type: LVType, strict_typedef: bool = False) -> str:
    """``LVType(code, name, type string)`` for ``lv_type``: its type-descriptor
    code, its name and its type string where it has one."""
    code, name = lv_type_code_and_name(lv_type)
    try:
        encoded: tuple[int, ...] | None = tuple(type_string.encode(lv_type))
    except type_string.TypeStringError:
        encoded = None
    return (
        f"{LV_TYPE_NAME}({code}, {name!r}, {encoded!r}"
        f"{_refnum_arguments(lv_type)}{_typedef_argument(lv_type, strict_typedef)})"
    )


def _typedef_argument(lv_type: LVType, strict_typedef: bool) -> str:
    """The keyword arguments of the type of a type definition's instance that
    record what its code does not: that it is one, the type definition's name
    (``typedef_name``, as the file records it: library, then the ``.ctl``) and
    whether the definition is a strict one (``strict_typedef``)."""
    if not lv_type.typedef_name:
        return ""
    text = f", typedef_name={lv_type.typedef_name!r}"
    if strict_typedef:
        text += ", strict_typedef=True"
    return text


def _refnum_arguments(lv_type: LVType) -> str:
    """The keyword arguments of a refnum's ``LVType`` that record what a type code
    alone does not: its kind (``refnum_kind``, a ``REFNUM_TYPE`` name), the VI
    Server class of a generic reference (``server_class``) and the strict type of
    a typed refnum (``strict``, the type of what it refers to: a Queue's element).
    A strict type that has no type-descriptor code is recorded by its name alone."""
    if lv_type.underlying_type != "Refnum" or lv_type.ref_type is None:
        return ""
    text = f", refnum_kind={lv_type.ref_type!r}"
    if lv_type.server_class is not None:
        text += f", server_class={lv_type.server_class.value!r}"
    element = lv_type.element_type
    if element is not None:
        try:
            strict = _inline_expression(element)
        except ConversionGap:
            strict = f"{LV_TYPE_NAME}(0, {element.type_descriptor()!r})"
        text += f", strict={strict}"
    return text


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


# LabVIEW's error cluster: the three fields every error wire has, in order.
def _error_cluster() -> LVType:
    return LVType(
        LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField("status", _scalar("Boolean")),
            ClusterField("code", _scalar("NumInt32")),
            ClusterField("source", _scalar("String")),
        ],
    )


def _scalar(name: str) -> LVType:
    return LVType(LVTypeKind.PRIMITIVE, underlying_type=name)


# A constant is named ``LV_`` and its LabVIEW type name. A class or typedef is
# named from its file by ``type_names.derive_python_name``, which never writes an
# underscore inside a name, so no generated class can take a constant's name.
_CONSTANT_PREFIX = "LV_"


def _constant_name(lv_type: LVType) -> str:
    return f"{_CONSTANT_PREFIX}{lv_type.type_descriptor()}"


# Each constant's name by the inline expression it stands for: one for each
# scalar type and one for the error cluster.
_CONSTANTS: dict[str, str] = {
    _inline_expression(lv_type): _constant_name(lv_type)
    for lv_type in (*map(_scalar, _SCALAR_TYPE_NAMES), _error_cluster())
}


class TypeExpression(NamedTuple):
    """The expression of an ``LVType`` and the names of ``variants`` it reads."""

    text: str
    names: tuple[str, ...]


def lv_type_expression(
    lv_type: LVType, strict_typedef: bool = False
) -> TypeExpression:
    """The expression of the package's ``LVType`` for ``lv_type``: the name of the
    constant of ``variants`` that is that type, else ``LVType(...)`` written out.
    ``strict_typedef`` records that its type definition is a strict one."""
    inline = _inline_expression(lv_type, strict_typedef)
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

import math
import struct
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LVType:
    """A LabVIEW type: the type-descriptor ``code``, its ``name`` for messages
    and, where it has one, the bytes of its type string. A refnum also records
    what its code does not: its kind (``refnum_kind``, ``Queue``, ``LVObjCtl``,
    ...), the VI Server class of a generic reference (``server_class``, ``VI`` or
    ``Application``) and the type of what a typed refnum refers to (``strict``).
    An instance of a type definition records the definition's name
    (``typedef_name``) and whether that definition is a strict one
    (``strict_typedef``); every other type has none."""

    code: int
    name: str
    type_string: tuple[int, ...] | None = None
    refnum_kind: str | None = None
    server_class: str | None = None
    strict: LVType | None = None
    typedef_name: str | None = None
    strict_typedef: bool = False


@dataclass
class Variant:
    """A variant: the data, its type (None for an empty variant), its named
    attributes, each a variant, and its ``name``, the label of the control or
    indicator the data was made from ("" when it came from no labelled data). The
    name is not part of what two variants are equal by."""

    value: Any = None
    type: LVType | None = None
    attributes: dict[str, Variant] = field(default_factory=dict)
    name: str = field(default="", compare=False)

    def __copy__(self) -> Variant:
        return Variant(self.value, self.type, dict(self.attributes), self.name)
'''


def _scalar_constants_by_code() -> dict[int, str]:
    """The constant of each scalar type that has a type string, by its code."""
    constants: dict[int, str] = {}
    for lv_type in map(_scalar, _SCALAR_TYPE_NAMES):
        code, _ = lv_type_code_and_name(lv_type)
        try:
            type_string.encode(lv_type)
        except type_string.TypeStringError:
            continue
        constants[code] = _constant_name(lv_type)
    return constants


def _module_source() -> str:
    lines = [f"{name} = {inline}" for inline, name in _CONSTANTS.items()]
    return (
        _CLASSES_SOURCE
        + "\n\n"
        + "\n".join(lines)
        + "\n"
        + type_reader_source(_scalar_constants_by_code())
        + flat_reader_source()
        + flat_writer_source()
    )


VARIANTS_MODULE_SOURCE = _module_source()

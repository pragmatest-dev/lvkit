"""A cluster or array constant written as a Python expression, from its type and
its decoded value (``ConstantNode.structured_value``).

Each part is written from its own type: a cluster as a tuple (anonymous) or a
``SimpleNamespace`` (named), an array as a list, a class object as a new object of
its class, a Variant as the value it holds (``None`` when empty), an error cluster
as the value of an error wire, any other value through the scalar formatting a
constant of that type goes through. A timestamp is a ``hightime.datetime`` and a
waveform a ``nitypes`` waveform (``waveform_values``). A part with no Python
value is a gap."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import hightime

from lvkit.conversion_gap import ConversionGap
from lvkit.lv_type_codes import (
    is_boolean_type_name,
    is_numeric_type_name,
    is_string_type_name,
    is_variant_type_name,
)
from lvkit.measure_data import measure_data_value_type
from lvkit.models import (
    LVType,
    LVTypeKind,
    VariantData,
    _is_error_cluster,
    unique_names,
)
from lvkit.timestamp_value import TIMESTAMP_FLAVOR, timestamp_expression

from .ast_utils import (
    INTEGER_TYPE_NAMES,
    REAL_FLOAT_TYPE_NAMES,
    field_attribute_names,
)
from .variant_values import type_expression
from .variants_module import VARIANT_NAME
from .waveform_values import IMPORTS as WAVEFORM_IMPORTS
from .waveform_values import build_expression, waveform_form

if TYPE_CHECKING:
    from .context import CodeGenContext

_TIMESTAMP = LVType(
    LVTypeKind.PRIMITIVE, underlying_type="MeasureData", measure_flavor=TIMESTAMP_FLAVOR
)
_DOUBLE = LVType(LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")

# Types written as ``None`` whatever the decoded value.
_NO_VALUE_TYPES = frozenset({"Void", "Picture"})
# Types whose decoded value is the display text the scalar formatting reads.
_SCALAR_TYPES = frozenset({"Boolean", "String", "Tag", "Path", "Refnum"})


def constant_value_expression(
    ctx: CodeGenContext, lv_type: LVType, value: object, where: str = "the constant"
) -> str:
    """The Python expression for ``value``, a decoded value of type ``lv_type``.
    ``where`` names the part of the constant being written, for a gap."""
    underlying = lv_type.underlying_type or ""
    if lv_type.ref_type == "UDClassInst":
        return ctx.class_object_expression(lv_type)
    if underlying in _NO_VALUE_TYPES:
        return "None"
    if is_variant_type_name(underlying):
        if not isinstance(value, VariantData):
            raise ConversionGap(_not_read(where, lv_type))
        return _variant(ctx, value, where)
    if lv_type.is_timestamp:
        if not isinstance(value, hightime.datetime):
            raise ConversionGap(_not_read(where, lv_type))
        return timestamp_expression(value)
    if underlying == "MeasureData" and lv_type.measure_flavor:
        return _waveform(ctx, lv_type.measure_flavor, value, where)
    if lv_type.kind == LVTypeKind.CLUSTER and lv_type.fields is not None:
        if not isinstance(value, dict):
            raise ConversionGap(_not_read(where, lv_type))
        if _is_error_cluster(lv_type):
            return _error_value(ctx, lv_type, value, where)
        return _cluster(ctx, lv_type, value, where)
    if lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
        return _array(ctx, lv_type.element_type, lv_type.dimensions or 1, value, where)
    if underlying in ("Set", "Map") and isinstance(value, list):
        return _collection(ctx, lv_type, value, where)
    if (
        lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING)
        or underlying in _SCALAR_TYPES
        or is_numeric_type_name(underlying)
    ):
        if not isinstance(value, str):
            raise ConversionGap(_not_read(where, lv_type))
        return ctx.scalar_constant_expression(lv_type, value)
    raise ConversionGap(
        f"{where[0].upper()}{where[1:]} is a {underlying or lv_type.kind.value} "
        "value, which lvkit cannot write as Python yet"
    )


def _waveform(ctx: CodeGenContext, flavor: str, value: object, where: str) -> str:
    """A waveform constant: the ``nitypes`` waveform of its samples, start time,
    interval and attributes."""
    form = waveform_form(flavor)
    layout = measure_data_value_type(flavor)
    if not isinstance(value, dict) or layout is None or not layout.fields:
        raise ConversionGap(f"The value of {where} (a {flavor}) was not read")
    samples = next(f.type for f in layout.fields if f.name == "Y")
    assert samples is not None and samples.element_type is not None
    attributes = value.get("attributes")
    ctx.imports.update(WAVEFORM_IMPORTS)
    return build_expression(
        form,
        _array(ctx, samples.element_type, 1, value.get("Y"), where),
        constant_value_expression(
            ctx, _TIMESTAMP, value.get("t0"), f"the start time of {where}"
        ),
        constant_value_expression(
            ctx, _DOUBLE, value.get("dt"), f"the interval of {where}"
        ),
        _extended_properties(ctx, attributes, where)
        if isinstance(attributes, VariantData)
        else None,
    )


def _extended_properties(
    ctx: CodeGenContext, variant: VariantData, where: str
) -> str | None:
    """The dict of extended properties from the attributes of a waveform's
    Variant; nitypes holds only a Boolean, a number or a string for each, so any
    other attribute is a gap."""
    items = []
    for name, attribute in variant.attributes:
        held = attribute.value_type
        underlying = held.underlying_type if held is not None else None
        if held is None or not (
            is_boolean_type_name(underlying)
            or is_string_type_name(underlying)
            or underlying in INTEGER_TYPE_NAMES
            or underlying in REAL_FLOAT_TYPE_NAMES
        ):
            raise ConversionGap(
                f"The attribute {name!r} of {where} holds "
                f"{underlying or 'no value'}: nitypes keeps only a Boolean, a "
                "number or a string for a waveform attribute"
            )
        items.append(
            f"{name!r}: "
            + constant_value_expression(
                ctx, held, attribute.value, f"the attribute {name!r} of {where}"
            )
        )
    return "{" + ", ".join(items) + "}" if items else None


def _collection(
    ctx: CodeGenContext, lv_type: LVType, entries: list[object], where: str
) -> str:
    """A Set constant as a ``set`` and a Map constant as a ``dict``: each entry is
    an element (a Set) or a ``[key, value]`` pair (a Map)."""
    key_type = lv_type.element_type
    if key_type is None:
        raise ConversionGap(f"The element type of {where} is not known")
    if lv_type.underlying_type == "Set":
        if not entries:
            return "set()"
        elements = [
            constant_value_expression(ctx, key_type, entry, f"an element of {where}")
            for entry in entries
        ]
        return "{" + ", ".join(elements) + "}"
    value_type = lv_type.value_type
    if value_type is None:
        raise ConversionGap(f"The value type of {where} is not known")
    items = []
    for entry in entries:
        assert isinstance(entry, list)
        key = constant_value_expression(ctx, key_type, entry[0], f"a key of {where}")
        value = constant_value_expression(
            ctx, value_type, entry[1], f"a value of {where}"
        )
        items.append(f"{key}: {value}")
    return "{" + ", ".join(items) + "}"


def _variant(ctx: CodeGenContext, value: VariantData, where: str) -> str:
    """A Variant constant: ``Variant()`` when empty, else the ``Variant`` of the
    value it holds, its type and its attributes, each written as the Variant it
    is."""
    if value.value_type is None and not value.attributes:
        ctx.import_variant()
        return f"{VARIANT_NAME}()"
    held = "None"
    type_expr = "None"
    if value.value_type is not None:
        held = constant_value_expression(ctx, value.value_type, value.value, where)
        type_expr = type_expression(ctx, value.value_type)
    items = []
    for name, attribute in value.attributes:
        part = f"the attribute {name!r} of {where}"
        items.append(f"{name!r}: {_variant(ctx, attribute, part)}")
    ctx.import_variant()
    return f"{VARIANT_NAME}({held}, {type_expr}, {{{', '.join(items)}}})"


def _not_read(where: str, lv_type: LVType) -> str:
    return (
        f"The value of {where} ({lv_type.underlying_type or lv_type.kind.value}) "
        "was not read from the VI"
    )


def _fields(
    ctx: CodeGenContext, lv_type: LVType, value: dict[str, object], where: str
) -> list[str]:
    """The expression for each field of a cluster, in field order."""
    assert lv_type.fields is not None
    keys = unique_names(f.name for f in lv_type.fields)
    expressions = []
    for f, key in zip(lv_type.fields, keys):
        part = f"the field {f.name!r} of {where}"
        if f.type is None:
            raise ConversionGap(f"The type of {part} is not known")
        expressions.append(constant_value_expression(ctx, f.type, value.get(key), part))
    return expressions


def _cluster(
    ctx: CodeGenContext, lv_type: LVType, value: dict[str, object], where: str
) -> str:
    """A cluster: a named or typedef cluster as an object with an attribute for
    each field (Bundle and Unbundle By Name read and write ``.field``), an
    anonymous cluster as a positional tuple."""
    assert lv_type.fields is not None
    expressions = _fields(ctx, lv_type, value, where)
    if not lv_type.typedef_name and not lv_type.classname:
        trailing = "," if len(expressions) == 1 else ""
        return f"({', '.join(expressions)}{trailing})"
    attributes = field_attribute_names(f.name for f in lv_type.fields)
    arguments = ", ".join(
        f"{attribute}={expression}"
        for attribute, expression in zip(attributes, expressions, strict=True)
    )
    return f"SimpleNamespace({arguments})"


def _error_value(
    ctx: CodeGenContext, lv_type: LVType, value: dict[str, object], where: str
) -> str:
    """An error cluster: the value of an error wire."""
    parts = tuple(ast.literal_eval(e) for e in _fields(ctx, lv_type, value, where))
    return ctx.error_value_expression(parts, where)


def _array(
    ctx: CodeGenContext,
    element_type: LVType,
    dimensions: int,
    value: object,
    where: str,
) -> str:
    """An array as a list, one nested list per dimension."""
    if not isinstance(value, list):
        raise ConversionGap(f"The value of {where} (an array) was not read from the VI")
    if dimensions > 1:
        items = [_array(ctx, element_type, dimensions - 1, v, where) for v in value]
    else:
        items = [
            constant_value_expression(ctx, element_type, v, f"an element of {where}")
            for v in value
        ]
    return "[" + ", ".join(items) + "]"

"""The converter to a LabVIEW type that a generated package's ``variants`` module
holds, which Variant To Data uses for a cluster or an array.

NI, Variant To Data: ``data`` "is the variant changed to the data type specified
by type", the whole type (https://docs-be.ni.com/api/bundle/labview-api-ref/page/functions/variant-to-data.html).
A variant that holds a cluster of variants (a
cluster of Variants by its own design, such as the one JDP's Array of Variants to
Cluster.vi builds) therefore comes out as the typed cluster, each member
converted to its field's type; an array comes out with each element converted to
the element's type. The converter does that with the type's own type string
(``type_reader``) and the standard library, so it reads only what the type
records.
"""

from __future__ import annotations

from lvkit.lv_type_codes import TD_FULL_TYPE

_CONVERTER_TEMPLATE = '''

# The converter of a value to a LabVIEW type: Variant To Data's conversion of a
# cluster or an array down to its members (NI, Variant To Data: the data comes out
# as the type wired to the type input).
_VARIANT_CODE = $VARIANT
_NUMBER_CODES = tuple(c for c in _NUMBER_NAMES if c not in _ENUM_CODES)
_INCOMPATIBLE = (
    "The data type of the variant is not compatible with the data type wired to "
    "the type input."
)


def _bare_variant(value: Any) -> Variant:
    """A value that is not a variant, made one where its Python type is its
    LabVIEW type (a Boolean, a double or a string); any other has no recorded
    type to make one of."""
    if isinstance(value, bool):
        return Variant(value, LV_TF)
    if isinstance(value, float):
        return Variant(value, LV_DBL)
    if isinstance(value, str):
        return Variant(value, LV_String)
    raise TypeError(_INCOMPATIBLE)


def _number(value: Any, info: TypeInfo) -> Any:
    name = info.number or ""
    if info.kind == "enum" or "Int" in name:
        return round(value)
    return complex(value) if "Complex" in name else float(value)


def convert_to_type(value: Any, lv_type: LVType) -> Any:
    """``value`` as a value of ``lv_type``, as Variant To Data converts a variant
    to the type wired to its ``type`` input. A ``Variant`` holding the value is
    read as its value first, and its type must be one ``lv_type`` takes (a number
    for a number type, the same code for any other); an empty variant takes none.
    A cluster is converted to a tuple of its members, each converted to the field's
    type; an array to a list of its elements converted to the element's type; a
    number as LabVIEW coerces it (an integer type takes it rounded to the nearest
    integer, ties to even); a Variant type takes the value as a ``Variant``.
    Anything that does not fit raises the ``TypeError`` the node maps to error 91.
    """
    if lv_type.code == _VARIANT_CODE:
        return value if isinstance(value, Variant) else _bare_variant(value)
    if isinstance(value, Variant):
        accepted = (
            _NUMBER_CODES + (lv_type.code,)
            if _kind_of(lv_type.code) in ("number", "enum")
            else (lv_type.code,)
        )
        if value.type is None or value.type.code not in accepted:
            raise TypeError(_INCOMPATIBLE)
        value = value.value
    info = read_type(lv_type) if lv_type.type_string is not None else None
    if info is None or info.kind not in ("cluster", "array", "number", "enum"):
        return value
    if info.kind in ("number", "enum"):
        return _number(value, info)
    if info.kind == "cluster":
        if not isinstance(value, (tuple, list)) or len(value) != len(info.fields):
            raise TypeError(_INCOMPATIBLE)
        return tuple(convert_to_type(m, f) for m, f in zip(value, info.fields))
    if not isinstance(value, (tuple, list)) or info.element is None:
        raise TypeError(_INCOMPATIBLE)
    return _convert_rows(value, info.element, len(info.dimensions))


def _convert_rows(rows: Any, element: LVType, dimensions: int) -> list:
    """The ``dimensions`` nested lists ``rows`` hold, each element converted to
    ``element``."""
    if dimensions == 1:
        return [convert_to_type(item, element) for item in rows]
    if not isinstance(rows, (tuple, list)):
        raise TypeError(_INCOMPATIBLE)
    return [_convert_rows(row, element, dimensions - 1) for row in rows]
'''


def type_converter_source() -> str:
    """The converter's source, the code of a Variant written from the table the
    descriptors use."""
    return _CONVERTER_TEMPLATE.replace("$VARIANT", repr(TD_FULL_TYPE.LVVariant.value))

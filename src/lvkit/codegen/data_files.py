"""Arrays too large to write into a generated module go to a data file beside it.

The VI stores such an array's elements flattened big-endian; the data file holds
those bytes unchanged and the module reads them back with ``lv.load_array``.
"""

from __future__ import annotations

import array

from lvkit.lv_type_codes import (
    FLOAT_BYTE_WIDTHS,
    INTEGER_BYTE_WIDTHS,
    SIGNED_INTEGER_TYPE_NAMES,
    TD_FULL_TYPE,
)
from lvkit.models import LVType, LVTypeKind, UnwritableValue, ValuesNotKept

from .ast_utils import to_var_name
from .errors import CodeGenError, unwritable_value_error


def array_typecode(element: LVType | None) -> tuple[str, bool] | None:
    """The ``array`` module's type code for an element type, and whether the
    elements are Booleans; None for an element type with no such code. The code is
    the one whose item size matches the type's flattened width."""
    if element is None or element.kind != LVTypeKind.PRIMITIVE:
        return None
    name = element.underlying_type or ""
    if name == TD_FULL_TYPE.Boolean.name:
        return "B", True
    if name in FLOAT_BYTE_WIDTHS:
        width = FLOAT_BYTE_WIDTHS[name]
        return next(c for c in "fd" if array.array(c).itemsize == width), False
    if name in INTEGER_BYTE_WIDTHS:
        width = INTEGER_BYTE_WIDTHS[name]
        signed = "bhilq"
        code = next(c for c in signed if array.array(c).itemsize == width)
        return (code if name in SIGNED_INTEGER_TYPE_NAMES else code.upper()), False
    return None


def holds_array_whole(unwritable: UnwritableValue, lv_type: LVType | None) -> bool:
    """Whether ``unwritable`` is the whole value of ``lv_type`` (an array of that
    element type), not an array inside a cluster the value holds."""
    return (
        isinstance(unwritable, ValuesNotKept)
        and lv_type is not None
        and lv_type.kind == LVTypeKind.ARRAY
        and unwritable.element_type is lv_type.element_type
    )


def data_file_expression(
    *,
    what: str,
    name: str | None,
    uid: str,
    module_stem: str,
    unwritable: UnwritableValue | None,
    lv_type: LVType | None,
    data_files: dict[str, bytes] | None,
    whole_value: bool,
) -> str:
    """The expression that loads a large array from its data file, and the file
    registered in ``data_files``. Raises ``CodeGenError`` when the value is not
    one this can write: not a large array, an array inside a cluster, an element
    type with no ``array`` code, bytes that do not hold every element, or a call
    that has nowhere to put the file."""
    if unwritable is None:
        raise ValueError("data_file_expression needs an unwritable value")
    if not isinstance(unwritable, ValuesNotKept) or not whole_value:
        raise unwritable_value_error(what, name, unwritable)
    coded = array_typecode(unwritable.element_type)
    if coded is None:
        raise CodeGenError(
            f"The {what} {name!r} holds {unwritable.reason()}; its element type "
            "has no fixed width to write to a data file."
        )
    typecode, as_bool = coded
    width = array.array(typecode).itemsize
    if len(unwritable.element_bytes) != width * unwritable.element_count:
        raise CodeGenError(
            f"The {what} {name!r} holds {unwritable.reason()}, but the VI stores "
            "fewer bytes than that many elements take."
        )
    if data_files is None:
        raise CodeGenError(
            f"The {what} {name!r} holds {unwritable.reason()}; it is written to a "
            "data file beside the generated module, and this call returns only the "
            "module's text. Generate with `lvkit generate` to get the file."
        )
    file_name = f"{module_stem}.{to_var_name(name or 'array')}_{uid}.bin"
    data_files[file_name] = unwritable.element_bytes
    sizes = ", ".join(str(size) for size in unwritable.dimension_sizes)
    trailing = "," if len(unwritable.dimension_sizes) == 1 else ""
    extra = ", as_bool=True" if as_bool else ""
    return (
        f"_lv.load_array(__file__, {file_name!r}, {typecode!r}, "
        f"({sizes}{trailing}){extra})"
    )

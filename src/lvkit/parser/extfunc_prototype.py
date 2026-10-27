"""The call a Call Library Function Node makes: library, function, calling
convention, and how each parameter is passed.

The node's ``<funcTD>`` is a Cluster whose fields are the C prototype, one per
row (the return value first). Each field's type descriptor says how that
parameter reaches the function: a number by value, ``PtrTo`` a number, a
``CString`` (C string pointer), an ``ArrayDataPtr`` to numbers, a LabVIEW string
or array handle, or a pointer to the wired value (``Adapt to Type``, ``Ptr``).
Any other descriptor is kept as an unsupported parameter with the reason.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum, IntEnum

from .type_mapping import TypeMap, flat_type_descs


class ParamKind(str, Enum):
    """How a parameter is passed to the function."""

    VALUE = "value"  # a number by value
    POINTER = "pointer"  # a pointer to a number; the function may write it
    C_STRING = "c_string"  # a NUL-terminated string pointer
    ARRAY_DATA = "array_data"  # a pointer to the elements of a 1-D numeric array
    # Adapt to Type: a scalar is passed as a pointer to it (NI: "Scalars are passed
    # by reference"), so its C type is that of the wire type, known only on the
    # graph (see ``codegen.nodes.ext_func``). A bare pointer (``Ptr``) points at
    # the wired value the same way.
    ADAPT_TO_TYPE = "adapt_to_type"
    NO_VALUE = "no_value"  # a function with no return value
    STRING_HANDLE = "string_handle"  # a LabVIEW string handle
    STRING_HANDLE_POINTER = "string_handle_pointer"  # a pointer to a string handle
    ARRAY_HANDLE = "array_handle"  # a LabVIEW array handle
    UNSUPPORTED = "unsupported"


class ExtFuncThread(IntEnum):
    """The node's ``<threadInfo>`` (absent is 0): where the call runs. NI's
    dialog default is the UI thread; "Run in any thread" is the reentrant
    setting."""

    UI_THREAD = 0
    ANY_THREAD = 1


@dataclass(frozen=True)
class ExtFuncParam:
    """One prototype field: how it is passed and its C element type
    (``int32``, ``uint8``, ``float64``, ``intptr`` ...; None for a string or an
    unsupported parameter)."""

    kind: ParamKind
    c_type: str | None = None
    reason: str = ""  # why an UNSUPPORTED parameter is
    dimensions: int = 0  # an array handle's number of dimensions


# Parameter kinds the generated Python cannot pass: each carries its ``reason``.
UNPASSABLE_KINDS = frozenset({ParamKind.UNSUPPORTED})


@dataclass(frozen=True)
class ExtFuncCall:
    """A Call Library Function Node's call.

    ``calling_convention`` is ``stdcall`` or ``c``. ``library`` is None when the
    node records no library (its path comes from the ``path in`` terminal).
    ``row_terminal_ids`` is each row's (input, output) terminal id, the return
    value first; the return value has no input terminal (None)."""

    library: str | None
    function: str
    calling_convention: str
    path_in_terminal_id: str | None
    row_terminal_ids: tuple[tuple[str | None, str], ...]
    func_td: int | None = None
    thread: ExtFuncThread | None = None
    params: tuple[ExtFuncParam, ...] = field(default=())


NUMERIC_C_TYPES = {
    "NumInt8": "int8",
    "NumInt16": "int16",
    "NumInt32": "int32",
    "NumInt64": "int64",
    "NumUInt8": "uint8",
    "NumUInt16": "uint16",
    "NumUInt32": "uint32",
    "NumUInt64": "uint64",
    "NumFloat32": "float32",
    "NumFloat64": "float64",
}
# A 64-bit integer flagged ``Prop1="1"`` is LabVIEW's pointer-sized integer.
_POINTER_SIZED = {"int64": "intptr", "uint64": "uintptr"}


# An element's size in bytes, for the element types of an array handle.
_ELEMENT_BYTES = {
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "uint32": 4,
    "float32": 4,
    "int64": 8,
    "uint64": 8,
    "float64": 8,
    "intptr": 8,
    "uintptr": 8,
}


def array_handle_problem(c_type: str) -> str | None:
    """Why an array handle of ``c_type`` elements cannot be passed, or None.

    NI: "LabVIEW stores arrays as handles ... that contain the size of each
    dimension of the array in 32-bit integers followed by the data ... a few
    bytes of padding might follow the dimension size so that the first element
    of the data is correctly aligned." The padding is not given, so an element
    wider than the 4-byte dimension size has no settled data offset."""
    if _ELEMENT_BYTES.get(c_type, 8) > 4:
        return (
            f"a LabVIEW array handle of {c_type} elements (NI does not say how "
            "many bytes of padding precede its data)"
        )
    return None


def _number(td: ET.Element | None) -> str | None:
    """The C element type of a numeric type descriptor, else None."""
    if td is None:
        return None
    c_type = NUMERIC_C_TYPES.get(td.get("Type", ""))
    if c_type is not None and td.get("Prop1") == "1":
        return _POINTER_SIZED.get(c_type, c_type)
    return c_type


def _child(td: ET.Element, flat: dict[int, ET.Element]) -> ET.Element | None:
    ref = td.find("TypeDesc[@TypeID]")
    return flat.get(int(ref.get("TypeID", "-1"))) if ref is not None else None


def _param(
    td: ET.Element, flat: dict[int, ET.Element], is_return: bool
) -> ExtFuncParam:
    kind_name = td.get("Type", "")
    number = _number(td)
    if number is not None:
        return ExtFuncParam(ParamKind.VALUE, number)
    if kind_name == "PtrTo" and (c_type := _number(_child(td, flat))):
        return ExtFuncParam(ParamKind.POINTER, c_type)
    if kind_name == "CString":
        return ExtFuncParam(ParamKind.C_STRING)
    if kind_name == "ArrayDataPtr" and (c_type := _number(_child(td, flat))):
        if len(td.findall("Dimension")) == 1:
            return ExtFuncParam(ParamKind.ARRAY_DATA, c_type)
        return ExtFuncParam(
            ParamKind.UNSUPPORTED, reason="an array data pointer of several dimensions"
        )
    if kind_name == "Void":
        if is_return:
            return ExtFuncParam(ParamKind.NO_VALUE)
        return ExtFuncParam(ParamKind.ADAPT_TO_TYPE)
    if kind_name == "String":
        return ExtFuncParam(ParamKind.STRING_HANDLE)
    if kind_name == "Ptr":
        return ExtFuncParam(ParamKind.ADAPT_TO_TYPE)
    if kind_name == "PtrTo" and (target := _child(td, flat)) is not None:
        if target.get("Type") == "String":
            return ExtFuncParam(ParamKind.STRING_HANDLE_POINTER)
    if kind_name == "Array":
        element = _number(_child(td, flat))
        dimensions = len(td.findall("Dimension"))
        if element is None or dimensions < 1:
            return ExtFuncParam(
                ParamKind.UNSUPPORTED, reason="a LabVIEW array handle of non-numbers"
            )
        if problem := array_handle_problem(element):
            return ExtFuncParam(ParamKind.UNSUPPORTED, reason=problem)
        return ExtFuncParam(ParamKind.ARRAY_HANDLE, element, dimensions=dimensions)
    return ExtFuncParam(ParamKind.UNSUPPORTED, reason=f"a {kind_name} parameter")


def extfunc_params(
    root: ET.Element, type_map: TypeMap, func_td: int
) -> tuple[ExtFuncParam, ...]:
    """The parameters of the prototype ``func_td`` names, in row order; empty
    when it is not a Cluster of the VCTP."""
    flat = flat_type_descs(root)
    flat_id = type_map.flat_ids.get(func_td)
    cluster = flat.get(flat_id) if flat_id is not None else None
    if cluster is None or cluster.get("Type") != "Cluster":
        return ()
    params: list[ExtFuncParam] = []
    for index, ref in enumerate(cluster.findall("TypeDesc[@TypeID]")):
        td = flat.get(int(ref.get("TypeID", "-1")))
        params.append(
            _param(td, flat, index == 0)
            if td is not None
            else ExtFuncParam(ParamKind.UNSUPPORTED, reason="an unreadable type")
        )
    return tuple(params)

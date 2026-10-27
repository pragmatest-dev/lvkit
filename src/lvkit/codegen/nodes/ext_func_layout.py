"""The C layout of a LabVIEW value passed to a Call Library Function Node, and the
Python that fills it and reads it back.

NI ("Configuring the Call Library Function Node", "How LabVIEW Stores Data in
Memory"): Adapt to Type passes a scalar by reference and a cluster as a pointer to
a structure; "scalar elements in arrays or clusters are in line", "clusters within
arrays are in line" and "strings and arrays within clusters are referenced by a
handle". A string handle points to a pointer to ``{int32 count; bytes}``. An array
handle points to a pointer to the dimension sizes (32-bit integers) followed by
the elements. A top-level string or array depends on the Data Format setting,
which no corpus node uses and so is not decoded.

Each layout becomes a ``ctypes.Structure`` defined once in the generated module
(``CtypesModule``); ``fill`` and ``read`` return the statements and expression that
move a Python value into and out of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from lvkit.models import LVType, LVTypeKind, unique_names
from lvkit.parser.extfunc_prototype import NUMERIC_C_TYPES, array_handle_problem

from ..ast_utils import to_var_name
from ..ctypes_module import CtypesModule

CTYPES_NAMES = {
    "int8": "c_int8",
    "int16": "c_int16",
    "int32": "c_int32",
    "int64": "c_int64",
    "uint8": "c_uint8",
    "uint16": "c_uint16",
    "uint32": "c_uint32",
    "uint64": "c_uint64",
    "float32": "c_float",
    "float64": "c_double",
    "intptr": "c_ssize_t",
    "uintptr": "c_size_t",
}
_UNIT_TYPES = {"UnitUInt8": "uint8", "UnitUInt16": "uint16", "UnitUInt32": "uint32"}
_REFNUM_TYPE = "uint32"  # NI: a refnum is a 32-bit magic cookie


def c_type(element: str) -> str:
    """The ``ctypes`` class of numeric type ``element`` (``bool`` is 8 bits)."""
    return f"ctypes.{CTYPES_NAMES.get(element, 'c_uint8')}"


@dataclass(frozen=True)
class Scalar:
    """A number, or a Boolean (``bool``: LabVIEW stores 8 bits)."""

    element: str


@dataclass(frozen=True)
class StringHandle:
    """A string behind a handle."""


@dataclass(frozen=True)
class ArrayHandle:
    """An array of numbers behind a handle."""

    element: str
    dimensions: int


@dataclass(frozen=True)
class Member:
    name: str
    layout: Layout


@dataclass(frozen=True)
class Cluster:
    """A C structure of ``members``. ``label`` names its structure class; a cluster
    with a typedef or class name is a ``SimpleNamespace`` in generated code and an
    anonymous one a tuple."""

    label: str
    named: bool
    members: tuple[Member, ...]


Layout = Scalar | StringHandle | ArrayHandle | Cluster


def class_label(name: str) -> str:
    """``name`` as a CamelCase class name."""
    words = to_var_name(name).split("_")
    return "".join(word.capitalize() for word in words if word) or "Cluster"


def _scalar(lv_type: LVType) -> str | None:
    if lv_type.underlying_type == "Boolean":
        return "bool"
    if lv_type.underlying_type == "Refnum":
        return _REFNUM_TYPE
    if lv_type.underlying_type in _UNIT_TYPES:
        return _UNIT_TYPES[lv_type.underlying_type]
    return NUMERIC_C_TYPES.get(lv_type.underlying_type or "")


def _member(lv_type: LVType | None, label: str) -> tuple[Layout | None, str]:
    """A cluster member's layout, or why it has none."""
    if lv_type is None:
        return None, "a member of unknown type"
    if (scalar := _scalar(lv_type)) is not None:
        return Scalar(scalar), ""
    if lv_type.kind == LVTypeKind.CLUSTER:
        return cluster_layout(lv_type, label)
    if lv_type.underlying_type == "String":
        return StringHandle(), ""
    if lv_type.kind == LVTypeKind.ARRAY:
        element = lv_type.element_type
        element_c = NUMERIC_C_TYPES.get(
            (element.underlying_type if element else "") or ""
        )
        if element_c is None:
            return None, "an array of something other than numbers"
        if problem := array_handle_problem(element_c):
            return None, problem
        return ArrayHandle(element_c, lv_type.dimensions or 1), ""
    return None, f"a {lv_type.underlying_type or lv_type.kind.value} member"


def cluster_layout(lv_type: LVType, label: str) -> tuple[Cluster | None, str]:
    """The structure of a cluster, or why it has none."""
    members = []
    names = unique_names(f.name for f in lv_type.fields or [])
    for field, name in zip(lv_type.fields or [], names):
        layout, why = _member(field.type, name)
        if layout is None:
            return None, f"a cluster with {why}"
        members.append(Member(to_var_name(name), layout))
    class_name = lv_type.python_class_name()
    return Cluster(
        class_name or class_label(label), bool(class_name), tuple(members)
    ), ""


def parameter_layout(lv_type: LVType | None, label: str) -> tuple[Layout | None, str]:
    """The layout a top-level Adapt to Type parameter of ``lv_type`` is passed by
    (as a pointer to it), or why it has none."""
    if lv_type is None:
        return None, "of unknown type"
    if (scalar := _scalar(lv_type)) is not None:
        return Scalar(scalar), ""
    if lv_type.kind == LVTypeKind.CLUSTER:
        return cluster_layout(lv_type, label)
    return None, (
        f"of type {lv_type.underlying_type or lv_type.kind.value} (its Data Format "
        "setting is not decoded: no corpus node has one)"
    )


# ------------------------------------------------------------- declarations


def string_struct(module: CtypesModule) -> str:
    """The structure behind a string handle."""
    return module.declare(
        "LStr",
        "_LStr",
        lambda n: (
            f"class {n}(ctypes.Structure):\n"
            "    _fields_ = [('cnt', ctypes.c_int32), ('str', ctypes.c_ubyte * 0)]\n"
        ),
    )


def array_struct(module: CtypesModule, layout: ArrayHandle) -> str:
    """The structure behind an array handle: the dimension sizes, then the elements."""
    dims = "".join(f"('dim{i}', ctypes.c_int32), " for i in range(layout.dimensions))
    preferred = f"_Array{layout.dimensions}{layout.element.capitalize()}"
    return module.declare(
        layout,
        preferred,
        lambda n: (
            f"class {n}(ctypes.Structure):\n"
            f"    _fields_ = [{dims}('elt', {c_type(layout.element)} * 0)]\n"
        ),
    )


def ctype_of(layout: Layout, module: CtypesModule) -> str:
    """The source of the ``ctypes`` type of a value of ``layout`` as it sits in a
    structure or behind a pointer, defining the structures it needs."""
    if isinstance(layout, Scalar):
        return c_type(layout.element)
    if isinstance(layout, StringHandle):
        return f"ctypes.POINTER(ctypes.POINTER({string_struct(module)}))"
    if isinstance(layout, ArrayHandle):
        return f"ctypes.POINTER(ctypes.POINTER({array_struct(module, layout)}))"
    return _cluster_struct(module, layout)


def _cluster_struct(module: CtypesModule, layout: Cluster) -> str:
    fields = [(m.name, ctype_of(m.layout, module)) for m in layout.members]
    listing = ", ".join(f"({name!r}, {ctype})" for name, ctype in fields)
    return module.declare(
        layout,
        f"_{layout.label}",
        lambda n: f"class {n}(ctypes.Structure):\n    _fields_ = [{listing}]\n",
    )


# ------------------------------------------------------------ fill and read


def _text(value: str) -> str:
    return f"{value}.encode('latin-1')"


def fill_handle(
    layout: StringHandle | ArrayHandle,
    value: str,
    target: str,
    prefix: str,
    module: CtypesModule,
) -> list[str]:
    """Statements that build the block of ``value`` and store a handle to it in
    ``target``. The block lives as long as the local ``{prefix}_block``."""
    if isinstance(layout, StringHandle):
        struct = string_struct(module)
        return [
            f"{prefix}_data = {_text(value)}",
            f"{prefix}_block = (ctypes.c_char * (ctypes.sizeof({struct})"
            f" + len({prefix}_data)))()",
            f"{prefix}_lstr = {struct}.from_buffer({prefix}_block)",
            f"{prefix}_lstr.cnt = len({prefix}_data)",
            f"ctypes.memmove(ctypes.addressof({prefix}_lstr.str), {prefix}_data,"
            f" len({prefix}_data))",
            f"{prefix}_cell = ctypes.pointer({prefix}_lstr)",
            f"{target} = ctypes.pointer({prefix}_cell)",
        ]
    struct = array_struct(module, layout)
    element = c_type(layout.element)
    loops = " ".join(
        f"for {prefix}_e{k} in {value if k == 0 else f'{prefix}_e{k - 1}'}"
        for k in range(layout.dimensions)
    )
    flat = f"[{prefix}_e{layout.dimensions - 1} {loops}]"
    sizes = []
    for k in range(layout.dimensions):
        deeper = value + "[0]" * k
        guard = " and ".join(value + "[0]" * j for j in range(k))
        sizes.append(f"len({deeper})" if k == 0 else f"len({deeper}) if {guard} else 0")
    lines = [f"{prefix}_flat = {flat}"]
    lines += [
        f"{prefix}_block = (ctypes.c_char * (ctypes.sizeof({struct})"
        f" + len({prefix}_flat) * ctypes.sizeof({element})))()",
        f"{prefix}_array = {struct}.from_buffer({prefix}_block)",
    ]
    lines += [f"{prefix}_array.dim{k} = {size}" for k, size in enumerate(sizes)]
    lines += [
        f"({element} * len({prefix}_flat)).from_address("
        f"ctypes.addressof({prefix}_array.elt))[:] = {prefix}_flat",
        f"{prefix}_cell = ctypes.pointer({prefix}_array)",
        f"{target} = ctypes.pointer({prefix}_cell)",
    ]
    return lines


def fill(
    layout: Layout, value: str, target: str, prefix: str, module: CtypesModule
) -> list[str]:
    """Statements that store ``value`` in ``target``, a field or cell of
    ``ctype_of(layout)`` (a cluster's structure already exists)."""
    if isinstance(layout, Scalar):
        return [f"{target} = {value}"]
    if isinstance(layout, Cluster):
        lines: list[str] = []
        for index, member in enumerate(layout.members):
            source = f"{value}.{member.name}" if layout.named else f"{value}[{index}]"
            lines += fill(
                member.layout,
                source,
                f"{target}.{member.name}",
                f"{prefix}_{member.name}",
                module,
            )
        return lines
    return fill_handle(layout, value, target, prefix, module)


def read_handle(
    layout: StringHandle | ArrayHandle, handle: str, prefix: str, module: CtypesModule
) -> tuple[list[str], str]:
    """Statements and the expression for the value behind ``handle`` (a NULL
    handle or block is empty)."""
    if isinstance(layout, StringHandle):
        block = f"{handle}.contents.contents"
        return [], (
            f"(ctypes.string_at(ctypes.addressof({block}.str), {block}.cnt)"
            f".decode('latin-1') if {handle} and {handle}.contents else '')"
        )
    array = f"{prefix}_array"
    dims = [f"{array}.dim{k}" for k in range(layout.dimensions)]
    count = " * ".join(dims)
    lines = [
        f"{array} = {handle}.contents.contents if {handle} and {handle}.contents"
        " else None",
        f"{prefix}_flat = list(({c_type(layout.element)} * ({count})).from_address("
        f"ctypes.addressof({array}.elt))) if {array} else []",
    ]
    return lines, f"{_nest(f'{prefix}_flat', dims, prefix)} if {array} else []"


def _nest(flat: str, dims: list[str], prefix: str) -> str:
    """The expression that slices ``flat`` (row-major) into lists of ``dims``."""

    def build(level: int, offset: str) -> str:
        if level == len(dims) - 1:
            return f"{flat}[{offset}:{offset} + {dims[level]}]" if level else flat
        stride = " * ".join(dims[level + 1 :])
        index = f"{prefix}_i{level}"
        term = f"{index} * {stride}"
        deeper = f"{offset} + {term}" if offset != "0" else term
        return f"[{build(level + 1, deeper)} for {index} in range({dims[level]})]"

    return build(0, "0")


def read(
    layout: Layout, source: str, prefix: str, module: CtypesModule
) -> tuple[list[str], str]:
    """Statements and the expression for the Python value of ``source``, a field or
    cell of ``ctype_of(layout)`` read after the call."""
    if isinstance(layout, Scalar):
        return [], f"bool({source})" if layout.element == "bool" else source
    if isinstance(layout, Cluster):
        lines: list[str] = []
        values: list[str] = []
        for member in layout.members:
            member_lines, value = read(
                member.layout,
                f"{source}.{member.name}",
                f"{prefix}_{member.name}",
                module,
            )
            lines += member_lines
            values.append(value)
        if layout.named:
            fields = ", ".join(f"{m.name}={v}" for m, v in zip(layout.members, values))
            return lines, f"SimpleNamespace({fields})"
        body = ", ".join(values)
        return lines, f"({body},)" if len(values) == 1 else f"({body})"
    return read_handle(layout, source, prefix, module)

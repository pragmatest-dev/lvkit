"""LabVIEW's flattened-data format written as ``struct`` code for one type.

NI's "Flattened Data" page: "LabVIEW stores flattened numeric data in big-endian
form (most significant byte first)"; "A Boolean value of TRUE is any nonzero
value. A Boolean value of FALSE is 00"; "Because strings and paths have variable
sizes, a flattened 32-bit integer that records the length in bytes precedes the
flattened form" (``ABC`` is ``0000 0003 4142 43``); "Flattened 32-bit integers
that record the size, in elements, of each of the dimensions of an array precede
the data for a flattened array. The slowest varying dimension is first" (a 2-D
array of six 8-bit integers, ``{{1,2,3},{4,5,6}}``, is ``0000 0002 0000 0003 0102
0304 0506``); "A flattened cluster is the concatenation, in cluster order, of the
flattened data of its elements" (a string ``ABC`` and a 16-bit integer 4 is
``0000 0003 4142 4300 04``).

A terminal's type becomes a ``Layout``; a layout writes the Python that flattens a
value of that type into ``bytes`` and reads one back. Numbers, Booleans and runs
of them are one ``struct.pack`` / ``struct.unpack_from`` call with the format
written out; a cluster or a multi-dimensional array gets one module-level
function of its own (``_flatten_<name>`` / ``_unflatten_<name>``), defined once
and called wherever the type recurs.

A string or array that is the whole value carries no size when ``prefix`` is
false: Type Cast flattens and unflattens without one (NI: casting an array of
four 16-bit integers to 32-bit integers gives two elements), while a string or
array inside a cluster or array always does.
"""

from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from collections.abc import Sequence as SequenceOf
from dataclasses import dataclass

from lvkit.lv_type_codes import is_string_type_name
from lvkit.models import LVType, LVTypeKind, unique_names

from .ast_utils import to_var_name
from .context import CodeGenContext
from .native_array import dimension_sizes

# The byte-order prefix of a ``struct`` format for LabVIEW's three byte orders:
# big-endian, the machine's, little-endian.
BYTE_ORDER_PREFIX = {0: ">", 1: "=", 2: "<"}

_INTEGER_CODES = {
    "NumInt8": ("b", "c_int8", 1),
    "NumUInt8": ("B", "c_uint8", 1),
    "NumInt16": ("h", "c_int16", 2),
    "NumUInt16": ("H", "c_uint16", 2),
    "NumInt32": ("i", "c_int32", 4),
    "NumUInt32": ("I", "c_uint32", 4),
    "NumInt64": ("q", "c_int64", 8),
    "NumUInt64": ("Q", "c_uint64", 8),
}
_FLOAT_CODES = {"NumFloat32": ("f", 4), "NumFloat64": ("d", 8)}
_COMPLEX_CODES = {"NumComplex64": ("ff", 8), "NumComplex128": ("dd", 16)}

# Refnum kinds that do not flatten as a 32-bit integer (NI's "Flattened Data":
# VISA, IVI and user-defined tags flatten as strings or several values).
_STRING_REFNUMS = frozenset({"VisaRef", "IVIRef", "UsrDefndTag", "UsrDefTagFlt"})


class UnsupportedLayout(Exception):
    """A type whose flattened form this does not write (extended-precision
    numbers, variants, paths, …); the message names it."""


@dataclass(frozen=True)
class Fixed:
    """Part of a flattened value that is a run of fixed-size numbers: one
    ``struct`` format and the expressions packed into it."""

    format: str
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class Raw:
    """Part of a flattened value that is a ``bytes`` expression."""

    expression: str


Part = Fixed | Raw


class Functions:
    """The module-level functions generated for one module, keyed by what they
    do, so a type that recurs is written once. Functions are added to the
    context's module-level statements."""

    def __init__(self, ctx: CodeGenContext, order: str) -> None:
        self.ctx = ctx
        self.order = order

    def named(self, kind: str, label: str, key: tuple, source_for) -> str:
        """The name of the function ``kind`` for ``key``, generating its source
        (``source_for(name)``) on first use."""
        registry = self.ctx.flat_functions
        full_key = (kind, self.order, key)
        if full_key in registry:
            return registry[full_key]
        taken = set(registry.values())
        base = f"_{kind}_{to_var_name(label)}"
        name, serial = base, 2
        while name in taken:
            name, serial = f"{base}_{serial}", serial + 1
        registry[full_key] = name
        source = source_for(name)
        self.ctx.formula_helpers.append(ast.parse(source).body[0])
        for module in ("struct", "ctypes"):
            if f"{module}." in source:
                self.ctx.add_import(f"import {module}")
        return name

    def reader(self, layout: Layout, prefix: bool) -> str:
        """The name of a module-level function ``(data, pos) -> (value, pos)``
        that reads ``layout`` from ``data``."""
        return layout.reader(self, prefix)


def render_parts(parts: SequenceOf[Part], order: str) -> str:
    """The ``bytes`` expression for ``parts``: adjacent numbers share one
    ``struct.pack``."""
    pieces: list[str] = []
    run: list[Fixed] = []

    def close() -> None:
        if run:
            fmt = order + "".join(p.format for p in run)
            args = ", ".join(a for p in run for a in p.arguments)
            pieces.append(f"struct.pack({fmt!r}, {args})")
            run.clear()

    for part in parts:
        if isinstance(part, Fixed):
            run.append(part)
        else:
            close()
            pieces.append(part.expression)
    close()
    return " + ".join(pieces) if pieces else "b''"


class Layout(ABC):
    """How one LabVIEW type is flattened."""

    @abstractmethod
    def fixed_size(self) -> int | None:
        """The flattened size in bytes, or None when it varies."""

    @abstractmethod
    def flatten(
        self, value: str, prefix: bool, depth: int, fns: Functions
    ) -> list[Part]:
        """The parts that flatten ``value``."""

    @abstractmethod
    def unflatten(
        self, target: str, prefix: bool, out: LineWriter, fns: Functions
    ) -> None:
        """Lines that read this type from ``data`` at ``pos`` into ``target``,
        advancing ``pos``."""

    def swap(
        self, value: str, chunk: int, width_min: int, depth: int, fns: Functions
    ) -> str:
        """``value`` with the ``chunk``-byte halves swapped in every integer of at
        least ``width_min`` bytes (other values are kept)."""
        return value

    def helper_label(self) -> str:
        return "value"

    def reader(self, fns: Functions, prefix: bool) -> str:
        """The name of the function that reads this type, generated on first use."""

        def source(name: str) -> str:
            out = LineWriter()
            self.unflatten("_value", prefix, out, fns)
            lines = "\n".join(out.lines)
            return f"def {name}(data, pos):\n{lines}\n    return _value, pos\n"

        return fns.named("unflatten", self.helper_label(), (self, prefix), source)


class LineWriter:
    """Source lines of a generated function body, with unique temporary names."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.indent = 1
        self._serial = 0

    def add(self, line: str) -> None:
        self.lines.append("    " * self.indent + line)

    def temporary(self, stem: str) -> str:
        self._serial += 1
        return f"_{stem}{self._serial}"


@dataclass(frozen=True)
class Integer(Layout):
    code: str
    ctype: str
    width: int

    def fixed_size(self) -> int | None:
        return self.width

    def flatten(self, value, prefix, depth, fns):
        return [Fixed(self.code, (f"ctypes.{self.ctype}({value}).value",))]

    def unflatten(self, target, prefix, out, fns):
        out.add(
            f"{target} = struct.unpack_from({fns.order + self.code!r}, data, pos)[0]"
        )
        out.add(f"pos += {self.width}")

    def swap(self, value, chunk, width_min, depth, fns):
        if self.width < width_min:
            return value
        signed = self.code.islower()
        name = fns.named(
            f"swap_{'bytes' if chunk == 1 else 'words'}",
            self.ctype,
            (self.ctype,),
            lambda n: (
                f"def {n}(value):\n"
                f"    raw = ctypes.{self.ctype}(value).value.to_bytes("
                f"{self.width}, 'big', signed={signed})\n"
                f"    swapped = b''.join(raw[i + {chunk}:i + {2 * chunk}] + "
                f"raw[i:i + {chunk}] for i in range(0, {self.width}, {2 * chunk}))\n"
                f"    return int.from_bytes(swapped, 'big', signed={signed})\n"
            ),
        )
        return f"{name}({value})"


@dataclass(frozen=True)
class Real(Layout):
    """A floating-point number, a Boolean or a complex number: the values
    ``struct`` packs as they are."""

    code: str
    width: int

    def fixed_size(self) -> int | None:
        return self.width

    def flatten(self, value, prefix, depth, fns):
        if len(self.code) == 2:
            return [Fixed(self.code, (f"{value}.real", f"{value}.imag"))]
        return [Fixed(self.code, (value,))]

    def unflatten(self, target, prefix, out, fns):
        read = f"struct.unpack_from({fns.order + self.code!r}, data, pos)"
        out.add(
            f"{target} = complex(*{read})"
            if len(self.code) == 2
            else f"{target} = {read}[0]"
        )
        out.add(f"pos += {self.width}")


@dataclass(frozen=True)
class Text(Layout):
    def fixed_size(self) -> int | None:
        return None

    def helper_label(self) -> str:
        return "string"

    def flatten(self, value, prefix, depth, fns):
        parts: list[Part] = []
        if prefix:
            parts.append(Fixed("i", (f"len({value})",)))
        parts.append(Raw(f"{value}.encode('latin-1')"))
        return parts

    def unflatten(self, target, prefix, out, fns):
        if prefix:
            count = out.temporary("count")
            out.add(f"{count} = struct.unpack_from({fns.order + 'i'!r}, data, pos)[0]")
            out.add("pos += 4")
            out.add(f"{target} = data[pos:pos + {count}].decode('latin-1')")
            out.add(f"pos += {count}")
        else:
            out.add(f"{target} = data[pos:].decode('latin-1')")
            out.add("pos = len(data)")


@dataclass(frozen=True)
class Sequence(Layout):
    element: Layout
    dimensions: int

    def fixed_size(self) -> int | None:
        return None

    def helper_label(self) -> str:
        return "array"

    def _leaves(self, value: str, depth: int) -> tuple[str, str]:
        """A generator source over every element in row-major order, and the
        name of the loop variable that holds one element."""
        names = [f"_a{depth}_{level}" for level in range(self.dimensions)]
        clauses = []
        source = value
        for name in names:
            clauses.append(f"for {name} in {source}")
            source = name
        return " ".join(clauses), names[-1]

    def flatten(self, value, prefix, depth, fns):
        parts: list[Part] = []
        if prefix or self.dimensions > 1:
            sizes = dimension_sizes(value, self.dimensions)
            parts.append(Fixed("i" * self.dimensions, tuple(sizes)))
        clauses, leaf = self._leaves(value, depth)
        element = self.element
        if (
            self.dimensions == 1
            and isinstance(element, (Integer, Real))
            and len(element.code) == 1
        ):
            packed = element.flatten(leaf, True, depth, fns)[0]
            assert isinstance(packed, Fixed)
            parts.append(
                Raw(
                    f"struct.pack(f'{fns.order}{{len({value})}}{element.code}', "
                    f"*({packed.arguments[0]} {clauses}))"
                )
            )
        else:
            item = render_parts(element.flatten(leaf, True, depth + 1, fns), fns.order)
            parts.append(Raw(f"b''.join({item} {clauses})"))
        return parts

    def unflatten(self, target, prefix, out, fns):
        sizes: list[str] = []
        if prefix or self.dimensions > 1:
            sizes = [out.temporary("size") for _ in range(self.dimensions)]
            fmt = fns.order + "i" * self.dimensions
            out.add(f"{', '.join(sizes)}, = struct.unpack_from({fmt!r}, data, pos)")
            out.add(f"pos += {4 * self.dimensions}")
        else:
            width = self.element.fixed_size()
            if not width:
                raise UnsupportedLayout(
                    "an array of variable-size elements without sizes"
                )
            sizes = [out.temporary("size")]
            out.add(f"{sizes[0]} = (len(data) - pos) // {width}")
        count = " * ".join(sizes)
        flat = out.temporary("items") if self.dimensions > 1 else target
        element = self.element
        if isinstance(element, (Integer, Real)) and len(element.code) == 1:
            fmt = f"{fns.order}{{{count}}}{element.code}"
            out.add(f"{flat} = list(struct.unpack_from(f'{fmt}', data, pos))")
            out.add(f"pos += {count} * {element.width}")
        else:
            item = out.temporary("item")
            out.add(f"{flat} = []")
            out.add(f"for _ in range({count}):")
            out.indent += 1
            element.unflatten(item, True, out, fns)
            out.add(f"{flat}.append({item})")
            out.indent -= 1
        if self.dimensions > 1:
            out.add(f"{target} = {_reshape(flat, sizes)}")

    def swap(self, value, chunk, width_min, depth, fns):
        names = [f"_s{depth}_{level}" for level in range(self.dimensions)]
        inner = self.element.swap(names[-1], chunk, width_min, depth + 1, fns)
        if inner == names[-1]:
            return value
        expression = inner
        for level in reversed(range(self.dimensions)):
            source = value if level == 0 else names[level - 1]
            expression = f"[{expression} for {names[level]} in {source}]"
        return expression


def _reshape(flat: str, sizes: list[str], start: str = "", depth: int = 0) -> str:
    """The nested lists of ``flat`` with the given ``sizes``, outermost first;
    ``start`` is the index of the first element of the part being cut."""
    if len(sizes) == 1:
        stop = f"{start} + {sizes[0]}" if start else sizes[0]
        return f"{flat}[{start}:{stop}]"
    stride = " * ".join(sizes[1:])
    index = f"_i{depth}"
    offset = f"{index} * {stride}"
    inner = _reshape(
        flat, sizes[1:], f"{start} + {offset}" if start else offset, depth + 1
    )
    return f"[{inner} for {index} in range({sizes[0]})]"


@dataclass(frozen=True)
class Field:
    name: str
    layout: Layout


@dataclass(frozen=True)
class Record(Layout):
    """A cluster. A named one (a typedef or class) is a ``SimpleNamespace`` read by
    attribute; an anonymous one is a tuple read by position."""

    label: str
    named: bool
    fields: tuple[Field, ...]

    def fixed_size(self) -> int | None:
        sizes = [f.layout.fixed_size() for f in self.fields]
        if None in sizes:
            return None
        return sum(s for s in sizes if s is not None)

    def _member(self, value: str, index: int) -> str:
        field = self.fields[index]
        return f"{value}.{field.name}" if self.named else f"{value}[{index}]"

    def flatten(self, value, prefix, depth, fns):
        name = fns.named(
            "flatten",
            self.label,
            (self,),
            lambda n: self._flatten_source(n, fns),
        )
        return [Raw(f"{name}({value})")]

    def _flatten_source(self, name: str, fns: Functions) -> str:
        parts: list[Part] = []
        for index, field in enumerate(self.fields):
            parts.extend(
                field.layout.flatten(self._member("value", index), True, 0, fns)
            )
        return f"def {name}(value):\n    return {render_parts(parts, fns.order)}\n"

    def _number_run(self, start: int) -> list[Integer | Real]:
        """The fields from ``start`` on that are single numbers or Booleans, which
        one ``struct.unpack_from`` reads together."""
        run: list[Integer | Real] = []
        for field in self.fields[start:]:
            layout = field.layout
            if not (isinstance(layout, (Integer, Real)) and len(layout.code) == 1):
                break
            run.append(layout)
        return run

    def unflatten(self, target, prefix, out, fns):
        out.add(f"{target}, pos = {self.reader(fns, prefix)}(data, pos)")

    def reader(self, fns, prefix):
        return fns.named(
            "unflatten", self.label, (self,), lambda n: self._unflatten_source(n, fns)
        )

    def _unflatten_source(self, name: str, fns: Functions) -> str:
        out = LineWriter()
        values = [f"_field{index}" for index in range(len(self.fields))]
        index = 0
        while index < len(self.fields):
            numbers = self._number_run(index)
            if len(numbers) > 1:
                fmt = fns.order + "".join(layout.code for layout in numbers)
                targets = ", ".join(values[index : index + len(numbers)])
                out.add(f"{targets} = struct.unpack_from({fmt!r}, data, pos)")
                out.add(f"pos += {sum(layout.width for layout in numbers)}")
                index += len(numbers)
            else:
                self.fields[index].layout.unflatten(values[index], True, out, fns)
                index += 1
        if self.named:
            fns.ctx.add_import("from types import SimpleNamespace")
            built = (
                "SimpleNamespace("
                + ", ".join(f"{f.name}={v}" for f, v in zip(self.fields, values))
                + ")"
            )
        else:
            built = "(" + "".join(f"{v}, " for v in values) + ")"
        out.add(f"return {built}, pos")
        return f"def {name}(data, pos):\n" + "\n".join(out.lines) + "\n"

    def swap(self, value, chunk, width_min, depth, fns):
        members = [
            f.layout.swap(self._member("value", i), chunk, width_min, 0, fns)
            for i, f in enumerate(self.fields)
        ]
        if all(m == self._member("value", i) for i, m in enumerate(members)):
            return value
        kind = "bytes" if chunk == 1 else "words"
        name = fns.named(
            f"swap_{kind}",
            self.label,
            (self, width_min),
            lambda n: self._swap_source(n, members),
        )
        return f"{name}({value})"

    def _swap_source(self, name: str, members: list[str]) -> str:
        if self.named:
            built = (
                "SimpleNamespace("
                + ", ".join(f"{f.name}={m}" for f, m in zip(self.fields, members))
                + ")"
            )
        else:
            built = "(" + "".join(f"{m}, " for m in members) + ")"
        return f"def {name}(value):\n    return {built}\n"

    def helper_label(self) -> str:
        return self.label


def layout_of(lv_type: LVType | None) -> Layout:
    """The layout of a terminal's type; raises ``UnsupportedLayout`` for a type
    this does not flatten."""
    if lv_type is None:
        raise UnsupportedLayout("a value with no known type")
    if lv_type.kind == LVTypeKind.ARRAY:
        return Sequence(layout_of(lv_type.element_type), lv_type.dimensions or 1)
    if lv_type.kind == LVTypeKind.CLUSTER and lv_type.fields:
        names = unique_names(f.name for f in lv_type.fields)
        return Record(
            label=lv_type.typedef_name or lv_type.classname or "cluster",
            named=bool(lv_type.typedef_name or lv_type.classname),
            fields=tuple(
                Field(to_var_name(name), layout_of(field.type))
                for field, name in zip(lv_type.fields, names)
            ),
        )
    underlying = lv_type.underlying_type or ""
    if underlying == "Refnum" and lv_type.ref_type not in _STRING_REFNUMS:
        return Integer("I", "c_uint32", 4)
    if underlying == "Refnum":
        return Text()
    if is_string_type_name(underlying):
        return Text()
    if underlying == "Boolean":
        return Real("?", 1)
    # A unit type is its number with a physical unit; units mean nothing in Python.
    if underlying.startswith("Unit"):
        underlying = "Num" + underlying[len("Unit") :]
    if underlying in _INTEGER_CODES:
        code, ctype, width = _INTEGER_CODES[underlying]
        return Integer(code, ctype, width)
    if underlying in _FLOAT_CODES:
        return Real(*_FLOAT_CODES[underlying])
    if underlying in _COMPLEX_CODES:
        return Real(*_COMPLEX_CODES[underlying])
    raise UnsupportedLayout(f"a value of type {underlying or lv_type.kind.value!r}")

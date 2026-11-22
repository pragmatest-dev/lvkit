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
from collections.abc import Callable
from collections.abc import Sequence as SequenceOf
from dataclasses import dataclass

from lvkit.lv_type_codes import is_string_type_name
from lvkit.models import LVType, LVTypeKind, unique_names

from .ast_utils import to_var_name
from .context import CodeGenContext
from .flat_path_source import FLATTEN_PATH, UNFLATTEN_PATH
from .flat_picture_source import (
    FLATTEN_PICTURE,
    PICTURE_IMPORTS,
    UNFLATTEN_PICTURE,
)
from .flat_tables import (
    COMPLEX_CODES,
    EXTENDED_UNFLATTEN,
    FLOAT_CODES,
    INTEGER_CODES,
    NUMBER_PREFIX,
    UNIT_PREFIX,
)
from .flat_timestamp_source import (
    FLATTEN_TIMESTAMP,
    TIMESTAMP_IMPORTS,
    UNFLATTEN_TIMESTAMP,
)
from .native_array import dimension_sizes

# The byte-order prefix of a ``struct`` format for LabVIEW's three byte orders:
# big-endian, the machine's, little-endian.
BYTE_ORDER_PREFIX = {0: ">", 1: "=", 2: "<"}

# Refnum kinds that do not flatten as a 32-bit integer (NI's "Flattened Data":
# VISA, IVI and user-defined tags flatten as strings or several values).
_STRING_REFNUMS = frozenset({"VisaRef", "IVIRef", "UsrDefndTag", "UsrDefTagFlt"})
# The refnum kind of a LabVIEW class value: a by-value object, not a handle.
_CLASS_REF_TYPE = "UDClassInst"


class UnsupportedLayout(Exception):
    """A type whose flattened form this does not write (extended-precision
    numbers, variants, …); the message names it."""


class ShapeMismatch(UnsupportedLayout):
    """Operands of an element-wise node whose arrays and clusters do not line up.
    ``operand_labels`` name every operand's layout at the place they differ, in
    operand order."""

    def __init__(
        self, driver: Layout, other: Layout, operands: SequenceOf[Operand]
    ) -> None:
        super().__init__(f"{driver.helper_label()} taken with {other.helper_label()}")
        self.operand_labels = [layout.helper_label() for layout, _ in operands]


# One value of an element-wise node: (its layout, its expression).
Operand = tuple["Layout", str]
# What a node writes for the values in the same place of every operand: (their
# layouts, their expressions, both in operand order) -> the expression of its
# result. A leaf that handles only some kinds of value (integers) checks the
# layouts itself.
OperandLeaf = Callable[[list["Layout"], list[str]], str]

# The names an operand's value has inside a helper function a cluster needs.
_PARAMETERS = ("value", "other")
# The first letter of the loop variables of operand 0, 1, ... in a comprehension.
_LOOP_LETTERS = "stuvwxyz"


def _parameter(operand: int) -> str:
    return _PARAMETERS[operand] if operand < len(_PARAMETERS) else f"operand{operand}"


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
        for module in ("struct", "ctypes", "math"):
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

    def map_operands(
        self,
        operands: SequenceOf[Operand],
        leaf: OperandLeaf,
        kind: str,
        tag: object,
        depth: int,
        fns: Functions,
        result: Layout | None = None,
        broadcast: bool = True,
    ) -> str:
        """Called on the first aggregate of ``operands`` by ``walk_operands``; a
        value that is no array or cluster applies ``leaf`` to the operands'
        layouts and expressions, and raises ``ShapeMismatch`` when another operand
        is an array or cluster. ``result`` is the layout of the value the walk
        builds (None: this layout's own shape); ``broadcast`` is whether a value
        that is no array or cluster goes with every element of an aggregate."""
        for layout, _ in operands:
            if isinstance(layout, (Record, Sequence)):
                raise ShapeMismatch(self, layout, operands)
        return leaf(
            [layout for layout, _ in operands],
            [expression for _, expression in operands],
        )

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


_EXTENDED_FLATTEN = """def {name}(value):
    value = float(value)
    if value != value:
        return b'\\x7f\\xff\\x80' + bytes(13)
    sign = 0x8000 if math.copysign(1.0, value) < 0 else 0
    value = abs(value)
    if value == math.inf:
        return struct.pack('>H', sign | 0x7FFF) + bytes(14)
    if value == 0:
        return struct.pack('>H', sign) + bytes(14)
    mantissa, exponent = math.frexp(value)
    fraction = int((mantissa * 2 - 1) * 2**52) << 60
    head = struct.pack('>H', sign | (exponent - 1 + 16383))
    return head + fraction.to_bytes(14, 'big')
"""



@dataclass(frozen=True)
class Extended(Layout):
    """An extended-precision number, which Python holds as a ``float``; it
    flattens as the 128-bit IEEE format (NI's "Flattened Data"). A complex one is
    a Python ``complex`` of two."""

    is_complex: bool = False

    def fixed_size(self) -> int | None:
        return 32 if self.is_complex else 16

    def _writer(self, fns: Functions) -> str:
        return fns.named(
            "flatten",
            "extended",
            ("extended",),
            lambda name: _EXTENDED_FLATTEN.format(name=name),
        )

    def _reader(self, fns: Functions) -> str:
        return fns.named(
            "unflatten",
            "extended",
            ("extended",),
            lambda name: EXTENDED_UNFLATTEN.format(name=name),
        )

    def flatten(self, value, prefix, depth, fns):
        write = self._writer(fns)
        if self.is_complex:
            return [Raw(f"{write}({value}.real) + {write}({value}.imag)")]
        return [Raw(f"{write}({value})")]

    def unflatten(self, target, prefix, out, fns):
        read = self._reader(fns)
        if not self.is_complex:
            out.add(f"{target}, pos = {read}(data, pos)")
            return
        real, imaginary = out.temporary("real"), out.temporary("imaginary")
        out.add(f"{real}, pos = {read}(data, pos)")
        out.add(f"{imaginary}, pos = {read}(data, pos)")
        out.add(f"{target} = complex({real}, {imaginary})")


@dataclass(frozen=True)
class TimeStamp(Layout):
    """A time stamp: an I64 count of seconds and a U64 fraction, 16 bytes."""

    def fixed_size(self) -> int | None:
        return 16

    def _function(self, fns: Functions, kind: str, template: str) -> str:
        for statement_import in TIMESTAMP_IMPORTS:
            fns.ctx.add_import(statement_import)
        return fns.named(
            kind,
            "timestamp",
            ("timestamp",),
            lambda name: template.format(
                name=name, order=repr(fns.order), params="", setup=""
            ),
        )

    def flatten(self, value, prefix, depth, fns):
        write = self._function(fns, "flatten", FLATTEN_TIMESTAMP)
        return [Raw(f"{write}({value})")]

    def unflatten(self, target, prefix, out, fns):
        read = self._function(fns, "unflatten", UNFLATTEN_TIMESTAMP)
        out.add(f"{target}, pos = {read}(data, pos)")


@dataclass(frozen=True)
class PictureLayout(Layout):
    """A Picture: its length and its drawing records (``flat_picture_source``).
    The length is part of the form, so the prepend-size option does not change
    it."""

    def fixed_size(self) -> int | None:
        return None

    def helper_label(self) -> str:
        return "picture"

    def _function(self, fns: Functions, kind: str, template: str) -> str:
        for statement_import in PICTURE_IMPORTS:
            fns.ctx.add_import(statement_import)
        return fns.named(
            kind,
            "picture",
            ("picture",),
            lambda name: template.format(name=name, order=fns.order),
        )

    def flatten(self, value, prefix, depth, fns):
        write = self._function(fns, "flatten", FLATTEN_PICTURE)
        return [Raw(f"{write}({value})")]

    def unflatten(self, target, prefix, out, fns):
        read = self._function(fns, "unflatten", UNFLATTEN_PICTURE)
        out.add(f"{target}, pos = {read}(data, pos)")


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
            # A string longer than the data left is truncated data, which a
            # slice would hide; it is reported as struct.unpack_from reports it.
            out.add(f"if {count} < 0 or pos + {count} > len(data):")
            out.indent += 1
            out.add("raise struct.error('a string runs past the end of the data')")
            out.indent -= 1
            out.add(f"{target} = data[pos:pos + {count}].decode('latin-1')")
            out.add(f"pos += {count}")
        else:
            out.add(f"{target} = data[pos:].decode('latin-1')")
            out.add("pos = len(data)")


@dataclass(frozen=True)
class PathLayout(Layout):
    """A path: ``PTH0``, its own length, the type and the components
    (``flat_path_source``). The length is part of the form, so the prepend-size
    option does not change it."""

    def fixed_size(self) -> int | None:
        return None

    def helper_label(self) -> str:
        return "path"

    def _imports(self, fns: Functions) -> None:
        for line in ("import os", "import re", "from pathlib import Path"):
            fns.ctx.add_import(line)

    def flatten(self, value, prefix, depth, fns):
        self._imports(fns)
        name = fns.named(
            "flatten",
            "path",
            ("path",),
            lambda n: FLATTEN_PATH.format(name=n, order=fns.order),
        )
        return [Raw(f"{name}({value})")]

    def unflatten(self, target, prefix, out, fns):
        out.add(f"{target}, pos = {self.reader(fns, prefix)}(data, pos)")

    def reader(self, fns, prefix):
        self._imports(fns)
        return fns.named(
            "unflatten",
            "path",
            ("path",),
            lambda n: UNFLATTEN_PATH.format(name=n, order=fns.order),
        )


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

    def map_operands(
        self,
        operands,
        leaf,
        kind,
        tag,
        depth,
        fns,
        result=None,
        broadcast=True,
    ):
        element_result = None
        if result is not None:
            if not isinstance(result, Sequence) or result.dimensions != self.dimensions:
                raise UnsupportedLayout(
                    f"{self.helper_label()} giving {result.helper_label()}"
                )
            element_result = result.element
        arrays = [
            j for j, (layout, _) in enumerate(operands) if isinstance(layout, Sequence)
        ]
        for layout, _ in operands:
            if (
                isinstance(layout, Record)
                or (
                    isinstance(layout, Sequence)
                    and layout.dimensions != self.dimensions
                )
                or (not broadcast and not isinstance(layout, Sequence))
            ):
                raise ShapeMismatch(self, layout, operands)
        names = {
            j: [
                f"_{_LOOP_LETTERS[j]}{depth}_{level}"
                for level in range(self.dimensions)
            ]
            for j in arrays
        }
        inner = [
            (layout.element, names[j][-1])
            if isinstance(layout, Sequence)
            else (layout, expression)
            for j, (layout, expression) in enumerate(operands)
        ]
        expression = walk_operands(
            inner, leaf, kind, tag, depth + 1, fns, element_result, broadcast
        )
        if len(operands) == 1 and expression == names[0][-1]:
            return operands[0][1]
        for level in reversed(range(self.dimensions)):
            targets = ", ".join(names[j][level] for j in arrays)
            sources = [
                operands[j][1] if level == 0 else names[j][level - 1] for j in arrays
            ]
            source = sources[0] if len(sources) == 1 else f"zip({', '.join(sources)})"
            expression = f"[{expression} for {targets} in {source}]"
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

    def map_operands(
        self,
        operands,
        leaf,
        kind,
        tag,
        depth,
        fns,
        result=None,
        broadcast=True,
    ):
        built = self if result is None else result
        if not isinstance(built, Record) or len(built.fields) != len(self.fields):
            raise UnsupportedLayout(
                f"{self.helper_label()} giving {built.helper_label()}"
            )
        for layout, _ in operands:
            if (
                isinstance(layout, Sequence)
                or (
                    isinstance(layout, Record)
                    and len(layout.fields) != len(self.fields)
                )
                or (not broadcast and not isinstance(layout, Record))
            ):
                raise ShapeMismatch(self, layout, operands)
        parameters = [_parameter(j) for j in range(len(operands))]
        members = [
            walk_operands(
                [
                    (layout.fields[index].layout, layout._member(parameter, index))
                    if isinstance(layout, Record)
                    else (layout, parameter)
                    for (layout, _), parameter in zip(operands, parameters)
                ],
                leaf,
                kind,
                tag,
                0,
                fns,
                built.fields[index].layout,
                broadcast,
            )
            for index in range(len(self.fields))
        ]
        if len(operands) == 1 and all(
            member == self._member(parameters[0], index)
            for index, member in enumerate(members)
        ):
            return operands[0][1]
        layouts = tuple(layout for layout, _ in operands)
        if built.named:
            fns.ctx.add_import("from types import SimpleNamespace")
        name = fns.named(
            kind,
            built.label,
            (built, layouts, tag),
            lambda n: built._mapped_source(n, members, ", ".join(parameters)),
        )
        return f"{name}({', '.join(expression for _, expression in operands)})"

    def _mapped_source(
        self, name: str, members: list[str], parameters: str = "value"
    ) -> str:
        if self.named:
            built = (
                "SimpleNamespace("
                + ", ".join(f"{f.name}={m}" for f, m in zip(self.fields, members))
                + ")"
            )
        else:
            built = "(" + "".join(f"{m}, " for m in members) + ")"
        return f"def {name}({parameters}):\n    return {built}\n"

    def helper_label(self) -> str:
        return self.label


def walk_operands(
    operands: SequenceOf[Operand],
    leaf: OperandLeaf,
    kind: str,
    tag: object,
    depth: int,
    fns: Functions,
    result: Layout | None = None,
    broadcast: bool = True,
) -> str:
    """The expression that applies ``leaf`` to the values in the same place of
    every operand, element by element: an array or cluster operand fixes the
    shape (the first one, when several), arrays and clusters of the same shape
    are taken in step, and a value that is neither is used with every element
    (with ``broadcast`` false, every operand must be of that shape: a mismatch
    raises ``ShapeMismatch``). A walk of one operand whose leaf returns every value
    as it is gives that operand itself. ``result`` is the layout of the value built
    (its clusters' names and kind), None for the shape of the operand that fixes
    it. ``kind`` and ``tag`` name the helper function a cluster needs."""
    driver = next(
        (layout for layout, _ in operands if isinstance(layout, (Record, Sequence))),
        operands[0][0],
    )
    return driver.map_operands(
        operands, leaf, kind, tag, depth, fns, result, broadcast
    )


def layout_of(lv_type: LVType | None, ctx: CodeGenContext | None = None) -> Layout:
    """The layout of a terminal's type; raises ``UnsupportedLayout`` for a type
    this does not flatten. ``ctx`` is the module being written: a class value
    needs its graph to list the classes it can hold, so with none it is a gap."""
    if lv_type is None:
        raise UnsupportedLayout("a value with no known type")
    if lv_type.kind == LVTypeKind.ARRAY:
        return Sequence(
            layout_of(lv_type.element_type, ctx), lv_type.dimensions or 1
        )
    if lv_type.kind == LVTypeKind.CLUSTER and lv_type.fields:
        names = unique_names(f.name for f in lv_type.fields)
        return Record(
            label=lv_type.typedef_name or lv_type.classname or "cluster",
            named=bool(lv_type.typedef_name or lv_type.classname),
            fields=tuple(
                Field(to_var_name(name), layout_of(field.type, ctx))
                for field, name in zip(lv_type.fields, names)
            ),
        )
    if lv_type.is_timestamp:
        return TimeStamp()
    underlying = lv_type.underlying_type or ""
    if lv_type.ref_type == _CLASS_REF_TYPE:
        if ctx is None:
            raise UnsupportedLayout(
                f"a LabVIEW class value ({lv_type.classname or 'a class'}), which "
                "flattens as its class name and its data, not as a reference"
            )
        # flat_class builds on this module, so it is imported where it is used.
        from .flat_class import class_layout

        return class_layout(lv_type, ctx)
    if underlying in ("Set", "Map"):
        # flat_collection builds on this module, so it is imported where it is used.
        from .flat_collection import collection_layout

        return collection_layout(lv_type, ctx)
    if underlying == "Refnum" and lv_type.ref_type not in _STRING_REFNUMS:
        raise UnsupportedLayout(
            f"a {lv_type.ref_type or 'reference'} refnum as an integer or bytes: "
            "a reference is a Python object (a VI reference is a function), "
            "which has no integer value"
        )
    if underlying == "Refnum":
        return Text()
    if is_string_type_name(underlying):
        return Text()
    if underlying == "Path":
        return PathLayout()
    if underlying == "Picture":
        return PictureLayout()
    if underlying == "Boolean":
        return Real("?", 1)
    # A unit type is its number with a physical unit; units mean nothing in Python.
    if underlying.startswith(UNIT_PREFIX):
        underlying = NUMBER_PREFIX + underlying[len(UNIT_PREFIX) :]
    if underlying in INTEGER_CODES:
        code, ctype, width = INTEGER_CODES[underlying]
        return Integer(code, ctype, width)
    if underlying in FLOAT_CODES:
        return Real(*FLOAT_CODES[underlying])
    if underlying in ("NumFloatExt", "NumComplexExt"):
        return Extended(is_complex=underlying == "NumComplexExt")
    if underlying in COMPLEX_CODES:
        return Real(*COMPLEX_CODES[underlying])
    raise UnsupportedLayout(f"a value of type {underlying or lv_type.kind.value!r}")

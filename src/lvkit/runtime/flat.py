"""LabVIEW's flattened-data format, for Type Cast, Unflatten From String and the
byte-order functions.

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

A type spec is one of:

- an integer, float, Boolean or string name: ``i8`` .. ``u64``, ``f32``, ``f64``,
  ``bool``, ``str``;
- ``"refnum"``: a refnum that flattens as a 32-bit integer (NI: "LabVIEW stores
  the majority of flattened refnums as flattened 32-bit integers"; the
  exceptions, refnum type codes 0xE, 0xF, 0x15, 0x1A, 0x1C and 0x1D, are not
  supported);
- ``"u32[]"``: a 1-D array of integers (the short form of the next);
- ``("array", element, dimensions)``;
- ``("cluster", named, ((attribute, spec), ...))``: a named cluster is a
  ``SimpleNamespace``, an anonymous one a tuple.

A string or array that is the whole value carries no size when ``prefix`` is
false: Type Cast flattens and unflattens without one (NI: casting an array of
four 16-bit integers to 32-bit integers gives two elements), while a string or
array inside a cluster or array always does. Anything else raises
``NotImplementedError``, never a silently wrong value.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

INTEGERS = {
    "i8": (1, True),
    "u8": (1, False),
    "i16": (2, True),
    "u16": (2, False),
    "i32": (4, True),
    "u32": (4, False),
    "i64": (8, True),
    "u64": (8, False),
}
_FLOATS = {"f32": ("f", 4), "f64": ("d", 8)}


def _normal(spec: Any) -> Any:
    """``spec`` with the ``"u32[]"`` short form written out."""
    if isinstance(spec, str) and spec.endswith("[]") and spec[:-2] in INTEGERS:
        return ("array", spec[:-2], 1)
    return "u32" if spec == "refnum" else spec


def _unsupported(what: str, spec: Any) -> NotImplementedError:
    return NotImplementedError(f"Type Cast cannot {what} spec {spec!r}")


def fixed_size(spec: Any) -> int | None:
    """The flattened size in bytes of a value of ``spec``, or None when it varies
    (a string or array)."""
    spec = _normal(spec)
    if isinstance(spec, str):
        if spec in INTEGERS:
            return INTEGERS[spec][0]
        if spec in _FLOATS:
            return _FLOATS[spec][1]
        return 1 if spec == "bool" else None
    if spec[0] == "cluster":
        sizes = [fixed_size(sub) for _, sub in spec[2]]
        return None if None in sizes else sum(s for s in sizes if s is not None)
    return None


def _wrapped(value: Any, width: int) -> int:
    """``value`` as an unsigned ``width``-byte integer: a LabVIEW integer wraps
    around at its width, which a Python integer does not."""
    return int(value) & ((1 << (8 * width)) - 1)


def _member(value: Any, index: int, name: str) -> Any:
    """Member ``index`` of a cluster value: a tuple or list by position, a
    dictionary by its (ordered) values, anything else by attribute ``name``."""
    if isinstance(value, (tuple, list)):
        return value[index]
    if isinstance(value, Mapping):
        return list(value.values())[index]
    return getattr(value, name)


def _shape(value: Any, dimensions: int) -> tuple[list[int], list[Any]]:
    if dimensions == 1:
        return [len(value)], list(value)
    rows = [_shape(row, dimensions - 1) for row in value]
    inner = rows[0][0] if rows else [0] * (dimensions - 1)
    return [len(rows), *inner], [item for _, flat in rows for item in flat]


def flatten(value: Any, spec: Any, order: str = "big", prefix: bool = False) -> bytes:
    """``value`` as flattened bytes of type ``spec``. ``order`` is ``big`` (what
    LabVIEW stores) or ``little``."""
    spec = _normal(spec)
    if isinstance(spec, str):
        if spec == "str":
            data = value.encode("latin-1") if isinstance(value, str) else bytes(value)
            size = len(data).to_bytes(4, order) if prefix else b""  # type: ignore[arg-type]
            return size + data
        if spec in INTEGERS:
            width, _ = INTEGERS[spec]
            return _wrapped(value, width).to_bytes(width, order)  # type: ignore[arg-type]
        if spec in _FLOATS:
            code, _ = _FLOATS[spec]
            return struct.pack((">" if order == "big" else "<") + code, value)
        if spec == "bool":
            return b"\x01" if value else b"\x00"
        raise _unsupported("flatten", spec)
    if spec[0] == "array":
        sizes, flat = _shape(value, spec[2])
        head = (
            b"".join(n.to_bytes(4, order) for n in sizes)  # type: ignore[arg-type]
            if prefix or spec[2] > 1
            else b""
        )
        return head + b"".join(flatten(item, spec[1], order, True) for item in flat)
    if spec[0] == "cluster":
        return b"".join(
            flatten(_member(value, i, name), sub, order, True)
            for i, (name, sub) in enumerate(spec[2])
        )
    raise _unsupported("flatten", spec)


def _read(data: bytes, pos: int, size: int) -> bytes:
    if pos + size > len(data):
        raise ValueError("the flattened data is shorter than its type")
    return data[pos : pos + size]


def unflatten_at(
    data: bytes, pos: int, spec: Any, order: str = "big", prefix: bool = False
) -> tuple[Any, int]:
    """The value of ``spec`` flattened at ``data[pos:]`` and the position after
    it."""
    spec = _normal(spec)
    if isinstance(spec, str):
        if spec == "str":
            if prefix:
                count = int.from_bytes(_read(data, pos, 4), order)  # type: ignore[arg-type]
                return _read(data, pos + 4, count).decode("latin-1"), pos + 4 + count
            return data[pos:].decode("latin-1"), len(data)
        if spec in INTEGERS:
            width, signed = INTEGERS[spec]
            raw = _read(data, pos, width)
            return int.from_bytes(raw, order, signed=signed), pos + width  # type: ignore[arg-type]
        if spec in _FLOATS:
            code, width = _FLOATS[spec]
            raw = _read(data, pos, width)
            fmt = (">" if order == "big" else "<") + code
            return struct.unpack(fmt, raw)[0], pos + width
        if spec == "bool":
            return bool(_read(data, pos, 1)[0]), pos + 1
        raise _unsupported("unflatten", spec)
    if spec[0] == "array":
        element, dimensions = spec[1], spec[2]
        if prefix or dimensions > 1:
            sizes = [
                int.from_bytes(_read(data, pos + 4 * i, 4), order)  # type: ignore[arg-type]
                for i in range(dimensions)
            ]
            pos += 4 * dimensions
        else:
            width = fixed_size(element)
            if not width:
                raise _unsupported("unflatten without sizes", spec)
            sizes = [(len(data) - pos) // width]
        count = 1
        for n in sizes:
            count *= n
        flat = []
        for _ in range(count):
            item, pos = unflatten_at(data, pos, element, order, True)
            flat.append(item)
        return _nest(flat, sizes), pos
    if spec[0] == "cluster":
        values = []
        for _, sub in spec[2]:
            item, pos = unflatten_at(data, pos, sub, order, True)
            values.append(item)
        if spec[1]:
            return SimpleNamespace(**{n: v for (n, _), v in zip(spec[2], values)}), pos
        return tuple(values), pos
    raise _unsupported("unflatten", spec)


def _nest(flat: list[Any], sizes: Sequence[int]) -> list[Any]:
    if len(sizes) == 1:
        return flat
    step = 1
    for n in sizes[1:]:
        step *= n
    return [_nest(flat[i * step : (i + 1) * step], sizes[1:]) for i in range(sizes[0])]


def unflatten(data: bytes, spec: Any, order: str = "big", prefix: bool = False) -> Any:
    """The value of ``spec`` at the start of ``data``."""
    return unflatten_at(data, 0, spec, order, prefix)[0]


def type_cast(value: Any, source: Any, destination: Any) -> Any:
    """NI's Type Cast: "Casts x to the data type, type, by flattening it and
    unflattening it using the new data type." When ``value`` is shorter than the
    destination "this function moves the data in x to the upper bytes of type and
    fills the remaining bytes with zeros"; when longer it "uses the upper bytes of
    x and discards the remaining lower bytes"."""
    data = flatten(value, source)
    size = fixed_size(destination)
    if size is not None and len(data) < size:
        data += bytes(size - len(data))
    return unflatten(data, destination)


def _swapped(value: Any, spec: Any, width_min: int, chunk: int) -> Any:
    """``value`` with the ``chunk``-byte halves swapped in every integer of at
    least ``width_min`` bytes: see ``swap_bytes`` and ``swap_words``."""
    spec = _normal(spec)
    if isinstance(spec, str):
        if spec not in INTEGERS:
            return value  # NI: only the integers of a cluster are swapped
        width, signed = INTEGERS[spec]
        if width < width_min:
            return value
        raw = _wrapped(value, width).to_bytes(width, "big")
        swapped = b"".join(
            raw[i : i + 2 * chunk][chunk : 2 * chunk] + raw[i : i + 2 * chunk][:chunk]
            for i in range(0, width, 2 * chunk)
        )
        return int.from_bytes(swapped, "big", signed=signed)
    if spec[0] == "array":
        if spec[2] == 1:
            return [_swapped(item, spec[1], width_min, chunk) for item in value]
        return [
            _swapped(row, ("array", spec[1], spec[2] - 1), width_min, chunk)
            for row in value
        ]
    members = [
        _swapped(_member(value, i, name), sub, width_min, chunk)
        for i, (name, sub) in enumerate(spec[2])
    ]
    if spec[1]:
        return SimpleNamespace(**{n: v for (n, _), v in zip(spec[2], members)})
    return tuple(members)


def swap_bytes(value: Any, spec: Any) -> Any:
    """NI's Swap Bytes: "Swaps the high-order 8 bits and the low-order 8 bits for
    every word in data"; ``data`` is an integer, an array of integers or a
    cluster, of which "this function swaps only the integer elements"."""
    return _swapped(value, spec, 2, 1)


def swap_words(value: Any, spec: Any) -> Any:
    """NI's Swap Words: "Swaps the high-order 16 bits and the low-order 16 bits for
    every long integer in data" (``1234567890ABCDEF`` becomes
    ``56781234CDEF90AB``)."""
    return _swapped(value, spec, 4, 2)

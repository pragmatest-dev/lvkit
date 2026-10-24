"""LabVIEW handles and clusters passed to C-callable functions.

Each function under test is a genuine C function pointer (``ctypes.CFUNCTYPE``)
that reads and writes the memory it is given the way C code would, following
NI's documented layouts: a string handle points to a pointer to
``{int32 count; bytes}``; an array handle points to a pointer to the 32-bit
dimension sizes followed by the elements; a cluster is a C structure with natural
alignment, its strings and arrays handles."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from typing import Any

import pytest

from lvkit.runtime.clfn import call_function_pointer

_keep: list[Any] = []  # memory the C-callable functions allocate and hand back


def _string_at(handle: int) -> bytes:
    block = ctypes.c_void_p.from_address(handle).value
    if not block:
        return b""
    count = ctypes.c_int32.from_address(block).value
    return ctypes.string_at(block + 4, count)


def _new_string(data: bytes) -> ctypes.Array[ctypes.c_char]:
    block = ctypes.create_string_buffer(4 + len(data))
    ctypes.c_int32.from_address(ctypes.addressof(block)).value = len(data)
    block[4 : 4 + len(data)] = data
    _keep.append(block)
    return block


def test_string_handle_is_read_and_written_in_place() -> None:
    @ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p)
    def upper(handle: int) -> int:
        block = ctypes.c_void_p.from_address(handle).value
        assert block is not None
        data = _string_at(handle)
        ctypes.memmove(block + 4, data.upper(), len(data))
        return len(data)

    result = call_function_pointer(
        upper, [("value", "int32"), ("string_handle", None)], ["hello"]
    )
    assert result == (5, "HELLO")


def test_string_handle_the_function_replaces_comes_back_resized() -> None:
    @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    def grow(handle: int) -> None:
        new = _new_string(_string_at(handle) + b" world")
        ctypes.c_void_p.from_address(handle).value = ctypes.addressof(new)

    result = call_function_pointer(
        grow, [("no_value", None), ("string_handle", None)], ["hello"]
    )
    assert result == (None, "hello world")


def test_a_null_string_handle_is_an_empty_string() -> None:
    @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    def clear(handle: int) -> None:
        ctypes.c_void_p.from_address(handle).value = None

    assert call_function_pointer(
        clear, [("no_value", None), ("string_handle", None)], ["text"]
    ) == (None, "")


def test_string_with_a_null_character_keeps_its_length() -> None:
    @ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p)
    def length(handle: int) -> int:
        return len(_string_at(handle))

    result = call_function_pointer(
        length, [("value", "int32"), ("string_handle", None)], ["a\x00b"]
    )
    assert result == (3, "a\x00b")


def test_one_dimensional_array_handle() -> None:
    @ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p)
    def double(handle: int) -> int:
        block = ctypes.c_void_p.from_address(handle).value
        assert block is not None
        count = ctypes.c_int32.from_address(block).value
        elements = (ctypes.c_int32 * count).from_address(block + 4)
        for i in range(count):
            elements[i] *= 2
        return count

    result = call_function_pointer(
        double,
        [("value", "int32"), ("array_handle", ("array", "int32", 1))],
        [[1, 2, 3]],
    )
    assert result == (3, [2, 4, 6])


def test_two_dimensional_array_handle_has_both_sizes_before_the_data() -> None:
    @ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p)
    def negate(handle: int) -> int:
        block = ctypes.c_void_p.from_address(handle).value
        assert block is not None
        rows, columns = (ctypes.c_int32 * 2).from_address(block)
        elements = (ctypes.c_int16 * (rows * columns)).from_address(block + 8)
        for i in range(rows * columns):
            elements[i] = -elements[i]
        return rows * 100 + columns

    result = call_function_pointer(
        negate,
        [("value", "int32"), ("array_handle", ("array", "int16", 2))],
        [[[1, 2, 3], [4, 5, 6]]],
    )
    assert result == (203, [[-1, -2, -3], [-4, -5, -6]])


def test_array_handle_the_function_replaces_comes_back_resized() -> None:
    @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    def replace(handle: int) -> None:
        block = ctypes.create_string_buffer(4 + 4 * 4)
        ctypes.c_int32.from_address(ctypes.addressof(block)).value = 4
        (ctypes.c_float * 4).from_address(ctypes.addressof(block) + 4)[:] = [
            0.5,
            1.5,
            2.5,
            3.5,
        ]
        _keep.append(block)
        ctypes.c_void_p.from_address(handle).value = ctypes.addressof(block)

    result = call_function_pointer(
        replace,
        [("no_value", None), ("array_handle", ("array", "float32", 1))],
        [[]],
    )
    assert result == (None, [0.5, 1.5, 2.5, 3.5])


def test_a_null_array_handle_is_an_empty_array() -> None:
    @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    def clear(handle: int) -> None:
        ctypes.c_void_p.from_address(handle).value = None

    assert call_function_pointer(
        clear,
        [("no_value", None), ("array_handle", ("array", "int32", 1))],
        [[1, 2]],
    ) == (None, [])


class _Reading(ctypes.Structure):
    """A cluster {int8 flag; float64 value; int32 count; Boolean ok}: the double
    sits on its natural 8-byte boundary, after 7 bytes of padding."""

    _fields_ = [
        ("flag", ctypes.c_int8),
        ("value", ctypes.c_double),
        ("count", ctypes.c_int32),
        ("ok", ctypes.c_uint8),
    ]


_READING = (
    "cluster",
    False,
    (("flag", "int8"), ("value", "float64"), ("count", "int32"), ("ok", "bool")),
)


def test_cluster_is_a_structure_with_natural_alignment() -> None:
    assert _Reading.value.offset == 8
    assert ctypes.sizeof(_Reading) == 24

    @ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p)
    def update(address: int) -> int:
        reading = _Reading.from_address(address)
        reading.flag -= 1
        reading.value *= 2
        reading.count += 10
        reading.ok = 0 if reading.ok else 1
        return ctypes.sizeof(_Reading)

    result = call_function_pointer(
        update,
        [("value", "int32"), ("pointer", _READING)],
        [(5, 1.25, 3, True)],
    )
    assert result == (24, (4, 2.5, 13, False))


def test_named_cluster_comes_back_as_a_namespace_and_reads_attributes() -> None:
    named = ("cluster", True, (("low", "uint16"), ("high", "uint16")))

    @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    def swap(address: int) -> None:
        pair = (ctypes.c_uint16 * 2).from_address(address)
        pair[0], pair[1] = pair[1], pair[0]

    result = call_function_pointer(
        swap,
        [("no_value", None), ("pointer", named)],
        [SimpleNamespace(low=1, high=2)],
    )
    assert result[0] is None
    assert (result[1].low, result[1].high) == (2, 1)


class _Inner(ctypes.Structure):
    _fields_ = [("x", ctypes.c_uint8), ("y", ctypes.c_int32)]


class _Outer(ctypes.Structure):
    """A cluster {int16 id; cluster {uint8 x; int32 y}; string name}: the nested
    cluster is in line and the string is a handle."""

    _fields_ = [("id", ctypes.c_int16), ("inner", _Inner), ("name", ctypes.c_void_p)]


def test_cluster_with_a_nested_cluster_and_a_string_handle() -> None:
    spec = (
        "cluster",
        False,
        (
            ("id", "int16"),
            ("inner", ("cluster", False, (("x", "uint8"), ("y", "int32")))),
            ("name", ("string",)),
        ),
    )

    @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    def rewrite(address: int) -> None:
        outer = _Outer.from_address(address)
        outer.id += 1
        outer.inner.y = -outer.inner.y
        # The string is a handle: replace the string it points to.
        new = _new_string(_string_at(outer.name) + b"!")
        cell = ctypes.c_void_p(ctypes.addressof(new))
        _keep.append(cell)
        outer.name = ctypes.addressof(cell)

    result = call_function_pointer(
        rewrite,
        [("no_value", None), ("pointer", spec)],
        [(7, (3, 40), "hi")],
    )
    assert result == (None, (8, (3, -40), "hi!"))


@pytest.mark.parametrize(
    "element,why",
    [("int32", None), ("float64", "padding"), ("uint8", None)],
)
def test_array_handle_element_width_is_judged_by_the_documented_layout(
    element: str, why: str | None
) -> None:
    from lvkit.parser.extfunc_prototype import array_handle_problem

    problem = array_handle_problem(element)
    assert (problem is None) == (why is None)
    if why:
        assert why in problem  # type: ignore[operator]

"""Read from Binary File (8051) per NI's "Read from Binary File Function" page: it
reads ``count`` instances of ``data type`` from the file's current position (one,
as a single value, when ``count`` is unwired; every one to the end of the file for
-1), through the flattened-data layout of that type (a string or array carries its
size), in the byte order the node says (big-endian by default). Reaching the end
of the file keeps the complete elements read and gives error 4; a count under -1
is an error; an unwired file (a dialog) is a gap.

Each test builds a tiny diagram, generates its Python and runs it on real bytes.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType
from tests.helpers import error_cluster_type
from tests.native_vi import Generated, array, generate, node, result_values, scalar

I16 = scalar("NumInt16")
I32 = scalar("NumInt32")
U16 = scalar("NumUInt16")
DBL = scalar("NumFloat64")
STR = scalar("String")
FILE = scalar("Refnum")
PATH = scalar("Path")
ERROR = error_cluster_type()


def _read(
    data_type: LVType,
    *,
    count: int | None = None,
    order: int | None = None,
    file: LVType = FILE,
    result: LVType | None = None,
    with_error: bool = False,
    gaps: list[Gap] | None = None,
    file_wired: bool = True,
    type_wired: bool = True,
) -> Generated:
    """A VI whose Read from Binary File reads its ``f`` control as ``data_type``
    (left unwired when ``type_wired`` is False); ``count`` and ``order`` are wired
    from constants when given."""
    terminals: list[tuple[int, str, LVType | None]] = [
        (5, "input", data_type),
        (11, "input", file),
        (2, "output", result or data_type),
    ]
    wires = [("rb:2", "fp:d")]
    if type_wired:
        wires.append(("c5", "rb:5"))
    if file_wired:
        wires.append(("fp:f", "rb:11"))
    constants: dict[str, tuple[object, LVType]] = (
        {"c5": (0, data_type)} if type_wired else {}
    )
    outputs: dict[str, LVType] = {"d": result or data_type}
    inputs: dict[str, LVType] = {"f": file}
    if count is not None:
        terminals.append((10, "input", I32))
        wires.append(("c10", "rb:10"))
        constants["c10"] = (count, I32)
    if order is not None:
        terminals.append((9, "input", U16))
        wires.append(("c9", "rb:9"))
        constants["c9"] = (order, U16)
    if with_error:
        terminals += [(0, "output", ERROR), (8, "input", ERROR)]
        wires += [("rb:0", "fp:error_out"), ("fp:error_in", "rb:8")]
        outputs["error_out"] = ERROR
        inputs["error_in"] = ERROR
    return generate(
        inputs,
        outputs,
        [node("rb", 8051, terminals)],
        wires,
        constants=constants,
        gaps=gaps,
    )


def test_one_i32_big_endian_and_the_file_is_left_after_it() -> None:
    generated = _read(I32)
    stream = io.BytesIO(struct.pack(">ii", 256, 7))
    assert result_values(generated.function(stream))[0] == 256
    assert stream.tell() == 4
    assert result_values(generated.function(stream))[0] == 7


def test_the_data_type_is_read_not_one_byte() -> None:
    generated = _read(I32)
    assert "struct" in generated.source
    assert ".read(4)" in generated.source


def test_an_array_of_three_doubles() -> None:
    generated = _read(DBL, count=3, result=array("NumFloat64"))
    stream = io.BytesIO(struct.pack(">3d", 1.5, -2.0, 3.25) + b"tail")
    assert result_values(generated.function(stream))[0] == [1.5, -2.0, 3.25]
    assert stream.tell() == 24


def test_a_string_carries_its_size() -> None:
    generated = _read(STR)
    stream = io.BytesIO(struct.pack(">I", 3) + b"abcdef")
    assert result_values(generated.function(stream))[0] == "abc"
    assert stream.tell() == 7


def test_count_minus_one_reads_to_the_end_of_the_file() -> None:
    generated = _read(I16, count=-1, result=array("NumInt16"))
    stream = io.BytesIO(struct.pack(">4h", 1, 2, 3, 4))
    stream.seek(2)
    assert result_values(generated.function(stream))[0] == [2, 3, 4]
    assert stream.tell() == 8


def test_little_endian() -> None:
    generated = _read(I32, order=2)
    stream = io.BytesIO(struct.pack("<i", -5))
    assert result_values(generated.function(stream))[0] == -5


def test_a_path_is_opened_and_read_from_its_start(tmp_path: Path) -> None:
    generated = _read(I32, file=PATH)
    target = tmp_path / "numbers.bin"
    target.write_bytes(struct.pack(">ii", 11, 12))
    assert result_values(generated.function(target))[0] == 11


def test_the_end_of_the_file_keeps_the_complete_elements_and_is_error_4() -> None:
    generated = _read(I16, count=3, result=array("NumInt16"), with_error=True)
    stream = io.BytesIO(struct.pack(">hh", 1, 2) + b"\x00")
    data, error = result_values(generated.function(stream, None))
    assert data == [1, 2]
    assert (error.status, error.code) == (True, 4)
    assert error.source == "Read from Binary File"


def test_one_element_at_the_end_of_the_file_is_error_4() -> None:
    generated = _read(I32, with_error=True)
    data, error = result_values(generated.function(io.BytesIO(b"\x00\x01"), None))
    assert data == 0
    assert error.code == 4


def test_a_count_under_minus_one_is_an_error() -> None:
    generated = _read(I32, count=-2, result=array("NumInt32"), with_error=True)
    data, error = result_values(generated.function(io.BytesIO(bytes(8)), None))
    assert data == []
    assert error.status is True
    assert "-2" in error.message


# NI: "count is the number of data elements to read. Data elements can be bytes or
# instances of data type"; with the data type unwired the elements are bytes and
# the string read has no size ahead of it.


def test_a_string_data_type_wired_reads_count_strings_each_with_its_size() -> None:
    generated = _read(STR, count=2, result=array("String"))
    stream = io.BytesIO(struct.pack(">I", 2) + b"ab" + struct.pack(">I", 1) + b"cX")
    assert result_values(generated.function(stream))[0] == ["ab", "c"]
    assert stream.tell() == 11


def test_a_string_data_type_wired_against_bytes_without_size_is_error_4() -> None:
    generated = _read(STR, count=2, result=array("String"), with_error=True)
    data, error = result_values(generated.function(io.BytesIO(b"abcd"), None))
    assert error.code == 4
    assert data == []


def test_an_array_data_type_wired_reads_count_arrays_each_with_its_size() -> None:
    generated = _read(
        array("NumInt16"), count=2, result=array("NumInt16", rank=2)
    )
    stream = io.BytesIO(struct.pack(">I2h", 2, 1, 2) + struct.pack(">I1h", 1, 3))
    assert result_values(generated.function(stream))[0] == [[1, 2], [3]]


def test_an_unwired_data_type_reads_count_bytes_as_one_string() -> None:
    generated = _read(STR, count=5, type_wired=False)
    stream = io.BytesIO(b"hello world")
    assert result_values(generated.function(stream))[0] == "hello"
    assert stream.tell() == 5
    assert "struct" not in generated.source


def test_an_unwired_data_type_and_count_reads_one_byte() -> None:
    generated = _read(STR, type_wired=False)
    stream = io.BytesIO(b"\x00\x00\x00\x02hi")
    assert result_values(generated.function(stream))[0] == "\x00"
    assert stream.tell() == 1


def test_an_unwired_data_type_with_minus_one_reads_to_the_end_of_the_file() -> None:
    generated = _read(STR, count=-1, type_wired=False)
    stream = io.BytesIO(b"skip-the-rest\xff")
    stream.seek(5)
    assert result_values(generated.function(stream))[0] == "the-rest\xff"


def test_an_unwired_data_type_short_of_count_keeps_the_bytes_and_is_error_4() -> None:
    generated = _read(STR, count=10, type_wired=False, with_error=True)
    data, error = result_values(generated.function(io.BytesIO(b"abc"), None))
    assert data == "abc"
    assert (error.status, error.code) == (True, 4)


def test_an_unwired_data_type_with_a_count_under_minus_one_is_an_error() -> None:
    generated = _read(STR, count=-2, type_wired=False, with_error=True)
    data, error = result_values(generated.function(io.BytesIO(b"abc"), None))
    assert data == ""
    assert error.status is True
    assert "-2" in error.message


def test_an_unwired_file_is_a_gap() -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _read(I32, file_wired=False, gaps=gaps).function(None)
    assert [g.kind for g in gaps] == ["unsupported_node"]

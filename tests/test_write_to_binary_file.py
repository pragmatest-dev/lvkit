"""Write to Binary File (8078) per NI's "Write to Binary File Function" page: the
data is written flattened (the layout Flatten To String writes), in the byte order
the node says, with a size ahead of a top-level string or array unless ``prepend
array or string size?`` is FALSE; a path is created and its contents replaced, a
refnum is written at its current position; an unwired file (a dialog) is a gap.
Each test writes bytes and reads them back with Read from Binary File."""

from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType
from tests.native_vi import Generated, array, generate, node, scalar
from tests.test_read_from_binary_file import _read

I32 = scalar("NumInt32")
U16 = scalar("NumUInt16")
BOOL = scalar("Boolean")
DBL = scalar("NumFloat64")
STR = scalar("String")
FILE = scalar("Refnum")
PATH = scalar("Path")


def _write(
    data_type: LVType,
    *,
    order: int | None = None,
    prefix: bool | None = None,
    file: LVType = FILE,
    refnum_out: bool = False,
    file_wired: bool = True,
    gaps: list[Gap] | None = None,
) -> Generated:
    terminals: list[tuple[int, str, LVType | None]] = [
        (10, "input", data_type),
        (11, "input", file),
        (1, "output", BOOL),
    ]
    wires = [("fp:d", "wb:10"), ("wb:1", "fp:cancelled")]
    inputs: dict[str, LVType] = {"d": data_type, "f": file}
    outputs: dict[str, LVType] = {"cancelled": BOOL}
    constants: dict[str, tuple[object, LVType]] = {}
    if file_wired:
        wires.append(("fp:f", "wb:11"))
    if order is not None:
        terminals.append((9, "input", U16))
        wires.append(("c9", "wb:9"))
        constants["c9"] = (order, U16)
    if prefix is not None:
        terminals.append((5, "input", BOOL))
        wires.append(("c5", "wb:5"))
        constants["c5"] = (prefix, BOOL)
    if refnum_out:
        terminals.append((3, "output", FILE))
        wires.append(("wb:3", "fp:kept"))
        outputs["kept"] = FILE
    return generate(
        inputs,
        outputs,
        [node("wb", 8078, terminals)],
        wires,
        constants=constants,
        gaps=gaps,
    )


def _round_trip(
    data_type: LVType,
    value: object,
    *,
    order: int | None = None,
    count: int | None = None,
    result: LVType | None = None,
) -> tuple[bytes, object]:
    stream = io.BytesIO()
    _write(data_type, order=order).function(value, stream)
    written = stream.getvalue()
    stream.seek(0)
    read = _read(data_type, count=count, order=order, result=result)
    return written, read.function(stream).d  # type: ignore[attr-defined]


def test_an_i32_is_four_big_endian_bytes_and_reads_back() -> None:
    written, back = _round_trip(I32, 258)
    assert written == b"\x00\x00\x01\x02"
    assert back == 258


def test_an_array_of_doubles_carries_its_size_and_reads_back() -> None:
    values = [1.5, -2.0, 3.25]
    written, back = _round_trip(array("NumFloat64"), values)
    assert written == struct.pack(">I3d", 3, *values)
    assert back == values


def test_a_string_carries_its_size_and_reads_back() -> None:
    written, back = _round_trip(STR, "hello")
    assert written == b"\x00\x00\x00\x05hello"
    assert back == "hello"


def test_little_endian() -> None:
    written, back = _round_trip(I32, -2, order=2)
    assert written == struct.pack("<i", -2)
    assert back == -2


def test_prepend_size_false_writes_the_array_alone() -> None:
    stream = io.BytesIO()
    _write(array("NumFloat64"), prefix=False).function([1.0, 2.0], stream)
    assert stream.getvalue() == struct.pack(">2d", 1.0, 2.0)


def test_a_path_is_created_and_its_contents_replaced(tmp_path: Path) -> None:
    generated = _write(I32, file=PATH)
    target = tmp_path / "out.bin"
    result = generated.function(1, target)
    assert result.cancelled is False  # type: ignore[attr-defined]
    generated.function(2, target)
    assert target.read_bytes() == struct.pack(">i", 2)


def test_a_refnum_is_written_at_its_position_and_given_back() -> None:
    generated = _write(I32, refnum_out=True)
    stream = io.BytesIO()
    generated.function(1, stream)
    result = generated.function(2, stream)
    assert stream.getvalue() == struct.pack(">ii", 1, 2)
    assert result.kept is stream  # type: ignore[attr-defined]


def test_an_unwired_file_is_a_gap() -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _write(I32, file_wired=False, gaps=gaps).function(1, None)
    assert [g.kind for g in gaps] == ["unsupported_node"]

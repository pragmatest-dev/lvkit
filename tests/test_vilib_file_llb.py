"""The legacy ``<vilib>/Utility/file.llb`` VIs the corpus calls (Read Characters
From File, Write Characters To File, Open File+, Read File+ (string), Close File+):
each caller diagram is generated from the catalog entry and its Python is run.

The terminal indices and labels are those the JKI EasyXML callers' call nodes save
for each VI's pane. NI has no public page for these VIs; the behaviour comes from
the labels, NI's Set File Position page (pos mode: 0 start, 1 end, 2 current) and
the sources named in each entry's comments."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType
from lvkit.vilib_resolver import get_resolver
from tests.vilib_caller import (
    BOOL,
    ERROR,
    I32,
    PATH,
    STRING,
    primitive,
    run_catalog_call,
)

FILE_LLB = ["<vilib>", "Utility", "file.llb"]
REFNUM = primitive("Refnum")

READ_CHARACTERS_PANE = [
    (0, "input", PATH),
    (2, "input", I32),
    (3, "input", I32),
    (5, "input", BOOL),
    (6, "output", PATH),
    (7, "output", STRING),
    (8, "output", BOOL),
    (9, "output", I32),
]


def _read_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **arguments: Any
) -> Any:
    inputs: dict[str, tuple[int, LVType]] = {"path": (0, PATH)}
    for name, index, lv_type in (
        ("count", 2, I32),
        ("offset", 3, I32),
        ("convert", 5, BOOL),
    ):
        if name in arguments:
            inputs[name] = (index, lv_type)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*FILE_LLB, "Read Characters From File.vi"],
        pane=READ_CHARACTERS_PANE,
        inputs=inputs,
        outputs={
            "new_path": (6, PATH),
            "text": (7, STRING),
            "eof": (8, BOOL),
            "mark": (9, I32),
        },
        arguments=arguments,
        runtime_not_implemented=True,
    )
    return result


def test_read_characters_reads_the_whole_file_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"hello\nworld")
    result = _read_characters(tmp_path, monkeypatch, path=target)
    assert result.text == "hello\nworld"
    assert result.new_path == target
    assert result.mark == 11


def test_read_characters_counts_from_a_byte_offset_and_gives_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"0123456789")
    first = _read_characters(tmp_path, monkeypatch, path=target, count=4, offset=2)
    assert first.text == "2345"
    assert first.mark == 6
    assert first.eof is False
    # The mark is the next call's offset; a read past the end sets EOF?.
    rest = _read_characters(tmp_path, monkeypatch, path=target, count=9, offset=6)
    assert rest.text == "6789"
    assert rest.eof is True


def test_read_characters_converts_the_end_of_line_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"a\r\nb\rc\n")
    kept = _read_characters(tmp_path, monkeypatch, path=target)
    assert kept.text == "a\r\nb\rc\n"
    converted = _read_characters(tmp_path, monkeypatch, path=target, convert=True)
    assert converted.text == "a\nb\nc\n"


def test_read_characters_with_an_empty_path_names_the_value_it_got(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(NotImplementedError, match="file path is None"):
        _read_characters(tmp_path, monkeypatch, path=None)
    with pytest.raises(NotImplementedError, match="file dialog"):
        _read_characters(tmp_path, monkeypatch, path=Path(""))


def test_read_characters_of_a_missing_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(FileNotFoundError):
        _read_characters(tmp_path, monkeypatch, path=tmp_path / "missing.txt")


WRITE_CHARACTERS_PANE = [
    (0, "input", BOOL),
    (2, "output", PATH),
    (3, "input", BOOL),
    (4, "input", STRING),
    (5, "input", PATH),
]


def _write_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **arguments: Any
) -> Any:
    inputs: dict[str, tuple[int, LVType]] = {
        "text": (4, STRING),
        "path": (5, PATH),
    }
    for name, index in (("convert", 0), ("append", 3)):
        if name in arguments:
            inputs[name] = (index, BOOL)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*FILE_LLB, "Write Characters To File.vi"],
        pane=WRITE_CHARACTERS_PANE,
        inputs=inputs,
        outputs={"new_path": (2, PATH)},
        arguments=arguments,
        runtime_not_implemented=True,
    )
    return result


def test_write_characters_replaces_the_file_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.txt"
    target.write_bytes(b"old old old")
    result = _write_characters(tmp_path, monkeypatch, text="new", path=target)
    assert target.read_bytes() == b"new"
    assert result.new_path == target


def test_write_characters_appends_when_asked_and_creates_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.txt"
    _write_characters(tmp_path, monkeypatch, text="a", path=target, append=True)
    _write_characters(tmp_path, monkeypatch, text="b", path=target, append=True)
    assert target.read_bytes() == b"ab"


def test_write_characters_converts_line_ends_to_the_platform_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.txt"
    _write_characters(
        tmp_path, monkeypatch, text="a\r\nb\rc\n", path=target, convert=True
    )
    assert target.read_bytes() == f"a{os.linesep}b{os.linesep}c{os.linesep}".encode()
    _write_characters(tmp_path, monkeypatch, text="a\nb", path=target)
    assert target.read_bytes() == b"a\nb"


def test_write_characters_with_an_empty_path_names_the_value_it_got(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(NotImplementedError, match="file path is None"):
        _write_characters(tmp_path, monkeypatch, text="x", path=None)


OPEN_PANE = [
    (0, "output", ERROR),
    (1, "output", I32),
    (2, "output", PATH),
    (3, "output", REFNUM),
    (8, "input", ERROR),
    (9, "input", I32),
    (11, "input", PATH),
]


def _open_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **arguments: Any
) -> Any:
    inputs: dict[str, tuple[int, LVType]] = {"path": (11, PATH)}
    if "mode" in arguments:
        inputs["mode"] = (9, I32)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*FILE_LLB, "Open File+.vi"],
        pane=OPEN_PANE,
        inputs=inputs,
        outputs={"size": (1, I32), "path_out": (2, PATH), "refnum": (3, REFNUM)},
        arguments=arguments,
        runtime_not_implemented=True,
    )
    return result


def test_open_file_gives_the_size_the_path_and_a_refnum_that_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"12345")
    result = _open_file(tmp_path, monkeypatch, path=target)
    try:
        assert result.size == 5
        assert result.path_out == target
        assert result.refnum.read() == b"12345"
    finally:
        result.refnum.close()


def test_open_file_of_a_missing_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(FileNotFoundError):
        _open_file(tmp_path, monkeypatch, path=tmp_path / "missing.txt")


def test_open_file_with_an_empty_path_or_another_mode_names_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(NotImplementedError, match="file path is None"):
        _open_file(tmp_path, monkeypatch, path=None)
    target = tmp_path / "a.txt"
    target.write_bytes(b"x")
    with pytest.raises(NotImplementedError, match="open mode 0"):
        _open_file(tmp_path, monkeypatch, path=target, mode=0)


READ_FILE_PANE = [
    (0, "output", ERROR),
    (1, "output", I32),
    (2, "output", STRING),
    (3, "output", REFNUM),
    (4, "output", BOOL),
    (5, "input", BOOL),
    (6, "input", I32),
    (7, "input", BOOL),
    (9, "input", I32),
    (10, "input", I32),
    (11, "input", REFNUM),
]


def _read_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes, **arguments: Any
) -> Any:
    target = tmp_path / "a.txt"
    target.write_bytes(content)
    inputs: dict[str, tuple[int, LVType]] = {
        "refnum": (11, REFNUM),
        "count": (6, I32),
    }
    for name, index, lv_type in (
        ("convert", 5, BOOL),
        ("line_mode", 7, BOOL),
        ("offset", 9, I32),
        ("mode", 10, I32),
    ):
        if name in arguments:
            inputs[name] = (index, lv_type)
    with open(target, "rb") as handle:
        _, result = run_catalog_call(
            tmp_path,
            monkeypatch,
            [*FILE_LLB, "Read File+ (string).vi"],
            pane=READ_FILE_PANE,
            inputs=inputs,
            outputs={
                "mark": (1, I32),
                "text": (2, STRING),
                "eof": (4, BOOL),
            },
            arguments={"refnum": handle, **arguments},
            runtime_not_implemented=True,
        )
    return result


def test_read_file_reads_count_characters_from_the_current_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _read_file(tmp_path, monkeypatch, b"0123456789", count=4)
    assert result.text == "0123"
    assert result.mark == 4
    assert result.eof is False


def test_read_file_positions_by_mode_start_end_and_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"0123456789"
    start = _read_file(tmp_path, monkeypatch, content, count=2, offset=3, mode=0)
    assert (start.text, start.mark) == ("34", 5)
    end = _read_file(tmp_path, monkeypatch, content, count=2, offset=-4, mode=1)
    assert (end.text, end.mark) == ("67", 8)
    current = _read_file(tmp_path, monkeypatch, content, count=2, offset=5)
    assert (current.text, current.mark) == ("56", 7)


def test_read_file_past_the_end_gives_what_is_left_and_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _read_file(tmp_path, monkeypatch, b"abc", count=10)
    assert result.text == "abc"
    assert result.eof is True
    assert result.mark == 3


def test_read_file_in_line_mode_reads_one_line_with_its_line_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"one\r\ntwo\nthree\rfour"
    first = _read_file(tmp_path, monkeypatch, content, count=0, line_mode=True)
    assert first.text == "one\r\n"
    assert first.mark == 5
    assert first.eof is False
    second = _read_file(
        tmp_path, monkeypatch, content, count=0, line_mode=True, offset=5, mode=0
    )
    assert (second.text, second.mark, second.eof) == ("two\n", 9, False)
    third = _read_file(
        tmp_path, monkeypatch, content, count=0, line_mode=True, offset=9, mode=0
    )
    assert (third.text, third.mark, third.eof) == ("three\r", 15, False)


def test_read_file_in_line_mode_ends_at_the_file_end_without_a_line_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    last = _read_file(
        tmp_path, monkeypatch, b"a\nfour", count=0, line_mode=True, offset=2, mode=0
    )
    assert (last.text, last.mark, last.eof) == ("four", 6, True)
    nothing = _read_file(
        tmp_path, monkeypatch, b"a\n", count=0, line_mode=True, offset=2, mode=0
    )
    assert (nothing.text, nothing.eof) == ("", True)


def test_read_file_in_line_mode_stops_at_the_max_characters_of_the_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _read_file(
        tmp_path, monkeypatch, b"abcdefgh\nij\n", count=3, line_mode=True
    )
    assert (result.text, result.mark, result.eof) == ("abc", 3, False)


def test_read_file_converts_the_end_of_line_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"a\r\nb\rc"
    kept = _read_file(tmp_path, monkeypatch, content, count=-1)
    assert kept.text == "a\r\nb\rc"
    converted = _read_file(tmp_path, monkeypatch, content, count=-1, convert=True)
    assert converted.text == "a\nb\nc"


CLOSE_PANE = [
    (1, "input", BOOL),
    (3, "output", ERROR),
    (4, "input", REFNUM),
    (6, "output", I32),
    (7, "output", REFNUM),
]


def _close_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, handle: Any, **arguments: Any
) -> Any:
    inputs: dict[str, tuple[int, LVType]] = {"refnum": (4, REFNUM)}
    if "when" in arguments:
        inputs["when"] = (1, BOOL)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*FILE_LLB, "Close File+.vi"],
        pane=CLOSE_PANE,
        inputs=inputs,
        outputs={"size": (6, I32), "refnum_out": (7, REFNUM)},
        arguments={"refnum": handle, **arguments},
    )
    return result


def test_close_file_closes_it_and_gives_the_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"12345")
    handle = open(target, "rb")
    result = _close_file(tmp_path, monkeypatch, handle)
    assert result.size == 5
    assert handle.closed


def test_close_file_not_now_leaves_it_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"12345")
    handle = open(target, "rb")
    try:
        _close_file(tmp_path, monkeypatch, handle, when=False)
        assert not handle.closed
    finally:
        handle.close()


def test_a_neighbour_in_file_llb_without_an_entry_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError, match="terminal_mapping"):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*FILE_LLB, "Not In The Catalog.vi"],
            pane=[(0, "input", PATH), (1, "output", PATH)],
            inputs={"path": (0, PATH)},
            outputs={"out": (1, PATH)},
            arguments={"path": None},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert len(gaps) == 1


WRITE_FILE_PANE = [
    (0, "output", ERROR),
    (1, "output", I32),
    (3, "output", REFNUM),
    (4, "input", BOOL),
    (5, "input", STRING),
    (6, "input", ERROR),
    (7, "input", I32),
    (8, "input", I32),
    (9, "input", REFNUM),
]


def _write_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    **arguments: Any,
) -> tuple[Any, bytes]:
    target = tmp_path / "a.txt"
    target.write_bytes(content)
    inputs: dict[str, tuple[int, LVType]] = {
        "refnum": (9, REFNUM),
        "text": (5, STRING),
    }
    for name, index, lv_type in (
        ("convert", 4, BOOL),
        ("offset", 7, I32),
        ("mode", 8, I32),
    ):
        if name in arguments:
            inputs[name] = (index, lv_type)
    with open(target, "r+b") as handle:
        _, result = run_catalog_call(
            tmp_path,
            monkeypatch,
            [*FILE_LLB, "Write File+ (string).vi"],
            pane=WRITE_FILE_PANE,
            inputs=inputs,
            outputs={"mark": (1, I32)},
            arguments={"refnum": handle, **arguments},
            runtime_not_implemented=True,
        )
    return result, target.read_bytes()


def test_write_file_appends_by_default_because_the_mode_is_relative_to_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, content = _write_file(tmp_path, monkeypatch, b"abc", text="def")
    assert content == b"abcdef"
    assert result.mark == 6


def test_write_file_positions_by_mode_start_end_and_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start, content = _write_file(
        tmp_path, monkeypatch, b"0123456789", text="XY", offset=2, mode=0
    )
    assert (content, start.mark) == (b"01XY456789", 4)
    end, content = _write_file(
        tmp_path, monkeypatch, b"0123456789", text="XY", offset=-3, mode=1
    )
    assert (content, end.mark) == (b"0123456XY9", 9)
    current, content = _write_file(
        tmp_path, monkeypatch, b"0123456789", text="XY", offset=5, mode=2
    )
    assert (content, current.mark) == (b"01234XY789", 7)


def test_write_file_converts_the_end_of_line_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, content = _write_file(tmp_path, monkeypatch, b"", text="a\r\nb\n", convert=True)
    assert content == f"a{os.linesep}b{os.linesep}".encode()
    _, content = _write_file(tmp_path, monkeypatch, b"", text="a\r\nb\n")
    assert content == b"a\r\nb\n"


# The defaults each VI's own pane labels state (NI has no page for these VIs).
LABEL_DEFAULTS = [
    ("Read Characters From File", "number of characters (all:-1)", -1),
    ("Read Characters From File", "start of read offset (chars.:0)", 0),
    ("Read Characters From File", "convert eol? (no:F)", False),
    ("Write Characters To File", "convert eol? (no:F)", False),
    ("Write Characters To File", "append to file? (new file:F)", False),
    ("Open File+", "open mode (read-only:1)", 1),
    ("Open File+", "advisory dialog? (display:T)", True),
    ("Read File+ (string)", "convert eol? (no:F)", False),
    ("Read File+ (string)", "line mode (characters:F)", False),
    ("Read File+ (string)", "pos offset (bytes:0)", 0),
    ("Read File+ (string)", "pos mode (rel. to current:2)", 2),
    ("Close File+", "close when? (now:T)", True),
    ("Write File+ (string)", "convert eol? (no:F)", False),
    ("Write File+ (string)", "pos offset (bytes:0)", 0),
    ("Write File+ (string)", "pos mode (rel. to end:1)", 1),
]


@pytest.mark.parametrize(("vi_name", "label", "expected"), LABEL_DEFAULTS)
def test_an_input_records_the_default_its_label_states(
    vi_name: str, label: str, expected: object
) -> None:
    entry = get_resolver().resolve_by_name(f"{vi_name}.vi")
    assert entry is not None
    [terminal] = [t for t in entry.terminals if t.name == label]
    assert terminal.default == expected
    assert type(terminal.default) is type(expected)

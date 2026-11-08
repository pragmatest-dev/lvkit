"""Write to Text File (8080) per NI's "Write to Text File Function" page: a path is
opened (or created) and its previous contents replaced; a refnum is written at
its current position; ``refnum out`` of a path keeps the file open; an array of
strings is one EOL-ended line per element, a string has its line feeds converted
when Convert EOL (the node's ``objFlags`` bit 0x2000000) is on; an empty or
relative path is error 1430; an unwired file (a dialog) is a gap.

Each test builds a tiny diagram, generates its Python and runs it on real files.
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pytest

from tests.error_diagrams import (
    BOOL,
    ERR,
    FILE,
    PATH,
    REQUIRED,
    STR,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Write Lines.vi"
CONVERT_EOL = 0x2000000
_STRINGS = (
    '<TypeDesc Type="Array" Format="inline">'
    '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
    f'<TypeDesc TypeID="{STR}" /></TypeDesc>'
)


def _write(
    project: Project,  # noqa: F811
    *,
    file_type: int = PATH,
    text_type: int | None = STR,
    refnum_out: bool = False,
    flags: int = CONVERT_EOL,
    file_wired: bool = True,
) -> Package:
    """Write to Text File(file, text) with its error out and cancelled wired, and
    its refnum out when asked."""
    vi = Vi(NAME)
    target = vi.control("target", file_type, REQUIRED)
    if text_type is None:
        text_type = vi.add_type(_STRINGS)
    text = vi.control("text", text_type, REQUIRED)
    cancelled = vi.indicator("cancelled", BOOL)
    out = vi.error_out()
    node = vi.primitive(
        8080,
        [
            ("error out", 0, ERR, True),
            ("cancelled", 1, BOOL, True),
            ("refnum out", 3, FILE, True),
            ("error in", 8, ERR, False),
            ("text", 10, text_type, False),
            ("file", 11, file_type, False),
        ],
        obj_flags=flags,
    )
    if file_wired:
        vi.wire(target, node["file"])
    vi.wire(text, node["text"])
    vi.wire(node["cancelled"], cancelled)
    vi.wire(node["error out"], out)
    if refnum_out:
        kept = vi.indicator("kept", FILE)
        vi.wire(node["refnum out"], kept)
    project.add(vi)
    return project.generate(NAME)


def test_describe_and_lvnet_name_the_function(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    vi.primitive(
        8080,
        [
            ("error out", 0, ERR, True),
            ("text", 10, STR, False),
            ("file", 11, PATH, False),
        ],
    )
    project.add(vi)
    views = project.views(NAME)
    assert "Write to Text File [prim 8080]" in views.describe
    assert ": Write to Text File\n" in views.lvnet


def test_a_path_is_created_and_its_contents_replaced(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _write(project)
    assert "lvkit" not in package.source
    target = tmp_path / "out.txt"
    result = package.function(target, "first\nsecond")
    assert (result.error_out, result.cancelled) == (None, False)
    assert target.read_bytes() == f"first{os.linesep}second".encode()
    package.function(target, "new")
    assert target.read_bytes() == b"new"


def test_convert_eol_off_writes_the_string_as_it_is(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _write(project, flags=0x280)
    assert "os.linesep" not in package.source.split("def ")[-1]
    target = tmp_path / "out.json"
    package.function(target, '{\n  "a": 1\n}')
    assert target.read_bytes() == b'{\n  "a": 1\n}'


def test_convert_eol_on_writes_line_feeds_as_the_platform_eol(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _write(project)
    assert ".replace('\\n', os.linesep)" in package.source
    target = tmp_path / "out.txt"
    package.function(target, "a\nb\n")
    assert target.read_bytes() == f"a{os.linesep}b{os.linesep}".encode()


@pytest.mark.parametrize("flags", [CONVERT_EOL, 0x280], ids=["eol-on", "eol-off"])
def test_an_array_of_strings_is_one_eol_ended_line_per_element(
    project: Project,  # noqa: F811
    tmp_path: Path,
    flags: int,
) -> None:
    """The EOL follows each element whether Convert EOL is on or off."""
    package = _write(project, text_type=None, flags=flags)
    target = tmp_path / "lines.txt"
    package.function(target, ["one", "two", ""])
    eol = os.linesep
    assert target.read_bytes() == f"one{eol}two{eol}{eol}".encode()


def test_a_refnum_is_written_at_its_current_position(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _write(project, file_type=FILE, refnum_out=True)
    target = tmp_path / "ref.txt"
    with open(target, "w+b") as handle:
        handle.write(b"head|")
        result = package.function(handle, "tail")
        assert result.kept is handle
        assert result.error_out is None
        handle.seek(0)
        assert handle.read() == b"head|tail"


def test_a_text_mode_refnum_is_written_text(
    project: Project,  # noqa: F811
) -> None:
    package = _write(project, file_type=FILE)
    handle = io.StringIO()
    package.function(handle, "a\nb")
    assert handle.getvalue() == f"a{os.linesep}b"


def test_a_path_keeps_the_file_open_when_refnum_out_is_wired(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _write(project, refnum_out=True)
    target = tmp_path / "open.txt"
    result = package.function(target, "x")
    try:
        assert not result.kept.closed
        result.kept.write("y")
    finally:
        result.kept.close()
    assert target.read_bytes() == b"xy"


def test_an_empty_or_relative_path_is_error_1430(
    project: Project,  # noqa: F811
) -> None:
    package = _write(project)
    for bad in (Path("relative.txt"), Path("")):
        error = package.function(bad, "x").error_out
        assert (error.code, error.source) == (
            1430,
            "Write to Text File",
        )
    assert not Path("relative.txt").exists()


def test_a_missing_folder_is_error_7_and_refnum_out_is_the_default(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _write(project, refnum_out=True)
    result = package.function(tmp_path / "missing" / "f.txt", "x")
    assert result.error_out.code == 7
    assert result.kept is None
    assert result.cancelled is False


def test_no_file_is_a_gap(project: Project) -> None:  # noqa: F811
    package = _write(project, file_wired=False)
    reason = "Write to Text File with no file shows a file dialog"
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function(Path("/x"), "x")

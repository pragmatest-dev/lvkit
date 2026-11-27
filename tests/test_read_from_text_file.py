"""Read from Text File (primitive 8070) per NI's "Read from Text File Function"
page: ``file`` "can be a refnum or absolute file path. If it is a path, this
function opens the file specified by the path"; ``refnum out`` "is the refnum of the
file that the function read ... If you wire refnum out to another function, LabVIEW
assumes that the file is still in use until you close it". So ``refnum out`` is an
open file (the path opened, or the refnum that came in), and a Close File that takes
it closes that file. Each test builds a tiny diagram, generates its Python and runs
it on a real file.
"""

from __future__ import annotations

from pathlib import Path

from tests.error_diagrams import (
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

NAME = "Read Then Close.vi"


def _read_then_close(project: Project, file_type: int) -> Package:  # noqa: F811
    """Read from Text File(file) whose refnum out goes to a Close File; the VI
    returns the text and the path Close File reports."""
    vi = Vi(NAME)
    target = vi.control("target", file_type, REQUIRED)
    text = vi.indicator("text", STR)
    closed_path = vi.indicator("closed_path", PATH)
    out = vi.error_out()
    reading = vi.primitive(
        8070,
        [
            ("error out", 0, ERR, True),
            ("text", 2, STR, True),
            ("refnum out", 3, FILE, True),
            ("error in", 8, ERR, False),
            ("file", 11, file_type, False),
        ],
    )
    closing = vi.primitive(
        8052,
        [
            ("error in", 4, ERR, False),
            ("refnum", 7, FILE, False),
            ("path", 3, PATH, True),
            ("error out", 0, ERR, True),
        ],
    )
    vi.wire(target, reading["file"])
    vi.wire(reading["text"], text)
    vi.wire(reading["refnum out"], closing["refnum"])
    vi.wire(reading["error out"], closing["error in"])
    vi.wire(closing["path"], closed_path)
    vi.wire(closing["error out"], out)
    project.add(vi)
    return project.generate(NAME)


def test_the_refnum_out_of_a_path_is_the_opened_file_a_close_file_closes(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _read_then_close(project, PATH)
    target = tmp_path / "in.txt"
    target.write_text("hello", encoding="utf-8")
    result = package.function(target)
    assert result.error_out is None
    assert result.text == "hello"
    assert Path(result.closed_path) == target


def test_the_refnum_out_of_a_refnum_is_that_file(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _read_then_close(project, FILE)
    target = tmp_path / "in.txt"
    target.write_text("hello", encoding="utf-8")
    opened = open(target, encoding="utf-8")
    result = package.function(opened)
    assert result.text == "hello"
    assert opened.closed

"""Close File (primitive 8052) runs even when an error came in (NI: it "closes the
file regardless of whether an error occurred in a preceding operation"). A missing
refnum (an Open/Create/Replace File that failed or was cancelled leaves none) is an
invalid refnum: error 1, unless an error came in, which error out then keeps. The
generated function is run for each case."""

from __future__ import annotations

from pathlib import Path

from tests.error_diagrams import (
    ERR,
    FILE,
    PATH,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)


def _generate(project: Project) -> Package:  # noqa: F811
    vi = Vi("Err.vi")
    refnum = vi.control("refnum", FILE, REQUIRED)
    ein = vi.error_in()
    path = vi.indicator("path", PATH)
    eout = vi.error_out()
    closing = vi.primitive(
        8052,
        [
            ("error in", 4, ERR, False),
            ("refnum", 7, FILE, False),
            ("path", 3, PATH, True),
            ("error out", 0, ERR, True),
        ],
    )
    vi.wire(refnum, closing["refnum"])
    vi.wire(ein, closing["error in"])
    vi.wire(closing["path"], path)
    vi.wire(closing["error out"], eout)
    project.add(vi)
    return project.generate("Err.vi", None)


def test_a_file_refnum_is_closed(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project)
    opened = open(tmp_path / "f.bin", "wb")
    result = package.function(refnum=opened)
    assert opened.closed
    assert result.error_out is None
    assert result.path == opened.name


def test_a_missing_refnum_with_no_error_in_is_error_1(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project)
    result = package.function(refnum=None)
    assert result.error_out.code == 1
    assert result.error_out.status is True


def test_a_missing_refnum_with_an_error_in_keeps_that_error(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project)
    incoming = package.error(43, source="Open/Create/Replace File")
    result = package.function(refnum=None, error_in=incoming)
    assert result.error_out.code == 43
    assert result.error_out.source == "Open/Create/Replace File"


def test_a_file_is_closed_even_when_an_error_came_in(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _generate(project)
    opened = open(tmp_path / "g.bin", "wb")
    incoming = package.error(7)
    result = package.function(refnum=opened, error_in=incoming)
    assert opened.closed
    assert result.error_out.code == 7

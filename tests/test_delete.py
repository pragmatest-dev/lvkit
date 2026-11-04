"""Delete (8056, 1417) per NI's "Delete Function" page: a file is removed, an
empty folder is removed, a folder with contents only when ``entire hierarchy``
is TRUE; an empty or relative path is error 1430, a missing one 7, and any other
failure (a folder that is not empty) 6. An unwired path (a dialog) and
``confirm`` TRUE (a dialog) are gaps.

Each test builds a tiny diagram, generates its Python and runs it on real files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.error_diagrams import (
    BOOL,
    ERR,
    PATH,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Remove.vi"


def _delete(project: Project, *, hierarchy: bool | None) -> Package:  # noqa: F811
    """Delete(path, entire hierarchy: a control when ``hierarchy`` is None, else
    that constant) with its error out and deleted path wired."""
    vi = Vi(NAME)
    target = vi.control("target", PATH, REQUIRED)
    whole = vi.control("whole", BOOL, REQUIRED) if hierarchy is None else None
    deleted = vi.indicator("deleted", PATH)
    out = vi.error_out()
    node = vi.primitive(
        8056,
        [
            ("error out", 0, ERR, True),
            ("cancelled", 1, BOOL, True),
            ("deleted path", 3, PATH, True),
            ("error in", 8, ERR, False),
            ("confirm", 9, BOOL, False),
            ("entire hierarchy", 10, BOOL, False),
            ("path", 11, PATH, False),
        ],
    )
    vi.wire(target, node["path"])
    if whole is not None:
        vi.wire(whole, node["entire hierarchy"])
    elif hierarchy:
        vi.wire(vi.bool_constant(True), node["entire hierarchy"])
    vi.wire(node["deleted path"], deleted)
    vi.wire(node["error out"], out)
    project.add(vi)
    return project.generate(NAME)


def test_a_file_and_an_empty_folder_are_deleted(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _delete(project, hierarchy=False)
    assert "shutil" not in package.source
    file = tmp_path / "f.txt"
    file.write_text("x")
    folder = tmp_path / "empty"
    folder.mkdir()
    for target in (file, folder):
        result = package.function(target)
        assert (result.error_out, result.deleted) == (None, target)
        assert not target.exists()


def test_a_folder_with_contents_needs_entire_hierarchy(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _delete(project, hierarchy=None)
    folder = tmp_path / "full"
    folder.mkdir()
    (folder / "f.txt").write_text("x")
    error = package.function(folder, False).error_out
    assert (error.status, error.code, error.source) == (True, 6, "Delete")
    assert folder.exists()
    assert package.function(folder, True).error_out is None
    assert not folder.exists()


def test_the_errors_are_nis_codes(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _delete(project, hierarchy=True)
    assert "shutil.rmtree(" in package.source
    relative = package.function(Path("relative", "f.txt")).error_out
    assert (relative.code, relative.message) == (
        1430,
        "The path is empty or relative. You must use an absolute path.",
    )
    assert package.function(Path("")).error_out.code == 1430
    assert package.function(tmp_path / "missing").error_out.code == 7


@pytest.mark.parametrize(
    ("wire", "reason"),
    [
        ("no-path", "Delete with no path shows a file dialog"),
        ("confirm", "Delete with confirm TRUE shows a dialog"),
    ],
)
def test_a_dialog_is_a_gap(
    project: Project,  # noqa: F811
    wire: str,
    reason: str,
) -> None:
    vi = Vi(NAME)
    target = vi.control("target", PATH, REQUIRED)
    out = vi.error_out()
    node = vi.primitive(
        8056,
        [
            ("error out", 0, ERR, True),
            ("confirm", 9, BOOL, False),
            ("path", 11, PATH, False),
        ],
    )
    if wire == "confirm":
        vi.wire(target, node["path"])
        vi.wire(vi.bool_constant(True), node["confirm"])
    vi.wire(node["error out"], out)
    project.add(vi)
    package = project.generate(NAME)
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function(Path("/x"))


def test_the_delete_with_no_hierarchy_input(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    """1417: path in (3), deleted path out (1), error in and out."""
    vi = Vi(NAME)
    target = vi.control("target", PATH, REQUIRED)
    deleted = vi.indicator("deleted", PATH)
    out = vi.error_out()
    node = vi.primitive(
        1417,
        [
            ("error out", 0, ERR, True),
            ("dup path", 1, PATH, True),
            ("error in", 2, ERR, False),
            ("path", 3, PATH, False),
        ],
    )
    vi.wire(target, node["path"])
    vi.wire(node["dup path"], deleted)
    vi.wire(node["error out"], out)
    project.add(vi)
    package = project.generate(NAME)
    file = tmp_path / "f.txt"
    file.write_text("x")
    result = package.function(file)
    assert (result.error_out, result.deleted) == (None, file)
    assert not file.exists()
    assert package.function(file).error_out.code == 7


def test_a_failed_delete_gives_the_empty_path(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _delete(project, hierarchy=False)
    # The Path type's default, the empty path (plan decision 1).
    assert package.function(tmp_path / "missing").deleted == Path("")


@pytest.mark.parametrize("hierarchy", [True, None], ids=["constant", "run-time"])
def test_entire_hierarchy_deletes_a_file_too(
    project: Project,  # noqa: F811
    tmp_path: Path,
    hierarchy: bool | None,
) -> None:
    """Entire hierarchy matters only for a folder with contents; a file is
    deleted as it is without it."""
    package = _delete(project, hierarchy=hierarchy)
    file = tmp_path / "f.txt"
    file.write_text("x")
    args = (file,) if hierarchy else (file, True)
    assert package.function(*args).error_out is None
    assert not file.exists()

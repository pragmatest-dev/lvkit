"""Create Folder (8055) per NI's "Create Folder Function" page: it creates every
folder missing from the path; an existing file or folder is an error (10, "Duplicate
path"); an empty or relative path is error 1430; an unwired path (a dialog) is a
gap; ``created path`` is the path, or the Path default on a failure; ``cancelled`` is
FALSE.

Each test builds a tiny diagram, generates its Python and runs it on real folders.
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

NAME = "Make Folder.vi"
_PANE = [
    ("error out", 0, ERR, True),
    ("cancelled", 1, BOOL, True),
    ("created path", 3, PATH, True),
    ("error in", 8, ERR, False),
    ("path", 11, PATH, False),
]


def _create(project: Project) -> Package:  # noqa: F811
    """Create Folder(path) with its created path, cancelled and error out wired."""
    vi = Vi(NAME)
    target = vi.control("target", PATH, REQUIRED)
    created = vi.indicator("created", PATH)
    cancelled = vi.indicator("cancelled", BOOL)
    out = vi.error_out()
    node = vi.primitive(8055, _PANE)
    vi.wire(target, node["path"])
    vi.wire(node["created path"], created)
    vi.wire(node["cancelled"], cancelled)
    vi.wire(node["error out"], out)
    project.add(vi)
    return project.generate(NAME)


def test_describe_and_lvnet_name_the_function(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    vi.primitive(8055, _PANE)
    project.add(vi)
    views = project.views(NAME)
    assert "Create Folder [prim 8055]" in views.describe
    assert ": Create Folder\n" in views.lvnet


def test_every_missing_folder_of_the_path_is_created(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _create(project)
    assert "lvkit" not in package.source
    target = tmp_path / "a" / "b" / "c"
    result = package.function(target)
    assert (result.error_out, result.created, result.cancelled) == (
        None,
        target,
        False,
    )
    assert target.is_dir()


def test_an_existing_folder_or_file_is_error_10(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _create(project)
    folder = tmp_path / "there"
    folder.mkdir()
    file = tmp_path / "file.txt"
    file.write_text("x")
    for existing in (folder, file):
        result = package.function(existing)
        assert (result.error_out.status, result.error_out.code) == (True, 10)
        assert result.error_out.source == "Create Folder"
        assert result.created == Path("")
        assert result.cancelled is False
    assert file.read_text() == "x"


def test_an_empty_or_relative_path_is_error_1430(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _create(project)
    for bad in (Path("relative", "dir"), Path("")):
        error = package.function(bad).error_out
        assert (error.code, error.message) == (
            1430,
            "The path is empty or relative. You must use an absolute path.",
        )
    assert not Path("relative").exists()


def test_a_path_with_a_null_character_is_error_1(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _create(project)
    assert package.function(tmp_path / "a\0b").error_out.code == 1


def test_a_folder_under_a_file_is_an_error(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    package = _create(project)
    file = tmp_path / "file.txt"
    file.write_text("x")
    assert package.function(file / "sub").error_out.status is True


def test_an_error_in_skips_the_node(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    vi = Vi(NAME)
    target = vi.control("target", PATH, REQUIRED)
    error_in = vi.error_in()
    out = vi.error_out()
    node = vi.primitive(8055, _PANE)
    vi.wire(target, node["path"])
    vi.wire(error_in, node["error in"])
    vi.wire(node["error out"], out)
    project.add(vi)
    package = project.generate(NAME)
    target_folder = tmp_path / "skipped"
    incoming = package.error(5)
    assert package.function(target_folder, error_in=incoming).error_out.code == 5
    assert not target_folder.exists()


def test_no_path_is_a_gap(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    out = vi.error_out()
    node = vi.primitive(8055, _PANE)
    vi.wire(node["error out"], out)
    project.add(vi)
    package = project.generate(NAME)
    reason = "Create Folder with no path shows a folder dialog"
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function()

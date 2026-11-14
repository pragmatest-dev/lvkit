"""``generate_python`` returns the paths in the form the caller gave the output
folder, even where the files are written through Windows' extended-length form."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from lvkit import pipeline
from tests.test_ext_func_library import Project

_FAKE_PREFIX = "//"


def _fake_extended(path: Path) -> Path:
    return Path(_FAKE_PREFIX + str(path.absolute()).lstrip("/"))


def _fake_display(path: Path) -> Path:
    text = str(path)
    return Path("/" + text.lstrip("/")) if text.startswith(_FAKE_PREFIX) else path


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Project]:
    yield Project(tmp_path)
    for name in [m for m in sys.modules if m.split(".")[0] == "clfn_bdhb"]:
        del sys.modules[name]


def test_result_paths_have_no_extended_prefix(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows writes through its real extended-length form; elsewhere a stand-in
    # prefix plays that part.
    if sys.platform != "win32":
        monkeypatch.setattr(pipeline, "extended_length", _fake_extended)
        monkeypatch.setattr(pipeline, "display_path", _fake_display)
    vi = project.write_vi("c.*", ["", "..", "libs", "c.dll"], "swap")
    out = project.base / "out"

    result = pipeline.generate_python(vi, out)

    folder = out / result["vi_folder_name"]
    assert result["output_dir"] == folder
    assert result["vi_paths"]
    written = [p for _, p, _ in result["generated"] if p is not None]
    assert written
    for path in [*result["vi_paths"].values(), *written]:
        assert not str(path).startswith((_FAKE_PREFIX, "\\\\?\\"))
        assert path.relative_to(folder)
        assert path.exists()


def test_result_paths_match_a_relative_output_folder(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project.base)
    vi = project.write_vi("c.*", ["", "..", "libs", "c.dll"], "swap")

    result = pipeline.generate_python(vi, Path("out"))

    for path in result["vi_paths"].values():
        assert path.relative_to(Path("out")).parts


def test_an_output_folder_given_with_dots_is_written_where_it_points(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows takes an extended-length path as written, so a ".." left in it is
    # a folder name and the write fails (#308).
    work = project.base / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    vi = project.write_vi("c.*", ["", "..", "libs", "c.dll"], "swap")

    for given, where in [
        (Path("../out_b"), project.base / "out_b"),
        (Path("sub/../out_c"), work / "out_c"),
    ]:
        result = pipeline.generate_python(vi, given)

        folder = given / result["vi_folder_name"]
        assert result["output_dir"] == folder
        assert (where / result["vi_folder_name"]).is_dir()
        assert not (work / "sub").exists()
        for path in result["vi_paths"].values():
            assert path.relative_to(folder).parts
            # POSIX follows ".." only out of a folder that exists, so check the
            # file the path names, normalised as Windows reads it.
            assert Path(os.path.abspath(path)).is_file()

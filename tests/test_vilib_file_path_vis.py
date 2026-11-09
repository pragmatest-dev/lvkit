"""Application Directory, Check if File or Folder Exists and Get File Extension:
each caller diagram is generated from the catalog entry and its Python is run.

The terminal indices are those a corpus caller's call node saves for the VI's
pane (the actor-framework, JKI-VI-Tester and Pico examples); the behaviour is
what NI's public page for each VI documents."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from tests.vilib_caller import BOOL, ERROR, PATH, STRING, run_catalog_call

FILE_LLB = ["<vilib>", "Utility", "file.llb"]
LIBRARYN_LLB = ["<vilib>", "Utility", "libraryn.llb"]


def _application_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, Any]:
    return run_catalog_call(
        tmp_path,
        monkeypatch,
        [*FILE_LLB, "Application Directory.vi"],
        pane=[(0, "output", PATH)],
        inputs={},
        outputs={"directory": (0, PATH)},
        arguments={},
    )


def test_application_directory_is_the_folder_of_the_running_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(
        sys.modules["__main__"], "__file__", str(app / "main.py"), raising=False
    )
    source, result = _application_directory(tmp_path, monkeypatch)
    assert result.directory == app.resolve()
    assert "lvkit" not in source


def test_application_directory_without_a_program_file_is_the_working_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(sys.modules["__main__"], "__file__", raising=False)
    monkeypatch.chdir(tmp_path)
    _, result = _application_directory(tmp_path, monkeypatch)
    assert result.directory == tmp_path.resolve()


EXISTS_PANE = [
    (8, "input", ERROR),
    (11, "input", PATH),
    (0, "output", ERROR),
    (2, "output", BOOL),
    (3, "output", PATH),
]


def _exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: Path | None
) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LIBRARYN_LLB, "Check if File or Folder Exists.vi"],
        pane=EXISTS_PANE,
        inputs={"path": (11, PATH)},
        outputs={"exists": (2, BOOL), "dup_path": (3, PATH)},
        arguments={"path": path},
    )
    return result


def test_a_file_a_folder_and_a_missing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data"
    target.mkdir()
    (target / "a.txt").write_text("x", encoding="utf-8")

    file_result = _exists(tmp_path, monkeypatch, target / "a.txt")
    assert file_result.exists is True
    assert file_result.dup_path == target / "a.txt"
    assert _exists(tmp_path, monkeypatch, target).exists is True
    missing = _exists(tmp_path, monkeypatch, target / "nope.txt")
    assert missing.exists is False
    assert missing.dup_path == target / "nope.txt"


def test_an_empty_path_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NI: "If you wire an empty path to path, file or folder exists? returns TRUE".
    assert _exists(tmp_path, monkeypatch, Path("")).exists is True


def test_not_a_path_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _exists(tmp_path, monkeypatch, None).exists is False


EXTENSION_PANE = [
    (8, "input", ERROR),
    (11, "input", PATH),
    (0, "output", ERROR),
    (1, "output", STRING),
    (2, "output", STRING),
    (3, "output", PATH),
    (4, "output", STRING),
]


def _extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file: Path) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LIBRARYN_LLB, "Get File Extension.vi"],
        pane=EXTENSION_PANE,
        inputs={"file": (11, PATH)},
        outputs={
            "dup_file": (3, PATH),
            "lowercase": (2, STRING),
            "name": (1, STRING),
            "unmodified": (4, STRING),
        },
        arguments={"file": file},
    )
    return result


def test_the_extension_is_lowercased_and_the_unmodified_one_keeps_its_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = Path("/data/dir/Report.Final.TXT")
    result = _extension(tmp_path, monkeypatch, file)
    assert result.dup_file == file
    assert result.lowercase == "txt"
    assert result.unmodified == "TXT"
    # The name has no folder, period or extension, and keeps its case.
    assert result.name == "Report.Final"


def test_a_file_without_an_extension_has_an_empty_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _extension(tmp_path, monkeypatch, Path("/data/dir/README"))
    assert result.lowercase == ""
    assert result.unmodified == ""
    assert result.name == "README"


def test_a_period_in_a_folder_name_is_not_an_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _extension(tmp_path, monkeypatch, Path("/data/v1.2/README"))
    assert result.unmodified == ""
    assert result.name == "README"

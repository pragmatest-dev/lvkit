"""OpenG file and array VIs: each caller diagram is generated from the catalog
entry the recorded path names, and its Python is run.

The terminal indices are the connector pane positions of the VI in the OpenG
sample (a front-panel terminal's position is its pane index); the behaviour is
what each VI's diagram does (OpenG's own BSD code, read as a parsed graph) and
what NI's public page for the function it is built from documents."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from lvkit.models import LVType, LVTypeKind
from tests.vilib_caller import BOOL, I32, PATH, primitive, run_catalog_call

ARRAY = LVType(kind=LVTypeKind.ARRAY)
I64 = primitive("NumInt64")
FILE_LLB = ["<userlib>", "_OpenG.lib", "file", "file.llb"]
ARRAY_LLB = ["<userlib>", "_OpenG.lib", "array", "array.llb"]


def _relative_path_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vi: str,
    relative: str | None,
    unwired: bool = False,
) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*FILE_LLB, vi],
        pane=[(1, "input", PATH), (0, "output", PATH)],
        inputs={} if unwired else {"relative": (1, PATH)},
        outputs={"out": (0, PATH)},
        arguments={} if unwired else {"relative": relative},
    )
    return result.out


def test_application_directory_is_the_program_folder_joined_with_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(
        sys.modules["__main__"], "__file__", str(app / "main.py"), raising=False
    )
    out = _relative_path_call(
        tmp_path, monkeypatch, "Application Directory__ogtk.vi", "data/x.txt"
    )
    assert out == app.resolve() / "data" / "x.txt"


def test_application_directory_with_an_empty_path_is_the_program_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(
        sys.modules["__main__"], "__file__", str(app / "main.py"), raising=False
    )
    out = _relative_path_call(
        tmp_path, monkeypatch, "Application Directory__ogtk.vi", ""
    )
    assert out == app.resolve()


def test_temporary_directory_is_the_system_temporary_folder_joined_with_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _relative_path_call(
        tmp_path, monkeypatch, "Temporary Directory__ogtk.vi", "scratch"
    )
    assert out == Path(tempfile.gettempdir()) / "scratch"


def test_current_vis_path_is_the_path_of_the_vi_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*FILE_LLB, "Current VI's Path__ogtk.vi"],
        pane=[(1, "input", PATH), (0, "output", PATH)],
        inputs={},
        outputs={"out": (0, PATH)},
        arguments={},
    )
    # The caller is the module the call is written into: its own file.
    assert result.out == (tmp_path / "pkg" / "caller.py").resolve()
    assert "lvkit" not in source


def _search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    array: list[bool],
    element: bool,
    start: int | None = None,
) -> list[int]:
    inputs = {"array": (0, ARRAY), "element": (2, BOOL)}
    arguments: dict[str, Any] = {"array": array, "element": element}
    if start is not None:
        inputs["start"] = (3, I32)
        arguments["start"] = start
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*ARRAY_LLB, "Search 1D Array (Boolean)__ogtk.vi"],
        pane=[
            (0, "input", ARRAY),
            (2, "input", BOOL),
            (3, "input", I32),
            (7, "output", ARRAY),
        ],
        inputs=inputs,
        outputs={"found": (7, ARRAY)},
        arguments=arguments,
    )
    return result.found


def test_search_1d_array_returns_every_index_of_the_element(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _search(tmp_path, monkeypatch, [True, False, True, True], True) == [0, 2, 3]


def test_search_1d_array_starts_at_the_start_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = _search(tmp_path, monkeypatch, [True, False, True, True], True, start=1)
    assert found == [2, 3]


def test_search_1d_array_without_a_match_returns_no_indices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _search(tmp_path, monkeypatch, [True, True], False) == []


def test_remove_duplicates_keeps_the_first_of_each_and_reports_the_removed_indices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*ARRAY_LLB, "Remove Duplicates from 1D Array (Path)__ogtk.vi"],
        pane=[(0, "input", ARRAY), (1, "output", ARRAY), (2, "output", ARRAY)],
        inputs={"array": (0, ARRAY)},
        outputs={"unique": (1, ARRAY), "removed": (2, ARRAY)},
        arguments={"array": [Path("a"), Path("b"), Path("a"), Path("c"), Path("b")]},
    )
    assert result.unique == [Path("a"), Path("b"), Path("c")]
    assert result.removed == [2, 4]


def test_delete_elements_returns_the_rest_and_the_deleted_in_index_order_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*ARRAY_LLB, "Delete Elements from 1D Array (Path)__ogtk.vi"],
        pane=[
            (11, "input", ARRAY),
            (9, "input", ARRAY),
            (3, "output", ARRAY),
            (1, "output", ARRAY),
        ],
        inputs={"array": (11, ARRAY), "indices": (9, ARRAY)},
        outputs={"rest": (3, ARRAY), "deleted": (1, ARRAY)},
        arguments={"array": ["a", "b", "c", "d", "e"], "indices": [3, 0]},
    )
    assert result.rest == ["b", "c", "e"]
    assert result.deleted == ["d", "a"]


@pytest.mark.parametrize(
    ("by", "expected"),
    [(0, [[4, 5, 6], [1, 2, 3]]), (1, [[3, 2, 1], [6, 5, 4]])],
)
def test_reverse_2d_array_reverses_rows_or_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, by: int, expected: list[list[int]]
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*ARRAY_LLB, "Reverse 2D Array (I64)__ogtk.vi"],
        pane=[(3, "input", ARRAY), (2, "input", I32), (1, "output", ARRAY)],
        inputs={"array": (3, ARRAY), "by": (2, I32)},
        outputs={"flipped": (1, ARRAY)},
        arguments={"array": [[1, 2, 3], [4, 5, 6]], "by": by},
    )
    assert result.flipped == expected

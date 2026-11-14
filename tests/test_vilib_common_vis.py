"""Space Constant, High Resolution Relative Seconds, Create Directory Recursive,
Generate Temporary File Path, Color to RGB and RGB to Color: each caller diagram
is generated from the catalog entry and its Python is run.

The terminal indices are those a corpus caller's call node saves for the VI's
pane; the behaviour is what NI's public page for each VI documents."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from lvkit.models import LVType, LVTypeKind
from tests.vilib_caller import (
    DBL,
    ERROR,
    I32,
    PATH,
    STRING,
    U32,
    primitive,
    run_catalog_call,
)

ARRAY = LVType(kind=LVTypeKind.ARRAY)
I16 = primitive("NumInt16")
U8 = primitive("NumUInt8")
LIBRARYN = ["<vilib>", "Utility", "libraryn.llb"]
COLORCONV = ["<vilib>", "Utility", "colorconv.llb"]


def test_space_constant_is_one_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "dlg_ctls.llb", "Space Constant.vi"],
        pane=[(0, "output", STRING)],
        inputs={},
        outputs={"s": (0, STRING)},
        arguments={},
    )
    assert result.s == " "


def test_relative_seconds_never_go_backwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seconds = []
    for _ in range(2):
        _, result = run_catalog_call(
            tmp_path,
            monkeypatch,
            ["<vilib>", "Utility", "High Resolution Relative Seconds.vi"],
            pane=[(0, "output", DBL)],
            inputs={},
            outputs={"t": (0, DBL)},
            arguments={},
        )
        seconds.append(result.t)
    assert isinstance(seconds[0], float) and seconds[1] >= seconds[0]


CREATE_PANE = [
    (4, "input", ERROR),
    (5, "input", I16),
    (6, "input", I32),
    (7, "input", PATH),
    (0, "output", ERROR),
    (2, "output", ARRAY),
    (3, "output", PATH),
]


def _create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: Path
) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LIBRARYN, "Create Directory Recursive.vi"],
        pane=CREATE_PANE,
        inputs={"directory": (7, PATH)},
        outputs={"dup": (3, PATH), "created": (2, ARRAY)},
        arguments={"directory": directory},
    )
    return result


def test_the_directory_and_its_missing_parents_are_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a" / "b" / "c"
    result = _create(tmp_path, monkeypatch, target)
    assert target.is_dir()
    assert result.dup == target
    assert result.created == [target, target.parent, target.parent.parent]


def test_an_existing_directory_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NI: "If you specify a directory that already exists, this VI does nothing."
    result = _create(tmp_path, monkeypatch, tmp_path)
    assert result.created == []


def test_a_file_at_the_path_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "f").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _create(tmp_path, monkeypatch, tmp_path / "f")


TEMP_PANE = [
    (8, "input", ERROR),
    (10, "input", STRING),
    (0, "output", ERROR),
    (3, "output", PATH),
]


def _temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **arguments: str
) -> Path:
    wired = {"ext": (10, STRING)} if arguments else {}
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LIBRARYN, "Generate Temporary File Path.vi"],
        pane=TEMP_PANE,
        inputs=wired,
        outputs={"path": (3, PATH)},
        arguments=arguments,
    )
    path: Path = result.path
    return path


def test_a_temporary_path_is_new_unused_and_in_the_temporary_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _temp(tmp_path, monkeypatch)
    second = _temp(tmp_path, monkeypatch)
    assert first != second
    assert first.parent == Path(tempfile.gettempdir())
    assert not first.exists()  # NI: the VI does not create the file
    assert first.suffix == ".tmp"  # the documented default extension


def test_a_wired_extension_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _temp(tmp_path, monkeypatch, ext="log").suffix == ".log"


def test_color_to_rgb_splits_a_color(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*COLORCONV, "Color to RGB.vi"],
        pane=[
            (0, "input", U32),
            (1, "output", U32),
            (2, "output", U8),
            (3, "output", U8),
            (4, "output", U8),
        ],
        inputs={"color": (0, U32)},
        outputs={"resolved": (1, U32), "r": (2, U8), "g": (3, U8), "b": (4, U8)},
        arguments={"color": 0x00FF8040},
    )
    assert (result.resolved, result.r, result.g, result.b) == (0xFF8040, 255, 128, 64)


def test_rgb_to_color_joins_the_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*COLORCONV, "RGB to Color.vi"],
        pane=[
            (0, "output", U32),
            (1, "input", U8),
            (2, "input", U8),
            (3, "input", U8),
        ],
        inputs={"r": (1, U8), "g": (2, U8), "b": (3, U8)},
        outputs={"color": (0, U32)},
        arguments={"r": 255, "g": 128, "b": 64},
    )
    assert result.color == 0xFF8040

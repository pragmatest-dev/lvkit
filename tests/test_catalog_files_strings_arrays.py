"""vi.lib file, path, string, array, zip and system VIs: each caller diagram is
generated from the catalog entry the recorded path names (the connector pane
numbers are the ones the corpus callers record), and its Python is run.

The behaviour is what NI's public page for the VI documents; a case a page leaves
out is not written into the entry, so the VI stays a gap or raises when it runs."""

from __future__ import annotations

import math
import ntpath
import os
import pathlib
import shlex
import sys
import time
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.vilib_resolver import VILibResolver
from tests.test_driver_catalog_calls import _generate
from tests.vilib_caller import (
    BOOL,
    DBL,
    I32,
    PATH,
    STRING,
    run_catalog_call,
)

ARRAY = LVType(kind=LVTypeKind.ARRAY)
# LabVIEW's error cluster: exactly status, code and source.
ERROR_CLUSTER = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=I32),
        ClusterField(name="source", type=STRING),
    ],
)
LIBRARYN = ["<vilib>", "Utility", "libraryn.llb"]
ADVANCED_STRING = ["<vilib>", "AdvancedString"]
ZIP = ["<vilib>", "zip"]
OPENG_ARRAY = ["<userlib>", "_OpenG.lib", "array", "array.llb"]
OPENG_FILE = ["<userlib>", "_OpenG.lib", "file", "file.llb"]


def _call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded: list[str],
    pane: list[Any],
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    arguments: dict[str, Any],
    **options: Any,
) -> Any:
    _, result = run_catalog_call(
        tmp_path, monkeypatch, recorded, pane, inputs, outputs, arguments, **options
    )
    return result


# --- Recursive File List ------------------------------------------------------

FILE_LIST_RECORDED = [*LIBRARYN, "Recursive File List.vi"]
FILE_LIST_PANE = [
    (0, "output", ERROR_CLUSTER),
    (1, "output", I32),
    (2, "output", ARRAY),
    (3, "output", PATH),
    (4, "input", ARRAY),
    (5, "output", ARRAY),
    (6, "input", STRING),
    (8, "input", ERROR_CLUSTER),
    (9, "input", BOOL),
    (10, "input", BOOL),
    (11, "input", PATH),
]
FILE_LIST_OUTPUTS = {
    "count": (1, I32),
    "files": (2, ARRAY),
    "folder": (3, PATH),
    "folders": (5, ARRAY),
    "error": (0, ERROR_CLUSTER),
}


def _tree(root: Path) -> None:
    for name in ("a.vi", "b.txt", "sub/c.vi", "sub/d.txt", "skip/e.vi", "Z/f.vi"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")


def _file_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    folder: Path,
    pattern: str | None = None,
    exclude: list[str] | None = None,
    llbs_as_folders: bool | None = None,
    continue_on_error: bool | None = None,
) -> Any:
    inputs: dict[str, Any] = {"path": (11, PATH)}
    arguments: dict[str, Any] = {"path": folder}
    for name, index, kind, value in (
        ("pattern", 6, STRING, pattern),
        ("exclude", 4, ARRAY, exclude),
        ("llbs", 10, BOOL, llbs_as_folders),
        ("cont", 9, BOOL, continue_on_error),
    ):
        if value is not None:
            inputs[name] = (index, kind)
            arguments[name] = value
    return _call(
        tmp_path,
        monkeypatch,
        FILE_LIST_RECORDED,
        FILE_LIST_PANE,
        inputs,
        FILE_LIST_OUTPUTS,
        arguments,
        runtime_not_implemented=True,
    )


def test_recursive_file_list_lists_files_matching_the_pattern_in_sorted_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    _tree(root)
    result = _file_list(tmp_path, monkeypatch, root, "*.vi", ["SKIP"])
    # Each level's names are sorted ("Z" before "sub"); the folder "skip" is
    # excluded without regard to case, neither entered nor listed.
    assert result.files == [root / "a.vi", root / "Z" / "f.vi", root / "sub" / "c.vi"]
    assert result.folders == [root / "Z", root / "sub"]
    assert result.count == 3
    assert result.folder == root
    assert result.error is None


def test_recursive_file_list_without_pattern_or_exclusions_lists_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    _tree(root)
    result = _file_list(tmp_path, monkeypatch, root)
    assert result.files == [
        root / "a.vi",
        root / "b.txt",
        root / "Z" / "f.vi",
        root / "skip" / "e.vi",
        root / "sub" / "c.vi",
        root / "sub" / "d.txt",
    ]
    assert result.folders == [root / "Z", root / "skip", root / "sub"]
    assert result.count == 6


def test_recursive_file_list_counts_an_llb_as_a_file_unless_treated_as_a_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    _tree(root)
    (root / "lib.llb").write_text("x", encoding="utf-8")
    listed = _file_list(tmp_path, monkeypatch, root, llbs_as_folders=False)
    assert root / "lib.llb" in listed.files
    with pytest.raises(NotImplementedError, match=r"lib\.llb"):
        _file_list(tmp_path, monkeypatch, root, llbs_as_folders=True)


def test_recursive_file_list_of_an_llb_path_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llb = tmp_path / "lib.llb"
    llb.write_text("x", encoding="utf-8")
    with pytest.raises(NotImplementedError, match=r"lib\.llb"):
        _file_list(tmp_path, monkeypatch, llb)


def test_recursive_file_list_of_a_missing_folder_is_an_error_out_or_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing"
    stopped = _file_list(tmp_path, monkeypatch, missing, continue_on_error=False)
    assert stopped.error is not None
    assert stopped.error.code == 7
    assert stopped.files == []
    assert stopped.count == 0
    carried_on = _file_list(tmp_path, monkeypatch, missing, continue_on_error=True)
    assert carried_on.files == []
    assert carried_on.error is None


# --- Compare Two Paths --------------------------------------------------------

COMPARE_RECORDED = [*LIBRARYN, "Compare Two Paths.vi"]


def _compare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_1: Any, path_2: Any
) -> Any:
    return _call(
        tmp_path,
        monkeypatch,
        COMPARE_RECORDED,
        [
            (0, "output", ERROR_CLUSTER),
            (1, "output", BOOL),
            (2, "output", PATH),
            (3, "output", PATH),
            (8, "input", ERROR_CLUSTER),
            (10, "input", PATH),
            (11, "input", PATH),
        ],
        {"one": (11, PATH), "two": (10, PATH)},
        {
            "relative": (3, PATH),
            "common": (2, PATH),
            "same_level": (1, BOOL),
            "error": (0, ERROR_CLUSTER),
        },
        {"one": path_1, "two": path_2},
    )


def test_compare_two_paths_reproduces_ni_example_with_windows_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "path", ntpath)
    monkeypatch.setattr(pathlib, "Path", PureWindowsPath)
    result = _compare(
        tmp_path,
        monkeypatch,
        PureWindowsPath(r"C:\folder\sub\test.txt"),
        PureWindowsPath(r"C:\folder\sub2\dir\test2.doc"),
    )
    assert result.relative == PureWindowsPath(r"..\..\sub2\dir\test2.doc")
    assert result.common == PureWindowsPath(r"C:\folder")
    assert result.same_level is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_compare_two_paths_reproduces_ni_example_with_posix_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _compare(
        tmp_path,
        monkeypatch,
        Path("/folder/sub/test.txt"),
        Path("/folder/sub2/dir/test2.doc"),
    )
    assert result.relative == Path("../../sub2/dir/test2.doc")
    assert result.common == Path("/folder")
    assert result.same_level is False
    assert result.error is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_compare_two_paths_is_relative_only_at_or_below_path_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NI's example is FALSE ("you have to go up two levels"); the corpus caller
    # Calculate Relative Path from Directory.vi passes a folder as Path 1 and
    # treats FALSE as "not inside that folder", so any ".." step is FALSE.
    inside = _compare(
        tmp_path, monkeypatch, Path("/folder/sub"), Path("/folder/sub/b.txt")
    )
    assert inside.relative == Path("b.txt")
    assert inside.same_level is True
    below = _compare(
        tmp_path, monkeypatch, Path("/folder/sub"), Path("/folder/sub/x/y.txt")
    )
    assert below.relative == Path("x/y.txt")
    assert below.same_level is True
    beside = _compare(
        tmp_path, monkeypatch, Path("/folder/sub"), Path("/folder/other/c.txt")
    )
    assert beside.relative == Path("../other/c.txt")
    assert beside.same_level is False


def test_compare_two_paths_on_different_drives_is_an_error_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "path", ntpath)
    monkeypatch.setattr(pathlib, "Path", PureWindowsPath)
    result = _compare(
        tmp_path,
        monkeypatch,
        PureWindowsPath(r"C:\folder\a.txt"),
        PureWindowsPath(r"D:\folder\b.txt"),
    )
    assert result.error is not None
    assert result.error.code == 42


# --- Delimited string and string array ----------------------------------------


def _delimited_to_array(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    delimiter: str | None,
) -> list[str]:
    inputs: dict[str, Any] = {"text": (11, STRING)}
    arguments: dict[str, Any] = {"text": text}
    if delimiter is not None:
        inputs["delimiter"] = (10, STRING)
        arguments["delimiter"] = delimiter
    return _call(
        tmp_path,
        monkeypatch,
        [*ADVANCED_STRING, "Delimited String to 1D String Array.vi"],
        [(3, "output", ARRAY), (10, "input", STRING), (11, "input", STRING)],
        inputs,
        {"array": (3, ARRAY)},
        arguments,
    ).array


def _array_to_delimited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    array: list[str],
    delimiter: str | None,
) -> str:
    inputs: dict[str, Any] = {"array": (11, ARRAY)}
    arguments: dict[str, Any] = {"array": array}
    if delimiter is not None:
        inputs["delimiter"] = (10, STRING)
        arguments["delimiter"] = delimiter
    return _call(
        tmp_path,
        monkeypatch,
        [*ADVANCED_STRING, "1D String Array to Delimited String.vi"],
        [(3, "output", STRING), (10, "input", STRING), (11, "input", ARRAY)],
        inputs,
        {"text": (3, STRING)},
        arguments,
    ).text


def test_delimited_string_splits_at_the_delimiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _delimited_to_array(tmp_path, monkeypatch, "Lib:Class.lvclass", ":") == [
        "Lib",
        "Class.lvclass",
    ]


def test_delimited_string_without_a_delimiter_splits_at_tabs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _delimited_to_array(tmp_path, monkeypatch, "a\tb\tc", None) == [
        "a",
        "b",
        "c",
    ]


def test_string_array_is_joined_with_the_delimiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _array_to_delimited(tmp_path, monkeypatch, ["Lib", "Class"], ":") == (
        "Lib:Class"
    )


def test_string_array_without_a_delimiter_is_joined_with_tabs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _array_to_delimited(tmp_path, monkeypatch, ["a", "b"], None) == "a\tb"


# --- Remove Duplicates From 1D Array.vim --------------------------------------


def _remove_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, array: list[Any]
) -> list[Any]:
    return _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "Array", "Remove Duplicates From 1D Array.vim"],
        [(3, "output", ARRAY), (11, "input", ARRAY)],
        {"array": (11, ARRAY)},
        {"unique": (3, ARRAY)},
        {"array": array},
    ).unique


def test_remove_duplicates_keeps_the_first_of_each_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _remove_duplicates(tmp_path, monkeypatch, [3, 1, 3, 2, 1]) == [3, 1, 2]
    assert _remove_duplicates(tmp_path, monkeypatch, []) == []


def test_remove_duplicates_compares_elements_that_cannot_be_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _remove_duplicates(tmp_path, monkeypatch, [[1], [2], [1]]) == [[1], [2]]


def test_openg_remove_duplicates_keeps_its_own_entry() -> None:
    entry = VILibResolver().resolve_by_name("Remove Duplicates from 1D Array__ogtk.vi")
    assert entry is not None
    assert [t.name for t in entry.terminals if t.index == 2] == [
        "indices of removed elements"
    ]


# --- Mean (DBL) ---------------------------------------------------------------


def _mean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: list[float]) -> Any:
    return _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "Analysis", "baseanly.llb", "Mean (DBL).vi"],
        [(0, "input", ARRAY), (1, "output", DBL), (2, "output", I32)],
        {"x": (0, ARRAY)},
        {"mean": (1, DBL), "error": (2, I32)},
        {"x": values},
    )


def test_mean_is_the_average_of_the_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _mean(tmp_path, monkeypatch, [1.0, 2.0, 4.5])
    assert result.mean == 2.5
    assert result.error == 0


def test_mean_of_an_empty_sequence_is_nan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _mean(tmp_path, monkeypatch, [])
    assert math.isnan(result.mean)
    assert result.error == 0


# --- System Exec --------------------------------------------------------------

SYSTEM_EXEC_PANE = [
    (0, "output", ERROR_CLUSTER),
    (1, "output", I32),
    (2, "output", STRING),
    (3, "output", STRING),
    (6, "input", BOOL),
    (7, "input", BOOL),
    (8, "input", ERROR_CLUSTER),
    (9, "input", PATH),
    (10, "input", STRING),
    (11, "input", STRING),
]

pytestmark_posix = pytest.mark.skipif(
    sys.platform == "win32", reason="commands are written for a POSIX shell"
)


def _system_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    stdin: str | None = None,
    folder: Path | None = None,
    wait: bool | None = None,
) -> Any:
    inputs: dict[str, Any] = {"command": (11, STRING)}
    arguments: dict[str, Any] = {"command": command}
    for name, index, kind, value in (
        ("stdin", 10, STRING, stdin),
        ("folder", 9, PATH, folder),
        ("wait", 7, BOOL, wait),
    ):
        if value is not None:
            inputs[name] = (index, kind)
            arguments[name] = value
    return _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "Platform", "system.llb", "System Exec.vi"],
        SYSTEM_EXEC_PANE,
        inputs,
        {
            "code": (1, I32),
            "stderr": (2, STRING),
            "stdout": (3, STRING),
            "error": (0, ERROR_CLUSTER),
        },
        arguments,
    )


@pytestmark_posix
def test_system_exec_returns_output_error_output_and_return_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import sys; print('out'); sys.stderr.write('err'); sys.exit(3)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    result = _system_exec(tmp_path, monkeypatch, command)
    assert result.stdout == "out\n"
    assert result.stderr == "err"
    # A non-zero exit code is the command's own result, not an error.
    assert result.code == 3
    assert result.error is None


@pytestmark_posix
def test_system_exec_passes_standard_input_and_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _system_exec(tmp_path, monkeypatch, "cat", stdin="abc").stdout == "abc"
    folder = tmp_path / "work"
    folder.mkdir()
    printed = _system_exec(tmp_path, monkeypatch, "pwd", folder=folder).stdout
    assert Path(printed.strip()).resolve() == folder.resolve()


@pytestmark_posix
def test_system_exec_without_waiting_returns_empty_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "marker"
    result = _system_exec(
        tmp_path, monkeypatch, f"echo hi > {shlex.quote(str(marker))}", wait=False
    )
    assert (result.stdout, result.stderr) == ("", "")
    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.read_text(encoding="utf-8") == "hi\n"


@pytestmark_posix
def test_system_exec_with_a_missing_working_directory_is_an_error_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _system_exec(tmp_path, monkeypatch, "true", folder=tmp_path / "none")
    assert result.error is not None
    assert result.error.code == 7


# --- Zip ----------------------------------------------------------------------


def _new_zip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    confirm: bool | None = None,
) -> Any:
    inputs: dict[str, Any] = {"target": (7, PATH)}
    arguments: dict[str, Any] = {"target": target}
    if confirm is not None:
        inputs["confirm"] = (5, BOOL)
        arguments["confirm"] = confirm
    return _call(
        tmp_path,
        monkeypatch,
        [*ZIP, "New Zip File.vi"],
        [
            (0, "output", ERROR_CLUSTER),
            (1, "output", BOOL),
            (3, "output", I32),
            (4, "input", ERROR_CLUSTER),
            (5, "input", BOOL),
            (7, "input", PATH),
        ],
        inputs,
        {"cancelled": (1, BOOL), "error": (0, ERROR_CLUSTER)},
        arguments,
        runtime_not_implemented=True,
    )


def _zip_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    source: Path,
    inside: str,
    comment: str,
) -> None:
    """New Zip File, Add File to Zip and Close Zip File in one caller diagram's
    worth of calls: the zip reference each returns is the open ``ZipFile``."""
    new = _call(
        tmp_path,
        monkeypatch,
        [*ZIP, "New Zip File.vi"],
        [(3, "output", I32), (7, "input", PATH)],
        {"target": (7, PATH)},
        {"archive": (3, I32)},
        {"target": target},
        runtime_not_implemented=True,
    )
    assert isinstance(new.archive, zipfile.ZipFile)
    added = _call(
        tmp_path,
        monkeypatch,
        [*ZIP, "Add File to Zip.vi"],
        [
            (3, "output", I32),
            (9, "input", PATH),
            (10, "input", PATH),
            (11, "input", I32),
        ],
        {"archive": (11, I32), "source": (10, PATH), "inside": (9, PATH)},
        {"zip_out": (3, I32)},
        {"archive": new.archive, "source": source, "inside": Path(inside)},
    )
    assert added.zip_out is new.archive
    _call(
        tmp_path,
        monkeypatch,
        [*ZIP, "Close Zip File.vi"],
        [(6, "input", STRING), (7, "input", I32)],
        {"archive": (7, I32), "comments": (6, STRING)},
        {},
        {"archive": new.archive, "comments": comment},
    )


def test_new_add_and_close_zip_file_write_a_zip_with_the_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "data.txt"
    source.write_text("payload", encoding="utf-8")
    target = tmp_path / "out.zip"
    _zip_a_file(tmp_path, monkeypatch, target, source, "folder/inner.txt", "note")
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["folder/inner.txt"]
        assert archive.read("folder/inner.txt") == b"payload"
        assert archive.comment == b"note"


def test_new_zip_file_overwrites_an_existing_file_unless_asked_to_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "old.zip"
    target.write_text("not a zip", encoding="utf-8")
    created = _new_zip(tmp_path, monkeypatch, target)
    assert created.cancelled is False
    created_with_false = _new_zip(tmp_path, monkeypatch, target, confirm=False)
    assert created_with_false.cancelled is False
    with pytest.raises(NotImplementedError, match="dialog"):
        _new_zip(tmp_path, monkeypatch, target, confirm=True)
    # Nothing exists to confirm for a new file: no dialog is asked.
    assert _new_zip(tmp_path, monkeypatch, tmp_path / "new.archive", confirm=True)


def _unzip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive: Path,
    target: Path | None = None,
    preview: bool | None = None,
) -> Any:
    inputs: dict[str, Any] = {"archive": (11, PATH)}
    arguments: dict[str, Any] = {"archive": archive}
    for name, index, kind, value in (
        ("target", 9, PATH, target),
        ("preview", 10, BOOL, preview),
    ):
        if value is not None:
            inputs[name] = (index, kind)
            arguments[name] = value
    return _call(
        tmp_path,
        monkeypatch,
        [*ZIP, "Unzip.vi"],
        [
            (0, "output", ERROR_CLUSTER),
            (2, "output", STRING),
            (3, "output", ARRAY),
            (8, "input", ERROR_CLUSTER),
            (9, "input", PATH),
            (10, "input", BOOL),
            (11, "input", PATH),
        ],
        inputs,
        {"files": (3, ARRAY), "error": (0, ERROR_CLUSTER)},
        arguments,
        runtime_not_implemented=True,
    )


def _make_zip(archive: Path) -> None:
    with zipfile.ZipFile(archive, "w") as made:
        made.writestr("a.txt", "A")
        made.writestr("dir/b.txt", "B")


def test_unzip_extracts_into_the_target_and_lists_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "x.zip"
    _make_zip(archive)
    target = tmp_path / "out"
    result = _unzip(tmp_path, monkeypatch, archive, target)
    assert result.files == [target / "a.txt", target / "dir" / "b.txt"]
    assert (target / "dir" / "b.txt").read_text(encoding="utf-8") == "B"
    assert result.error is None


def test_unzip_without_a_target_extracts_next_to_the_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "here"
    folder.mkdir()
    archive = folder / "x.zip"
    _make_zip(archive)
    result = _unzip(tmp_path, monkeypatch, archive)
    assert result.files == [folder / "a.txt", folder / "dir" / "b.txt"]
    assert (folder / "a.txt").read_text(encoding="utf-8") == "A"


def test_unzip_refuses_a_password_protected_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "locked.zip"
    with zipfile.ZipFile(archive, "w") as made:
        made.writestr("a.txt", "A")
        made.filelist[0].flag_bits |= 0x1
    result = _unzip(tmp_path, monkeypatch, archive, tmp_path / "out")
    assert result.error is not None
    assert "password protected" in result.error.message
    assert not (tmp_path / "out" / "a.txt").exists()


def test_unzip_of_a_file_that_is_no_zip_is_an_error_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_text("not a zip", encoding="utf-8")
    result = _unzip(tmp_path, monkeypatch, archive, tmp_path / "out")
    assert result.error is not None


def test_unzip_preview_only_is_not_written_for_lack_of_a_documented_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "x.zip"
    _make_zip(archive)
    with pytest.raises(NotImplementedError, match="Preview"):
        _unzip(tmp_path, monkeypatch, archive, preview=True)


# --- VIs without NI text for the case the call needs stay gaps ----------------


def test_dflt_data_dir_is_found_by_its_file_name_and_stays_a_gap() -> None:
    entry = VILibResolver().resolve_recorded(
        "<vilib>/Utility/file.llb/Dflt Data Dir.vi"
    )
    assert entry is not None
    assert entry.python_code is None


def test_librarian_get_info_and_qualified_name_array_to_single_string_are_gaps(
    tmp_path: Path,
) -> None:
    resolver = VILibResolver()
    for path in (
        "<vilib>/Utility/libraryn.llb/Librarian Get Info.vi",
        "<vilib>/Utility/LVClass/Qualified Name Array To Single String.vi",
    ):
        entry = resolver.resolve_recorded(path)
        assert entry is None or entry.python_code is None
    gaps: list[Gap] = []
    _generate(
        tmp_path,
        name="Librarian Get Info.vi",
        path="<vilib>/Utility/libraryn.llb/Librarian Get Info.vi",
        selector=None,
        terminals=[
            (11, "input", "path", PATH, "path"),
            (3, "output", "dup path", PATH, ""),
        ],
        gaps=gaps,
    )
    assert len(gaps) == 1


# --- OpenG: Sort 1D Array and Current VI's Parent Directory -------------------


def _sort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    array: list[Any],
    order: int | None = None,
    method: int | None = None,
) -> Any:
    inputs: dict[str, Any] = {"array": (11, ARRAY)}
    arguments: dict[str, Any] = {"array": array}
    pane: list[Any] = [(11, "input", ARRAY), (3, "output", ARRAY), (2, "output", ARRAY)]
    for name, index, value in (("order", 9, order), ("method", 7, method)):
        if value is not None:
            inputs[name] = (index, I32)
            arguments[name] = value
            pane.append((index, "input", I32))
    return _call(
        tmp_path,
        monkeypatch,
        [*OPENG_ARRAY, f"Sort 1D Array ({variant})__ogtk.vi"],
        pane,
        inputs,
        {"ordered": (3, ARRAY), "pointers": (2, ARRAY)},
        arguments,
    )


def test_sort_1d_array_gives_each_sorted_element_its_original_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _sort(tmp_path, monkeypatch, "I32", [3, 1, 2, 1])
    assert result.ordered == [1, 1, 2, 3]
    assert result.pointers == [1, 3, 2, 0]


def test_sort_1d_array_descending_reverses_the_sorted_list_with_its_pointers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _sort(tmp_path, monkeypatch, "I32", [3, 1, 2, 1], order=1)
    assert result.ordered == [3, 2, 1, 1]
    assert result.pointers == [0, 2, 3, 1]


def test_sort_1d_array_of_nothing_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _sort(tmp_path, monkeypatch, "String", [])
    assert (result.ordered, result.pointers) == ([], [])


@pytest.mark.parametrize(
    ("method", "expected"), [(0, [1, 0, 2]), (1, [2, 0, 1]), (2, [2, 0, 1])]
)
def test_sort_1d_array_of_complex_numbers_sorts_by_the_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: int, expected: list[int]
) -> None:
    values = [complex(1, 5), complex(0, 9), complex(1, 2)]
    result = _sort(tmp_path, monkeypatch, "CDB", values, method=method)
    assert result.pointers == expected
    assert result.ordered == [values[i] for i in expected]


def test_current_vis_parent_directory_is_the_folder_of_the_vi_joined_with_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _call(
        tmp_path,
        monkeypatch,
        [*OPENG_FILE, "Current VIs Parent Directory__ogtk.vi"],
        [(4, "input", PATH), (1, "output", PATH)],
        {"relative": (4, PATH)},
        {"out": (1, PATH)},
        {"relative": "data"},
    )
    assert result.out == (tmp_path / "pkg").resolve() / "data"

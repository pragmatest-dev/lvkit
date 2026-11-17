"""The ``<resource>/dialog/lvconfig.llb`` VIs (LV Config Read/Write/Remove and Get
LabVIEW Preferences (INI) File Path): each caller diagram is generated from the
catalog entry the recorded ``<resource>`` path names, and its Python is run against
the ``LabVIEW.ini`` beside the generated package's root.

The terminal indices and labels are those the ni-labview-icon-editor callers' call
nodes save for each VI's pane."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType
from lvkit.vilib_resolver import get_resolver
from tests.vilib_caller import (
    BOOL,
    I32,
    PATH,
    STRING,
    U32,
    run_catalog_call,
)

LLB = ["<resource>", "dialog", "lvconfig.llb"]


def _ini(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir(exist_ok=True)
    return tmp_path / "pkg" / "LabVIEW.ini"


def _read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vi: str,
    lv_type: LVType,
    **arguments: Any,
) -> Any:
    pane = [(2, "output", BOOL), (3, "output", lv_type), (6, "input", lv_type)]
    if vi == "LV Config Read String":
        pane.append((5, "input", PATH))
    pane.append((7, "input", STRING))
    inputs: dict[str, tuple[int, LVType]] = {"key": (7, STRING)}
    if "default" in arguments:
        inputs["default"] = (6, lv_type)
    if "path" in arguments:
        inputs["path"] = (5, PATH)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LLB, f"{vi}.vi"],
        pane=pane,
        inputs=inputs,
        outputs={"value": (3, lv_type), "found": (2, BOOL)},
        arguments=arguments,
    )
    return result


def _write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vi: str,
    lv_type: LVType,
    key: str,
    value: Any,
) -> None:
    run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LLB, f"{vi}.vi"],
        pane=[(6, "input", lv_type), (7, "input", STRING)],
        inputs={"key": (7, STRING), "value": (6, lv_type)},
        outputs={},
        arguments={"key": key, "value": value},
    )


def test_read_string_returns_an_existing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ini(tmp_path).write_text('[LabVIEW]\nname="hello world"\n', encoding="utf-8")
    result = _read(
        tmp_path, monkeypatch, "LV Config Read String", STRING, key="name", default="d"
    )
    assert result.value == "hello world"
    assert result.found is True


def test_read_string_of_a_missing_file_returns_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _read(
        tmp_path, monkeypatch, "LV Config Read String", STRING, key="name", default="d"
    )
    assert result.value == "d"
    assert result.found is False
    assert not _ini(tmp_path).exists()


def test_an_unwired_default_value_is_the_empty_value_of_its_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _read(tmp_path, monkeypatch, "LV Config Read String", STRING, key="k")
    assert (text.value, text.found) == ("", False)
    flag = _read(tmp_path, monkeypatch, "LV Config Read Boolean", BOOL, key="k")
    assert flag.value is False
    number = _read(tmp_path, monkeypatch, "LV Config Read Numeric (I32)", I32, key="k")
    assert number.value == 0


def test_read_string_of_a_missing_key_or_section_returns_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ini(tmp_path).write_text('[LabVIEW]\nother="x"\n', encoding="utf-8")
    missing_key = _read(
        tmp_path, monkeypatch, "LV Config Read String", STRING, key="name", default="d"
    )
    assert (missing_key.value, missing_key.found) == ("d", False)
    _ini(tmp_path).write_text('[Other]\nname="x"\n', encoding="utf-8")
    missing_section = _read(
        tmp_path, monkeypatch, "LV Config Read String", STRING, key="name", default="d"
    )
    assert (missing_section.value, missing_section.found) == ("d", False)


def test_read_string_matches_the_key_ignoring_case_and_keeps_an_unquoted_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ini(tmp_path).write_text("[LabVIEW]\nMyKey=plain text\n", encoding="utf-8")
    result = _read(
        tmp_path, monkeypatch, "LV Config Read String", STRING, key="mykey", default=""
    )
    assert result.value == "plain text"
    assert result.found is True


def test_read_string_reads_the_file_the_config_file_path_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other.ini"
    other.write_text('[LabVIEW]\nname="elsewhere"\n', encoding="utf-8")
    result = _read(
        tmp_path,
        monkeypatch,
        "LV Config Read String",
        STRING,
        key="name",
        default="d",
        path=other,
    )
    assert result.value == "elsewhere"


def test_write_string_then_read_back_with_equals_spaces_and_backslashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = "a = b ; c\\d\tend\nline"
    _write(tmp_path, monkeypatch, "LV Config Write String", STRING, "Path", value)
    text = _ini(tmp_path).read_text(encoding="utf-8")
    assert text == '[LabVIEW]\nPath="a = b ; c\\\\d\tend\\0Aline"\n\n'
    result = _read(
        tmp_path, monkeypatch, "LV Config Read String", STRING, key="Path", default=""
    )
    assert result.value == value
    assert result.found is True


def test_write_string_keeps_the_other_keys_and_the_case_of_the_key_it_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ini(tmp_path).write_text('[LabVIEW]\nKeepMe="1"\nMyKey="old"\n', encoding="utf-8")
    _write(tmp_path, monkeypatch, "LV Config Write String", STRING, "MYKEY", "new")
    assert _ini(tmp_path).read_text(encoding="utf-8") == (
        '[LabVIEW]\nKeepMe="1"\nMyKey="new"\n\n'
    )


def test_boolean_is_written_as_true_false_and_read_in_any_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, monkeypatch, "LV Config Write Boolean", BOOL, "flag", True)
    assert "flag=TRUE" in _ini(tmp_path).read_text(encoding="utf-8")
    on = _read(
        tmp_path, monkeypatch, "LV Config Read Boolean", BOOL, key="flag", default=False
    )
    assert (on.value, on.found) == (True, True)
    _ini(tmp_path).write_text("[LabVIEW]\nflag=false\n", encoding="utf-8")
    off = _read(
        tmp_path, monkeypatch, "LV Config Read Boolean", BOOL, key="flag", default=True
    )
    assert (off.value, off.found) == (False, True)
    absent = _read(
        tmp_path, monkeypatch, "LV Config Read Boolean", BOOL, key="nope", default=True
    )
    assert (absent.value, absent.found) == (True, False)


def test_numeric_i32_round_trips_and_a_bad_number_gives_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ini(tmp_path).write_text("[LabVIEW]\nn=-42\nbad=abc\n", encoding="utf-8")
    good = _read(
        tmp_path, monkeypatch, "LV Config Read Numeric (I32)", I32, key="n", default=7
    )
    assert (good.value, good.found) == (-42, True)
    bad = _read(
        tmp_path, monkeypatch, "LV Config Read Numeric (I32)", I32, key="bad", default=7
    )
    assert bad.value == 7


def test_color_is_written_as_eight_hex_digits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, monkeypatch, "LV Config Write Color", U32, "diagram", 0xFFCC00)
    assert "diagram=00FFCC00" in _ini(tmp_path).read_text(encoding="utf-8")
    result = _read(
        tmp_path, monkeypatch, "LV Config Read Color", U32, key="diagram", default=0
    )
    assert result.value == 0xFFCC00


def test_remove_deletes_only_that_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ini(tmp_path).write_text("[LabVIEW]\na=1\nB=2\n", encoding="utf-8")
    run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LLB, "LV Config Remove.vi"],
        pane=[(7, "input", STRING)],
        inputs={"key": (7, STRING)},
        outputs={},
        arguments={"key": "b"},
    )
    assert _ini(tmp_path).read_text(encoding="utf-8") == "[LabVIEW]\na=1\n\n"


def test_get_preferences_file_path_is_the_package_ini(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LLB, "Get LabVIEW Preferences (INI) File Path.vi"],
        pane=[(3, "output", PATH)],
        inputs={},
        outputs={"path": (3, PATH)},
        arguments={},
    )
    assert result.path == _ini(tmp_path).resolve()


def test_the_resource_folder_path_picks_the_entry_and_another_folder_does_not() -> None:
    resolver = get_resolver()
    recorded = "<resource>/dialog/lvconfig.llb/LV Config Read String.vi"
    entry = resolver.resolve_recorded(recorded)
    assert entry is not None
    assert entry.vi_path == recorded
    other = "<resource>/other/LV Config Read String.vi"
    assert resolver.resolve_recorded(other) is None
    in_vilib = "<vilib>/dialog/lvconfig.llb/LV Config Read String.vi"
    assert resolver.resolve_recorded(in_vilib) is None


def test_a_neighbour_in_the_same_library_without_an_entry_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = get_resolver()
    pathlist = "<resource>/dialog/lvconfig.llb/LV Config Read Pathlist.vi"
    assert resolver.resolve_recorded(pathlist) is None
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError, match="terminal_mapping"):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*LLB, "LV Config Read Pathlist.vi"],
            pane=[(7, "input", STRING)],
            inputs={"key": (7, STRING)},
            outputs={},
            arguments={"key": "k"},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert len(gaps) == 1

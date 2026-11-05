"""Every command that takes a VI path answers a path that does not exist, or a
file of a kind it does not take, with a usage error: exit 2 and one message
saying what it takes; never an lvkit error or a run summary."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lvkit import cli

_VI = "a VI file (.vi, .vim, .vit)"
_TAKES = {
    "describe": f"{_VI} or a .ctl",
    "generate": f"{_VI}, a .ctl, a .lvclass, a .lvlib, a .llb or a folder",
    "unresolved": f"{_VI}, a .ctl, a .lvclass, a .lvlib, a .llb or a folder",
    "docs": f"{_VI}, a .ctl, a .lvclass, a .lvlib or a folder",
    "render": f"{_VI}, a .ctl or a folder",
    "visualize": f"{_VI}, a .lvclass, a .lvlib or a folder",
    "diff": _VI,
}


def _argv(command: str, path: Path, tmp_path: Path) -> list[str]:
    extra = {
        "generate": ["-o", str(tmp_path / "out")],
        "docs": [str(tmp_path / "out")],
        "diff": [str(path)],
    }
    return [command, str(path), *extra.get(command, [])]


def _lvkit(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", *argv])
    return cli.main()


@pytest.mark.parametrize("command", sorted(_TAKES))
def test_a_file_of_the_wrong_kind_is_a_usage_error(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = tmp_path / "Bad.txt"
    bad.write_text("not a VI\n")
    code = _lvkit(monkeypatch, _argv(command, bad, tmp_path))
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == f"Error: expected {_TAKES[command]}: Bad.txt\n"


@pytest.mark.parametrize("command", sorted(_TAKES))
def test_a_path_that_does_not_exist_is_a_usage_error(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "Missing.vi"
    code = _lvkit(monkeypatch, _argv(command, missing, tmp_path))
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == f"Error: Path not found: {missing}\n"


@pytest.mark.parametrize("command", ["describe", "diff"])
def test_a_folder_is_a_usage_error_where_the_command_takes_none(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = tmp_path / "vis"
    folder.mkdir()
    code = _lvkit(monkeypatch, _argv(command, folder, tmp_path))
    assert code == 2
    assert capsys.readouterr().err == f"Error: expected {_TAKES[command]}: vis\n"

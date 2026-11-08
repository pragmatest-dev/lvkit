"""A command takes a path as lvkit prints it: ``<userlib>/…`` is found in the
``--userlib`` folder, and a VI inside an ``.llb`` is named through its archive."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from lvkit import cli
from tests.test_llb_stream_lifetime import _write_archive

_VI = Path(__file__).parent / "corpus/issues/36/bundle-unbundle-names.vi"


def _lvkit(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", *argv])
    return cli.main()


def _userlib(tmp_path: Path) -> Path:
    ulib = tmp_path / "ulib"
    (ulib / "Sub").mkdir(parents=True)
    shutil.copy(_VI, ulib / "Sub" / "X.vi")
    return ulib


def test_describe_finds_a_userlib_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ulib = _userlib(tmp_path)
    real = ["--userlib", str(ulib), "--no-auto-vilib"]
    assert _lvkit(monkeypatch, "describe", str(ulib / "Sub" / "X.vi"), *real) == 0
    expected = capsys.readouterr().out
    assert expected
    assert _lvkit(monkeypatch, "describe", "<userlib>/Sub/X.vi", *real) == 0
    assert capsys.readouterr().out == expected


def test_a_missing_stored_path_names_what_was_tried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ulib = _userlib(tmp_path)
    code = _lvkit(
        monkeypatch,
        "describe",
        "<userlib>/Sub/Gone.vi",
        "--userlib",
        str(ulib),
        "--no-auto-vilib",
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "Path not found: <userlib>/Sub/Gone.vi" in err
    assert f"tried: {ulib / 'Sub' / 'Gone.vi'}" in err


def test_a_stored_path_without_its_folder_names_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _lvkit(monkeypatch, "describe", "<userlib>/Sub/X.vi", "--no-auto-vilib")
    err = capsys.readouterr().err
    assert code == 2
    assert "--userlib" in err


def test_describe_takes_a_vi_inside_an_llb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = _write_archive(
        tmp_path / "vi.lib" / "Utility", "UCRF", {"member.vi": _VI.read_bytes()}
    )
    flags = ["--vilib", str(tmp_path / "vi.lib"), "--no-auto-vilib"]
    assert _lvkit(monkeypatch, "describe", str(archive / "member.vi"), *flags) == 0
    expected = capsys.readouterr().out
    assert expected
    stored = f"<vilib>/Utility/{archive.name}/member.vi"
    assert _lvkit(monkeypatch, "describe", stored, *flags) == 0
    assert capsys.readouterr().out == expected


def test_callers_names_the_flag_the_command_lacks_when_no_folder_is_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("lvkit.lv_detect.detect_labview", lambda: None)
    code = _lvkit(monkeypatch, "callers", "<userlib>/Sub/X.vi", str(tmp_path))
    err = capsys.readouterr().err
    assert code == 2
    assert "Path not found: <userlib>/Sub/X.vi" in err
    assert "--userlib" in err

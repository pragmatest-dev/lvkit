"""An MCP tool takes a path as lvkit prints it: ``<userlib>/…`` is found in the
tool's own ``userlib`` folder; a VI inside an ``.llb`` is named through its
archive."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from lvkit.mcp import server as srv
from tests.test_llb_stream_lifetime import _write_archive

_VI = Path(__file__).parent / "corpus/issues/36/bundle-unbundle-names.vi"


def _userlib(tmp_path: Path) -> Path:
    ulib = tmp_path / "ulib"
    (ulib / "Sub").mkdir(parents=True)
    shutil.copy(_VI, ulib / "Sub" / "X.vi")
    return ulib


def test_read_vi_finds_a_userlib_path(tmp_path: Path) -> None:
    ulib = _userlib(tmp_path)
    real = asyncio.run(
        srv.read_vi(str(ulib / "Sub" / "X.vi"), userlib=str(ulib), format="lvnet")
    )
    stored = asyncio.run(
        srv.read_vi("<userlib>/Sub/X.vi", userlib=str(ulib), format="lvnet")
    )
    assert real["lvnet"]
    assert stored == real


def test_read_vi_takes_a_vi_inside_an_llb(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "vi.lib" / "Utility", "UCRF", {"member.vi": _VI.read_bytes()}
    )
    vilib = str(tmp_path / "vi.lib")
    real = asyncio.run(
        srv.read_vi(str(archive / "member.vi"), vilib=vilib, format="lvnet")
    )
    stored = asyncio.run(
        srv.read_vi(
            f"<vilib>/Utility/{archive.name}/member.vi", vilib=vilib, format="lvnet"
        )
    )
    assert real["lvnet"]
    assert stored == real


def test_a_stored_path_found_nowhere_fails_the_call(tmp_path: Path) -> None:
    ulib = _userlib(tmp_path)
    with pytest.raises(FileNotFoundError) as e:
        asyncio.run(srv.read_vi("<userlib>/Sub/Gone.vi", userlib=str(ulib)))
    assert f"tried: {ulib.resolve() / 'Sub' / 'Gone.vi'}" in str(e.value)

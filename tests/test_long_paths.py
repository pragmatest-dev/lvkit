"""``extended_length`` gives the folder the caller meant, with no ``.`` or ``..``
left in it: Windows takes an extended-length path as written (#308)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from lvkit.long_paths import display_path, extended_length


@pytest.mark.parametrize(
    ("given", "meant"),
    [
        ("out_a", "work/out_a"),
        ("../out_b", "out_b"),
        ("sub/../out_c", "work/out_c"),
        ("./out_d/.", "work/out_d"),
    ],
)
def test_dots_are_removed_before_the_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, given: str, meant: str
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    extended = extended_length(Path(given))

    assert display_path(extended) == Path(os.path.abspath(tmp_path / meant))
    assert ".." not in extended.parts
    extended.mkdir(parents=True)
    assert (tmp_path / meant).is_dir()


def test_a_symbolic_link_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot make symbolic links")

    assert display_path(extended_length(link / "out")) == link / "out"


@pytest.mark.skipif(sys.platform != "win32", reason="the prefix is Windows-only")
def test_windows_paths_get_the_prefix(tmp_path: Path) -> None:
    assert str(extended_length(tmp_path)).startswith("\\\\?\\")

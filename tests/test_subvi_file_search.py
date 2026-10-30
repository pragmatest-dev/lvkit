"""SubVI file search matches the file name the caller records, nothing else."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.core import InMemoryVIGraph


def test_double_underscore_name_is_not_shortened(tmp_path: Path) -> None:
    (tmp_path / "Do.vi").write_bytes(b"")
    (tmp_path / "Do__Lib.vi").write_bytes(b"")
    graph = InMemoryVIGraph()
    assert graph._find_subvi("Do__Lib.vi", [tmp_path]) == tmp_path / "Do__Lib.vi"
    (tmp_path / "Do__Lib.vi").unlink()
    assert graph._find_subvi("Do__Lib.vi", [tmp_path]) is None
    assert graph._find_subvi("Do.vi", [tmp_path]) == tmp_path / "Do.vi"

"""A rendered diagram never carries the VI's key (a file path) in its aria-label
or element ids, whichever entry point drew it."""

from __future__ import annotations

import shutil
from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.render import render_vi, render_vi_with_subvis

SOURCE = (
    Path(__file__).parent
    / "corpus"
    / "issues"
    / "30"
    / "visible-frame-case-sequence.vi"
)


def _graph(tmp_path: Path) -> tuple[InMemoryVIGraph, str]:
    folder = tmp_path / "secret folder"
    folder.mkdir()
    vi = Path(shutil.copy(SOURCE, folder / "Mine.vi"))
    graph = InMemoryVIGraph()
    key = graph.load_vi(vi, mode=LoadMode.NONE, layout=True)
    return graph, key


def test_no_entry_point_writes_the_key_into_the_svg(tmp_path: Path) -> None:
    graph, key = _graph(tmp_path)
    plain = render_vi(graph, key)
    docs, _ = render_vi_with_subvis(graph, key)
    for svg in (plain, docs):
        assert svg is not None
        assert "<script" in svg  # the id is in use
        assert str(tmp_path) not in svg
        assert "secret" not in svg
        assert "pytest" not in svg
        assert 'id="lv-' in svg

"""Every command that parses a VI warms the facts index (progressive build).

`describe`/`diff` already warmed the entry VI; this locks in the tail —
`render`, `generate`, `docs`, `visualize` — plus the `warm_all_loaded` helper
that warms ALL VIs a parse loaded, not just the entry one. Hermetic: the autouse
cache fixture points the index at a fresh tmp dir, so each test asserts on rows
its own parse wrote.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lvkit.index import sql
from lvkit.index.store import db_path

pytestmark = [pytest.mark.needs_samples, pytest.mark.cold_cache]

SAMPLE = Path(
    ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi"
).resolve()


def _project_root_of(vi: Path) -> Path:
    from lvkit.cache_paths import _project_root_for

    return _project_root_for(vi) or vi.parent


def _indexed_names(root: Path) -> set[str]:
    if not db_path(root).exists():
        return set()
    res = sql.run_query(root, "SELECT name FROM vi")
    return {row[0] for row in res.rows}


@pytest.fixture
def sample() -> Path:
    if not SAMPLE.exists():
        pytest.skip(f"sample VI absent: {SAMPLE}")
    return SAMPLE


def test_render_warms_the_index(sample: Path):
    from lvkit.render import render_vi_file

    root = _project_root_of(sample)
    assert sample.name not in _indexed_names(root)  # hermetic: starts empty

    svg = render_vi_file(sample)
    assert svg  # the render itself still works

    assert sample.name in _indexed_names(root), "render must warm the index"


def test_visualize_warms_the_index(
    sample: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from lvkit import cli

    pytest.importorskip("pyvis")
    root = _project_root_of(sample)
    assert sample.name not in _indexed_names(root)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lvkit",
            "visualize",
            str(sample),
            "-o",
            str(tmp_path / "viz.html"),
            "--no-auto-vilib",
        ],
    )
    assert cli.main() == 0

    assert sample.name in _indexed_names(root), "visualize must warm the index"


def test_generate_warms_the_index(sample: Path, tmp_path: Path):
    from lvkit.pipeline import generate_python

    root = _project_root_of(sample)
    assert sample.name not in _indexed_names(root)

    generate_python(sample, str(tmp_path / "out"))

    assert sample.name in _indexed_names(root), "generate must warm the index"


def test_warm_all_loaded_warms_from_one_parse(sample: Path):
    """warm_all_loaded warms every VI it CAN project from a single parse — the
    entry VI is guaranteed; leaf SubVIs are warmed best-effort (a NONE-depth
    leaf that can't be projected is skipped, never crashes the caller). Assert
    the entry is present and the result is a clean subset of what was loaded
    (no phantom rows)."""
    from lvkit.graph import InMemoryVIGraph, LoadMode
    from lvkit.index.build import warm_all_loaded

    graph = InMemoryVIGraph()
    graph.load_vi(sample, LoadMode.MINIMAL)
    # The index is PATH-keyed: a "save-as" VI (file renamed, internal name kept)
    # is indexed by its FILE name, not the graph's internal node name — so
    # compare against source-path filenames, not list_vis() names.
    loaded_files = {
        src.name
        for n in graph.list_vis()
        if (src := graph.get_vi_source_path(n)) is not None
    }

    warm_all_loaded(graph)

    root = _project_root_of(sample)
    indexed = _indexed_names(root)
    assert sample.name in indexed  # the entry VI is the hard guarantee
    assert indexed  # non-empty
    assert indexed <= loaded_files  # only real, loaded files — no phantoms

"""Corpus VIs loaded once per test worker and shared by every test that reads
them.

Loading a large corpus VI with its subVIs takes tens of seconds, and many tests
read the same one. ``load_corpus_vi`` returns the same graph to each caller, so
callers treat it as read-only: a test that changes a graph loads its own with
``load_vi_by_path``. Tests of one large VI share an ``xdist_group`` mark so they
run on the worker that already holds its graph.
"""

from __future__ import annotations

import functools
from pathlib import Path

from lvkit.graph import InMemoryVIGraph, load_vi_by_path
from lvkit.load_mode import LoadMode


@functools.cache
def load_corpus_vi(vi_path: Path, search_root: Path) -> tuple[InMemoryVIGraph, str]:
    """``(graph, vi key)`` for ``vi_path`` loaded without layout, searching
    ``search_root`` for its dependencies. The key is the one ``load_vi`` gave
    ``vi_path``. The graph is shared: do not change it."""
    return load_vi_by_path(
        vi_path, LoadMode.MINIMAL, search_paths=[search_root], layout=False
    )

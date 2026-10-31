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
import os
from pathlib import Path

from lvkit.graph import InMemoryVIGraph, load_vi_by_path
from lvkit.index.build import BuildResult, build_index
from lvkit.index.project import resolve_project
from lvkit.load_mode import LoadMode

# The sample corpus's user.lib: the OpenG libraries the samples call through
# their recorded ``<userlib>/_OpenG.lib/...`` paths.
OPENG_USERLIB = (
    Path(__file__).resolve().parent.parent
    / ".lvkit"
    / "cache"
    / "samples"
    / "OpenG"
    / "extracted"
    / "File Group 0"
    / "user.lib"
)


@functools.cache
def load_corpus_vi(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    """``(graph, vi key)`` for ``vi_path`` loaded without layout, with the
    corpus's user.lib (``OPENG_USERLIB``) as the ``<userlib>`` root. The key is
    the one ``load_vi`` gave ``vi_path``. The graph is shared: do not change
    it."""
    return load_vi_by_path(
        vi_path, LoadMode.MINIMAL, userlib_root=OPENG_USERLIB, layout=False
    )


@functools.cache
def build_corpus_index(project_root: Path) -> BuildResult:
    """The index of every VI under ``project_root``, built once per test worker
    and shared by the tests that read it: do not change it. The build runs
    against the environment's own extraction cache (``LVKIT_CACHE_DIR`` is
    unset for it), so a cold cache still pays a full cold build."""
    saved = os.environ.pop("LVKIT_CACHE_DIR", None)
    try:
        root, vi_paths = resolve_project(project_root)
        return build_index(root, vi_paths)
    finally:
        if saved is not None:
            os.environ["LVKIT_CACHE_DIR"] = saved

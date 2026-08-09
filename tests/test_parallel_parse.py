"""The parallel pre-parse must only spin up a process pool on the MAIN thread.

``build_index`` runs inside the MCP server via ``asyncio.to_thread`` — a
worker thread of a long-running server process. A spawn ``ProcessPoolExecutor``
created there never makes progress (the reindex "structurally hangs"). The
optimization is perf-only and best-effort, so off the main thread it must
return ``{}`` and let the caller's serial ``parse_vi()`` run — WITHOUT ever
constructing a pool.

Hermetic: the pool and extraction are stubbed, so nothing is spawned or parsed.
"""

from __future__ import annotations

import threading
from pathlib import Path

import lvkit.graph.parallel_parse as parallel_parse


class _FakePool:
    """Records that a pool was constructed; ``map`` yields nothing."""

    created = 0

    def __init__(self, *args, **kwargs):
        type(self).created += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, batches):
        return []


def _patch(monkeypatch):
    _FakePool.created = 0
    monkeypatch.setattr(parallel_parse, "ProcessPoolExecutor", _FakePool)
    # A non-empty resolved list is what would trigger the pool on the main
    # thread — stub extraction so we never touch a real .vi.
    monkeypatch.setattr(
        parallel_parse, "extract_vi_xml", lambda p: (p, None, None)
    )


def test_main_thread_constructs_the_pool(monkeypatch):
    _patch(monkeypatch)
    result = parallel_parse.parallel_parse_directory([Path("a.vi"), Path("b.vi")])
    assert _FakePool.created == 1  # pool WAS used on the main thread
    assert result == {}  # (our fake map yields nothing)


def test_worker_thread_skips_the_pool_and_returns_empty(monkeypatch):
    _patch(monkeypatch)
    box: dict[str, object] = {}

    def run():
        box["result"] = parallel_parse.parallel_parse_directory(
            [Path("a.vi"), Path("b.vi")]
        )

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=10)  # the whole point: it must NOT hang
    assert not t.is_alive()
    assert box["result"] == {}  # serial-fallback signal
    assert _FakePool.created == 0  # no pool was ever constructed off-main-thread

"""Extract VIs (``.vi`` -> XML via pylabview) in parallel worker processes.

``ExtractionPool`` does ONE thing: run ``extract_vi_xml`` for files that are not
extracted yet, off the main process, so a load whose dependency tree is not in
the cache extracts the tree's width at once instead of one VI at a time. Workers
only call ``extract_vi_xml`` -- they never touch a graph. The loader still builds
the graph in its own sorted order: it calls ``prefetch`` for a VI's dependencies,
then ``wait`` for each file just before its own ``extract_vi_xml`` call, which is
then a cache hit.

Best-effort by contract: a worker or pool failure is swallowed here, and the
loader's own ``extract_vi_xml`` call then extracts the file serially and raises
the real error. A pool is also never used off the main thread (the MCP server
runs loads on a worker thread, where a spawn pool never makes progress), inside
a worker process, or under Pyodide. It starts lazily, on the first file that
actually needs extracting, so an already-extracted load starts no processes.

pylabview keeps memory across VIs within one process, so worker lifetime is
bounded by retiring each executor after a batch of files (``_FILES_PER_WORKER``
per worker): its queued files still finish and its workers then exit, and the
next file starts a fresh executor. At most one retired executor is left
draining at a time.

Spawned workers re-import the calling script's ``__main__``, so a script that
loads VIs at module level without an ``if __name__ == "__main__":`` guard runs
that load again in every worker (a worker never starts a pool of its own).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import sys
import threading
from collections.abc import Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from types import TracebackType

from . import cache_paths
from .console import use_utf8_output
from .extractor import cached_extraction, extract_vi_xml

logger = logging.getLogger(__name__)

_MAX_WORKERS = 8

# Files submitted per worker before the executor is retired and a fresh one
# started, bounding pylabview's memory growth across many VIs in one process.
_FILES_PER_WORKER = 50


def _worker_count() -> int:
    return min(os.cpu_count() or 1, _MAX_WORKERS)


def _init_worker(
    vilib_root: tuple[Path, ...],
    userlib_root: tuple[Path, ...],
    instrlib_root: tuple[Path, ...],
) -> None:
    """Worker start-up: adopt the parent's library roots, which decide each VI's
    cache slot (see ``cache_paths.classify``). Its warnings are UTF-8, like the
    parent's."""
    use_utf8_output()
    cache_paths.set_extraction_roots(
        vilib_root=vilib_root, userlib_root=userlib_root, instrlib_root=instrlib_root
    )


def _extract_one(vi_path: str) -> None:
    """Worker entry point: extract one VI into the managed cache."""
    extract_vi_xml(vi_path)


class ExtractionPool:
    """A lazily-started process pool that extracts the files it is given."""

    def __init__(self) -> None:
        self._executor: ProcessPoolExecutor | None = None
        self._retired: ProcessPoolExecutor | None = None
        self._submitted = 0  # files submitted to the current executor
        self._futures: dict[Path, Future[None]] = {}
        self._enabled = (
            threading.current_thread() is threading.main_thread()
            and mp.parent_process() is None
            and sys.platform != "emscripten"
        )

    @property
    def enabled(self) -> bool:
        """Whether this pool will extract anything (False off the main thread, in
        a worker process, under Pyodide, or after a pool failure)."""
        return self._enabled

    def prefetch(self, paths: Iterable[Path]) -> None:
        """Start extracting every file in ``paths`` that is not extracted yet and
        was not already submitted."""
        for path in paths:
            if not self._enabled:
                return
            key = path.resolve()
            if key in self._futures or not key.is_file():
                continue
            try:
                if cached_extraction(key) is not None:
                    continue
                self._futures[key] = self._submit(key)
            except Exception:
                logger.debug("parallel extract: disabled", exc_info=True)
                self._enabled = False

    def wait(self, path: Path) -> None:
        """Block until ``path``'s submitted extraction finished (no-op when it was
        never submitted). A failed extraction is not raised here: the caller's own
        ``extract_vi_xml`` call re-extracts it and raises the real error."""
        future = self._futures.get(path.resolve())
        if future is None:
            return
        try:
            future.result()
        except Exception:
            logger.debug("parallel extract: %s failed", path, exc_info=True)

    def close(self) -> None:
        """Cancel queued work and stop the workers."""
        executors = (self._executor, self._retired)
        self._executor = self._retired = None
        self._submitted = 0
        self._futures.clear()
        for executor in executors:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> ExtractionPool:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _submit(self, path: Path) -> Future[None]:
        """Submit ``path`` to the current executor; once it holds a full batch,
        retire it."""
        future = self._start().submit(_extract_one, str(path))
        self._submitted += 1
        if self._submitted >= _FILES_PER_WORKER * _worker_count():
            self._retire()
        return future

    def _retire(self) -> None:
        """Let the current executor finish its queued files and exit its workers.
        The previously retired executor is drained first, so at most one retired
        executor is alive."""
        executor, self._executor = self._executor, None
        self._submitted = 0
        if self._retired is not None:
            self._retired.shutdown(wait=True)
        self._retired = executor
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=False)

    def _start(self) -> ProcessPoolExecutor:
        if self._executor is None:
            # SPAWN, never fork: forking a multi-threaded process (the MCP
            # server) risks a child deadlock.
            self._executor = ProcessPoolExecutor(
                max_workers=_worker_count(),
                mp_context=mp.get_context("spawn"),
                initializer=_init_worker,
                initargs=cache_paths.extraction_roots(),
            )
        return self._executor

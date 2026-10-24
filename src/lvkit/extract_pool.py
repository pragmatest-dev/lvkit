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
from .cache_paths import classify
from .extractor import cached_extraction, extract_vi_xml

logger = logging.getLogger(__name__)

_MAX_WORKERS = 8

# Tasks per worker before it is replaced, bounding pylabview's memory growth
# across many VIs. Only Python >= 3.11 supports it.
_MAX_TASKS_PER_CHILD = 50


def _init_worker(vilib_root: Path | None, userlib_root: Path | None) -> None:
    """Worker start-up: adopt the parent's library roots, which decide each VI's
    cache slot (see ``cache_paths.classify``)."""
    cache_paths.set_extraction_roots(vilib_root=vilib_root, userlib_root=userlib_root)


def _extract_one(vi_path: str) -> None:
    """Worker entry point: extract one VI into the managed cache."""
    extract_vi_xml(vi_path)


def _shares_cache_slot(vi_path: Path) -> bool:
    """Whether ``vi_path`` extracts into a cache slot shared with the other VIs
    of its directory (a VI outside any project or library, see
    ``cache_paths.classify``). Extractions into one slot overwrite each other, so
    those run only one at a time, in the loader."""
    return classify(vi_path, "extract")[2] == "adhoc"


class ExtractionPool:
    """A lazily-started process pool that extracts the files it is given."""

    def __init__(self) -> None:
        self._executor: ProcessPoolExecutor | None = None
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
                if cached_extraction(key) is not None or _shares_cache_slot(key):
                    continue
                self._futures[key] = self._start().submit(_extract_one, str(key))
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
        executor, self._executor = self._executor, None
        self._futures.clear()
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

    def _start(self) -> ProcessPoolExecutor:
        if self._executor is None:
            # SPAWN, never fork: forking a multi-threaded process (the MCP
            # server) risks a child deadlock.
            options: dict[str, object] = {}
            if sys.version_info >= (3, 11):
                options["max_tasks_per_child"] = _MAX_TASKS_PER_CHILD
            self._executor = ProcessPoolExecutor(
                max_workers=min(os.cpu_count() or 1, _MAX_WORKERS),
                mp_context=mp.get_context("spawn"),
                initializer=_init_worker,
                initargs=cache_paths.extraction_roots(),
                **options,  # type: ignore[arg-type]
            )
        return self._executor

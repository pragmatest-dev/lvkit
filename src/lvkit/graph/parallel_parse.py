"""Parallel pre-parse of a directory's VIs (XML -> ``ParsedVI``) for
``load_directory``.

Profiling ``build_index`` on JKI VI Tester (487 VIs, warm extraction cache)
showed ``parse_vi`` (XML -> ``ParsedVI``) at ~62% of ``load_directory``'s
wall time -- the dominant cost, and pure CPU with no shared state. Binary
extraction (``.vi`` -> XML via pylabview) is a separate, already-cached step
(``extract_vi_xml``); this module only parallelizes the decode that happens
AFTER extraction.

This module does ONE thing: given a directory's ``.vi`` paths, pre-parse them
across a process pool and return a ``dict[str, ParsedVI]`` keyed by the
``bd_xml`` path string -- the same key ``_load_vi_recursive`` (in
``loading.py``) looks up via ``InMemoryVIGraph._parse_cache`` before falling
back to its own serial ``parse_vi()`` call. Graph assembly itself stays
completely serial and untouched: workers only parse, they never touch a
graph, so node/edge order (and therefore codegen determinism) is unaffected
by parse completion order -- the caller iterates the SAME sorted VI list it
always did and just finds the parse already done.

Best-effort by contract: any failure (a crashed/broken process pool, a
mis-behaving worker) yields a partial or empty cache rather than raising --
every cache miss falls through to a normal serial ``parse_vi()`` call, so
this can only make a load faster, never break it.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..extractor import extract_vi_xml
from ..parser import ParsedVI, parse_vi

logger = logging.getLogger(__name__)

# Below this VI count, process-pool startup overhead isn't worth paying --
# the serial path (~15-20ms/VI on a warm extraction cache) is already fast,
# and small directories are common (single-class loads, tests).
PARALLEL_THRESHOLD = 50

# VIs per worker task. Batching (rather than one task per VI) amortizes
# process-pool scheduling/pickling overhead across many small XML files.
_BATCH_SIZE = 20

_ParseItem = tuple[Path, Path | None, Path | None]


def _parse_batch(items: list[_ParseItem]) -> dict[str, ParsedVI]:
    """Worker entry point: parse a batch of (bd_xml, fp_xml, main_xml)
    triples. Runs in a worker process. A single unparseable VI is skipped,
    not fatal to the rest of the batch -- the caller's serial path parses it
    (and reports its error, same as today) when it finds no cache entry."""
    out: dict[str, ParsedVI] = {}
    for bd_xml, fp_xml, main_xml in items:
        try:
            out[str(bd_xml)] = parse_vi(bd_xml=bd_xml, fp_xml=fp_xml, main_xml=main_xml)
        except Exception:
            logger.debug("parallel parse: skipping %s", bd_xml, exc_info=True)
    return out


def parallel_parse_directory(
    vi_paths: list[Path], max_workers: int | None = None,
) -> dict[str, ParsedVI]:
    """Pre-parse every ``.vi`` in ``vi_paths`` across a process pool.

    Returns a dict keyed by ``str(bd_xml)`` for every VI that parsed cleanly.
    Never raises: a partial or empty dict on any failure (extraction miss,
    a broken process pool, ...) just means the caller's serial loop parses
    those VIs itself, exactly as if this pre-parse pass never ran.
    """
    # Only spin up a process pool on the MAIN thread. build_index runs inside
    # the MCP server via asyncio.to_thread — i.e. a worker thread of a
    # long-running server process. Creating a spawn ProcessPoolExecutor there
    # never makes progress (the reindex "structurally hangs"; workers are
    # cleaned up on cancel, the DB is left untouched). The main-thread path
    # (CLI / tests) is the context this optimization was validated on. This is
    # perf-only and best-effort by contract, so off the main thread we return
    # {} and every VI falls through to the caller's serial parse_vi().
    if threading.current_thread() is not threading.main_thread():
        return {}

    resolved: list[_ParseItem] = []
    for vi_path in vi_paths:
        try:
            bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)
        except Exception:
            # Extraction failure -- let the serial path hit (and report) it.
            continue
        resolved.append((bd_xml, fp_xml, main_xml))

    if not resolved:
        return {}

    batches = [
        resolved[i : i + _BATCH_SIZE] for i in range(0, len(resolved), _BATCH_SIZE)
    ]
    workers = max_workers or min(len(batches), os.cpu_count() or 1)

    cache: dict[str, ParsedVI] = {}
    try:
        # Use a SPAWN context, never fork: build_index runs inside the MCP
        # server's thread pool (asyncio.to_thread), and forking a
        # multi-threaded process risks a child deadlock (Python warns about
        # exactly this). Spawn starts a clean interpreter — safe with threads,
        # and its higher startup is amortized over the batched work.
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            for result in pool.map(_parse_batch, batches):
                cache.update(result)
    except Exception:
        # BrokenProcessPool or any other pool-level failure -- fall all the
        # way back to serial parsing rather than fail the build. The usual
        # cause of BrokenProcessPool is an API script that calls build_index at
        # module level without an ``if __name__ == "__main__":`` guard: spawn
        # re-imports the script in each worker and the pool aborts. The build
        # still completes here, serially.
        logger.warning(
            "parallel parse of %d VIs failed -- falling back to serial parsing "
            "(if you're calling build_index from a script, guard it with "
            "`if __name__ == \"__main__\":`)",
            len(vi_paths),
            exc_info=True,
        )
        return {}

    return cache

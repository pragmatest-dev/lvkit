"""Content-addressed cache for :func:`parse_vi`'s output (XML -> ``ParsedVI``).

This is a SECOND cache layer, stacked on top of the existing extraction cache
(``lvkit.extractor.extract_vi_xml``, which caches ``.vi`` -> XML). This module
caches the next step: XML -> ``ParsedVI``. Profiling showed the XML decode
(``parse_vi``) dominates a warm-extraction-cache load; deserializing a cached
``ParsedVI`` is far cheaper than re-walking the XML tree.

Design:

- **Key**: ``sha256`` of the BD/FP/main XML bytes actually fed to the parse,
  plus :data:`SCHEMA_VERSION`, the running ``lvkit.__version__``, and the
  ``layout`` flag. The version components mean a schema or tool change is an
  automatic cache MISS (never a silently-stale hit) -- bump
  ``SCHEMA_VERSION`` whenever ``ParsedVI`` or any of its nested dataclasses
  changes shape in a way that would make an old pickle wrong.
- **Only ``layout=False`` is cached.** ``parse_vi(layout=True)`` decodes
  geometry into ``ParsedVI.layout`` for rendering; render already has its own
  SVG output-cache, so caching the (larger, less-reused) layout variant isn't
  worth the complexity of a second cache dimension.
- **Storage**: pickle, under ``<cache root>/parsed/<key[:2]>/<key>.pkl``. Safe
  to pickle because the key already encodes the schema/tool version -- a
  drift is a cache MISS (falls through to a fresh parse), never a
  silently-wrong load.
- **Concurrency**: written with the same write-to-unique-temp +
  ``os.replace`` pattern as the extraction cache (see ``extractor.py``), so
  concurrent writers (parallel-parse worker processes) can't corrupt or race
  on an entry.
- **Best-effort**: any read/write/(un)pickle error is caught and logged at
  debug -- a cache failure must never break or change a parse, only its
  speed. Callers always get a normal fresh ``parse_vi`` decode on any error.
- **Disable switch**: set ``LVKIT_NO_PARSE_CACHE=1`` to bypass both read and
  write (debugging, and used by the equivalence test to force a cold parse).

Note on ``source_path``: the key is pure XML-content-based and does NOT
include the source ``.vi`` path, so the same XML content parsed once via
``vi_path=`` (a top-level load) and once via ``bd_xml=`` alone (a SubVI
dependency walk in ``loading.py``) hits the SAME cache entry. That's fine for
every OTHER field -- they're pure functions of the XML -- but
``ParsedVI.metadata.source_path`` is a function of the CALL SITE, not the
XML. ``parse_vi`` handles this itself: it derives ``source_path`` before
consulting the cache and patches it onto a hit (``dataclasses.replace``)
rather than trusting whatever the cached copy carries -- so a cache hit still
gets ITS OWN correct ``source_path`` regardless of which call first
populated the entry.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import tempfile
from pathlib import Path

from .. import __version__
from ..cache_paths import global_cache_root
from .models import ParsedVI

logger = logging.getLogger(__name__)

# Bump on any change to ParsedVI (or a nested dataclass) that would make a
# previously-pickled entry wrong -- e.g. a new/renamed/retyped field, or a
# change to what parse_vi() decodes. A bump makes every prior entry an
# automatic miss (different key), never a silently-stale hit.
SCHEMA_VERSION = 1

_ENV_DISABLE = "LVKIT_NO_PARSE_CACHE"


def _enabled() -> bool:
    """False when the caller has opted out via LVKIT_NO_PARSE_CACHE=1."""
    return os.environ.get(_ENV_DISABLE, "") not in ("1", "true", "True")


def _cache_dir() -> Path:
    return global_cache_root() / "parsed"


def compute_key(
    bd_xml: Path | str,
    fp_xml: Path | str | None,
    main_xml: Path | str | None,
) -> str | None:
    """Content-addressed cache key for one ``parse_vi(layout=False)`` call.

    Hashes the ACTUAL bytes that will be parsed -- ``bd_xml`` is required;
    ``fp_xml``/``main_xml`` are included only when present (mirroring the
    optionality ``parse_vi`` itself honors, including the front-panel-heap
    size guard already applied by the caller before this is computed).

    Returns ``None`` (never raises) if a file can't be read -- the caller
    then simply skips the cache and parses normally.
    """
    try:
        h = hashlib.sha256()
        h.update(Path(bd_xml).read_bytes())
        for p in (fp_xml, main_xml):
            if p is not None and Path(p).exists():
                h.update(b"\x01")
                h.update(Path(p).read_bytes())
            else:
                h.update(b"\x00")
        h.update(f"|schema={SCHEMA_VERSION}|version={__version__}".encode())
        return h.hexdigest()
    except OSError:
        logger.debug("parse cache: failed to hash inputs", exc_info=True)
        return None


def load(key: str) -> ParsedVI | None:
    """Return the cached ``ParsedVI`` for ``key``, or ``None`` on any miss
    (not present, disabled, or a read/unpickle error -- best-effort by
    contract, so any failure here is silent-and-logged, never raised)."""
    if not _enabled():
        return None
    path = _cache_dir() / key[:2] / f"{key}.pkl"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        obj = pickle.loads(data)
    except Exception:
        logger.debug("parse cache: unpickle failed for %s", key, exc_info=True)
        return None
    if not isinstance(obj, ParsedVI):
        return None
    return obj


def store(key: str, parsed: ParsedVI) -> None:
    """Best-effort write of ``parsed`` under ``key``. Atomic (unique temp +
    ``os.replace``) so concurrent writers -- the parallel-parse worker
    processes -- can't corrupt or collide on an entry. Any failure is caught
    and logged at debug; it must never propagate and break the parse."""
    if not _enabled():
        return
    try:
        d = _cache_dir() / key[:2]
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{key}.pkl"
        data = pickle.dumps(parsed, protocol=pickle.HIGHEST_PROTOCOL)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=d,
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        logger.debug("parse cache: write failed for %s", key, exc_info=True)

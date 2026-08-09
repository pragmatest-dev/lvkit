"""Content-addressed cache for :func:`parse_vi`'s output (XML -> ``ParsedVI``).

This is a SECOND cache layer, stacked on top of the existing extraction cache
(``lvkit.extractor.extract_vi_xml``, which caches ``.vi`` -> XML). This module
caches the next step: XML -> ``ParsedVI``. Profiling showed the XML decode
(``parse_vi``) dominates a warm-extraction-cache load; deserializing a cached
``ParsedVI`` is far cheaper than re-walking the XML tree.

Design:

- **Key**: the SHARED content-identity spine — the extraction layer's
  already-computed ``.vi`` ``sha256`` (from its ``meta.json`` sidecar, i.e.
  ``cache_paths.sha256_file``) — plus :data:`SCHEMA_VERSION` and the running
  ``lvkit.__version__``. Reusing that one signal (instead of re-hashing the XML)
  means a ``.vi`` change invalidates THIS cache exactly as it invalidates
  extraction/index/render, with no redundant read. Only ``layout=False`` is
  cached, so no ``layout`` key dimension is needed. Falls back to hashing the
  XML bytes when there's no sidecar (ad-hoc / temp extractions). The version
  components make a schema or tool change an automatic MISS (never a
  silently-stale hit) -- bump ``SCHEMA_VERSION`` whenever ``ParsedVI`` or a
  nested dataclass changes shape in a way that would make an old pickle wrong.
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
import json
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


def _extraction_content_id(bd_xml: Path) -> str | None:
    """The ``.vi`` content sha the EXTRACTION layer already computed, read from
    its ``meta.json`` sidecar next to ``bd_xml`` — the shared content-identity
    spine (``cache_paths.sha256_file``, written by ``extractor._write_cache_meta``).

    Reusing it means the parse cache keys off the SAME signal every other cache
    layer uses (a ``.vi`` change → new sha → miss, everywhere) WITHOUT a second
    full read+hash of the (large) XML. The ``.vi`` sha is a complete identity
    for a ``layout=False`` ParsedVI: the BD/FP/main XML and the deterministic
    front-panel-heap-size guard are all pure functions of that content.

    ``None`` when there is no sidecar (an ad-hoc / temp extraction, e.g. diff's
    git-blob VIs) — the caller then falls back to hashing the XML bytes.
    """
    name = Path(bd_xml).name
    if not name.endswith("_BDHb.xml"):
        return None
    meta = Path(bd_xml).parent / f"{name[: -len('_BDHb.xml')]}.meta.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sha = data.get("sha256")
    return sha if isinstance(sha, str) and sha else None


def _hash_xml_bytes(
    bd_xml: Path, fp_xml: Path | str | None, main_xml: Path | str | None,
) -> str | None:
    """Fallback content-id: hash the actual XML bytes fed to the parse. Used
    only when there is no extraction sidecar to reuse (ad-hoc/temp)."""
    try:
        h = hashlib.sha256()
        h.update(bd_xml.read_bytes())
        for p in (fp_xml, main_xml):
            if p is not None and Path(p).exists():
                h.update(b"\x01")
                h.update(Path(p).read_bytes())
            else:
                h.update(b"\x00")
        return h.hexdigest()
    except OSError:
        logger.debug("parse cache: failed to hash inputs", exc_info=True)
        return None


def compute_key(
    bd_xml: Path | str,
    fp_xml: Path | str | None,
    main_xml: Path | str | None,
) -> str | None:
    """Content-addressed cache key for one ``parse_vi(layout=False)`` call.

    Derives the content-id from the SHARED spine — the extraction layer's
    already-computed ``.vi`` sha (:func:`_extraction_content_id`) — so a change
    invalidates this cache the same way it invalidates every other, with no
    redundant re-hash of the XML. Falls back to hashing the XML bytes when
    there's no extraction sidecar. The key folds in :data:`SCHEMA_VERSION` and
    the running ``__version__`` so a schema/tool change is an automatic miss,
    never a silently-stale hit.

    Returns ``None`` (never raises) if the content-id can't be obtained — the
    caller then simply skips the cache and parses normally.
    """
    bd = Path(bd_xml)
    content_id = _extraction_content_id(bd) or _hash_xml_bytes(bd, fp_xml, main_xml)
    if content_id is None:
        return None
    h = hashlib.sha256()
    h.update(content_id.encode())
    h.update(f"|schema={SCHEMA_VERSION}|version={__version__}".encode())
    return h.hexdigest()


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

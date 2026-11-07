"""The vi.lib and primitive catalogs a run reads.

A project's own additions live in its ``.lvkit/`` folder. Each catalog is built
once per ``.lvkit/`` folder (``None`` is the shipped data alone) and kept while
that folder's JSON files are unchanged; a run names its folder in
``RunSettings.project_store`` and reads the catalogs of that folder, so two runs
for different projects never see each other's additions. A file in the folder
added, removed or rewritten (by the user, a skill, or lvkit recording a vi.lib
VI's terminal layout) rebuilds that folder's catalog at the next lookup.

Catalogs are only read once built: what a run learns goes to its own ``fork()``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TypeVar

from .primitive_resolver.resolver import PrimitiveResolver
from .vilib_resolver.resolver import VILibResolver

# Each JSON file of a store with its modification time and size: equal stamps
# mean the catalog built from the store is still the store's catalog.
_Stamp = tuple[tuple[str, int, int], ...]
_C = TypeVar("_C", VILibResolver, PrimitiveResolver)

_lock = threading.Lock()
_vilib: dict[Path | None, tuple[_Stamp, VILibResolver]] = {}
_primitive: dict[Path | None, tuple[_Stamp, PrimitiveResolver]] = {}


def _key(store: Path | None) -> Path | None:
    return store.resolve() if store is not None else None


def _stamp(store: Path | None) -> _Stamp:
    if store is None or not store.is_dir():
        return ()
    stamps = []
    for path in sorted(store.rglob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        stamps.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(stamps)


def _catalog_for(
    built: dict[Path | None, tuple[_Stamp, _C]], make: type[_C], store: Path | None
) -> _C:
    key = _key(store)
    stamp = _stamp(key)
    with _lock:
        held = built.get(key)
        if held is None or held[0] != stamp:
            held = (stamp, make(project_data_dir=key))
            built[key] = held
        return held[1]


def vilib_catalog_for(store: Path | None) -> VILibResolver:
    """The vi.lib catalog for the ``.lvkit/`` folder ``store`` (``None``: shipped
    data only), rebuilt when the folder's files change."""
    return _catalog_for(_vilib, VILibResolver, store)


def primitive_catalog_for(store: Path | None) -> PrimitiveResolver:
    """The primitive catalog for the ``.lvkit/`` folder ``store`` (``None``:
    shipped data only), rebuilt when the folder's files change."""
    return _catalog_for(_primitive, PrimitiveResolver, store)

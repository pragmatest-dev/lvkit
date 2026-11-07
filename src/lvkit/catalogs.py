"""The vi.lib and primitive catalogs a run reads.

A project's own additions live in its ``.lvkit/`` folder. Each catalog is built
once per ``.lvkit/`` folder (``None`` is the shipped data alone) and kept; a run
names its folder in ``RunSettings.project_store`` and reads the catalogs of that
folder, so two runs for different projects never see each other's additions and
a run never reloads the catalog JSON.

Catalogs are only read once built: what a run learns goes to its own ``fork()``.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .primitive_resolver.resolver import PrimitiveResolver
from .vilib_resolver.resolver import VILibResolver

_lock = threading.Lock()
_vilib: dict[Path | None, VILibResolver] = {}
_primitive: dict[Path | None, PrimitiveResolver] = {}


def _key(store: Path | None) -> Path | None:
    return store.resolve() if store is not None else None


def vilib_catalog_for(store: Path | None) -> VILibResolver:
    """The vi.lib catalog for the ``.lvkit/`` folder ``store`` (``None``: shipped
    data only), built on first use."""
    key = _key(store)
    with _lock:
        catalog = _vilib.get(key)
        if catalog is None:
            catalog = VILibResolver(project_data_dir=key)
            _vilib[key] = catalog
        return catalog


def primitive_catalog_for(store: Path | None) -> PrimitiveResolver:
    """The primitive catalog for the ``.lvkit/`` folder ``store`` (``None``:
    shipped data only), built on first use."""
    key = _key(store)
    with _lock:
        catalog = _primitive.get(key)
        if catalog is None:
            catalog = PrimitiveResolver(project_data_dir=key)
            _primitive[key] = catalog
        return catalog

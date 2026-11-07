"""What a project's ``.lvkit/`` catalog additions change, entry by entry.

A project's catalog entries change what a VI's graph says (terminal indices and
names of a call, unwired defaults, render names), so a result cached for a VI
(a render, a diff, an index row) is stale once an entry the VI uses was edited.
A VI uses a set of :class:`CatalogKey` (``catalog_keys.catalog_keys``); the
project's entries are hashed per key here, and a result records the hash of each
of its keys. An edit to one entry changes the hash of that key only, so only the
results of the VIs that use the key are stale. Shipped entries are never hashed:
lvkit's own fingerprint covers them.

Stdlib-light like :mod:`lvkit.output_cache`: checking a cached result imports
nothing heavy.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Collection, Iterator
from pathlib import Path
from typing import Any, NamedTuple

from ._data import load_primitives

# Each JSON file of a store with its modification time and size: equal stamps
# mean the store's files are as they were.
StoreStamp = tuple[tuple[str, int, int], ...]


class CatalogKey(NamedTuple):
    """One catalog entry a VI looks up. ``kind`` is ``primitive`` (a primResID),
    ``node_type`` (a node class such as ``aBuild``), ``vilib`` (the catalog file
    name of a vi.lib VI, as ``VILibResolver.catalog_file_name`` gives it) or
    ``type`` (a typedef's qualified name)."""

    kind: str
    key: str


def resolved_store(store: Path | None) -> Path | None:
    return store.resolve() if store is not None else None


def store_stamp(store: Path | None) -> StoreStamp:
    """The modification time and size of every JSON file of ``store``."""
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


def iter_category_entries(vilib_dir: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """``(category, entry data)`` of every entry of the vi.lib catalog files in
    ``vilib_dir``: the files its ``_index.json`` lists, in that order."""
    index_path = vilib_dir / "_index.json"
    if not index_path.exists():
        return
    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)
    for category, filename in index.get("categories", {}).items():
        category_path = vilib_dir / filename
        if not category_path.exists():
            continue
        with open(category_path, encoding="utf-8") as f:
            data = json.load(f)
        for entry_data in data.get("entries", []):
            yield category, entry_data


def _vi_file_name(name: str) -> str:
    return name if name.endswith(".vi") else f"{name}.vi"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _collect(store: Path) -> dict[CatalogKey, list[str]]:
    """Every entry the store's files define, under each key a lookup finds it by."""
    parts: dict[CatalogKey, list[str]] = {}

    def add(key: CatalogKey, entry: object) -> None:
        parts.setdefault(key, []).append(_digest(entry))

    prims = load_primitives(store / "primitives.json")
    for prim_id, entry in prims["primitives"].items():
        add(CatalogKey("primitive", str(prim_id)), entry)
    for node_type, entry in prims["node_types"].items():
        add(CatalogKey("node_type", node_type), entry)
    for subdir in ("vilib", "openg", "drivers"):
        for category, entry in iter_category_entries(store / subdir):
            names = [entry.get("name", ""), *entry.get("match_names", [])]
            if entry.get("base_vi"):
                names.append(entry["base_vi"])
            for name in names:
                if name:
                    add(CatalogKey("vilib", _vi_file_name(name)), [category, entry])
    types_path = store / "vilib" / "_types.json"
    if types_path.exists():
        with open(types_path, encoding="utf-8") as f:
            for typedef_path, entry in json.load(f).items():
                add(CatalogKey("type", typedef_path), entry)
    return parts


_lock = threading.Lock()
_hashed: dict[Path, tuple[StoreStamp, dict[CatalogKey, str]]] = {}


def project_entry_hashes(store: Path | None) -> dict[CatalogKey, str]:
    """A hash of what the store's files define for each key (empty for no store).
    Kept while the store's JSON files are unchanged."""
    root = resolved_store(store)
    if root is None:
        return {}
    stamp = store_stamp(root)
    with _lock:
        held = _hashed.get(root)
        if held is None or held[0] != stamp:
            try:
                parts = _collect(root)
            except (OSError, ValueError):
                parts = {}
            held = (stamp, {k: _digest(sorted(v)) for k, v in parts.items()})
            _hashed[root] = held
        return held[1]


def catalog_stamp(store: Path | None, keys: Collection[CatalogKey]) -> str:
    """The record kept with a result built from ``keys``: each key with the hash
    of the store's entries for it (``""`` for a key the store defines nothing
    for), as JSON text."""
    hashes = project_entry_hashes(store)
    return json.dumps(
        sorted([k.kind, k.key, hashes.get(k, "")] for k in set(keys)),
        separators=(",", ":"),
    )


def catalog_stamp_fresh(store: Path | None, recorded: object) -> bool:
    """Whether a record made by :func:`catalog_stamp` still holds: the store's
    hash of every recorded key is as it was. No record is fresh."""
    if not isinstance(recorded, str) or not recorded:
        return True
    try:
        rows = json.loads(recorded)
    except ValueError:
        return False
    hashes = project_entry_hashes(store)
    return all(hashes.get(CatalogKey(kind, key), "") == h for kind, key, h in rows)

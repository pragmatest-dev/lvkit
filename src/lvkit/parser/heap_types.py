"""Decode the pre-LV9 ``DTHP`` block -- the block-diagram / front-panel heap type
pool -- into a ``TypeID -> LVType`` map.

A block diagram and a front panel name their terminals' types by ``TypeID(N)``.
In an LV9+ VI, ``VCTP`` holds the type pool and ``DTHP`` only a slice of it. A
pre-LV9 (LV8.x) VI has no ``VCTP``: its ``DTHP`` block IS the pool, laid out like
``VCTP`` (pylabview leaves it raw; the extractor dumps it as ``*_DTHP.bin``)::

    u32  count
    count x TypeDesc            (flat list; see ``td_pool``)
    u16  ntop
    ntop x u16                  TD index of heap TypeID k  (k = 1..ntop)

Heap ``TypeID(N)`` therefore resolves to flat TypeDesc ``top[N - 1]``. An LV9+
VI's ``DTHP`` parses to ``TypeDescSlice`` XML and yields no sidecar, so this
decoder is a no-op there.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..models import LVType
from .td_pool import TdPool, block_sidecar_path, read_td_list


def heap_sidecar_path(xml_path: Path | str) -> Path:
    """The ``*_DTHP.bin`` sidecar beside a VI's extracted XML (see
    ``block_sidecar_path``)."""
    return block_sidecar_path(xml_path, "DTHP")


@dataclass(frozen=True)
class HeapTypes:
    """The heap types a ``DTHP`` block decodes to, and why the block could not be
    read to its end (None when it was). A descriptor that did not decode has an
    unreadable type in ``types``; ``failure`` is for the block itself."""

    types: dict[int, LVType] = field(default_factory=dict)
    failure: str | None = None


def decode_heap_types(dthp_bytes: bytes) -> HeapTypes:
    """Decode a ``*_DTHP.bin`` sidecar into its ``HeapTypes``.

    An empty block (a pool with no descriptors) decodes to nothing and no failure;
    a block that does not follow the layout above has a ``failure`` saying where.
    """
    listed = read_td_list(dthp_bytes)
    if listed.failure is not None:
        return HeapTypes(failure=f"the heap type pool is unreadable: {listed.failure}")
    if not listed.tds:
        return HeapTypes()
    end = listed.end
    if end + 2 > len(dthp_bytes):
        return HeapTypes(
            failure="the heap type pool is unreadable: it has no TypeID table"
        )
    (ntop,) = struct.unpack_from(">H", dthp_bytes, end)
    if end + 2 + 2 * ntop > len(dthp_bytes):
        return HeapTypes(
            failure=(
                "the heap type pool is unreadable: its TypeID table lists "
                f"{ntop} entries the block does not hold"
            )
        )
    pool = TdPool(listed.tds)
    top = struct.unpack_from(f">{ntop}H", dthp_bytes, end + 2)
    types: dict[int, LVType] = {}
    for type_id, td_index in enumerate(top, start=1):
        if td_index >= len(pool.lvtypes):
            return HeapTypes(
                types,
                failure=(
                    "the heap type pool is unreadable: its TypeID table names "
                    f"descriptor {td_index}, which is not listed"
                ),
            )
        types[type_id] = pool.lvtypes[td_index]
    return HeapTypes(types)

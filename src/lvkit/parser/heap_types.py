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
from pathlib import Path

from ..models import LVType
from .td_pool import TdPool, block_sidecar_path, read_td_list


def heap_sidecar_path(xml_path: Path | str) -> Path:
    """The ``*_DTHP.bin`` sidecar beside a VI's extracted XML (see
    ``block_sidecar_path``)."""
    return block_sidecar_path(xml_path, "DTHP")


def decode_heap_types(dthp_bytes: bytes) -> dict[int, LVType]:
    """Decode a ``*_DTHP.bin`` sidecar into ``{TypeID: LVType}``.

    Only TypeIDs whose TypeDesc decodes to a type appear. Returns ``{}`` for an
    empty block or on any structural surprise -- the caller then keeps its
    existing resolution. Never raises on malformed input.
    """
    tds, end = read_td_list(dthp_bytes)
    if not tds or end + 2 > len(dthp_bytes):
        return {}
    (ntop,) = struct.unpack_from(">H", dthp_bytes, end)
    if end + 2 + 2 * ntop > len(dthp_bytes):
        return {}
    pool = TdPool(tds)
    top = struct.unpack_from(f">{ntop}H", dthp_bytes, end + 2)
    return {
        type_id: lv
        for type_id, td_index in enumerate(top, start=1)
        if td_index < len(pool.lvtypes) and (lv := pool.lvtypes[td_index]) is not None
    }

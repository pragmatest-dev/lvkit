"""Decode the pre-LV9 ``CONP`` block -- the connector-pane type pool -- into named
``LVType``s, one per connector-pane terminal.

Pre-LV9 (LV8.x) VIs have no ``VCTP`` consolidated type pool; the connector-pane
terminal types live in ``CONP`` instead, in the SAME bottom-up TypeDesc format
VCTP uses (decoded by ``td_pool``) -- but pylabview leaves ``CONP`` raw for old
VIs (its block parser has a ``# we do not know how to parse complex form of
CONP`` TODO). The extractor dumps the decompressed block as a ``*_CONP.bin``
sidecar, which we decode here.

Unlike the FP-heap reconstruction (``fp_heap_type``), CONP carries the **names**:
cluster field names, the terminal name, the class name of a class refnum, enum
item labels, and the refnum kind. It is therefore the authoritative source for a
pre-LV9 VI's *interface* -- but ONLY the interface: a front-panel control not
wired to the connector pane is not in CONP (use the heap reconstruction there).

One TD has ``type_code == 0xf0`` -- the connector-pane map: ``u16 nslots`` then
``nslots x u16`` TD-index per slot (empty slots point at a ``0x00`` Void TD).
That array, in slot order, is the terminal list.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..lv_type_codes import TD_FULL_TYPE
from ..models import LVType
from .td_pool import TdPool, block_sidecar_path, read_td_list


@dataclass(frozen=True)
class ConpTerminal:
    """One connector-pane terminal recovered from CONP: its slot, its label, and
    its fully-named type (an unreadable type when its descriptor did not decode)."""

    slot: int
    name: str | None
    lv_type: LVType


@dataclass(frozen=True)
class ConpTerminals:
    """The terminals a CONP block decodes to, and why the block could not be read
    (None when it was). An LV9+ VI's empty stub decodes to no terminals and no
    failure."""

    terminals: list[ConpTerminal] = field(default_factory=list)
    failure: str | None = None


def _conpane_slots(body: bytes) -> list[int]:
    if len(body) < 2:
        return []
    (nslots,) = struct.unpack_from(">H", body, 0)
    slots: list[int] = []
    for s in range(nslots):
        o = 2 + s * 2
        if o + 2 > len(body):
            break
        slots.append(struct.unpack_from(">H", body, o)[0])
    return slots


def _decode(data: bytes) -> ConpTerminals:
    if len(data) < 6:
        return ConpTerminals()
    listed = read_td_list(data)
    if listed.failure is not None:
        return ConpTerminals(
            failure=f"the connector pane type pool is unreadable: {listed.failure}"
        )
    tds = listed.tds
    pool = TdPool(tds)
    slot_map: list[int] = []
    for td in tds:
        if td.type_code == TD_FULL_TYPE.Function:
            slot_map = _conpane_slots(td.body)
    out: list[ConpTerminal] = []
    for slot, tdi in enumerate(slot_map):
        if tdi >= len(tds):
            return ConpTerminals(
                out,
                failure=(
                    "the connector pane type pool is unreadable: its slot "
                    f"{slot} names descriptor {tdi}, which is not listed"
                ),
            )
        if tds[tdi].type_code == TD_FULL_TYPE.Void:
            continue  # empty connector-pane slot
        out.append(
            ConpTerminal(slot=slot, name=pool.names[tdi], lv_type=pool.lvtypes[tdi])
        )
    return ConpTerminals(out)


def conp_sidecar_path(xml_path: Path | str) -> Path:
    """The ``*_CONP.bin`` sidecar beside a VI's extracted XML (see
    ``block_sidecar_path``)."""
    return block_sidecar_path(xml_path, "CONP")


def decode_conp_terminals(conp_bytes: bytes) -> ConpTerminals:
    """Decode a ``*_CONP.bin`` sidecar into its connector-pane terminals.

    An empty/stub CONP (an LV9+ VI, whose types live in VCTP) has no terminals
    and no failure; a block that does not follow the layout has a ``failure``
    saying where.
    """
    return _decode(conp_bytes)

"""Which typedef instances of a VI a link record is for.

A ``TDCC`` link record (a typedef's custom control) lists, in its offset list,
the uids of the heap objects that are the typedef's instances in the front panel
(``LIfp``) or block diagram (``LIbd``) heap. Such an object is a ``typeDef``
whose own ``typeDesc`` is the typedef's type id; when it is the ``ddo`` of a
terminal object, that terminal's ``typeDesc`` is the same typedef instance. A
type id is a position in the VI's type table (``LVType.type_id``), so the record
and every use of that typedef type in the VI are tied without reading a name.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable
from pathlib import Path

from .models import ParsedDependencyRef
from .xml_trees import parsed_xml

_TYPE_ID = re.compile(r"TypeID\((\d+)\)")
# The heap each link table section's uids are in.
_HEAP_SUFFIX = {"LIfp": "_FPHb.xml", "LIbd": "_BDHb.xml"}
_TYPEDEF_CLASS = "typeDef"


def _own_type_id(elem) -> int | None:  # noqa: ANN001
    text = elem.findtext("typeDesc")
    match = _TYPE_ID.search(text or "")
    return int(match.group(1)) if match else None


def typedef_heap_type_ids(heap_xml: Path, uids: set[int]) -> dict[int, set[int]]:
    """For each uid of ``uids`` that is a ``typeDef`` object of the heap in
    ``heap_xml``: the heap type ids of that typedef instance (its own, and its
    terminal's when it is that terminal's ``ddo``)."""
    found: dict[int, set[int]] = {}
    wanted = {str(uid): uid for uid in uids}
    for parent in parsed_xml(heap_xml).iter():
        for child in parent:
            uid = wanted.get(child.get("uid") or "")
            if uid is None or child.get("class") != _TYPEDEF_CLASS:
                continue
            ids = {_own_type_id(child)}
            if child.tag == "ddo":
                ids.add(_own_type_id(parent))
            found[uid] = {i for i in ids if i is not None}
    return found


def tie_typedef_records(
    refs: Iterable[ParsedDependencyRef],
    main_xml: Path,
    flat_ids: dict[int, int],
) -> list[ParsedDependencyRef]:
    """``refs`` with each ``TDCC`` record's ``type_ids`` filled in: the flat type
    ids (``flat_ids`` maps a heap type id to its flat id) of the typedef
    instances its uids are."""
    refs = list(refs)
    heaps: dict[str, dict[int, set[int]]] = {}
    for section, suffix in _HEAP_SUFFIX.items():
        uids = {
            uid
            for ref in refs
            if ref.record == "TDCC" and ref.section == section
            for uid in ref.uids
        }
        heap_xml = main_xml.with_name(main_xml.stem + suffix)
        if uids and heap_xml.exists():
            heaps[section] = typedef_heap_type_ids(heap_xml, uids)
    tied: list[ParsedDependencyRef] = []
    for ref in refs:
        heap = heaps.get(ref.section or "")
        if ref.record == "TDCC" and heap is not None:
            type_ids = {
                flat_ids[heap_id]
                for uid in ref.uids
                for heap_id in heap.get(uid, ())
                if heap_id in flat_ids
            }
            ref = dataclasses.replace(ref, type_ids=tuple(sorted(type_ids)))
        tied.append(ref)
    return tied

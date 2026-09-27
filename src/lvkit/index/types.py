"""Structural identity for LabVIEW types, and the catalog that records them.

A type's ``type_id`` is a hash of its STRUCTURE -- kind, underlying / ref type,
class or typedef name, dimensions, enum items ``(name, value)``, cluster fields
``(name, child id)``, array / refnum element id -- built bottom-up, so the id of a
composite depends only on the ids of its parts. Two occurrences of the same type
share an id wherever they appear (different VIs, different files); two types that
merely share a NAME but differ in structure get different ids (a name is not
identity). A type nested inside itself bottoms out at an unresolved field
(``type_id`` None) rather than recursing.

The hash is over the type's meaning, never its location, so an id is stable
across machines and checkouts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from ..models import ClusterField, LVType, LVTypeKind
from .model import TypeFact, TypeFieldFact, TypeItemFact

# Kinds whose fields the graph may know even when the LVType itself carries none
# (a reference to a typedef resolves to its cluster).
_FIELDED = (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF)


class TypeCatalog:
    """Records the ``TypeFact`` of every type it is shown, and every type nested in
    it, once each. ``resolve_fields`` supplies a cluster / typedef reference's
    fields when the ``LVType`` has none of its own (the graph's
    ``get_type_fields``)."""

    def __init__(
        self,
        resolve_fields: Callable[[LVType], list[ClusterField] | None] | None = None,
    ) -> None:
        self._resolve_fields = resolve_fields
        # id(lv_type) -> (lv_type, type_id). The object is held so its id cannot
        # be freed and reused by another type while the catalog lives.
        self._memo: dict[int, tuple[LVType, str]] = {}
        self._building: set[int] = set()
        self._cuts = 0  # cycle cut-offs so far
        self.types: dict[str, TypeFact] = {}

    def add(self, lv_type: LVType | None) -> str | None:
        """The ``type_id`` of ``lv_type`` (recording it and its parts), or None
        when there is no type -- or when it is reached again while its own id is
        being built (a self-nesting type ends there, unresolved)."""
        if lv_type is None:
            return None
        key = id(lv_type)
        memoed = self._memo.get(key)
        if memoed is not None:
            return memoed[1]
        if key in self._building:
            self._cuts += 1
            return None
        self._building.add(key)
        cuts_before = self._cuts
        try:
            fields = self._fields_of(lv_type)
            field_facts = [TypeFieldFact(f.name, self.add(f.type)) for f in fields]
            element_id = self.add(lv_type.element_type)
        finally:
            self._building.discard(key)
        items = sorted(
            (TypeItemFact(n, ev.value) for n, ev in (lv_type.values or {}).items()),
            key=lambda i: (i.value, i.name),
        )
        type_id = _hash(lv_type, field_facts, items, element_id)
        self.types.setdefault(
            type_id,
            TypeFact(
                type_id=type_id,
                kind=lv_type.kind,
                descriptor=lv_type.type_descriptor(),
                name=lv_type.typedef_name or lv_type.classname,
                dimensions=lv_type.dimensions,
                element_type_id=element_id,
                fields=field_facts,
                items=items,
            ),
        )
        if self._cuts == cuts_before:
            # Only an id built from complete parts is reusable: one that had a
            # cycle cut beneath it depends on where the walk entered.
            self._memo[key] = (lv_type, type_id)
        return type_id

    def _fields_of(self, lv_type: LVType) -> list[ClusterField]:
        if lv_type.fields:
            return lv_type.fields
        if lv_type.kind in _FIELDED and self._resolve_fields is not None:
            return self._resolve_fields(lv_type) or []
        return []


def _hash(
    lv_type: LVType,
    fields: list[TypeFieldFact],
    items: list[TypeItemFact],
    element_id: str | None,
) -> str:
    canonical = json.dumps(
        [
            lv_type.kind.value,
            lv_type.underlying_type,
            lv_type.ref_type,
            lv_type.classname,
            lv_type.typedef_name,
            lv_type.dimensions,
            lv_type.measure_flavor,
            [[i.name, i.value] for i in items],
            [[f.name, f.type_id] for f in fields],
            element_id,
        ],
        separators=(",", ":"),
    )
    return "t_" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]

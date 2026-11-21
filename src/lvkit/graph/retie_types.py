"""Tie the class and typedef types a load left without a file.

A type is tied to its file once, when its VI or control loads
(``LoadingMixin._store_type_records``), from the link records of the file and of
the dependencies it is nested in. When files load in a cycle (a control whose class
lists a method that calls back into the class), the dependency that holds the
record is still loading when the VI is tied, so the type stays without a file.
Once the load has finished every record is there, so this ties them again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lvkit.models import LVType

from .type_records import type_names

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


def retie_unresolved_types(graph: InMemoryVIGraph) -> None:
    """Give each class or typedef type of a loaded VI that has no file the file
    the records now give it. Idempotent: a type already tied is not touched, and a
    miss recorded while the load was running is forgotten first."""
    for vi_key in graph.list_vis():
        records = graph.type_records_of(vi_key)
        if records is None:
            continue
        untied: list[LVType] = []
        for node in graph.iter_nodes(vi_key):
            types = [getattr(node, "lv_type", None)]
            types.extend(t.lv_type for t in getattr(node, "terminals", None) or [])
            untied.extend(t for t in type_names(types) if t.file_key is None)
        if not untied:
            continue
        for name in [n for n, key in records.resolved.items() if key is None]:
            del records.resolved[name]
        for lv_type in untied:
            lv_type.file_key = graph.recorded_type_key(vi_key, lv_type)
            if lv_type.file_key is not None:
                lv_type.strict_typedef = graph.is_strict_typedef(lv_type)

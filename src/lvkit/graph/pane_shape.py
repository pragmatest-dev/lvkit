"""A connector pane as matched against another: each slot's index, direction and
the shape of its type (what the type is, never a label, a description or how a
class is spelled; a class is the class it resolves to). A pane a refnum records,
the pane a call records and a VI's own pane are compared this one way."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol

from .op_walk import is_void_slot

if TYPE_CHECKING:
    from lvkit.models import LVType, PaneSlot, Terminal


class PaneGraph(Protocol):
    """What the shapes read from the graph: the class a class type resolves to,
    and a VI's connector pane."""

    def class_type_key(self, lv_type: LVType, /) -> str | None: ...

    def vi_interface(
        self, vi_key: str, /
    ) -> tuple[list[Terminal], list[Terminal], list[Terminal], list[Terminal]]: ...


_CLASS_REF_TYPE = "UDClassInst"

# A type as the pane matches it: what it is, never a label or a name.
TypeShape = tuple[object, ...]
PaneShape = frozenset[tuple[int, bool, TypeShape]]


def type_shape(graph: PaneGraph, lv_type: LVType | None) -> TypeShape:
    """What ``lv_type`` is: its kind, its
    underlying and refnum types, the class a class wire carries, and the shapes
    of its elements and fields."""
    if lv_type is None:
        return ()
    if lv_type.ref_type == _CLASS_REF_TYPE:
        return ("class", graph.class_type_key(lv_type) or lv_type.classname)
    return (
        lv_type.kind,
        lv_type.underlying_type,
        lv_type.ref_type,
        lv_type.dimensions,
        type_shape(graph, lv_type.element_type),
        type_shape(graph, lv_type.value_type),
        tuple(type_shape(graph, f.type) for f in lv_type.fields or []),
    )


def slots_shape(graph: PaneGraph, pane: Iterable[PaneSlot]) -> PaneShape:
    """The shape of the pane a refnum records."""
    return frozenset(
        (slot.index, slot.is_output, type_shape(graph, slot.type)) for slot in pane
    )


def terminals_shape(graph: PaneGraph, terminals: Iterable[Terminal]) -> PaneShape:
    """The shape of the pane ``terminals`` fill (a VI's own pane, or the pane a
    call records); an unused slot
    (``op_walk.is_void_slot``) is left out."""
    return frozenset(
        (t.index, t.direction == "output", type_shape(graph, t.lv_type))
        for t in terminals
        if t.index is not None and not is_void_slot(t.lv_type)
    )


def vi_shape(graph: PaneGraph, key: str) -> PaneShape:
    """The shape of the connector pane of the VI ``key``."""
    inputs, _, outputs, _ = graph.vi_interface(key)
    return terminals_shape(graph, [*inputs, *outputs])

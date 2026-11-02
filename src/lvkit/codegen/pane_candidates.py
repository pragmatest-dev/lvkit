"""The one call that is right for every VI of the project a reference value can hold.

A reference whose VI is only known when the diagram runs holds a VI of the
project with the connector pane its strictly typed refnum records: the slots of
the pane, each with its direction and type. A VI's function and a method of a
class are called differently (``class_model.MethodShape``): a method takes its
class input as ``self``. Both return nothing when the connector pane has no
output, else a ``NamedTuple`` of the pane's outputs.
``call_form`` finds the VIs of the project whose pane is that pane, slot by slot
and type by type, and the way each is called; the call is right only when they
are all called the same way."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .ast_utils import interface_names
from .class_attributes import is_private
from .class_model import MethodKind, MethodShape, member_shape, not_a_method

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.models import LVType, PaneSlot, Terminal

_CLASS_REF_TYPE = "UDClassInst"

# A type as the pane matches it: what it is, never a label or a name.
TypeShape = tuple[object, ...]


class ResultStyle(Enum):
    """How a call gives its results back."""

    NONE = "none"  # nothing
    FIELDS = "fields"  # the fields of a NamedTuple


@dataclass(frozen=True)
class CallForm:
    """How one VI is called by keyword: ``keywords`` by pane index (the class
    input is ``self``), the results by pane index and how they come back
    (``fields`` names a NamedTuple's field by pane index), and the pane index of
    a class output that is the object the call was made on. ``member`` is the
    method of a class the form was read from; ``class_key`` its class when the
    call takes the class input."""

    keywords: tuple[tuple[int, str], ...]
    style: ResultStyle
    fields: tuple[tuple[int, str], ...]
    receiver_out: int | None
    class_key: str | None
    member: MethodShape | None = None

    def same_call(self, other: CallForm) -> bool:
        """Whether one generated call is right for both."""
        return (
            self.keywords,
            self.style,
            self.fields,
            self.receiver_out,
            self.class_key,
        ) == (
            other.keywords,
            other.style,
            other.fields,
            other.receiver_out,
            other.class_key,
        )


@dataclass(frozen=True)
class Candidates:
    """The VIs of the project with a pane, and how each is called, by key."""

    forms: dict[str, CallForm]

    @property
    def members(self) -> list[str]:
        return sorted(k for k, f in self.forms.items() if f.member is not None)

    def one_form(self) -> CallForm | None:
        """The call right for every candidate, or None when they differ."""
        forms = list(self.forms.values())
        first = forms[0]
        if all(first.same_call(f) for f in forms[1:]):
            return next((f for f in forms if f.member is not None), first)
        return None


def type_shape(
    graph: InMemoryVIGraph, lv_type: LVType | None, vi_key: str
) -> TypeShape:
    """What ``lv_type`` is, as seen from the VI ``vi_key``: its kind, its
    underlying and refnum types, the class a class wire carries, and the shapes
    of its elements and fields."""
    if lv_type is None:
        return ()
    if lv_type.ref_type == _CLASS_REF_TYPE:
        return ("class", graph.class_type_key(lv_type, vi_key) or lv_type.classname)
    return (
        lv_type.kind,
        lv_type.underlying_type,
        lv_type.ref_type,
        lv_type.dimensions,
        type_shape(graph, lv_type.element_type, vi_key),
        type_shape(graph, lv_type.value_type, vi_key),
        tuple(type_shape(graph, f.type, vi_key) for f in lv_type.fields or []),
    )


def _pane_shape(
    graph: InMemoryVIGraph, pane: tuple[PaneSlot, ...], vi_key: str
) -> frozenset[tuple[int, bool, TypeShape]]:
    return frozenset(
        (slot.index, slot.is_output, type_shape(graph, slot.type, vi_key))
        for slot in pane
    )


def _vi_shape(
    graph: InMemoryVIGraph, key: str
) -> frozenset[tuple[int, bool, TypeShape]]:
    inputs, _, outputs, _ = graph.vi_interface(key)
    return frozenset(
        (t.index, t.direction == "output", type_shape(graph, t.lv_type, key))
        for t in [*inputs, *outputs]
    )


def _function_form(graph: InMemoryVIGraph, key: str) -> CallForm:
    inputs, off_inputs, outputs, _ = graph.vi_interface(key)
    names = interface_names(inputs, off_inputs, outputs)
    results: list[Terminal] = [t for t in outputs if t.id in names.fields]
    return CallForm(
        keywords=tuple(
            sorted(
                (t.index, names.parameters[t.id])
                for t in inputs
                if t.id in names.parameters
            )
        ),
        style=ResultStyle.FIELDS if results else ResultStyle.NONE,
        fields=tuple(sorted((t.index, names.fields[t.id]) for t in results)),
        receiver_out=None,
        class_key=None,
    )


def _method_form(shape: MethodShape) -> CallForm:
    keywords = dict(shape.parameters)
    if shape.receiver_in is not None:
        keywords[shape.receiver_in] = "self"
    style = ResultStyle.FIELDS if shape.results else ResultStyle.NONE
    return CallForm(
        keywords=tuple(sorted(keywords.items())),
        style=style,
        fields=tuple(sorted(shape.result_fields.items())),
        receiver_out=None if shape.returns_receiver else shape.receiver_out,
        class_key=shape.class_key if shape.receiver_in is not None else None,
        member=shape,
    )


def candidates(
    graph: InMemoryVIGraph, pane: tuple[PaneSlot, ...], caller_key: str
) -> Candidates:
    """The VIs of the project whose connector pane is ``pane`` (recorded by the
    VI ``caller_key``), and how each is called. A member that has no method,
    that is dynamic dispatch (Call By Reference cannot call one), or that is
    private to a class the caller is not a VI of (only its own class's VIs call
    a private VI), is left out."""
    wanted = _pane_shape(graph, pane, caller_key)
    caller_class = graph.owning_class_key(caller_key)
    forms: dict[str, CallForm] = {}
    for key in sorted(graph.list_vis()):
        if graph.is_stub(key) or _vi_shape(graph, key) != wanted:
            continue
        shape = member_shape(graph, key)
        if shape is None:
            forms[key] = _function_form(graph, key)
        elif (
            shape.kind is not MethodKind.DYNAMIC
            and not_a_method(graph, key) is None
            and (not is_private(shape.name) or shape.class_key == caller_class)
        ):
            forms[key] = _method_form(shape)
    return Candidates(forms)

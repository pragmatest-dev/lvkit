"""The kinds of control the panel builds, and what the panel knows about one
control or indicator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from lvkit.models import LVType, LVTypeKind


class PanelKind(Enum):
    """The kinds of control the panel builds, each with the builder functions of
    the ``controls`` package that draw it. A kind is chosen by the control's
    class and either the Python type of its value (a number, Boolean, string or
    path) or the kind of its LabVIEW type (an enum, ring or cluster)."""

    NUMBER = ("number", "stdNum", ("int", "float"), ())
    BOOLEAN = ("bool", "stdBool", ("bool",), ())
    STRING = ("string", "stdString", ("str",), ())
    PATH = ("path", "stdPath", ("Path | None",), ())
    ENUM = ("choice", "stdEnum", ("int",), ())
    RING = ("choice", "stdRing", ("int",), ())
    ARRAY = ("array", "indArr", (), (LVTypeKind.ARRAY,))
    CLUSTER = (
        "cluster",
        "stdClust",
        (),
        (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF),
    )

    def __init__(
        self,
        stem: str,
        control_type: str,
        python_types: tuple[str, ...],
        type_kinds: tuple[LVTypeKind, ...],
    ) -> None:
        self.control_builder = f"{stem}_control"
        self.indicator_builder = f"{stem}_indicator"
        self.port_builder = f"{stem}_port"
        self.control_type = control_type
        self.python_types = python_types
        self.type_kinds = type_kinds

    @classmethod
    def of(
        cls, control_type: str, python_type: str, lv_type: LVType | None
    ) -> PanelKind | None:
        for kind in cls:
            if kind.control_type != control_type:
                continue
            if python_type in kind.python_types:
                return kind
            if lv_type is not None and lv_type.kind in kind.type_kinds:
                return kind
        return None


@dataclass(frozen=True)
class PanelControl:
    """One control or indicator of the panel.

    ``key`` is the name the hooks pass for it and ``member`` the member of the
    VI's name enum that holds it (None when the VI has no hooks). ``parameter``
    is the keyword the function takes the control by (a connector-pane control)
    and ``field`` the result field the function returns the indicator in (a
    connector-pane indicator). ``kind`` is None for a control the panel cannot
    build; ``gap`` then says why. ``items`` are the (label, value) pairs of an
    enum or ring. A cluster has its fields in ``members`` (each one a
    ``PanelControl`` whose ``key`` is the field's attribute name) and ``named``
    says whether the function holds it as an object with those attributes (a
    cluster type definition) or as a tuple. A one-dimensional array has the
    control that draws one of its elements in ``element``."""

    label: str
    kind: PanelKind | None
    is_indicator: bool
    bounds: tuple[int, int, int, int]  # top, left, bottom, right
    key: str
    member: str | None
    parameter: str | None
    field: str | None
    default: str | int | float | bool
    integer: bool
    gap: str | None = None
    items: tuple[tuple[str, int], ...] = ()
    named: bool = False
    members: tuple[PanelControl, ...] = ()
    element: PanelControl | None = None

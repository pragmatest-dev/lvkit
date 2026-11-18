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
    path) or the kind of its LabVIEW type (an enum, ring or cluster). An error
    cluster is a cluster whose type is LabVIEW's error cluster; ``describe``
    picks it, ``of`` never does. A variant is chosen by its control class alone,
    and so are the references and LabVIEW class objects, which the panel does not
    draw (``HIDDEN``): their value is the type's default."""

    NUMBER = ("number", "stdNum", ("int", "float"), ())
    COMPLEX = ("complex", "stdNum", ("complex",), ())
    BOOLEAN = ("bool", "stdBool", ("bool",), ())
    STRING = ("string", "stdString", ("str",), ())
    PATH = ("path", "stdPath", ("Path | None",), ())
    COMBO = ("combo", "stdComboBox", ("str",), ())
    ENUM = ("choice", "stdEnum", ("int",), ())
    RING = ("choice", "stdRing", ("int",), ())
    ARRAY = ("array", "indArr", (), (LVTypeKind.ARRAY,))
    CLUSTER = (
        "cluster",
        "stdClust",
        (),
        (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF),
    )

    ERROR = ("error", "stdClust", (), ())
    VARIANT = ("variant", "stdLvVariant", (), (), True)
    HIDDEN = ("hidden", ("stdRefNum", "udClassDDO"), (), (), True)

    def __init__(
        self,
        stem: str,
        control_types: str | tuple[str, ...],
        python_types: tuple[str, ...],
        type_kinds: tuple[LVTypeKind, ...],
        any_type: bool = False,
    ) -> None:
        self.control_builder = f"{stem}_control"
        self.indicator_builder = f"{stem}_indicator"
        self.port_builder = f"{stem}_port"
        self.control_types = (
            (control_types,) if isinstance(control_types, str) else control_types
        )
        self.any_type = any_type
        self.python_types = python_types
        self.type_kinds = type_kinds

    @classmethod
    def of(
        cls, control_type: str, python_type: str, lv_type: LVType | None
    ) -> PanelKind | None:
        for kind in cls:
            if control_type not in kind.control_types:
                continue
            if kind.any_type or python_type in kind.python_types:
                return kind
            if lv_type is not None and lv_type.kind in kind.type_kinds:
                return kind
        return None


_VISIBLE = frozenset({"visible"})
_DISABLE = _VISIBLE | {"disabled"}
_FOCUS = _DISABLE | {"key_focus"}
_LIMITS = _FOCUS | {"minimum", "maximum"}

# The control properties (``member`` of ``control_properties.json``) the widget of a
# kind can show, for a control and for an indicator: what the ``Port`` of each
# builder in ``runtime/widgets*.py`` holds appliers for, and Visible, which every
# drawn control has (``PanelWidgets.box``). Every kind shows Visible and Disabled (a
# container applies Disabled to the inputs inside it). What remains unshown has no
# widget meaning: Key Focus on a kind with no single focusable input (an indicator,
# a complex number's two boxes, a cluster, an array, an error cluster, a variant's
# text), Minimum / Maximum on anything but a number control, and Strings on
# anything but a Combo Box.
SHOWN_PROPERTIES: dict[tuple[PanelKind, bool], frozenset[str]] = {
    (PanelKind.NUMBER, False): _LIMITS,
    (PanelKind.NUMBER, True): _DISABLE,
    (PanelKind.COMPLEX, False): _DISABLE,
    (PanelKind.COMPLEX, True): _DISABLE,
    (PanelKind.BOOLEAN, False): _FOCUS,
    (PanelKind.BOOLEAN, True): _DISABLE,
    (PanelKind.STRING, False): _FOCUS,
    (PanelKind.STRING, True): _DISABLE,
    (PanelKind.PATH, False): _FOCUS,
    (PanelKind.PATH, True): _DISABLE,
    (PanelKind.COMBO, False): _FOCUS | {"strings"},
    (PanelKind.COMBO, True): _DISABLE | {"strings"},
    (PanelKind.ENUM, False): _FOCUS,
    (PanelKind.ENUM, True): _DISABLE,
    (PanelKind.RING, False): _FOCUS,
    (PanelKind.RING, True): _DISABLE,
    (PanelKind.VARIANT, False): _DISABLE,
    (PanelKind.VARIANT, True): _DISABLE,
    (PanelKind.ARRAY, False): _DISABLE,
    (PanelKind.ARRAY, True): _DISABLE,
    (PanelKind.CLUSTER, False): _DISABLE,
    (PanelKind.CLUSTER, True): _DISABLE,
    (PanelKind.ERROR, False): _DISABLE,
    (PanelKind.ERROR, True): _DISABLE,
}


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
    control that draws one of its elements in ``element`` and the elements it is
    saved with in ``initial``. ``required`` says whether the parameter has no
    default in the function's signature. ``properties`` are the control properties
    (by member name, sorted) the VI sets or reads on it and the ones the file
    stores it in (hidden, disabled)."""

    label: str
    kind: PanelKind | None
    is_indicator: bool
    bounds: tuple[int, int, int, int]  # top, left, bottom, right
    key: str
    member: str | None
    parameter: str | None
    field: str | None
    default: str | int | float | bool | complex
    integer: bool
    gap: str | None = None
    items: tuple[tuple[str, int], ...] = ()
    named: bool = False
    members: tuple[PanelControl, ...] = ()
    element: PanelControl | None = None
    initial: tuple[object, ...] = ()
    required: bool = False
    properties: tuple[str, ...] = ()

    @property
    def unshown_properties(self) -> tuple[str, ...]:
        """The properties of ``properties`` (the ones the VI sets or reads, and the
        ones the file stores it in) that the widget of its kind cannot show."""
        if self.kind is None or self.hidden:
            return ()
        shown = SHOWN_PROPERTIES[(self.kind, self.is_indicator)]
        return tuple(p for p in self.properties if p not in shown)

    @property
    def hidden(self) -> bool:
        """True for a control the panel does not draw (a reference or a LabVIEW
        class object): the function gets the type's default for it."""
        return self.kind is PanelKind.HIDDEN

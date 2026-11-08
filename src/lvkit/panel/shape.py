"""The part of a ``PanelControl`` that depends on the control and its type alone:
its kind, starting value and, for an enum or ring, its items, for a cluster, its
fields. A kind with no widget, or a control missing what its widget needs, is a
gap."""

from __future__ import annotations

from dataclasses import replace

from lvkit.codegen.ast_utils import field_attribute_names
from lvkit.models import LVType, _is_error_cluster
from lvkit.parser.models import ParsedFPControl

from .choices import choice_default, choice_items
from .kinds import PanelControl, PanelKind


def _scalar_default(kind: PanelKind, saved: object) -> str | int | float | bool:
    if kind is PanelKind.BOOLEAN:
        return bool(saved)
    if kind in (PanelKind.STRING, PanelKind.PATH):
        return "" if saved is None else str(saved)
    if isinstance(saved, (int, float)) and not isinstance(saved, bool):
        return saved
    return 0


def _no_widget(control: ParsedFPControl, python_type: str) -> str:
    return (
        f"the panel has no widget for a {control.control_type} "
        f"control of type {python_type}"
    )


def _cluster(
    base: PanelControl, control: ParsedFPControl, lv_type: LVType | None
) -> PanelControl:
    """A cluster: each field built by its own kind. The function holds a cluster
    type definition as an object with an attribute for each field and any other
    cluster as a tuple of the fields in order."""
    fields = lv_type.fields if lv_type is not None else None
    if lv_type is None or not fields or lv_type.classname:
        return replace(
            base,
            kind=None,
            gap=f"the panel needs the fields of the {control.control_type} control",
        )
    if _is_error_cluster(lv_type):
        return replace(
            base,
            kind=None,
            gap=f"the panel has no widget for an error cluster ({control.name!r})",
        )
    if len(control.children) != len(fields):
        return replace(
            base,
            kind=None,
            gap=(
                f"the panel found {len(control.children)} fields in the "
                f"{control.control_type} control and {len(fields)} in its type"
            ),
        )
    named = bool(lv_type.typedef_name)
    keys = (
        field_attribute_names(f.name for f in fields)
        if named
        else [str(i) for i in range(len(fields))]
    )
    members = tuple(
        describe(
            child,
            field.type or child.lv_type,
            child.default_value,
            key,
            indicator=base.is_indicator,
        )
        for child, field, key in zip(control.children, fields, keys, strict=True)
    )
    broken = next((m for m in members if m.gap is not None), None)
    if broken is not None:
        return replace(
            base,
            kind=None,
            gap=f"{broken.gap} (field {broken.label!r} of the cluster)",
        )
    return replace(base, named=named, members=members)


_CELL_KINDS = (
    PanelKind.NUMBER,
    PanelKind.BOOLEAN,
    PanelKind.STRING,
    PanelKind.PATH,
    PanelKind.ENUM,
    PanelKind.RING,
)


def _array(
    base: PanelControl, control: ParsedFPControl, lv_type: LVType | None
) -> PanelControl:
    """A one-dimensional array of a number, Boolean, string, path, enum, ring or
    of a cluster of those: a grid with a row for each element."""
    element_type = lv_type.element_type if lv_type is not None else None
    if lv_type is None or element_type is None:
        return replace(
            base,
            kind=None,
            gap=f"the panel needs the element type of the {control.control_type}",
        )
    if (lv_type.dimensions or 1) != 1:
        return replace(
            base,
            kind=None,
            gap=f"the panel has no widget for a {lv_type.dimensions}-dimensional array",
        )
    if control.children:
        cell = replace(control, control_type="stdClust", name="")
    else:
        part = next((p for p in control.parts if p.part_id is None), None)
        if part is None:
            return replace(
                base,
                kind=None,
                gap=f"the panel needs the element of the {control.control_type}",
            )
        if PanelKind.of(part.part_class, "", element_type) is PanelKind.ARRAY:
            return replace(
                base, kind=None, gap="the panel has no widget for an array of arrays"
            )
        cell = replace(
            control, control_type=part.part_class, name="", children=[], parts=[]
        )
    element = describe(
        cell, element_type, None, "value", indicator=base.is_indicator
    )
    cells = element.members if element.kind is PanelKind.CLUSTER else (element,)
    blocker = element if element.gap is not None else None
    if blocker is None:
        blocker = next((c for c in cells if c.kind not in _CELL_KINDS), None)
    if blocker is not None:
        reason = blocker.gap or (
            f"the panel has no widget for an array of {blocker.kind.name.lower()}"
            if blocker.kind is not None
            else "the panel has no widget for this array"
        )
        return replace(base, kind=None, gap=f"{reason} (element of the array)")
    return replace(base, element=element)


def describe(
    control: ParsedFPControl,
    lv_type: LVType | None,
    saved: object,
    key: str,
    *,
    indicator: bool | None = None,
) -> PanelControl:
    """The control as the panel builds it, named ``key``; the names the function
    and the hooks use for it are the caller's to add. ``indicator`` says whether
    the control is one, for a field of a cluster, which takes its cluster's."""
    python_type = lv_type.to_python() if lv_type is not None else "Any"
    kind = PanelKind.of(control.control_type, python_type, lv_type)
    base = PanelControl(
        label=control.name,
        kind=kind,
        is_indicator=control.is_indicator if indicator is None else indicator,
        bounds=control.bounds,
        key=key,
        member=None,
        parameter=None,
        field=None,
        default=0,
        integer=python_type == "int",
    )
    if kind is None:
        return replace(base, gap=_no_widget(control, python_type))
    if kind is PanelKind.CLUSTER:
        return _cluster(base, control, lv_type)
    if kind is PanelKind.ARRAY:
        return _array(base, control, lv_type)
    if kind in (PanelKind.ENUM, PanelKind.RING):
        items = choice_items(control, lv_type)
        if not items:
            return replace(
                base,
                kind=None,
                gap=(
                    f"the panel needs the items of the {control.control_type} "
                    "control, and none are stored"
                ),
            )
        return replace(base, items=items, default=choice_default(items, saved))
    return replace(base, default=_scalar_default(kind, saved))

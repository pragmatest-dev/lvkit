"""The part of a ``PanelControl`` that depends on the control and its type alone:
its kind, starting value and, for an enum or ring, its items. A kind with no
widget, or a control missing what its widget needs, is a gap."""

from __future__ import annotations

from dataclasses import replace

from lvkit.models import LVType
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


def describe(
    control: ParsedFPControl, lv_type: LVType | None, saved: object, key: str
) -> PanelControl:
    """The control as the panel builds it, named ``key``; the names the function
    and the hooks use for it are the caller's to add."""
    python_type = lv_type.to_python() if lv_type is not None else "Any"
    kind = PanelKind.of(control.control_type, python_type)
    base = PanelControl(
        label=control.name,
        kind=kind,
        is_indicator=control.is_indicator,
        bounds=control.bounds,
        key=key,
        member=None,
        parameter=None,
        field=None,
        default=0,
        integer=python_type == "int",
    )
    if kind is None:
        return replace(
            base,
            gap=(
                f"the panel has no widget for a {control.control_type} "
                f"control of type {python_type}"
            ),
        )
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
